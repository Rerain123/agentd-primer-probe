"""Structured user-interaction payloads for the Agent D workflow boundary.

The scientific engines remain deterministic and continue to record their
native errors.  This module translates an actionable block, confirmation, or
candidate shortfall into the stable UI object persisted by the host backend.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4


INTERACTION_TYPE = "primer_agent_interaction"
INTERACTION_SCHEMA_VERSION = "1.0"
RECOMMENDED_CANDIDATE_COUNT = 3


_ACTION_DETAILS = {
    "EDIT": ("补充或修改信息", "补充缺失信息或修正当前输入后继续。"),
    "REMOVE_TARGET": ("移除该靶标", "不再处理当前存在问题的靶标。"),
    "REMOVE_REQUEST": ("移除该请求", "移除当前不在本期支持范围内的请求。"),
    "RENAME": ("修改名称", "修改重复或不规范的记录名称。"),
    "CONTINUE": ("继续", "按当前确认的信息继续流程。"),
    "ADD_TEMPLATE": ("补充序列", "上传或粘贴有效的目标序列/FASTA 文件。"),
    "CONFIRM_AMBIGUITY_POLICY": (
        "确认简并碱基处理方式",
        "确认是否按当前简并碱基策略继续。",
    ),
    "CONTINUE_WITH_NOT_EVALUATED": (
        "按未评估继续",
        "保留“未评估”状态并继续后续流程。",
    ),
    "SET_ORDINARY_TAQMAN": (
        "改为普通 TaqMan 探针",
        "当前流程不支持 MGB 探针，改为普通 TaqMan 探针后继续。",
    ),
    "REMOVE_FIELD": ("移除不支持参数", "删除当前流程不支持的输入参数。"),
    "RETRY_STAGE": ("重试当前步骤", "保留已完成结果并重试当前执行步骤。"),
    "ADJUST_CONSTRAINTS": ("调整设计条件", "修改目标区域或设计约束后重新设计。"),
    "CONTINUE_SEARCH": (
        "继续尝试",
        "扩大可设计范围，继续寻找更多候选，并保留当前结果。",
    ),
    "EXPORT_CURRENT_REPORT": (
        "先输出报告",
        "停止继续尝试，使用当前候选生成阶段性报告。",
    ),
}

_ISSUE_INPUT_FIELDS = {
    "target_organism": {
        "key": "targetOrganism",
        "label": "目标病原体",
        "inputType": "TEXT",
        "required": True,
    },
    "hosts": {
        "key": "host",
        "label": "宿主动物",
        "inputType": "SELECT",
        "required": True,
    },
    "template.sequence": {
        "key": "sourceFile",
        "label": "目标序列/FASTA 文件",
        "inputType": "FILE",
        "required": True,
    },
    "fasta_text": {
        "key": "sourceFile",
        "label": "目标序列/FASTA 文件",
        "inputType": "FILE",
        "required": True,
    },
    "sequence_files": {
        "key": "sourceFile",
        "label": "目标序列/FASTA 文件",
        "inputType": "FILE",
        "required": True,
    },
}


def _other_option() -> dict[str, Any]:
    return {
        "value": "OTHER",
        "label": "其他",
        "description": "告诉 HiMat 应该如何做。",
        "inputRequired": True,
        "inputKey": "customInstruction",
        "inputType": "TEXT",
    }


def _options_for_actions(actions: list[str] | None) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for action in actions or []:
        label, description = _ACTION_DETAILS.get(
            str(action),
            (str(action), "按该处理方式继续。"),
        )
        options.append(
            {
                "value": str(action),
                "label": label,
                "description": description,
            }
        )
    if not any(item["value"] == "OTHER" for item in options):
        options.append(_other_option())
    return options


def build_interaction(
    *,
    task_id: str,
    stage: str,
    blocker_code: str,
    message: str,
    blocking: bool = True,
    context: dict[str, Any] | None = None,
    options: list[dict[str, Any]] | None = None,
    input_fields: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the persisted UI payload for a paused Agent D run."""
    return {
        "type": INTERACTION_TYPE,
        "schemaVersion": INTERACTION_SCHEMA_VERSION,
        "data": {
            "interactionId": f"interaction-{uuid4()}",
            "taskId": task_id,
            "status": "WAITING_USER",
            "stage": stage,
            "blocker": {
                "code": blocker_code,
                "blocking": blocking,
                "message": message,
            },
            "message": message,
            "context": deepcopy(context or {}),
            "options": deepcopy(options or [_other_option()]),
            "inputFields": deepcopy(input_fields or []),
            "userResponse": {
                "selectedOption": None,
                "fieldValues": {},
                "customInstruction": None,
                "submittedAt": None,
            },
        },
    }


def build_issue_interaction(task_id: str, issue: dict[str, Any]) -> dict[str, Any]:
    """Translate an M1 structured issue into a user-resolvable interaction."""
    field = str(issue.get("field") or "")
    input_field = _ISSUE_INPUT_FIELDS.get(field)
    context = {
        "issueId": issue.get("issue_id"),
        "severity": issue.get("severity"),
        "scope": issue.get("scope"),
        "targetId": issue.get("target_id"),
        "field": issue.get("field"),
        "evidence": deepcopy(issue.get("evidence") or {}),
    }
    return build_interaction(
        task_id=task_id,
        stage="M1_PREFLIGHT",
        blocker_code=str(issue.get("code") or "INPUT_CONFIRMATION_REQUIRED"),
        message=str(issue.get("message") or "需要补充或确认输入信息。"),
        blocking=True,
        context=context,
        options=_options_for_actions(issue.get("allowed_actions")),
        input_fields=[input_field] if input_field else [],
    )


def build_execution_error_interaction(
    task_id: str,
    *,
    stage: str,
    target_id: str,
    error: dict[str, Any],
) -> dict[str, Any]:
    """Translate a recoverable M2/M3/M4 execution error into a UI payload."""
    code = str(error.get("code") or f"{stage}_EXECUTION_ERROR")
    message = str(error.get("message") or f"{stage} 无法继续执行。")
    return build_interaction(
        task_id=task_id,
        stage=stage,
        blocker_code=code,
        message=message,
        context={
            "targetId": target_id,
            "technicalErrorCode": code,
            "technicalDetails": deepcopy(error.get("details") or {}),
        },
        options=_options_for_actions(
            ["RETRY_STAGE", "ADJUST_CONSTRAINTS"]
        ),
    )


def build_candidate_shortfall_interaction(
    task_id: str,
    *,
    target_id: str,
    candidate_count: int,
    recommended_count: int = RECOMMENDED_CANDIDATE_COUNT,
) -> dict[str, Any]:
    """Build the product decision dialog when fewer than three candidates remain."""
    actions = [
        "CONTINUE_SEARCH",
        "EXPORT_CURRENT_REPORT",
        "ADJUST_CONSTRAINTS",
    ]
    return build_interaction(
        task_id=task_id,
        stage="M5_DELIVERY_DECISION",
        blocker_code="INSUFFICIENT_CANDIDATES",
        message=(
            f"目前只找到 {candidate_count} 组候选，少于建议的 "
            f"{recommended_count} 组，下一步怎么处理？"
        ),
        context={
            "targetId": target_id,
            "candidateCount": candidate_count,
            "recommendedCandidateCount": recommended_count,
        },
        options=_options_for_actions(actions),
    )

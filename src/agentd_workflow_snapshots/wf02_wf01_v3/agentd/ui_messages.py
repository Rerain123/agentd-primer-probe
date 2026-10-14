"""Deterministic HiMat-facing messages for the WF-01 conversation boundary.

These builders keep UI rendering independent from whether a language model
repeats the requested JSON shape in its natural-language response.
"""

from __future__ import annotations

import copy
from typing import Any

from .identifiers import wf01_batch_code


def _wait_for_user_control(resume_message_type: str) -> dict[str, Any]:
    """Describe a hard conversation turn boundary for UI and agent hosts."""
    return {
        "action": "WAIT_FOR_USER",
        "stopCurrentTurn": True,
        "resumeMessageType": resume_message_type,
    }


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _display_file_name(value: Any) -> str:
    """Return a safe display-only basename without rewriting the filename."""
    name = _text(value)
    return name.replace("\\", "/").rsplit("/", 1)[-1].strip() if name else ""


def _file_name_from_item(item: Any) -> str:
    if isinstance(item, str):
        return _display_file_name(item)
    if not isinstance(item, dict):
        return ""
    for key in (
        "fileName",
        "file_name",
        "filename",
        "originalName",
        "original_name",
        "name",
    ):
        name = _display_file_name(item.get(key))
        if name:
            return name
    return ""


def _source_file_display_name(payload: dict[str, Any]) -> str:
    """Resolve one uploaded filename or the fixed multi-file display label."""
    for key in (
        "uploaded_file_names",
        "fileNames",
        "file_names",
        "sourceFiles",
        "source_files",
        "sequence_files",
        "attachments",
        "files",
        "uploadedFiles",
        "uploaded_files",
        "inputFiles",
        "input_files",
    ):
        items = payload.get(key)
        if not isinstance(items, list) or not items:
            continue
        if len(items) > 1:
            return "多个文件"
        name = _file_name_from_item(items[0])
        if name:
            return name

    for key in ("sourceFile", "source_file", "attachment", "file"):
        name = _file_name_from_item(payload.get(key))
        if name:
            return name
    for key in ("fileName", "file_name", "filename"):
        name = _display_file_name(payload.get(key))
        if name:
            return name
    return "未获取文件名"


def build_primer_design_input(payload: dict[str, Any]) -> dict[str, Any]:
    """Build the pre-design confirmation form from canonical or UI aliases."""
    payload = copy.deepcopy(payload) if isinstance(payload, dict) else {}
    defaults = payload.get("batch_defaults")
    defaults = defaults if isinstance(defaults, dict) else {}
    direct = payload.get("designInput") or payload.get("design_input") or {}
    direct = direct if isinstance(direct, dict) else {}

    target_value = (
        direct.get("targetOrganism")
        or defaults.get("target_organism")
        or payload.get("target_organism")
        or payload.get("target_object")
    )
    if isinstance(target_value, dict):
        target_value = (
            target_value.get("name")
            or target_value.get("normalized_name")
            or target_value.get("identifier")
        )
    target = _text(target_value)

    host_value = (
        direct.get("host")
        or defaults.get("hosts")
        or payload.get("hosts")
        or payload.get("host")
        or payload.get("host_objects")
    )
    if isinstance(host_value, list):
        host_value = host_value[0] if host_value else ""
    if isinstance(host_value, dict):
        host_value = (
            host_value.get("name")
            or host_value.get("normalized_name")
            or host_value.get("identifier")
        )
    host = _text(host_value)

    coverage = _text(
        direct.get("coverageRequirement")
        or defaults.get("coverage_requirement")
        or payload.get("coverage_requirement")
    )

    file_name = _source_file_display_name(payload)
    missing_context: list[str] = []
    if not target:
        missing_context.append("targetOrganism")
    issues = [
        {
            "code": "MISSING_SPECIFICITY_CONTEXT",
            "level": "WARNING",
            "field": field,
            "message": f"未提供 {field}；可以开始 Primer3 设计，但不会启动 BLAST+ 特异性检测",
        }
        for field in missing_context
    ]
    summary_parts = [value for value in (target, f"宿主：{host}" if host else "", coverage) if value]
    return {
        "type": "primer_design_input",
        "data": {
            "status": "WAITING_USER",
            "confirmed": False,
            "canStart": True,
            "summaryText": "·".join(summary_parts) or "请确认引物探针设计信息",
            "sourceFile": {"fileName": file_name, "fileType": "FASTA"},
            "designInput": {
                "targetOrganism": target,
                "host": host,
                "coverageRequirement": coverage,
            },
            "issues": issues,
        },
    }


def build_agent_interaction_message(
    interactions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return HiMat's flat single-question shape or a multi-question wrapper."""
    mapped: list[dict[str, Any]] = []
    for interaction in interactions:
        data = interaction.get("data") or {}
        if data.get("status") != "WAITING_USER":
            continue
        input_fields = copy.deepcopy(data.get("inputFields") or [])
        item = {
            "interactionId": data.get("interactionId"),
            "targetId": (data.get("context") or {}).get("targetId"),
            "field": (data.get("context") or {}).get("field"),
            "blockerCode": (data.get("blocker") or {}).get("code"),
            "reason": (data.get("blocker") or {}).get("message") or data.get("message"),
            "question": data.get("message"),
            "inputType": "FORM" if input_fields else "SINGLE_SELECT",
            "options": copy.deepcopy(data.get("options") or []),
            "answer": None,
        }
        if input_fields:
            item["inputFields"] = input_fields
        mapped.append(item)
    if not mapped:
        return None
    if len(mapped) == 1:
        return {
            "type": "agent_interaction",
            "task": "PRIMER_DESIGN",
            "flowControl": _wait_for_user_control(
                "agent_interaction_response"
            ),
            **mapped[0],
        }
    return {
        "type": "agent_interaction",
        "task": "PRIMER_DESIGN",
        "flowControl": _wait_for_user_control(
            "agent_interaction_response"
        ),
        "interactions": mapped,
    }


def build_primer_design_output(batch: dict[str, Any]) -> dict[str, Any] | None:
    """Build the report card directly from persisted M6 export metadata."""
    if batch.get("m6_outcome") != "COMPLETED":
        return None
    m6 = batch.get("m6_execution") or {}
    exports = m6.get("exports") or {}
    attachments: list[dict[str, Any]] = []
    attachment_types = {
        "html": "HTML",
        "process_html": "HTML",
        "scientific_data": "ZIP",
        "csv": "CSV",
        "pdf": "PDF",
    }
    for format_name, file_type in attachment_types.items():
        value = exports.get(format_name)
        if not isinstance(value, dict):
            continue
        attachments.append(
            {
                "attachmentId": f"attachment_{format_name}",
                "fileName": value.get("filename"),
                "fileType": file_type,
                "mimeType": value.get("media_type"),
                "fileUrl": value.get("file_url"),
            }
        )
    html = next(
        (
            item
            for item in attachments
            if item["attachmentId"] == "attachment_html"
        ),
        None,
    )
    primary = html or (attachments[0] if attachments else None)
    if primary is None:
        # The MCP summary exposes a deterministic paginated text fallback when
        # no user-visible export could be rendered.
        return None
    task_name = _text(
        (batch.get("preflight") or {})
        .get("normalised_input", {})
        .get("task_name")
    ) or "引物探针设计"
    counts = batch.get("candidate_counts") or {}
    deliverable = counts.get("deliverable")
    candidate_text = (
        f"，共生成 {deliverable} 套可交付候选"
        if isinstance(deliverable, int)
        else ""
    )
    shortfall_messages = [
        _text(((target.get("m5_execution") or {}).get("candidate_notice") or {}).get("message"))
        for target in batch.get("targets") or []
        if ((target.get("m5_execution") or {}).get("candidate_notice") or {}).get(
            "status"
        )
        == "INSUFFICIENT"
    ]
    shortfall_messages = [message for message in shortfall_messages if message]
    completeness = m6.get("completeness") or {}
    issues: list[str] = []
    for issue in completeness.get("issues") or []:
        if isinstance(issue, dict):
            code = _text(issue.get("code"))
            message = _text(issue.get("message"))
            issues.append(
                f"{code}: {message}" if code and message else message or code
            )
        else:
            text = _text(issue)
            if text:
                issues.append(text)
    export_failures = m6.get("export_failures") or {}
    for format_name, failure in export_failures.items():
        code = failure.get("code") or (
            f"M6_{str(format_name).upper()}_EXPORT_FAILED"
        )
        message = failure.get("message") or (
            f"{str(format_name).upper()} 导出失败"
        )
        issues.append(f"{code}: {message}")
    issues.extend(shortfall_messages)
    if html:
        delivery_text = (
            "候选报告和科学过程详情均已生成；"
            "21条规则验证报告及其他辅助文件已收纳在 ZIP 数据包中。"
        )
    else:
        delivery_text = f"HTML 暂不可用，请打开 {primary['fileType']} 附件查看结果。"
    if export_failures:
        delivery_text += " 部分附加格式生成失败，详情见 issues。"
    if shortfall_messages:
        delivery_text += " 候选不足或无候选的原因已写入报告和 issues。"
    batch_code = str(
        batch.get("batch_code")
        or wf01_batch_code(
            str(batch.get("batch_id") or ""),
            batch.get("created_at"),
            workflow=str(batch.get("workflow") or "WF-01"),
        )
    )
    return {
        "data": {
            "status": "COMPLETED",
            "reportTitle": f"{task_name}引物探针设计报告",
            "summaryText": (
                f"任务编号 {batch_code}。已完成设计与报告生成"
                f"{candidate_text}，{delivery_text}"
            ),
            "primaryAttachmentId": primary["attachmentId"],
            "attachments": attachments,
            "issues": issues,
        },
        "type": "primer_design_output",
    }


def build_workflow_terminal_output(
    report: dict[str, Any],
    export: dict[str, Any],
) -> dict[str, Any]:
    """Wrap a WF-03/WF-04 terminal report in the WF-01 host contract."""

    workflow = _text(report.get("workflow")) or "WORKFLOW"
    job_id = _text(report.get("job_id"))
    task_code = _text(export.get("task_code")) or job_id
    execution_status = _text(
        report.get("execution_status") or report.get("status")
    ) or "UNKNOWN"
    decision = _text(
        report.get("decision")
        or report.get("verdict")
        or (report.get("internal_control") or {}).get("verdict")
    ) or "NOT_AVAILABLE"
    issues: list[str] = []
    for key in ("errors", "warnings", "decision_reasons"):
        value = report.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict):
                code = _text(item.get("code") or item.get("reason_code"))
                message = _text(item.get("message") or item.get("reason"))
                selected = f"{code}: {message}" if code and message else code or message
            else:
                selected = _text(item)
            if selected:
                issues.append(selected)
    attachment = {
        "attachmentId": "attachment_html",
        "fileName": export.get("filename"),
        "fileType": "HTML",
        "mimeType": export.get("media_type") or "text/html; charset=utf-8",
        "fileUrl": export.get("file_url"),
    }
    return {
        "data": {
            # This is the report-delivery state. The workflow's own execution
            # state and decision remain independent and visible in the report.
            "status": "COMPLETED",
            "reportTitle": f"{workflow} 终态报告",
            "summaryText": (
                f"任务编号 {task_code}。后端工作流已结束；执行状态 "
                f"{execution_status}，最终状态 {decision}。完整结果见 HTML 附件。"
            ),
            "primaryAttachmentId": attachment["attachmentId"],
            "attachments": [attachment],
            "issues": list(dict.fromkeys(issues)),
        },
        "type": "primer_design_output",
    }


def ui_message_for_batch(batch: dict[str, Any]) -> dict[str, Any] | None:
    """Select the authoritative UI message for the current batch state."""
    interaction = build_agent_interaction_message(
        batch.get("agent_interactions") or []
    )
    if interaction is not None:
        return interaction
    return build_primer_design_output(batch)

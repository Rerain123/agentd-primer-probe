"""OligoSmith 可流式传输 HTTP MCP 服务。

所有 MCP 工具均委托给 AgentDService，使命令行、REST 兼容接口和 MCP 客户端
共享同一套科学工作流实现。
"""

from __future__ import annotations

import copy
import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar
from urllib.parse import urlsplit

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent
from pydantic import Field

from .api import app as compatibility_api
from .api import service
from .auth import JWTBearerMiddleware, JWTSettings
from .config import PROJECT_ROOT
from .errors import AgentDError, InputValidationError
from .wf01_agent_view import serialized_size_bytes, summarize_wf01_batch
from .wf02_v01 import (
    summarize_wf02_batch,
    wf02_execution_progress,
    wf02_resume_token,
)


LOGGER = logging.getLogger("agentd.mcp")
T = TypeVar("T")
WF01BatchReference = Annotated[
    str,
    Field(
        description=(
            "WF-01 内部 UUID，或最终报告卡片中的 WF01-YYMMDD-HHMM-XXXX 任务编号。"
        )
    ),
]


MCP_INSTRUCTIONS = """\
你是 OligoSmith 编排智能体。候选与结论均采用工具结果。
1. WF-01：FULL_SPECIFICITY 完整路线；缺少 target_organism 先询问。create_wf01_batch接文本，附件先download_himat_file，run_wf01_until_blocked恢复。primer_design_output须输出紧凑裸 JSON并立即结束，本轮禁止再调用工具。保留 batch_id；只有用户后续明确要求才用 get_wf01_report/get_wf01_export 获取下载地址或 create_wf01_version。
2. WF-02：download_himat_file→downstream.wf02.target→create_wf02_batch。复用对话已有的靶标、文件映射和宿主。缺字段由 N04 返回 agent_interaction；省略 include_internal_control、省略 runtime_config。
3. WF-02交互用 submit_wf02_input_response/submit_wf02_child_response；run_wf02_until_blocked在服务端连续执行多个已持久化单元。CALL_TOOL_NOW时禁止输出中间文字，按 next_tool/next_arguments 同轮调用；get_wf02_batch仅查询。收到 primer_design_output 时原样交付。
4. WF-03：有附件/file_id时先 download_himat_file→取顶层 input_artifact_id→run_wf03_internal_control；无附件且 host_species已知时直接调用。缺少 host_species 时只询问该字段并停止。禁止先分析或用文本模拟结果，P5 采用后端结果；补证用 retry_wf03_internal_control，查询用 get_wf03_report。收到 primer_design_output 时原样交付并结束。
5. WF-04：附件/file_id先 download_himat_file→取顶层 input_artifact_id→run_wf04_validation；无附件且 forward_primer、reverse_primer齐全时直接调用，缺字段只询问。按 retryable_steps 调 retry_wf04_validation，查询用 get_wf04_report。执行状态和最终判定是两个独立字段，禁止模型执行或重算；收到 primer_design_output 时原样交付并结束。
"""


def _csv_env(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


def _transport_security() -> TransportSecuritySettings:
    public_base_url = os.environ.get("MCP_PUBLIC_BASE_URL", "").strip()
    public = urlsplit(public_base_url) if public_base_url else None
    default_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    default_origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    if public and public.hostname:
        default_hosts.append(public.netloc)
        default_origins.append(f"{public.scheme}://{public.netloc}")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_csv_env("MCP_ALLOWED_HOSTS") or default_hosts,
        allowed_origins=_csv_env("MCP_ALLOWED_ORIGINS") or default_origins,
    )


mcp = FastMCP(
    name="OligoSmith",
    instructions=MCP_INSTRUCTIONS,
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
    transport_security=_transport_security(),
)


def _invoke(
    operation: str,
    callback: Callable[[], T],
    *,
    audit_input: Any = None,
) -> T:
    trace_id: str | None = None
    try:
        trace_id = service.audit.start(operation, audit_input)
    except Exception:  # pragma: no cover - 审计不得中断工作流调用
        LOGGER.exception("无法启动工具审计", extra={"operation": operation})
    try:
        result = callback()
    except AgentDError as exc:
        if trace_id:
            try:
                service.audit.fail(trace_id, operation, exc)
            except Exception:  # pragma: no cover - 防御性可观测边界
                LOGGER.exception("无法保存工具失败审计", extra={"operation": operation})
        raise ToolError(json.dumps(exc.to_dict(), ensure_ascii=False)) from exc
    except Exception as exc:  # pragma: no cover - defensive MCP boundary
        if trace_id:
            try:
                service.audit.fail(trace_id, operation, exc)
            except Exception:
                LOGGER.exception("无法保存工具失败审计", extra={"operation": operation})
        LOGGER.exception("MCP 工具执行失败", extra={"operation": operation})
        raise ToolError(
            json.dumps(
                {
                    "code": "INTERNAL_ERROR",
                    "message": "智能体工具执行失败",
                    "details": {"operation": operation, "error_type": type(exc).__name__},
                }
            )
        ) from exc
    if trace_id:
        try:
            service.audit.complete(trace_id, operation, result)
        except Exception:  # pragma: no cover - 审计不得改变工具结果
            LOGGER.exception("无法完成工具审计", extra={"operation": operation})
    return _bounded_tool_result(operation, result)


def _tool_result_limit_bytes() -> int:
    raw = os.environ.get("MCP_MAX_TOOL_RESULT_BYTES", "65536")
    try:
        return max(8192, int(raw))
    except ValueError:
        return 65536


def _bounded_tool_result(operation: str, result: T) -> T | dict[str, Any]:
    """Prevent an unexpectedly large tool payload from entering model context."""
    limit = _tool_result_limit_bytes()
    size = serialized_size_bytes(result)
    if size <= limit:
        return result
    if isinstance(result, dict):
        if result.get("workflow") == "WF-02" and result.get(
            "response_schema_version"
        ):
            compact = {
                key: copy.deepcopy(result.get(key))
                for key in (
                    "response_schema_version",
                    "workflow",
                    "batch_id",
                    "batch_code",
                    "report_id",
                    "status",
                    "stage",
                    "next_action",
                    "view",
                    "section",
                    "resume_token",
                    "execution_unit",
                    "execution_progress",
                    "orchestration",
                    "tool_control",
                    "flowControl",
                    "ui_message",
                    "result_summary",
                    "outcome",
                )
                if result.get(key) is not None
            }
            compact.update(
                {
                    "response_truncated": True,
                    "original_size_bytes": size,
                    "maximum_size_bytes": limit,
                    "operation": operation,
                    "detail_query": {
                        "tool": (
                            "get_wf02_panel"
                            if result.get("panel_id")
                            else (
                                "get_wf02_report"
                                if result.get("report_id")
                                else "get_wf02_batch"
                            )
                        ),
                        "use_pagination": True,
                        **(
                            {"panel_id": result.get("panel_id")}
                            if result.get("panel_id")
                            else {}
                        ),
                    },
                }
            )
            if serialized_size_bytes(compact) <= limit:
                return compact
        batch = result.get("batch")
        if not isinstance(batch, dict) and result.get("batch_id"):
            batch = result
        if isinstance(batch, dict) and batch.get("batch_id"):
            if batch.get("workflow") == "WF-02":
                summary = summarize_wf02_batch(batch)
            else:
                summary = summarize_wf01_batch(
                    batch,
                    top_candidates_per_target=0,
                )
            summary.update(
                {
                    "response_truncated": True,
                    "original_size_bytes": size,
                    "maximum_size_bytes": limit,
                    "operation": operation,
                }
            )
            if serialized_size_bytes(summary) <= limit:
                return summary
        source = result.get("source")
        if isinstance(source, dict) and source.get("batch_id"):
            report_summary = {
                "response_schema_version": "wf02-v0.1-report-overflow-v1",
                "workflow": "WF-02",
                "batch_id": source.get("batch_id"),
                "report_id": result.get("report_id"),
                "result_summary": copy.deepcopy(result.get("result_summary") or {}),
                "outcome": copy.deepcopy(result.get("outcome") or {}),
                "response_truncated": True,
                "original_size_bytes": size,
                "maximum_size_bytes": limit,
                "operation": operation,
                "detail_query": {
                    "tool": "get_wf02_report",
                    "sections": ["summary", "panels", "failure", "trace"],
                },
            }
            if serialized_size_bytes(report_summary) <= limit:
                return report_summary
        retained = {
            key: result.get(key)
            for key in (
                "batch_id",
                "job_id",
                "status",
                "next_action",
                "ui_message",
            )
            if result.get(key) is not None
        }
    else:
        retained = {"result_type": type(result).__name__}
    return {
        "response_schema_version": "agent-tool-overflow-v1",
        "response_truncated": True,
        "operation": operation,
        "original_size_bytes": size,
        "maximum_size_bytes": limit,
        "summary": retained,
        "message": (
            "完整结果已被服务端保留，但因超过智能体工具响应上限未写入模型上下文。"
        ),
    }


_WF02_CONTINUE_PREFIXES = ("RUN_", "CONTINUE_", "RETRY_")
_WF02_HARD_STOP_REASONS = {"MISSING_RESUME_TOKEN", "NO_PROGRESS"}


def _wf02_continuation_allowed(result: dict[str, Any]) -> bool:
    ui_message = result.get("ui_message")
    if isinstance(ui_message, dict) and ui_message.get("type") == (
        "primer_design_output"
    ):
        return False
    flow_control = result.get("flowControl")
    if isinstance(flow_control, dict) and flow_control.get("stopCurrentTurn"):
        return False
    status = str(result.get("status") or "")
    if status.startswith(("WAITING_", "REVIEW_")):
        return False
    orchestration = result.get("orchestration")
    if isinstance(orchestration, dict) and orchestration.get(
        "stop_reason"
    ) in _WF02_HARD_STOP_REASONS:
        return False
    next_action = str(result.get("next_action") or "")
    return next_action.startswith(_WF02_CONTINUE_PREFIXES)


def _wf02_tool_control(result: dict[str, Any]) -> dict[str, Any]:
    """Return an executable continuation command instead of prose guidance."""
    call_again = _wf02_continuation_allowed(result)
    resume_token = str(result.get("resume_token") or "") or None
    next_arguments = None
    if call_again:
        next_arguments = {
            "batch_id": result.get("batch_id"),
            "resume_token": resume_token,
        }
    ui_message = result.get("ui_message")
    terminal = (
        isinstance(ui_message, dict)
        and ui_message.get("type") == "primer_design_output"
    )
    if call_again:
        directive = "CALL_TOOL_NOW"
    elif terminal:
        directive = "DELIVER_TERMINAL_OUTPUT"
    else:
        directive = "STOP_CURRENT_TURN"
    execution_unit = result.get("execution_unit") or {}
    result["tool_control"] = {
        "mode": "SERVER_EXECUTION_WINDOW",
        "directive": directive,
        "call_again": call_again,
        "continue_in_same_turn": call_again,
        "suppress_intermediate_text": call_again,
        "next_tool": "run_wf02_until_blocked" if call_again else None,
        "next_arguments": next_arguments,
        "resume_token": resume_token,
        "state_committed": bool(execution_unit.get("state_committed")),
        "progress": copy.deepcopy(result.get("execution_progress") or {}),
    }
    return result


def _wf02_window_limit(name: str, default: int, maximum: int) -> int:
    try:
        return max(1, min(int(os.environ.get(name, str(default))), maximum))
    except ValueError:
        return default


def _run_wf02_execution_window(
    batch_id: str,
    resume_token: str | None,
) -> dict[str, Any]:
    """Drain several persisted WF-02 units inside one model tool call."""
    maximum_units = _wf02_window_limit(
        "MCP_WF02_MAX_INTERNAL_UNITS", 8, 32
    )
    maximum_seconds = _wf02_window_limit(
        "MCP_WF02_MAX_EXECUTION_SECONDS", 45, 240
    )
    started = time.monotonic()
    submitted_token = resume_token
    current: dict[str, Any] = {}
    committed_units = 0
    stale_token_recoveries = 0
    stop_reason = "UNIT_LIMIT"

    for unit_index in range(maximum_units):
        run_options: dict[str, Any] = {"bounded": True}
        if submitted_token:
            run_options["expected_resume_token"] = submitted_token
        current = service.wf02_v01.run_until_blocked(batch_id, **run_options)
        execution_unit = current.get("execution_unit") or {}
        execution_status = str(execution_unit.get("status") or "")
        if execution_unit.get("state_committed"):
            committed_units += 1
        if execution_status == "STALE_RESUME_TOKEN_NOOP":
            stale_token_recoveries += 1

        if not _wf02_continuation_allowed(current):
            stop_reason = (
                "TERMINAL"
                if isinstance(current.get("ui_message"), dict)
                and current["ui_message"].get("type")
                == "primer_design_output"
                else "WORKFLOW_BOUNDARY"
            )
            break

        current_token = str(current.get("resume_token") or "")
        if not current_token:
            stop_reason = "MISSING_RESUME_TOKEN"
            break
        if current_token == submitted_token:
            stop_reason = "NO_PROGRESS"
            break
        submitted_token = current_token
        if time.monotonic() - started >= maximum_seconds:
            stop_reason = "TIME_LIMIT"
            break
    else:
        unit_index = maximum_units - 1

    current["orchestration"] = {
        "mode": "SERVER_EXECUTION_WINDOW",
        "execution_units": unit_index + 1,
        "committed_units": committed_units,
        "stale_token_recoveries": stale_token_recoveries,
        "stop_reason": stop_reason,
        "maximum_units": maximum_units,
        "maximum_seconds": maximum_seconds,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
    return _wf02_tool_control(current)


def _page(items: list[Any], cursor: int, limit: int) -> dict[str, Any]:
    start = max(0, int(cursor))
    size = max(1, min(int(limit), 50))
    selected = copy.deepcopy(items[start : start + size])
    next_cursor = start + len(selected)
    return {
        "items": selected,
        "cursor": start,
        "limit": size,
        "returned": len(selected),
        "total": len(items),
        "next_cursor": next_cursor if next_cursor < len(items) else None,
    }


def _wf02_batch_view(
    batch_id: str,
    *,
    view: str,
    target_id: str | None,
    cursor: int,
    limit: int,
) -> dict[str, Any]:
    if view == "summary":
        return service.get_wf02_v01_agent_summary(batch_id)
    batch = service.get_wf02_v01_batch(batch_id)
    common = {
        "response_schema_version": "wf02-v0.1-batch-view-v1",
        "workflow": "WF-02",
        "batch_id": batch.get("batch_id"),
        "batch_code": batch.get("batch_code"),
        "status": batch.get("status"),
        "stage": batch.get("stage"),
        "next_action": batch.get("next_action"),
        "resume_token": wf02_resume_token(batch),
        "execution_progress": wf02_execution_progress(batch),
        "view": view,
    }
    if view == "target":
        targets = (batch.get("target_design") or {}).get("targets") or []
        target = next(
            (
                item
                for item in targets
                if str(item.get("target_id")) == str(target_id or "")
            ),
            None,
        )
        if target is None:
            raise InputValidationError(
                "WF-02 任务中不存在该靶标",
                details={"batch_id": batch_id, "target_id": target_id},
            )
        candidates = [
            {
                "candidate_id": item.get("candidate_id"),
                "rank": item.get("rank"),
                "score": item.get("score"),
                "verdict": item.get("verdict"),
                "evidence_level": item.get("evidence_level"),
                "specificity_status": (item.get("specificity") or {}).get(
                    "overall_status"
                ),
                "p6_status": (item.get("amplicon_structure") or {}).get("status"),
                "warning_count": len(item.get("warnings") or []),
            }
            for item in target.get("candidates") or []
        ]
        return {
            **common,
            "target": {
                key: copy.deepcopy(target.get(key))
                for key in (
                    "target_id",
                    "display_name",
                    "input_order",
                    "status",
                    "candidate_count",
                    "failure",
                    "p6_summary",
                    "resource_retry_count",
                )
                if target.get(key) is not None
            },
            "candidates": _page(candidates, cursor, limit),
            "wf01_lineage": _page(
                target.get("wf01_lineage") or [], cursor, limit
            ),
        }
    if view == "trace":
        return {
            **common,
            "workflow_trace": _page(
                batch.get("workflow_trace") or [], cursor, limit
            ),
        }
    if view == "events":
        return {
            **common,
            "events": _page(batch.get("events") or [], cursor, limit),
        }
    relaxation = (batch.get("multiplex") or {}).get("relaxation") or {}
    return {
        **common,
        "relaxation": {
            "status": relaxation.get("status"),
            "active_step": relaxation.get("active_step"),
            "maximum_partial_target_count": relaxation.get(
                "maximum_partial_target_count"
            ),
            "tool_failure": copy.deepcopy(relaxation.get("tool_failure")),
            "rounds": [
                {
                    key: copy.deepcopy(item.get(key))
                    for key in (
                        "node",
                        "status",
                        "total_panels",
                        "cursor",
                        "accepted_count",
                        "rejected_count",
                    )
                }
                for item in relaxation.get("rounds") or []
            ],
            "log": _page(
                relaxation.get("relaxation_log") or [], cursor, limit
            ),
        },
    }


def _wf02_report_view(
    report: dict[str, Any],
    *,
    section: str,
    cursor: int,
    limit: int,
) -> dict[str, Any]:
    source = report.get("source") or {}
    multiplex = report.get("multiplex") or {}

    def compact_failure(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or not value:
            return None
        return {
            key: copy.deepcopy(value.get(key))
            for key in (
                "code",
                "message",
                "status",
                "retryable",
                "stage",
                "reason_codes",
            )
            if value.get(key) is not None
        }

    def compact_gap(value: dict[str, Any]) -> dict[str, Any]:
        return {
            key: copy.deepcopy(value.get(key))
            for key in (
                "target_id",
                "display_name",
                "input_order",
                "reason_code",
                "reason_message",
                "wf01_batch_id",
            )
            if value.get(key) is not None
        } | {"failure": compact_failure(value.get("failure"))}

    common = {
        "response_schema_version": "wf02-v0.1-report-view-v1",
        "workflow": "WF-02",
        "batch_id": source.get("batch_id"),
        "report_id": report.get("report_id"),
        "generated_at": report.get("generated_at"),
        "section": section,
    }
    if section == "summary":
        return {
            **common,
            "source": copy.deepcopy(source),
            "result_summary": copy.deepcopy(report.get("result_summary") or {}),
            "outcome": copy.deepcopy(report.get("outcome") or {}),
            "selection_mode": report.get("selection_mode"),
            "top3_panel_count": len(report.get("top3_panels") or []),
            "partial_panel_count": len(report.get("partial_panels") or []),
            "risk_alternative_count": len(report.get("risk_alternatives") or []),
            "internal_control_summary": copy.deepcopy(
                report.get("internal_control_summary") or {}
            ),
            "multiplex": {
                key: copy.deepcopy(multiplex.get(key))
                for key in (
                    "status",
                    "panel_size",
                    "requested_panel_size",
                    "delivered_target_count",
                    "delivered_member_count",
                    "internal_control_included",
                    "targets_order",
                    "failed_targets",
                    "blocked_targets",
                    "coverage",
                    "primary_panel_id",
                    "delivered_panel_ids",
                    "relaxation_applied",
                    "channel_assignment",
                    "heterodimer",
                    "tm_spread_celsius",
                    "gc_spread_percent",
                    "issues",
                    "verdict",
                )
            }
            | {
                "coverage_gap_count": len(
                    multiplex.get("coverage_gaps") or []
                )
            },
            "candidate_comparison": copy.deepcopy(
                report.get("candidate_comparison") or {}
            ),
            "result_explanation": copy.deepcopy(
                report.get("result_explanation") or {}
            ),
            "exports": copy.deepcopy(report.get("exports") or {}),
            "available_sections": ["summary", "panels", "failure", "trace"],
        }
    if section == "panels":
        full_panels = (
            list(report.get("top3_panels") or [])
            + list(report.get("partial_panels") or [])
            + list(report.get("risk_alternatives") or [])
        )
        panels = [
            {
                "rank": panel.get("rank"),
                "panel_id": panel.get("panel_id"),
                "tier": panel.get("tier"),
                "classification": panel.get("classification"),
                "final_score": (panel.get("score") or {}).get(
                    "final_score"
                ),
                "member_count": len(panel.get("members") or []),
                "target_ids": [
                    item.get("target_id")
                    for item in panel.get("members") or []
                ],
                "warning_count": len(panel.get("warnings") or []),
                "detail_query": {
                    "tool": "get_wf02_panel",
                    "panel_id": panel.get("panel_id"),
                    "section": "summary",
                },
            }
            for panel in full_panels
        ]
        return {**common, "panels": _page(panels, cursor, min(limit, 3))}
    if section == "failure":
        return {
            **common,
            "outcome": copy.deepcopy(report.get("outcome") or {}),
            "failure_guidance": copy.deepcopy(report.get("failure_guidance") or []),
            "independent_target_schemes": _page(
                report.get("independent_target_schemes") or [], cursor, limit
            ),
            "coverage": copy.deepcopy(multiplex.get("coverage") or {}),
            "coverage_gaps": _page(
                [
                    compact_gap(item)
                    for item in multiplex.get("coverage_gaps") or []
                    if isinstance(item, dict)
                ],
                cursor,
                limit,
            ),
        }
    traceability = report.get("traceability") or {}
    tool_executions = traceability.get("tool_executions") or {}
    compact_tool_executions = {
        "wf01_targets": [
            {
                "tool": item.get("tool"),
                "target_id": item.get("target_id"),
                "status": item.get("status"),
                "active_batch_id": item.get("active_batch_id"),
                "lineage_count": len(item.get("lineage") or []),
                "failure": compact_failure(item.get("failure")),
            }
            for item in tool_executions.get("wf01_targets") or []
            if isinstance(item, dict)
        ],
        "p6": copy.deepcopy(tool_executions.get("p6") or {}),
        "p5": {
            key: copy.deepcopy((tool_executions.get("p5") or {}).get(key))
            for key in ("status", "verdict", "attempt_count")
        }
        | {
            "failure": compact_failure(
                (tool_executions.get("p5") or {}).get("failure")
            )
        },
        "p4": {
            key: copy.deepcopy((tool_executions.get("p4") or {}).get(key))
            for key in (
                "status",
                "processed_combinations",
                "total_combinations",
            )
        }
        | {
            "tool_failure": compact_failure(
                (tool_executions.get("p4") or {}).get("tool_failure")
            )
        },
    }
    target_lineage = [
        {
            key: copy.deepcopy(item.get(key))
            for key in (
                "tool",
                "target_id",
                "status",
                "active_batch_id",
                "lineage",
                "input_source",
            )
        }
        | {"failure": compact_failure(item.get("failure"))}
        for item in traceability.get("target_lineage") or []
        if isinstance(item, dict)
    ]
    uncompleted = traceability.get("uncompleted_reasons") or {}
    return {
        **common,
        "execution_route": copy.deepcopy(
            traceability.get("execution_route") or {}
        ),
        "tool_executions": compact_tool_executions,
        "relaxation": {
            key: copy.deepcopy((traceability.get("relaxation") or {}).get(key))
            for key in ("status", "active_step")
        },
        "instrument_configuration": copy.deepcopy(
            traceability.get("instrument_configuration") or {}
        ),
        "channel_assignment": copy.deepcopy(
            traceability.get("channel_assignment") or {}
        ),
        "uncompleted_reasons": {
            "outcome": copy.deepcopy(uncompleted.get("outcome") or {}),
            "coverage_gaps": [
                compact_gap(item)
                for item in uncompleted.get("coverage_gaps") or []
                if isinstance(item, dict)
            ],
        },
        "workflow_trace": _page(
            traceability.get("workflow_trace") or [], cursor, limit
        ),
        "events": _page(traceability.get("events") or [], cursor, limit),
        "target_lineage": _page(
            target_lineage, cursor, limit
        ),
    }


def _wf02_panel_view(
    detail: dict[str, Any],
    *,
    section: str,
    cursor: int,
    limit: int,
) -> dict[str, Any]:
    """Expose one panel without placing raw pair matrices in model context."""
    panel = detail.get("panel") or {}
    common = {
        "response_schema_version": "wf02-v0.1-panel-view-v1",
        "workflow": "WF-02",
        "batch_id": detail.get("batch_id"),
        "report_id": detail.get("report_id"),
        "panel_id": panel.get("panel_id"),
        "section": section,
    }
    if section == "summary":
        return {
            **common,
            "panel": {
                key: copy.deepcopy(panel.get(key))
                for key in (
                    "rank",
                    "panel_id",
                    "tier",
                    "classification",
                    "score",
                    "sequence_signature_sha256",
                    "channel_assignment",
                    "relaxation_applied",
                    "recommended_annealing_temp_celsius",
                    "annealing_temperature_basis",
                    "recommendation_policy_version",
                    "warnings",
                )
            }
            | {
                "member_count": len(panel.get("members") or []),
                "alternate_channel_assignment_count": len(
                    panel.get("alternate_channel_assignments") or []
                ),
            },
            "available_sections": ["summary", "members", "evidence"],
        }
    if section == "members":
        members = []
        for member in panel.get("members") or []:
            specificity = member.get("specificity") or {}
            structure = member.get("amplicon_structure") or {}
            members.append(
                {
                    key: copy.deepcopy(member.get(key))
                    for key in (
                        "target_id",
                        "role",
                        "candidate_id",
                        "fluorophore",
                        "quencher",
                        "fluorophore_source",
                        "quencher_source",
                        "forward_primer",
                        "reverse_primer",
                        "probe",
                        "amplicon",
                        "blast_summary",
                        "conservation_score",
                        "evidence_level",
                        "warnings",
                    )
                }
                | {
                    "specificity": {
                        key: copy.deepcopy(specificity.get(key))
                        for key in (
                            "status",
                            "overall_status",
                            "verdict",
                            "summary",
                            "reason_codes",
                        )
                        if specificity.get(key) is not None
                    },
                    "amplicon_structure": {
                        key: copy.deepcopy(structure.get(key))
                        for key in (
                            "status",
                            "mfe_dg",
                            "most_stable_dg",
                            "temperature_celsius",
                            "confidence",
                            "tool",
                            "tool_version",
                            "warnings",
                        )
                        if structure.get(key) is not None
                    },
                }
            )
        return {**common, "members": _page(members, cursor, min(limit, 6))}
    p4_result = panel.get("p4_result") or {}
    heterodimer = p4_result.get("heterodimer") or {}
    return {
        **common,
        "evidence": {
            "p4_result": {
                key: copy.deepcopy(p4_result.get(key))
                for key in (
                    "status",
                    "verdict",
                    "spectral_check",
                    "quencher_check",
                    "instrument_check",
                    "tm_spread_celsius",
                    "gc_spread_percent",
                    "issues",
                    "thresholds",
                )
                if p4_result.get(key) is not None
            }
            | {
                "heterodimer": {
                    key: copy.deepcopy(heterodimer.get(key))
                    for key in (
                        "total_pairs",
                        "evaluated_pairs",
                        "worst_pair",
                        "worst_dg_kcal",
                        "worst_dg_cal_per_mol",
                        "worst_three_prime_dg_kcal",
                    )
                    if heterodimer.get(key) is not None
                }
            },
            "balance": copy.deepcopy(panel.get("balance") or {}),
            "amplicon_length_check": copy.deepcopy(
                panel.get("amplicon_length_check") or {}
            ),
            "warnings": copy.deepcopy(panel.get("warnings") or []),
        },
    }


def _host_ui_result(
    result: T | dict[str, Any],
) -> T | dict[str, Any] | CallToolResult:
    """Promote UI data while keeping model-visible content bounded.

    FastMCP normally serializes every structured ``dict`` into both
    ``structuredContent`` and a text block in ``content``. HiMat consumes the
    former when available, but its compatibility path reconstructs the final
    assistant message from the latter. UI objects are therefore mirrored as
    exact compact JSON in both channels. This prevents the model from turning
    attachments into prose or Markdown links that the frontend cannot render.
    """
    if not isinstance(result, dict):
        return result
    ui_message = (
        result
        if result.get("type") in {"agent_interaction", "primer_design_output"}
        else result.get("ui_message")
    )
    if not isinstance(ui_message, dict):
        retained = result.get("summary")
        if isinstance(retained, dict):
            ui_message = retained.get("ui_message")
    if isinstance(ui_message, dict):
        if ui_message.get("type") == "primer_design_output":
            # Keep the MCP business payload exactly aligned with the host's
            # PrimerDesignOutputData contract. `block_data` and `blockId` are
            # transport fields added later by HiMat, never by Agent D.
            payload = {
                "data": ui_message.get("data"),
                "type": "primer_design_output",
            }
            batch_code = result.get("batch_code")
            workflow = result.get("workflow")
            if not workflow and str(batch_code or "").startswith("WF01-"):
                workflow = "WF-01"
            elif not workflow and str(batch_code or "").startswith("WF02-"):
                workflow = "WF-02"
            workflow_state = {
                "batchId": result.get("batch_id") or result.get("job_id"),
                "batchCode": batch_code,
                "nextAction": result.get("next_action") or "COMPLETE",
                "terminal": True,
                "stopCurrentTurn": True,
                "retainAcrossTurns": True,
            }
            if workflow:
                workflow_state["workflow"] = workflow
            if result.get("job_id"):
                workflow_state["jobId"] = result.get("job_id")
            return CallToolResult(
                **{"_meta": {"oligosmith": {"workflowState": workflow_state}}},
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(
                            payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    )
                ],
                structuredContent=payload,
            )
        if ui_message.get("type") == "agent_interaction":
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(
                            ui_message,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    )
                ],
                structuredContent=ui_message,
            )
    tool_control = result.get("tool_control")
    if result.get("workflow") == "WF-02" and isinstance(tool_control, dict):
        workflow_state = {
            "workflow": "WF-02",
            "batchId": result.get("batch_id"),
            "batchCode": result.get("batch_code"),
            "nextAction": result.get("next_action"),
            "terminal": False,
            "stopCurrentTurn": not bool(tool_control.get("call_again")),
            "continueCurrentTurn": bool(tool_control.get("call_again")),
            "suppressAssistantText": bool(
                tool_control.get("suppress_intermediate_text")
            ),
            "nextTool": tool_control.get("next_tool"),
            "nextArguments": copy.deepcopy(
                tool_control.get("next_arguments")
            ),
            "retainAcrossTurns": True,
        }
        return CallToolResult(
            **{"_meta": {"oligosmith": {"workflowState": workflow_state}}},
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(
                        result,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
            ],
            structuredContent=result,
        )
    return result


def _wf01_create_payload(
    *,
    sequence: str | None,
    input_artifact_id: str | None,
    target_organism: str,
    host: str | None,
    task_name: str | None,
) -> dict[str, Any]:
    """Build the only model-facing WF-01 payload for a full-route task."""
    has_sequence = isinstance(sequence, str) and bool(sequence.strip())
    has_artifact = isinstance(input_artifact_id, str) and bool(input_artifact_id.strip())
    if has_sequence == has_artifact:
        raise ToolError(
            json.dumps(
                {
                    "code": "INVALID_INPUT",
                    "message": "必须且只能提供 sequence 或 input_artifact_id 之一",
                    "details": {
                        "sequence_source_fields": ["sequence", "input_artifact_id"],
                        "upload_flow": "download_himat_file → create_wf01_batch",
                    },
                },
                ensure_ascii=False,
            )
        )
    if not isinstance(target_organism, str) or not target_organism.strip():
        raise ToolError(
            json.dumps(
                {
                    "code": "MISSING_TARGET_ORGANISM",
                    "message": "完整 WF-01 路线必须提供靶标物种或病原名称",
                    "details": {
                        "required_field": "target_organism",
                        "design_only_fallback_allowed": False,
                        "next_action": "ASK_USER_FOR_TARGET_ORGANISM",
                    },
                },
                ensure_ascii=False,
            )
        )
    payload: dict[str, Any] = {}
    if has_sequence:
        assert isinstance(sequence, str)
        stripped_sequence = sequence.strip()
        if stripped_sequence.startswith(">"):
            payload["fasta_text"] = stripped_sequence
        else:
            payload["sequence"] = stripped_sequence
    else:
        assert isinstance(input_artifact_id, str)
        payload["input_artifact_id"] = input_artifact_id.strip()
    payload["target_organism"] = target_organism.strip()
    if isinstance(host, str) and host.strip():
        payload["host"] = host.strip()
    if isinstance(task_name, str) and task_name.strip():
        payload["task_name"] = task_name.strip()
    return payload


@mcp.tool(
    description=(
        "file_id→顶层input_artifact_id；WF-01/WF-03/WF-04直接复用，"
        "WF-02另含downstream.wf02.target；"
        "FASTA 正文由 AgentD 后端解析。"
    )
)
def download_himat_file(file_id: int) -> dict[str, Any]:
    def execute() -> dict[str, Any]:
        result = service.download_himat_file(file_id)
        artifact_id = str(result.get("input_artifact_id") or "")
        file_name = str(result.get("file_name") or f"file-{file_id}")
        suggested_name = Path(file_name).stem.strip() or f"file-{file_id}"
        result["downstream"] = {
            "wf01": {
                "tool": "create_wf01_batch",
                "input_artifact_id": artifact_id,
            },
            "wf02": {
                "tool": "create_wf02_batch",
                "target": {
                    "display_name": suggested_name,
                    "input_artifact_id": artifact_id,
                },
            },
        }
        return result

    return _invoke(
        "download_himat_file",
        execute,
        audit_input={"file_id": file_id},
    )


@mcp.tool(
    description=(
        "新设计唯一入口，只运行完整 WF-01；target_organism 必填。后端自动执行 M2-M6 与"
        "P2/BLAST，禁止 DESIGN_ONLY。返回 primer_design_output 即最终交付已完成：原样输出"
        "content[0].text 并立即结束，本轮不得再调用工具；仅响应用户后续明确请求时调用其他工具。"
    )
)
def create_wf01_batch(
    target_organism: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "必填：用户明确提供的靶标物种或病原名称。缺少时先询问用户，不得猜测或降级。"
            ),
        ),
    ],
    sequence: Annotated[
        str | None,
        Field(
            description=(
                "用户粘贴的模板 DNA 序列。可包含空白、换行或单条 FASTA 标题；"
                "不得省略用户消息中的序列。"
            )
        ),
    ] = None,
    input_artifact_id: Annotated[
        str | None,
        Field(
            description=(
                "download_himat_file 返回的内部附件引用；与 sequence 二选一。"
            )
        ),
    ] = None,
    host: Annotated[
        str | None,
        Field(description="可选宿主名称；用户明确提供时传入，未提供时省略且不得猜测。"),
    ] = None,
    task_name: Annotated[
        str | None,
        Field(description="可选任务名或序列记录名，例如 CR_0010。"),
    ] = None,
) -> dict[str, Any]:
    prepared_payload = _wf01_create_payload(
        sequence=sequence,
        input_artifact_id=input_artifact_id,
        target_organism=target_organism,
        host=host,
        task_name=task_name,
    )

    def execute() -> dict[str, Any]:
        batch = service.create_wf01_v02_batch(
            prepared_payload,
            include_input_mapping=True,
            interaction_mode="NO_POPUPS",
            require_full_route=True,
        )
        mapping = batch.get("mcp_input_mapping") or {}
        execution_paths = {
            str(item.get("execution_path") or "")
            for item in (batch.get("target_manifest") or {}).get("targets") or []
        }
        if execution_paths != {"FULL_SPECIFICITY"}:
            raise InputValidationError(
                "新建 WF-01 任务未进入完整特异性路线，已停止执行",
                details={
                    "execution_paths": sorted(execution_paths),
                    "required_execution_path": "FULL_SPECIFICITY",
                    "design_only_fallback_allowed": False,
                },
            )
        if batch.get("status") == "QUEUED":
            result = service.run_wf01_v02_until_blocked(str(batch["batch_id"]))
        else:
            result = summarize_wf01_batch(batch, top_candidates_per_target=0)
        result["mcp_input_mapping"] = mapping
        return result

    return _host_ui_result(
        _invoke(
            "wf01_create_batch",
            execute,
            audit_input={
                "payload": prepared_payload,
                "full_route_required": True,
            },
        )
    )


@mcp.tool(
    description=(
        "创建同管 2–5 靶标 WF-02；上传目标采用 download_himat_file 返回的 "
        "input_artifact_id；缺失字段由 N04 交互。"
    )
)
def create_wf02_batch(
    targets: Annotated[
        list[str | dict[str, Any]] | None,
        Field(
            description=(
                "同管检测的 2–5 个目标靶标（内标不计入重数）；"
                "上传文件对象包含 target_id/display_name 及 "
                "download_himat_file 返回的 input_artifact_id。"
                "每个文件绑定一个 target，文件内记录作为同源序列。"
            )
        ),
    ] = None,
    host_species: Annotated[
        str | None,
        Field(description="待测样本对应的宿主物种；复用本轮及此前对话已提供的值。"),
    ] = None,
    include_internal_control: Annotated[
        bool | None,
        Field(description="是否需要内标；用户未说明时省略，由后端默认设为需要。"),
    ] = None,
    task_name: Annotated[
        str | None,
        Field(description="可选的 WF-02 任务名称。"),
    ] = None,
    runtime_config: Annotated[
        dict[str, Any] | None,
        Field(
            description=(
                "非核心运行配置；用户明确指定时传 instrument 或 rox_as_reference；"
                "其余情况保持省略，由后端应用默认配置。"
            )
        ),
    ] = None,
    expected_target_count: Annotated[
        int | None,
        Field(
            ge=2,
            le=5,
            description=(
                "使用上传文件时必填：用户本轮明确上传或请求处理的目标文件总数；"
                "必须包含下载失败的文件。"
            ),
        ),
    ] = None,
    failed_file_ids: Annotated[
        list[int] | None,
        Field(
            description=(
                "本轮 download_himat_file 尚未成功的 file_id；非空时禁止创建正式任务。"
            )
        ),
    ] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if targets is not None:
        payload["targets"] = copy.deepcopy(targets)
    if host_species is not None:
        payload["host_species"] = host_species
    if include_internal_control is not None:
        payload["include_internal_control"] = include_internal_control
    if task_name is not None:
        payload["task_name"] = task_name
    if runtime_config is not None:
        payload["runtime_config"] = copy.deepcopy(runtime_config)
    if expected_target_count is not None or failed_file_ids:
        payload["upload_intake_manifest"] = {
            "expected_target_count": expected_target_count,
            "failed_file_ids": copy.deepcopy(failed_file_ids or []),
        }

    def execute() -> dict[str, Any]:
        inline_sources: list[dict[str, Any]] = []
        artifact_target_indexes: list[int] = []
        for index, target in enumerate(payload.get("targets") or [], start=1):
            if not isinstance(target, dict):
                continue
            if target.get("input_artifact_id") not in (None, ""):
                artifact_target_indexes.append(index)
            received = [
                field
                for field in (
                    "sequence",
                    "sequences",
                    "template_records",
                    "fasta_text",
                    "content",
                    "fasta_path",
                )
                if target.get(field) not in (None, "", [], {})
            ]
            sequence_files = target.get("sequence_files")
            if isinstance(sequence_files, list):
                for item in sequence_files:
                    if not isinstance(item, dict):
                        continue
                    received.extend(
                        f"sequence_files.{field}"
                        for field in ("fasta_text", "content")
                        if item.get(field) not in (None, "")
                    )
            if received:
                inline_sources.append(
                    {
                        "target_index": index,
                        "target_id": target.get("target_id"),
                        "source_fields": sorted(set(received)),
                    }
                )
        if inline_sources:
            raise InputValidationError(
                "WF-02 上传附件请先调用 download_himat_file，再使用返回的 input_artifact_id 建单",
                details={
                    "next_tool": "download_himat_file",
                    "accepted_source_field": "input_artifact_id",
                    "targets": inline_sources,
                },
            )
        if artifact_target_indexes and expected_target_count is None:
            raise InputValidationError(
                "WF-02 上传建单必须声明用户本轮上传文件总数",
                details={
                    "code": "WF02_UPLOAD_EXPECTED_TARGET_COUNT_REQUIRED",
                    "required_argument": "expected_target_count",
                    "artifact_target_indexes": artifact_target_indexes,
                    "next_action": "RETRY_CREATE_WITH_UPLOAD_COUNT",
                },
            )
        received_target_count = len(payload.get("targets") or [])
        if expected_target_count is not None and (
            received_target_count != expected_target_count or failed_file_ids
        ):
            raise InputValidationError(
                "WF-02 上传文件尚未完整接收，不能创建正式任务",
                details={
                    "code": "WF02_UPLOAD_INTAKE_INCOMPLETE",
                    "expected_target_count": expected_target_count,
                    "received_target_count": received_target_count,
                    "failed_file_ids": copy.deepcopy(failed_file_ids or []),
                    "next_tool": "download_himat_file",
                    "next_action": "RETRY_FAILED_DOWNLOADS",
                },
            )
        result = service.create_wf02_v01_batch(payload)
        if result.get("batch_id") and result.get("user_input_snapshot"):
            return _wf02_tool_control(summarize_wf02_batch(result))
        return result

    return _host_ui_result(
        _invoke(
            "wf02_create_batch",
            execute,
            audit_input={"payload": payload},
        )
    )


@mcp.tool(
    description="提交 WF-02 N04 的全部交互回复；校验通过后创建任务。"
)
def submit_wf02_input_response(
    draft_id: str,
    response: dict[str, Any],
) -> dict[str, Any]:
    def execute() -> dict[str, Any]:
        result = service.submit_wf02_v01_input_response(draft_id, response)
        batch = result.get("batch")
        if isinstance(batch, dict):
            summary = summarize_wf02_batch(batch)
            summary["interaction_submission"] = {
                "draft_id": draft_id,
                "submission_replayed": bool(result.get("submission_replayed")),
            }
            return _wf02_tool_control(summary)
        return result

    return _host_ui_result(
        _invoke(
            "wf02_submit_input_response",
            execute,
            audit_input={"draft_id": draft_id, "response": response},
        )
    )


@mcp.tool(
    description=(
        "用户明确查询或恢复丢失状态时读取 WF-02；CALL_TOOL_NOW 按 "
        "next_arguments 恢复；终态输出裸 JSON。"
    )
)
def get_wf02_batch(
    batch_id: str,
    view: Literal["summary", "target", "trace", "events", "relaxation"] = "summary",
    target_id: str | None = None,
    cursor: Annotated[int, Field(ge=0)] = 0,
    limit: Annotated[int, Field(ge=1, le=50)] = 20,
) -> dict[str, Any]:
    return _host_ui_result(
        _invoke(
            "wf02_get_batch",
            lambda: _wf02_tool_control(
                _wf02_batch_view(
                    batch_id,
                    view=view,
                    target_id=target_id,
                    cursor=cursor,
                    limit=limit,
                )
            ),
            audit_input={
                "batch_id": batch_id,
                "view": view,
                "target_id": target_id,
                "cursor": cursor,
                "limit": limit,
            },
        )
    )


@mcp.tool(
    description=(
        "连续推进多个已持久化 WF-02 单元；CALL_TOOL_NOW 时无文字、按 "
        "next_arguments 立即续跑；旧 token 自动恢复，STOP_CURRENT_TURN 才停止。"
    )
)
def run_wf02_until_blocked(
    batch_id: str,
    resume_token: str | None = None,
) -> dict[str, Any]:
    def execute() -> dict[str, Any]:
        return _run_wf02_execution_window(batch_id, resume_token)

    return _host_ui_result(
        _invoke(
            "wf02_run_until_blocked",
            execute,
            audit_input={"batch_id": batch_id},
        )
    )


@mcp.tool(
    description=(
        "提交某靶标的模板或 WF-01 交互回复，并恢复 WF-02 至下一交互边界。"
    )
)
def submit_wf02_child_response(
    batch_id: str,
    target_id: str,
    response: dict[str, Any],
) -> dict[str, Any]:
    def execute() -> dict[str, Any]:
        return _wf02_tool_control(
            service.wf02_v01.submit_child_response(
                batch_id,
                target_id,
                response,
                bounded=True,
            )
        )

    return _host_ui_result(
        _invoke(
            "wf02_submit_child_response",
            execute,
            audit_input={
                "batch_id": batch_id,
                "target_id": target_id,
                "response": response,
            },
        )
    )


@mcp.tool(
    description=(
        "本工具用于恢复旧版 WF-02 N12/N13 待处理内标交互；"
        "新任务的 P5 或通道 FAIL 由流程自动切换为无内标面板。"
    )
)
def submit_wf02_internal_control_response(
    batch_id: str,
    response: dict[str, Any],
) -> dict[str, Any]:
    def execute() -> dict[str, Any]:
        return _wf02_tool_control(
            service.wf02_v01.submit_internal_control_response(
                batch_id,
                response,
                bounded=True,
            )
        )

    return _host_ui_result(
        _invoke(
            "wf02_submit_internal_control_response",
            execute,
            audit_input={"batch_id": batch_id, "response": response},
        )
    )


@mcp.tool(
    description=(
        "基于已完成的 WF-02 N32 报告创建不可变新版本；"
        "用户输入变化和仪器/ROX 运行配置变化分开记录，不覆盖旧报告。"
    )
)
def create_wf02_version(
    batch_id: str,
    reason: str | None = None,
    user_input_changes: dict[str, Any] | None = None,
    runtime_config_changes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    options: dict[str, Any] = {}
    if reason is not None:
        options["reason"] = reason
    if user_input_changes is not None:
        options["user_input_changes"] = copy.deepcopy(user_input_changes)
    if runtime_config_changes is not None:
        options["runtime_config_changes"] = copy.deepcopy(
            runtime_config_changes
        )
    return _invoke(
        "wf02_create_version",
        lambda: service.create_wf02_v01_version(batch_id, options),
        audit_input={"batch_id": batch_id, "options": options},
    )


@mcp.tool(
    description=(
        "分页读取 WF-02 终态摘要、面板索引、失败或追踪；"
        "完整面板使用 get_wf02_panel。"
    )
)
def get_wf02_report(
    batch_id: str,
    section: Literal["summary", "panels", "failure", "trace"] = "summary",
    cursor: Annotated[int, Field(ge=0)] = 0,
    limit: Annotated[int, Field(ge=1, le=50)] = 20,
) -> dict[str, Any]:
    return _invoke(
        "wf02_get_report",
        lambda: _wf02_report_view(
            service.get_wf02_v01_report(batch_id),
            section=section,
            cursor=cursor,
            limit=limit,
        ),
        audit_input={
            "batch_id": batch_id,
            "section": section,
            "cursor": cursor,
            "limit": limit,
        },
    )


@mcp.tool(description="分页读取 WF-02 指定面板的摘要、成员或证据。")
def get_wf02_panel(
    batch_id: str,
    panel_id: str,
    section: Literal["summary", "members", "evidence"] = "summary",
    cursor: Annotated[int, Field(ge=0)] = 0,
    limit: Annotated[int, Field(ge=1, le=10)] = 6,
) -> dict[str, Any]:
    return _invoke(
        "wf02_get_panel",
        lambda: _wf02_panel_view(
            service.get_wf02_v01_panel(batch_id, panel_id),
            section=section,
            cursor=cursor,
            limit=limit,
        ),
        audit_input={
            "batch_id": batch_id,
            "panel_id": panel_id,
            "section": section,
            "cursor": cursor,
            "limit": limit,
        },
    )


@mcp.tool(
    description=(
        "按明确请求生成并返回指定 WF-02 HTML/CSV/PDF 卡片；CSV/PDF 不预生成；"
        "RustFS 启用时返回公网 URL，否则返回短期签名 URL；"
        "标准下载仍需 Bearer JWT；"
        "卡片须原样交付。"
    )
)
def get_wf02_export(
    batch_id: str,
    format_name: Literal["html", "csv", "pdf"],
) -> dict[str, Any]:
    def resolve() -> dict[str, Any]:
        path = service.get_wf02_v01_export_path(batch_id, format_name)
        metadata = service.get_wf02_v01_export_metadata(batch_id, format_name)
        summary = service.get_wf02_v01_agent_summary(
            batch_id,
            attachment_formats=(format_name,),
        )
        return {
            "batch_id": batch_id,
            "format": format_name,
            "filename": path.name,
            "size_bytes": path.stat().st_size,
            "download_url": metadata.get("download_url"),
            "preview_url": metadata.get("preview_url"),
            "authorization": (
                "标准 REST 下载需要 Bearer JWT；返回卡片的 fileUrl 优先为 RustFS 完整 URL，"
                "未启用 RustFS 时为短期签名只读地址"
            ),
            "ui_message": summary.get("ui_message"),
        }

    return _host_ui_result(
        _invoke(
            "wf02_get_export",
            resolve,
            audit_input={"batch_id": batch_id, "format_name": format_name},
        )
    )


@mcp.tool(
    description=(
        "WF-03 新任务唯一入口；附件必须先调用 download_himat_file，并将其顶层"
        "input_artifact_id原样传入，禁止跳过下载。无附件字段齐全时必须立即调用；"
        "禁止在调用前分析，缺字段只能询问该字段并停止。"
    )
)
def run_wf03_internal_control(
    host_species: Annotated[
        str,
        Field(description="必填宿主物种；支持猪/pig、牛/cattle、鸡/chicken及对应学名。"),
    ],
    input_artifact_id: Annotated[
        str | None,
        Field(
            description=(
                "附件存在时必填；必须原样使用 download_himat_file 返回的顶层"
                "input_artifact_id，禁止传 file_id、正文或模型重建内容。"
            )
        ),
    ] = None,
    input_gene: Annotated[
        str | None,
        Field(
            description=(
                "可选附件输入标签；只复制用户明确给出的标签。省略时后端按单个 "
                "preferred_genes 或安全文件名确定，不读取文件内容让模型推断。"
            )
        ),
    ] = None,
    existing_targets: Annotated[
        list[dict[str, Any]] | None,
        Field(
            description=(
                "可选已有靶标方案，用于P5内部交叉二聚体检查；每项提供target_id、"
                "fwd_seq、rev_seq及可选probe_seq/fluorophore，也接受WF候选的嵌套寡核苷酸对象。"
            )
        ),
    ] = None,
    preferred_genes: Annotated[
        list[str | dict[str, Any]] | None,
        Field(
            description=(
                "仅在用户明确指定时传入的管家基因顺序；对象可同时包含gene和refseq_accession。"
                "未提供时由P5按ACTB、GAPDH、18S rRNA确定性选择。"
            )
        ),
    ] = None,
    gene_accessions: Annotated[
        dict[str, str] | None,
        Field(description="可选自定义基因到RefSeq编号的映射；不得由LLM猜测。"),
    ] = None,
    gene_sequences: Annotated[
        dict[str, list[Any] | dict[str, Any]] | None,
        Field(description="可选用户提供的版本化同源序列集合；每个基因至少5条。"),
    ] = None,
    host_db: Annotated[
        str | None,
        Field(description="可选已配置宿主BLAST数据库标识或路径；未提供时由后端目录解析。"),
    ] = None,
    cross_db: Annotated[
        str | None,
        Field(description="可选已配置近缘种/共感染病原BLAST数据库标识或路径。"),
    ] = None,
    assay_id: Annotated[
        str | None,
        Field(description="可选任务标识；省略时由后端按宿主生成。"),
    ] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"host_species": host_species}
    optional_values = {
        "input_artifact_id": input_artifact_id,
        "input_gene": input_gene,
        "existing_targets": existing_targets,
        "preferred_genes": preferred_genes,
        "gene_accessions": gene_accessions,
        "gene_sequences": gene_sequences,
        "host_db": host_db,
        "cross_db": cross_db,
        "assay_id": assay_id,
    }
    for key, value in optional_values.items():
        if value is not None:
            payload[key] = copy.deepcopy(value)

    def execute() -> dict[str, Any]:
        result = service.run_wf03_internal_control(payload)
        return service.get_wf03_delivery(str(result["job_id"]))

    return _host_ui_result(
        _invoke(
            "wf03_run_internal_control",
            execute,
            audit_input={"payload": payload},
        )
    )


@mcp.tool(
    description=(
        "WF-03不可变重入；后端恢复父输入、应用白名单补丁并返回primer_design_output。"
    )
)
def retry_wf03_internal_control(
    job_id: Annotated[str, Field(description="需要重入的失败WF-03 job_id。")],
    preferred_genes: Annotated[
        list[str | dict[str, Any]] | None,
        Field(description="用户补充的管家基因顺序，可含gene和refseq_accession。"),
    ] = None,
    gene_accessions: Annotated[
        dict[str, str] | None,
        Field(description="用户补充的基因到RefSeq编号映射。"),
    ] = None,
    gene_sequences: Annotated[
        dict[str, list[Any] | dict[str, Any]] | None,
        Field(description="用户补充的版本化同源序列集合。"),
    ] = None,
    existing_targets: Annotated[
        list[dict[str, Any]] | None,
        Field(description="用户修正后的已有靶标寡核苷酸；未修改时省略。"),
    ] = None,
    host_db: Annotated[
        str | None,
        Field(description="补充或修正的宿主BLAST数据库标识或路径。"),
    ] = None,
    cross_db: Annotated[
        str | None,
        Field(description="补充或修正的交叉物种BLAST数据库标识或路径。"),
    ] = None,
    reason: Annotated[
        str | None,
        Field(description="可选重入原因；只记录审计，不参与科学判定。"),
    ] = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {}
    optional_values = {
        "preferred_genes": preferred_genes,
        "gene_accessions": gene_accessions,
        "gene_sequences": gene_sequences,
        "existing_targets": existing_targets,
        "host_db": host_db,
        "cross_db": cross_db,
        "reason": reason,
    }
    for key, value in optional_values.items():
        if value is not None:
            response[key] = copy.deepcopy(value)

    def execute() -> dict[str, Any]:
        result = service.retry_wf03_internal_control(job_id, response)
        return service.get_wf03_delivery(str(result["job_id"]))

    return _host_ui_result(
        _invoke(
            "wf03_retry_internal_control",
            execute,
            audit_input={"job_id": job_id, "response": response},
        )
    )


@mcp.tool(
    description=(
        "读取WF-03持久化终态并返回primer_design_output；禁止模型重算。"
    )
)
def get_wf03_report(job_id: str) -> dict[str, Any]:
    return _host_ui_result(
        _invoke(
            "wf03_get_report",
            lambda: service.get_wf03_delivery(job_id),
            audit_input={"job_id": job_id},
        )
    )


@mcp.tool(
    description=(
        "WF-04新任务唯一入口；附件先download_himat_file并将其顶层"
        "input_artifact_id原样传入。禁止调用前分析；节点由后端执行并返回"
        "primer_design_output。"
    )
)
def run_wf04_validation(
    forward_primer: Annotated[
        str | None,
        Field(min_length=1, description="无附件时必填的正向输入序列。"),
    ] = None,
    reverse_primer: Annotated[
        str | None,
        Field(min_length=1, description="无附件时必填的反向输入序列。"),
    ] = None,
    input_artifact_id: Annotated[
        str | None,
        Field(
            description=(
                "附件存在时必填；必须原样使用download_himat_file返回的顶层"
                "input_artifact_id。正文只由后端解析。"
            )
        ),
    ] = None,
    probe: Annotated[
        str | None,
        Field(description="可选的第三输入序列。"),
    ] = None,
    template: Annotated[
        str | None,
        Field(description="可选的上下文序列。"),
    ] = None,
    target_id: Annotated[
        str | None,
        Field(description="可选任务对象标识。"),
    ] = None,
    target_species: Annotated[
        str | None,
        Field(description="可选分支上下文。"),
    ] = None,
    host_species: Annotated[
        str | None,
        Field(description="可选排除分支上下文。"),
    ] = None,
    target_db: Annotated[
        str | None,
        Field(description="Step 2 数据源标识或路径。"),
    ] = None,
    host_db: Annotated[
        str | None,
        Field(description="Step 3 数据源标识或路径。"),
    ] = None,
    cross_db: Annotated[
        str | None,
        Field(description="Step 4 数据源标识或路径。"),
    ] = None,
    use_mgb: Annotated[
        bool,
        Field(description="是否启用 Step 5 的条件分支。"),
    ] = False,
    probe_type: Annotated[
        str | None,
        Field(description="可选 Step 5 分支类型。"),
    ] = None,
    fluorophore: Annotated[
        str | None,
        Field(description="可选 Step 5 参数。"),
    ] = None,
    snp_position: Annotated[
        int | None,
        Field(ge=0, description="可选 Step 5 位置参数。"),
    ] = None,
    assay_id: Annotated[
        str | None,
        Field(description="可选任务结果标识。"),
    ] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"use_mgb": use_mgb}
    optional_values = {
        "forward_primer": forward_primer,
        "reverse_primer": reverse_primer,
        "input_artifact_id": input_artifact_id,
        "probe": probe,
        "template": template,
        "target_id": target_id,
        "target_species": target_species,
        "host_species": host_species,
        "target_db": target_db,
        "host_db": host_db,
        "cross_db": cross_db,
        "probe_type": probe_type,
        "fluorophore": fluorophore,
        "snp_position": snp_position,
        "assay_id": assay_id,
    }
    for key, value in optional_values.items():
        if value is not None:
            payload[key] = value

    def execute() -> dict[str, Any]:
        result = service.run_wf04_validation(payload)
        return service.get_wf04_delivery(str(result["job_id"]))

    return _host_ui_result(
        _invoke(
            "wf04_run_validation",
            execute,
            audit_input={"payload": payload},
        )
    )


@mcp.tool(
    description=(
        "按retryable_steps创建WF-04不可变子任务并返回primer_design_output。"
    )
)
def retry_wf04_validation(
    job_id: Annotated[str, Field(description="需要重试的 WF-04 job_id。")],
    retry_steps: Annotated[
        list[
            Literal[
                "WF04_STEP1_P1",
                "WF04_STEP2_P2_A",
                "WF04_STEP3_P2_B",
                "WF04_STEP5_COMPATIBILITY",
            ]
        ]
        | None,
        Field(description="可选；默认使用上次报告的 retryable_steps。"),
    ] = None,
    target_db: Annotated[
        str | None,
        Field(description="可选 Step 2 替换数据源。"),
    ] = None,
    host_db: Annotated[
        str | None,
        Field(description="可选 Step 3 替换数据源。"),
    ] = None,
    cross_db: Annotated[
        str | None,
        Field(description="可选 Step 4 替换数据源。"),
    ] = None,
    target_species: Annotated[
        str | None,
        Field(description="可选分支上下文补丁。"),
    ] = None,
    host_species: Annotated[
        str | None,
        Field(description="可选排除分支上下文补丁。"),
    ] = None,
    template: Annotated[
        str | None,
        Field(description="可选上游上下文补丁。"),
    ] = None,
    reason: Annotated[
        str | None,
        Field(description="可选重试原因。"),
    ] = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {}
    optional_values = {
        "retry_steps": retry_steps,
        "target_db": target_db,
        "host_db": host_db,
        "cross_db": cross_db,
        "target_species": target_species,
        "host_species": host_species,
        "template": template,
        "reason": reason,
    }
    for key, value in optional_values.items():
        if value is not None:
            response[key] = copy.deepcopy(value)

    def execute() -> dict[str, Any]:
        result = service.retry_wf04_validation(job_id, response)
        return service.get_wf04_delivery(str(result["job_id"]))

    return _host_ui_result(
        _invoke(
            "wf04_retry_validation",
            execute,
            audit_input={"job_id": job_id, "response": response},
        )
    )


@mcp.tool(
    description=(
        "读取WF-04持久化终态并返回primer_design_output；禁止模型重算。"
    )
)
def get_wf04_report(job_id: str) -> dict[str, Any]:
    return _host_ui_result(
        _invoke(
            "wf04_get_report",
            lambda: service.get_wf04_delivery(job_id),
            audit_input={"job_id": job_id},
        )
    )


@mcp.tool(
    description=(
        "读取当前 WF-01 摘要；已完成时重新返回最终 primer_design_output 报告卡片。"
        "用户要求查看、下载、下载地址或再次显示卡片时调用；完成后必须把 "
        "content[0].text 裸 JSON 原样作为唯一回复，禁止改写成 Markdown 链接。"
    )
)
def get_wf01_batch(batch_id: WF01BatchReference) -> dict[str, Any]:
    return _host_ui_result(
        _invoke(
            "wf01_get_batch",
            lambda: service.get_wf01_v02_agent_summary(batch_id),
            audit_input={"batch_id": batch_id},
        )
    )


@mcp.tool(
    description=(
        "在 M2 开始前按靶标修改或移除 WF-01 靶标清单，并重新执行逐靶标预检。"
        "payload 使用 target_changes 和/或 remove_target_ids；不会运行 Primer3。"
        "应根据用户明确提交的表单内容调用，不得猜测物种、宿主或序列。"
    )
)
def revise_wf01_target_manifest(
    batch_id: WF01BatchReference,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _host_ui_result(
        _invoke(
            "wf01_revise_target_manifest",
            lambda: summarize_wf01_batch(
                service.revise_wf01_v02_target_manifest(batch_id, payload),
                top_candidates_per_target=0,
            ),
            audit_input={"batch_id": batch_id, "payload": payload},
        )
    )


@mcp.tool(
    description=(
        "提交当前全部 WAITING_USER 的完整 role=user agent_interaction_response，并按 "
        "next_action 继续。agent_interaction 仅用于旧任务；完成时用 content[0].text "
        "说明实际路线，最终报告由 structuredContent 弹窗展示。"
    )
)
def submit_wf01_interaction_response(
    batch_id: WF01BatchReference,
    response: dict[str, Any],
    resume_automatically: bool = True,
) -> dict[str, Any]:
    def execute() -> dict[str, Any]:
        routed = service.submit_wf01_v02_interaction_response(batch_id, response)
        routed_batch_id = str(routed.get("batch_id") or batch_id)
        if resume_automatically and routed.get("next_action") in {
            "RUN_M2",
            "RUN_M6",
        }:
            result = service.run_wf01_v02_until_blocked(routed_batch_id)
        else:
            batch = routed.get("batch")
            if not isinstance(batch, dict):
                batch = service.get_wf01_v02_batch(routed_batch_id)
            result = summarize_wf01_batch(batch, top_candidates_per_target=0)
        result["interaction_submission"] = {
            "source_batch_id": batch_id,
            "resolved_interaction_ids": routed.get("resolved_interaction_ids") or [],
            "new_version_created": bool(routed.get("new_version_created")),
            "submission_replayed": bool(routed.get("submission_replayed")),
        }
        return result

    return _host_ui_result(
        _invoke(
            "wf01_submit_interaction_response",
            execute,
            audit_input={
                "batch_id": batch_id,
                "response": response,
                "resume_automatically": resume_automatically,
            },
        )
    )


@mcp.tool(
    description=(
        "仅用于问题修正、超时中断或旧任务恢复；新任务由 create_wf01_batch 自动运行。"
        "从当前持久化阶段继续，直到用户交互或 M6 报告生成完成。每个恢复轮次最多调用一次；"
        "WAIT_FOR_STAGE_COMPLETION 时不重试。完成时用 content[0].text 说明实际路线，"
        "最终报告由 structuredContent 弹窗展示。"
    )
)
def run_wf01_until_blocked(
    batch_id: WF01BatchReference,
    stage_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _host_ui_result(
        _invoke(
            "wf01_run_until_blocked",
            lambda: service.run_wf01_v02_until_blocked(
                batch_id,
                stage_options or {},
            ),
            audit_input={
                "batch_id": batch_id,
                "stage_options": stage_options or {},
            },
        )
    )


@mcp.tool(description="基于 WF-01 批次创建不可变的 M7 重新设计版本。")
def create_wf01_version(
    batch_id: WF01BatchReference,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _invoke(
        "wf01_create_version",
        lambda: service.create_wf01_v02_version(batch_id, options or {}),
        audit_input={"batch_id": batch_id, "options": options or {}},
    )


@mcp.tool(
    description=(
        "读取紧凑的最终报告摘要；提供 target_id 时返回紧凑的候选分页视图。完整规范化"
        "JSON 不会进入模型上下文。跟进请求需要列出或解释活动已完成批次的排名前 10 时，"
        "应使用本工具。"
    )
)
def get_wf01_report(
    batch_id: WF01BatchReference,
    target_id: str | None = None,
    candidate_scope: Literal[
        "top10", "deliverable", "qualified", "excluded", "all"
    ] = "top10",
    page: int = 1,
    page_size: int = 10,
    include_rule_details: bool = False,
) -> dict[str, Any]:
    return _invoke(
        "wf01_get_report",
        lambda: service.get_wf01_v02_report_agent_view(
            batch_id,
            target_id=target_id,
            candidate_scope=candidate_scope,
            page=page,
            page_size=page_size,
            include_rule_details=include_rule_details,
        ),
        audit_input={
            "batch_id": batch_id,
            "target_id": target_id,
            "candidate_scope": candidate_scope,
            "page": page,
            "page_size": page_size,
            "include_rule_details": include_rule_details,
        },
    )


@mcp.tool(
    description=(
        "读取一个明确选中的 WF-01 候选。规则明细只针对该候选返回，绝不展开整个候选池。"
    )
)
def get_wf01_candidate(
    batch_id: WF01BatchReference,
    target_id: str,
    candidate_id: str,
    include_rule_details: bool = True,
) -> dict[str, Any]:
    return _invoke(
        "wf01_get_candidate",
        lambda: service.get_wf01_v02_candidate_agent_view(
            batch_id,
            target_id,
            candidate_id,
            include_rule_details=include_rule_details,
        ),
        audit_input={
            "batch_id": batch_id,
            "target_id": target_id,
            "candidate_id": candidate_id,
            "include_rule_details": include_rule_details,
        },
    )


@mcp.tool(
    description=(
        "返回 M6 主报告、过程详情、数据 ZIP、CSV 或 PDF。"
        "M6 默认生成两份 HTML 和一份 ZIP，21条规则报告仅在 ZIP 内。"
        "CSV/PDF 明确请求时按需生成并返回 primer_design_output；"
        "必须原样输出 content[0].text 裸 JSON，禁止自行列出 URL。"
    )
)
def get_wf01_export(
    batch_id: WF01BatchReference,
    format_name: Literal[
        "html",
        "process_html",
        "scientific_data",
        "csv",
        "pdf",
    ],
) -> dict[str, Any]:
    def resolve() -> dict[str, Any]:
        path: Path = service.get_wf01_v02_export_path(batch_id, format_name)
        metadata = service.get_wf01_v02_export_metadata(
            batch_id,
            format_name,
        )
        file_url = str(metadata["file_url"])
        media_types = {
            "html": "text/html; charset=utf-8",
            "process_html": "text/html; charset=utf-8",
            "rules_html": "text/html; charset=utf-8",
            "csv": "text/csv; charset=utf-8",
            "pdf": "application/pdf",
            "scientific_data": "application/zip",
        }
        result = {
            "batch_id": batch_id,
            "batch_code": metadata.get("batch_code"),
            "format": format_name,
            "filename": path.name,
            "size_bytes": path.stat().st_size,
            "download_url": file_url,
            "preview_url": (
                file_url
                if format_name in {"html", "process_html", "rules_html"}
                else None
            ),
            "file_url": file_url,
            "attachment": {
                "attachmentId": f"attachment_{format_name}",
                "fileName": path.name,
                "fileType": (
                    "ZIP"
                    if format_name == "scientific_data"
                    else "HTML"
                    if format_name in {"process_html", "rules_html"}
                    else format_name.upper()
                ),
                "mimeType": media_types[format_name],
                "fileUrl": file_url,
            },
            "authorization": (
                "RustFS 完整 URL 可直接下载"
                if file_url.startswith(("http://", "https://"))
                else (
                    "无需认证（公开只读）"
                    if auth_settings.public_wf01_artifacts
                    else "需要 Bearer JWT 认证"
                )
            ),
        }
        # Export generation mutates persisted M6 metadata. Re-read the batch so
        # the report card contains the newly generated CSV/PDF attachment and
        # can be rendered through the same contract as initial completion.
        summary = service.get_wf01_v02_agent_summary(batch_id)
        result["batch_id"] = summary.get("batch_id") or batch_id
        result["batch_code"] = summary.get("batch_code") or result.get(
            "batch_code"
        )
        result["ui_message"] = summary.get("ui_message")
        return result

    return _host_ui_result(
        _invoke(
            "wf01_get_export",
            resolve,
            audit_input={"batch_id": batch_id, "format_name": format_name},
        )
    )


@mcp.resource(
    "oligosmith://contracts/agent-interaction",
    name="OligoSmith 智能体交互弹窗契约",
    description="前端一对多用户决策对话框的 JSON 结构约束",
    mime_type="application/schema+json",
)
def agent_interaction_contract_resource() -> str:
    schema_path = PROJECT_ROOT / "schemas" / "agent-interaction.schema.json"
    return schema_path.read_text(encoding="utf-8")


@mcp.resource(
    "oligosmith://contracts/agent-interaction-response",
    name="OligoSmith 智能体交互回复契约",
    description="前端提交用户决策对话框答案的 JSON 结构约束",
    mime_type="application/schema+json",
)
def agent_interaction_response_contract_resource() -> str:
    schema_path = PROJECT_ROOT / "schemas" / "agent-interaction-response.schema.json"
    return schema_path.read_text(encoding="utf-8")


@mcp.resource(
    "oligosmith://contracts/primer-design-output",
    name="OligoSmith 引物设计输出契约",
    description="引物设计完成报告消息的 JSON 结构约束",
    mime_type="application/schema+json",
)
def primer_design_output_contract_resource() -> str:
    schema_path = PROJECT_ROOT / "schemas" / "primer-design-output.schema.json"
    return schema_path.read_text(encoding="utf-8")


@mcp.resource(
    "oligosmith://jobs/{job_id}",
    name="OligoSmith 任务结果",
    description="已持久化的 OligoSmith 工作流结果",
    mime_type="application/json",
)
def job_resource(job_id: str) -> str:
    result = _invoke("job_resource", lambda: service.get_job(job_id))
    return json.dumps(result, ensure_ascii=False)


@mcp.resource(
    "oligosmith://wf01/{batch_id}/report",
    name="OligoSmith WF-01 报告",
    description="WF-01 规范化完整报告",
    mime_type="application/json",
)
def wf01_report_resource(batch_id: str) -> str:
    result = _invoke(
        "wf01_report_resource",
        lambda: service.get_wf01_v02_report(batch_id),
    )
    return json.dumps(result, ensure_ascii=False)


# 先注册 MCP 路由，再把兼容 API 挂载到根路径，从而保留健康检查、就绪检查和报告下载，
# 同时避免重复添加 MCP 路径前缀。
transport_app = mcp.streamable_http_app()
transport_app.mount("/", compatibility_api)
auth_settings = JWTSettings.from_env()
app = JWTBearerMiddleware(
    transport_app,
    auth_settings,
    public_paths={"/health", "/ready"},
)

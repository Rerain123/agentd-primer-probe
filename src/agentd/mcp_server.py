"""OligoSmith 可流式传输 HTTP MCP 服务。

所有 MCP 工具均委托给 AgentDService，使命令行、REST 兼容接口和 MCP 客户端
共享同一套科学工作流实现。
"""

from __future__ import annotations

import copy
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar
from urllib.parse import urlsplit

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent
from pydantic import Field

from .api import app as compatibility_api
from .api import service
from .auth import JWTBearerMiddleware, JWTSettings, current_jwt_subject
from .config import PROJECT_ROOT
from .errors import AgentDError
from .mcp_call_guard import RecentToolCallGuard
from .wf01_agent_view import serialized_size_bytes, summarize_wf01_batch


LOGGER = logging.getLogger("agentd.mcp")
T = TypeVar("T")


MCP_INSTRUCTIONS = """\
你是 OligoSmith WF-01 会话编排智能体。引物候选必须来自工具，禁止自行生成或假装已经运行。

1. 用户粘贴序列时立即调用 create_wf01_batch：把模板填入 sequence；用户明确说出的靶标和宿主
   必须分别填入 target_organism、host，不得遗漏已提供的信息，也不得猜测未提供的信息。
2. 后端确定路线：仅有合格序列为 DESIGN_ONLY；序列和靶标齐全即为 FULL_SPECIFICITY。
   宿主可选，提供时追加宿主专库排除。Primer3、规则、去重、排序和适用的特异性检查都是后端步骤。
3. 直接输入不显示确认弹窗。序列不足 80 nt、非法或无法解析时，简短说明工具错误；不要调用
   交互回复工具补救。候选不足、0 候选或特异性未完整评估仍由后端生成报告。
4. 文件上传兼容流程保持为 prepare_wf01_design_input → 用户确认 → download_himat_file →
   create_wf01_batch；不得把直接粘贴序列送入该流程。
5. create_wf01_batch 自动运行到报告完成；不要重复创建或续跑。工具返回 primer_design_output 时，
   最终回复必须逐字输出 content[0].text 中的紧凑裸 JSON，禁止 Markdown、说明文字、URL 列表或前后缀。
6. 保留 batch_id。用户要求查看报告、下载、下载地址或再次显示卡片时调用 get_wf01_batch；明确要求
   新增 CSV/PDF 时调用 get_wf01_export。二者完成后同样原样输出卡片 JSON，禁止自行整理附件链接。
7. 候选解释用 get_wf01_report/get_wf01_candidate，改参数用 create_wf01_version。
   run_wf01_until_blocked 仅用于旧任务恢复或明确续跑。
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


def _design_input_dedup_seconds() -> float:
    raw = os.environ.get("MCP_DESIGN_INPUT_DEDUP_SECONDS", "10")
    try:
        return min(300.0, max(0.0, float(raw)))
    except ValueError:
        return 10.0


_design_input_call_guard = RecentToolCallGuard(_design_input_dedup_seconds())


def _mcp_turn_key(context: Context) -> str | None:
    """Read an optional stable conversation/turn key from MCP request metadata."""
    meta = context.request_context.meta
    if meta is None:
        return None
    values = meta.model_dump(exclude_none=True)
    for name in (
        "turnId",
        "turn_id",
        "messageId",
        "message_id",
        "conversationId",
        "conversation_id",
        "threadId",
        "thread_id",
        "chatId",
        "chat_id",
    ):
        value = values.get(name)
        if isinstance(value, (str, int)) and str(value).strip():
            return f"{name}:{str(value).strip()}"
    return None


def _design_input_caller_scope(context: Context) -> str | None:
    subject = current_jwt_subject()
    if subject is None:
        return None
    turn_key = _mcp_turn_key(context)
    return f"{subject}|{turn_key}" if turn_key else subject


def _duplicate_design_input_result(payload: dict[str, Any]) -> CallToolResult:
    result = _invoke(
        "wf01_prepare_design_input_duplicate_suppressed",
        lambda: {
            "status": "DUPLICATE_SUPPRESSED",
            "duplicate_suppressed": True,
            "tool": "prepare_wf01_design_input",
            "next_action": "WAIT_FOR_USER",
            "message": (
                "同一输入确认请求的重复工具调用已被服务端抑制；首个表单仍然有效。"
                "立即结束当前轮次，等待用户提交 primer_design_input_update。"
            ),
        },
        audit_input={"payload": payload},
    )
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=json.dumps(result, ensure_ascii=False, separators=(",", ":")),
            )
        ],
        structuredContent=result,
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
        batch = result.get("batch")
        if not isinstance(batch, dict) and result.get("batch_id"):
            batch = result
        if isinstance(batch, dict) and batch.get("batch_id"):
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
        if result.get("type")
        in {"primer_design_input", "agent_interaction", "primer_design_output"}
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
            return CallToolResult(
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
        if ui_message.get("type") == "primer_design_input":
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
    return result


def _direct_create_payload(
    *,
    sequence: str | None,
    target_organism: str | None,
    host: str | None,
    task_name: str | None,
) -> dict[str, Any] | None:
    """Build the only model-facing payload shape for pasted-sequence designs."""
    direct_values = (sequence, target_organism, host, task_name)
    if not any(isinstance(value, str) and value.strip() for value in direct_values):
        return None
    payload: dict[str, Any] = {}
    if isinstance(sequence, str) and sequence.strip():
        stripped_sequence = sequence.strip()
        if stripped_sequence.startswith(">"):
            payload["fasta_text"] = stripped_sequence
        else:
            payload["sequence"] = stripped_sequence
    if isinstance(target_organism, str) and target_organism.strip():
        payload["target_organism"] = target_organism.strip()
    if isinstance(host, str) and host.strip():
        payload["host"] = host.strip()
    if isinstance(task_name, str) and task_name.strip():
        payload["task_name"] = task_name.strip()
    return payload


def _create_payload(
    *,
    sequence: str | None,
    target_organism: str | None,
    host: str | None,
    task_name: str | None,
    payload: dict[str, Any] | None,
    design_input_form: dict[str, Any] | None,
    user_field_updates: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    """Separate the simple direct-input contract from upload compatibility."""
    direct = _direct_create_payload(
        sequence=sequence,
        target_organism=target_organism,
        host=host,
        task_name=task_name,
    )
    if direct is not None:
        if payload is not None or design_input_form is not None or user_field_updates is not None:
            raise ToolError(
                json.dumps(
                    {
                        "code": "INVALID_INPUT",
                        "message": (
                            "直接序列字段不能与上传兼容字段混用；"
                            "粘贴序列时只传 sequence、target_organism、host、task_name"
                        ),
                        "details": {
                            "direct_fields": [
                                "sequence",
                                "target_organism",
                                "host",
                                "task_name",
                            ],
                            "upload_fields": [
                                "payload",
                                "design_input_form",
                                "user_field_updates",
                            ],
                        },
                    },
                    ensure_ascii=False,
                )
            )
        return direct, True
    if payload is None:
        raise ToolError(
            json.dumps(
                {
                    "code": "INVALID_INPUT",
                    "message": "必须提供 sequence；上传文件确认后才使用 payload 兼容字段",
                    "details": {
                        "required_direct_field": "sequence",
                        "minimum_length_nt": 80,
                    },
                },
                ensure_ascii=False,
            )
        )
    prepared = copy.deepcopy(payload)
    upload_messages_present = (
        design_input_form is not None or user_field_updates is not None
    )
    direct_template_present = any(
        prepared.get(field) not in (None, "", [])
        for field in (
            "sequence",
            "fasta_text",
            "template_records",
            "sequences",
            "target_manifest",
        )
    )
    direct_input = direct_template_present and not upload_messages_present
    return prepared, direct_input


UploadedFileName = Annotated[
    str,
    Field(
        min_length=1,
        description="用户上传附件的原始显示文件名，不得改写或使用 uploaded.fasta 占位。",
    ),
]


def _payload_with_uploaded_file_names(
    payload: dict[str, Any],
    file_names: list[str] | None,
) -> dict[str, Any]:
    selected_names = file_names or []
    normalized_names = [
        name.strip()
        for name in selected_names
        if isinstance(name, str) and name.strip()
    ]
    has_inline_sequence = any(
        isinstance(payload.get(field), str) and payload[field].strip()
        for field in ("sequence", "fasta_text")
    )
    if len(normalized_names) != len(selected_names) or (
        not normalized_names and not has_inline_sequence
    ):
        raise ToolError(
            json.dumps(
                {
                    "code": "INVALID_INPUT",
                    "message": "必须提供上传附件文件名，或直接提供 sequence/fasta_text",
                    "details": {
                        "fields": ["file_names", "payload.sequence", "payload.fasta_text"]
                    },
                },
                ensure_ascii=False,
            )
        )
    prepared = copy.deepcopy(payload)
    if normalized_names:
        prepared["uploaded_file_names"] = normalized_names
    return prepared


@mcp.tool(
    description=(
        "仅用于现有文件上传兼容链路，构造 primer_design_input 表单。直接粘贴序列或"
        "自然语言输入不得调用本工具，应直接调用 create_wf01_batch。"
    )
)
def prepare_wf01_design_input(
    payload: dict[str, Any],
    context: Context,
    file_names: Annotated[
        list[UploadedFileName],
        Field(
            min_length=1,
            description=(
                "当前用户消息中全部上传附件的原始显示文件名；直接粘贴序列且没有附件时省略。"
            ),
        ),
    ]
    | None = None,
) -> CallToolResult:
    prepared_payload = _payload_with_uploaded_file_names(payload, file_names)
    if not _design_input_call_guard.claim(
        "prepare_wf01_design_input",
        caller_scope=_design_input_caller_scope(context),
        payload=prepared_payload,
    ):
        return _duplicate_design_input_result(prepared_payload)
    result = _host_ui_result(
        _invoke(
            "wf01_prepare_design_input",
            lambda: service.prepare_wf01_design_input(prepared_payload),
            audit_input={"payload": prepared_payload},
        )
    )
    if not isinstance(result, CallToolResult):  # pragma: no cover - fixed UI contract
        raise RuntimeError("prepare_wf01_design_input did not produce a UI result")
    return result


@mcp.tool(
    description=(
        "下载 HiMat file_id 并返回 input_artifact_id；不返回文件正文。"
        "把该引用传给 create_wf01_batch，调用时不播报进度。"
    )
)
def download_himat_file(file_id: int) -> dict[str, Any]:
    return _invoke(
        "download_himat_file",
        lambda: service.download_himat_file(file_id),
        audit_input={"file_id": file_id},
    )


@mcp.tool(
    description=(
        "新引物设计的唯一入口。粘贴序列时传 sequence；后端自动选择路线：用户说明靶标时传 "
        "target_organism，并运行 FULL_SPECIFICITY。host 为可选项，提供时追加宿主专库排除。"
        "仅有序列时运行 DESIGN_ONLY，并自动运行到报告。"
        "payload/design_input_form/user_field_updates 仅保留给已确认的文件上传兼容流程。"
    )
)
def create_wf01_batch(
    sequence: Annotated[
        str | None,
        Field(
            description=(
                "用户粘贴的模板 DNA 序列。可包含空白、换行或单条 FASTA 标题；"
                "不得省略用户消息中的序列。"
            )
        ),
    ] = None,
    target_organism: Annotated[
        str | None,
        Field(description="用户明确提供的靶标物种或病原名称；未提供时省略，不得猜测。"),
    ] = None,
    host: Annotated[
        str | None,
        Field(description="可选宿主名称；用户明确提供时传入，未提供时省略且不得猜测。"),
    ] = None,
    task_name: Annotated[
        str | None,
        Field(description="可选任务名或序列记录名，例如 CR_0010。"),
    ] = None,
    payload: Annotated[
        dict[str, Any] | None,
        Field(description="仅用于文件上传确认后的兼容载荷；直接粘贴序列时不要传。"),
    ] = None,
    design_input_form: dict[str, Any] | None = None,
    user_field_updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prepared_payload, direct_input = _create_payload(
        sequence=sequence,
        target_organism=target_organism,
        host=host,
        task_name=task_name,
        payload=payload,
        design_input_form=design_input_form,
        user_field_updates=user_field_updates,
    )

    def execute() -> dict[str, Any]:
        batch = service.create_wf01_v02_batch(
            prepared_payload,
            design_input_form,
            user_field_updates,
            include_input_mapping=True,
            interaction_mode=("NO_POPUPS" if direct_input else "LEGACY_UPLOAD"),
        )
        mapping = batch.get("mcp_input_mapping") or {}
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
                "direct_input": direct_input,
                "design_input_form": design_input_form,
                "user_field_updates": user_field_updates,
            },
        )
    )


@mcp.tool(
    description=(
        "读取当前 WF-01 摘要；已完成时重新返回最终 primer_design_output 报告卡片。"
        "用户要求查看、下载、下载地址或再次显示卡片时调用；完成后必须把 "
        "content[0].text 裸 JSON 原样作为唯一回复，禁止改写成 Markdown 链接。"
    )
)
def get_wf01_batch(batch_id: str) -> dict[str, Any]:
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
    batch_id: str,
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
    batch_id: str,
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
    batch_id: str,
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
    batch_id: str,
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
    batch_id: str,
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
    batch_id: str,
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
        "返回 M6 候选报告、科学过程详情报告、21条规则验证报告、"
        "科学工具数据包、CSV 或 PDF 的附件信息。"
        "三份 HTML 报告和科学工具数据包随 M6 自动生成；"
        "CSV、PDF 在用户明确请求后按需生成。生成后重新返回完整 primer_design_output；"
        "必须原样输出 content[0].text 裸 JSON，禁止自行列出 URL。"
    )
)
def get_wf01_export(
    batch_id: str,
    format_name: Literal[
        "html",
        "process_html",
        "rules_html",
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
    "oligosmith://contracts/primer-design-input",
    name="OligoSmith 引物设计输入契约",
    description="供前端渲染的 role=assistant WF-01 表单 JSON 结构约束",
    mime_type="application/schema+json",
)
def primer_design_input_contract_resource() -> str:
    schema_path = PROJECT_ROOT / "schemas" / "primer-design-input.schema.json"
    return schema_path.read_text(encoding="utf-8")


@mcp.resource(
    "oligosmith://contracts/primer-design-input-update",
    name="OligoSmith 引物设计用户更新契约",
    description="表单提交后最小 role=user 字段更新的 JSON 结构约束",
    mime_type="application/schema+json",
)
def primer_design_input_update_contract_resource() -> str:
    schema_path = PROJECT_ROOT / "schemas" / "primer-design-input-update.schema.json"
    return schema_path.read_text(encoding="utf-8")


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

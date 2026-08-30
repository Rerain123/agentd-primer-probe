"""Workflow-scoped MCP catalogs built from the legacy tool registry.

The scoped servers are an additive presentation layer. They reuse the exact
callables already registered on the legacy MCP server, so workflow execution,
state, persistence, and output serialization remain single-sourced.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette


SCOPED_MCP_INSTRUCTIONS = (
    "只调用本服务提供的当前工作流工具，不自行构造或重算工具结果。"
    "缺少入口必填字段时只询问缺失字段；按照工具返回的继续、等待或完成状态执行。"
    "终态primer_design_output已由工具直接交付；"
    "不得生成助手文本、重建或解释JSON，不得再次调用工具，并立即结束当前轮次。"
)


SCOPED_TOOL_NAMES: dict[str, tuple[str, ...]] = {
    "wf01": (
        "download_himat_file",
        "create_wf01_batch",
        "get_wf01_batch",
        "revise_wf01_target_manifest",
        "submit_wf01_interaction_response",
        "run_wf01_until_blocked",
        "create_wf01_version",
        "get_wf01_report",
        "get_wf01_candidate",
        "get_wf01_export",
    ),
    "wf02": (
        "download_himat_file",
        "create_wf02_batch",
        "submit_wf02_input_response",
        "get_wf02_batch",
        "run_wf02_until_blocked",
        "submit_wf02_child_response",
        "submit_wf02_internal_control_response",
        "create_wf02_version",
        "get_wf02_report",
        "get_wf02_panel",
        "get_wf02_export",
    ),
    "wf03": (
        "download_himat_file",
        "run_wf03_internal_control",
        "retry_wf03_internal_control",
        "get_wf03_report",
    ),
    "wf04": (
        "download_himat_file",
        "run_wf04_validation",
        "retry_wf04_validation",
        "get_wf04_report",
    ),
    "wf05": (
        "download_himat_file",
        "run_wf05_variation",
        "retry_wf05_variation",
        "get_wf05_report",
    ),
}


MCP_PARAMETER_DESCRIPTIONS: dict[str, str] = {
    "assay_id": "任务结果标识。",
    "allow_fallback": "是否允许明确标记的开发降级模式。",
    "batch_id": "批次标识。",
    "candidate_id": "候选标识。",
    "candidate_scope": "需要读取的候选范围。",
    "cross_db": "交叉数据源标识或路径。",
    "cursor": "分页游标。",
    "draft_id": "输入草稿标识。",
    "existing_targets": "已有目标方案列表。",
    "expected_target_count": "预期目标文件数量。",
    "failed_file_ids": "下载失败的文件编号列表。",
    "file_id": "HiMat 文件编号。",
    "fluorophore": "可选标记参数。",
    "format_name": "导出格式。",
    "forward_primer": "无附件输入时的正向输入序列。",
    "gene_accessions": "用户提供的基因编号映射。",
    "gene_sequences": "用户提供的版本化同源序列集合。",
    "host": "可选宿主名称。",
    "host_db": "宿主数据源标识或路径。",
    "host_species": "宿主物种名称。",
    "include_internal_control": "是否启用内标。",
    "include_rule_details": "是否返回规则明细。",
    "input_artifact_id": "下载工具生成的内部文件引用。",
    "input_gene": "可选输入标签。",
    "job_id": "任务标识。",
    "limit": "本页最多返回的条目数。",
    "options": "重新设计选项。",
    "page": "页码。",
    "page_size": "每页条目数。",
    "panel_id": "面板标识。",
    "payload": "结构化提交内容。",
    "preferred_genes": "用户明确指定的候选顺序。",
    "probe": "可选的第三输入序列。",
    "probe_type": "可选分支类型。",
    "reason": "操作原因。",
    "reference_sequence_id": "输入序列集中作为参考的记录标识。",
    "response": "结构化交互回复。",
    "resume_automatically": "提交交互后是否自动继续。",
    "resume_token": "服务端恢复令牌。",
    "retry_steps": "需要重试的步骤列表。",
    "reverse_primer": "无附件输入时的反向输入序列。",
    "runtime_config": "运行配置。",
    "runtime_config_changes": "运行配置变更。",
    "section": "需要读取的报告分区。",
    "sequence": "直接输入的序列文本。",
    "sequences": "直接提交的代表性序列集合。",
    "snp_position": "可选位置参数。",
    "stage_options": "阶段执行选项。",
    "target_db": "目标数据源标识或路径。",
    "target_id": "目标标识。",
    "target_organism": "目标对象名称。",
    "target_species": "目标分支上下文。",
    "targets": "目标列表。",
    "task_name": "任务名称。",
    "template": "可选上下文序列。",
    "use_mgb": "是否启用条件分支。",
    "user_input_changes": "用户输入变更。",
    "view": "需要读取的视图。",
}


def _contains_chinese(text: str) -> bool:
    return any("\u4e00" <= character <= "\u9fff" for character in text)


def _remove_schema_titles(value: object) -> None:
    """移除自动生成的英文展示标题，保留协议字段名。"""

    if isinstance(value, dict):
        value.pop("title", None)
        for child in value.values():
            _remove_schema_titles(child)
    elif isinstance(value, list):
        for child in value:
            _remove_schema_titles(child)


def localize_mcp_tool_metadata(server: FastMCP) -> None:
    """统一工具说明为中文，同时保留不可翻译的协议标识。"""

    tool_manager = getattr(server, "_tool_manager", None)
    tools = getattr(tool_manager, "_tools", None)
    if not isinstance(tools, dict):
        raise RuntimeError("当前 MCP 版本未提供可用的工具注册表")

    for tool_name, tool in tools.items():
        description = str(tool.description or "")
        if not _contains_chinese(description):
            raise RuntimeError(f"MCP 工具缺少中文说明：{tool_name}")

        parameters = copy.deepcopy(tool.parameters)
        _remove_schema_titles(parameters)
        properties = parameters.get("properties") or {}
        unknown = set(properties) - set(MCP_PARAMETER_DESCRIPTIONS)
        if unknown:
            raise RuntimeError(
                f"MCP 工具存在未本地化参数：{tool_name}.{sorted(unknown)}"
            )
        for parameter_name, schema in properties.items():
            if not isinstance(schema, dict):
                raise RuntimeError(
                    f"MCP 工具参数结构无效：{tool_name}.{parameter_name}"
                )
            description = str(schema.get("description") or "").strip()
            if not description:
                description = MCP_PARAMETER_DESCRIPTIONS[parameter_name]
                schema["description"] = description
            if not _contains_chinese(description):
                raise RuntimeError(
                    f"MCP 工具参数缺少中文说明：{tool_name}.{parameter_name}"
                )
        tool.parameters = parameters


def build_scoped_mcp_servers(
    source: FastMCP,
    *,
    transport_security_factory: Callable[[], TransportSecuritySettings],
    repeated_terminal_description: str,
) -> dict[str, FastMCP]:
    """Register existing tool callables on workflow-specific MCP servers."""

    # FastMCP 1.x exposes registered callables only through ToolManager. Keep
    # this compatibility access in one module and fail during startup if a
    # future MCP upgrade changes the registry API.
    tool_manager = getattr(source, "_tool_manager", None)
    if tool_manager is None or not callable(
        getattr(tool_manager, "get_tool", None)
    ):
        raise RuntimeError("Installed MCP version does not expose ToolManager")

    servers: dict[str, FastMCP] = {}
    for scope, tool_names in SCOPED_TOOL_NAMES.items():
        workflow = scope.upper().replace("WF", "WF-")
        server = FastMCP(
            name=f"OligoSmith {workflow}",
            instructions=SCOPED_MCP_INSTRUCTIONS,
            streamable_http_path="/",
            json_response=True,
            stateless_http=True,
            transport_security=transport_security_factory(),
        )
        for tool_name in tool_names:
            source_tool = tool_manager.get_tool(tool_name)
            if source_tool is None:
                raise RuntimeError(
                    f"Scoped MCP tool is not registered: {scope}.{tool_name}"
                )
            description = (source_tool.description or "").replace(
                repeated_terminal_description,
                "",
            ).strip()
            server.add_tool(
                source_tool.fn,
                name=source_tool.name,
                title=source_tool.title,
                description=description,
                annotations=source_tool.annotations,
                icons=source_tool.icons,
                meta=source_tool.meta,
            )
            scoped_tool = server._tool_manager.get_tool(source_tool.name)
            if scoped_tool is None:
                raise RuntimeError(
                    f"受限 MCP 工具注册失败：{scope}.{source_tool.name}"
                )
            scoped_tool.fn_metadata.output_schema = copy.deepcopy(
                source_tool.fn_metadata.output_schema
            )
            scoped_tool.__dict__.pop("output_schema", None)
        localize_mcp_tool_metadata(server)
        servers[scope] = server
    return servers


def mount_scoped_mcp_servers(
    parent_app: Starlette,
    servers: dict[str, FastMCP],
) -> dict[str, Starlette]:
    """Mount scoped MCP apps and compose their lifespan managers."""

    child_apps = {
        scope: server.streamable_http_app()
        for scope, server in servers.items()
    }
    parent_lifespan = parent_app.router.lifespan_context

    @asynccontextmanager
    async def combined_lifespan(app: Starlette):
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(parent_lifespan(app))
            for child_app in child_apps.values():
                await stack.enter_async_context(
                    child_app.router.lifespan_context(child_app)
                )
            yield

    parent_app.router.lifespan_context = combined_lifespan
    for scope, child_app in child_apps.items():
        parent_app.mount(f"/mcp/{scope}", child_app)
    return child_apps


def scoped_tool_names(scope: str) -> frozenset[str]:
    """Return the immutable expected tool catalog for tests and diagnostics."""

    return frozenset(SCOPED_TOOL_NAMES[scope])


def scoped_endpoint_urls(base_url: str) -> dict[str, str]:
    """Build canonical trailing-slash URLs for mounted scoped MCP apps."""

    normalized = base_url.rstrip("/")
    return {
        scope: f"{normalized}/mcp/{scope}/"
        for scope in SCOPED_TOOL_NAMES
    }


__all__ = [
    "MCP_PARAMETER_DESCRIPTIONS",
    "SCOPED_MCP_INSTRUCTIONS",
    "SCOPED_TOOL_NAMES",
    "build_scoped_mcp_servers",
    "localize_mcp_tool_metadata",
    "mount_scoped_mcp_servers",
    "scoped_endpoint_urls",
    "scoped_tool_names",
]

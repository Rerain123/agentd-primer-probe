"""可选的 FastAPI 服务边界。

在服务器进程中导入本模块前，需要安装 ``api`` 可选依赖。
"""

from __future__ import annotations

from pathlib import Path

from . import __version__
from .errors import AgentDError
from .service import AgentDService

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
except ImportError as exc:  # pragma: no cover - exercised by deployment smoke test
    raise RuntimeError("FastAPI未安装，请执行: pip install -e .[api]") from exc


app = FastAPI(title="Agent D 引物探针服务接口", version=__version__)
service = AgentDService()
STATIC_DIR = Path(__file__).with_name("static")
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.get("/", include_in_schema=False)
@app.get("/ui", include_in_schema=False)
def local_interface() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health", summary="健康检查")
def health() -> dict:
    return {"status": "ok", "service": "agentd-primer-probe", "phase": 3}


@app.get("/ready", summary="就绪检查")
def readiness() -> dict:
    result = service.readiness()
    if result["status"] != "ready":
        raise HTTPException(status_code=503, detail=result)
    return result


@app.get("/v1/capabilities", summary="查询服务能力")
def capabilities() -> dict:
    return service.capabilities()


@app.get("/v1/tool-status", summary="查询科学工具状态")
def tool_status() -> dict:
    return service.tool_status()


@app.post("/v1/tools/primer3/design", summary="独立 Primer3 引物探针设计")
def design_with_primer3(payload: dict) -> dict:
    """仅根据序列生成引物/探针候选；不评估特异性。"""
    try:
        return service.design_with_primer3(payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.get("/v1/audit/agent", summary="查询智能体审计日志")
def get_agent_audit_log(
    trace_id: str | None = None,
    batch_id: str | None = None,
    operation: str | None = None,
    limit: int = 100,
) -> dict:
    """返回用于调试的已脱敏可观察 MCP 工具事件。"""
    try:
        return service.get_agent_audit_log(
            trace_id=trace_id,
            batch_id=batch_id,
            operation=operation,
            limit=limit,
        )
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.get("/v1/jobs/{job_id}", summary="查询任务结果")
def get_job(job_id: str) -> dict:
    try:
        return service.get_job(job_id)
    except AgentDError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc


@app.post("/v1/wf02/tasks/preflight", summary="执行 WF-02 输入预检")
def preflight_wf02_v01(payload: dict) -> dict:
    """执行流程图 N01-N03；只校验和回显，不创建任务。"""
    try:
        return service.preflight_wf02_v01(payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf02/tasks", summary="创建 WF-02 v0.1 任务或确认草稿")
def create_wf02_v01_task(payload: dict) -> dict:
    """完整输入进入 N05；有阻塞项则停在 N04 并保留已输入内容。"""
    try:
        return service.create_wf02_v01_batch(payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.get("/v1/wf02/tasks/{batch_id}", summary="查询 WF-02 v0.1 任务")
def get_wf02_v01_task(batch_id: str) -> dict:
    try:
        return service.get_wf02_v01_batch(batch_id)
    except AgentDError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc


@app.get("/v1/wf02/drafts/{draft_id}", summary="查询 WF-02 输入确认草稿")
def get_wf02_v01_draft(draft_id: str) -> dict:
    try:
        return service.get_wf02_v01_draft(draft_id)
    except AgentDError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc


@app.post(
    "/v1/wf02/drafts/{draft_id}/interactions/response",
    summary="提交 WF-02 输入确认回复",
)
def submit_wf02_v01_input_response(
    draft_id: str,
    response: dict,
) -> dict:
    """提交本轮全部阻塞项；校验通过后自动创建 N05 正式任务。"""
    try:
        return service.submit_wf02_v01_input_response(draft_id, response)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post(
    "/v1/wf02/tasks/{batch_id}/run-until-blocked",
    summary="推进 WF-02 普通多重面板流程",
)
def run_wf02_v01_until_blocked(batch_id: str) -> dict:
    """启动后台执行；提交后立即返回，任务独立于请求连接运行。"""
    try:
        return service.run_wf02_v01_background(batch_id, wait_seconds=0.0)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.get(
    "/v1/wf02/tasks/{batch_id}/report",
    summary="读取 WF-02 普通多重报告",
)
def get_wf02_v01_report(batch_id: str) -> dict:
    try:
        return service.get_wf02_v01_report(batch_id)
    except AgentDError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc


@app.get(
    "/v1/wf02/tasks/{batch_id}/panels/{panel_id}",
    summary="读取 WF-02 候选面板详情",
)
def get_wf02_v01_panel(batch_id: str, panel_id: str) -> dict:
    try:
        return service.get_wf02_v01_panel(batch_id, panel_id)
    except AgentDError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc


@app.post(
    "/v1/wf02/tasks/{batch_id}/versions",
    summary="基于已完成 WF-02 报告创建新版本",
)
def create_wf02_v01_version(batch_id: str, payload: dict) -> dict:
    try:
        return service.create_wf02_v01_version(batch_id, payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.head(
    "/v1/wf02/tasks/{batch_id}/exports/{format_name}",
    include_in_schema=False,
)
@app.get(
    "/v1/wf02/tasks/{batch_id}/exports/{format_name}",
    summary="下载 WF-02 HTML、CSV 或 PDF 报告",
)
def download_wf02_v01_export(
    batch_id: str, format_name: str
) -> FileResponse:
    selected = format_name.lower()
    media_types = {
        "html": "text/html; charset=utf-8",
        "csv": "text/csv; charset=utf-8",
        "pdf": "application/pdf",
    }
    if selected not in media_types:
        raise HTTPException(
            status_code=404,
            detail={"code": "WF02_EXPORT_FORMAT_NOT_SUPPORTED"},
        )
    try:
        path = service.get_wf02_v01_export_path(batch_id, selected)
    except AgentDError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc
    return FileResponse(
        path,
        media_type=media_types[selected],
        filename=path.name,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get(
    "/v1/wf02/tasks/{batch_id}/exports/html/preview",
    summary="预览 WF-02 HTML 报告",
)
def preview_wf02_v01_html(batch_id: str) -> FileResponse:
    try:
        path = service.get_wf02_v01_export_path(batch_id, "html")
    except AgentDError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc
    return FileResponse(
        path,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": "inline",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post(
    "/v1/wf02/tasks/{batch_id}/internal-control/interactions/response",
    summary="提交 WF-02 内标设计交互回复",
)
def submit_wf02_v01_internal_control_response(
    batch_id: str,
    response: dict,
) -> dict:
    """补充 P5 输入、重试或明确不含内标继续，并恢复 WF-02。"""
    try:
        service.wf02_v01.submit_internal_control_response(
            batch_id,
            response,
            auto_resume=False,
        )
        return service.run_wf02_v01_background(batch_id)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post(
    "/v1/wf02/tasks/{batch_id}/targets/{target_id}/interactions/response",
    summary="提交 WF-02 靶标子任务交互回复",
)
def submit_wf02_v01_child_response(
    batch_id: str,
    target_id: str,
    response: dict,
) -> dict:
    """补充模板或代理 WF-01 交互回复，然后恢复该 WF-02 任务。"""
    try:
        service.wf02_v01.submit_child_response(
            batch_id,
            target_id,
            response,
            auto_resume=False,
        )
        return service.run_wf02_v01_background(batch_id)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf01/tasks/preflight", summary="执行 WF-01 输入预检")
def preflight_wf01_v02(payload: dict) -> dict:
    """执行 PRD v0.2 输入预检；不运行 Primer3。"""
    try:
        return service.preflight_wf01_v02(
            payload,
            require_full_route=True,
        )
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf01/tasks", summary="创建 WF-01 批次")
def create_wf01_v02_batch(payload: dict) -> dict:
    """以 QUEUED 或 PENDING_CONFIRMATION 状态创建 v0.2 批次。"""
    try:
        return service.create_wf01_v02_batch(
            payload,
            require_full_route=True,
        )
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.get("/v1/wf01/tasks/{batch_id}", summary="查询 WF-01 批次")
def get_wf01_v02_batch(batch_id: str) -> dict:
    try:
        return service.get_wf01_v02_batch(batch_id)
    except AgentDError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc


@app.patch("/v1/wf01/tasks/{batch_id}/targets", summary="修改 WF-01 靶标清单")
def revise_wf01_v02_target_manifest(batch_id: str, payload: dict) -> dict:
    """修改或移除靶标草稿，然后重新执行逐靶标预检。"""
    try:
        return service.revise_wf01_v02_target_manifest(batch_id, payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf01/tasks/{batch_id}/confirm", summary="确认 WF-01 靶标清单")
def confirm_wf01_v02_target_manifest(batch_id: str) -> dict:
    """运行批次前确认已就绪的靶标清单。"""
    try:
        return service.confirm_wf01_v02_target_manifest(batch_id)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post(
    "/v1/wf01/tasks/{batch_id}/run-until-blocked",
    summary="续跑 WF-01 至阻塞点",
)
def run_wf01_v02_until_blocked(batch_id: str, payload: dict | None = None) -> dict:
    """运行确定性阶段，直到需要用户输入或 M6 完成。"""
    try:
        return service.run_wf01_v02_background(batch_id, payload or {})
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.get("/v1/wf01/tasks/{batch_id}/interactions", summary="查询 WF-01 交互记录")
def get_wf01_v02_interactions(batch_id: str) -> dict:
    """返回批次中待处理或已记录的 Agent D 交互载荷。"""
    try:
        return service.get_wf01_v02_interactions(batch_id)
    except AgentDError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc


@app.post(
    "/v1/wf01/tasks/{batch_id}/interactions/response",
    summary="提交 WF-01 交互回复",
)
def submit_wf01_v02_interaction_response(batch_id: str, payload: dict) -> dict:
    """持久化完整交互回复，并按确定性规则继续路由。"""
    try:
        return service.submit_wf01_v02_interaction_response(batch_id, payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf01/tasks/{batch_id}/run-m2", summary="执行 WF-01 M2 阶段")
def execute_wf01_v02_m2(batch_id: str, payload: dict | None = None) -> dict:
    """运行 Primer3 及 v0.2 R01–R12；M3/M4 保持待运行。"""
    try:
        return service.execute_wf01_v02_m2(batch_id, payload or {})
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf01/tasks/{batch_id}/run-m3", summary="执行 WF-01 M3 阶段")
def execute_wf01_v02_m3(batch_id: str, payload: dict | None = None) -> dict:
    """验证探索范围、去重，并生成非最终 Top10。"""
    try:
        return service.execute_wf01_v02_m3(batch_id, payload or {})
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf01/tasks/{batch_id}/run-m4", summary="执行 WF-01 M4 阶段")
def execute_wf01_v02_m4(batch_id: str, payload: dict | None = None) -> dict:
    """评估靶标、宿主及近缘种/背景特异性证据。"""
    try:
        return service.execute_wf01_v02_m4(batch_id, payload or {})
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf01/tasks/{batch_id}/run-m5", summary="执行 WF-01 M5 阶段")
def execute_wf01_v02_m5(batch_id: str, payload: dict | None = None) -> dict:
    """在不重复运行科学工具的情况下汇总靶标与批次状态。"""
    try:
        return service.execute_wf01_v02_m5(batch_id, payload or {})
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf01/tasks/{batch_id}/run-m6", summary="执行 WF-01 M6 阶段")
def execute_wf01_v02_m6(batch_id: str, payload: dict | None = None) -> dict:
    """组装规范化报告及默认 HTML 导出。"""
    try:
        return service.execute_wf01_v02_m6(batch_id, payload or {})
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf01/tasks/{batch_id}/versions", summary="创建 WF-01 重新设计版本")
def create_wf01_v02_version(batch_id: str, payload: dict | None = None) -> dict:
    """根据明确的靶标变更创建不可变的 M7 重新设计版本。"""
    try:
        return service.create_wf01_v02_version(batch_id, payload or {})
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.get("/v1/wf01/tasks/{batch_id}/report", summary="查询 WF-01 规范化报告")
def get_wf01_v02_report(
    batch_id: str,
    target_id: str | None = None,
    candidate_scope: str = "top10",
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """返回完整 WF-01 报告的内部 JSON 契约。"""
    try:
        return service.get_wf01_v02_report(
            batch_id,
            target_id=target_id,
            candidate_scope=candidate_scope,
            page=page,
            page_size=page_size,
        )
    except AgentDError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc


@app.get(
    "/v1/wf01/tasks/{batch_id}/scientific-artifacts",
    summary="列出 WF-01 科学工件",
)
def list_wf01_v02_scientific_artifacts(
    batch_id: str,
    stage: str | None = None,
) -> dict:
    """列出可供专家复核的原始科学输出。"""
    try:
        return service.get_wf01_v02_scientific_artifacts(
            batch_id,
            stage=stage,
        )
    except AgentDError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc


@app.get(
    "/v1/wf01/tasks/{batch_id}/scientific-artifacts/{artifact_id}",
    summary="下载 WF-01 科学工件",
)
def download_wf01_v02_scientific_artifact(
    batch_id: str,
    artifact_id: str,
) -> FileResponse:
    """下载一个供专家复核的不可变阶段原始快照。"""
    try:
        path = service.get_wf01_v02_scientific_artifact_path(
            batch_id,
            artifact_id,
        )
    except AgentDError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc
    return FileResponse(
        path,
        media_type="application/json; charset=utf-8",
        filename=path.name,
    )


@app.get(
    "/v1/wf01/tasks/{batch_id}/exports/{format_name}",
    summary="下载 WF-01 导出文件",
)
def download_wf01_v02_export(
    batch_id: str,
    format_name: str,
) -> FileResponse:
    """下载根据完整规范化报告生成的 M6 导出文件。"""
    try:
        path = service.get_wf01_v02_export_path(batch_id, format_name)
    except AgentDError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc
    media_types = {
        "html": "text/html; charset=utf-8",
        "process_html": "text/html; charset=utf-8",
        "rules_html": "text/html; charset=utf-8",
        "csv": "text/csv; charset=utf-8",
        "pdf": "application/pdf",
        "scientific_data": "application/zip",
    }
    selected = format_name.lower()
    return FileResponse(
        path,
        media_type=media_types[selected],
        filename=path.name,
    )


@app.get(
    "/v1/wf01/tasks/{batch_id}/exports/{format_name}/preview",
    summary="预览 WF-01 HTML 报告",
)
def preview_wf01_v02_export(
    batch_id: str,
    format_name: str,
) -> FileResponse:
    """以内联方式呈现已完成的 HTML 报告，供前端预览。"""
    selected = format_name.lower()
    if selected not in {"html", "process_html", "rules_html"}:
        raise HTTPException(
            status_code=404,
            detail={"code": "PREVIEW_NOT_SUPPORTED", "format": selected},
        )
    try:
        path = service.get_wf01_v02_export_path(batch_id, selected)
    except AgentDError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc
    return FileResponse(
        path,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": "inline"},
    )


@app.get(
    "/data/reports/{batch_id}/{file_name}",
    summary="获取稳定工作流报告文件",
)
def get_workflow_report_file(
    batch_id: str,
    file_name: str,
) -> FileResponse:
    """通过返回给前端的稳定 fileUrl 提供一份已生成报告。"""
    formats = {
        ".html": ("html", "text/html; charset=utf-8", "inline"),
        ".pdf": ("pdf", "application/pdf", "inline"),
        ".csv": ("csv", "text/csv; charset=utf-8", "attachment"),
        ".zip": ("scientific_data", "application/zip", "attachment"),
    }
    selected = formats.get(Path(file_name).suffix.lower())
    if selected is None:
        raise HTTPException(status_code=404, detail={"code": "REPORT_FILE_NOT_FOUND"})
    format_name, media_type, disposition = selected
    if file_name.endswith("_科学过程详情报告.html"):
        format_name = "process_html"
    elif file_name.endswith("_21条规则验证报告.html"):
        format_name = "rules_html"
    try:
        path = service.get_wf01_v02_export_path(batch_id, format_name)
    except AgentDError:
        try:
            path = service.get_workflow_terminal_export_path(batch_id, file_name)
        except AgentDError as exc:
            raise HTTPException(status_code=404, detail=exc.to_dict()) from exc
    if path.name != file_name:
        raise HTTPException(status_code=404, detail={"code": "REPORT_FILE_NOT_FOUND"})
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Content-Disposition": disposition},
    )


@app.get("/data/{file_name}", summary="获取 WF-01 附件")
def get_wf01_v02_attachment(file_name: str) -> FileResponse:
    """提供 primer_design_output.fileUrl 使用的扁平附件地址。"""
    try:
        path = service.get_wf01_v02_export_path_by_filename(file_name)
    except AgentDError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc
    media_types = {
        ".html": "text/html; charset=utf-8",
        ".csv": "text/csv; charset=utf-8",
        ".pdf": "application/pdf",
        ".zip": "application/zip",
    }
    media_type = media_types.get(path.suffix.lower())
    if media_type is None:
        raise HTTPException(
            status_code=404, detail={"code": "REPORT_FILE_NOT_FOUND"}
        )
    disposition = "inline" if path.suffix.lower() in {".html", ".pdf"} else "attachment"
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Content-Disposition": disposition},
    )


@app.post("/v1/wf01/design", summary="运行 WF-01 单靶标设计")
def design(payload: dict) -> dict:
    try:
        return service.run("WF-01", payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post(
    "/v1/wf02/multiplex",
    summary="兼容创建 WF-02 v0.1 任务",
    deprecated=True,
)
def multiplex(payload: dict) -> dict:
    """兼容别名；与 POST /v1/wf02/tasks 使用同一个新 WF-02 服务。"""
    try:
        return service.run("WF-02", payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf03/internal-control", summary="运行 WF-03 宿主内标设计")
def internal_control(payload: dict) -> dict:
    try:
        return service.run_wf03_internal_control(payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post(
    "/v1/wf03/internal-control/{job_id}/retry",
    summary="以补充证据重入 WF-03 Step 1",
)
def retry_internal_control(job_id: str, payload: dict | None = None) -> dict:
    try:
        return service.retry_wf03_internal_control(job_id, payload or {})
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.get(
    "/v1/wf03/internal-control/{job_id}/report",
    summary="读取 WF-03 紧凑报告",
)
def internal_control_report(job_id: str) -> dict:
    try:
        return service.get_wf03_report(job_id)
    except AgentDError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc


@app.post("/v1/wf04/validate", summary="运行 WF-04 v0.1")
def validate(payload: dict) -> dict:
    try:
        return service.run_wf04_validation(payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post(
    "/v1/wf04/validate/{job_id}/retry",
    summary="创建 WF-04 不可变重试任务",
)
def retry_wf04_validation(job_id: str, payload: dict | None = None) -> dict:
    try:
        return service.retry_wf04_validation(job_id, payload or {})
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.get(
    "/v1/wf04/validate/{job_id}/report",
    summary="读取 WF-04 精简报告",
)
def get_wf04_report(job_id: str) -> dict:
    try:
        return service.get_wf04_report(job_id)
    except AgentDError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc


@app.post("/v1/wf05/variation", summary="运行 WF-05 变异耐受性分析")
def variation(payload: dict) -> dict:
    try:
        return service.run_wf05_variation(payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post(
    "/v1/wf05/variation/{job_id}/retry",
    summary="创建 WF-05 不可变重试任务",
)
def retry_variation(job_id: str, payload: dict | None = None) -> dict:
    try:
        return service.retry_wf05_variation(job_id, payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.get(
    "/v1/wf05/variation/{job_id}/report",
    summary="读取 WF-05 精简报告",
)
def get_wf05_report(job_id: str) -> dict:
    try:
        return service.get_wf05_report(job_id)
    except AgentDError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc


@app.post("/v1/workflows/{workflow}/run", summary="运行指定工作流")
def run_workflow(workflow: str, payload: dict) -> dict:
    try:
        return service.run(workflow, payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.get("/v1/wf01/tasks/{batch_id}/progress", summary="只读查询 WF-01 后台进度")
def get_wf01_progress(batch_id: str) -> dict:
    try:
        return service.get_wf01_v02_agent_summary(batch_id)
    except AgentDError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc


@app.get("/v1/wf02/tasks/{batch_id}/progress", summary="只读查询 WF-02 后台进度")
def get_wf02_progress(batch_id: str) -> dict:
    try:
        return service.get_wf02_v01_agent_summary(batch_id)
    except AgentDError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc

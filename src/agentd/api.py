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


@app.post("/v1/wf01/tasks/preflight", summary="执行 WF-01 输入预检")
def preflight_wf01_v02(payload: dict) -> dict:
    """执行 PRD v0.2 输入预检；不运行 Primer3。"""
    try:
        return service.preflight_wf01_v02(payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf01/design-input", summary="构造 WF-01 设计输入表单")
def prepare_wf01_design_input(payload: dict) -> dict:
    """构造权威的设计前 HiMat 消息；不由 LLM 生成。"""
    return service.prepare_wf01_design_input(payload)


@app.post("/v1/wf01/tasks", summary="创建 WF-01 批次")
def create_wf01_v02_batch(payload: dict) -> dict:
    """以 QUEUED 或 PENDING_CONFIRMATION 状态创建 v0.2 批次。"""
    try:
        return service.create_wf01_v02_batch(payload)
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
        return service.run_wf01_v02_until_blocked(batch_id, payload or {})
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
    summary="获取 WF-01 稳定报告文件",
)
def get_wf01_v02_report_file(
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


@app.post("/v1/wf02/multiplex", summary="运行 WF-02 多重面板设计")
def multiplex(payload: dict) -> dict:
    try:
        return service.run("WF-02", payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf03/internal-control", summary="运行 WF-03 宿主内标设计")
def internal_control(payload: dict) -> dict:
    try:
        return service.run("WF-03", payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf04/validate", summary="运行 WF-04 已有检测体系验证")
def validate(payload: dict) -> dict:
    try:
        return service.run("WF-04", payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf05/variation", summary="运行 WF-05 变异耐受性分析")
def variation(payload: dict) -> dict:
    try:
        return service.run("WF-05", payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf06/complete-panel", summary="运行 WF-06 完整面板设计")
def complete_panel(payload: dict) -> dict:
    try:
        return service.run("WF-06", payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf07/diagnose", summary="运行 WF-07 问题诊断")
def diagnose(payload: dict) -> dict:
    try:
        return service.run("WF-07", payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/workflows/{workflow}/run", summary="运行指定工作流")
def run_workflow(workflow: str, payload: dict) -> dict:
    try:
        return service.run(workflow, payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc

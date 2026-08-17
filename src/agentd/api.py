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


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "agentd-primer-probe", "phase": 3}


@app.get("/ready")
def readiness() -> dict:
    result = service.readiness()
    if result["status"] != "ready":
        raise HTTPException(status_code=503, detail=result)
    return result


@app.get("/v1/capabilities")
def capabilities() -> dict:
    return service.capabilities()


@app.get("/v1/tool-status")
def tool_status() -> dict:
    return service.tool_status()


@app.post("/v1/tools/primer3/design")
def design_with_primer3(payload: dict) -> dict:
    """Generate primer/probe candidates from sequence only; no specificity."""
    try:
        return service.design_with_primer3(payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.get("/v1/audit/agent")
def get_agent_audit_log(
    trace_id: str | None = None,
    batch_id: str | None = None,
    operation: str | None = None,
    limit: int = 100,
) -> dict:
    """Return redacted observable MCP tool events for debugging."""
    try:
        return service.get_agent_audit_log(
            trace_id=trace_id,
            batch_id=batch_id,
            operation=operation,
            limit=limit,
        )
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.get("/v1/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    try:
        return service.get_job(job_id)
    except AgentDError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc


@app.post("/v1/wf01/tasks/preflight")
def preflight_wf01_v02(payload: dict) -> dict:
    """PRD v0.2 intake validation; does not run Primer3."""
    try:
        return service.preflight_wf01_v02(payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf01/design-input")
def prepare_wf01_design_input(payload: dict) -> dict:
    """Build the authoritative pre-design HiMat message without LLM output."""
    return service.prepare_wf01_design_input(payload)


@app.post("/v1/wf01/tasks")
def create_wf01_v02_batch(payload: dict) -> dict:
    """Create a v0.2 batch in QUEUED or PENDING_CONFIRMATION."""
    try:
        return service.create_wf01_v02_batch(payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.get("/v1/wf01/tasks/{batch_id}")
def get_wf01_v02_batch(batch_id: str) -> dict:
    try:
        return service.get_wf01_v02_batch(batch_id)
    except AgentDError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc


@app.patch("/v1/wf01/tasks/{batch_id}/targets")
def revise_wf01_v02_target_manifest(batch_id: str, payload: dict) -> dict:
    """Edit or remove target drafts, then rerun target-level preflight."""
    try:
        return service.revise_wf01_v02_target_manifest(batch_id, payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf01/tasks/{batch_id}/confirm")
def confirm_wf01_v02_target_manifest(batch_id: str) -> dict:
    """Confirm a ready target manifest before running the batch."""
    try:
        return service.confirm_wf01_v02_target_manifest(batch_id)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf01/tasks/{batch_id}/run-until-blocked")
def run_wf01_v02_until_blocked(batch_id: str, payload: dict | None = None) -> dict:
    """Run deterministic stages until user input is needed or M6 completes."""
    try:
        return service.run_wf01_v02_until_blocked(batch_id, payload or {})
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.get("/v1/wf01/tasks/{batch_id}/interactions")
def get_wf01_v02_interactions(batch_id: str) -> dict:
    """Return Agent D interaction payloads pending or recorded for the batch."""
    try:
        return service.get_wf01_v02_interactions(batch_id)
    except AgentDError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc


@app.post("/v1/wf01/tasks/{batch_id}/interactions/response")
def submit_wf01_v02_interaction_response(batch_id: str, payload: dict) -> dict:
    """Persist and deterministically route a complete interaction response."""
    try:
        return service.submit_wf01_v02_interaction_response(batch_id, payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf01/tasks/{batch_id}/run-m2")
def execute_wf01_v02_m2(batch_id: str, payload: dict | None = None) -> dict:
    """Run Primer3 plus v0.2 R01-R12; M3/M4 remain pending."""
    try:
        return service.execute_wf01_v02_m2(batch_id, payload or {})
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf01/tasks/{batch_id}/run-m3")
def execute_wf01_v02_m3(batch_id: str, payload: dict | None = None) -> dict:
    """Validate exploration, deduplicate and prepare a non-final Top10."""
    try:
        return service.execute_wf01_v02_m3(batch_id, payload or {})
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf01/tasks/{batch_id}/run-m4")
def execute_wf01_v02_m4(batch_id: str, payload: dict | None = None) -> dict:
    """Evaluate target, host and related/background specificity evidence."""
    try:
        return service.execute_wf01_v02_m4(batch_id, payload or {})
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf01/tasks/{batch_id}/run-m5")
def execute_wf01_v02_m5(batch_id: str, payload: dict | None = None) -> dict:
    """Assemble target and batch statuses without rerunning scientific tools."""
    try:
        return service.execute_wf01_v02_m5(batch_id, payload or {})
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf01/tasks/{batch_id}/run-m6")
def execute_wf01_v02_m6(batch_id: str, payload: dict | None = None) -> dict:
    """Assemble the canonical report and default HTML export."""
    try:
        return service.execute_wf01_v02_m6(batch_id, payload or {})
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf01/tasks/{batch_id}/versions")
def create_wf01_v02_version(batch_id: str, payload: dict | None = None) -> dict:
    """Create an immutable M7 redesign version from explicit target changes."""
    try:
        return service.create_wf01_v02_version(batch_id, payload or {})
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.get("/v1/wf01/tasks/{batch_id}/report")
def get_wf01_v02_report(
    batch_id: str,
    target_id: str | None = None,
    candidate_scope: str = "top10",
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """Internal JSON contract for the complete WF-01 report."""
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


@app.get("/v1/wf01/tasks/{batch_id}/scientific-artifacts")
def list_wf01_v02_scientific_artifacts(
    batch_id: str,
    stage: str | None = None,
) -> dict:
    """List raw scientific outputs available for expert review."""
    try:
        return service.get_wf01_v02_scientific_artifacts(
            batch_id,
            stage=stage,
        )
    except AgentDError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc


@app.get(
    "/v1/wf01/tasks/{batch_id}/scientific-artifacts/{artifact_id}"
)
def download_wf01_v02_scientific_artifact(
    batch_id: str,
    artifact_id: str,
) -> FileResponse:
    """Download one immutable raw stage snapshot for expert review."""
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


@app.get("/v1/wf01/tasks/{batch_id}/exports/{format_name}")
def download_wf01_v02_export(
    batch_id: str,
    format_name: str,
) -> FileResponse:
    """Download an M6 export generated from the full canonical report."""
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


@app.get("/v1/wf01/tasks/{batch_id}/exports/{format_name}/preview")
def preview_wf01_v02_export(
    batch_id: str,
    format_name: str,
) -> FileResponse:
    """Render the completed HTML report inline for a frontend preview action."""
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


@app.get("/data/reports/{batch_id}/{file_name}")
def get_wf01_v02_report_file(
    batch_id: str,
    file_name: str,
) -> FileResponse:
    """Serve one generated report through the stable fileUrl returned to the UI."""
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


@app.get("/data/{file_name}")
def get_wf01_v02_attachment(file_name: str) -> FileResponse:
    """Serve the flat attachment URL used by primer_design_output.fileUrl."""
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


@app.post("/v1/wf01/design")
def design(payload: dict) -> dict:
    try:
        return service.run("WF-01", payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf02/multiplex")
def multiplex(payload: dict) -> dict:
    try:
        return service.run("WF-02", payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf03/internal-control")
def internal_control(payload: dict) -> dict:
    try:
        return service.run("WF-03", payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf04/validate")
def validate(payload: dict) -> dict:
    try:
        return service.run("WF-04", payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf05/variation")
def variation(payload: dict) -> dict:
    try:
        return service.run("WF-05", payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf06/complete-panel")
def complete_panel(payload: dict) -> dict:
    try:
        return service.run("WF-06", payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/wf07/diagnose")
def diagnose(payload: dict) -> dict:
    try:
        return service.run("WF-07", payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@app.post("/v1/workflows/{workflow}/run")
def run_workflow(workflow: str, payload: dict) -> dict:
    try:
        return service.run(workflow, payload)
    except AgentDError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc

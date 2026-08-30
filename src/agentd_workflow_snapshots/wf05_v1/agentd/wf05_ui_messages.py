"""WF-05 terminal card assembly for the shared host protocol."""

from __future__ import annotations

from typing import Any

from .errors import InputValidationError


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def build_workflow_terminal_output(
    report: dict[str, Any],
    export: dict[str, Any],
) -> dict[str, Any]:
    if report.get("workflow") != "WF-05":
        raise InputValidationError("WF-05终态层不能处理其他工作流")
    job_id = _text(report.get("job_id"))
    task_code = _text(export.get("task_code")) or job_id
    execution_status = _text(
        report.get("execution_status") or report.get("status")
    ) or "UNKNOWN"
    decision = _text(
        report.get("decision") or (report.get("variation") or {}).get("verdict")
    ) or "INCONCLUSIVE"
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
            "status": "COMPLETED",
            "reportTitle": "WF-05 终态报告",
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

"""Persisted, redacted audit events for agent/tool workflow debugging.

The audit trail records observable execution facts. It deliberately does not
record or request a language model's hidden chain of thought.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .errors import InputValidationError, PersistenceError


_SECRET_FRAGMENTS = (
    "authorization",
    "password",
    "secret",
    "token",
    "jwt",
    "api_key",
    "apikey",
)
_SEQUENCE_KEYS = {
    "fasta_text",
    "sequence",
    "sequence_5to3",
}
_MAX_TEXT_LENGTH = 500
_MAX_LIST_ITEMS = 25
_MAX_DEPTH = 7


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest_text(value: str, *, kind: str) -> dict[str, Any]:
    return {
        "redacted": True,
        "kind": kind,
        "length": len(value),
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        "record_count": value.count(">") if kind == "fasta_text" else None,
    }


def sanitize_for_audit(
    value: Any,
    *,
    key: str = "",
    depth: int = 0,
) -> Any:
    """Return bounded audit-safe data without secrets or biological sequences."""
    lowered = key.lower()
    if any(fragment in lowered for fragment in _SECRET_FRAGMENTS):
        return "[REDACTED]"
    if isinstance(value, str) and lowered in _SEQUENCE_KEYS:
        return _digest_text(value, kind=lowered)
    if depth >= _MAX_DEPTH:
        return "[MAX_DEPTH]"
    if isinstance(value, dict):
        return {
            str(item_key): sanitize_for_audit(
                item_value,
                key=str(item_key),
                depth=depth + 1,
            )
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        items = [
            sanitize_for_audit(item, key=key, depth=depth + 1)
            for item in value[:_MAX_LIST_ITEMS]
        ]
        if len(value) > _MAX_LIST_ITEMS:
            items.append({"truncated_items": len(value) - _MAX_LIST_ITEMS})
        return items
    if isinstance(value, str) and len(value) > _MAX_TEXT_LENGTH:
        return {
            "truncated": True,
            "length": len(value),
            "preview": value[:_MAX_TEXT_LENGTH],
            "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        }
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)[:_MAX_TEXT_LENGTH]


def summarize_tool_result(result: Any) -> dict[str, Any]:
    """Extract workflow evidence useful for debugging without copying results."""
    if not isinstance(result, dict):
        return {"result_type": type(result).__name__}
    summary: dict[str, Any] = {
        "result_type": result.get("type") or "object",
    }
    for key in (
        "batch_id",
        "job_id",
        "status",
        "preflight_status",
        "next_action",
        "m2_outcome",
        "m3_outcome",
        "m4_outcome",
        "m5_outcome",
        "m6_outcome",
    ):
        if result.get(key) is not None:
            summary[key] = result[key]

    ui_message = result.get("ui_message")
    if isinstance(ui_message, dict):
        summary["ui_message_type"] = ui_message.get("type")
        summary["flow_control"] = ui_message.get("flowControl")
    elif result.get("type") in {"primer_design_input", "agent_interaction"}:
        summary["ui_message_type"] = result.get("type")
        summary["flow_control"] = result.get("flowControl")

    mapping = result.get("mcp_input_mapping")
    if isinstance(mapping, dict):
        summary["mcp_input_mapping"] = mapping

    preflight = result.get("preflight")
    if not isinstance(preflight, dict) and "normalised_input" in result:
        preflight = result
    if isinstance(preflight, dict):
        normalised = preflight.get("normalised_input") or {}
        summary["effective_batch_defaults"] = normalised.get("batch_defaults")
        summary["issue_counts"] = preflight.get("issue_counts")

    readiness = result.get("readiness")
    if isinstance(readiness, dict):
        summary["readiness"] = readiness
    return sanitize_for_audit(summary)


_UI_OUTPUT_TYPES = {
    "agent_interaction",
    "primer_design_input",
    "primer_design_output",
}


def observable_tool_output(result: Any) -> tuple[str, Any]:
    """Select the business value that the MCP caller actually observes.

    Terminal and interaction tools promote ``ui_message`` to the MCP
    ``structuredContent`` field after the service call.  Recording that object
    here keeps the audit log aligned with the client-visible business payload
    instead of copying the much larger internal workflow result.
    """

    if not isinstance(result, dict):
        return "tool_result", result
    if result.get("type") in _UI_OUTPUT_TYPES:
        return "tool_result", result

    ui_message = result.get("ui_message")
    if isinstance(ui_message, dict):
        return "ui_message", ui_message

    retained = result.get("summary")
    if isinstance(retained, dict):
        ui_message = retained.get("ui_message")
        if isinstance(ui_message, dict):
            return "summary.ui_message", ui_message
    return "tool_result", result


class AgentAuditLog:
    """Append-only JSONL audit storage colocated with persisted workflow state."""

    def __init__(self, state_root: str | Path) -> None:
        self.root = Path(state_root) / "audit"
        self.path = self.root / "agent-tools.jsonl"
        self._lock = threading.Lock()

    def start(
        self,
        operation: str,
        inputs: Any = None,
    ) -> str:
        trace_id = str(uuid4())
        self._append(
            {
                "event_id": str(uuid4()),
                "trace_id": trace_id,
                "timestamp": utc_now(),
                "event": "TOOL_STARTED",
                "operation": operation,
                "inputs": sanitize_for_audit(inputs or {}),
            }
        )
        return trace_id

    def complete(self, trace_id: str, operation: str, result: Any) -> None:
        output_source, output = observable_tool_output(result)
        self._append(
            {
                "event_id": str(uuid4()),
                "trace_id": trace_id,
                "timestamp": utc_now(),
                "event": "TOOL_COMPLETED",
                "operation": operation,
                "result": summarize_tool_result(result),
                "output_source": output_source,
                "output": sanitize_for_audit(output),
            }
        )

    def fail(self, trace_id: str, operation: str, error: Any) -> None:
        if hasattr(error, "to_dict"):
            error_value = error.to_dict()
        else:
            error_value = {
                "type": type(error).__name__,
                "message": str(error),
            }
        self._append(
            {
                "event_id": str(uuid4()),
                "trace_id": trace_id,
                "timestamp": utc_now(),
                "event": "TOOL_FAILED",
                "operation": operation,
                "error": sanitize_for_audit(error_value),
            }
        )

    def read(
        self,
        *,
        trace_id: str | None = None,
        batch_id: str | None = None,
        operation: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
            raise InputValidationError("审计日志 limit 必须为 1-500 的整数")
        if not self.path.exists():
            return {
                "events": [],
                "count": 0,
                "limit": limit,
                "chain_of_thought_included": False,
            }
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            events = [json.loads(line) for line in lines if line.strip()]
        except (OSError, json.JSONDecodeError) as exc:
            raise PersistenceError(
                "智能体审计日志读取失败",
                details={"path": str(self.path), "error": str(exc)},
            ) from exc

        if trace_id:
            events = [item for item in events if item.get("trace_id") == trace_id]
        if operation:
            events = [item for item in events if item.get("operation") == operation]
        if batch_id:
            events = [
                item
                for item in events
                if batch_id in json.dumps(item, ensure_ascii=False)
            ]
        selected = events[-limit:]
        return {
            "events": selected,
            "count": len(selected),
            "limit": limit,
            "filters": {
                "trace_id": trace_id,
                "batch_id": batch_id,
                "operation": operation,
            },
            "chain_of_thought_included": False,
            "note": "记录可观察的工具调用、字段来源、状态迁移和错误；不记录模型内部思维链。",
        }

    def _append(self, event: dict[str, Any]) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            with self._lock:
                with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(line + "\n")
        except OSError as exc:
            raise PersistenceError(
                "智能体审计日志写入失败",
                details={"path": str(self.path), "error": str(exc)},
            ) from exc

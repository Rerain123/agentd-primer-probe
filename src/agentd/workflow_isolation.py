"""Infrastructure boundaries for independently versioned workflow runtimes.

The workflow packages own implementation code.  This module contains only
cross-cutting routing: scoped persistence, terminal-export dispatch, and error
translation back to the public ``agentd`` contract.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable, Mapping

from .errors import (
    AgentDError,
    InputValidationError,
    NoCandidateError,
    PersistenceError,
    SchemaValidationError,
    ToolUnavailableError,
)
from .job_store import JobStore


_PUBLIC_ERROR_BY_CODE: dict[str, type[AgentDError]] = {
    "AGENTD_ERROR": AgentDError,
    "INVALID_INPUT": InputValidationError,
    "TOOL_UNAVAILABLE": ToolUnavailableError,
    "NO_CANDIDATE": NoCandidateError,
    "INVALID_OUTPUT_SCHEMA": SchemaValidationError,
    "PERSISTENCE_ERROR": PersistenceError,
}


def translate_snapshot_error(error: Exception) -> Exception:
    """Translate a private snapshot exception to the stable public classes."""

    if isinstance(error, AgentDError):
        return error
    if not type(error).__module__.startswith("agentd_workflow_snapshots."):
        return error
    to_dict = getattr(error, "to_dict", None)
    if not callable(to_dict):
        return error
    payload = to_dict()
    if not isinstance(payload, dict):
        return error
    error_type = _PUBLIC_ERROR_BY_CODE.get(
        str(payload.get("code") or ""),
        AgentDError,
    )
    details = payload.get("details")
    return error_type(
        str(payload.get("message") or str(error)),
        details=copy.deepcopy(details) if isinstance(details, dict) else {},
    )


def call_snapshot(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Invoke private code while preserving the public exception contract."""

    try:
        return function(*args, **kwargs)
    except Exception as error:
        translated = translate_snapshot_error(error)
        if translated is error:
            raise
        raise translated from error


class WorkflowScopedJobStore:
    """Route selected workflows to version-specific state directories.

    A root-level copy is maintained as a non-authoritative compatibility view
    for existing callers that inspect ``<state_root>/<job_id>.json`` directly.
    A hard link is intentionally not used: writes through a compatibility path
    must never mutate the version-scoped authoritative result.
    """

    def __init__(
        self,
        root: str | Path,
        scopes: Mapping[str, str | Path],
    ) -> None:
        self.root = Path(root)
        self.base = JobStore(self.root)
        self.scopes = {
            str(workflow).upper(): JobStore(path)
            for workflow, path in scopes.items()
        }

    def _scope_for_result(self, result: dict) -> JobStore | None:
        return self.scopes.get(str(result.get("workflow") or "").upper())

    def _existing_scope(self, job_id: str) -> JobStore | None:
        JobStore._validate_job_id(job_id)
        for store in self.scopes.values():
            if (store.root / f"{job_id}.json").is_file():
                return store
        return None

    def _refresh_compatibility_view(self, _source: Path, result: dict) -> None:
        self.base.save(copy.deepcopy(result))

    def save(self, result: dict) -> Path:
        store = self._scope_for_result(result)
        if store is None:
            return self.base.save(result)
        destination = store.save(result)
        self._refresh_compatibility_view(destination, result)
        return destination

    def get(self, job_id: str) -> dict:
        scoped = self._existing_scope(job_id)
        if scoped is not None:
            return scoped.get(job_id)
        return self.base.get(job_id)

    def save_attachment(self, job_id: str, name: str, data: dict) -> Path:
        store = self._existing_scope(job_id) or self.base
        return store.save_attachment(job_id, name, data)

    def get_attachment(self, job_id: str, name: str) -> dict:
        scoped = self._existing_scope(job_id)
        if scoped is not None:
            try:
                return scoped.get_attachment(job_id, name)
            except InputValidationError:
                # A migrated job can still have a legacy attachment at root.
                pass
        return self.base.get_attachment(job_id, name)

    def scope_root(self, workflow: str) -> Path | None:
        store = self.scopes.get(str(workflow).upper())
        return store.root if store is not None else None


class WorkflowTerminalDeliveryRouter:
    """Dispatch terminal artifacts to the workflow-owned renderer/store."""

    def __init__(
        self,
        delegates: Mapping[str, Any],
        *,
        legacy: Any,
    ) -> None:
        self.delegates = {
            str(workflow).upper(): delegate
            for workflow, delegate in delegates.items()
        }
        self.legacy = legacy

    def build_delivery(self, report: dict[str, Any]) -> dict[str, Any]:
        workflow = str(report.get("workflow") or "").upper()
        delegate = self.delegates.get(workflow, self.legacy)
        return call_snapshot(delegate.build_delivery, report)

    def get_export_path(self, job_id: str, file_name: str) -> Path:
        candidates = [*self.delegates.values(), self.legacy]
        for delegate in candidates:
            try:
                return call_snapshot(
                    delegate.get_export_path,
                    job_id,
                    file_name,
                )
            except InputValidationError:
                continue
        raise InputValidationError(
            "工作流终态附件不存在",
            details={"job_id": job_id, "file_name": file_name},
        )

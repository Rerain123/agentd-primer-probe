"""Terminal delivery owned by the isolated WF-05 runtime.

The workflow implementation owns the result.  This module only persists a
deterministic HTML view, publishes it through the configured artifact
publisher, and builds the shared host ``primer_design_output`` envelope.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from contextlib import contextmanager
from pathlib import Path
from threading import Lock, RLock
from typing import Any, Iterator
from uuid import uuid4

from .errors import InputValidationError, PersistenceError
from .object_storage import RustFSArtifactPublisher
from .wf05_identifiers import wf05_task_code
from .wf05_ui_messages import build_workflow_terminal_output
from .wf05_report_html import (
    WORKFLOW_REPORT_RENDERER_VERSION,
    render_workflow_report_html,
)


_WORKFLOW_PATTERN = re.compile(r"WF-05")
_JOB_ID_PATTERN = re.compile(r"[0-9a-fA-F-]+")


class WorkflowTerminalDeliveryStore:
    """Persist and publish deterministic WF-05 terminal reports."""

    def __init__(
        self,
        state_root: str | Path,
        artifact_publisher: RustFSArtifactPublisher,
    ) -> None:
        self.root = Path(state_root) / "workflow-exports"
        self.artifact_publisher = artifact_publisher
        self._lock_guard = Lock()
        self._job_locks: dict[str, tuple[RLock, int]] = {}

    @staticmethod
    def _validate(workflow: str, job_id: str) -> tuple[str, str]:
        selected_workflow = str(workflow).upper()
        selected_job_id = str(job_id)
        if not _WORKFLOW_PATTERN.fullmatch(selected_workflow):
            raise InputValidationError(
                "终态交付仅支持WF-05",
                details={"workflow": workflow},
            )
        if not selected_job_id or not _JOB_ID_PATTERN.fullmatch(selected_job_id):
            raise InputValidationError(
                "终态交付job_id无效",
                details={"job_id": job_id},
            )
        return selected_workflow, selected_job_id

    def _directory(self, job_id: str) -> Path:
        return self.root / job_id

    @contextmanager
    def _serialize_job(self, job_id: str) -> Iterator[None]:
        with self._lock_guard:
            lock, users = self._job_locks.get(job_id, (RLock(), 0))
            self._job_locks[job_id] = (lock, users + 1)
        try:
            with lock:
                yield
        finally:
            with self._lock_guard:
                current_lock, current_users = self._job_locks[job_id]
                if current_users == 1:
                    self._job_locks.pop(job_id, None)
                else:
                    self._job_locks[job_id] = (
                        current_lock,
                        current_users - 1,
                    )

    @staticmethod
    def _atomic_write_bytes(destination: Path, content: bytes) -> None:
        temporary = destination.with_name(
            f".{destination.name}.{uuid4().hex}.tmp"
        )
        try:
            temporary.write_bytes(content)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _atomic_write_json(destination: Path, value: dict[str, Any]) -> None:
        temporary = destination.with_name(
            f".{destination.name}.{uuid4().hex}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(value, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    def ensure_export(
        self,
        workflow: str,
        job_id: str,
        report: dict[str, Any],
    ) -> dict[str, Any]:
        _selected_workflow, selected_job_id = self._validate(workflow, job_id)
        with self._serialize_job(selected_job_id):
            return self._ensure_export_unlocked(workflow, job_id, report)

    def _ensure_export_unlocked(
        self,
        workflow: str,
        job_id: str,
        report: dict[str, Any],
    ) -> dict[str, Any]:
        selected_workflow, selected_job_id = self._validate(workflow, job_id)
        if not isinstance(report, dict):
            raise InputValidationError("终态报告必须为对象")
        directory = self._directory(selected_job_id)
        content = render_workflow_report_html(report)
        digest = hashlib.sha256(content).hexdigest()
        metadata_path = directory / "metadata.json"
        try:
            existing_metadata: dict[str, Any] = {}
            if metadata_path.is_file():
                existing_metadata = json.loads(
                    metadata_path.read_text(encoding="utf-8")
                )
                if not isinstance(existing_metadata, dict):
                    raise PersistenceError(
                        "终态报告附件清单必须为对象",
                        details={"job_id": selected_job_id},
                    )
            task_code = str(
                existing_metadata.get("task_code") or ""
            ) or wf05_task_code(
                selected_job_id,
                report.get("created_at"),
            )
            filename = str(existing_metadata.get("filename") or "") or (
                f"{task_code}_{selected_job_id}_终态报告.html"
            )
            if Path(filename).name != filename or filename in {"", ".", ".."}:
                raise PersistenceError(
                    "终态报告附件清单包含非法文件名",
                    details={
                        "workflow": selected_workflow,
                        "job_id": selected_job_id,
                    },
                )
            destination = directory / filename
            metadata_is_current = (
                existing_metadata.get("workflow") == selected_workflow
                and existing_metadata.get("job_id") == selected_job_id
                and existing_metadata.get("renderer_version")
                == WORKFLOW_REPORT_RENDERER_VERSION
                and existing_metadata.get("sha256") == digest
                and destination.is_file()
            )
            if metadata_is_current:
                publication = existing_metadata.get("object_storage") or {}
                publication_is_current = (
                    publication.get("provider") == "rustfs"
                    and publication.get("sha256") == digest
                    and existing_metadata.get("file_url")
                    == publication.get("file_url")
                )
                if (
                    not self.artifact_publisher.enabled
                    or publication_is_current
                ):
                    return copy.deepcopy(existing_metadata)
                refreshed = copy.deepcopy(existing_metadata)
                published = self.artifact_publisher.publish(
                    destination,
                    batch_id=selected_job_id,
                    media_type=str(refreshed["media_type"]),
                    sha256=digest,
                    object_name=filename,
                )
                if published is None:
                    return refreshed
                refreshed["object_storage"] = published
                refreshed["file_url"] = published["file_url"]
                self._atomic_write_json(metadata_path, refreshed)
                return refreshed
            directory.mkdir(parents=True, exist_ok=True)
            if not destination.is_file() or hashlib.sha256(
                destination.read_bytes()
            ).hexdigest() != digest:
                self._atomic_write_bytes(destination, content)
            metadata: dict[str, Any] = {
                "workflow": selected_workflow,
                "job_id": selected_job_id,
                "task_code": task_code,
                "format": "html",
                "renderer_version": WORKFLOW_REPORT_RENDERER_VERSION,
                "filename": filename,
                "media_type": "text/html; charset=utf-8",
                "size_bytes": len(content),
                "sha256": digest,
                "file_url": f"/data/reports/{selected_job_id}/{filename}",
            }
            publication = self.artifact_publisher.publish(
                destination,
                batch_id=selected_job_id,
                media_type=metadata["media_type"],
                sha256=digest,
                object_name=filename,
            )
            if publication is not None:
                metadata["object_storage"] = publication
                metadata["file_url"] = publication["file_url"]
            self._atomic_write_json(metadata_path, metadata)
            return copy.deepcopy(metadata)
        except (OSError, json.JSONDecodeError) as exc:
            raise PersistenceError(
                "终态报告持久化失败",
                details={
                    "workflow": selected_workflow,
                    "job_id": selected_job_id,
                    "error": str(exc),
                },
            ) from exc

    def build_delivery(self, report: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(report, dict):
            raise InputValidationError("终态报告必须为对象")
        workflow = str(report.get("workflow") or "")
        job_id = str(report.get("job_id") or "")
        metadata = self.ensure_export(workflow, job_id, report)
        delivery = copy.deepcopy(report)
        delivery["batch_code"] = metadata["task_code"]
        delivery["ui_message"] = build_workflow_terminal_output(report, metadata)
        return delivery

    def get_export_path(self, job_id: str, file_name: str) -> Path:
        selected_job_id = str(job_id)
        if not selected_job_id or not _JOB_ID_PATTERN.fullmatch(selected_job_id):
            raise InputValidationError("终态交付job_id无效")
        with self._serialize_job(selected_job_id):
            return self._get_export_path_unlocked(selected_job_id, file_name)

    def _get_export_path_unlocked(
        self,
        job_id: str,
        file_name: str,
    ) -> Path:
        _workflow, selected_job_id, metadata = self._validate_from_metadata(job_id)
        expected = str(metadata.get("filename") or "")
        if not expected or file_name != expected or Path(file_name).name != file_name:
            raise InputValidationError(
                "终态报告附件不存在",
                details={"job_id": selected_job_id, "file_name": file_name},
            )
        path = self._directory(selected_job_id) / expected
        if not path.is_file():
            raise InputValidationError(
                "终态报告附件不存在",
                details={"job_id": selected_job_id, "file_name": file_name},
            )
        return path

    def _load_metadata(self, job_id: str) -> dict[str, Any]:
        metadata_path = self._directory(job_id) / "metadata.json"
        if not metadata_path.is_file():
            raise InputValidationError(
                "终态报告附件不存在",
                details={"job_id": job_id},
            )
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PersistenceError(
                "终态报告附件清单无法读取",
                details={"job_id": job_id, "error": str(exc)},
            ) from exc
        if not isinstance(metadata, dict):
            raise PersistenceError(
                "终态报告附件清单必须为对象",
                details={"job_id": job_id},
            )
        return metadata

    def _validate_from_metadata(
        self,
        job_id: str,
    ) -> tuple[str, str, dict[str, Any]]:
        selected_job_id = str(job_id)
        if not selected_job_id or not _JOB_ID_PATTERN.fullmatch(selected_job_id):
            raise InputValidationError("终态交付job_id无效")
        metadata = self._load_metadata(selected_job_id)
        selected_workflow, selected_job_id = self._validate(
            str(metadata.get("workflow") or ""),
            selected_job_id,
        )
        if str(metadata.get("job_id") or "") != selected_job_id:
            raise PersistenceError(
                "终态报告附件清单任务标识不一致",
                details={"job_id": selected_job_id},
            )
        return selected_workflow, selected_job_id, metadata

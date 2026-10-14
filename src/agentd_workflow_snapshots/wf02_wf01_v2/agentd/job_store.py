"""Small filesystem job store for the synchronous service."""

from __future__ import annotations

import json
from pathlib import Path

from .errors import InputValidationError, PersistenceError


class JobStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def save(self, result: dict) -> Path:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            destination = self.root / f"{result['job_id']}.json"
            temporary = destination.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(destination)
            return destination
        except OSError as exc:
            raise PersistenceError(
                "任务结果持久化失败",
                details={"state_dir": str(self.root), "error": str(exc)},
            ) from exc

    def get(self, job_id: str) -> dict:
        self._validate_job_id(job_id)
        path = self.root / f"{job_id}.json"
        if not path.exists():
            raise InputValidationError("任务不存在", details={"job_id": job_id})
        return json.loads(path.read_text(encoding="utf-8"))

    def save_attachment(self, job_id: str, name: str, data: dict) -> Path:
        self._validate_job_id(job_id)
        if not name or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in name.lower()):
            raise InputValidationError("非法任务附件名称")
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            destination = self.root / f"{job_id}.{name}.json"
            temporary = destination.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(destination)
            return destination
        except OSError as exc:
            raise PersistenceError(
                "任务附件持久化失败",
                details={"state_dir": str(self.root), "job_id": job_id, "name": name, "error": str(exc)},
            ) from exc

    def get_attachment(self, job_id: str, name: str) -> dict:
        self._validate_job_id(job_id)
        if not name or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in name.lower()):
            raise InputValidationError("非法任务附件名称")
        path = self.root / f"{job_id}.{name}.json"
        if not path.exists():
            raise InputValidationError(
                "任务附件不存在",
                details={"job_id": job_id, "name": name},
            )
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _validate_job_id(job_id: str) -> None:
        if not job_id or any(char not in "0123456789abcdef-" for char in job_id.lower()):
            raise InputValidationError("非法job_id")

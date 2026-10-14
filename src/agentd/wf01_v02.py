"""WF-01 PRD v0.2 batch intake and preflight boundary.

This module deliberately stops before Primer3 execution.  It converts the
user's one-or-more template sequences into one target draft, records issues
instead of silently guessing, and persists a versioned batch record for M2
onwards.  Multiple templates are retained for the M2 MAFFT/conserved-region
branch; one template is retained for the direct Primer3 branch.
The legacy WF-01 workflow remains untouched until the new execution engine is
ready to replace it.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from .database import DatabaseCatalog
from .errors import (
    AgentDError,
    InputValidationError,
    PersistenceError,
    SchemaValidationError,
)
from .interactions import (
    build_execution_error_interaction,
    build_issue_interaction,
)
from .identifiers import wf01_batch_code
from .models import TargetExecutionPath, TargetManifestStatus
from .object_storage import RustFSArtifactPublisher


PRD_VERSION = "0.2"
MINIMUM_TEMPLATE_LENGTH = 80
FIXED_TOP_CANDIDATES = 10
DNA_BASES = frozenset("ACGT")
IUPAC_BASES = frozenset("ACGTRYSWKMBDHVN")

# This is an intake-level alias catalogue, not a genome database.  M4 replaces
# ``NORMALIZED`` objects with authoritative database resolution and provenance.
SUPPORTED_HOSTS = {
    "pig": {"canonical_name": "pig", "scientific_name": "Sus scrofa", "aliases": {"pig", "猪", "sus scrofa", "9823"}},
    "cattle": {"canonical_name": "cattle", "scientific_name": "Bos taurus", "aliases": {"cattle", "cow", "牛", "bos taurus", "9913"}},
    "chicken": {"canonical_name": "chicken", "scientific_name": "Gallus gallus", "aliases": {"chicken", "鸡", "gallus gallus", "9031"}},
    "sheep": {"canonical_name": "sheep", "scientific_name": "Ovis aries", "aliases": {"sheep", "绵羊", "羊", "ovis aries", "9940"}},
    "goat": {"canonical_name": "goat", "scientific_name": "Capra hircus", "aliases": {"goat", "山羊", "capra hircus", "9925"}},
    "duck": {"canonical_name": "duck", "scientific_name": None, "aliases": {"duck", "鸭"}},
    "goose": {"canonical_name": "goose", "scientific_name": None, "aliases": {"goose", "鹅"}},
    "pigeon": {"canonical_name": "pigeon", "scientific_name": None, "aliases": {"pigeon", "鸽", "鸽子"}},
}

# Common names accepted by the conversational input are retained for display,
# while these aliases provide unambiguous NCBI organism names/accessions for
# remote nt background exclusion and target-hit de-duplication.
KNOWN_TARGET_IDENTITIES = {
    "csfv": {
        "scientific_name": "Pestivirus C",
        "accessions": ["NC_002657.1"],
        "aliases": {
            "csfv",
            "classical swine fever virus",
            "猪瘟病毒",
            "猪瘟病毒（csfv）",
            "猪瘟病毒(csfv)",
        },
    },
    "lawsonia_intracellularis": {
        "scientific_name": "Lawsonia intracellularis",
        "accessions": [],
        "aliases": {
            "lawsonia intracellularis",
            "胞内劳森菌",
            "胞内劳森氏菌",
        },
    },
}

DEFAULT_PARAMETERS: dict[str, int | float] = {
    "product_min_bp": 70,
    "product_max_bp": 150,
    "primer_min_size": 18,
    "primer_opt_size": 20,
    "primer_max_size": 28,
    "primer_min_tm": 58.0,
    "primer_opt_tm": 60.0,
    "primer_max_tm": 62.0,
    "primer_min_gc": 0.0,
    "primer_max_gc": 100.0,
    "probe_min_size": 22,
    "probe_opt_size": 27,
    "probe_max_size": 32,
    "probe_tm_delta_min": 8.0,
    "probe_tm_delta_max": 10.0,
}

PARAMETER_PROFILES = {
    "default": DEFAULT_PARAMETERS,
    "L1": {**DEFAULT_PARAMETERS, "primer_max_tm": 65.0, "product_max_bp": 180},
    "L2": {
        **DEFAULT_PARAMETERS,
        "primer_min_tm": 50.0,
        "primer_opt_tm": 58.0,
        "primer_max_tm": 65.0,
        "product_max_bp": 200,
    },
}

NUMERIC_PARAMETERS = frozenset(DEFAULT_PARAMETERS)
INTEGER_PARAMETERS = frozenset(
    {
        "product_min_bp",
        "product_max_bp",
        "primer_min_size",
        "primer_opt_size",
        "primer_max_size",
        "probe_min_size",
        "probe_opt_size",
        "probe_max_size",
    }
)
OUT_OF_SCOPE_REQUESTS = {
    "use_mgb": "MGB",
    "multiplex": "MULTIPLEX",
    "internal_control": "INTERNAL_CONTROL",
    "degenerate": "DEGENERATE_PRIMER",
    "amplicon_secondary_structure": "AMPLICON_SECONDARY_STRUCTURE",
}
ACCESSION_PATTERN = re.compile(r"^[A-Za-z]{1,8}_?\d+(?:\.\d+)?$")
WF01_BATCH_CODE_PATTERN = re.compile(
    r"^WF01-[0-9]{6}-[0-9]{4}-([A-F0-9]{4})$",
    re.IGNORECASE,
)
PRIMER3_BLOCKING_ISSUE_CODES = frozenset(
    {
        "MISSING_SEQUENCE",
        "ILLEGAL_BASES",
        "TEMPLATE_TOO_SHORT",
        "INVALID_DESIGN_PARAMETERS",
        "UNSUPPORTED_PARAMETER",
        "INVALID_PARAMETER_VALUE",
        "PARAMETER_OUT_OF_HARD_RANGE",
        "INVALID_PARAMETER_ORDER",
    }
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WF01V02BatchStore:
    """Filesystem persistence for v0.2 batch records, separate from legacy jobs."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root) / "wf01-v02-batches"

    @staticmethod
    def _validate_id(batch_id: str) -> None:
        try:
            UUID(str(batch_id))
        except (TypeError, ValueError) as exc:
            raise InputValidationError("非法批次任务ID", details={"batch_id": batch_id}) from exc

    def resolve_id(self, batch_reference: str) -> str:
        """Resolve an internal UUID or a user-facing WF-01 task code."""
        reference = str(batch_reference or "").strip()
        try:
            return str(UUID(reference))
        except (TypeError, ValueError, AttributeError):
            pass

        match = WF01_BATCH_CODE_PATTERN.fullmatch(reference)
        if match is None:
            raise InputValidationError(
                "非法批次任务ID或任务编号",
                details={
                    "batch_reference": batch_reference,
                    "accepted_formats": ["UUID", "WF01-YYMMDD-HHMM-XXXX"],
                },
            )

        suffix = match.group(1).lower()
        matches: list[str] = []
        try:
            for path in self.root.glob(f"{suffix}*.json"):
                value = json.loads(path.read_text(encoding="utf-8"))
                resolved_id = str(value.get("batch_id") or path.stem)
                self._validate_id(resolved_id)
                stored_code = str(
                    value.get("batch_code")
                    or wf01_batch_code(
                        resolved_id,
                        value.get("created_at"),
                        workflow=str(value.get("workflow") or "WF-01"),
                    )
                )
                if stored_code.upper() == reference.upper():
                    matches.append(str(UUID(resolved_id)))
        except (OSError, json.JSONDecodeError) as exc:
            raise PersistenceError(
                "WF-01 任务编号解析失败",
                details={"batch_reference": reference, "error": str(exc)},
            ) from exc

        unique_matches = sorted(set(matches))
        if not unique_matches:
            raise InputValidationError(
                "批次任务不存在",
                details={"batch_reference": reference},
            )
        if len(unique_matches) > 1:
            raise InputValidationError(
                "任务编号对应多个批次，必须使用内部批次ID",
                details={
                    "batch_reference": reference,
                    "matching_batch_count": len(unique_matches),
                },
            )
        return unique_matches[0]

    def save(self, batch: dict[str, Any]) -> Path:
        batch_id = str(batch.get("batch_id", ""))
        self._validate_id(batch_id)
        batch.setdefault(
            "batch_code",
            wf01_batch_code(
                batch_id,
                batch.get("created_at"),
                workflow=str(batch.get("workflow") or "WF-01"),
            ),
        )
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            destination = self.root / f"{batch_id}.json"
            temporary = destination.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(destination)
            return destination
        except OSError as exc:
            raise PersistenceError("WF-01 v0.2 批次持久化失败", details={"error": str(exc)}) from exc

    def get(self, batch_id: str) -> dict[str, Any]:
        batch_reference = batch_id
        batch_id = self.resolve_id(batch_reference)
        path = self.root / f"{batch_id}.json"
        if not path.exists():
            raise InputValidationError(
                "批次任务不存在",
                details={
                    "batch_reference": batch_reference,
                    "batch_id": batch_id,
                },
            )
        try:
            batch = json.loads(path.read_text(encoding="utf-8"))
            batch.setdefault(
                "batch_code",
                wf01_batch_code(
                    str(batch.get("batch_id") or batch_id),
                    batch.get("created_at"),
                    workflow=str(batch.get("workflow") or "WF-01"),
                ),
            )
            return batch
        except (OSError, json.JSONDecodeError) as exc:
            raise PersistenceError("WF-01 v0.2 批次读取失败", details={"batch_id": batch_id, "error": str(exc)}) from exc

    def next_task_version(self, lineage_root_batch_id: str) -> int:
        """Return the next monotonic version number within one task lineage."""
        self._validate_id(lineage_root_batch_id)
        highest = 0
        try:
            for path in self.root.glob("*.json"):
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise PersistenceError(
                        "WF-01 v0.2 批次版本扫描失败",
                        details={"path": str(path), "error": str(exc)},
                    ) from exc
                root = value.get("lineage_root_batch_id") or value.get(
                    "batch_id"
                )
                if root != lineage_root_batch_id:
                    continue
                try:
                    highest = max(highest, int(value.get("task_version") or 0))
                except (TypeError, ValueError) as exc:
                    raise PersistenceError(
                        "WF-01 v0.2 批次版本号无效",
                        details={"path": str(path)},
                    ) from exc
        except OSError as exc:
            raise PersistenceError(
                "WF-01 v0.2 批次版本扫描失败",
                details={"lineage_root_batch_id": lineage_root_batch_id, "error": str(exc)},
            ) from exc
        return highest + 1

    def _artifact_directory(self, batch_id: str) -> Path:
        resolved_id = self.resolve_id(batch_id)
        return self.root / "artifacts" / resolved_id

    def save_scientific_artifact(
        self,
        batch_id: str,
        *,
        artifact_id: str,
        stage: str,
        tools: list[str],
        raw_data: Any,
        target_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist one expert-facing raw scientific evidence snapshot."""
        self._validate_id(batch_id)
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", artifact_id):
            raise InputValidationError(
                "科学原始数据 artifact_id 无效",
                details={"artifact_id": artifact_id},
            )
        directory = self._artifact_directory(batch_id) / "scientific-artifacts"
        destination = directory / f"{artifact_id}.json"
        created_at = utc_now()
        document = {
            "schemaVersion": "wf01-scientific-artifact-v1",
            "artifactId": artifact_id,
            "batchId": batch_id,
            "targetId": target_id,
            "stage": str(stage).upper(),
            "tools": [str(tool) for tool in tools],
            "createdAt": created_at,
            "dataScope": "RAW_SCIENTIFIC_STAGE_OUTPUT",
            "rawData": copy.deepcopy(raw_data),
        }
        content = json.dumps(
            document,
            ensure_ascii=False,
            indent=2,
            default=str,
        ).encode("utf-8")
        entry = {
            "artifactId": artifact_id,
            "stage": str(stage).upper(),
            "tools": [str(tool) for tool in tools],
            "targetId": target_id,
            "fileName": destination.name,
            "mimeType": "application/json; charset=utf-8",
            "sizeBytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "downloadUrl": (
                f"/v1/wf01/tasks/{batch_id}/scientific-artifacts/{artifact_id}"
            ),
            "createdAt": created_at,
            "dataScope": "RAW_SCIENTIFIC_STAGE_OUTPUT",
        }
        try:
            directory.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(".json.tmp")
            temporary.write_bytes(content)
            temporary.replace(destination)
            index_path = directory / "index.json"
            if index_path.exists():
                index = json.loads(index_path.read_text(encoding="utf-8"))
            else:
                index = {
                    "schemaVersion": "wf01-scientific-artifact-index-v1",
                    "batchId": batch_id,
                    "artifacts": [],
                }
            artifacts = [
                item
                for item in index.get("artifacts") or []
                if item.get("artifactId") != artifact_id
            ]
            artifacts.append(entry)
            artifacts.sort(
                key=lambda item: (
                    str(item.get("stage") or ""),
                    str(item.get("targetId") or ""),
                    str(item.get("artifactId") or ""),
                )
            )
            index["artifacts"] = artifacts
            index_tmp = index_path.with_suffix(".json.tmp")
            index_tmp.write_text(
                json.dumps(index, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            index_tmp.replace(index_path)
            return copy.deepcopy(entry)
        except (OSError, json.JSONDecodeError) as exc:
            raise PersistenceError(
                "WF-01 科学原始数据持久化失败",
                details={
                    "batch_id": batch_id,
                    "artifact_id": artifact_id,
                    "error": str(exc),
                },
            ) from exc

    def list_scientific_artifacts(self, batch_id: str) -> dict[str, Any]:
        batch_id = self.resolve_id(batch_id)
        index_path = (
            self._artifact_directory(batch_id)
            / "scientific-artifacts"
            / "index.json"
        )
        if not index_path.exists():
            return {
                "schemaVersion": "wf01-scientific-artifact-index-v1",
                "batchId": batch_id,
                "artifacts": [],
            }
        try:
            return json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PersistenceError(
                "WF-01 科学原始数据索引读取失败",
                details={"batch_id": batch_id, "error": str(exc)},
            ) from exc

    def get_scientific_artifact_path(
        self,
        batch_id: str,
        artifact_id: str,
    ) -> Path:
        batch_id = self.resolve_id(batch_id)
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", artifact_id):
            raise InputValidationError(
                "科学原始数据 artifact_id 无效",
                details={"artifact_id": artifact_id},
            )
        path = (
            self._artifact_directory(batch_id)
            / "scientific-artifacts"
            / f"{artifact_id}.json"
        )
        if not path.is_file():
            raise InputValidationError(
                "科学原始数据文件不存在",
                details={"batch_id": batch_id, "artifact_id": artifact_id},
            )
        return path

    def save_scientific_artifact_archive(
        self,
        batch_id: str,
        *,
        filename: str,
    ) -> Path:
        """Package a user-readable projection of the internal scientific evidence."""
        self._validate_id(batch_id)
        if Path(filename).name != filename or Path(filename).suffix.lower() != ".zip":
            raise InputValidationError(
                "科学原始数据压缩包文件名无效",
                details={"filename": filename},
            )
        index = self.list_scientific_artifacts(batch_id)
        report = self.get_report(batch_id)
        render_source = copy.deepcopy(report)
        render_source["_scientific_artifact_manifest"] = index
        from .wf01_v02_m6 import render_scientific_data_archive

        try:
            content = render_scientific_data_archive(render_source)
        except Exception as exc:
            raise PersistenceError(
                "WF-01 科学工具数据包生成失败",
                details={"batch_id": batch_id, "error": str(exc)},
            ) from exc
        return self.save_export(
            batch_id,
            "scientific_data",
            content,
            filename=filename,
        )

    def save_report(
        self,
        batch_id: str,
        report: dict[str, Any],
    ) -> Path:
        directory = self._artifact_directory(batch_id)
        destination = directory / "canonical-report.json"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(destination)
            return destination
        except OSError as exc:
            raise PersistenceError(
                "WF-01 v0.2 canonical report 持久化失败",
                details={"batch_id": batch_id, "error": str(exc)},
            ) from exc

    def get_report(self, batch_id: str) -> dict[str, Any]:
        path = self._artifact_directory(batch_id) / "canonical-report.json"
        if not path.exists():
            raise InputValidationError(
                "该批次尚未生成 M6 canonical report",
                details={"batch_id": batch_id},
            )
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PersistenceError(
                "WF-01 v0.2 canonical report 读取失败",
                details={"batch_id": batch_id, "error": str(exc)},
            ) from exc

    def save_export(
        self,
        batch_id: str,
        format_name: str,
        content: bytes,
        *,
        filename: str | None = None,
    ) -> Path:
        extensions = {
            "html": "html",
            "process_html": "html",
            "rules_html": "html",
            "csv": "csv",
            "pdf": "pdf",
            "scientific_data": "zip",
        }
        selected = str(format_name).lower()
        if selected not in extensions:
            raise InputValidationError(
                "不支持的 M6 导出格式",
                details={
                    "format": format_name,
                    "supported": sorted(extensions),
                },
            )
        directory = self._artifact_directory(batch_id)
        selected_filename = filename or f"wf01-report.{extensions[selected]}"
        if (
            Path(selected_filename).name != selected_filename
            or Path(selected_filename).suffix.lower()
            != f".{extensions[selected]}"
        ):
            raise InputValidationError(
                "M6 导出文件名无效",
                details={"filename": selected_filename, "format": selected},
            )
        destination = directory / selected_filename
        try:
            directory.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(
                f".{extensions[selected]}.tmp"
            )
            temporary.write_bytes(content)
            temporary.replace(destination)
            return destination
        except OSError as exc:
            raise PersistenceError(
                "WF-01 v0.2 报告文件持久化失败",
                details={
                    "batch_id": batch_id,
                    "format": selected,
                    "error": str(exc),
                },
            ) from exc

    def get_export_path(
        self,
        batch_id: str,
        format_name: str,
        *,
        filename: str | None = None,
    ) -> Path:
        extensions = {
            "html": "html",
            "process_html": "html",
            "rules_html": "html",
            "csv": "csv",
            "pdf": "pdf",
            "scientific_data": "zip",
        }
        selected = str(format_name).lower()
        if selected not in extensions:
            raise InputValidationError(
                "不支持的 M6 导出格式",
                details={
                    "format": format_name,
                    "supported": sorted(extensions),
                },
            )
        selected_filename = filename or f"wf01-report.{extensions[selected]}"
        if (
            Path(selected_filename).name != selected_filename
            or Path(selected_filename).suffix.lower()
            != f".{extensions[selected]}"
        ):
            raise InputValidationError(
                "M6 导出文件名无效",
                details={"filename": selected_filename, "format": selected},
            )
        path = self._artifact_directory(batch_id) / selected_filename
        if not path.is_file():
            raise InputValidationError(
                "该批次尚未生成所请求的 M6 导出文件",
                details={"batch_id": batch_id, "format": selected},
            )
        return path


class WF01V02Preflight:
    """Build a deterministic M1 preflight result from a JSON-compatible payload."""

    def __init__(
        self,
        payload: dict[str, Any],
        *,
        interaction_mode: str = "LEGACY",
    ) -> None:
        if not isinstance(payload, dict):
            raise InputValidationError("WF-01 v0.2 请求必须为对象")
        self.payload = copy.deepcopy(payload)
        self.interaction_mode = interaction_mode
        self.issues: list[dict[str, Any]] = []
        self.intent_extraction: dict[str, Any] = {
            "method": "M1_LABELLED_TEXT_ONLY",
            "input_present": False,
            "extracted_fields": {},
        }
        self.manifest_overrides: dict[str, dict[str, Any]] = {}
        self.manifest_input_present = False

    def add_issue(
        self,
        code: str,
        severity: str,
        message: str,
        *,
        scope: str = "BATCH",
        target_id: str | None = None,
        field: str | None = None,
        evidence: dict[str, Any] | None = None,
        allowed_actions: list[str] | None = None,
    ) -> None:
        self.issues.append(
            {
                "issue_id": f"issue-{len(self.issues) + 1:03d}",
                "code": code,
                "severity": severity,
                "scope": scope,
                "target_id": target_id,
                "field": field,
                "message": message,
                "evidence": evidence or {},
                "allowed_actions": allowed_actions or ["EDIT", "REMOVE_TARGET"],
                "status": "OPEN",
            }
        )

    @staticmethod
    def _normalise_sequence(raw: Any) -> tuple[str, list[str], list[str]]:
        if not isinstance(raw, str):
            return "", [], ["NON_STRING_SEQUENCE"]
        sequence = re.sub(r"\s+", "", raw).upper().replace("U", "T")
        invalid = sorted(set(sequence) - IUPAC_BASES)
        ambiguity = sorted(set(sequence) - DNA_BASES) if not invalid else []
        return sequence, ambiguity, invalid

    @staticmethod
    def _as_text(value: Any) -> str:
        return value.strip() if isinstance(value, str) else ""

    def _parse_fasta_text(self, content: Any, source_file: str, ordinal_start: int) -> list[dict[str, Any]]:
        if not isinstance(content, str):
            self.add_issue(
                "INVALID_FILE_CONTENT",
                "BLOCKER",
                "序列文件内容必须为文本",
                field="sequence_files",
                evidence={"source_file": source_file},
            )
            return []
        lines = content.splitlines()
        nonempty = [line.strip() for line in lines if line.strip()]
        if not nonempty:
            self.add_issue(
                "EMPTY_SEQUENCE_FILE",
                "BLOCKER",
                "序列文件没有可解析的内容",
                field="sequence_files",
                evidence={"source_file": source_file},
            )
            return []
        if not any(line.startswith(">") for line in nonempty):
            return [
                {
                    "record_name": f"sequence_{ordinal_start}",
                    "original_record_name": None,
                    "description": "",
                    "sequence_raw": "".join(nonempty),
                    "source_file": source_file,
                    "source_record_ordinal": ordinal_start,
                    "generated_record_name": True,
                }
            ]

        records: list[dict[str, Any]] = []
        record_name: str | None = None
        description = ""
        sequence_parts: list[str] = []

        def flush() -> None:
            nonlocal record_name, description, sequence_parts
            if record_name is None:
                return
            ordinal = ordinal_start + len(records)
            records.append(
                {
                    "record_name": record_name or f"sequence_{ordinal}",
                    "original_record_name": record_name or None,
                    "description": description,
                    "sequence_raw": "".join(sequence_parts),
                    "source_file": source_file,
                    "source_record_ordinal": ordinal,
                    "generated_record_name": not bool(record_name),
                }
            )
            sequence_parts = []

        for line in nonempty:
            if line.startswith(">"):
                flush()
                header = line[1:].strip()
                record_name = header.split(maxsplit=1)[0] if header else ""
                description = header
            elif record_name is None:
                self.add_issue(
                    "INVALID_FASTA_FORMAT",
                    "BLOCKER",
                    "FASTA 首个非空行必须以 > 开头",
                    field="sequence_files",
                    evidence={"source_file": source_file},
                )
                return []
            else:
                sequence_parts.append(line)
        flush()
        return records

    def _collect_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []

        def add_explicit(item: Any, index: int, source_file: str = "inline") -> None:
            if isinstance(item, str):
                records.append(
                    {
                        "record_name": f"sequence_{index}",
                        "original_record_name": None,
                        "description": "",
                        "sequence_raw": item,
                        "source_file": source_file,
                        "source_record_ordinal": index,
                        "generated_record_name": True,
                    }
                )
                return
            if not isinstance(item, dict):
                self.add_issue("INVALID_TEMPLATE_RECORD", "BLOCKER", "模板记录必须为对象或序列文本", field="template_records")
                return
            name = self._as_text(item.get("record_name") or item.get("id") or item.get("name"))
            records.append(
                {
                    "record_name": name or f"sequence_{index}",
                    "original_record_name": name or None,
                    "description": self._as_text(item.get("description")),
                    "sequence_raw": item.get("sequence", ""),
                    "source_file": self._as_text(item.get("source_file") or item.get("source")) or source_file,
                    "source_record_ordinal": index,
                    "generated_record_name": not bool(name),
                }
            )

        manifest = self.payload.get("target_manifest")
        if manifest is not None:
            self.manifest_input_present = True
            manifest_targets = (
                manifest.get("targets") if isinstance(manifest, dict) else manifest
            )
            if not isinstance(manifest_targets, list):
                self.add_issue(
                    "INVALID_TARGET_MANIFEST",
                    "BLOCKER",
                    "target_manifest.targets 必须为数组",
                    field="target_manifest.targets",
                )
            else:
                for index, item in enumerate(manifest_targets, start=1):
                    if not isinstance(item, dict):
                        self.add_issue(
                            "INVALID_TARGET_MANIFEST_ENTRY",
                            "BLOCKER",
                            "靶标清单中的每个靶标必须为对象",
                            field="target_manifest.targets",
                        )
                        continue
                    template = item.get("template")
                    if isinstance(template, str):
                        template = {"sequence": template}
                    elif not isinstance(template, dict):
                        template = {}
                    requested_target_id = self._as_text(item.get("target_id"))
                    record_name = self._as_text(
                        template.get("record_name")
                        or item.get("record_name")
                        or item.get("name")
                        or requested_target_id
                    )
                    override_key = f"__manifest_{index:03d}"
                    records.append(
                        {
                            "record_name": record_name or f"sequence_{index}",
                            "original_record_name": record_name or None,
                            "description": self._as_text(
                                template.get("description")
                                or item.get("description")
                            ),
                            "sequence_raw": template.get(
                                "sequence", item.get("sequence", "")
                            ),
                            "source_file": self._as_text(
                                template.get("source_file")
                                or item.get("source_file")
                            )
                            or "target_manifest",
                            "source_record_ordinal": index,
                            "generated_record_name": not bool(record_name),
                            "requested_target_id": requested_target_id or None,
                            "override_key": override_key,
                        }
                    )
                    parameters = item.get("design_parameters")
                    if parameters is None and isinstance(item.get("parameters"), dict):
                        parameters = item["parameters"].get("user_overrides")
                    self.manifest_overrides[override_key] = {
                        "target_organism": copy.deepcopy(
                            item.get("target_organism", item.get("target_object"))
                        ),
                        "hosts": copy.deepcopy(
                            item.get("hosts", item.get("host_objects", []))
                        ),
                        "related_species": copy.deepcopy(
                            item.get("related_species", item.get("related_objects", []))
                        ),
                        "background_organisms": copy.deepcopy(
                            item.get(
                                "background_organisms",
                                item.get("background_objects", []),
                            )
                        ),
                        "design_parameters": copy.deepcopy(parameters or {}),
                    }

        explicit = self.payload.get("template_records", self.payload.get("sequences"))
        if explicit is not None:
            if not isinstance(explicit, list):
                self.add_issue("INVALID_TEMPLATE_RECORDS", "BLOCKER", "template_records 必须为数组", field="template_records")
            else:
                for item in explicit:
                    add_explicit(item, len(records) + 1)

        if self.payload.get("sequence") is not None:
            add_explicit(
                {
                    "record_name": self.payload.get("target_id") or f"sequence_{len(records) + 1}",
                    "sequence": self.payload.get("sequence"),
                    "source": "legacy_sequence_field",
                },
                len(records) + 1,
            )

        if self.payload.get("fasta_text") is not None:
            records.extend(self._parse_fasta_text(self.payload["fasta_text"], "pasted_fasta", len(records) + 1))

        sequence_files = self.payload.get("sequence_files", [])
        if sequence_files is None:
            sequence_files = []
        if not isinstance(sequence_files, list):
            self.add_issue("INVALID_SEQUENCE_FILES", "BLOCKER", "sequence_files 必须为数组", field="sequence_files")
        else:
            for index, item in enumerate(sequence_files, start=1):
                if not isinstance(item, dict):
                    self.add_issue("INVALID_SEQUENCE_FILE", "BLOCKER", "每个序列文件必须为对象", field="sequence_files")
                    continue
                filename = self._as_text(item.get("file_name") or item.get("name")) or f"sequence_file_{index}.fasta"
                content = item.get("fasta_text", item.get("content"))
                records.extend(self._parse_fasta_text(content, filename, len(records) + 1))

        if not records:
            self.add_issue(
                "MISSING_TEMPLATE",
                "BLOCKER",
                "必须粘贴或上传至少一条模板序列；病原名、基因名和 accession 不能代替模板",
                field="template_records",
                allowed_actions=["ADD_TEMPLATE"],
            )
        return records

    def _normalise_overrides(self) -> dict[str, dict[str, Any]]:
        raw = self.payload.get("target_overrides", {})
        if raw in (None, {}):
            return copy.deepcopy(self.manifest_overrides)
        if isinstance(raw, dict):
            overrides: dict[str, dict[str, Any]] = {}
            for record_name, value in raw.items():
                if not isinstance(value, dict):
                    self.add_issue("INVALID_TARGET_OVERRIDE", "BLOCKER", "靶标覆盖值必须为对象", field="target_overrides")
                    continue
                overrides[str(record_name)] = copy.deepcopy(value)
            return {**copy.deepcopy(self.manifest_overrides), **overrides}
        if isinstance(raw, list):
            overrides = {}
            for value in raw:
                if not isinstance(value, dict):
                    self.add_issue("INVALID_TARGET_OVERRIDE", "BLOCKER", "靶标覆盖值必须为对象", field="target_overrides")
                    continue
                key = self._as_text(value.get("record_name") or value.get("template_record_id"))
                if not key:
                    self.add_issue("MISSING_OVERRIDE_RECORD", "BLOCKER", "靶标覆盖必须指出 record_name", field="target_overrides")
                    continue
                overrides[key] = {key_: item for key_, item in value.items() if key_ not in {"record_name", "template_record_id"}}
            return {**copy.deepcopy(self.manifest_overrides), **overrides}
        self.add_issue("INVALID_TARGET_OVERRIDES", "BLOCKER", "target_overrides 必须为对象或数组", field="target_overrides")
        return copy.deepcopy(self.manifest_overrides)

    def _apply_labelled_intent_defaults(self, defaults: dict[str, Any]) -> dict[str, Any]:
        """Extract only explicit labelled values; never infer biology from prose.

        A full LLM extraction adapter can populate the same canonical fields in
        front of M1.  This deterministic fallback makes the behaviour auditable
        and prevents a free-text sentence from silently changing a target.
        """
        text = self._as_text(self.payload.get("natural_language") or self.payload.get("intent"))
        if not text:
            return defaults
        self.intent_extraction["input_present"] = True
        effective = copy.deepcopy(defaults)
        patterns = {
            "target_organism": r"(?:靶标(?:物种)?|目标(?:物种)?|target(?:\s+(?:organism|species))?)\s*[:：=]\s*([^,，;；\n]+)",
            "hosts": r"(?:宿主|host)\s*[:：=]\s*([^,，;；\n]+)",
            "related_species": r"(?:近缘种|相关物种|related(?:\s+species)?)\s*[:：=]\s*([^,，;；\n]+)",
            "background_organisms": r"(?:背景物种|背景|background(?:\s+organisms?)?)\s*[:：=]\s*([^,，;；\n]+)",
        }
        for field, pattern in patterns.items():
            if effective.get(field) not in (None, "", []):
                continue
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            value = match.group(1).strip()
            if not value:
                continue
            if field in {"hosts", "related_species", "background_organisms"}:
                extracted: Any = [{"name": item.strip()} for item in re.split(r"[、/|]", value) if item.strip()]
            else:
                extracted = {"name": value}
            effective[field] = extracted
            self.intent_extraction["extracted_fields"][field] = extracted
        return effective

    def _validate_parameters(self, raw: Any, *, target_id: str | None, source: str) -> dict[str, Any]:
        if raw in (None, {}):
            return {"source": source, "user_overrides": {}, "effective_user_profile": copy.deepcopy(DEFAULT_PARAMETERS)}
        if not isinstance(raw, dict):
            self.add_issue("INVALID_DESIGN_PARAMETERS", "BLOCKER", "design_parameters 必须为对象", scope="TARGET" if target_id else "BATCH", target_id=target_id, field="design_parameters")
            return {"source": source, "user_overrides": {}, "effective_user_profile": copy.deepcopy(DEFAULT_PARAMETERS)}

        unknown = sorted(set(raw) - NUMERIC_PARAMETERS)
        for name in unknown:
            self.add_issue(
                "UNSUPPORTED_PARAMETER",
                "BLOCKER",
                f"本期不支持设计参数：{name}",
                scope="TARGET" if target_id else "BATCH",
                target_id=target_id,
                field=f"design_parameters.{name}",
            )

        overrides: dict[str, int | float] = {}
        for name in sorted(set(raw) & NUMERIC_PARAMETERS):
            value = raw[name]
            if isinstance(value, bool):
                self.add_issue("INVALID_PARAMETER_VALUE", "BLOCKER", f"{name} 必须为数值", scope="TARGET" if target_id else "BATCH", target_id=target_id, field=f"design_parameters.{name}")
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                self.add_issue("INVALID_PARAMETER_VALUE", "BLOCKER", f"{name} 必须为数值", scope="TARGET" if target_id else "BATCH", target_id=target_id, field=f"design_parameters.{name}")
                continue
            if name in INTEGER_PARAMETERS:
                if not numeric.is_integer():
                    self.add_issue("INVALID_PARAMETER_VALUE", "BLOCKER", f"{name} 必须为整数", scope="TARGET" if target_id else "BATCH", target_id=target_id, field=f"design_parameters.{name}")
                    continue
                overrides[name] = int(numeric)
            else:
                overrides[name] = numeric

        effective = {**DEFAULT_PARAMETERS, **overrides}
        ranges = {
            "product_min_bp": (70, 200), "product_max_bp": (70, 200),
            "primer_min_size": (18, 28), "primer_opt_size": (18, 28), "primer_max_size": (18, 28),
            "primer_min_tm": (50, 65), "primer_opt_tm": (50, 65), "primer_max_tm": (50, 65),
            "primer_min_gc": (0, 100), "primer_max_gc": (0, 100),
            "probe_min_size": (22, 32), "probe_opt_size": (22, 32), "probe_max_size": (22, 32),
            "probe_tm_delta_min": (8, 10), "probe_tm_delta_max": (8, 10),
        }
        for name, (minimum, maximum) in ranges.items():
            if not minimum <= float(effective[name]) <= maximum:
                self.add_issue(
                    "PARAMETER_OUT_OF_HARD_RANGE",
                    "BLOCKER",
                    f"{name} 必须位于 {minimum}-{maximum} 范围内",
                    scope="TARGET" if target_id else "BATCH",
                    target_id=target_id,
                    field=f"design_parameters.{name}",
                    evidence={"value": effective[name], "minimum": minimum, "maximum": maximum},
                )
        for label, fields in {
            "扩增子长度": ["product_min_bp", "product_max_bp"],
            "引物长度": ["primer_min_size", "primer_opt_size", "primer_max_size"],
            "引物Tm": ["primer_min_tm", "primer_opt_tm", "primer_max_tm"],
            "引物GC": ["primer_min_gc", "primer_max_gc"],
            "普通探针长度": ["probe_min_size", "probe_opt_size", "probe_max_size"],
            "探针Tm差": ["probe_tm_delta_min", "probe_tm_delta_max"],
        }.items():
            values = [float(effective[field]) for field in fields]
            if values != sorted(values):
                self.add_issue(
                    "INVALID_PARAMETER_ORDER",
                    "BLOCKER",
                    f"{label} 参数必须从最小值到最优值到最大值递增",
                    scope="TARGET" if target_id else "BATCH",
                    target_id=target_id,
                    field="design_parameters",
                    evidence={field: effective[field] for field in fields},
                )
        return {"source": source, "user_overrides": overrides, "effective_user_profile": effective}

    def _normalise_object(self, raw: Any, *, role: str, target_id: str, ordinal: int) -> dict[str, Any] | None:
        if isinstance(raw, str):
            source = {"name": raw}
        elif isinstance(raw, dict):
            source = copy.deepcopy(raw)
        else:
            self.add_issue("INVALID_SPECIFICITY_OBJECT", "BLOCKER", "特异性对象必须为名称、taxid 或 accession 对象", scope="TARGET", target_id=target_id, field=role)
            return None

        name = self._as_text(source.get("name") or source.get("scientific_name") or source.get("common_name"))
        taxid = self._as_text(source.get("taxid"))
        accession = self._as_text(source.get("accession") or source.get("record_id"))
        if taxid:
            if not taxid.isdigit() or int(taxid) <= 0:
                self.add_issue("INVALID_TAXID", "BLOCKER", "taxid 必须为正整数", scope="TARGET", target_id=target_id, field=role, evidence={"value": taxid})
                return None
            identifier_type = "TAXID"
            identifier = taxid
        elif accession:
            if not ACCESSION_PATTERN.fullmatch(accession):
                self.add_issue("INVALID_ACCESSION", "BLOCKER", "accession/记录 ID 格式不合法", scope="TARGET", target_id=target_id, field=role, evidence={"value": accession})
                return None
            identifier_type = "ACCESSION"
            identifier = accession
        elif name:
            identifier_type = "NAME"
            identifier = name
        else:
            self.add_issue("MISSING_SPECIFICITY_OBJECT", "BLOCKER", "特异性对象缺少名称、taxid 或 accession", scope="TARGET", target_id=target_id, field=role)
            return None

        host_info = None
        target_info = None
        if role == "HOST":
            lowered = (name or taxid).casefold()
            host_info = next((item for item in SUPPORTED_HOSTS.values() if lowered in {alias.casefold() for alias in item["aliases"]}), None)
            if host_info is None:
                continue_without_popup = self.interaction_mode == "NO_POPUPS"
                self.add_issue(
                    "UNSUPPORTED_HOST_DATABASE",
                    "WARNING" if continue_without_popup else "CONFIRMATION",
                    (
                        "未识别该宿主名称；任务继续到 M4 核对实际数据库，无法解析时宿主排除标记为未评估"
                        if continue_without_popup
                        else "未识别该宿主名称；请使用规范物种名或 taxid，实际数据库可用性由 M4 核对"
                    ),
                    scope="TARGET",
                    target_id=target_id,
                    field="hosts",
                    evidence={"input": source},
                    allowed_actions=(
                        ["CONTINUE_WITH_NOT_EVALUATED"]
                        if continue_without_popup
                        else ["EDIT", "REMOVE_TARGET", "CONTINUE_WITH_NOT_EVALUATED"]
                    ),
                )

        if role == "TARGET" and name:
            lowered = name.casefold().strip()
            target_info = next(
                (
                    item
                    for item in KNOWN_TARGET_IDENTITIES.values()
                    if lowered in {alias.casefold() for alias in item["aliases"]}
                ),
                None,
            )

        return {
            "object_id": f"{target_id}:{role.lower()}:{ordinal}",
            "role": role,
            "original_input": source,
            "identifier_type": identifier_type,
            "identifier": identifier,
            "normalized_name": host_info["canonical_name"] if host_info else (name or None),
            "scientific_name": (
                host_info["scientific_name"]
                if host_info
                else target_info["scientific_name"]
                if target_info
                else None
            ),
            "known_accessions": (
                copy.deepcopy(target_info["accessions"]) if target_info else []
            ),
            "taxid": taxid or None,
            "accession": accession or None,
            "resolution_status": "NORMALIZED",
            "resolution_scope": "M1_INPUT_NORMALIZATION",
            "database_support": "SUPPORTED" if role != "HOST" or host_info else "NOT_CONFIGURED",
        }

    @staticmethod
    def _list_value(value: Any) -> list[Any]:
        if value in (None, ""):
            return []
        return value if isinstance(value, list) else [value]

    def _build_target(
        self,
        record: dict[str, Any],
        target_id: str,
        batch_defaults: dict[str, Any],
        override: dict[str, Any],
    ) -> dict[str, Any]:
        effective = copy.deepcopy(batch_defaults)
        effective.update(copy.deepcopy(override))
        sequence, ambiguity, invalid = self._normalise_sequence(record["sequence_raw"])
        validation_status = "VALID"
        if record["generated_record_name"]:
            self.add_issue(
                "MISSING_RECORD_ID",
                "WARNING",
                "模板记录缺少名称，系统已自动生成稳定记录名",
                scope="TARGET",
                target_id=target_id,
                field="template.record_name",
                evidence={"generated_record_name": record["record_name"]},
                allowed_actions=["RENAME_LATER"],
            )
        if not sequence:
            validation_status = "INVALID"
            self.add_issue("MISSING_SEQUENCE", "BLOCKER", "模板记录不含有效序列", scope="TARGET", target_id=target_id, field="template.sequence")
        if invalid:
            validation_status = "INVALID"
            self.add_issue("ILLEGAL_BASES", "BLOCKER", "模板序列含非法碱基", scope="TARGET", target_id=target_id, field="template.sequence", evidence={"invalid_bases": invalid})
        if sequence and len(sequence) < MINIMUM_TEMPLATE_LENGTH:
            validation_status = "INVALID"
            self.add_issue(
                "TEMPLATE_TOO_SHORT",
                "BLOCKER",
                f"模板长度必须不少于 {MINIMUM_TEMPLATE_LENGTH} nt",
                scope="TARGET",
                target_id=target_id,
                field="template.sequence",
                evidence={"length_nt": len(sequence), "minimum_length_nt": MINIMUM_TEMPLATE_LENGTH},
            )
        if ambiguity:
            if validation_status == "VALID":
                validation_status = "AMBIGUOUS"
            self.add_issue(
                "AMBIGUOUS_BASES_REQUIRE_CONFIRMATION",
                "CONFIRMATION",
                "模板含 IUPAC 歧义碱基；M1 已保留原序列，运行策略待确认",
                scope="TARGET",
                target_id=target_id,
                field="template.sequence",
                evidence={"ambiguous_bases": ambiguity},
                allowed_actions=["EDIT", "REMOVE_TARGET", "CONFIRM_AMBIGUITY_POLICY"],
            )

        target_raw = effective.get("target_organism", effective.get("target_species"))
        target_object = self._normalise_object(target_raw, role="TARGET", target_id=target_id, ordinal=1) if target_raw not in (None, "") else None
        if target_object is None:
            self.add_issue(
                "MISSING_TARGET_ORGANISM",
                "WARNING",
                "未提供靶标物种；仍可运行 Primer3 和规则评估，靶标命中特异性将标记为未评估",
                scope="TARGET",
                target_id=target_id,
                field="target_organism",
                allowed_actions=["ADD_TARGET_ORGANISM_LATER"],
            )

        hosts_raw = effective.get("hosts", effective.get("host", effective.get("host_species")))
        hosts = [
            item for index, raw in enumerate(self._list_value(hosts_raw), start=1)
            if (item := self._normalise_object(raw, role="HOST", target_id=target_id, ordinal=index)) is not None
        ]
        related_raw = self._list_value(effective.get("related_species", effective.get("related")))
        background_raw = self._list_value(effective.get("background_organisms", effective.get("background")))
        related = [
            item for index, raw in enumerate(related_raw, start=1)
            if (item := self._normalise_object(raw, role="RELATED", target_id=target_id, ordinal=index)) is not None
        ]
        background = [
            item for index, raw in enumerate(background_raw, start=1)
            if (item := self._normalise_object(raw, role="BACKGROUND", target_id=target_id, ordinal=index)) is not None
        ]

        parameter_source = "target_override" if "design_parameters" in override else "batch_default"
        parameters = self._validate_parameters(effective.get("design_parameters"), target_id=target_id, source=parameter_source)
        # A target is the only organism context required to start specificity.
        # Host is an optional refinement: when supplied M4 adds a dedicated
        # host-genome exclusion search; otherwise the required target and broad
        # background searches still form a complete applicable evaluation.
        specificity_input_complete = target_object is not None
        if target_object is None:
            specificity_status = "NOT_EVALUATED_MISSING_TARGET_CONTEXT"
        elif hosts:
            specificity_status = "READY_FOR_TARGET_BACKGROUND_AND_HOST_EVALUATION"
        else:
            specificity_status = "READY_FOR_TARGET_AND_BACKGROUND_EVALUATION"
        return {
            "target_id": target_id,
            "ordinal": record["source_record_ordinal"],
            "status": "PENDING_CONFIRMATION",
            "template": {
                "template_id": f"{target_id}:template",
                "record_name": record["record_name"],
                "original_record_name": record["original_record_name"],
                "description": record["description"],
                "sequence": sequence,
                "length_nt": len(sequence),
                "source_file": record["source_file"],
                "source_record_ordinal": record["source_record_ordinal"],
                "validation_status": validation_status,
                "checksum_sha256": hashlib.sha256(sequence.encode("ascii", errors="ignore")).hexdigest(),
            },
            "target_object": target_object,
            "host_objects": hosts,
            "related_objects": related,
            "background_objects": background,
            "primer3_execution_eligible": validation_status == "VALID",
            "specificity_input_complete": specificity_input_complete,
            "specificity_status": specificity_status,
            "host_exclusion_requested": bool(hosts),
            "specificity_missing_fields": [
                field
                for field, missing in (
                    ("target_organism", target_object is None),
                )
                if missing
            ],
            "parameters": parameters,
            "exploration_manifest": [],
            "candidate_counts": {"generated": 0, "hard_failed": 0, "specificity_failed": 0, "qualified": 0},
            "search_complete": False,
        }

    def _check_scope_requests(self) -> None:
        for field, capability in OUT_OF_SCOPE_REQUESTS.items():
            if self.payload.get(field):
                self.add_issue(
                    "OUT_OF_SCOPE_CAPABILITY",
                    "BLOCKER",
                    f"本期 WF-01 不支持 {capability}",
                    field=field,
                    evidence={"capability": capability},
                    allowed_actions=["REMOVE_REQUEST"],
                )
        probe_type = self._as_text(self.payload.get("probe_type")).upper()
        if probe_type in {"MGB", "TAQMAN_MGB"}:
            self.add_issue("OUT_OF_SCOPE_CAPABILITY", "BLOCKER", "本期 WF-01 不支持 MGB 探针", field="probe_type", evidence={"capability": "MGB"}, allowed_actions=["SET_ORDINARY_TAQMAN"])
        if "top_candidates" in self.payload and self.payload.get("top_candidates") != FIXED_TOP_CANDIDATES:
            self.add_issue(
                "TOP_CANDIDATES_IS_SYSTEM_FIXED",
                "BLOCKER",
                "本期每个靶标固定推荐 Top10，不能由请求改变",
                field="top_candidates",
                evidence={"requested": self.payload.get("top_candidates"), "fixed": FIXED_TOP_CANDIDATES},
                allowed_actions=["REMOVE_FIELD"],
            )

    def run(self) -> dict[str, Any]:
        self._check_scope_requests()
        records = self._collect_records()
        overrides = self._normalise_overrides()
        sequence_mode = self._as_text(
            self.payload.get("sequence_mode")
            or ("independent_targets" if self.manifest_input_present else "alignment_target")
        ).lower()
        if self.manifest_input_present and sequence_mode != "independent_targets":
            self.add_issue(
                "TARGET_MANIFEST_REQUIRES_INDEPENDENT_MODE",
                "BLOCKER",
                "target_manifest 中的靶标必须使用 independent_targets 模式",
                field="sequence_mode",
                allowed_actions=["SET_INDEPENDENT_TARGETS"],
            )
        if sequence_mode not in {"alignment_target", "independent_targets"}:
            self.add_issue(
                "INVALID_SEQUENCE_MODE",
                "BLOCKER",
                "sequence_mode 仅支持 alignment_target 或 independent_targets",
                field="sequence_mode",
                evidence={"value": self.payload.get("sequence_mode")},
                allowed_actions=["SET_ALIGNMENT_TARGET", "SET_INDEPENDENT_TARGETS"],
            )
            sequence_mode = "alignment_target"
        batch_defaults = self.payload.get("batch_defaults", {})
        if batch_defaults is None:
            batch_defaults = {}
        if not isinstance(batch_defaults, dict):
            self.add_issue("INVALID_BATCH_DEFAULTS", "BLOCKER", "batch_defaults 必须为对象", field="batch_defaults")
            batch_defaults = {}
        batch_defaults = self._apply_labelled_intent_defaults(batch_defaults)

        record_names = [record["record_name"] for record in records]
        duplicate_names = sorted({name for name in record_names if record_names.count(name) > 1})
        if duplicate_names:
            self.add_issue(
                "DUPLICATE_RECORD_ID",
                "BLOCKER",
                "模板记录名重复，无法安全映射靶标级覆盖",
                field="template_records",
                evidence={"duplicate_record_names": duplicate_names},
                allowed_actions=["RENAME", "REMOVE_TARGET"],
            )
        for key in sorted(
            key
            for key in set(overrides) - set(record_names)
            if not key.startswith("__manifest_")
        ):
            self.add_issue(
                "UNKNOWN_TARGET_OVERRIDE",
                "BLOCKER",
                "靶标覆盖引用了不存在的模板记录",
                field="target_overrides",
                evidence={"record_name": key},
            )

        if sequence_mode == "alignment_target" and len(records) > 1 and overrides:
            self.add_issue(
                "ALIGNMENT_TARGET_OVERRIDE_NOT_SUPPORTED",
                "BLOCKER",
                "多序列输入会作为同一靶标进行 MAFFT 比对，不能再按 FASTA 记录分别覆盖靶标参数",
                field="target_overrides",
                evidence={"record_names": sorted(overrides)},
                allowed_actions=["REMOVE_TARGET_OVERRIDES", "SUBMIT_SEPARATE_TASKS"],
            )

        targets: list[dict[str, Any]] = []
        if sequence_mode == "independent_targets":
            requested_target_ids = [
                self._as_text(record.get("requested_target_id"))
                for record in records
                if self._as_text(record.get("requested_target_id"))
            ]
            duplicate_target_ids = sorted(
                {
                    target_id
                    for target_id in requested_target_ids
                    if requested_target_ids.count(target_id) > 1
                }
            )
            if duplicate_target_ids:
                self.add_issue(
                    "DUPLICATE_TARGET_ID",
                    "BLOCKER",
                    "靶标清单中的 target_id 必须唯一",
                    field="target_manifest.targets",
                    evidence={"duplicate_target_ids": duplicate_target_ids},
                    allowed_actions=["RENAME", "REMOVE_TARGET"],
                )
            targets = [
                self._build_target(
                    record,
                    (
                        self._as_text(record.get("requested_target_id"))
                        if self._as_text(record.get("requested_target_id"))
                        and self._as_text(record.get("requested_target_id"))
                        not in duplicate_target_ids
                        else f"target_{index:03d}"
                    ),
                    batch_defaults,
                    overrides.get(
                        record.get("override_key"),
                        overrides.get(record["record_name"], {}),
                    ),
                )
                for index, record in enumerate(records, start=1)
            ]
            for target in targets:
                template = copy.deepcopy(target["template"])
                template["role"] = "REFERENCE"
                target["template"] = template
                target["templates"] = [copy.deepcopy(template)]
                target["template_count"] = 1
                target["alignment_required"] = False
        else:
            target_drafts = [
                self._build_target(
                    record,
                    "target_001",
                    batch_defaults,
                    overrides.get(record["record_name"], {}) if len(records) == 1 else {},
                )
                for record in records
            ]
            if target_drafts:
                target = target_drafts[0]
                templates = []
                for index, draft in enumerate(target_drafts, start=1):
                    template = copy.deepcopy(draft["template"])
                    template["template_id"] = f"target_001:template:{index:03d}"
                    template["role"] = "REFERENCE" if index == 1 else "ALIGNMENT_MEMBER"
                    templates.append(template)
                target["template"] = copy.deepcopy(templates[0])
                target["templates"] = templates
                target["template_count"] = len(templates)
                target["alignment_required"] = len(templates) > 1
                targets = [target]
        target_issues = {target["target_id"]: [] for target in targets}
        for issue in self.issues:
            if issue["target_id"] in target_issues:
                target_issues[issue["target_id"]].append(issue)
        for target in targets:
            target["issues"] = target_issues[target["target_id"]]
            blocking_issues = [
                issue
                for issue in target["issues"]
                if issue["code"] in PRIMER3_BLOCKING_ISSUE_CODES
            ]
            confirmation_issues = [
                issue
                for issue in target["issues"]
                if issue["severity"] in {"BLOCKER", "CONFIRMATION"}
                and issue not in blocking_issues
            ]
            if blocking_issues:
                manifest_status = TargetManifestStatus.BLOCKED
            elif confirmation_issues:
                manifest_status = TargetManifestStatus.NEEDS_CONFIRMATION
            elif target.get("specificity_input_complete"):
                manifest_status = TargetManifestStatus.READY_FULL
            else:
                manifest_status = TargetManifestStatus.READY_DESIGN_ONLY
            target["manifest_status"] = manifest_status.value
            target["execution_path"] = (
                TargetExecutionPath.FULL_SPECIFICITY.value
                if manifest_status == TargetManifestStatus.READY_FULL
                else TargetExecutionPath.DESIGN_ONLY.value
                if manifest_status == TargetManifestStatus.READY_DESIGN_ONLY
                else TargetExecutionPath.NOT_EXECUTABLE.value
            )
            target["can_execute_now"] = manifest_status in {
                TargetManifestStatus.READY_DESIGN_ONLY,
                TargetManifestStatus.READY_FULL,
            }
            target["requires_user_action"] = manifest_status in {
                TargetManifestStatus.NEEDS_CONFIRMATION,
                TargetManifestStatus.BLOCKED,
            }
            target["blocking_issue_codes"] = [
                issue["code"] for issue in blocking_issues
            ]
            target["confirmation_issue_codes"] = [
                issue["code"] for issue in confirmation_issues
            ]
            if target["can_execute_now"]:
                target["status"] = "QUEUED"

        can_auto_run = bool(targets) and not any(issue["severity"] in {"BLOCKER", "CONFIRMATION"} for issue in self.issues)
        status = "READY_TO_QUEUE" if can_auto_run else "PENDING_CONFIRMATION"
        issue_counts = {severity: sum(issue["severity"] == severity for issue in self.issues) for severity in ("BLOCKER", "CONFIRMATION", "WARNING")}
        normalised_input = {
            "task_name": self._as_text(self.payload.get("task_name") or self.payload.get("assay_id")) or "wf01_batch",
            "intent": self._as_text(self.payload.get("intent")) or "ordinary_taqman_conserved_target",
            "sequence_mode": sequence_mode,
            "batch_defaults": batch_defaults,
            "fixed_top_candidates": FIXED_TOP_CANDIDATES,
        }
        fingerprint = hashlib.sha256(json.dumps({"input": normalised_input, "targets": targets}, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        manifest_counts = {
            status.value: sum(
                target.get("manifest_status") == status.value
                for target in targets
            )
            for status in TargetManifestStatus
        }
        actionable_batch_issues = [
            issue
            for issue in self.issues
            if issue.get("scope") == "BATCH"
            and issue.get("severity") in {"BLOCKER", "CONFIRMATION"}
        ]
        target_manifest = {
            "schema_version": "wf01-target-manifest-v1",
            "target_total": len(targets),
            "counts": manifest_counts,
            "ready_target_ids": [
                target["target_id"]
                for target in targets
                if target.get("can_execute_now")
            ],
            "problem_target_ids": [
                target["target_id"]
                for target in targets
                if target.get("requires_user_action")
            ],
            "batch_requires_user_action": bool(actionable_batch_issues),
            "batch_issues": actionable_batch_issues,
            "targets": targets,
        }
        return {
            "api_version": "wf01-prd-v0.2-m1",
            "prd_version": PRD_VERSION,
            "preflight_status": status,
            "can_auto_run": can_auto_run,
            "normalised_input": normalised_input,
            "intent_extraction": self.intent_extraction,
            "fixed_top_candidates": FIXED_TOP_CANDIDATES,
            "parameter_profiles": copy.deepcopy(PARAMETER_PROFILES),
            "targets": targets,
            "target_manifest": target_manifest,
            "issues": self.issues,
            "issue_counts": issue_counts,
            "primer3_execution_eligible": bool(targets)
            and all(target.get("primer3_execution_eligible") for target in targets),
            "specificity_plan": {
                "mode": (
                    "FULL_SPECIFICITY_EVALUATION"
                    if targets
                    and all(target.get("specificity_input_complete") for target in targets)
                    else "NOT_EVALUATED_MISSING_TARGET_CONTEXT"
                ),
                "required_modes": ["TARGET_HIT", "RELATED_BACKGROUND_NT"],
                "optional_modes": ["HOST_EXCLUSION"],
                "host_exclusion_target_ids": [
                    target["target_id"]
                    for target in targets
                    if target.get("host_objects")
                ],
                "missing_context_target_ids": [
                    target["target_id"]
                    for target in targets
                    if not target.get("specificity_input_complete")
                ],
            },
            "input_fingerprint": fingerprint,
            "next_action": "CREATE_BATCH" if can_auto_run else "RESOLVE_ISSUES",
        }


class WF01V02BatchService:
    """M1-M4 batch orchestration for the v0.2 engine."""

    def __init__(
        self,
        state_root: str | Path,
        config: dict[str, Any] | None = None,
        catalog: DatabaseCatalog | None = None,
        artifact_publisher: RustFSArtifactPublisher | None = None,
    ) -> None:
        self.store = WF01V02BatchStore(state_root)
        self.config = copy.deepcopy(config or {})
        self.catalog = catalog or DatabaseCatalog(self.config)
        self.artifact_publisher = (
            artifact_publisher
            if artifact_publisher is not None
            else RustFSArtifactPublisher.from_environment()
        )

    def _record_scientific_artifact(
        self,
        batch: dict[str, Any],
        *,
        stage: str,
        tools: list[str],
        raw_data: Any,
        target_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist raw evidence and attach its bounded manifest to the batch."""
        scope = target_id or "batch"
        safe_scope = re.sub(r"[^a-z0-9._-]+", "-", scope.lower()).strip("-.")
        safe_scope = safe_scope[:80] or hashlib.sha256(
            scope.encode("utf-8")
        ).hexdigest()[:16]
        raw_fingerprint = hashlib.sha256(
            json.dumps(
                raw_data,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        artifact_id = f"{stage.lower()}-{safe_scope}-{raw_fingerprint[:12]}"
        existing = next(
            (
                copy.deepcopy(item)
                for item in batch.get("scientific_artifacts") or []
                if item.get("artifactId") == artifact_id
            ),
            None,
        )
        if existing is not None:
            return existing
        entry = self.store.save_scientific_artifact(
            str(batch["batch_id"]),
            artifact_id=artifact_id,
            stage=stage,
            tools=tools,
            raw_data=raw_data,
            target_id=target_id,
        )
        artifacts = [
            item
            for item in batch.get("scientific_artifacts") or []
            if item.get("artifactId") != artifact_id
        ]
        artifacts.append(entry)
        artifacts.sort(
            key=lambda item: (
                str(item.get("stage") or ""),
                str(item.get("targetId") or ""),
            )
        )
        batch["scientific_artifacts"] = artifacts
        return entry

    @staticmethod
    def _strip_download_only_scientific_raw(
        execution: dict[str, Any],
        *,
        stage: str,
    ) -> None:
        """Keep large native-tool payloads in artifacts, not normal task state."""
        if stage == "M2":
            alignment = execution.get("alignment")
            if isinstance(alignment, dict):
                alignment.pop("raw_tool_output", None)
            for unit in execution.get("exploration_units") or []:
                if isinstance(unit, dict):
                    unit.pop("primer3_raw_output", None)
        elif stage == "M4":
            execution.pop("raw_search_outputs", None)

    def preflight(
        self,
        payload: dict[str, Any],
        *,
        interaction_mode: str = "LEGACY",
        require_full_route: bool = False,
    ) -> dict[str, Any]:
        result = WF01V02Preflight(
            payload,
            interaction_mode=interaction_mode,
        ).run()
        if require_full_route:
            self._assert_full_route_context(result)
        return result

    @staticmethod
    def _target_counts(targets: list[dict[str, Any]]) -> dict[str, int]:
        """Return intake counts while preserving legacy executor counters."""
        return {
            "queued": sum(target.get("status") == "QUEUED" for target in targets),
            "pending_confirmation": sum(
                target.get("status") != "QUEUED" for target in targets
            ),
            "ready_design_only": sum(
                target.get("manifest_status") == "READY_DESIGN_ONLY"
                for target in targets
            ),
            "ready_full": sum(
                target.get("manifest_status") == "READY_FULL"
                for target in targets
            ),
            "needs_confirmation": sum(
                target.get("manifest_status") == "NEEDS_CONFIRMATION"
                for target in targets
            ),
            "blocked": sum(
                target.get("manifest_status") == "BLOCKED" for target in targets
            ),
        }

    @staticmethod
    def _manifest_payload_from_targets(
        targets: list[dict[str, Any]],
        *,
        task_name: str,
    ) -> dict[str, Any]:
        from .wf01_v02_m7 import target_input_model

        manifest_targets: list[dict[str, Any]] = []
        for target in targets:
            model = target_input_model(target)
            manifest_targets.append(
                {
                    "target_id": target.get("target_id"),
                    **model,
                }
            )
        return {
            "task_name": task_name,
            "sequence_mode": "independent_targets",
            "target_manifest": {
                "schema_version": "wf01-target-manifest-v1",
                "targets": manifest_targets,
            },
        }

    @staticmethod
    def _apply_manifest_patch(
        model: dict[str, Any],
        changes: dict[str, Any],
        *,
        target_id: str,
    ) -> dict[str, Any]:
        allowed = {
            "template",
            "sequence",
            "record_name",
            "description",
            "source_file",
            "target_organism",
            "target_object",
            "hosts",
            "host_objects",
            "related_species",
            "related_objects",
            "background_organisms",
            "background_objects",
            "design_parameters",
        }
        unknown = sorted(set(changes) - allowed)
        if unknown:
            raise InputValidationError(
                "靶标修改包含不支持的字段",
                details={"target_id": target_id, "unknown_fields": unknown},
            )
        revised = copy.deepcopy(model)
        template_changes = changes.get("template")
        if template_changes is not None:
            if not isinstance(template_changes, dict):
                raise InputValidationError(
                    "template 修改必须为对象",
                    details={"target_id": target_id},
                )
            revised["template"].update(copy.deepcopy(template_changes))
        for field in ("sequence", "record_name", "description", "source_file"):
            if field in changes:
                revised["template"][field] = copy.deepcopy(changes[field])
        aliases = {
            "target_object": "target_organism",
            "host_objects": "hosts",
            "related_objects": "related_species",
            "background_objects": "background_organisms",
        }
        for field in (
            "target_organism",
            "hosts",
            "related_species",
            "background_organisms",
            "design_parameters",
        ):
            source_field = field
            if field not in changes:
                source_field = next(
                    (
                        alias
                        for alias, canonical in aliases.items()
                        if canonical == field and alias in changes
                    ),
                    field,
                )
            if source_field in changes:
                revised[field] = copy.deepcopy(changes[source_field])
        return revised

    @staticmethod
    def _export_filename(
        report: dict[str, Any], format_name: str
    ) -> str:
        task_name = str(
            (report.get("batch_overview") or {}).get("task_name")
            or "WF01"
        ).strip()
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", task_name)
        safe_name = re.sub(r"\s+", " ", safe_name).strip(" ._") or "WF01"
        safe_name = safe_name[:100].rstrip(" ._") or "WF01"
        suffixes = {
            "html": "引物探针设计报告.html",
            "process_html": "科学过程详情报告.html",
            "rules_html": "21条规则验证报告.html",
            "csv": "引物探针候选结果.csv",
            "pdf": "引物探针设计报告.pdf",
            "scientific_data": "科学工具原始数据.zip",
        }
        selected = str(format_name).lower()
        if selected not in suffixes:
            raise InputValidationError(
                "不支持的 M6 导出格式",
                details={"format": format_name, "supported": sorted(suffixes)},
            )
        if selected in {
            "html",
            "process_html",
            "rules_html",
            "pdf",
            "scientific_data",
        }:
            generated_at = str(report.get("generated_at") or "")
            try:
                generated_time = datetime.fromisoformat(
                    generated_at.replace("Z", "+00:00")
                )
                if generated_time.tzinfo is None:
                    generated_time = generated_time.replace(tzinfo=timezone.utc)
                china_time = generated_time.astimezone(
                    timezone(timedelta(hours=8))
                )
                time_prefix = china_time.strftime("%Y-%m-%d_%H-%M-%S")
            except ValueError:
                time_prefix = "生成时间未知"
            return f"{time_prefix}_{suffixes[selected]}"
        return f"{safe_name}_{suffixes[selected]}"

    @staticmethod
    def _export_metadata(
        batch_id: str,
        format_name: str,
        path: Path,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "format": format_name,
            "filename": path.name,
            "media_type": value["media_type"],
            "size_bytes": value["size_bytes"],
            "sha256": value["sha256"],
            "canonical_report_id": value["canonical_report_id"],
            "canonical_content_sha256": value[
                "canonical_content_sha256"
            ],
            "download_endpoint": f"/data/{path.name}",
            "file_url": f"/data/{path.name}",
            "preview_endpoint": (
                f"/data/{path.name}"
                if format_name in {"html", "process_html", "rules_html"}
                else None
            ),
        }

    def _publish_export_metadata(
        self,
        batch_id: str,
        path: Path,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Publish one local export and attach non-secret RustFS provenance."""
        publication = self.artifact_publisher.publish(
            path,
            batch_id=batch_id,
            media_type=str(metadata["media_type"]),
            sha256=str(metadata["sha256"]),
        )
        if publication is None:
            return metadata
        published = copy.deepcopy(metadata)
        published["object_storage"] = publication
        published["file_url"] = publication["file_url"]
        published["download_endpoint"] = publication["file_url"]
        if published.get("format") in {
            "html",
            "process_html",
            "rules_html",
        }:
            published["preview_endpoint"] = publication["file_url"]
        return published

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _assert_no_pending_user_interactions(
        batch: dict[str, Any],
        *,
        requested_stage: str,
    ) -> None:
        """Prevent any scientific stage from crossing a user-turn boundary."""
        pending_ids = [
            str((item.get("data") or {}).get("interactionId"))
            for item in batch.get("agent_interactions") or []
            if (item.get("data") or {}).get("status")
            in {"WAITING_USER", "USER_SUBMITTED", "RUNNING"}
        ]
        if pending_ids:
            raise InputValidationError(
                "批次仍有待处理交互，正在等待用户回复，当前轮次必须停止；"
                "只有 QUEUED 且无待处理交互时才能继续执行后续阶段",
                details={
                    "batch_id": batch.get("batch_id"),
                    "requested_stage": requested_stage,
                    "interaction_ids": pending_ids,
                    "next_action": "WAIT_FOR_USER",
                },
            )

    @staticmethod
    def _assert_full_route_context(preflight: dict[str, Any]) -> None:
        """Reject a public create before a DESIGN_ONLY batch is persisted."""
        targets = preflight.get("targets") or []
        invalid_targets = [
            {
                "target_id": target.get("target_id"),
                "manifest_status": target.get("manifest_status"),
                "execution_path": target.get("execution_path"),
            }
            for target in targets
            if target.get("specificity_input_complete") is not True
        ]
        if not targets or invalid_targets:
            raise InputValidationError(
                "新建 WF-01 任务缺少完整路线必填上下文，未创建批次",
                details={
                    "required_execution_path": "FULL_SPECIFICITY",
                    "design_only_fallback_allowed": False,
                    "invalid_targets": invalid_targets,
                },
            )

    def create_batch(
        self,
        payload: dict[str, Any],
        *,
        interaction_mode: str = "LEGACY",
        require_full_route: bool = False,
    ) -> dict[str, Any]:
        if interaction_mode not in {"LEGACY", "LEGACY_UPLOAD", "NO_POPUPS"}:
            raise InputValidationError(
                "interaction_mode 不受支持",
                details={"interaction_mode": interaction_mode},
            )
        preflight = self.preflight(
            payload,
            interaction_mode=interaction_mode,
            require_full_route=require_full_route,
        )
        if interaction_mode == "NO_POPUPS" and not preflight["can_auto_run"]:
            blocking_issues = [
                {
                    "code": issue.get("code"),
                    "field": issue.get("field"),
                    "message": issue.get("message"),
                    "evidence": copy.deepcopy(issue.get("evidence") or {}),
                }
                for issue in preflight.get("issues") or []
                if issue.get("severity") in {"BLOCKER", "CONFIRMATION"}
            ]
            raise InputValidationError(
                "模板序列或设计参数不合格，未启动 Primer3",
                details={
                    "issues": blocking_issues,
                    "minimum_template_length_nt": MINIMUM_TEMPLATE_LENGTH,
                    "retry_with_create_wf01_batch": True,
                    "show_popup": False,
                },
            )
        batch_id = str(uuid4())
        status = "QUEUED" if preflight["can_auto_run"] else "PENDING_CONFIRMATION"
        batch = {
            "batch_id": batch_id,
            "task_version": 1,
            "source_batch_id": None,
            "source_version": None,
            "lineage_root_batch_id": batch_id,
            "workflow": "WF-01",
            "prd_version": PRD_VERSION,
            "interaction_mode": interaction_mode,
            "status": status,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "preflight": preflight,
            "target_manifest": copy.deepcopy(preflight["target_manifest"]),
            "targets": preflight["targets"],
            "target_total": len(preflight["targets"]),
            "target_counts": self._target_counts(preflight["targets"]),
            "events": [{"event_seq": 1, "event_type": "BATCH_CREATED", "created_at": utc_now(), "status": status}],
            "agent_interactions": [],
            "scientific_artifacts": [],
            "report_id": None,
            "search_complete": False,
        }
        if require_full_route:
            batch["required_execution_path"] = "FULL_SPECIFICITY"
        if interaction_mode != "NO_POPUPS":
            for issue in preflight["issues"]:
                if issue.get("severity") not in {"BLOCKER", "CONFIRMATION"}:
                    continue
                self._append_agent_interaction(
                    batch,
                    build_issue_interaction(batch_id, issue),
                )
        self._record_scientific_artifact(
            batch,
            stage="M1",
            tools=["WF01_PREFLIGHT"],
            raw_data=preflight,
        )
        self.store.save(batch)
        return batch

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        return self.store.get(batch_id)

    def get_interactions(self, batch_id: str) -> dict[str, Any]:
        """Return the UI interactions persisted for a WF-01 batch."""
        batch = self.store.get(batch_id)
        batch_id = str(batch["batch_id"])
        return {
            "batch_id": batch_id,
            "agent_interactions": copy.deepcopy(
                batch.get("agent_interactions") or []
            ),
        }

    def revise_target_manifest(
        self,
        batch_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Edit/remove target drafts, re-run M1, and atomically persist the list."""
        if not isinstance(payload, dict):
            raise InputValidationError("靶标清单修改请求必须为对象")
        raw_changes = payload.get("target_changes", payload.get("changes", []))
        remove_target_ids = payload.get(
            "remove_target_ids", payload.get("remove_targets", [])
        )
        if raw_changes is None:
            raw_changes = []
        if remove_target_ids is None:
            remove_target_ids = []
        if not isinstance(raw_changes, list) or not isinstance(remove_target_ids, list):
            raise InputValidationError(
                "target_changes 和 remove_target_ids 必须为数组"
            )
        if not raw_changes and not remove_target_ids:
            raise InputValidationError("至少需要修改或移除一个靶标")
        removed = [str(value).strip() for value in remove_target_ids]
        if any(not value for value in removed) or len(set(removed)) != len(removed):
            raise InputValidationError("remove_target_ids 必须是非空且不重复的靶标 ID")

        batch = self.store.get(batch_id)
        batch_id = str(batch["batch_id"])
        if batch.get("m2_outcome") is not None or batch.get("status") not in {
            "QUEUED",
            "PENDING_CONFIRMATION",
        }:
            raise InputValidationError(
                "只有尚未进入 M2 的批次可以修改靶标清单",
                details={"batch_id": batch_id, "status": batch.get("status")},
            )
        targets = batch.get("targets") or []
        source_by_id = {str(target.get("target_id")): target for target in targets}
        unknown_removed = sorted(set(removed) - set(source_by_id))
        if unknown_removed:
            raise InputValidationError(
                "移除请求引用了不存在的靶标",
                details={"target_ids": unknown_removed},
            )

        changes_by_id: dict[str, dict[str, Any]] = {}
        for item in raw_changes:
            if not isinstance(item, dict):
                raise InputValidationError("每个靶标修改项必须为对象")
            target_id = str(item.get("target_id") or "").strip()
            if not target_id:
                raise InputValidationError("靶标修改项缺少 target_id")
            if target_id in changes_by_id:
                raise InputValidationError(
                    "同一靶标只能提交一次修改",
                    details={"target_id": target_id},
                )
            if target_id not in source_by_id:
                raise InputValidationError(
                    "靶标修改引用了不存在的靶标",
                    details={"target_id": target_id},
                )
            if target_id in removed:
                raise InputValidationError(
                    "同一靶标不能同时修改和移除",
                    details={"target_id": target_id},
                )
            changes = item.get("changes")
            if changes is None:
                changes = {
                    key: copy.deepcopy(value)
                    for key, value in item.items()
                    if key != "target_id"
                }
            if not isinstance(changes, dict) or not changes:
                raise InputValidationError(
                    "靶标修改项没有可应用的字段",
                    details={"target_id": target_id},
                )
            changes_by_id[target_id] = changes

        from .wf01_v02_m7 import target_input_model

        manifest_targets: list[dict[str, Any]] = []
        for target in targets:
            target_id = str(target.get("target_id"))
            if target_id in removed:
                continue
            model = target_input_model(target)
            if target_id in changes_by_id:
                model = self._apply_manifest_patch(
                    model,
                    changes_by_id[target_id],
                    target_id=target_id,
                )
            manifest_targets.append({"target_id": target_id, **model})
        if not manifest_targets:
            raise InputValidationError("不能移除批次中的全部靶标")

        task_name = str(
            ((batch.get("preflight") or {}).get("normalised_input") or {}).get(
                "task_name"
            )
            or "wf01_batch"
        )
        preflight = self.preflight(
            {
                "task_name": task_name,
                "sequence_mode": "independent_targets",
                "target_manifest": {
                    "schema_version": "wf01-target-manifest-v1",
                    "targets": manifest_targets,
                },
            }
        )
        if batch.get("required_execution_path") == "FULL_SPECIFICITY":
            self._assert_full_route_context(preflight)
        submitted_at = utc_now()
        batch["preflight"] = preflight
        batch["target_manifest"] = copy.deepcopy(preflight["target_manifest"])
        batch["targets"] = preflight["targets"]
        batch["target_total"] = len(preflight["targets"])
        batch["target_counts"] = self._target_counts(preflight["targets"])
        batch["status"] = (
            "QUEUED" if preflight["can_auto_run"] else "PENDING_CONFIRMATION"
        )
        batch["agent_interactions"] = []
        for issue in preflight["issues"]:
            if issue.get("severity") in {"BLOCKER", "CONFIRMATION"}:
                self._append_agent_interaction(
                    batch,
                    build_issue_interaction(batch_id, issue),
                )
        batch["updated_at"] = submitted_at
        self._append_event(
            batch,
            "TARGET_MANIFEST_REVISED",
            status=batch["status"],
            changed_target_ids=sorted(changes_by_id),
            removed_target_ids=removed,
            target_total=batch["target_total"],
        )
        self._record_scientific_artifact(
            batch,
            stage="M1",
            tools=["WF01_TARGET_MANIFEST", "WF01_PREFLIGHT"],
            raw_data=preflight,
        )
        self.store.save(batch)
        return batch

    def confirm_target_manifest(self, batch_id: str) -> dict[str, Any]:
        """Start a ready manifest or return its unresolved per-target form."""
        batch = self.store.get(batch_id)
        batch_id = str(batch["batch_id"])
        if batch.get("m2_outcome") is not None or batch.get("status") not in {
            "QUEUED",
            "PENDING_CONFIRMATION",
        }:
            raise InputValidationError(
                "当前批次已进入执行阶段，不能再次确认靶标清单",
                details={"batch_id": batch_id, "status": batch.get("status")},
            )
        problem_targets = [
            target
            for target in batch.get("targets") or []
            if target.get("manifest_status")
            in {"NEEDS_CONFIRMATION", "BLOCKED"}
        ]
        batch_issues = [
            issue
            for issue in (batch.get("preflight") or {}).get("issues") or []
            if issue.get("scope") == "BATCH"
            and issue.get("severity") in {"BLOCKER", "CONFIRMATION"}
        ]
        if problem_targets or batch_issues:
            batch["status"] = "PENDING_CONFIRMATION"
            batch["updated_at"] = utc_now()
            self.store.save(batch)
            return {
                "batch_id": batch_id,
                "status": batch["status"],
                "started": False,
                "next_action": "RESOLVE_TARGET_MANIFEST",
                "problem_target_ids": [
                    target.get("target_id") for target in problem_targets
                ],
                "batch_issues": copy.deepcopy(batch_issues),
                "target_manifest": copy.deepcopy(batch.get("target_manifest")),
                "batch": batch,
            }
        if not batch.get("targets"):
            raise InputValidationError("靶标清单为空，不能开始批量任务")
        confirmed_at = utc_now()
        batch["status"] = "QUEUED"
        batch["manifest_confirmed_at"] = confirmed_at
        batch["updated_at"] = confirmed_at
        self._append_event(
            batch,
            "TARGET_MANIFEST_CONFIRMED",
            status="QUEUED",
            target_total=len(batch.get("targets") or []),
        )
        self.store.save(batch)
        return {
            "batch_id": batch_id,
            "status": "QUEUED",
            "started": True,
            "next_action": "RUN_M2",
            "target_manifest": copy.deepcopy(batch.get("target_manifest")),
            "batch": batch,
        }

    def submit_interaction_response(
        self,
        batch_id: str,
        response: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist and route a complete frontend interaction response.

        M1 confirmations are resolved atomically so a successfully acknowledged
        batch cannot remain stranded in ``PENDING_CONFIRMATION``. M5 delivery
        decisions either release the current result for M6 or create an
        immutable M7 version from explicit target changes.
        """
        if not isinstance(response, dict):
            raise InputValidationError("交互回复必须为对象")
        if response.get("type") != "agent_interaction_response":
            raise InputValidationError(
                "交互回复 type 必须为 agent_interaction_response"
            )
        if response.get("task") != "PRIMER_DESIGN":
            raise InputValidationError(
                "交互回复 task 必须为 PRIMER_DESIGN"
            )
        submitted = response.get("interactions")
        if submitted is None and response.get("interactionId"):
            submitted = [
                {
                    "interactionId": response.get("interactionId"),
                    "answer": response.get("answer"),
                    "fieldValues": response.get("fieldValues") or {},
                    "customInstruction": response.get("customInstruction"),
                }
            ]
        if not isinstance(submitted, list) or not submitted:
            raise InputValidationError(
                "交互回复必须包含 interactionId/answer 或非空 interactions 数组"
            )

        submitted_by_id: dict[str, dict[str, Any]] = {}
        for item in submitted:
            if not isinstance(item, dict):
                raise InputValidationError("每条交互回复必须为对象")
            interaction_id = str(item.get("interactionId") or "").strip()
            if not interaction_id:
                raise InputValidationError("交互回复缺少 interactionId")
            if interaction_id in submitted_by_id:
                raise InputValidationError(
                    "交互回复包含重复 interactionId",
                    details={"interaction_id": interaction_id},
                )
            submitted_by_id[interaction_id] = item

        batch = self.store.get(batch_id)
        batch_id = str(batch["batch_id"])
        persisted = batch.get("agent_interactions") or []
        persisted_by_id = {
            str((item.get("data") or {}).get("interactionId")): item
            for item in persisted
        }
        unknown_ids = sorted(set(submitted_by_id) - set(persisted_by_id))
        if unknown_ids:
            raise InputValidationError(
                "交互回复引用了不存在的 interactionId",
                details={"interaction_ids": unknown_ids},
            )

        waiting_ids = {
            interaction_id
            for interaction_id, interaction in persisted_by_id.items()
            if (interaction.get("data") or {}).get("status") == "WAITING_USER"
        }
        if not waiting_ids:
            replayed = all(
                (persisted_by_id[interaction_id].get("data") or {})
                .get("userResponse", {})
                .get("selectedOption")
                == submitted_by_id[interaction_id].get("answer")
                and (persisted_by_id[interaction_id].get("data") or {}).get(
                    "status"
                )
                == "RESOLVED"
                for interaction_id in submitted_by_id
            )
            if replayed:
                routed_batch_ids = {
                    (persisted_by_id[interaction_id].get("data") or {})
                    .get("userResponse", {})
                    .get("routedBatchId")
                    for interaction_id in submitted_by_id
                }
                routed_batch_ids.discard(None)
                routed_batch = (
                    self.store.get(next(iter(routed_batch_ids)))
                    if len(routed_batch_ids) == 1
                    else batch
                )
                selected_actions = sorted(
                    {
                        str(submitted_by_id[interaction_id].get("answer"))
                        for interaction_id in submitted_by_id
                    }
                )
                if routed_batch_ids:
                    next_action = (
                        "RUN_M2"
                        if routed_batch.get("status") == "QUEUED"
                        else "WAIT_FOR_USER"
                    )
                elif "EXPORT_CURRENT_REPORT" in selected_actions:
                    next_action = "RUN_M6"
                else:
                    next_action = "RUN_M2"
                return {
                    "batch_id": routed_batch.get("batch_id", batch_id),
                    "source_batch_id": batch_id,
                    "status": routed_batch.get("status"),
                    "resolved_interaction_ids": sorted(submitted_by_id),
                    "remaining_interaction_ids": [],
                    "submission_replayed": True,
                    "new_version_created": bool(routed_batch_ids),
                    "next_action": next_action,
                    "batch": routed_batch,
                }
            raise InputValidationError(
                "该批次没有等待用户处理的交互项",
                details={"batch_id": batch_id},
            )

        submitted_ids = set(submitted_by_id)
        if submitted_ids != waiting_ids:
            raise InputValidationError(
                "必须一次回答本轮全部等待交互项",
                details={
                    "expected_interaction_ids": sorted(waiting_ids),
                    "submitted_interaction_ids": sorted(submitted_ids),
                },
            )

        prepared: list[tuple[str, dict[str, Any], dict[str, Any], str]] = []
        version_target_changes: list[dict[str, Any]] = []
        route_kinds: set[str] = set()
        for interaction_id in sorted(waiting_ids):
            interaction = persisted_by_id[interaction_id]
            data = interaction.get("data") or {}
            submitted_item = submitted_by_id[interaction_id]
            answer = submitted_item.get("answer")
            if not isinstance(answer, str) or not answer.strip():
                raise InputValidationError(
                    "当前交互只接受单选动作值",
                    details={"interaction_id": interaction_id},
                )
            action = answer.strip()
            allowed_actions = {
                str(option.get("value"))
                for option in data.get("options") or []
                if isinstance(option, dict) and option.get("value") is not None
            }
            if action not in allowed_actions:
                raise InputValidationError(
                    "交互回复动作不在允许选项中",
                    details={
                        "interaction_id": interaction_id,
                        "answer": action,
                        "allowed_actions": sorted(allowed_actions),
                    },
                )
            stage = data.get("stage")
            if action == "CONTINUE_WITH_NOT_EVALUATED":
                if stage != "M1_PREFLIGHT":
                    raise InputValidationError(
                        "按未评估继续仅适用于 M1 预检确认",
                        details={"interaction_id": interaction_id},
                    )
                route_kinds.add("QUEUE")
            elif stage == "M5_DELIVERY_DECISION" and action == "EXPORT_CURRENT_REPORT":
                route_kinds.add("REPORT_CURRENT")
            elif stage == "M5_DELIVERY_DECISION" and action in {
                "CONTINUE_SEARCH",
                "ADJUST_CONSTRAINTS",
            }:
                field_values = submitted_item.get("fieldValues") or {}
                target_changes = (
                    field_values.get("target_changes")
                    or field_values.get("targetChanges")
                ) if isinstance(field_values, dict) else None
                if target_changes is None and isinstance(field_values, dict):
                    allowed_change_fields = {
                        "template",
                        "target_organism",
                        "hosts",
                        "related_species",
                        "background_organisms",
                        "design_parameters",
                    }
                    direct_change = {
                        key: copy.deepcopy(value)
                        for key, value in field_values.items()
                        if key in allowed_change_fields
                    }
                    if direct_change:
                        target_changes = [
                            {
                                "target_id": (data.get("context") or {}).get(
                                    "targetId"
                                ),
                                **direct_change,
                            }
                        ]
                if not isinstance(target_changes, list) or not target_changes:
                    raise InputValidationError(
                        "继续搜索或调整约束必须提交明确的 target_changes，不能隐式放宽硬规则",
                        details={
                            "interaction_id": interaction_id,
                            "answer": action,
                            "required_field": "fieldValues.target_changes",
                        },
                    )
                version_target_changes.extend(copy.deepcopy(target_changes))
                route_kinds.add("NEW_VERSION")
            else:
                raise InputValidationError(
                    "该交互动作尚未接入确定性路由",
                    details={
                        "interaction_id": interaction_id,
                        "answer": action,
                        "stage": stage,
                    },
                )
            prepared.append((interaction_id, interaction, submitted_item, action))

        if len(route_kinds) > 1:
            raise InputValidationError(
                "同一批交互回复不能混合继续当前报告、排队和创建新版本",
                details={"route_kinds": sorted(route_kinds)},
            )
        if route_kinds == {"NEW_VERSION"}:
            from .wf01_v02_m7 import apply_target_changes

            apply_target_changes(batch.get("targets") or [], version_target_changes)

        submitted_at = utc_now()
        resolved_ids: list[str] = []
        for interaction_id, interaction, submitted_item, action in prepared:
            data = interaction["data"]
            data["status"] = "USER_SUBMITTED"
            data["userResponse"] = {
                "selectedOption": action,
                "fieldValues": copy.deepcopy(
                    submitted_item.get("fieldValues") or {}
                ),
                "customInstruction": submitted_item.get("customInstruction"),
                "submittedAt": submitted_at,
                "routedBatchId": None,
            }
            self._append_event(
                batch,
                "AGENT_INTERACTION_USER_SUBMITTED",
                status="USER_SUBMITTED",
                interaction_id=interaction_id,
                action=action,
            )

            context = data.get("context") or {}
            issue_id = context.get("issueId")
            target_id = context.get("targetId")
            resolution = {
                "status": "ACCEPTED",
                "action": action,
                "interaction_id": interaction_id,
                "resolved_at": submitted_at,
            }
            for issue in (batch.get("preflight") or {}).get("issues") or []:
                if issue.get("issue_id") == issue_id:
                    issue["resolution"] = copy.deepcopy(resolution)
            for target in batch.get("targets") or []:
                if target.get("target_id") != target_id:
                    continue
                for issue in target.get("issues") or []:
                    if issue.get("issue_id") == issue_id:
                        issue["resolution"] = copy.deepcopy(resolution)
                if action == "CONTINUE_WITH_NOT_EVALUATED":
                    target.setdefault("confirmation_overrides", []).append(
                        {
                            "issue_id": issue_id,
                            "blocker_code": (data.get("blocker") or {}).get("code"),
                            "policy": "CONTINUE_WITH_NOT_EVALUATED",
                            "interaction_id": interaction_id,
                            "accepted_at": submitted_at,
                        }
                    )

            data["status"] = "RESOLVED"
            data["resolvedAt"] = submitted_at
            resolved_ids.append(interaction_id)
            self._append_event(
                batch,
                "AGENT_INTERACTION_RESOLVED",
                status="RESOLVED",
                interaction_id=interaction_id,
                action=action,
                target_id=target_id,
            )

        unresolved = [
            interaction
            for interaction in persisted
            if (interaction.get("data") or {}).get("status")
            in {"WAITING_USER", "USER_SUBMITTED", "RUNNING"}
        ]
        unresolved_target_ids = {
            (interaction.get("data") or {}).get("context", {}).get("targetId")
            for interaction in unresolved
        }
        for target in batch.get("targets") or []:
            if (
                target.get("status") == "PENDING_CONFIRMATION"
                and target.get("target_id") not in unresolved_target_ids
            ):
                target["status"] = "QUEUED"
                target["updated_at"] = submitted_at

        targets = batch.get("targets") or []
        if (
            not unresolved
            and targets
            and all(target.get("status") == "QUEUED" for target in targets)
        ):
            batch["status"] = "QUEUED"
            preflight = batch.get("preflight") or {}
            preflight["preflight_status"] = "READY_TO_QUEUE"
            preflight["can_auto_run"] = True
            preflight["next_action"] = "RUN_M2"
            preflight["confirmation_resolved_at"] = submitted_at
            self._append_event(
                batch,
                "BATCH_CONFIRMATION_RESOLVED",
                status="QUEUED",
                resolved_interaction_ids=resolved_ids,
            )

        batch["target_counts"] = {
            "queued": sum(
                target.get("status") == "QUEUED" for target in targets
            ),
            "pending_confirmation": sum(
                target.get("status") == "PENDING_CONFIRMATION"
                for target in targets
            ),
        }
        batch["updated_at"] = submitted_at
        self.store.save(batch)

        routed_batch = batch
        new_version_created = False
        if route_kinds == {"NEW_VERSION"}:
            routed_batch = self.create_version(
                batch_id,
                {
                    "reason": "由用户交互决定创建重设计版本",
                    "target_changes": version_target_changes,
                },
            )
            new_version_created = True
            for interaction_id in resolved_ids:
                data = persisted_by_id[interaction_id]["data"]
                data["userResponse"]["routedBatchId"] = routed_batch["batch_id"]
            self._append_event(
                batch,
                "AGENT_INTERACTION_ROUTED_TO_VERSION",
                status=routed_batch.get("status"),
                routed_batch_id=routed_batch["batch_id"],
                resolved_interaction_ids=resolved_ids,
            )
            batch["updated_at"] = utc_now()
            self.store.save(batch)
        if new_version_created:
            next_action = (
                "RUN_M2"
                if routed_batch.get("status") == "QUEUED"
                else "WAIT_FOR_USER"
            )
        elif route_kinds == {"REPORT_CURRENT"}:
            next_action = "RUN_M6"
        else:
            next_action = "RUN_M2"
        return {
            "batch_id": routed_batch.get("batch_id", batch_id),
            "source_batch_id": batch_id,
            "status": routed_batch.get("status"),
            "resolved_interaction_ids": resolved_ids,
            "remaining_interaction_ids": sorted(
                str((item.get("data") or {}).get("interactionId"))
                for item in unresolved
            ),
            "submission_replayed": False,
            "new_version_created": new_version_created,
            "next_action": next_action,
            "batch": routed_batch,
        }

    @staticmethod
    def _append_event(batch: dict[str, Any], event_type: str, **details: Any) -> None:
        events = batch.setdefault("events", [])
        events.append(
            {
                "event_seq": len(events) + 1,
                "event_type": event_type,
                "created_at": utc_now(),
                **details,
            }
        )

    @classmethod
    def _append_agent_interaction(
        cls,
        batch: dict[str, Any],
        interaction: dict[str, Any],
    ) -> None:
        interactions = batch.setdefault("agent_interactions", [])
        interactions.append(interaction)
        data = interaction["data"]
        cls._append_event(
            batch,
            "AGENT_INTERACTION_CREATED",
            status="WAITING_USER",
            interaction_id=data["interactionId"],
            stage=data["stage"],
            blocker_code=data["blocker"]["code"],
            target_id=data["context"].get("targetId"),
        )

    @staticmethod
    def _is_m7_reused(target: dict[str, Any]) -> bool:
        return (
            (target.get("m7_provenance") or {}).get("disposition")
            == "REUSED"
        )

    def create_version(
        self,
        source_batch_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create an immutable M7 redesign version from a completed source.

        Unmodified targets retain their M2--M5 result snapshots.  Every target
        whose M1-relevant input differs is reconstructed from fresh preflight
        data and starts again at M2; no prior candidate or specificity result is
        copied into that target.
        """
        from .wf01_v02_m7 import (
            M7_ENGINE_VERSION,
            M7_REDESIGN_CONTRACT_VERSION,
            apply_target_changes,
            preflight_payload_from_models,
            rebind_preflight_target_ids,
            stable_sha256,
        )

        options = {} if options is None else options
        if not isinstance(options, dict):
            raise InputValidationError("M7 重设计请求必须为对象")
        unknown_options = sorted(set(options) - {"target_changes", "reason", "task_name"})
        if unknown_options:
            raise InputValidationError(
                "M7 包含不支持的请求字段",
                details={"unknown_fields": unknown_options},
            )
        reason = options.get("reason")
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise InputValidationError("M7 reason 必须为非空文本")

        source = self.store.get(source_batch_id)
        source_batch_id = str(source["batch_id"])
        if source.get("workflow") != "WF-01" or source.get("prd_version") != PRD_VERSION:
            raise InputValidationError(
                "只能基于 WF-01 PRD v0.2 批次创建 M7 版本",
                details={
                    "batch_id": source_batch_id,
                    "workflow": source.get("workflow"),
                    "prd_version": source.get("prd_version"),
                },
            )
        if source.get("m5_outcome") != "COMPLETED":
            raise InputValidationError(
                "只有已完成 M5 状态汇总的批次可以创建 M7 版本",
                details={
                    "batch_id": source_batch_id,
                    "m5_outcome": source.get("m5_outcome"),
                    "status": source.get("status"),
                },
            )
        source_targets = source.get("targets") or []
        if not source_targets:
            raise InputValidationError("源批次没有可版本化的靶标")

        requested_task_name = options.get("task_name")
        if requested_task_name is not None and (
            not isinstance(requested_task_name, str)
            or not requested_task_name.strip()
        ):
            raise InputValidationError("M7 task_name 必须为非空文本")
        task_name = (
            requested_task_name.strip()
            if isinstance(requested_task_name, str)
            else str(
                (source.get("preflight") or {})
                .get("normalised_input", {})
                .get("task_name")
                or "wf01_batch"
            )
        )

        models, rerun_target_ids, version_diff = apply_target_changes(
            source_targets,
            options.get("target_changes"),
        )
        preflight_payload = preflight_payload_from_models(
            models,
            task_name=task_name,
        )
        preflight = rebind_preflight_target_ids(
            WF01V02Preflight(preflight_payload).run(),
            source_targets,
        )
        if source.get("required_execution_path") == "FULL_SPECIFICITY":
            self._assert_full_route_context(preflight)
        fresh_by_id = {
            str(target["target_id"]): target
            for target in preflight.get("targets") or []
        }
        rerun_target_id_set = set(rerun_target_ids)
        reused_target_ids: list[str] = []
        version_targets: list[dict[str, Any]] = []
        for source_target in source_targets:
            target_id = str(source_target["target_id"])
            source_result_sha256 = stable_sha256(source_target)
            if target_id in rerun_target_id_set:
                target = copy.deepcopy(fresh_by_id[target_id])
                target["m7_provenance"] = {
                    "disposition": "RERUN_REQUIRED",
                    "source_batch_id": source_batch_id,
                    "source_version": source.get("task_version"),
                    "source_target_id": target_id,
                    "source_target_result_sha256": source_result_sha256,
                    "reason": "M1_RELEVANT_INPUT_CHANGED",
                }
                version_targets.append(target)
                continue
            if not isinstance(source_target.get("m5_execution"), dict):
                raise InputValidationError(
                    "源靶标缺少 M5 结果，不能安全复用",
                    details={"target_id": target_id},
                )
            target = copy.deepcopy(source_target)
            target["m7_provenance"] = {
                "disposition": "REUSED",
                "source_batch_id": source_batch_id,
                "source_version": source.get("task_version"),
                "source_target_id": target_id,
                "source_target_result_sha256": source_result_sha256,
                "reason": "M1_RELEVANT_INPUT_UNCHANGED",
            }
            version_targets.append(target)
            reused_target_ids.append(target_id)

        lineage_root_batch_id = (
            source.get("lineage_root_batch_id") or source_batch_id
        )
        batch_id = str(uuid4())
        task_version = self.store.next_task_version(lineage_root_batch_id)
        status = "QUEUED" if preflight.get("can_auto_run") else "PENDING_CONFIRMATION"
        source_m6 = source.get("m6_execution") or {}
        batch = {
            "batch_id": batch_id,
            "task_version": task_version,
            "source_batch_id": source_batch_id,
            "source_version": source.get("task_version"),
            "lineage_root_batch_id": lineage_root_batch_id,
            "workflow": "WF-01",
            "prd_version": PRD_VERSION,
            "status": status,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "preflight": preflight,
            "target_manifest": {
                **copy.deepcopy(preflight.get("target_manifest") or {}),
                "targets": version_targets,
            },
            "targets": version_targets,
            "target_total": len(version_targets),
            "target_counts": {
                "total": len(version_targets),
                "rerun_required": len(rerun_target_ids),
                "reused": len(reused_target_ids),
                "queued": sum(target.get("status") == "QUEUED" for target in version_targets),
                "pending_confirmation": sum(
                    target.get("status") == "PENDING_CONFIRMATION"
                    for target in version_targets
                ),
            },
            "events": [
                {
                    "event_seq": 1,
                    "event_type": "M7_VERSION_CREATED",
                    "created_at": utc_now(),
                    "status": status,
                    "source_batch_id": source_batch_id,
                    "source_version": source.get("task_version"),
                    "lineage_root_batch_id": lineage_root_batch_id,
                    "task_version": task_version,
                }
            ],
            "agent_interactions": [],
            "scientific_artifacts": [],
            "report_id": None,
            "search_complete": False,
            "technical_search_complete": False,
            "version_diff": version_diff,
            "m7_execution": {
                "stage": "M7_VERSION_CREATED",
                "engine_version": M7_ENGINE_VERSION,
                "redesign_contract_version": M7_REDESIGN_CONTRACT_VERSION,
                "source_batch_id": source_batch_id,
                "source_version": source.get("task_version"),
                "lineage_root_batch_id": lineage_root_batch_id,
                "task_version": task_version,
                "reason": reason.strip() if isinstance(reason, str) else None,
                "changed_target_ids": rerun_target_ids,
                "rerun_target_ids": rerun_target_ids,
                "reused_target_ids": reused_target_ids,
                "version_diff": copy.deepcopy(version_diff),
                "source_report_id": source.get("report_id"),
                "source_report_content_sha256": source_m6.get(
                    "canonical_content_sha256"
                ) or source.get("report_content_sha256"),
                "created_at": utc_now(),
            },
        }
        if source.get("required_execution_path") == "FULL_SPECIFICITY":
            batch["required_execution_path"] = "FULL_SPECIFICITY"
        for target_id in rerun_target_ids:
            self._append_event(
                batch,
                "TARGET_M7_RERUN_REQUIRED",
                target_id=target_id,
                status=(fresh_by_id[target_id]).get("status"),
                reason="M1_RELEVANT_INPUT_CHANGED",
            )
        for target_id in reused_target_ids:
            self._append_event(
                batch,
                "TARGET_M7_REUSED",
                target_id=target_id,
                status=next(
                    target.get("status")
                    for target in version_targets
                    if target.get("target_id") == target_id
                ),
                reason="M1_RELEVANT_INPUT_UNCHANGED",
            )
        self._record_scientific_artifact(
            batch,
            stage="M1",
            tools=["WF01_PREFLIGHT"],
            raw_data=preflight,
        )
        self._record_scientific_artifact(
            batch,
            stage="M7",
            tools=["WF01_VERSIONING_ENGINE"],
            raw_data=batch["m7_execution"],
        )
        self.store.save(batch)
        return batch

    def execute_m2(self, batch_id: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run the pure M2 engine for every queued target.

        M2 does not declare a final success/partial/failure result.  Targets
        remain in EXPLORING because M3 finite exploration and M4 specificity
        have not run.
        """
        from .wf01_v02_engine import WF01V02TargetEngine

        options = options or {}
        if not isinstance(options, dict):
            raise InputValidationError("M2 运行选项必须为对象")
        unknown_options = sorted(set(options) - {"allow_development_fallback"})
        if unknown_options:
            raise InputValidationError(
                "存在不支持的 M2 运行选项",
                details={"unknown_options": unknown_options},
            )
        allow_fallback = options.get(
            "allow_development_fallback",
            self.config.get("tools", {}).get("allow_development_fallback", False),
        )
        if not isinstance(allow_fallback, bool):
            raise InputValidationError(
                "allow_development_fallback 必须为布尔值",
                details={"value": allow_fallback},
            )

        batch = self.store.get(batch_id)
        self._assert_no_pending_user_interactions(
            batch,
            requested_stage="M2",
        )
        if batch.get("status") != "QUEUED":
            raise InputValidationError(
                "只有 QUEUED 批次可以进入 M2",
                details={"batch_id": batch_id, "status": batch.get("status")},
            )

        batch["status"] = "RUNNING"
        batch["updated_at"] = utc_now()
        self._append_event(
            batch,
            "M2_EXECUTION_STARTED",
            status="RUNNING",
            target_total=len(batch.get("targets", [])),
            reused_target_total=sum(
                self._is_m7_reused(target)
                for target in batch.get("targets", [])
            ),
            allow_development_fallback=allow_fallback,
        )
        self.store.save(batch)

        engine = WF01V02TargetEngine(
            self.config,
            default_parameters=DEFAULT_PARAMETERS,
            parameter_profiles=PARAMETER_PROFILES,
            allow_development_fallback=allow_fallback,
        )
        completed = 0
        failed = 0
        for target in batch.get("targets", []):
            target_id = str(target.get("target_id"))
            if self._is_m7_reused(target):
                self._append_event(
                    batch,
                    "TARGET_M2_REUSED",
                    target_id=target_id,
                    status=target.get("status"),
                    source_batch_id=(target.get("m7_provenance") or {}).get(
                        "source_batch_id"
                    ),
                    source_target_id=(target.get("m7_provenance") or {}).get(
                        "source_target_id"
                    ),
                )
                continue
            if target.get("status") != "QUEUED":
                continue
            target["status"] = "RUNNING"
            target["updated_at"] = utc_now()
            self._append_event(
                batch,
                "TARGET_M2_STARTED",
                target_id=target_id,
                status="RUNNING",
            )
            try:
                execution = engine.run_target(copy.deepcopy(target))
            except AgentDError as exc:
                failed += 1
                error = exc.to_dict()
                target["status"] = "M2_TOOL_ERROR"
                target["m2_execution"] = {
                    "stage": "M2_TOOL_ERROR",
                    "target_id": target_id,
                    "error": error,
                    "search_complete": False,
                    "search_incomplete_reason": "M2_TOOL_ERROR",
                    "specificity_status": "NOT_RUN",
                }
                target["search_complete"] = False
                if batch.get("interaction_mode") != "NO_POPUPS":
                    self._append_agent_interaction(
                        batch,
                        build_execution_error_interaction(
                            batch_id,
                            stage="M2",
                            target_id=target_id,
                            error=error,
                        ),
                    )
                self._append_event(
                    batch,
                    "TARGET_M2_FAILED",
                    target_id=target_id,
                    status=target["status"],
                    error=error,
                )
            else:
                completed += 1
                target["status"] = "EXPLORING"
                target["m2_execution"] = execution
                target["exploration_manifest"] = copy.deepcopy(execution["exploration_manifest"])
                target["candidate_counts"] = copy.deepcopy(execution["candidate_counts"])
                target["search_complete"] = False
                self._append_event(
                    batch,
                    "TARGET_M2_COMPLETED",
                    target_id=target_id,
                    status="EXPLORING",
                    candidate_counts=copy.deepcopy(execution["candidate_counts"]),
                    search_complete=False,
                )
            self._record_scientific_artifact(
                batch,
                stage="M2",
                tools=["MAFFT", "Primer3", "WF01_HARD_RULE_ENGINE"],
                raw_data=target["m2_execution"],
                target_id=target_id,
            )
            self._strip_download_only_scientific_raw(
                target["m2_execution"],
                stage="M2",
            )
            target["updated_at"] = utc_now()
            batch["updated_at"] = utc_now()
            self.store.save(batch)

        target_counts = {
            "exploring": sum(target.get("status") == "EXPLORING" for target in batch.get("targets", [])),
            "m2_tool_error": sum(target.get("status") == "M2_TOOL_ERROR" for target in batch.get("targets", [])),
            "pending_confirmation": sum(target.get("status") == "PENDING_CONFIRMATION" for target in batch.get("targets", [])),
            "m7_reused": sum(
                self._is_m7_reused(target)
                for target in batch.get("targets", [])
            ),
        }
        batch["target_counts"] = target_counts
        batch["status"] = "EXPLORING"
        batch["m2_outcome"] = "COMPLETED" if failed == 0 else "COMPLETED_WITH_TARGET_ERRORS"
        batch["search_complete"] = False
        batch["search_incomplete_reason"] = "M3_FINITE_EXPLORATION_MANIFEST_NOT_RUN"
        batch["updated_at"] = utc_now()
        self._append_event(
            batch,
            "M2_EXECUTION_COMPLETED",
            status="EXPLORING",
            m2_outcome=batch["m2_outcome"],
            completed_targets=completed,
            failed_targets=failed,
            reused_targets=sum(
                self._is_m7_reused(target)
                for target in batch.get("targets", [])
            ),
            search_complete=False,
        )
        self.store.save(batch)
        return batch

    def execute_m3(self, batch_id: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Validate the finite manifest, deduplicate, score and prepare Top10."""
        from .wf01_v02_m3 import WF01V02M3Engine

        options = options or {}
        if not isinstance(options, dict):
            raise InputValidationError("M3 运行选项必须为对象")
        if options:
            raise InputValidationError(
                "M3 没有可由调用方修改的运行选项",
                details={"unknown_options": sorted(options)},
            )
        batch = self.store.get(batch_id)
        self._assert_no_pending_user_interactions(
            batch,
            requested_stage="M3",
        )
        if batch.get("status") != "EXPLORING" or not batch.get("m2_outcome"):
            raise InputValidationError(
                "只有已完成 M2 且处于 EXPLORING 的批次可以进入 M3",
                details={
                    "batch_id": batch_id,
                    "status": batch.get("status"),
                    "m2_outcome": batch.get("m2_outcome"),
                },
            )
        if any(
            target.get("m3_execution") and not self._is_m7_reused(target)
            for target in batch.get("targets", [])
        ):
            raise InputValidationError(
                "该批次已经执行过 M3；本期不允许原地覆盖结果",
                details={"batch_id": batch_id},
            )

        batch["status"] = "RUNNING"
        batch["updated_at"] = utc_now()
        self._append_event(
            batch,
            "M3_EXECUTION_STARTED",
            status="RUNNING",
            target_total=len(batch.get("targets", [])),
        )
        self.store.save(batch)

        engine = WF01V02M3Engine()
        completed = 0
        blocked = 0
        for target in batch.get("targets", []):
            target_id = str(target.get("target_id"))
            if self._is_m7_reused(target):
                self._append_event(
                    batch,
                    "TARGET_M3_REUSED",
                    target_id=target_id,
                    status=target.get("status"),
                    source_batch_id=(target.get("m7_provenance") or {}).get(
                        "source_batch_id"
                    ),
                    source_target_id=(target.get("m7_provenance") or {}).get(
                        "source_target_id"
                    ),
                )
                continue
            self._append_event(
                batch,
                "TARGET_M3_STARTED",
                target_id=target_id,
                status="RUNNING",
            )
            try:
                execution = engine.run_target(copy.deepcopy(target))
            except AgentDError as exc:
                blocked += 1
                error = exc.to_dict()
                target["m3_execution"] = {
                    "stage": "M3_BLOCKED",
                    "target_id": target_id,
                    "error": error,
                    "primer3_search_complete": False,
                    "search_complete": False,
                    "search_incomplete_reason": "M3_INPUT_INCOMPLETE",
                }
                target["primer3_search_complete"] = False
                target["search_complete"] = False
                if batch.get("interaction_mode") != "NO_POPUPS":
                    self._append_agent_interaction(
                        batch,
                        build_execution_error_interaction(
                            batch_id,
                            stage="M3",
                            target_id=target_id,
                            error=error,
                        ),
                    )
                self._append_event(
                    batch,
                    "TARGET_M3_BLOCKED",
                    target_id=target_id,
                    status="EXPLORING",
                    error=error,
                )
            else:
                completed += 1
                target["m3_execution"] = execution
                target["candidate_counts"] = copy.deepcopy(execution["candidate_counts"])
                target["primer3_search_complete"] = execution["primer3_search_complete"]
                target["search_complete"] = False
                target["provisional_top10"] = copy.deepcopy(execution["provisional_top10"])
                self._append_event(
                    batch,
                    "TARGET_M3_COMPLETED",
                    target_id=target_id,
                    status="EXPLORING",
                    candidate_counts=copy.deepcopy(execution["candidate_counts"]),
                    primer3_search_complete=execution["primer3_search_complete"],
                    search_complete=False,
                )
            self._record_scientific_artifact(
                batch,
                stage="M3",
                tools=["WF01_DEDUPLICATION", "WF01_SCORING"],
                raw_data=target["m3_execution"],
                target_id=target_id,
            )
            target["status"] = "EXPLORING"
            target["updated_at"] = utc_now()
            batch["updated_at"] = utc_now()
            self.store.save(batch)

        processed = [
            target
            for target in batch.get("targets", [])
            if target.get("m3_execution", {}).get("stage") == "M3_COMPLETE_AWAITING_M4"
        ]
        batch["status"] = "EXPLORING"
        batch["m3_outcome"] = "COMPLETED" if blocked == 0 else "COMPLETED_WITH_BLOCKED_TARGETS"
        batch["primer3_search_complete"] = bool(processed) and len(processed) == len(
            batch.get("targets", [])
        ) and all(target.get("primer3_search_complete") for target in processed)
        batch["search_complete"] = False
        batch["search_incomplete_reason"] = (
            "M4_SPECIFICITY_NOT_RUN"
            if batch["primer3_search_complete"]
            else "PRIMER3_EXPLORATION_INCOMPLETE"
        )
        batch["target_counts"] = {
            "exploring": len(batch.get("targets", [])),
            "m3_completed": completed,
            "m3_blocked": blocked,
            "m7_reused": sum(
                self._is_m7_reused(target)
                for target in batch.get("targets", [])
            ),
        }
        batch["updated_at"] = utc_now()
        self._append_event(
            batch,
            "M3_EXECUTION_COMPLETED",
            status="EXPLORING",
            m3_outcome=batch["m3_outcome"],
            completed_targets=completed,
            blocked_targets=blocked,
            reused_targets=sum(
                self._is_m7_reused(target)
                for target in batch.get("targets", [])
            ),
            primer3_search_complete=batch["primer3_search_complete"],
            search_complete=False,
        )
        self.store.save(batch)
        return batch

    def execute_m4(self, batch_id: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run the three-mode specificity evidence chain and final M4 ranking."""
        options = options or {}
        if not isinstance(options, dict):
            raise InputValidationError("M4 运行选项必须为对象")
        unknown_options = sorted(set(options) - {"allow_development_fallback"})
        if unknown_options:
            raise InputValidationError(
                "存在不支持的 M4 运行选项",
                details={"unknown_options": unknown_options},
            )
        allow_fallback = options.get(
            "allow_development_fallback",
            self.config.get("tools", {}).get("allow_development_fallback", False),
        )
        if not isinstance(allow_fallback, bool):
            raise InputValidationError(
                "allow_development_fallback 必须为布尔值",
                details={"value": allow_fallback},
            )

        batch = self.store.get(batch_id)
        self._assert_no_pending_user_interactions(
            batch,
            requested_stage="M4",
        )
        if batch.get("status") != "EXPLORING" or not batch.get("m3_outcome"):
            raise InputValidationError(
                "只有已完成 M3 且处于 EXPLORING 的批次可以进入 M4",
                details={
                    "batch_id": batch_id,
                    "status": batch.get("status"),
                    "m3_outcome": batch.get("m3_outcome"),
                },
            )
        if any(
            target.get("m4_execution") and not self._is_m7_reused(target)
            for target in batch.get("targets", [])
        ):
            raise InputValidationError(
                "该批次已经执行过 M4；本期不允许原地覆盖结果",
                details={"batch_id": batch_id},
            )

        # Construct adapters before persisting RUNNING. Configuration or
        # executable-discovery failures must not leave a stage marked in-flight.
        from .wf01_v02_m4 import WF01V02M4Engine

        engine = WF01V02M4Engine(
            self.catalog,
            self.config,
            allow_development_fallback=allow_fallback,
        )
        batch["status"] = "RUNNING"
        batch["updated_at"] = utc_now()
        self._append_event(
            batch,
            "M4_EXECUTION_STARTED",
            status="RUNNING",
            target_total=len(batch.get("targets", [])),
            reused_target_total=sum(
                self._is_m7_reused(target)
                for target in batch.get("targets", [])
            ),
            allow_development_fallback=allow_fallback,
        )
        self.store.save(batch)

        completed = 0
        blocked = 0
        for target in batch.get("targets", []):
            target_id = str(target.get("target_id"))
            if self._is_m7_reused(target):
                self._append_event(
                    batch,
                    "TARGET_M4_REUSED",
                    target_id=target_id,
                    status=target.get("status"),
                    source_batch_id=(target.get("m7_provenance") or {}).get(
                        "source_batch_id"
                    ),
                    source_target_id=(target.get("m7_provenance") or {}).get(
                        "source_target_id"
                    ),
                )
                continue
            self._append_event(
                batch,
                "TARGET_M4_STARTED",
                target_id=target_id,
                status="RUNNING",
            )
            try:
                execution = engine.run_target(copy.deepcopy(target))
            except AgentDError as exc:
                blocked += 1
                error = exc.to_dict()
                target["m4_execution"] = {
                    "stage": "M4_BLOCKED",
                    "target_id": target_id,
                    "error": error,
                    "specificity_complete": False,
                    "technical_search_complete": False,
                    "search_complete": False,
                    "search_incomplete_reason": "M4_INPUT_INCOMPLETE",
                }
                target["specificity_complete"] = False
                target["technical_search_complete"] = False
                target["search_complete"] = False
                if batch.get("interaction_mode") != "NO_POPUPS":
                    self._append_agent_interaction(
                        batch,
                        build_execution_error_interaction(
                            batch_id,
                            stage="M4",
                            target_id=target_id,
                            error=error,
                        ),
                    )
                self._append_event(
                    batch,
                    "TARGET_M4_BLOCKED",
                    target_id=target_id,
                    status="EXPLORING",
                    error=error,
                )
            except Exception as exc:
                # Unexpected adapter/runtime failures must still leave a
                # persisted, resumable batch.  Without this conversion M4 has
                # already saved status=RUNNING and every retry is rejected by
                # the non-reentrant stage guard, stranding the workflow.
                blocked += 1
                error = {
                    "code": "M4_UNEXPECTED_RUNTIME_ERROR",
                    "message": "M4 特异性检查发生未预期的运行时错误",
                    "details": {
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                    },
                }
                target["m4_execution"] = {
                    "stage": "M4_BLOCKED",
                    "target_id": target_id,
                    "error": error,
                    "specificity_complete": False,
                    "technical_search_complete": False,
                    "search_complete": False,
                    "search_incomplete_reason": "M4_UNEXPECTED_RUNTIME_ERROR",
                }
                target["specificity_complete"] = False
                target["technical_search_complete"] = False
                target["search_complete"] = False
                if batch.get("interaction_mode") != "NO_POPUPS":
                    self._append_agent_interaction(
                        batch,
                        build_execution_error_interaction(
                            batch_id,
                            stage="M4",
                            target_id=target_id,
                            error=error,
                        ),
                    )
                self._append_event(
                    batch,
                    "TARGET_M4_BLOCKED",
                    target_id=target_id,
                    status="EXPLORING",
                    error=error,
                )
            else:
                completed += 1
                target["m4_execution"] = execution
                target["candidate_counts"] = copy.deepcopy(
                    execution["candidate_counts"]
                )
                target["specificity_complete"] = execution[
                    "specificity_complete"
                ]
                target["technical_search_complete"] = execution[
                    "technical_search_complete"
                ]
                target["search_complete"] = False
                target["top10"] = copy.deepcopy(execution["top10"])
                self._append_event(
                    batch,
                    "TARGET_M4_COMPLETED",
                    target_id=target_id,
                    status="EXPLORING",
                    candidate_counts=copy.deepcopy(execution["candidate_counts"]),
                    specificity_complete=execution["specificity_complete"],
                    technical_search_complete=execution[
                        "technical_search_complete"
                    ],
                    search_complete=False,
                )
            self._record_scientific_artifact(
                batch,
                stage="M4",
                tools=[
                    "BLAST+",
                    "Primer3_THERMODYNAMICS",
                    "WF01_SPECIFICITY_ENGINE",
                ],
                raw_data=target["m4_execution"],
                target_id=target_id,
            )
            self._strip_download_only_scientific_raw(
                target["m4_execution"],
                stage="M4",
            )
            target["status"] = "EXPLORING"
            target["updated_at"] = utc_now()
            batch["updated_at"] = utc_now()
            self.store.save(batch)

        processed = [
            target
            for target in batch.get("targets", [])
            if target.get("m4_execution", {}).get("stage")
            == "M4_COMPLETE_AWAITING_M5"
        ]
        target_total = len(batch.get("targets", []))
        batch["status"] = "EXPLORING"
        batch["m4_outcome"] = (
            "COMPLETED" if blocked == 0 else "COMPLETED_WITH_BLOCKED_TARGETS"
        )
        batch["specificity_complete"] = (
            bool(processed)
            and len(processed) == target_total
            and all(target.get("specificity_complete") for target in processed)
        )
        batch["technical_search_complete"] = (
            bool(processed)
            and len(processed) == target_total
            and all(
                target.get("technical_search_complete") for target in processed
            )
        )
        batch["search_complete"] = False
        batch["search_incomplete_reason"] = (
            "M5_STATUS_ASSEMBLY_AND_POLICY_APPROVAL_PENDING"
            if batch["technical_search_complete"]
            else "SPECIFICITY_OR_PRIMER3_INCOMPLETE"
        )
        batch["target_counts"] = {
            "exploring": target_total,
            "m4_completed": completed,
            "m4_blocked": blocked,
            "m7_reused": sum(
                self._is_m7_reused(target)
                for target in batch.get("targets", [])
            ),
            "specificity_complete": sum(
                bool(target.get("specificity_complete"))
                for target in batch.get("targets", [])
            ),
            "specificity_incomplete": sum(
                not bool(target.get("specificity_complete"))
                for target in batch.get("targets", [])
            ),
        }
        batch["updated_at"] = utc_now()
        self._append_event(
            batch,
            "M4_EXECUTION_COMPLETED",
            status="EXPLORING",
            m4_outcome=batch["m4_outcome"],
            completed_targets=completed,
            blocked_targets=blocked,
            reused_targets=sum(
                self._is_m7_reused(target)
                for target in batch.get("targets", [])
            ),
            specificity_complete=batch["specificity_complete"],
            technical_search_complete=batch["technical_search_complete"],
            search_complete=False,
        )
        self.store.save(batch)
        return batch

    def execute_m5(self, batch_id: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Classify target results and assemble the final batch result status."""
        from .wf01_v02_m5 import WF01V02M5Engine

        options = {} if options is None else options
        if not isinstance(options, dict):
            raise InputValidationError("M5 运行选项必须为对象")
        if options:
            raise InputValidationError(
                "M5 没有可由调用方修改的运行选项",
                details={"unknown_options": sorted(options)},
            )

        batch = self.store.get(batch_id)
        self._assert_no_pending_user_interactions(
            batch,
            requested_stage="M5",
        )
        if batch.get("m5_execution") or any(
            target.get("m5_execution") and not self._is_m7_reused(target)
            for target in batch.get("targets", [])
        ):
            raise InputValidationError(
                "该批次已经执行过 M5；本期不允许原地覆盖结果",
                details={"batch_id": batch_id},
            )
        if batch.get("status") != "EXPLORING" or not batch.get("m4_outcome"):
            raise InputValidationError(
                "只有已完成 M4 且处于 EXPLORING 的批次可以进入 M5",
                details={
                    "batch_id": batch_id,
                    "status": batch.get("status"),
                    "m4_outcome": batch.get("m4_outcome"),
                },
            )
        batch["status"] = "SUMMARIZING"
        batch["updated_at"] = utc_now()
        self._append_event(
            batch,
            "M5_SUMMARIZATION_STARTED",
            status="SUMMARIZING",
            target_total=len(batch.get("targets", [])),
            reused_target_total=sum(
                self._is_m7_reused(target)
                for target in batch.get("targets", [])
            ),
        )
        self.store.save(batch)

        engine = WF01V02M5Engine()
        target_results: list[dict[str, Any]] = []
        for target in batch.get("targets", []):
            target_id = str(target.get("target_id"))
            if self._is_m7_reused(target):
                execution = copy.deepcopy(target["m5_execution"])
                target_results.append(execution)
                self._append_event(
                    batch,
                    "TARGET_M5_REUSED",
                    target_id=target_id,
                    status=target.get("status"),
                    source_batch_id=(target.get("m7_provenance") or {}).get(
                        "source_batch_id"
                    ),
                    source_target_id=(target.get("m7_provenance") or {}).get(
                        "source_target_id"
                    ),
                    status_reason=execution.get("status_reason"),
                )
                continue
            target["status"] = "SUMMARIZING"
            target["updated_at"] = utc_now()
            self._append_event(
                batch,
                "TARGET_M5_SUMMARIZATION_STARTED",
                target_id=target_id,
                status="SUMMARIZING",
            )
            execution = engine.run_target(copy.deepcopy(target))
            target["m5_execution"] = execution
            target["status"] = execution["status"]
            target["search_complete"] = execution["search_complete"]
            target["technical_search_complete"] = execution[
                "technical_search_complete"
            ]
            target["candidate_counts"] = copy.deepcopy(
                execution["candidate_counts"]
            )
            target["top10"] = copy.deepcopy(execution["top10"])
            target["result_summary"] = copy.deepcopy(
                execution["result_summary"]
            )
            target["incomplete_reasons"] = copy.deepcopy(
                execution["incomplete_reasons"]
            )
            target["recommendations"] = copy.deepcopy(
                execution["recommendations"]
            )
            self._record_scientific_artifact(
                batch,
                stage="M5",
                tools=["WF01_DECISION_ENGINE"],
                raw_data=execution,
                target_id=target_id,
            )
            target["updated_at"] = utc_now()
            target_results.append(execution)
            self._append_event(
                batch,
                "TARGET_M5_SUMMARIZED",
                target_id=target_id,
                status=execution["status"],
                status_reason=execution["status_reason"],
                deliverable_candidate_count=execution["candidate_counts"][
                    "deliverable"
                ],
                search_complete=execution["search_complete"],
                technical_search_complete=execution[
                    "technical_search_complete"
                ],
            )
            batch["updated_at"] = utc_now()
            self.store.save(batch)

        summary = engine.summarize_batch(target_results)
        batch["m5_execution"] = summary
        batch["m5_outcome"] = "COMPLETED"
        batch["status"] = summary["status"]
        batch["target_counts"] = copy.deepcopy(summary["target_counts"])
        batch["candidate_counts"] = copy.deepcopy(summary["candidate_counts"])
        batch["target_status_matrix"] = copy.deepcopy(
            summary["target_status_matrix"]
        )
        batch["search_complete"] = summary["search_complete"]
        batch["technical_search_complete"] = summary[
            "technical_search_complete"
        ]
        batch["search_incomplete_reason"] = (
            None
            if summary["search_complete"]
            else "ONE_OR_MORE_TARGET_SEARCHES_INCOMPLETE"
        )
        batch["result_summary"] = copy.deepcopy(summary["result_summary"])
        batch["recommendations"] = copy.deepcopy(
            summary["recommendations"]
        )
        batch["updated_at"] = utc_now()
        self._append_event(
            batch,
            "M5_SUMMARIZATION_COMPLETED",
            status=summary["status"],
            status_reason=summary["status_reason"],
            target_counts=copy.deepcopy(summary["target_counts"]),
            candidate_counts=copy.deepcopy(summary["candidate_counts"]),
            search_complete=summary["search_complete"],
            technical_search_complete=summary[
                "technical_search_complete"
            ],
            reused_targets=sum(
                self._is_m7_reused(target)
                for target in batch.get("targets", [])
            ),
        )
        self._record_scientific_artifact(
            batch,
            stage="M5",
            tools=["WF01_DECISION_ENGINE"],
            raw_data=summary,
        )
        self.store.save(batch)
        return batch

    def execute_m6(
        self,
        batch_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Assemble, validate and render the immutable complete-batch report."""
        from .wf01_v02_m6 import (
            WF01V02CanonicalReportAssembler,
            render_available_exports,
        )

        options = {} if options is None else options
        if not isinstance(options, dict):
            raise InputValidationError("M6 运行选项必须为对象")
        if options:
            raise InputValidationError(
                "M6 没有可由调用方修改的数据范围或导出选项",
                details={"unknown_options": sorted(options)},
            )

        batch = self.store.get(batch_id)
        self._assert_no_pending_user_interactions(
            batch,
            requested_stage="M6",
        )
        if batch.get("m6_execution"):
            raise InputValidationError(
                "该批次已经执行过 M6；本期不允许原地覆盖报告",
                details={"batch_id": batch_id},
            )
        if batch.get("m5_outcome") != "COMPLETED":
            raise InputValidationError(
                "只有已完成 M5 状态汇总的批次可以进入 M6",
                details={
                    "batch_id": batch_id,
                    "status": batch.get("status"),
                    "m5_outcome": batch.get("m5_outcome"),
                },
            )
        scientific_status = str(batch.get("status") or "")
        batch["status"] = "REPORTING"
        batch["updated_at"] = utc_now()
        self._append_event(
            batch,
            "M6_REPORT_GENERATION_STARTED",
            status="REPORTING",
            scientific_result_status=scientific_status,
            data_scope="COMPLETE_BATCH",
        )
        self.store.save(batch)

        try:
            report_source = copy.deepcopy(batch)
            report_source["status"] = scientific_status
            report = WF01V02CanonicalReportAssembler().assemble(
                report_source
            )
            if report["completeness"]["status"] != "COMPLETE":
                raise SchemaValidationError(
                    "M6 canonical report 完整性校验未通过",
                    details=copy.deepcopy(report["completeness"]),
                )
            report_path = self.store.save_report(batch_id, report)
            render_source = copy.deepcopy(report)
            render_source["_scientific_artifact_manifest"] = (
                self.store.list_scientific_artifacts(batch_id)
            )
            rendered, export_failures = render_available_exports(render_source)
            export_metadata: dict[str, dict[str, Any]] = {}
            for format_name, value in rendered.items():
                try:
                    path = self.store.save_export(
                        batch_id,
                        format_name,
                        value["content"],
                        filename=self._export_filename(
                            report, format_name
                        ),
                    )
                except Exception as exc:
                    export_failures[format_name] = {
                        "code": "EXPORT_PERSIST_FAILED",
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:500],
                    }
                    continue
                export_metadata[format_name] = self._export_metadata(
                    batch_id, format_name, path, value
                )
                try:
                    export_metadata[format_name] = (
                        self._publish_export_metadata(
                            batch_id,
                            path,
                            export_metadata[format_name],
                        )
                    )
                except Exception as exc:
                    export_metadata.pop(format_name, None)
                    export_failures[format_name] = {
                        "code": "EXPORT_PUBLISH_FAILED",
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:500],
                    }
            required_exports = {
                "html",
                "process_html",
                "scientific_data",
            }
            missing_required = sorted(required_exports - set(export_metadata))
            if missing_required:
                raise PersistenceError(
                    "M6 默认报告附件未能全部生成并发布",
                    details={
                        "batch_id": batch_id,
                        "missing_formats": missing_required,
                        "failures": copy.deepcopy(export_failures),
                    },
                )
        except AgentDError as exc:
            batch = self.store.get(batch_id)
            batch["status"] = scientific_status
            batch["updated_at"] = utc_now()
            self._append_event(
                batch,
                "M6_REPORT_GENERATION_FAILED",
                status=scientific_status,
                error=exc.to_dict(),
            )
            self.store.save(batch)
            raise
        except Exception as exc:
            batch = self.store.get(batch_id)
            batch["status"] = scientific_status
            batch["updated_at"] = utc_now()
            self._append_event(
                batch,
                "M6_REPORT_GENERATION_FAILED",
                status=scientific_status,
                error={
                    "code": "M6_RENDER_ERROR",
                    "message": str(exc),
                    "details": {},
                },
            )
            self.store.save(batch)
            raise PersistenceError(
                "M6 报告生成失败",
                details={"batch_id": batch_id, "error": str(exc)},
            ) from exc

        batch = self.store.get(batch_id)
        batch["status"] = scientific_status
        batch["report_id"] = report["report_id"]
        batch["report_content_sha256"] = report["content_sha256"]
        batch["m6_outcome"] = "COMPLETED"
        export_status = (
            "COMPLETE"
            if not export_failures
            else "PARTIAL"
            if export_metadata
            else "FAILED"
        )
        batch["m6_execution"] = {
            "stage": "M6_COMPLETE",
            "engine_version": report["generator"]["engine_version"],
            "report_id": report["report_id"],
            "canonical_report_schema_version": report["schema_version"],
            "canonical_content_sha256": report["content_sha256"],
            "canonical_report_filename": report_path.name,
            "data_scope": "COMPLETE_BATCH",
            "target_filter_applied": False,
            "completeness": copy.deepcopy(report["completeness"]),
            "exports": export_metadata,
            "export_status": export_status,
            "export_failures": copy.deepcopy(export_failures),
            "generated_at": report["generated_at"],
        }
        self._record_scientific_artifact(
            batch,
            stage="M6",
            tools=["WF01_REPORT_ASSEMBLER"],
            raw_data={
                "canonicalReport": report,
                "execution": batch["m6_execution"],
            },
        )
        batch["updated_at"] = utc_now()
        self._append_event(
            batch,
            "M6_REPORT_GENERATION_COMPLETED",
            status=scientific_status,
            report_id=report["report_id"],
            canonical_content_sha256=report["content_sha256"],
            export_formats=sorted(export_metadata),
            export_status=export_status,
            export_failures=copy.deepcopy(export_failures),
            data_scope="COMPLETE_BATCH",
        )
        self.store.save(batch)
        return batch

    def get_report(
        self,
        batch_id: str,
        *,
        target_id: str | None = None,
        candidate_scope: str = "top10",
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """Return the complete report or a non-mutating online view envelope."""
        report = self.store.get_report(batch_id)
        if target_id is None:
            return report
        if page < 1 or page_size < 1 or page_size > 500:
            raise InputValidationError(
                "报告分页参数超出范围",
                details={
                    "page": page,
                    "page_size": page_size,
                    "maximum_page_size": 500,
                },
            )
        scopes = {
            "top10": "top10",
            "deliverable": "all_deliverable_candidates",
            "qualified": "all_qualified_candidates",
            "excluded": "all_excluded_candidates",
            "all": "all_primer3_candidates",
        }
        if candidate_scope not in scopes:
            raise InputValidationError(
                "不支持的报告候选视图",
                details={
                    "candidate_scope": candidate_scope,
                    "supported": sorted(scopes),
                },
            )
        selected = next(
            (
                item
                for item in report.get("targets") or []
                if item.get("target_id") == target_id
            ),
            None,
        )
        if selected is None:
            raise InputValidationError(
                "报告中不存在所请求的靶标",
                details={"batch_id": batch_id, "target_id": target_id},
            )
        field = scopes[candidate_scope]
        candidates = selected.get(field) or []
        start = (page - 1) * page_size
        stop = start + page_size
        target_view = copy.deepcopy(selected)
        for collection in (
            "top10",
            "all_qualified_candidates",
            "all_deliverable_candidates",
            "all_excluded_candidates",
            "all_specificity_incomplete_candidates",
            "all_primer3_candidates",
            "primer3_candidate_occurrences",
        ):
            target_view.pop(collection, None)
        target_view["candidate_view"] = {
            "scope": candidate_scope,
            "items": copy.deepcopy(candidates[start:stop]),
            "page": page,
            "page_size": page_size,
            "total": len(candidates),
        }
        return {
            "view_schema_version": "wf01-prd-v0.2-report-view-v1",
            "canonical_report_id": report["report_id"],
            "canonical_content_sha256": report["content_sha256"],
            "canonical_target_total": report["batch_overview"][
                "target_total"
            ],
            "view_filter": {
                "target_id": target_id,
                "candidate_scope": candidate_scope,
                "page": page,
                "page_size": page_size,
            },
            "export_data_scope": "COMPLETE_BATCH",
            "target": target_view,
        }

    def get_export_path(self, batch_id: str, format_name: str) -> Path:
        """Return an export, generating optional CSV/PDF on first request."""
        from .wf01_v02_m6 import render_available_exports

        selected = str(format_name).lower()
        supported = {
            "html",
            "process_html",
            "rules_html",
            "csv",
            "pdf",
            "scientific_data",
        }
        if selected not in supported:
            raise InputValidationError(
                "不支持的 M6 导出格式",
                details={"format": format_name, "supported": sorted(supported)},
            )
        batch = self.store.get(batch_id)
        batch_id = str(batch["batch_id"])
        m6 = batch.get("m6_execution") or {}
        if batch.get("m6_outcome") != "COMPLETED" or not m6:
            raise InputValidationError(
                "该批次尚未完成 M6 报告生成",
                details={"batch_id": batch_id},
            )
        existing = (m6.get("exports") or {}).get(selected)
        if isinstance(existing, dict) and existing.get("filename"):
            path = self.store.get_export_path(
                batch_id,
                selected,
                filename=str(existing["filename"]),
            )
            publication = existing.get("object_storage") or {}
            if (
                self.artifact_publisher.enabled
                and publication.get("provider") != "rustfs"
            ):
                refreshed_metadata = self._publish_export_metadata(
                    batch_id,
                    path,
                    copy.deepcopy(existing),
                )
                batch = self.store.get(batch_id)
                m6 = batch.setdefault("m6_execution", {})
                m6.setdefault("exports", {})[selected] = refreshed_metadata
                batch["updated_at"] = utc_now()
                self._append_event(
                    batch,
                    "M6_EXPORT_PUBLISHED_TO_RUSTFS",
                    format=selected,
                    filename=path.name,
                    object_key=(
                        refreshed_metadata.get("object_storage") or {}
                    ).get("object_key"),
                )
                self.store.save(batch)
            return path
        if selected == "html":
            raise InputValidationError(
                "该批次的 HTML 主报告不可用",
                details={"batch_id": batch_id},
            )
        if selected == "rules_html":
            raise InputValidationError(
                "21条规则验证报告已收纳在 ZIP 数据包中，不再提供新的独立导出",
                details={
                    "batch_id": batch_id,
                    "requested_format": selected,
                    "replacement_format": "scientific_data",
                },
            )

        if selected == "scientific_data":
            report = self.store.get_report(batch_id)
            path = self.store.save_scientific_artifact_archive(
                batch_id,
                filename=self._export_filename(report, selected),
            )
            metadata = self._export_metadata(
                batch_id,
                selected,
                path,
                {
                    "media_type": "application/zip",
                    "size_bytes": path.stat().st_size,
                    "sha256": self._file_sha256(path),
                    "canonical_report_id": report["report_id"],
                    "canonical_content_sha256": report["content_sha256"],
                },
            )
            metadata = self._publish_export_metadata(
                batch_id,
                path,
                metadata,
            )
            batch = self.store.get(batch_id)
            m6 = batch.setdefault("m6_execution", {})
            m6.setdefault("exports", {})[selected] = metadata
            (m6.get("export_failures") or {}).pop(selected, None)
            batch["updated_at"] = utc_now()
            self._append_event(
                batch,
                "M6_EXPORT_GENERATED_ON_DEMAND",
                format=selected,
                filename=path.name,
                canonical_content_sha256=report["content_sha256"],
            )
            self.store.save(batch)
            return path

        report = self.store.get_report(batch_id)
        render_source = copy.deepcopy(report)
        if selected == "process_html":
            render_source["_scientific_artifact_manifest"] = (
                self.store.list_scientific_artifacts(batch_id)
            )
        rendered, failures = render_available_exports(render_source, [selected])
        failure = failures.get(selected)
        if failure is not None:
            raise PersistenceError(
                f"{selected.upper()} 报告按需生成失败",
                details={"batch_id": batch_id, **failure},
            )
        value = rendered[selected]
        path = self.store.save_export(
            batch_id,
            selected,
            value["content"],
            filename=self._export_filename(report, selected),
        )
        metadata = self._export_metadata(
            batch_id, selected, path, value
        )
        metadata = self._publish_export_metadata(
            batch_id,
            path,
            metadata,
        )
        batch = self.store.get(batch_id)
        m6 = batch.setdefault("m6_execution", {})
        m6.setdefault("exports", {})[selected] = metadata
        (m6.get("export_failures") or {}).pop(selected, None)
        batch["updated_at"] = utc_now()
        self._append_event(
            batch,
            "M6_EXPORT_GENERATED_ON_DEMAND",
            format=selected,
            filename=path.name,
            canonical_content_sha256=value["canonical_content_sha256"],
        )
        self.store.save(batch)
        return path

    def get_export_metadata(
        self,
        batch_id: str,
        format_name: str,
    ) -> dict[str, Any]:
        """Return persisted publication metadata for one generated export."""
        selected = str(format_name).lower()
        batch = self.store.get(batch_id)
        batch_id = str(batch["batch_id"])
        metadata = (
            ((batch.get("m6_execution") or {}).get("exports") or {}).get(
                selected
            )
        )
        if not isinstance(metadata, dict) or not metadata.get("file_url"):
            raise InputValidationError(
                "该批次尚未发布所请求的 M6 导出文件",
                details={"batch_id": batch_id, "format": selected},
            )
        result = copy.deepcopy(metadata)
        result["batch_code"] = str(
            batch.get("batch_code")
            or wf01_batch_code(
                str(batch.get("batch_id") or batch_id),
                batch.get("created_at"),
                workflow=str(batch.get("workflow") or "WF-01"),
            )
        )
        return result

    def get_export_path_by_filename(self, filename: str) -> Path:
        """Resolve the flat `/data/<filename>` attachment compatibility path."""
        if Path(filename).name != filename or not filename:
            raise InputValidationError(
                "报告附件文件名无效", details={"filename": filename}
            )
        matches: list[tuple[int, str, str]] = []
        for batch_path in self.store.root.glob("*.json"):
            try:
                batch = json.loads(batch_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise PersistenceError(
                    "WF-01 报告附件索引读取失败",
                    details={"path": str(batch_path), "error": str(exc)},
                ) from exc
            for format_name, metadata in (
                ((batch.get("m6_execution") or {}).get("exports") or {})
            ).items():
                if (
                    isinstance(metadata, dict)
                    and metadata.get("filename") == filename
                ):
                    batch_id = str(batch.get("batch_id"))
                    try:
                        export_path = self.store.get_export_path(
                            batch_id,
                            format_name,
                            filename=filename,
                        )
                    except (InputValidationError, OSError):
                        # Ignore stale metadata whose source file is gone.
                        continue
                    matches.append(
                        (
                            export_path.stat().st_mtime_ns,
                            batch_id,
                            format_name,
                        )
                    )
        if not matches:
            raise InputValidationError(
                "报告附件不存在", details={"filename": filename}
            )
        # This flat URL is a short-lived hand-off endpoint: the host fetches
        # the bytes immediately and stores them in object storage. Repeated
        # runs may reuse the same user-facing filename, so serve the newest
        # real file instead of rejecting all duplicates as ambiguous.
        _, batch_id, format_name = max(matches)
        return self.store.get_export_path(
            batch_id,
            format_name,
            filename=filename,
        )

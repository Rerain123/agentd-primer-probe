"""HiMat uploaded-file intake without exposing file bodies to the model."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from email.message import Message
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from .errors import InputValidationError, PersistenceError, ToolUnavailableError
from .sequence import read_fasta


DEFAULT_HIMAT_DOWNLOAD_BASE_URL = (
    "https://himat.ws.scqcyz.cn/api/files/download"
)
DEFAULT_MAX_FILE_BYTES = 1024 * 1024 * 1024
ARTIFACT_ID_PATTERN = re.compile(r"himat-[0-9a-f]{32}")


class HiMatInputArtifactStore:
    """Download HiMat files and keep their filesystem paths server-side."""

    def __init__(
        self,
        state_root: str | Path,
        *,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        max_file_bytes: int | None = None,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.root = Path(state_root) / "input-artifacts"
        self.base_url = (
            base_url
            or os.environ.get("HIMAT_FILE_DOWNLOAD_BASE_URL")
            or DEFAULT_HIMAT_DOWNLOAD_BASE_URL
        ).rstrip("/")
        self.timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else os.environ.get("HIMAT_FILE_DOWNLOAD_TIMEOUT_SECONDS", "300")
        )
        self.max_file_bytes = int(
            max_file_bytes
            if max_file_bytes is not None
            else os.environ.get(
                "AGENTD_INPUT_FILE_MAX_BYTES",
                str(DEFAULT_MAX_FILE_BYTES),
            )
        )
        self._opener = opener or urlopen

    @staticmethod
    def _validate_file_id(file_id: int) -> int:
        if isinstance(file_id, bool) or not isinstance(file_id, int) or file_id <= 0:
            raise InputValidationError(
                "file_id 必须为正整数",
                details={"file_id": file_id},
            )
        return file_id

    @staticmethod
    def _safe_filename(content_disposition: str, file_id: int) -> str:
        filename = ""
        if content_disposition:
            message = Message()
            message["content-disposition"] = content_disposition
            filename = message.get_filename() or ""
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
        filename = filename.replace("\x00", "")
        if filename in {"", ".", ".."}:
            filename = ""
        return filename or f"file_{file_id}"

    @staticmethod
    def _content_type(headers: Any) -> str:
        value = str(headers.get("content-type", "") or "")
        return value.split(";", 1)[0].strip().lower() or "application/octet-stream"

    @staticmethod
    def _write_json(path: Path, document: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    def download(self, file_id: int) -> dict[str, Any]:
        """Download a HiMat file and return metadata only."""
        selected_file_id = self._validate_file_id(file_id)
        artifact_id = f"himat-{uuid4().hex}"
        url = f"{self.base_url}/{selected_file_id}"
        request = Request(url, headers={"User-Agent": "OligoSmith/HiMat-file-intake"})
        artifact_directory = self.root / artifact_id
        partial_path: Path | None = None

        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                filename = self._safe_filename(
                    str(response.headers.get("content-disposition", "") or ""),
                    selected_file_id,
                )
                content_type = self._content_type(response.headers)
                artifact_directory.mkdir(parents=True, exist_ok=False)
                destination = artifact_directory / filename
                partial_path = destination.with_name(destination.name + ".part")
                digest = hashlib.sha256()
                size_bytes = 0

                with partial_path.open("wb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        size_bytes += len(chunk)
                        if size_bytes > self.max_file_bytes:
                            raise InputValidationError(
                                "HiMat 文件超过工具允许的最大大小",
                                details={
                                    "file_id": selected_file_id,
                                    "max_file_bytes": self.max_file_bytes,
                                },
                            )
                        output.write(chunk)
                        digest.update(chunk)

                partial_path.replace(destination)
                manifest = {
                    "input_artifact_id": artifact_id,
                    "source_file_id": selected_file_id,
                    "file_name": filename,
                    "content_type": content_type,
                    "size_bytes": size_bytes,
                    "sha256": digest.hexdigest(),
                    "status": "READY",
                }
                self._write_json(artifact_directory / "manifest.json", manifest)
                return copy.deepcopy(manifest)
        except HTTPError as exc:
            raise InputValidationError(
                "HiMat 文件下载失败",
                details={"file_id": selected_file_id, "http_status": exc.code},
            ) from exc
        except URLError as exc:
            raise ToolUnavailableError(
                "暂时无法连接 HiMat 文件服务",
                details={"file_id": selected_file_id, "reason": str(exc.reason)},
            ) from exc
        except (InputValidationError, ToolUnavailableError):
            raise
        except OSError as exc:
            raise PersistenceError(
                "HiMat 输入文件保存失败",
                details={"file_id": selected_file_id, "error": str(exc)},
            ) from exc
        finally:
            if partial_path is not None:
                partial_path.unlink(missing_ok=True)

    def store_inline_fasta(
        self,
        fasta_text: str,
        *,
        file_name: str = "直接输入序列.fasta",
    ) -> dict[str, Any]:
        """Persist pasted sequence text without returning its body to the model.

        Inline sequence input uses the same opaque reference mechanism as a
        downloaded HiMat file. A content-derived identifier makes repeated REST
        preparation idempotent while the MCP call guard still suppresses model
        retries in one turn.
        """
        if not isinstance(fasta_text, str) or not fasta_text.strip():
            raise InputValidationError("直接输入的序列不能为空")
        body = fasta_text.encode("utf-8")
        if len(body) > self.max_file_bytes:
            raise InputValidationError(
                "直接输入的序列超过工具允许的最大大小",
                details={"max_file_bytes": self.max_file_bytes},
            )
        safe_name = Path(str(file_name).replace("\\", "/")).name.strip()
        if safe_name in {"", ".", ".."}:
            safe_name = "直接输入序列.fasta"
        digest = hashlib.sha256(body).hexdigest()
        artifact_id = f"himat-{digest[:32]}"
        artifact_directory = self.root / artifact_id
        destination = artifact_directory / safe_name
        manifest_path = artifact_directory / "manifest.json"
        manifest = {
            "input_artifact_id": artifact_id,
            "source_file_id": None,
            "source_type": "INLINE_SEQUENCE",
            "file_name": safe_name,
            "content_type": "text/x-fasta",
            "size_bytes": len(body),
            "sha256": digest,
            "status": "READY",
        }
        try:
            artifact_directory.mkdir(parents=True, exist_ok=True)
            if destination.is_file() and manifest_path.is_file():
                _path, existing = self.resolve(artifact_id)
                if existing.get("sha256") == digest:
                    return copy.deepcopy(existing)
            partial_path = destination.with_name(destination.name + ".part")
            partial_path.write_bytes(body)
            partial_path.replace(destination)
            self._write_json(manifest_path, manifest)
            return copy.deepcopy(manifest)
        except OSError as exc:
            raise PersistenceError(
                "直接输入序列保存失败",
                details={"error": str(exc)},
            ) from exc

    def resolve(self, artifact_id: str) -> tuple[Path, dict[str, Any]]:
        """Resolve an artifact internally; never expose this path as a tool result."""
        if not isinstance(artifact_id, str) or not ARTIFACT_ID_PATTERN.fullmatch(
            artifact_id
        ):
            raise InputValidationError(
                "input_artifact_id 格式无效",
                details={"input_artifact_id": artifact_id},
            )
        artifact_directory = self.root / artifact_id
        manifest_path = artifact_directory / "manifest.json"
        if not manifest_path.is_file():
            raise InputValidationError(
                "输入文件引用不存在或已失效",
                details={"input_artifact_id": artifact_id},
            )
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PersistenceError(
                "输入文件引用清单无法读取",
                details={"input_artifact_id": artifact_id},
            ) from exc
        filename = str(manifest.get("file_name") or "")
        if not filename or Path(filename).name != filename:
            raise PersistenceError(
                "输入文件引用清单包含非法文件名",
                details={"input_artifact_id": artifact_id},
            )
        path = artifact_directory / filename
        if not path.is_file():
            raise InputValidationError(
                "输入文件引用对应的文件不存在",
                details={"input_artifact_id": artifact_id},
            )
        return path, manifest

    def materialize_wf01_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Load referenced FASTA internally for WF-01 deterministic parsing."""
        if not isinstance(payload, dict):
            raise InputValidationError("payload 必须为对象")
        materialized = copy.deepcopy(payload)
        top_level_artifact = materialized.pop("input_artifact_id", None)
        sequence_files = materialized.get("sequence_files")

        if top_level_artifact is not None:
            if sequence_files not in (None, [], {}):
                raise InputValidationError(
                    "input_artifact_id 不能与 sequence_files 同时提供",
                    details={"conflicting_fields": ["input_artifact_id", "sequence_files"]},
                )
            materialized["sequence_files"] = [
                self._wf01_sequence_file(str(top_level_artifact))
            ]
            return materialized

        if sequence_files is None:
            return materialized
        if not isinstance(sequence_files, list):
            return materialized

        resolved_files: list[Any] = []
        for item in sequence_files:
            if not isinstance(item, dict):
                resolved_files.append(item)
                continue
            selected = copy.deepcopy(item)
            artifact_id = selected.pop("input_artifact_id", None)
            if artifact_id is None:
                artifact_id = selected.pop("artifact_id", None)
            if artifact_id is None:
                resolved_files.append(selected)
                continue
            if selected.get("fasta_text") is not None or selected.get("content") is not None:
                raise InputValidationError(
                    "文件引用不能与 FASTA 正文同时提供",
                    details={"input_artifact_id": artifact_id},
                )
            hydrated = self._wf01_sequence_file(str(artifact_id))
            hydrated.update(selected)
            resolved_files.append(hydrated)
        materialized["sequence_files"] = resolved_files
        return materialized

    def materialize_wf03_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Map one opaque upload to WF-03's path-based P5 input.

        The model passes only ``input_artifact_id`` and an optional explicit
        ``input_gene`` label.  The filesystem path is resolved here and is
        never returned by the MCP download tool.
        """
        if not isinstance(payload, dict):
            raise InputValidationError("payload 必须为对象")
        materialized = copy.deepcopy(payload)
        artifact_id = materialized.pop("input_artifact_id", None)
        input_gene = materialized.pop("input_gene", None)
        if artifact_id is None:
            if input_gene not in (None, ""):
                raise InputValidationError(
                    "input_gene 只能与 input_artifact_id 一起提供",
                    details={"required_field": "input_artifact_id"},
                )
            return materialized

        path, manifest = self.resolve(str(artifact_id))
        selected_gene = str(input_gene or "").strip()
        preferred = materialized.get("preferred_genes")
        if not selected_gene and isinstance(preferred, list) and len(preferred) == 1:
            preferred_item = preferred[0]
            if isinstance(preferred_item, str):
                selected_gene = preferred_item.strip()
            elif isinstance(preferred_item, dict):
                selected_gene = str(
                    preferred_item.get("gene")
                    or preferred_item.get("name")
                    or preferred_item.get("symbol")
                    or ""
                ).strip()
        if not selected_gene:
            selected_gene = Path(str(manifest.get("file_name") or "")).stem.strip()
        if not selected_gene:
            selected_gene = "uploaded_input"

        gene_paths = materialized.get("gene_fasta_paths")
        if gene_paths is None:
            gene_paths = {}
        if not isinstance(gene_paths, dict):
            raise InputValidationError("gene_fasta_paths 必须为对象")
        if selected_gene in gene_paths:
            raise InputValidationError(
                "上传附件与 gene_fasta_paths 使用了相同的输入标签",
                details={"input_gene": selected_gene},
            )
        gene_sequences = materialized.get("gene_sequences")
        if isinstance(gene_sequences, dict) and selected_gene in gene_sequences:
            raise InputValidationError(
                "上传附件与 gene_sequences 使用了相同的输入标签",
                details={"input_gene": selected_gene},
            )
        materialized["gene_fasta_paths"] = {
            **copy.deepcopy(gene_paths),
            selected_gene: str(path),
        }

        if preferred is None:
            materialized["preferred_genes"] = [selected_gene]
        elif isinstance(preferred, list):
            preferred_names = []
            for item in preferred:
                if isinstance(item, str):
                    preferred_names.append(item.strip())
                elif isinstance(item, dict):
                    preferred_names.append(
                        str(
                            item.get("gene")
                            or item.get("name")
                            or item.get("symbol")
                            or ""
                        ).strip()
                    )
            if selected_gene not in preferred_names:
                materialized["preferred_genes"] = [
                    selected_gene,
                    *copy.deepcopy(preferred),
                ]

        materialized["_wf03_input_artifact"] = {
            "input_artifact_id": str(artifact_id),
            "source_file_id": manifest.get("source_file_id"),
            "file_name": manifest.get("file_name"),
            "sha256": manifest.get("sha256"),
            "input_gene": selected_gene,
        }
        return materialized

    @staticmethod
    def _wf04_record_field(record: Any) -> str | None:
        label = " ".join(
            str(value or "")
            for value in (getattr(record, "id", ""), getattr(record, "description", ""))
        ).lower()
        tokens = {
            token
            for token in re.split(r"[^a-z0-9]+", label)
            if token
        }
        aliases = {
            "forward_primer": {"forward", "forwardprimer", "fwd", "left"},
            "reverse_primer": {"reverse", "reverseprimer", "rev", "right"},
            "probe": {"probe"},
            "template": {"template", "context", "reference"},
        }
        for field, accepted in aliases.items():
            if tokens & accepted:
                return field
        return None

    def materialize_wf04_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Map a WF-01-style opaque upload to deterministic WF-04 fields.

        Named FASTA records use ``forward``, ``reverse``, ``probe`` and
        ``template`` aliases.  A fully file-driven unnamed input may use the
        same positional order.  When both required inline values are already
        present, one unnamed record is treated as the optional template.
        """

        if not isinstance(payload, dict):
            raise InputValidationError("payload 必须为对象")
        materialized = copy.deepcopy(payload)
        artifact_id = materialized.pop("input_artifact_id", None)
        if artifact_id is None:
            return materialized

        path, manifest = self.resolve(str(artifact_id))
        records = read_fasta(path)
        mapped: dict[str, str] = {}
        unnamed: list[Any] = []
        for record in records:
            field = self._wf04_record_field(record)
            if field is None:
                unnamed.append(record)
                continue
            if field in mapped:
                raise InputValidationError(
                    "WF-04附件包含重复输入字段",
                    details={"field": field, "input_artifact_id": artifact_id},
                )
            mapped[field] = str(record.sequence)

        if not mapped:
            missing_core = [
                field
                for field in ("forward_primer", "reverse_primer")
                if not str(materialized.get(field) or "").strip()
            ]
            if len(missing_core) == 2 and 2 <= len(records) <= 4:
                positional = (
                    "forward_primer",
                    "reverse_primer",
                    "probe",
                    "template",
                )
                mapped = {
                    field: str(record.sequence)
                    for field, record in zip(positional, records)
                }
                unnamed = []
            elif len(missing_core) == 1 and len(records) == 1:
                mapped[missing_core[0]] = str(records[0].sequence)
                unnamed = []
            elif not missing_core and len(records) == 1:
                mapped["template"] = str(records[0].sequence)
                unnamed = []

        if unnamed:
            missing_core = [
                field
                for field in ("forward_primer", "reverse_primer")
                if not str(materialized.get(field) or mapped.get(field) or "").strip()
            ]
            if missing_core and len(unnamed) == len(missing_core):
                for field, record in zip(missing_core, unnamed):
                    mapped[field] = str(record.sequence)
                unnamed = []

        if unnamed:
            if (
                len(unnamed) == 1
                and "template" not in mapped
                and not str(materialized.get("template") or "").strip()
                and all(
                    str(materialized.get(field) or mapped.get(field) or "").strip()
                    for field in ("forward_primer", "reverse_primer")
                )
            ):
                mapped["template"] = str(unnamed[0].sequence)
            else:
                raise InputValidationError(
                    "WF-04附件记录名称无法确定性映射",
                    details={
                        "input_artifact_id": artifact_id,
                        "accepted_record_names": [
                            "forward",
                            "reverse",
                            "probe",
                            "template",
                        ],
                        "unmapped_records": [
                            str(getattr(record, "id", "")) for record in unnamed
                        ],
                    },
                )

        for field, value in mapped.items():
            existing = str(materialized.get(field) or "").strip()
            if existing and re.sub(r"\s+", "", existing).upper() != value.upper():
                raise InputValidationError(
                    "WF-04附件字段与内联字段冲突",
                    details={"field": field, "input_artifact_id": artifact_id},
                )
            materialized[field] = value

        materialized["_wf04_input_artifact"] = {
            "input_artifact_id": str(artifact_id),
            "source_file_id": manifest.get("source_file_id"),
            "file_name": manifest.get("file_name"),
            "sha256": manifest.get("sha256"),
            "record_count": len(records),
            "mapped_fields": sorted(mapped),
        }
        return materialized

    def attach_path(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Resolve input_artifact_id to fasta_path for path-based workflows."""
        if not isinstance(payload, dict):
            raise InputValidationError("payload 必须为对象")
        attached = copy.deepcopy(payload)
        artifact_id = attached.pop("input_artifact_id", None)
        if artifact_id is None:
            return attached
        if attached.get("fasta_path"):
            raise InputValidationError(
                "input_artifact_id 不能与 fasta_path 同时提供",
                details={"conflicting_fields": ["input_artifact_id", "fasta_path"]},
            )
        path, _manifest = self.resolve(str(artifact_id))
        attached["fasta_path"] = str(path)
        return attached

    def _wf01_sequence_file(self, artifact_id: str) -> dict[str, Any]:
        path, manifest = self.resolve(artifact_id)
        try:
            content = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise InputValidationError(
                "HiMat 输入文件不是 UTF-8 文本",
                details={"input_artifact_id": artifact_id},
            ) from exc
        except OSError as exc:
            raise PersistenceError(
                "HiMat 输入文件无法读取",
                details={"input_artifact_id": artifact_id},
            ) from exc
        return {
            "file_name": manifest["file_name"],
            "fasta_text": content,
            "source_file_id": manifest["source_file_id"],
            "sha256": manifest["sha256"],
        }

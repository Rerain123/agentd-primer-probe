"""WF-02 v0.1 intake, target design, and internal-control orchestration."""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, RLock
from typing import Any, Callable, Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from .attachment_urls import build_wf02_attachment_url
from .config import config_fingerprint
from .errors import AgentDError, InputValidationError, PersistenceError
from .identifiers import wf01_batch_code
from .input_artifacts import ARTIFACT_ID_PATTERN
from .interactions import build_interaction
from .multiplex import (
    FLUOROPHORE_DB,
    INSTRUMENT_DB,
    recommended_quencher,
)
from .object_storage import RustFSArtifactPublisher
from .sequence import gc_percent
from .workflow_contracts import (
    select_wf01_candidates,
    wf01_candidate_contract_manifest,
)
from .wf02_v01_report import (
    WF02_EXPORT_RENDERER_VERSIONS,
    render_wf02_csv,
    render_wf02_html,
    render_wf02_pdf,
)
from .wf02_runtime import (
    WF02_BACKGROUND_ACTIVE_STATES,
    WF02_BACKGROUND_EXECUTION_VERSION,
)


WF02_WORKFLOW_VERSION = "wf02-v0.1"
WF02_POLICY_VERSION = "wf02-policy-v0.1"
WF02_PREFLIGHT_SCHEMA_VERSION = "wf02-v0.1-intake-preflight-v1"
WF02_BATCH_SCHEMA_VERSION = "wf02-v0.1-batch-v1"
WF02_DRAFT_SCHEMA_VERSION = "wf02-v0.1-input-draft-v1"
MINIMUM_TARGETS = 2
MAXIMUM_TARGETS = 5
WF02_NODES = [
    "N01", "N02", "N03", "N04", "N05", "N06", "N07", "N08",
    "N09", "N10", "N11", "N12", "N13", "N14", "N15", "N16",
    "N16A", "N17", "N18", "N19", "N20", "N21", "N22", "N23",
    "N24", "N25", "N26", "N27", "N28", "N29", "N30", "N31", "N32",
]
WF02_INTERNAL_CHANNEL_POLICY_VERSION = "wf02-ic-channel-dynamic-v2"
WF02_TARGET_FILE_POLICY_VERSION = "wf02-one-file-per-target-v1"
WF02_INTERNAL_DEFAULT_FLUOROPHORE = "Cy5"
WF02_PANEL_CHANNEL_POLICY_VERSION = "wf02-channel-input-order-dynamic-ic-v2"
WF02_MINIMUM_CHANNEL_SEPARATION_NM = 15.0
WF02_TARGET_DYE_PRIORITY_WITH_ROX_REFERENCE = (
    "FAM",
    "VIC",
    "ABY",
    "JUN",
    "Cy5",
    "Cy5.5",
)
WF02_TARGET_DYE_PRIORITY_WITHOUT_ROX_REFERENCE = (
    "FAM",
    "VIC",
    "ROX",
    "Cy5",
    "Cy5.5",
)
WF02_AMPLICON_LENGTH_POLICY_VERSION = "wf02-r17-hard-filter-v2"
WF02_RELAXATION_POLICY_VERSION = "wf02-relaxation-conservative-v1"
WF02_RELAXED_DIMER_DG_KCAL = -7.0
WF02_STANDARD_TM_SPREAD_C = 3.0
WF02_RELAXED_TM_SPREAD_C = 4.0
WF02_BALANCE_POLICY_VERSION = "wf02-balance-sop-v1"
WF02_SCORING_POLICY_VERSION = "wf02-panel-score-conservative-v1"
WF02_DEDUP_POLICY_VERSION = "wf02-sequence-members-v1"
WF02_SELECTION_POLICY_VERSION = "wf02-global-score-top3-v2"
WF02_REPORT_SCHEMA_VERSION = "wf02-v0.1-report-v1"
WF02_MAX_BALANCE_REDESIGNS = 2
WF02_TOP_PANEL_LIMIT = 3
WF02_EXPORT_MEDIA_TYPES = {
    "html": "text/html; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
    "pdf": "application/pdf",
}
WF02_BOUNDED_COMBINATION_BUDGET = 8
WF02_CREATE_REPLAY_WINDOW_SECONDS = 15 * 60
WF02_CREATE_REPLAY_SCHEMA_VERSION = "wf02-create-replay-v1"
P5_HOST_ALIASES = {
    "pig": {"pig", "猪", "sus scrofa"},
    "cattle": {"cattle", "cow", "牛", "bos taurus"},
    "chicken": {"chicken", "鸡", "gallus gallus"},
}
WF02_RESOURCE_BLOCKING_REASON_CODES = {
    "TARGET_DATABASE_OR_VERSION_NOT_RESOLVED",
    "NT_DATABASE_OR_VERSION_NOT_RESOLVED",
    "HOST_DATABASE_OR_VERSION_NOT_RESOLVED",
    "TARGET_SEARCH_TOOL_ERROR",
    "NT_SEARCH_TOOL_ERROR",
    "HOST_SEARCH_TOOL_ERROR",
}
WF02_NO_CANDIDATE_REASON_CODES = {
    "PRIMER3_GENERATED_NO_CANDIDATES",
}
_REASON_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")


class WF02ServiceGateway(Protocol):
    """Public application-service operations consumed by WF-02."""

    def create_wf01_v02_batch(self, payload: dict, **kwargs: Any) -> dict: ...

    def run_wf01_v02_until_blocked(
        self,
        batch_id: str,
        stage_options: dict | None = None,
    ) -> dict: ...

    def get_wf01_v02_report(self, batch_id: str, **kwargs: Any) -> dict: ...

    def submit_wf01_v02_interaction_response(
        self,
        batch_id: str,
        response: dict,
    ) -> dict: ...

    def assess_p6_amplicon_structure(
        self,
        amplicon_sequence: str,
    ) -> dict: ...

    def design_p5_internal_control(self, payload: dict) -> dict: ...

    def check_p4_panel(self, payload: dict) -> dict: ...

    def create_wf01_tm_constrained_version(self, payload: dict) -> dict: ...



def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _p5_host_species(value: Any) -> str:
    selected = _text(value).casefold()
    for canonical, aliases in P5_HOST_ALIASES.items():
        if selected in {alias.casefold() for alias in aliases}:
            return canonical
    return selected


def _named_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return ""
    for key in (
        "name",
        "display_name",
        "normalized_name",
        "target_name",
        "target_organism",
        "identifier",
        "target_id",
    ):
        selected = value.get(key)
        if isinstance(selected, str) and selected.strip():
            return selected.strip()
    return ""


def _canonical_sha256(value: Any) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _product_window(
    pools: list[list[dict[str, Any]]],
    start: int,
    stop: int,
) -> list[tuple[dict[str, Any], ...]]:
    """Return an ``itertools.product`` slice without replaying its prefix.

    ``islice(product(...), cursor, ...)`` recreates and consumes every prior
    tuple whenever a persisted batch resumes.  Mixed-radix addressing keeps
    the exact same lexicographic order while making each window proportional
    only to the number of combinations it returns.
    """

    if not pools or any(not pool for pool in pools):
        return []
    sizes = [len(pool) for pool in pools]
    total = math.prod(sizes)
    lower = max(0, min(int(start), total))
    upper = max(lower, min(int(stop), total))
    selected: list[tuple[dict[str, Any], ...]] = []
    for linear_index in range(lower, upper):
        remainder = linear_index
        indexes = [0] * len(sizes)
        for position in range(len(sizes) - 1, -1, -1):
            remainder, indexes[position] = divmod(remainder, sizes[position])
        selected.append(
            tuple(pool[index] for pool, index in zip(pools, indexes))
        )
    return selected


def wf02_execution_progress(batch: dict[str, Any]) -> dict[str, Any]:
    """Return a compact, model-safe cursor for the active WF-02 phase."""
    multiplex = batch.get("multiplex") or {}
    relaxation = multiplex.get("relaxation") or {}
    relaxation_status = str(relaxation.get("status") or "NOT_STARTED")
    if relaxation and relaxation_status != "NOT_STARTED":
        active_step = str(relaxation.get("active_step") or "")
        progress: dict[str, Any] = {
            "phase": "RELAXATION",
            "step": active_step or None,
            "status": relaxation_status,
        }
        if active_step in {"N21", "N22"}:
            active_round = next(
                (
                    item
                    for item in relaxation.get("rounds") or []
                    if str(item.get("node") or "") == active_step
                ),
                None,
            ) or {}
            total = int(active_round.get("total_panels") or 0)
            processed = int(active_round.get("cursor") or 0)
            progress.update(
                {
                    "processed_units": processed,
                    "total_units": total,
                    "remaining_units": max(0, total - processed),
                    "accepted_units": int(
                        active_round.get("accepted_count") or 0
                    ),
                    "rejected_units": int(
                        active_round.get("rejected_count") or 0
                    ),
                }
            )
        elif active_step == "N23":
            levels = relaxation.get("partial_levels") or []
            level_index = int(
                relaxation.get("active_partial_level_index") or 0
            )
            level = levels[level_index] if level_index < len(levels) else {}
            groups = level.get("groups") or []
            total = sum(
                int(item.get("total_combinations") or 0) for item in groups
            )
            processed = sum(int(item.get("cursor") or 0) for item in groups)
            progress.update(
                {
                    "level_index": level_index,
                    "processed_units": processed,
                    "total_units": total,
                    "remaining_units": max(0, total - processed),
                    "accepted_units": sum(
                        int(item.get("accepted_count") or 0) for item in groups
                    ),
                    "rejected_units": sum(
                        int(item.get("rejected_count") or 0) for item in groups
                    ),
                    "invalid_units": sum(
                        int(item.get("invalid_count") or 0) for item in groups
                    ),
                }
            )
        return progress

    screening = multiplex.get("screening") or {}
    screening_status = str(screening.get("status") or "NOT_STARTED")
    if screening and screening_status != "NOT_STARTED":
        total = int(screening.get("total_combinations") or 0)
        processed = int(screening.get("cursor") or 0)
        return {
            "phase": "PANEL_SCREENING",
            "step": batch.get("stage"),
            "status": screening_status,
            "processed_units": processed,
            "total_units": total,
            "remaining_units": max(0, total - processed),
            "accepted_units": int(screening.get("strict_count") or 0)
            + int(screening.get("risk_count") or 0),
            "rejected_units": int(screening.get("incompatible_count") or 0),
            "invalid_units": int(screening.get("invalid_count") or 0),
        }

    target_design = batch.get("target_design") or {}
    requested = int(
        target_design.get("requested_targets")
        or len((batch.get("user_input_snapshot") or {}).get("targets") or [])
    )
    processed = (
        int(target_design.get("succeeded_targets") or 0)
        + int(target_design.get("failed_targets") or 0)
        + int(target_design.get("blocked_targets") or 0)
    )
    return {
        "phase": "TARGET_DESIGN",
        "step": batch.get("stage"),
        "status": target_design.get("status") or batch.get("status"),
        "processed_units": processed,
        "total_units": requested,
        "remaining_units": max(0, requested - processed),
    }


def wf02_resume_token(batch: dict[str, Any]) -> str:
    """Hash all persisted control cursors used by compare-and-resume."""
    multiplex = batch.get("multiplex") or {}
    screening = multiplex.get("screening") or {}
    relaxation = multiplex.get("relaxation") or {}
    balance = multiplex.get("balance") or {}
    state = {
        "batch_id": batch.get("batch_id"),
        "updated_at": batch.get("updated_at"),
        "status": batch.get("status"),
        "stage": batch.get("stage"),
        "next_action": batch.get("next_action"),
        "event_count": len(batch.get("events") or []),
        "target_states": [
            [
                item.get("target_id"),
                item.get("status"),
                item.get("active_wf01_batch_id"),
                item.get("resource_retry_count"),
            ]
            for item in (batch.get("target_design") or {}).get("targets") or []
        ],
        "screening_cursor": screening.get("cursor"),
        "screening_status": screening.get("status"),
        "relaxation_status": relaxation.get("status"),
        "relaxation_step": relaxation.get("active_step"),
        "execution_progress": wf02_execution_progress(batch),
        "balance_status": balance.get("status"),
    }
    return _canonical_sha256(state)


def _parse_target_text(value: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"[,，;；\n]+", value)
        if item.strip()
    ]


def _normalise_internal_control(value: Any) -> tuple[bool | None, str]:
    if value is None:
        return True, "DEFAULT"
    if isinstance(value, bool):
        return value, "USER"
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in {"true", "yes", "y", "1", "需要", "是", "包含"}:
            return True, "USER"
        if lowered in {"false", "no", "n", "0", "不需要", "否", "不包含"}:
            return False, "USER"
    return None, "INVALID"


def _reason_codes(value: Any) -> list[str]:
    """Extract persisted WF-01 reason codes without treating statuses as reasons."""
    reason_keys = {
        "code",
        "reason",
        "reason_code",
        "reason_codes",
        "status_reason",
        "incomplete_reason",
        "incomplete_reasons",
    }
    found: list[str] = []

    def visit(selected: Any, *, reason_context: bool = False) -> None:
        if isinstance(selected, dict):
            for key, nested in selected.items():
                visit(nested, reason_context=reason_context or str(key) in reason_keys)
            return
        if isinstance(selected, list):
            for nested in selected:
                visit(nested, reason_context=reason_context)
            return
        if not reason_context or not isinstance(selected, str):
            return
        code = selected.strip().upper()
        if _REASON_CODE_PATTERN.fullmatch(code) and code not in found:
            found.append(code)

    visit(value)
    return found


def _issue(
    code: str,
    field: str,
    message: str,
    *,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "issue_id": f"wf02-issue-{uuid4()}",
        "code": code,
        "severity": "BLOCKER",
        "field": field,
        "message": message,
        "allowed_actions": ["EDIT"],
        "evidence": copy.deepcopy(evidence or {}),
    }


class WF02V01Store:
    """Atomic filesystem persistence isolated from all WF-01 state."""

    def __init__(self, root: str | Path) -> None:
        state_root = Path(root)
        self.root = state_root / "wf02-v01-batches"
        self.batches_root = self.root
        self.drafts_root = state_root / "wf02-v01-drafts"
        self.reports_root = state_root / "wf02-v01-reports"
        self.exports_root = state_root / "wf02-v01-exports"

    @staticmethod
    def _validate_id(value: str, *, label: str) -> None:
        try:
            UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise InputValidationError(
                f"非法{label}ID",
                details={f"{label}_id": value},
            ) from exc

    @staticmethod
    def _write(directory: Path, identifier: str, value: dict[str, Any]) -> Path:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / f"{identifier}.json"
            temporary = destination.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(value, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(destination)
            return destination
        except OSError as exc:
            raise PersistenceError(
                "WF-02 v0.1 状态持久化失败",
                details={"identifier": identifier, "error": str(exc)},
            ) from exc

    @staticmethod
    def _read(directory: Path, identifier: str, *, label: str) -> dict[str, Any]:
        path = directory / f"{identifier}.json"
        if not path.exists():
            raise InputValidationError(
                f"WF-02 {label}不存在",
                details={f"{label}_id": identifier},
            )
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PersistenceError(
                f"WF-02 {label}读取失败",
                details={f"{label}_id": identifier, "error": str(exc)},
            ) from exc

    def save_draft(self, draft: dict[str, Any]) -> Path:
        draft_id = str(draft.get("draft_id") or "")
        self._validate_id(draft_id, label="草稿")
        return self._write(self.drafts_root, draft_id, draft)

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        self._validate_id(draft_id, label="草稿")
        return self._read(self.drafts_root, draft_id, label="草稿")

    def save_batch(self, batch: dict[str, Any]) -> Path:
        batch_id = str(batch.get("batch_id") or "")
        self._validate_id(batch_id, label="任务")
        batch.setdefault(
            "batch_code",
            wf01_batch_code(
                batch_id,
                batch.get("created_at"),
                workflow="WF-02",
            ),
        )
        return self._write(self.batches_root, batch_id, batch)

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        self._validate_id(batch_id, label="任务")
        batch = self._read(self.batches_root, batch_id, label="任务")
        batch.setdefault(
            "batch_code",
            wf01_batch_code(
                str(batch.get("batch_id") or batch_id),
                batch.get("created_at"),
                workflow="WF-02",
            ),
        )
        return batch

    def iter_batches(self) -> list[dict[str, Any]]:
        if not self.batches_root.exists():
            return []
        values: list[dict[str, Any]] = []
        for path in sorted(self.batches_root.glob("*.json")):
            try:
                values.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return values

    def save_report(self, report: dict[str, Any]) -> Path:
        batch_id = str((report.get("source") or {}).get("batch_id") or "")
        self._validate_id(batch_id, label="任务")
        return self._write(self.reports_root, batch_id, report)

    def get_report(self, batch_id: str) -> dict[str, Any]:
        self._validate_id(batch_id, label="任务")
        return self._read(self.reports_root, batch_id, label="报告")

    def save_export(self, batch_id: str, format_name: str, content: bytes) -> Path:
        self._validate_id(batch_id, label="任务")
        selected = str(format_name).lower()
        suffixes = {"html": ".html", "csv": ".csv", "pdf": ".pdf"}
        if selected not in suffixes:
            raise InputValidationError(
                "WF-02 导出格式无效",
                details={"format": format_name, "supported": sorted(suffixes)},
            )
        directory = self.exports_root / batch_id
        try:
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / f"wf02-multiplex-report{suffixes[selected]}"
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            temporary.write_bytes(content)
            temporary.replace(destination)
            return destination
        except OSError as exc:
            raise PersistenceError(
                "WF-02 报告附件持久化失败",
                details={"batch_id": batch_id, "format": selected, "error": str(exc)},
            ) from exc

    def get_export_path(self, batch_id: str, format_name: str) -> Path:
        self._validate_id(batch_id, label="任务")
        selected = str(format_name).lower()
        suffixes = {"html": ".html", "csv": ".csv", "pdf": ".pdf"}
        if selected not in suffixes:
            raise InputValidationError(
                "WF-02 导出格式无效",
                details={"format": format_name, "supported": sorted(suffixes)},
            )
        path = self.exports_root / batch_id / f"wf02-multiplex-report{suffixes[selected]}"
        if not path.exists():
            raise InputValidationError(
                "WF-02 导出文件不存在",
                details={"batch_id": batch_id, "format": selected},
            )
        return path

class WF02V01InputPreflight:
    """Deterministically implement N01-N04 without starting a task."""

    def __init__(
        self,
        config: dict[str, Any],
        artifact_resolver: Callable[[str], tuple[Path, dict[str, Any]]] | None = None,
    ) -> None:
        self.config = config
        self.artifact_resolver = artifact_resolver

    def _target_template_source(
        self,
        raw_target: Any,
        *,
        target_index: int,
        issues: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[str]]:
        """Validate one target-local source without parsing scientific content."""
        contract = {
            "policy_version": WF02_TARGET_FILE_POLICY_VERSION,
            "source_kind": "NOT_PROVIDED",
            "file_count": 0,
            "file_name": None,
            "record_grouping": "SINGLE_DETECTION_TARGET",
            "sequence_mode": "alignment_target",
            "multiple_homologous_sequences_allowed": True,
            "multi_target_file_auto_split": False,
        }
        if not isinstance(raw_target, dict):
            return contract, []

        requested_mode = _text(raw_target.get("sequence_mode")).lower()
        if requested_mode and requested_mode != "alignment_target":
            issues.append(
                _issue(
                    "WF02_TARGET_FILE_MODE_INVALID",
                    "targets",
                    (
                        f"第 {target_index} 个靶标的文件只能作为同一检测靶标的"
                        "同源序列集合，不能按 FASTA 记录拆成多个靶标。"
                    ),
                    evidence={
                        "target_index": target_index,
                        "received_sequence_mode": requested_mode,
                        "required_sequence_mode": "alignment_target",
                    },
                )
            )
        if raw_target.get("target_manifest") not in (None, {}, []):
            issues.append(
                _issue(
                    "WF02_MULTI_TARGET_FILE_MANIFEST_FORBIDDEN",
                    "targets",
                    (
                        f"第 {target_index} 个靶标不能在一个文件内声明多个检测靶标；"
                        "请把每个检测靶标放入独立文件并分别绑定。"
                    ),
                    evidence={"target_index": target_index},
                )
            )

        source_fields: list[tuple[str, str]] = []
        for field, kind in (
            ("sequence", "INLINE_SEQUENCE"),
            ("sequences", "INLINE_HOMOLOGOUS_SEQUENCES"),
            ("template_records", "INLINE_HOMOLOGOUS_SEQUENCES"),
            ("fasta_text", "INLINE_FASTA"),
            ("fasta_path", "SERVER_FASTA_FILE"),
            ("input_artifact_id", "UPLOADED_FASTA_FILE"),
        ):
            value = raw_target.get(field)
            if value not in (None, "", [], {}):
                source_fields.append((field, kind))

        artifact_ids: list[str] = []
        sequence_files = raw_target.get("sequence_files")
        if sequence_files not in (None, [], {}):
            if not isinstance(sequence_files, list):
                issues.append(
                    _issue(
                        "WF02_TARGET_SEQUENCE_FILES_INVALID",
                        "targets",
                        f"第 {target_index} 个靶标的 sequence_files 必须为数组。",
                        evidence={"target_index": target_index},
                    )
                )
            elif len(sequence_files) != 1:
                issues.append(
                    _issue(
                        "WF02_MULTIPLE_FILES_FOR_ONE_TARGET",
                        "targets",
                        (
                            f"第 {target_index} 个检测靶标只能绑定一个文件；"
                            f"当前收到 {len(sequence_files)} 个文件。"
                        ),
                        evidence={
                            "target_index": target_index,
                            "file_count": len(sequence_files),
                        },
                    )
                )
            else:
                selected_file = sequence_files[0]
                if not isinstance(selected_file, dict):
                    issues.append(
                        _issue(
                            "WF02_TARGET_SEQUENCE_FILE_INVALID",
                            "targets",
                            f"第 {target_index} 个靶标的文件记录必须为对象。",
                            evidence={"target_index": target_index},
                        )
                    )
                else:
                    file_sources = [
                        field
                        for field in ("input_artifact_id", "artifact_id", "fasta_text", "content")
                        if selected_file.get(field) not in (None, "")
                    ]
                    if len(file_sources) != 1:
                        issues.append(
                            _issue(
                                "WF02_TARGET_FILE_SOURCE_AMBIGUOUS",
                                "targets",
                                (
                                    f"第 {target_index} 个靶标的单个文件记录必须且只能提供"
                                    "一个文件引用或 FASTA 正文。"
                                ),
                                evidence={
                                    "target_index": target_index,
                                    "source_fields": file_sources,
                                },
                            )
                        )
                    source_fields.append(("sequence_files", "UPLOADED_FASTA_FILE"))
                    contract["file_name"] = _text(
                        selected_file.get("file_name") or selected_file.get("name")
                    ) or None
                    nested_artifact_value = selected_file.get(
                        "input_artifact_id"
                    ) or selected_file.get("artifact_id")
                    if nested_artifact_value not in (None, ""):
                        artifact_ids.append(_text(nested_artifact_value))

        if len(source_fields) > 1:
            issues.append(
                _issue(
                    "WF02_TARGET_TEMPLATE_SOURCE_CONFLICT",
                    "targets",
                    (
                        f"第 {target_index} 个检测靶标只能提供一个模板来源；"
                        "不能同时绑定文件和其他序列来源。"
                    ),
                    evidence={
                        "target_index": target_index,
                        "source_fields": [field for field, _kind in source_fields],
                    },
                )
            )

        if source_fields:
            source_field, source_kind = source_fields[0]
            contract["source_kind"] = source_kind
            contract["file_count"] = int(
                source_field in {"fasta_path", "input_artifact_id", "sequence_files"}
            )
            if source_field == "fasta_path":
                contract["file_name"] = Path(str(raw_target[source_field])).name or None
            if source_field == "input_artifact_id":
                artifact_ids.append(_text(raw_target.get(source_field)))

        for artifact_id in artifact_ids:
            if not ARTIFACT_ID_PATTERN.fullmatch(artifact_id):
                issues.append(
                    _issue(
                        "WF02_INPUT_ARTIFACT_ID_INVALID",
                        "targets",
                        f"第 {target_index} 个靶标的上传文件引用格式无效。",
                        evidence={
                            "target_index": target_index,
                            "input_artifact_id": artifact_id,
                        },
                    )
                )
                continue
            if self.artifact_resolver is not None:
                try:
                    _path, manifest = self.artifact_resolver(artifact_id)
                    contract["file_name"] = (
                        _text(manifest.get("file_name")) or contract["file_name"]
                    )
                except AgentDError as exc:
                    issues.append(
                        _issue(
                            "WF02_INPUT_ARTIFACT_UNAVAILABLE",
                            "targets",
                            f"第 {target_index} 个靶标的上传文件引用不存在或不可用。",
                            evidence={
                                "target_index": target_index,
                                "input_artifact_id": artifact_id,
                                "error": exc.to_dict(),
                            },
                        )
                    )
        return contract, artifact_ids

    @staticmethod
    def _raw_targets(payload: dict[str, Any]) -> Any:
        for key in ("targets", "target_list", "targetList"):
            if key in payload:
                return payload.get(key)
        return None

    @staticmethod
    def _host(payload: dict[str, Any]) -> str:
        for key in ("host_species", "hostSpecies", "host"):
            if key in payload:
                selected = _named_value(payload.get(key))
                if selected:
                    return selected
        return ""

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise InputValidationError("WF-02 输入必须为对象")

        issues: list[dict[str, Any]] = []
        target_alias_values = [
            payload[key]
            for key in ("targets", "target_list", "targetList")
            if key in payload
        ]
        if len(target_alias_values) > 1:
            canonical_target_values = [
                _parse_target_text(value) if isinstance(value, str) else value
                for value in target_alias_values
            ]
            if len(
                {
                    _canonical_sha256(value)
                    for value in canonical_target_values
                }
            ) > 1:
                issues.append(
                    _issue(
                        "WF02_TARGETS_ALIAS_CONFLICT",
                        "targets",
                        "检测到多份不一致的靶标列表，请确认唯一列表。",
                    )
                )
        raw_targets = self._raw_targets(payload)
        if isinstance(raw_targets, str):
            raw_targets = _parse_target_text(raw_targets)
        if raw_targets is None:
            raw_targets = []
            issues.append(
                _issue(
                    "WF02_TARGETS_REQUIRED",
                    "targets",
                    "请提供需要在同一反应管检测的 2–5 个目标靶标。",
                )
            )
        elif not isinstance(raw_targets, list):
            issues.append(
                _issue(
                    "WF02_TARGETS_TYPE_INVALID",
                    "targets",
                    "靶标列表必须为数组或可分隔的文本列表。",
                    evidence={"received_type": type(raw_targets).__name__},
                )
            )
            raw_targets = []

        if not raw_targets and not any(
            issue["code"] in {"WF02_TARGETS_REQUIRED", "WF02_TARGETS_TYPE_INVALID"}
            for issue in issues
        ):
            issues.append(
                _issue(
                    "WF02_TARGETS_REQUIRED",
                    "targets",
                    "请提供需要在同一反应管检测的 2–5 个目标靶标。",
                )
            )
        elif raw_targets and not (
            MINIMUM_TARGETS <= len(raw_targets) <= MAXIMUM_TARGETS
        ):
            issues.append(
                _issue(
                    "WF02_TARGET_COUNT_OUT_OF_RANGE",
                    "targets",
                    f"WF-02 普通多重需要 {MINIMUM_TARGETS}–{MAXIMUM_TARGETS} 个靶标。",
                    evidence={
                        "target_count": len(raw_targets),
                        "minimum": MINIMUM_TARGETS,
                        "maximum": MAXIMUM_TARGETS,
                    },
                )
            )

        raw_intake_manifest = payload.get("upload_intake_manifest")
        intake_manifest: dict[str, Any] | None = None
        if raw_intake_manifest not in (None, {}):
            if not isinstance(raw_intake_manifest, dict):
                issues.append(
                    _issue(
                        "WF02_UPLOAD_INTAKE_MANIFEST_INVALID",
                        "targets",
                        "上传接收清单必须为对象。",
                        evidence={
                            "received_type": type(raw_intake_manifest).__name__,
                        },
                    )
                )
            else:
                expected_count = raw_intake_manifest.get("expected_target_count")
                failed_file_ids = raw_intake_manifest.get("failed_file_ids") or []
                if (
                    isinstance(expected_count, bool)
                    or not isinstance(expected_count, int)
                    or not MINIMUM_TARGETS <= expected_count <= MAXIMUM_TARGETS
                ):
                    issues.append(
                        _issue(
                            "WF02_UPLOAD_EXPECTED_TARGET_COUNT_INVALID",
                            "targets",
                            f"上传接收清单中的目标总数必须为 {MINIMUM_TARGETS}–{MAXIMUM_TARGETS}。",
                            evidence={"expected_target_count": expected_count},
                        )
                    )
                if not isinstance(failed_file_ids, list) or any(
                    isinstance(file_id, bool)
                    or not isinstance(file_id, int)
                    or file_id <= 0
                    for file_id in failed_file_ids
                ):
                    issues.append(
                        _issue(
                            "WF02_UPLOAD_FAILED_FILE_IDS_INVALID",
                            "targets",
                            "上传接收清单中的 failed_file_ids 必须为正整数数组。",
                        )
                    )
                    failed_file_ids = []
                received_count = len(raw_targets)
                complete = (
                    isinstance(expected_count, int)
                    and not isinstance(expected_count, bool)
                    and expected_count == received_count
                    and not failed_file_ids
                )
                intake_manifest = {
                    "policy_version": "wf02-upload-intake-v1",
                    "expected_target_count": expected_count,
                    "received_target_count": received_count,
                    "failed_file_ids": copy.deepcopy(failed_file_ids),
                    "complete": complete,
                }
                if isinstance(expected_count, int) and expected_count != received_count:
                    issues.append(
                        _issue(
                            "WF02_UPLOAD_TARGET_COUNT_MISMATCH",
                            "targets",
                            "上传文件总数与已绑定目标数不一致，正式任务尚不能创建。",
                            evidence=copy.deepcopy(intake_manifest),
                        )
                    )
                if failed_file_ids:
                    issues.append(
                        _issue(
                            "WF02_UPLOAD_DOWNLOADS_INCOMPLETE",
                            "targets",
                            "仍有上传文件未成功接收，正式任务尚不能创建。",
                            evidence=copy.deepcopy(intake_manifest),
                        )
                    )

        targets: list[dict[str, Any]] = []
        identities: dict[str, int] = {}
        artifact_owners: dict[str, tuple[int, str]] = {}
        for index, raw_target in enumerate(raw_targets, start=1):
            source = copy.deepcopy(raw_target)
            if isinstance(raw_target, str):
                display_name = raw_target.strip()
                target_id = display_name
            elif isinstance(raw_target, dict):
                target_id = _text(raw_target.get("target_id"))
                display_name = _named_value(raw_target)
                if not target_id:
                    target_id = display_name
            else:
                target_id = ""
                display_name = ""

            if isinstance(raw_target, dict):
                identity_status = _text(
                    raw_target.get("identity_status")
                ).upper()
                identity_candidates = raw_target.get("identity_candidates")
                explicitly_ambiguous = (
                    raw_target.get("ambiguous") is True
                    or identity_status in {"AMBIGUOUS", "CONFLICT"}
                    or (
                        isinstance(identity_candidates, list)
                        and len(identity_candidates) > 1
                    )
                )
                if explicitly_ambiguous:
                    issues.append(
                        _issue(
                            "WF02_TARGET_IDENTITY_AMBIGUOUS",
                            "targets",
                            f"第 {index} 个靶标身份存在歧义，请确认唯一靶标。",
                            evidence={
                                "target_index": index,
                                "display_name": display_name or None,
                                "identity_candidates": copy.deepcopy(
                                    identity_candidates or []
                                ),
                            },
                        )
                    )

            if not target_id or not display_name:
                issues.append(
                    _issue(
                        "WF02_TARGET_IDENTITY_REQUIRED",
                        "targets",
                        f"第 {index} 个靶标缺少可识别的名称或 target_id。",
                        evidence={"target_index": index},
                    )
                )
                target_id = target_id or f"unresolved_target_{index}"
                display_name = display_name or target_id

            identity_key = re.sub(r"\s+", "", target_id).casefold()
            display_key = re.sub(r"\s+", "", display_name).casefold()
            identity_aliases = tuple(dict.fromkeys((identity_key, display_key)))
            conflict_index = next(
                (
                    identities[alias]
                    for alias in identity_aliases
                    if alias in identities
                ),
                None,
            )
            if conflict_index is not None:
                issues.append(
                    _issue(
                        "WF02_TARGET_IDENTITY_CONFLICT",
                        "targets",
                        f"靶标 {display_name} 与列表中已有靶标重复或身份冲突。",
                        evidence={
                            "target_index": index,
                            "conflicts_with_index": conflict_index,
                            "target_id": target_id,
                        },
                    )
                )
            else:
                for alias in identity_aliases:
                    identities[alias] = index

            template_source, artifact_ids = self._target_template_source(
                raw_target,
                target_index=index,
                issues=issues,
            )
            for artifact_id in dict.fromkeys(artifact_ids):
                if not artifact_id or not ARTIFACT_ID_PATTERN.fullmatch(artifact_id):
                    continue
                previous = artifact_owners.get(artifact_id)
                if previous is not None:
                    issues.append(
                        _issue(
                            "WF02_FILE_ASSIGNED_TO_MULTIPLE_TARGETS",
                            "targets",
                            (
                                f"同一个上传文件不能同时绑定第 {previous[0]} 个和"
                                f"第 {index} 个检测靶标。"
                            ),
                            evidence={
                                "input_artifact_id": artifact_id,
                                "first_target_index": previous[0],
                                "first_target_id": previous[1],
                                "second_target_index": index,
                                "second_target_id": target_id,
                            },
                        )
                    )
                else:
                    artifact_owners[artifact_id] = (index, target_id)

            targets.append(
                {
                    "target_id": target_id,
                    "display_name": display_name,
                    "input_order": index,
                    "source_input": source,
                    "template_source": template_source,
                }
            )

        host_alias_values = [
            _named_value(payload[key])
            for key in ("host_species", "hostSpecies", "host")
            if key in payload
        ]
        if len(
            {
                re.sub(r"\s+", "", value).casefold()
                for value in host_alias_values
            }
        ) > 1:
            issues.append(
                _issue(
                    "WF02_HOST_SPECIES_CONFLICT",
                    "host_species",
                    "检测到不一致的宿主物种，请确认唯一宿主。",
                )
            )
        host_species = self._host(payload)
        if not host_species:
            issues.append(
                _issue(
                    "WF02_HOST_SPECIES_REQUIRED",
                    "host_species",
                    "请补充待测样本对应的宿主物种。",
                )
            )

        internal_key = next(
            (
                key
                for key in (
                    "include_internal_control",
                    "includeInternalControl",
                    "need_internal_control",
                )
                if key in payload
            ),
            None,
        )
        internal_value = payload.get(internal_key) if internal_key else None
        include_internal_control, internal_source = _normalise_internal_control(
            internal_value
        )
        internal_alias_values = [
            _normalise_internal_control(payload[key])[0]
            for key in (
                "include_internal_control",
                "includeInternalControl",
                "need_internal_control",
            )
            if key in payload
        ]
        valid_internal_values = {
            value for value in internal_alias_values if isinstance(value, bool)
        }
        if len(valid_internal_values) > 1:
            issues.append(
                _issue(
                    "WF02_INTERNAL_CONTROL_CONFLICT",
                    "include_internal_control",
                    "检测到互相冲突的内标要求，请明确选择需要或不需要。",
                )
            )
        if include_internal_control is None:
            issues.append(
                _issue(
                    "WF02_INTERNAL_CONTROL_INVALID",
                    "include_internal_control",
                    "是否需要内标必须明确为是或否；未提供时默认需要。",
                    evidence={"received": internal_value},
                )
            )

        runtime: dict[str, Any] = {}
        for key in ("instrument", "rox_as_reference"):
            if key in payload:
                runtime[key] = copy.deepcopy(payload[key])
        nested_runtime = payload.get("runtime_config")
        if isinstance(nested_runtime, dict):
            runtime.update(copy.deepcopy(nested_runtime))
        instrument = _text(runtime.get("instrument")) or str(
            self.config["multiplex"]["default_instrument"]
        )
        if "rox_as_reference" in runtime and isinstance(
            runtime.get("rox_as_reference"), bool
        ):
            rox_as_reference = bool(runtime["rox_as_reference"])
            rox_source = "USER_OVERRIDE"
        else:
            instrument_settings = INSTRUMENT_DB.get(instrument) or {}
            rox_as_reference = (
                bool(instrument_settings.get("needs_rox"))
                if instrument_settings
                else None
            )
            rox_source = "INSTRUMENT_DEFAULT"
        runtime_source = "USER_OVERRIDE" if runtime else "SYSTEM_DEFAULT"

        execution_options_snapshot = {
            key: copy.deepcopy(payload[key])
            for key in (
                "allow_fallback",
                "host_db",
                "cross_db",
                "internal_control_payload",
                "sample_type",
                "assay_id",
            )
            if key in payload
        }

        user_input_snapshot = {
            "targets": copy.deepcopy(targets),
            "host_species": host_species,
            "include_internal_control": include_internal_control,
        }
        if intake_manifest is not None:
            user_input_snapshot["upload_intake_manifest"] = copy.deepcopy(
                intake_manifest
            )
        input_echo = {
            "target_count": len(raw_targets),
            "targets": [target["display_name"] for target in targets],
            "target_template_sources": [
                {
                    "target_id": target["target_id"],
                    **copy.deepcopy(target["template_source"]),
                }
                for target in targets
            ],
            "host_species": host_species or None,
            "include_internal_control": include_internal_control,
            "include_internal_control_source": internal_source,
            "upload_intake_manifest": copy.deepcopy(intake_manifest),
        }
        can_create = not issues
        node_trace = [
            {
                "node": "N01",
                "status": "COMPLETED",
                "message": "已接收同管多靶标检测需求",
            },
            {
                "node": "N02",
                "status": "COMPLETED",
                "message": "已提取并回显 WF-02 核心输入",
            },
            {
                "node": "N03",
                "status": "PASS" if can_create else "BLOCKED",
                "message": (
                    "核心输入完整且无冲突"
                    if can_create
                    else f"发现 {len(issues)} 个阻塞项"
                ),
            },
        ]
        if not can_create:
            node_trace.append(
                {
                    "node": "N04",
                    "status": "WAITING_USER",
                    "message": "仅请求补充或修正阻塞项",
                }
            )

        return {
            "schema_version": WF02_PREFLIGHT_SCHEMA_VERSION,
            "workflow": "WF-02",
            "workflow_version": WF02_WORKFLOW_VERSION,
            "policy_version": WF02_POLICY_VERSION,
            "stage": "N03" if can_create else "N04",
            "status": "READY_TO_CREATE" if can_create else "NEEDS_CONFIRMATION",
            "can_create_task": can_create,
            "next_action": "CREATE_TASK" if can_create else "WAIT_FOR_USER",
            "input_echo": input_echo,
            "normalised_input": {
                **user_input_snapshot,
                "task_name": _text(payload.get("task_name")) or None,
            },
            "user_input_snapshot": user_input_snapshot,
            "runtime_config_snapshot": {
                "instrument": instrument,
                "rox_as_reference": rox_as_reference,
                "source": runtime_source,
                "rox_source": rox_source,
                "is_core_user_input": False,
            },
            "execution_options_snapshot": execution_options_snapshot,
            "rule_snapshot": {
                "policy_version": WF02_POLICY_VERSION,
                "minimum_targets": MINIMUM_TARGETS,
                "maximum_targets": MAXIMUM_TARGETS,
                "internal_control_default": True,
                "target_file_policy_version": WF02_TARGET_FILE_POLICY_VERSION,
                "one_file_per_target": True,
                "multiple_homologous_sequences_per_file": True,
                "multi_target_file_auto_split": False,
                "config_sha256": config_fingerprint(self.config),
            },
            "issues": issues,
            "node_trace": node_trace,
        }


def _interaction_input_field(issue: dict[str, Any]) -> dict[str, Any]:
    field = str(issue.get("field") or "")
    definitions = {
        "targets": {
            "key": "targets",
            "label": "同管检测目标靶标列表（2–5 个；内标不计入重数）",
            "inputType": "TEXT",
            "required": True,
        },
        "host_species": {
            "key": "host_species",
            "label": "宿主物种",
            "inputType": "TEXT",
            "required": True,
        },
        "include_internal_control": {
            "key": "include_internal_control",
            "label": "是否需要内标",
            "inputType": "SELECT",
            "required": True,
            "options": [
                {"value": True, "label": "需要"},
                {"value": False, "label": "不需要"},
            ],
        },
    }
    return copy.deepcopy(
        definitions.get(
            field,
            {
                "key": field or "correction",
                "label": "修正内容",
                "inputType": "TEXT",
                "required": True,
            },
        )
    )


def _build_issue_interaction(draft_id: str, issue: dict[str, Any]) -> dict[str, Any]:
    return build_interaction(
        task_id=draft_id,
        stage="WF02_N04_INPUT_CONFIRMATION",
        blocker_code=str(issue.get("code") or "WF02_INPUT_BLOCKED"),
        message=str(issue.get("message") or "请补充或修正 WF-02 输入。"),
        blocking=True,
        context={
            "issueId": issue.get("issue_id"),
            "field": issue.get("field"),
            "evidence": copy.deepcopy(issue.get("evidence") or {}),
            "workflow": "WF-02",
            "draftId": draft_id,
        },
        options=[
            {
                "value": "EDIT",
                "label": "补充或修改信息",
                "description": "只修正当前阻塞项，保留其他已输入内容。",
            }
        ],
        input_fields=[_interaction_input_field(issue)],
    )


def _draft_ui_message(draft: dict[str, Any]) -> dict[str, Any] | None:
    mapped: list[dict[str, Any]] = []
    for interaction in draft.get("agent_interactions") or []:
        data = interaction.get("data") or {}
        if data.get("status") != "WAITING_USER":
            continue
        context = data.get("context") or {}
        mapped.append(
            {
                "interactionId": data.get("interactionId"),
                "field": context.get("field"),
                "blockerCode": (data.get("blocker") or {}).get("code"),
                "reason": (data.get("blocker") or {}).get("message"),
                "question": data.get("message"),
                "inputType": "FORM",
                "inputFields": copy.deepcopy(data.get("inputFields") or []),
                "options": copy.deepcopy(data.get("options") or []),
                "answer": None,
            }
        )
    if not mapped:
        return None
    common = {
        "type": "agent_interaction",
        "task": "MULTIPLEX_PANEL",
        "taskId": draft.get("draft_id"),
        "draftId": draft.get("draft_id"),
        "flowControl": {
            "action": "WAIT_FOR_USER",
            "stopCurrentTurn": True,
            "resumeMessageType": "agent_interaction_response",
        },
        "inputEcho": copy.deepcopy((draft.get("preflight") or {}).get("input_echo")),
    }
    if len(mapped) == 1:
        return {**common, **mapped[0]}
    return {**common, "interactions": mapped}


def _error_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, AgentDError):
        return exc.to_dict()
    return {
        "code": "UNEXPECTED_SERVICE_ERROR",
        "message": str(exc),
        "details": {"exception_type": type(exc).__name__},
    }


def _compact_failure(value: Any) -> dict[str, Any] | None:
    """Keep control-relevant error data in conversational summaries."""
    if not isinstance(value, dict) or not value:
        return None
    compact = {
        key: copy.deepcopy(value.get(key))
        for key in ("code", "message", "status", "retryable", "stage")
        if value.get(key) is not None
    }
    reason_codes = _reason_codes(value)
    if reason_codes:
        compact["reason_codes"] = reason_codes
    details = value.get("details")
    if isinstance(details, dict):
        retained_details = {
            key: copy.deepcopy(details.get(key))
            for key in (
                "target_id",
                "panel_id",
                "p4_status",
                "child_status",
                "database",
                "database_version",
                "next_action",
            )
            if details.get(key) is not None
        }
        if retained_details:
            compact["details"] = retained_details
    compact["full_details_available"] = True
    return compact


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dye_supported_by_instrument(
    dye: str,
    instrument_settings: dict[str, Any],
) -> bool:
    """Read the shared P4 catalogue for assignment eligibility, not a verdict."""
    emission = float(FLUOROPHORE_DB[dye]["em"])
    return any(
        abs(float(channel_em) - emission) <= 20.0
        for _channel_ex, channel_em in instrument_settings.get("channels", [])
    )


def _dyes_are_separated(left: str, right: str) -> bool:
    return (
        abs(
            float(FLUOROPHORE_DB[left]["em"])
            - float(FLUOROPHORE_DB[right]["em"])
        )
        >= WF02_MINIMUM_CHANNEL_SEPARATION_NM
    )


def _append_unique(values: list[str], candidates: list[str]) -> None:
    for candidate in candidates:
        if candidate not in values:
            values.append(candidate)


def _wf02_channel_plan(
    *,
    instrument: str,
    rox_as_reference: bool | None,
    target_count: int,
    include_internal: bool,
) -> dict[str, Any]:
    """Assign channels in WF-02; P4 remains the authoritative validator."""
    instrument_settings = INSTRUMENT_DB.get(instrument)
    if not isinstance(instrument_settings, dict):
        raise InputValidationError(
            "P4 未配置该仪器",
            details={"instrument": instrument, "supported": sorted(INSTRUMENT_DB)},
        )
    needs_rox = bool(
        instrument_settings.get("needs_rox") and rox_as_reference is not False
    )
    available_report_channels = max(
        0,
        int(instrument_settings.get("max_channels") or 0) - (1 if needs_rox else 0),
    )
    required_signal_channels = int(target_count) + (1 if include_internal else 0)
    base = {
        "policy_version": WF02_INTERNAL_CHANNEL_POLICY_VERSION,
        "panel_channel_policy_version": WF02_PANEL_CHANNEL_POLICY_VERSION,
        "assignment_owner": "WF-02",
        "validation_owner": "P4",
        "p4_validation_required": True,
        "instrument": instrument,
        "rox_as_reference": rox_as_reference,
        "available_report_channels": available_report_channels,
        "required_signal_channels": required_signal_channels,
        "target_count": int(target_count),
        "internal_control_count": 1 if include_internal else 0,
        "capacity_sufficient": required_signal_channels <= available_report_channels,
        "minimum_delta_em_nm": WF02_MINIMUM_CHANNEL_SEPARATION_NM,
    }
    if required_signal_channels > available_report_channels:
        return {
            **base,
            "status": "BLOCKED",
            "target_dyes": [],
            "internal_control_dye": None,
            "internal_control_selection_basis": None,
            "candidate_dyes_considered": [],
            "reasons": [
                f"需要 {required_signal_channels} 个报告通道，但仅有 "
                f"{available_report_channels} 个可用"
            ],
        }

    target_priority = list(
        WF02_TARGET_DYE_PRIORITY_WITH_ROX_REFERENCE
        if needs_rox
        else WF02_TARGET_DYE_PRIORITY_WITHOUT_ROX_REFERENCE
    )
    supported = [
        dye
        for dye in FLUOROPHORE_DB
        if not (needs_rox and dye == "ROX")
        and _dye_supported_by_instrument(dye, instrument_settings)
    ]
    _append_unique(
        target_priority,
        sorted(
            supported,
            key=lambda dye: float(FLUOROPHORE_DB[dye]["em"]),
        ),
    )
    target_priority = [dye for dye in target_priority if dye in supported]

    if include_internal:
        internal_candidates: list[str] = []
        if WF02_INTERNAL_DEFAULT_FLUOROPHORE in supported:
            internal_candidates.append(WF02_INTERNAL_DEFAULT_FLUOROPHORE)
        far_red = sorted(
            [
                dye
                for dye in supported
                if dye != WF02_INTERNAL_DEFAULT_FLUOROPHORE
                and dye != "Quasar 670"
                and float(FLUOROPHORE_DB[dye]["em"])
                > float(FLUOROPHORE_DB[WF02_INTERNAL_DEFAULT_FLUOROPHORE]["em"])
            ],
            key=lambda dye: -float(FLUOROPHORE_DB[dye]["em"]),
        )
        _append_unique(internal_candidates, far_red)
        fallback = sorted(
            [dye for dye in supported if dye != "Quasar 670"],
            key=lambda dye: -float(FLUOROPHORE_DB[dye]["em"]),
        )
        _append_unique(internal_candidates, fallback)
    else:
        internal_candidates = [""]

    for internal_dye in internal_candidates:
        selected_targets: list[str] = []
        for dye in target_priority:
            if internal_dye and (
                dye == internal_dye or not _dyes_are_separated(dye, internal_dye)
            ):
                continue
            if all(_dyes_are_separated(dye, selected) for selected in selected_targets):
                selected_targets.append(dye)
            if len(selected_targets) == target_count:
                break
        if len(selected_targets) != target_count:
            continue
        if not include_internal:
            basis = None
        elif internal_dye == WF02_INTERNAL_DEFAULT_FLUOROPHORE:
            basis = "P5_DEFAULT_CY5_CONFIRMED_BY_WF02"
        elif internal_dye in far_red:
            basis = "FARTHEST_SUPPORTED_FAR_RED_FALLBACK"
        else:
            basis = "NONCONFLICTING_FALLBACK_PENDING_P4"
        return {
            **base,
            "status": "ASSIGNED",
            "target_dyes": selected_targets,
            "internal_control_dye": internal_dye or None,
            "internal_control_selection_basis": basis,
            "candidate_dyes_considered": internal_candidates if include_internal else [],
            "reasons": [],
        }

    return {
        **base,
        "status": "BLOCKED",
        "target_dyes": [],
        "internal_control_dye": None,
        "internal_control_selection_basis": None,
        "candidate_dyes_considered": internal_candidates if include_internal else [],
        "reasons": [
            "未找到满足仪器覆盖、通道唯一性及发射峰间距要求的通道方案"
        ],
    }


def _candidate_amplicon_sequence(candidate: dict[str, Any]) -> str:
    amplicon = candidate.get("amplicon")
    if isinstance(amplicon, dict):
        sequence = _text(amplicon.get("sequence_5to3"))
        if sequence:
            return sequence
    return _text(candidate.get("amplicon_sequence_5to3"))


def _p6_not_evaluated(reason_code: str, reason: str, **details: Any) -> dict[str, Any]:
    return {
        "status": "not_evaluated",
        "mfe_dg": None,
        "mfe_structure": None,
        "most_stable_dg": None,
        "subopt_structures": [],
        "tool": "not_evaluated",
        "reason_code": reason_code,
        "reason": reason,
        "details": copy.deepcopy(details),
        "warnings": ["二级结构未评估"],
    }


def _empty_p6_summary() -> dict[str, Any]:
    return {
        "status": "NOT_STARTED",
        "candidate_count": 0,
        "evaluated_count": 0,
        "pass_count": 0,
        "caution_count": 0,
        "warning_count": 0,
        "not_evaluated_count": 0,
    }


def _aggregate_p6_summaries(values: list[dict[str, Any]]) -> dict[str, Any]:
    summary = _empty_p6_summary()
    for value in values:
        for key in (
            "candidate_count",
            "evaluated_count",
            "pass_count",
            "caution_count",
            "warning_count",
            "not_evaluated_count",
        ):
            summary[key] += int(value.get(key) or 0)
    if summary["candidate_count"] == 0:
        summary["status"] = "NOT_APPLICABLE"
    elif summary["evaluated_count"] == 0:
        summary["status"] = "NOT_EVALUATED"
    elif summary["not_evaluated_count"]:
        summary["status"] = "PARTIAL"
    else:
        summary["status"] = "COMPLETED"
    return summary


def _required_panel_number(
    value: Any,
    *,
    candidate_id: str,
    field: str,
) -> float:
    if value is None or isinstance(value, bool):
        raise InputValidationError(
            "候选缺少 P4 必需数值",
            details={"candidate_id": candidate_id, "field": field},
        )
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise InputValidationError(
            "候选的 P4 数值格式无效",
            details={
                "candidate_id": candidate_id,
                "field": field,
                "value": value,
            },
        ) from exc


def _candidate_panel_entry(
    candidate: dict[str, Any],
    *,
    target_id: str,
    role: str,
    fluorophore: str,
    quencher: str,
) -> dict[str, Any]:
    """Map a retained candidate to P4 input without reproducing P4 logic."""
    candidate_id = str(candidate.get("candidate_id") or "")
    if not candidate_id:
        raise InputValidationError(
            "面板候选缺少 candidate_id",
            details={"target_id": target_id},
        )
    forward = candidate.get("forward_primer") or {}
    reverse = candidate.get("reverse_primer") or {}
    probe = candidate.get("probe") or {}
    amplicon = candidate.get("amplicon") or {}
    sequences = {
        "fwd_seq": _text(forward.get("sequence_5to3")),
        "rev_seq": _text(reverse.get("sequence_5to3")),
        "probe_seq": _text(probe.get("sequence_5to3")),
    }
    missing_sequences = [key for key, value in sequences.items() if not value]
    if missing_sequences:
        raise InputValidationError(
            "候选缺少 P4 必需序列",
            details={
                "target_id": target_id,
                "candidate_id": candidate_id,
                "missing": missing_sequences,
            },
        )
    amplicon_sequence = _candidate_amplicon_sequence(candidate)
    length_value = amplicon.get("length_bp")
    if length_value is None and amplicon_sequence:
        length_value = len(amplicon_sequence)
    amplicon_length = int(
        _required_panel_number(
            length_value,
            candidate_id=candidate_id,
            field="amplicon.length_bp",
        )
    )
    gc_value = amplicon.get("gc_percent")
    if gc_value is None:
        gc_value = candidate.get("amplicon_gc_percent")
    if gc_value is None and amplicon_sequence:
        gc_value = gc_percent(amplicon_sequence)
    if gc_value is None:
        raise InputValidationError(
            "候选缺少 P4 所需扩增子 GC 数据",
            details={"target_id": target_id, "candidate_id": candidate_id},
        )
    corrected_probe_tm = (
        (candidate.get("mgb_assessment") or {}).get("corrected_tm_celsius")
    )
    probe_tm = (
        corrected_probe_tm
        if corrected_probe_tm is not None
        else probe.get("tm_celsius")
    )
    candidate_quencher = _text(probe.get("quencher"))
    resolved_quencher = candidate_quencher or _text(quencher)
    if not resolved_quencher:
        raise InputValidationError(
            "候选缺少 P4 所需的淬灭字段",
            details={"target_id": target_id, "candidate_id": candidate_id},
        )
    return {
        "target_id": target_id,
        "role": role,
        "candidate_id": candidate_id,
        **sequences,
        "fwd_tm": _required_panel_number(
            forward.get("tm_celsius"),
            candidate_id=candidate_id,
            field="forward_primer.tm_celsius",
        ),
        "rev_tm": _required_panel_number(
            reverse.get("tm_celsius"),
            candidate_id=candidate_id,
            field="reverse_primer.tm_celsius",
        ),
        "probe_tm": _required_panel_number(
            probe_tm,
            candidate_id=candidate_id,
            field="probe.tm_celsius",
        ),
        "gc_percent": _required_panel_number(
            gc_value,
            candidate_id=candidate_id,
            field="amplicon.gc_percent",
        ),
        "amplicon_length_bp": amplicon_length,
        "fluorophore": fluorophore,
        "quencher": resolved_quencher,
        "fluorophore_source": "WF02_CHANNEL_ASSIGNMENT",
        "quencher_source": (
            "CANDIDATE_PROBE"
            if candidate_quencher
            else "CHANNEL_RECOMMENDATION_FALLBACK"
        ),
        "score": _number(candidate.get("score")),
        "evidence_level": candidate.get("evidence_level"),
    }


def _resolved_panel_channel_assignment(
    assignment: list[dict[str, Any]],
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    entry_by_target = {
        str(item.get("target_id")): item for item in entries
    }
    resolved: list[dict[str, Any]] = []
    for item in assignment:
        target_id = str(item.get("target_id") or "")
        entry = entry_by_target.get(target_id) or {}
        projected = copy.deepcopy(item)
        projected["quencher"] = entry.get("quencher")
        projected["quencher_source"] = entry.get("quencher_source")
        resolved.append(projected)
    return resolved


def _r17_amplicon_length_check(
    entries: list[dict[str, Any]],
    minimum_difference_bp: int,
) -> dict[str, Any]:
    """Apply the documented R-17 minimum difference as a hard panel gate."""
    members = list(entries)
    comparisons: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    for left, right in itertools.combinations(members, 2):
        difference = abs(
            int(left["amplicon_length_bp"])
            - int(right["amplicon_length_bp"])
        )
        item = {
            "targets": [left["target_id"], right["target_id"]],
            "lengths_bp": [
                int(left["amplicon_length_bp"]),
                int(right["amplicon_length_bp"]),
            ],
            "difference_bp": difference,
            "minimum_difference_bp": minimum_difference_bp,
            "passed": difference >= minimum_difference_bp,
        }
        comparisons.append(item)
        if not item["passed"]:
            violations.append(copy.deepcopy(item))
    return {
        "policy_version": WF02_AMPLICON_LENGTH_POLICY_VERSION,
        "status": "FAIL" if violations else "PASS",
        "hard_filter": True,
        "scope": "ALL_PANEL_MEMBERS",
        "internal_control_included": any(
            item.get("role") == "internal_control" for item in members
        ),
        "expert_confirmation_required": False,
        "minimum_difference_bp": minimum_difference_bp,
        "comparisons": comparisons,
        "violations": violations,
    }


def _wf02_export_file_url(
    batch: dict[str, Any],
    format_name: str,
    metadata: dict[str, Any],
) -> str:
    published = str(metadata.get("file_url") or "")
    if published.startswith(("http://", "https://")):
        return published
    return build_wf02_attachment_url(
        str(batch.get("batch_id") or ""),
        format_name,
    )


def _wf02_report_ui_message(
    batch: dict[str, Any],
    *,
    attachment_formats: tuple[str, ...] = ("html",),
) -> dict[str, Any] | None:
    """Build one attachment card for every report-bearing terminal state."""
    workflow_status = str(batch.get("status") or "")
    if workflow_status not in {
        "COMPLETED",
        "PARTIAL_PANEL_AVAILABLE",
        "FAILED_INCOMPLETE_TARGET_COVERAGE",
        "FAILED_NO_COMPATIBLE_PANEL",
    }:
        return None
    exports = batch.get("report_exports") or {}
    media_types = {
        "html": "text/html; charset=utf-8",
        "csv": "text/csv; charset=utf-8",
        "pdf": "application/pdf",
    }
    attachments = [
        {
            "attachmentId": f"attachment_{format_name}",
            "fileName": value.get("filename"),
            "fileType": format_name.upper(),
            "mimeType": media_types[format_name],
            "fileUrl": _wf02_export_file_url(
                batch,
                format_name,
                value,
            ),
        }
        for format_name in attachment_formats
        if format_name in media_types
        if isinstance((value := exports.get(format_name)), dict)
        and value.get("filename")
        and value.get("download_url")
    ]
    if not attachments:
        return None
    primary = next(
        (
            item
            for item in attachments
            if item["attachmentId"] == "attachment_html"
        ),
        attachments[0],
    )
    report_summary = batch.get("report_summary") or {}
    issues: list[str] = []
    if report_summary.get("status") == "PASS_WITH_WARNING":
        issues.append("报告包含风险或均衡性警告，请在实验验证前复核。")
    if report_summary.get("internal_control_requested") and not report_summary.get(
        "internal_control_included"
    ):
        issues.append(
            "用户请求了内标，但本次未获得可用内标；已交付不含内标的目标面板。"
        )
    balance = ((batch.get("multiplex") or {}).get("balance") or {})
    if balance.get("tm_warning_fallback"):
        issues.append("Tm 定向重设计达到两次上限，已按流程保留最优组合。")
    task_name = str(batch.get("task_name") or "WF-02 普通多重 qPCR")
    top3_count = int(report_summary.get("top3_count") or 0)
    partial_count = int(report_summary.get("partial_panel_count") or 0)
    if workflow_status == "COMPLETED":
        summary_text = (
            f"任务编号 {batch.get('batch_code')}。已完成 WF-02 N01–N32，"
            f"生成 {top3_count} 套完整候选面板及 HTML 报告。"
            "CSV/PDF 可在明确请求时按需生成。"
        )
    elif workflow_status == "PARTIAL_PANEL_AVAILABLE":
        summary_text = (
            f"任务编号 {batch.get('batch_code')}。WF-02 已结束并生成"
            f" {partial_count} 套部分结果及 HTML 终态报告。"
            "CSV/PDF 可在明确请求时按需生成。"
        )
        issues.append("完整结果未形成；终态报告已保留部分结果和未完成原因。")
    else:
        summary_text = (
            f"任务编号 {batch.get('batch_code')}。WF-02 已结束；"
            "HTML 终态报告已记录失败阶段、原因码和恢复建议；"
            "CSV/PDF 可在明确请求时按需生成。"
        )
        reason_codes = (batch.get("outcome") or {}).get("reason_codes") or []
        if reason_codes:
            issues.append("原因码：" + ", ".join(str(item) for item in reason_codes))
    return {
        "data": {
            # This status describes successful report delivery. The workflow's
            # own terminal status remains in summaryText, issues and the report.
            "status": "COMPLETED",
            "reportTitle": f"{task_name}终态报告",
            "summaryText": summary_text,
            "primaryAttachmentId": primary["attachmentId"],
            "attachments": attachments,
            "issues": issues,
        },
        "type": "primer_design_output",
    }



def summarize_wf02_batch(
    batch: dict[str, Any],
    *,
    attachment_formats: tuple[str, ...] = ("html",),
) -> dict[str, Any]:
    snapshot = batch.get("user_input_snapshot") or {}
    target_design = batch.get("target_design") or {}
    internal_control = batch.get("internal_control") or {}
    multiplex = batch.get("multiplex") or {}
    balance = multiplex.get("balance") or {}
    scoring = multiplex.get("scoring") or {}
    deduplication = multiplex.get("deduplication") or {}
    selection = multiplex.get("selection") or {}
    background_execution = batch.get("background_execution") or {}
    target_results = target_design.get("targets") or []
    waiting_children = [
        {
            "target_id": item.get("target_id"),
            "purpose": item.get("purpose", "TARGET_DESIGN"),
            "wf01_batch_id": item.get("active_wf01_batch_id")
            or item.get("wf01_batch_id"),
            "wf01_batch_code": item.get("active_wf01_batch_code")
            or item.get("wf01_batch_code"),
            "next_action": item.get("child_next_action"),
            "ui_message": copy.deepcopy(item.get("child_ui_message")),
        }
        for item in target_results
        if item.get("status") in {"WAITING_USER", "RUNNING"}
    ]
    result = {
        "response_schema_version": "wf02-v0.1-agent-summary-v1",
        "workflow": "WF-02",
        "batch_id": batch.get("batch_id"),
        "batch_code": batch.get("batch_code"),
        "task_version": batch.get("task_version"),
        "status": batch.get("status"),
        "stage": batch.get("stage"),
        "next_action": batch.get("next_action"),
        "resume_token": wf02_resume_token(batch),
        "execution_progress": wf02_execution_progress(batch),
        "outcome": copy.deepcopy(batch.get("outcome") or {}),
        "input_echo": {
            "targets": [
                target.get("display_name")
                for target in snapshot.get("targets") or []
            ],
            "host_species": snapshot.get("host_species"),
            "include_internal_control": snapshot.get("include_internal_control"),
        },
        "input_snapshot_sha256": batch.get("input_snapshot_sha256"),
        "workflow_version": batch.get("workflow_version"),
        "policy_version": batch.get("policy_version"),
        "implemented_through_node": "N32",
        "phase_02_complete": target_design.get("status") == "COMPLETED",
        "phase_03_complete": internal_control.get("status") in {
            "COMPLETED",
            "COMPLETED_WITH_CAUTION",
            "SKIPPED_BY_USER",
            "SKIPPED_AFTER_FAILURE",
            "FAILED_CONTINUE_WITHOUT_INTERNAL",
        },
        "phase_04_complete": (
            (multiplex.get("screening") or {}).get("status")
            in {"COMPLETED", "NO_COMPLETE_PANEL"}
        ),
        "phase_05_complete": (
            bool((multiplex.get("screening") or {}).get("strict_count"))
            or (multiplex.get("relaxation") or {}).get("status")
            in {"COMPLETED_RISK_FULL", "COMPLETED_PARTIAL", "FAILED"}
        ),
        "phase_06_complete": batch.get("status") == "COMPLETED",
        "terminal_report_ready": bool(
            batch.get("report_id") and batch.get("report_exports")
        ),
        "runtime_config": copy.deepcopy(batch.get("runtime_config_snapshot") or {}),
        "target_design": {
            "requested": target_design.get("requested_targets", len(snapshot.get("targets") or [])),
            "succeeded": target_design.get("succeeded_targets", 0),
            "failed": target_design.get("failed_targets", 0),
            "failed_target_ids": copy.deepcopy(
                target_design.get("failed_target_ids")
                or multiplex.get("failed_targets")
                or []
            ),
            "blocked": target_design.get("blocked_targets", 0),
            "blocked_target_ids": copy.deepcopy(
                target_design.get("blocked_target_ids") or []
            ),
            "waiting": target_design.get("waiting_targets", 0),
            "tool_retry": target_design.get("tool_retry_targets", 0),
            "coverage": copy.deepcopy(target_design.get("coverage") or {}),
            "coverage_gaps": [
                {
                    key: copy.deepcopy(item.get(key))
                    for key in (
                        "target_id",
                        "display_name",
                        "input_order",
                        "reason_code",
                        "reason_message",
                        "wf01_batch_id",
                    )
                    if item.get(key) is not None
                }
                | {"failure": _compact_failure(item.get("failure"))}
                for item in target_design.get("coverage_gaps") or []
            ],
            "p6_summary": copy.deepcopy(
                target_design.get("p6_summary") or _empty_p6_summary()
            ),
            "targets": [
                {
                    "target_id": item.get("target_id"),
                    "display_name": item.get("display_name"),
                    "status": item.get("status"),
                    "candidate_count": item.get("candidate_count", 0),
                    "p6_summary": copy.deepcopy(
                        item.get("p6_summary") or _empty_p6_summary()
                    ),
                    "wf01_batch_id": item.get("active_wf01_batch_id")
                    or item.get("wf01_batch_id"),
                    "failure": _compact_failure(item.get("failure")),
                }
                for item in target_results
            ],
        },
        "multiplex": {
            "panel_size": multiplex.get(
                "panel_size", len(snapshot.get("targets") or [])
            ),
            "targets_order": copy.deepcopy(
                multiplex.get("targets_order")
                or [item.get("target_id") for item in snapshot.get("targets") or []]
            ),
            "failed_targets": copy.deepcopy(
                multiplex.get("failed_targets") or []
            ),
            "screening": {
                "status": (multiplex.get("screening") or {}).get(
                    "status", "NOT_STARTED"
                ),
                "complete_panel_possible": bool(
                    (multiplex.get("screening") or {}).get(
                        "complete_panel_possible"
                    )
                ),
                "total_combinations": int(
                    (multiplex.get("screening") or {}).get(
                        "total_combinations", 0
                    )
                    or 0
                ),
                "processed_combinations": int(
                    (multiplex.get("screening") or {}).get(
                        "cursor", 0
                    )
                    or 0
                ),
                "strict_count": int(
                    (multiplex.get("screening") or {}).get(
                        "strict_count", 0
                    )
                    or 0
                ),
                "risk_count": int(
                    (multiplex.get("screening") or {}).get(
                        "risk_count", 0
                    )
                    or 0
                ),
                "incompatible_count": int(
                    (multiplex.get("screening") or {}).get(
                        "incompatible_count", 0
                    )
                    or 0
                ),
                "invalid_count": int(
                    (multiplex.get("screening") or {}).get(
                        "invalid_count", 0
                    )
                    or 0
                ),
                "channel_assignment": copy.deepcopy(
                    (multiplex.get("screening") or {}).get(
                        "channel_assignment"
                    )
                    or []
                ),
                "instrument_evidence": copy.deepcopy(
                    (multiplex.get("screening") or {}).get(
                        "instrument_evidence"
                    )
                    or {}
                ),
                "tool_failure": _compact_failure(
                    (multiplex.get("screening") or {}).get("tool_failure")
                ),
            },
            "relaxation": {
                "status": (multiplex.get("relaxation") or {}).get(
                    "status", "NOT_STARTED"
                ),
                "active_step": (multiplex.get("relaxation") or {}).get(
                    "active_step"
                ),
                "full_risk_count": len(
                    (multiplex.get("relaxation") or {}).get(
                        "full_risk_panels"
                    )
                    or []
                ),
                "partial_count": len(
                    (multiplex.get("relaxation") or {}).get(
                        "partial_panels"
                    )
                    or []
                ),
                "maximum_partial_target_count": (
                    multiplex.get("relaxation") or {}
                ).get("maximum_partial_target_count"),
                "relaxation_log": copy.deepcopy(
                    ((multiplex.get("relaxation") or {}).get(
                        "relaxation_log"
                    ) or [])[-10:]
                ),
                "relaxation_log_total": len(
                    (multiplex.get("relaxation") or {}).get(
                        "relaxation_log"
                    ) or []
                ),
                "failure_suggestions": copy.deepcopy(
                    (multiplex.get("relaxation") or {}).get(
                        "failure_suggestions"
                    )
                    or []
                ),
                "tool_failure": _compact_failure(
                    (multiplex.get("relaxation") or {}).get("tool_failure")
                ),
            },
            "balance": {
                "status": balance.get("status", "NOT_STARTED"),
                "policy_version": balance.get("policy_version"),
                "redesign_attempt_count": int(
                    balance.get("redesign_attempt_count", 0) or 0
                ),
                "maximum_redesign_attempts": balance.get(
                    "maximum_redesign_attempts"
                ),
                "selected_panel_count": len(
                    balance.get("selected_panels") or []
                ),
                "tm_warning_fallback": bool(
                    balance.get("tm_warning_fallback")
                ),
            },
            "scoring": {
                "status": scoring.get("status", "NOT_STARTED"),
                "policy_version": scoring.get("policy_version"),
                "scored_panel_count": len(scoring.get("panels") or []),
            },
            "deduplication": {
                "status": deduplication.get("status", "NOT_STARTED"),
                "policy_version": deduplication.get("policy_version"),
                "unique_panel_count": deduplication.get("unique_count", 0),
                "duplicate_panel_count": deduplication.get("duplicate_count", 0),
            },
            "selection": {
                "status": selection.get("status", "NOT_STARTED"),
                "selection_mode": selection.get("selection_mode"),
                "top3_count": len(selection.get("top3") or []),
                "strict_candidate_count": selection.get(
                    "strict_candidate_count", 0
                ),
                "risk_candidate_count": selection.get(
                    "risk_candidate_count", 0
                ),
            },
        },
        "internal_control": {
            "requested": internal_control.get(
                "requested", snapshot.get("include_internal_control")
            ),
            "status": internal_control.get("status", "NOT_STARTED"),
            "p5_status": internal_control.get("p5_status"),
            "p5_verdict": internal_control.get("p5_verdict"),
            "selected_gene": internal_control.get("selected_gene"),
            "candidate_gene_count": internal_control.get(
                "candidate_gene_count", 0
            ),
            "primer_scheme_count": internal_control.get(
                "primer_scheme_count", 0
            ),
            "quantity_verdict": internal_control.get("quantity_verdict"),
            "combination_pool_member": bool(
                internal_control.get("combination_pool_member")
            ),
            "channel_reservation": copy.deepcopy(
                internal_control.get("channel_reservation") or {}
            ),
            "failure": _compact_failure(internal_control.get("failure")),
        },
        "pending_children": waiting_children,
        "pending_child_count": len(waiting_children),
        "pending_resources": [
            {
                "target_id": item.get("target_id"),
                "display_name": item.get("display_name"),
                "failure": _compact_failure(item.get("failure") or {}),
                "resource_retry_count": int(
                    item.get("resource_retry_count") or 0
                ),
            }
            for item in target_results
            if item.get("status") == "BLOCKED_RESOURCE"
        ],
        "pending_internal_control": (
            internal_control.get("status") == "WAITING_USER"
        ),
        "report": {
            "report_id": batch.get("report_id"),
            "summary": copy.deepcopy(batch.get("report_summary") or {}),
            "exports": copy.deepcopy(batch.get("report_exports") or {}),
        },
        "detail_paging": {
            "tool": "get_wf02_batch",
            "views": ["summary", "target", "trace", "events", "relaxation"],
            "default_limit": 20,
        },
        "background_execution": {
            "schema_version": background_execution.get("schema_version")
            or WF02_BACKGROUND_EXECUTION_VERSION,
            "state": background_execution.get("state") or "IDLE",
            "attempt_count": int(
                background_execution.get("attempt_count") or 0
            ),
            "synchronous_wait_seconds": int(
                background_execution.get("synchronous_wait_seconds") or 0
            ),
            "requested_at": background_execution.get("requested_at"),
            "started_at": background_execution.get("started_at"),
            "heartbeat_at": background_execution.get("heartbeat_at"),
            "stopped_at": background_execution.get("stopped_at"),
            "recovered": bool(background_execution.get("recovered")),
            "last_error": _compact_failure(
                background_execution.get("last_error")
            ),
        },
    }
    report_ui_message = _wf02_report_ui_message(
        batch,
        attachment_formats=attachment_formats,
    )
    if report_ui_message is not None:
        result["ui_message"] = report_ui_message
        result["conversation_context"] = {
            "activeBatchId": batch.get("batch_id"),
            "activeBatchCode": batch.get("batch_code"),
            "workflow": "WF-02",
            "lifecycleState": batch.get("status"),
            "retainAcrossTurns": True,
            "followUpTools": {
                "report": "get_wf02_report",
                "panelDetail": "get_wf02_panel",
                "export": "get_wf02_export",
                "redesignCurrentTask": "create_wf02_version",
            },
            "newDesignTool": "create_wf02_batch",
        }
    if waiting_children:
        result["ui_message"] = copy.deepcopy(
            waiting_children[0].get("ui_message")
        )
        result["flowControl"] = {
            "action": "WAIT_FOR_CHILD_INPUT",
            "stopCurrentTurn": True,
            "resumeTool": "submit_wf02_child_response",
        }
    elif target_design.get("status") == "WAITING_RESOURCE":
        result["flowControl"] = {
            "action": "WAIT_FOR_RESOURCE",
            "stopCurrentTurn": True,
            "resumeTool": "run_wf02_until_blocked",
            "retryable": True,
        }
    elif target_design.get("status") == "WAITING_TOOL_RETRY":
        result["flowControl"] = {
            "action": "RETRY_AFTER_TOOL_READY",
            "stopCurrentTurn": True,
            "resumeTool": "run_wf02_until_blocked",
            "retryable": True,
        }
    elif internal_control.get("status") == "WAITING_USER":
        result["ui_message"] = copy.deepcopy(
            internal_control.get("ui_message")
        )
        result["flowControl"] = {
            "action": "WAIT_FOR_INTERNAL_CONTROL_INPUT",
            "stopCurrentTurn": True,
            "resumeTool": "submit_wf02_internal_control_response",
        }
    elif (multiplex.get("screening") or {}).get("status") == "WAITING_P4_RETRY":
        result["flowControl"] = {
            "action": "RETRY_AFTER_TOOL_READY",
            "stopCurrentTurn": True,
            "resumeTool": "run_wf02_until_blocked",
        }
    elif (multiplex.get("relaxation") or {}).get("status") == "WAITING_P4_RETRY":
        result["flowControl"] = {
            "action": "RETRY_AFTER_TOOL_READY",
            "stopCurrentTurn": True,
            "resumeTool": "run_wf02_until_blocked",
        }
    elif balance.get("status") == "REBUILDING_PANELS" and batch.get(
        "status"
    ) == "WAITING_P4_RETRY":
        result["flowControl"] = {
            "action": "RETRY_AFTER_TOOL_READY",
            "stopCurrentTurn": True,
            "resumeTool": "run_wf02_until_blocked",
        }
    background_state = str(background_execution.get("state") or "IDLE")
    if "flowControl" not in result and report_ui_message is None:
        if background_state in WF02_BACKGROUND_ACTIVE_STATES:
            result["user_message"] = (
                f"WF-02 任务 {batch.get('batch_code')} 正在后台运行。"
                "任务独立于 HiMat 连接运行；"
                "稍后输入“查询进度”即可获取状态或最终报告。"
            )
            result["flowControl"] = {
                "action": "WAIT_FOR_BACKGROUND",
                "stopCurrentTurn": True,
                "resumeTool": "get_wf02_batch",
                "userInitiatedQueryOnly": True,
            }
        elif background_state == "FAILED":
            result["user_message"] = (
                f"WF-02 任务 {batch.get('batch_code')} 的后台执行已停止。"
                "请查询任务失败原因后再决定是否重试。"
            )
            result["flowControl"] = {
                "action": "BACKGROUND_EXECUTION_FAILED",
                "stopCurrentTurn": True,
                "resumeTool": "run_wf02_until_blocked",
                "retryable": True,
            }
    return result


class WF02V01BatchService:
    """N01-N32 orchestration over independent WF-01, P5, P6, and P4 tools."""

    def __init__(
        self,
        root: str | Path,
        config: dict[str, Any],
        gateway: WF02ServiceGateway | None = None,
        artifact_resolver: Callable[[str], tuple[Path, dict[str, Any]]] | None = None,
        artifact_publisher: RustFSArtifactPublisher | None = None,
        dependency_contracts: dict[str, Any] | None = None,
    ) -> None:
        self.store = WF02V01Store(root)
        self.config = config
        self.gateway = gateway
        self.dependency_contracts = copy.deepcopy(
            dependency_contracts
            or {
                "wf01_candidates": wf01_candidate_contract_manifest(),
            }
        )
        self.artifact_publisher = (
            artifact_publisher
            if artifact_publisher is not None
            else RustFSArtifactPublisher.from_environment()
        )
        self._batch_locks_guard = Lock()
        self._batch_locks: dict[str, RLock] = {}
        self._create_lock = RLock()
        self.preflight_engine = WF02V01InputPreflight(
            config,
            artifact_resolver=artifact_resolver,
        )

    def _batch_lock(self, batch_id: str) -> RLock:
        with self._batch_locks_guard:
            return self._batch_locks.setdefault(str(batch_id), RLock())

    @staticmethod
    def is_terminal_batch(batch: dict[str, Any]) -> bool:
        return str(batch.get("status") or "") in {
            "COMPLETED",
            "PARTIAL_PANEL_AVAILABLE",
            "FAILED_INCOMPLETE_TARGET_COVERAGE",
            "FAILED_NO_COMPATIBLE_PANEL",
        }

    def recoverable_background_batch_ids(self) -> list[str]:
        recoverable: list[str] = []
        for batch in self.store.iter_batches():
            background = batch.get("background_execution") or {}
            if (
                str(background.get("state") or "")
                in WF02_BACKGROUND_ACTIVE_STATES
                and not self.is_terminal_batch(batch)
                and batch.get("batch_id")
            ):
                recoverable.append(str(batch["batch_id"]))
        return recoverable

    def set_background_execution(
        self,
        batch_id: str,
        state: str,
        *,
        recovered: bool | None = None,
        increment_attempt: bool = False,
        clear_error: bool = False,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist transport execution state without changing scientific cursors."""

        selected = str(state).upper()
        allowed = {
            "IDLE",
            "QUEUED",
            "RUNNING",
            "WAITING_USER",
            "WAITING_EXTERNAL",
            "PAUSED",
            "COMPLETED",
            "FAILED",
        }
        if selected not in allowed:
            raise InputValidationError(
                "WF-02 后台执行状态无效",
                details={"state": state, "supported": sorted(allowed)},
            )
        with self._batch_lock(batch_id):
            batch = self.store.get_batch(batch_id)
            value = batch.setdefault("background_execution", {})
            now = utc_now()
            value.update(
                {
                    "schema_version": WF02_BACKGROUND_EXECUTION_VERSION,
                    "state": selected,
                    "synchronous_wait_seconds": 0,
                }
            )
            value.setdefault("attempt_count", 0)
            if selected == "QUEUED":
                value["requested_at"] = now
                value["stopped_at"] = None
            if selected == "RUNNING":
                value["started_at"] = now
                value["heartbeat_at"] = now
                value["stopped_at"] = None
            if increment_attempt:
                value["attempt_count"] = int(value.get("attempt_count") or 0) + 1
            if recovered is not None:
                value["recovered"] = bool(recovered)
            if clear_error:
                value["last_error"] = None
            if error is not None:
                value["last_error"] = copy.deepcopy(error)
            if selected not in WF02_BACKGROUND_ACTIVE_STATES:
                value["heartbeat_at"] = now
                value["stopped_at"] = now
            # ``updated_at`` and the resume token describe scientific progress.
            # Transport-only state must not make a valid scientific token stale.
            self.store.save_batch(batch)
            return batch

    @classmethod
    def is_background_boundary(cls, summary: dict[str, Any]) -> bool:
        ui_message = summary.get("ui_message")
        if isinstance(ui_message, dict) and ui_message.get("type") == (
            "primer_design_output"
        ):
            return True
        if str(summary.get("status") or "") in {
            "COMPLETED",
            "PARTIAL_PANEL_AVAILABLE",
            "FAILED_INCOMPLETE_TARGET_COVERAGE",
            "FAILED_NO_COMPATIBLE_PANEL",
        }:
            return True
        flow_control = summary.get("flowControl")
        if isinstance(flow_control, dict) and flow_control.get("stopCurrentTurn"):
            if flow_control.get("action") != "WAIT_FOR_BACKGROUND":
                return True
        next_action = str(summary.get("next_action") or "")
        return not next_action.startswith(("RUN_", "CONTINUE_", "RETRY_"))

    @classmethod
    def background_state_for_summary(cls, summary: dict[str, Any]) -> str:
        if str(summary.get("status") or "") in {
            "COMPLETED",
            "PARTIAL_PANEL_AVAILABLE",
            "FAILED_INCOMPLETE_TARGET_COVERAGE",
            "FAILED_NO_COMPATIBLE_PANEL",
        }:
            return "COMPLETED"
        flow_control = summary.get("flowControl") or {}
        action = str(flow_control.get("action") or "")
        if action in {
            "WAIT_FOR_CHILD_INPUT",
            "WAIT_FOR_INTERNAL_CONTROL_INPUT",
        }:
            return "WAITING_USER"
        if action in {
            "WAIT_FOR_RESOURCE",
            "RETRY_AFTER_TOOL_READY",
        }:
            return "WAITING_EXTERNAL"
        return "PAUSED"

    @staticmethod
    def _resume_token(batch: dict[str, Any]) -> str:
        return wf02_resume_token(batch)

    def preflight(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.preflight_engine.run(copy.deepcopy(payload))

    @staticmethod
    def _append_event(value: dict[str, Any], event_type: str, **details: Any) -> None:
        events = value.setdefault("events", [])
        events.append(
            {
                "event_seq": len(events) + 1,
                "event_type": event_type,
                "created_at": utc_now(),
                **details,
            }
        )

    def _create_task(
        self,
        preflight: dict[str, Any],
        *,
        source_draft_id: str | None = None,
        creation_request_sha256: str | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        if not preflight.get("can_create_task"):
            raise InputValidationError("存在未解决的 WF-02 输入阻塞项")
        batch_id = (
            str(uuid5(NAMESPACE_URL, f"wf02-v0.1-draft:{source_draft_id}"))
            if source_draft_id
            else str(uuid4())
        )
        created_at = utc_now()
        user_input_snapshot = copy.deepcopy(preflight["user_input_snapshot"])
        node_trace = copy.deepcopy(preflight.get("node_trace") or [])
        node_trace.append(
            {
                "node": "N05",
                "status": "COMPLETED",
                "message": "已冻结用户输入、规则版本与任务版本",
            }
        )
        batch = {
            "schema_version": WF02_BATCH_SCHEMA_VERSION,
            "batch_id": batch_id,
            "batch_code": wf01_batch_code(
                batch_id,
                created_at,
                workflow="WF-02",
            ),
            "workflow": "WF-02",
            "workflow_version": WF02_WORKFLOW_VERSION,
            "policy_version": WF02_POLICY_VERSION,
            "dependency_contracts": copy.deepcopy(self.dependency_contracts),
            "task_version": 1,
            "source_batch_id": None,
            "source_version": None,
            "lineage_root_batch_id": batch_id,
            "source_draft_id": source_draft_id,
            "status": "READY_FOR_TARGET_DESIGN",
            "stage": "N05",
            "next_action": "RUN_TARGET_DESIGN",
            "created_at": created_at,
            "updated_at": created_at,
            "task_name": (preflight.get("normalised_input") or {}).get("task_name"),
            "user_input_snapshot": user_input_snapshot,
            "input_snapshot_sha256": _canonical_sha256(user_input_snapshot),
            "runtime_config_snapshot": copy.deepcopy(
                preflight.get("runtime_config_snapshot") or {}
            ),
            "execution_options_snapshot": copy.deepcopy(
                preflight.get("execution_options_snapshot") or {}
            ),
            "creation_request": (
                {
                    "schema_version": WF02_CREATE_REPLAY_SCHEMA_VERSION,
                    "request_sha256": creation_request_sha256,
                }
                if creation_request_sha256
                else None
            ),
            "background_execution": {
                "schema_version": WF02_BACKGROUND_EXECUTION_VERSION,
                "state": "IDLE",
                "attempt_count": 0,
                "synchronous_wait_seconds": 0,
                "requested_at": None,
                "started_at": None,
                "heartbeat_at": None,
                "stopped_at": None,
                "recovered": False,
                "last_error": None,
            },
            "rule_snapshot": copy.deepcopy(preflight.get("rule_snapshot") or {}),
            "preflight": copy.deepcopy(preflight),
            "workflow_trace": node_trace,
            "target_design": None,
            "internal_control": None,
            "events": [],
        }
        self._append_event(
            batch,
            "WF02_TASK_CREATED",
            status=batch["status"],
            stage="N05",
            input_snapshot_sha256=batch["input_snapshot_sha256"],
            source_draft_id=source_draft_id,
            dependency_contracts=copy.deepcopy(batch["dependency_contracts"]),
        )
        if persist:
            self.store.save_batch(batch)
        return batch

    @staticmethod
    def _creation_request_sha256(
        preflight: dict[str, Any],
        creation_scope: str,
    ) -> str:
        """Fingerprint one caller-scoped create request without storing identity."""

        return _canonical_sha256(
            {
                "schema_version": WF02_CREATE_REPLAY_SCHEMA_VERSION,
                "scope": creation_scope,
                "user_input_snapshot": preflight.get("user_input_snapshot") or {},
                "runtime_config_snapshot": preflight.get("runtime_config_snapshot") or {},
                "execution_options_snapshot": (
                    preflight.get("execution_options_snapshot") or {}
                ),
            }
        )

    def _recent_creation_replay(
        self,
        request_sha256: str,
        *,
        window_seconds: int,
    ) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        most_recent: tuple[datetime, dict[str, Any]] | None = None
        for batch in self.store.iter_batches():
            creation = batch.get("creation_request") or {}
            if creation.get("request_sha256") != request_sha256:
                continue
            try:
                created_at = datetime.fromisoformat(str(batch["created_at"]))
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                age_seconds = (now - created_at.astimezone(timezone.utc)).total_seconds()
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= age_seconds <= max(0, int(window_seconds)):
                if most_recent is None or created_at > most_recent[0]:
                    most_recent = (created_at, batch)
        if most_recent is None:
            return None
        replayed = copy.deepcopy(most_recent[1])
        replayed["creation_replayed"] = True
        return replayed

    def _create_draft(
        self,
        payload: dict[str, Any],
        preflight: dict[str, Any],
        *,
        persist: bool = True,
    ) -> dict[str, Any]:
        draft_id = str(uuid4())
        created_at = utc_now()
        draft = {
            "schema_version": WF02_DRAFT_SCHEMA_VERSION,
            "draft_id": draft_id,
            "workflow": "WF-02",
            "workflow_version": WF02_WORKFLOW_VERSION,
            "policy_version": WF02_POLICY_VERSION,
            "status": "WAITING_USER",
            "stage": "N04",
            "next_action": "WAIT_FOR_USER",
            "created_at": created_at,
            "updated_at": created_at,
            "payload": copy.deepcopy(payload),
            "preflight": copy.deepcopy(preflight),
            "agent_interactions": [
                _build_issue_interaction(draft_id, issue)
                for issue in preflight.get("issues") or []
            ],
            "created_batch_id": None,
            "events": [],
        }
        self._append_event(
            draft,
            "WF02_INPUT_CONFIRMATION_REQUESTED",
            status="WAITING_USER",
            issue_codes=[
                issue.get("code") for issue in preflight.get("issues") or []
            ],
        )
        if persist:
            self.store.save_draft(draft)
        return draft

    @staticmethod
    def _draft_result(draft: dict[str, Any]) -> dict[str, Any]:
        return {
            "workflow": "WF-02",
            "draft_id": draft.get("draft_id"),
            "status": draft.get("status"),
            "stage": draft.get("stage"),
            "next_action": draft.get("next_action"),
            "preflight": copy.deepcopy(draft.get("preflight") or {}),
            "ui_message": _draft_ui_message(draft),
        }

    def create_batch(
        self,
        payload: dict[str, Any],
        *,
        persist: bool = True,
        creation_scope: str | None = None,
        replay_window_seconds: int = WF02_CREATE_REPLAY_WINDOW_SECONDS,
    ) -> dict[str, Any]:
        prepared = copy.deepcopy(payload)
        preflight = self.preflight(prepared)
        if preflight["can_create_task"]:
            if persist and creation_scope:
                request_sha256 = self._creation_request_sha256(
                    preflight,
                    creation_scope,
                )
                with self._create_lock:
                    replayed = self._recent_creation_replay(
                        request_sha256,
                        window_seconds=replay_window_seconds,
                    )
                    if replayed is not None:
                        return replayed
                    return self._create_task(
                        preflight,
                        creation_request_sha256=request_sha256,
                        persist=True,
                    )
            return self._create_task(preflight, persist=persist)
        return self._draft_result(
            self._create_draft(prepared, preflight, persist=persist)
        )

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        with self._batch_lock(batch_id):
            batch = self.store.get_batch(batch_id)
            return self._ensure_exports_published(batch)

    def get_persisted_background_snapshot(self, batch_id: str) -> dict[str, Any]:
        """Read the last atomic snapshot without waiting on the execution lock.

        The background worker can hold the per-task lock while one scientific
        execution unit is running.  A request whose synchronous wait deadline
        has expired must still be able to return the last committed state.  The
        store writes through an atomic replacement, so this read observes
        either the previous complete snapshot or the newly committed one.
        """

        return self.store.get_batch(batch_id)

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        return self._draft_result(self.store.get_draft(draft_id))

    @staticmethod
    def _submitted_items(response: dict[str, Any]) -> list[dict[str, Any]]:
        if response.get("type") != "agent_interaction_response":
            raise InputValidationError(
                "交互回复 type 必须为 agent_interaction_response"
            )
        if response.get("task") != "MULTIPLEX_PANEL":
            raise InputValidationError(
                "交互回复 task 必须为 MULTIPLEX_PANEL"
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
        if not all(isinstance(item, dict) for item in submitted):
            raise InputValidationError("每条 WF-02 交互回复必须为对象")
        return submitted

    @staticmethod
    def _apply_field_values(
        payload: dict[str, Any],
        field_values: dict[str, Any],
    ) -> None:
        aliases = {
            "targets": "targets",
            "targetList": "targets",
            "target_list": "targets",
            "host_species": "host_species",
            "hostSpecies": "host_species",
            "host": "host_species",
            "include_internal_control": "include_internal_control",
            "includeInternalControl": "include_internal_control",
            "need_internal_control": "include_internal_control",
        }
        alias_groups = {
            "targets": {"targets", "targetList", "target_list"},
            "host_species": {"host_species", "hostSpecies", "host"},
            "include_internal_control": {
                "include_internal_control",
                "includeInternalControl",
                "need_internal_control",
            },
        }
        applied = False
        for key, value in field_values.items():
            canonical = aliases.get(str(key))
            if canonical is None:
                continue
            if canonical == "targets" and isinstance(value, str):
                value = _parse_target_text(value)
            for alias in alias_groups[canonical]:
                if alias != canonical:
                    payload.pop(alias, None)
            payload[canonical] = copy.deepcopy(value)
            applied = True
        if not applied:
            raise InputValidationError(
                "WF-02 输入确认没有提交可识别的修正字段",
                details={"supported_fields": sorted(set(aliases.values()))},
            )

    def submit_input_response(
        self,
        draft_id: str,
        response: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(response, dict):
            raise InputValidationError("WF-02 交互回复必须为对象")
        draft = self.store.get_draft(draft_id)
        if draft.get("status") == "RESOLVED" and draft.get("created_batch_id"):
            batch = self.store.get_batch(str(draft["created_batch_id"]))
            return {
                "workflow": "WF-02",
                "draft_id": draft_id,
                "batch_id": batch["batch_id"],
                "status": batch["status"],
                "next_action": batch["next_action"],
                "submission_replayed": True,
                "batch": batch,
            }

        submitted = self._submitted_items(response)
        submitted_by_id: dict[str, dict[str, Any]] = {}
        for item in submitted:
            interaction_id = _text(item.get("interactionId"))
            if not interaction_id:
                raise InputValidationError("WF-02 交互回复缺少 interactionId")
            if interaction_id in submitted_by_id:
                raise InputValidationError(
                    "WF-02 交互回复包含重复 interactionId",
                    details={"interaction_id": interaction_id},
                )
            submitted_by_id[interaction_id] = item

        persisted = draft.get("agent_interactions") or []
        persisted_by_id = {
            str((item.get("data") or {}).get("interactionId")): item
            for item in persisted
        }
        waiting_ids = {
            interaction_id
            for interaction_id, interaction in persisted_by_id.items()
            if (interaction.get("data") or {}).get("status") == "WAITING_USER"
        }
        if set(submitted_by_id) != waiting_ids:
            raise InputValidationError(
                "必须一次回答本轮全部 WF-02 阻塞项",
                details={
                    "expected_interaction_ids": sorted(waiting_ids),
                    "submitted_interaction_ids": sorted(submitted_by_id),
                },
            )

        revised_payload = copy.deepcopy(draft.get("payload") or {})
        submitted_at = utc_now()
        for interaction_id in sorted(waiting_ids):
            submitted_item = submitted_by_id[interaction_id]
            if submitted_item.get("answer") != "EDIT":
                raise InputValidationError(
                    "WF-02 输入确认当前只接受 EDIT 动作",
                    details={"interaction_id": interaction_id},
                )
            field_values = submitted_item.get("fieldValues") or {}
            if not isinstance(field_values, dict):
                raise InputValidationError("fieldValues 必须为对象")
            self._apply_field_values(revised_payload, field_values)
            data = persisted_by_id[interaction_id]["data"]
            data["status"] = "RESOLVED"
            data["resolvedAt"] = submitted_at
            data["userResponse"] = {
                "selectedOption": "EDIT",
                "fieldValues": copy.deepcopy(field_values),
                "customInstruction": submitted_item.get("customInstruction"),
                "submittedAt": submitted_at,
            }
            self._append_event(
                draft,
                "WF02_INPUT_BLOCKER_RESOLVED",
                status="RESOLVED",
                interaction_id=interaction_id,
                field=(data.get("context") or {}).get("field"),
            )

        preflight = self.preflight(revised_payload)
        draft["payload"] = revised_payload
        draft["preflight"] = copy.deepcopy(preflight)
        draft["updated_at"] = submitted_at
        if not preflight["can_create_task"]:
            new_interactions = [
                _build_issue_interaction(draft_id, issue)
                for issue in preflight.get("issues") or []
            ]
            draft["agent_interactions"].extend(new_interactions)
            draft["status"] = "WAITING_USER"
            draft["stage"] = "N04"
            draft["next_action"] = "WAIT_FOR_USER"
            self._append_event(
                draft,
                "WF02_INPUT_CONFIRMATION_REOPENED",
                status="WAITING_USER",
                issue_codes=[
                    issue.get("code") for issue in preflight.get("issues") or []
                ],
            )
            self.store.save_draft(draft)
            return self._draft_result(draft)

        batch = self._create_task(preflight, source_draft_id=draft_id)
        draft["status"] = "RESOLVED"
        draft["stage"] = "N05"
        draft["next_action"] = "RUN_TARGET_DESIGN"
        draft["created_batch_id"] = batch["batch_id"]
        self._append_event(
            draft,
            "WF02_TASK_CREATED_FROM_CONFIRMED_INPUT",
            status="RESOLVED",
            batch_id=batch["batch_id"],
        )
        self.store.save_draft(draft)
        return {
            "workflow": "WF-02",
            "draft_id": draft_id,
            "batch_id": batch["batch_id"],
            "status": batch["status"],
            "stage": batch["stage"],
            "next_action": batch["next_action"],
            "submission_replayed": False,
            "batch": batch,
        }

    def _require_gateway(self, batch: dict | None = None) -> WF02ServiceGateway:
        if self.gateway is None:
            raise InputValidationError(
                "WF-02 执行服务未配置 WF-01/P5 公共服务网关"
            )
        if batch is not None and hasattr(self.gateway, "for_batch"):
            return self.gateway.for_batch(batch)
        return self.gateway

    @staticmethod
    def _record_node(
        batch: dict[str, Any],
        node: str,
        status: str,
        message: str,
        **details: Any,
    ) -> None:
        if node not in WF02_NODES:
            raise InputValidationError(
                "WF-02 节点编号无效", details={"node": node}
            )
        batch.setdefault("workflow_trace", []).append(
            {
                "node": node,
                "status": status,
                "message": message,
                "recorded_at": utc_now(),
                **copy.deepcopy(details),
            }
        )

    def _save_batch(self, batch: dict[str, Any]) -> dict[str, Any]:
        batch["updated_at"] = utc_now()
        self.store.save_batch(batch)
        return batch

    @staticmethod
    def _target_has_template(target: dict[str, Any]) -> bool:
        source = target.get("source_input")
        if not isinstance(source, dict):
            return False
        return any(
            source.get(key)
            for key in (
                "sequence",
                "sequences",
                "fasta_text",
                "fasta_path",
                "input_artifact_id",
                "sequence_files",
                "template_records",
            )
        )

    def _wf01_child_payload(
        self,
        batch: dict[str, Any],
        target: dict[str, Any],
    ) -> dict[str, Any]:
        source = target.get("source_input")
        payload = copy.deepcopy(source) if isinstance(source, dict) else {}
        target_id = str(target["target_id"])
        display_name = str(target.get("display_name") or target_id)
        # WF-02 owns the target boundary: every source file is one detection
        # target, and all records inside it are homologous alignment members.
        payload.pop("target_manifest", None)
        payload["sequence_mode"] = "alignment_target"
        sequence = payload.pop("sequence", None)
        if isinstance(sequence, str) and sequence.strip():
            payload.setdefault(
                "fasta_text",
                f">{target_id}\n{sequence.strip()}\n",
            )
        sequences = payload.pop("sequences", None)
        if (
            sequences
            and not payload.get("template_records")
            and isinstance(sequences, list)
        ):
            payload["template_records"] = [
                {
                    "record_name": f"{target_id}_{index}",
                    "sequence": str(value),
                }
                for index, value in enumerate(sequences, start=1)
            ]
            payload["sequence_mode"] = "population_alignment"

        batch_defaults = copy.deepcopy(payload.get("batch_defaults") or {})
        target_organism = (
            payload.pop("target_organism", None)
            or payload.pop("target_species", None)
            or payload.pop("organism", None)
            or {"name": display_name}
        )
        if isinstance(target_organism, str):
            target_organism = {"name": target_organism}
        if batch_defaults.get("target_organism") in (None, "", {}):
            batch_defaults["target_organism"] = target_organism
        host = (batch.get("user_input_snapshot") or {}).get("host_species")
        if host and not batch_defaults.get("hosts"):
            batch_defaults["hosts"] = [{"name": host}]
        payload["batch_defaults"] = batch_defaults
        payload["task_name"] = (
            f"{batch.get('batch_code') or batch.get('batch_id')}::{target_id}"
        )
        options = batch.get("execution_options_snapshot") or {}
        if "allow_fallback" in options:
            payload["allow_fallback"] = bool(options["allow_fallback"])
        return payload

    @staticmethod
    def _local_target_input_interaction(
        batch: dict[str, Any],
        target: dict[str, Any],
    ) -> dict[str, Any]:
        interaction = build_interaction(
            task_id=str(batch["batch_id"]),
            stage="WF02_N06_WF01_INPUT",
            blocker_code="WF02_TARGET_TEMPLATE_REQUIRED",
            message=(
                f"靶标 {target.get('display_name') or target.get('target_id')} "
                "缺少可供 WF-01 设计的模板序列或 FASTA 文件。"
            ),
            blocking=True,
            context={
                "workflow": "WF-02",
                "targetId": target.get("target_id"),
                "field": "sequence",
            },
            options=[
                {
                    "value": "PROVIDE_TEMPLATE",
                    "label": "补充模板序列",
                    "description": "补充后只恢复该靶标的 WF-01 子任务。",
                }
            ],
            input_fields=[
                {
                    "key": "sequence",
                    "label": "模板序列（5′→3′）",
                    "inputType": "TEXTAREA",
                    "required": True,
                }
            ],
        )
        return {
            "type": "agent_interaction",
            "task": "MULTIPLEX_PANEL",
            "taskId": batch.get("batch_id"),
            "targetId": target.get("target_id"),
            **copy.deepcopy(interaction.get("data") or {}),
            "flowControl": {
                "action": "WAIT_FOR_USER",
                "stopCurrentTurn": True,
                "resumeMessageType": "agent_interaction_response",
            },
        }

    def _collect_wf01_candidates(
        self,
        report: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        candidates, evidence = select_wf01_candidates(report, limit=5)
        evidence["reason_codes"] = _reason_codes(
            evidence.get("conclusion") or {}
        )
        return candidates, evidence

    def _assess_candidates_with_p6(
        self,
        candidates: list[dict[str, Any]],
        batch: dict | None = None,
    ) -> dict[str, Any]:
        """Attach P6 evidence to retained candidates without making it a blocker."""
        summary = _empty_p6_summary()
        summary["candidate_count"] = len(candidates)
        if not candidates:
            summary["status"] = "NOT_APPLICABLE"
            return summary

        gateway = self._require_gateway(batch)
        for candidate in candidates:
            sequence = _candidate_amplicon_sequence(candidate)
            if not sequence:
                assessment = _p6_not_evaluated(
                    "AMPLICON_SEQUENCE_MISSING",
                    "候选缺少扩增子序列，P6 无法执行",
                    candidate_id=candidate.get("candidate_id"),
                )
            else:
                try:
                    assessment = gateway.assess_p6_amplicon_structure(sequence)
                    returned_status = (
                        str(assessment.get("status") or "").upper()
                        if isinstance(assessment, dict)
                        else ""
                    )
                    if returned_status not in {
                        "PASS",
                        "CAUTION",
                        "WARNING",
                        "NOT_EVALUATED",
                    }:
                        assessment = _p6_not_evaluated(
                            "P6_RESULT_INVALID",
                            "P6 返回结果不符合契约",
                            candidate_id=candidate.get("candidate_id"),
                            returned_status=returned_status or None,
                        )
                except Exception as exc:
                    assessment = _p6_not_evaluated(
                        "P6_ASSESSMENT_FAILED",
                        "P6 评估异常，已按软性规则降级为未评估",
                        candidate_id=candidate.get("candidate_id"),
                        error=_error_payload(exc),
                    )

            assessment = copy.deepcopy(assessment)
            assessment["workflow_node"] = "N06"
            assessment["soft_rule"] = True
            candidate["amplicon_structure"] = assessment
            candidate["amplicon_secondary_structure"] = copy.deepcopy(
                assessment
            )

            candidate_warnings = candidate.get("warnings")
            if not isinstance(candidate_warnings, list):
                candidate_warnings = []
                candidate["warnings"] = candidate_warnings
            assessment_warnings = assessment.get("warnings") or []
            if not isinstance(assessment_warnings, list):
                assessment_warnings = []
            for warning in assessment_warnings:
                if isinstance(warning, str) and warning not in candidate_warnings:
                    candidate_warnings.append(warning)

            status = str(assessment.get("status") or "").upper()
            if status in {"PASS", "CAUTION", "WARNING"}:
                summary["evaluated_count"] += 1
                summary[f"{status.lower()}_count"] += 1
            else:
                summary["not_evaluated_count"] += 1

        if summary["evaluated_count"] == 0:
            summary["status"] = "NOT_EVALUATED"
        elif summary["not_evaluated_count"]:
            summary["status"] = "PARTIAL"
        else:
            summary["status"] = "COMPLETED"
        return summary

    @staticmethod
    def _target_source_for_result(
        batch: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        target_id = str(result["target_id"])
        frozen_target = next(
            item
            for item in (batch.get("user_input_snapshot") or {}).get("targets")
            or []
            if item.get("target_id") == target_id
        )
        source_target = copy.deepcopy(frozen_target)
        supplemental_input = result.get("supplemental_input") or {}
        if supplemental_input:
            source = source_target.get("source_input")
            if not isinstance(source, dict):
                source = {
                    "target_id": target_id,
                    "display_name": source_target.get("display_name"),
                }
                source_target["source_input"] = source
            source.update(copy.deepcopy(supplemental_input))
        return source_target

    def _advance_target_child(
        self,
        batch: dict[str, Any],
        result: dict[str, Any],
        *,
        source_target: dict[str, Any] | None = None,
    ) -> None:
        gateway = self._require_gateway(batch)
        target_id = str(result["target_id"])
        if source_target is None:
            source_target = self._target_source_for_result(batch, result)
        if not self._target_has_template(source_target):
            result.update(
                {
                    "status": "WAITING_USER",
                    "candidate_count": 0,
                    "child_next_action": "PROVIDE_TARGET_TEMPLATE",
                    "child_ui_message": self._local_target_input_interaction(
                        batch, source_target
                    ),
                }
            )
            return

        child_batch_id = result.get("active_wf01_batch_id") or result.get(
            "wf01_batch_id"
        )
        if not child_batch_id:
            child_payload = self._wf01_child_payload(batch, source_target)
            created = gateway.create_wf01_v02_batch(
                child_payload,
                require_full_route=True,
            )
            child_batch_id = created.get("batch_id")
            if not child_batch_id:
                raise InputValidationError(
                    "WF-01 子任务创建结果缺少 batch_id",
                    details={"target_id": target_id},
                )
            result.update(
                {
                    "wf01_batch_id": child_batch_id,
                    "wf01_batch_code": created.get("batch_code"),
                    "wf01_task_version": created.get("task_version", 1),
                    "active_wf01_batch_id": child_batch_id,
                    "active_wf01_batch_code": created.get("batch_code"),
                    "active_wf01_task_version": created.get("task_version", 1),
                }
            )
            result.setdefault("wf01_lineage", []).append(
                {
                    "batch_id": child_batch_id,
                    "batch_code": created.get("batch_code"),
                    "task_version": created.get("task_version", 1),
                    "purpose": (
                        "RESOURCE_RECOVERY_RETRY"
                        if int(result.get("resource_retry_count") or 0)
                        else "INITIAL_TARGET_DESIGN"
                    ),
                }
            )
            self._append_event(
                batch,
                "WF02_WF01_CHILD_CREATED",
                target_id=target_id,
                child_batch_id=child_batch_id,
                child_batch_code=created.get("batch_code"),
            )
            self._save_batch(batch)

        summary = gateway.run_wf01_v02_until_blocked(str(child_batch_id))
        result["child_status"] = summary.get("status")
        result["child_next_action"] = summary.get("next_action")
        result["child_ui_message"] = copy.deepcopy(summary.get("ui_message"))
        result["active_wf01_batch_code"] = summary.get("batch_code") or result.get(
            "active_wf01_batch_code"
        )
        result["active_wf01_task_version"] = summary.get(
            "task_version"
        ) or result.get("active_wf01_task_version")
        if summary.get("next_action") == "WAIT_FOR_USER":
            result["status"] = "WAITING_USER"
            return
        if summary.get("next_action") == "WAIT_FOR_STAGE_COMPLETION":
            result["status"] = "RUNNING"
            return
        if summary.get("next_action") != "COMPLETE":
            result.update(
                {
                    "status": "FAIL",
                    "failure": {
                        "code": "WF01_CHILD_NOT_COMPLETABLE",
                        "message": "WF-01 子任务没有可自动继续的阶段",
                        "details": {
                            "child_status": summary.get("status"),
                            "child_next_action": summary.get("next_action"),
                        },
                    },
                }
            )
            return

        report = gateway.get_wf01_v02_report(str(child_batch_id))
        candidates, evidence = self._collect_wf01_candidates(report)
        p6_summary = self._assess_candidates_with_p6(candidates, batch)
        result["child_target_id"] = evidence.get("child_target_id")
        result["wf01_result"] = evidence
        result["candidates"] = candidates
        result["candidate_count"] = len(candidates)
        result["p6_summary"] = p6_summary
        result["child_ui_message"] = None
        if candidates:
            result["status"] = "PASS"
            result.pop("failure", None)
        elif evidence.get("contract_status") == "INVALID":
            result["status"] = "FAIL"
            result["failure"] = {
                "code": "WF01_REPORT_INVALID",
                "message": "WF-01 完成报告不符合 WF-02 固定候选契约",
                "details": evidence,
            }
        else:
            reason_codes = list(evidence.get("reason_codes") or [])
            resource_reasons = [
                code
                for code in reason_codes
                if code in WF02_RESOURCE_BLOCKING_REASON_CODES
            ]
            no_candidate_reasons = [
                code
                for code in reason_codes
                if code in WF02_NO_CANDIDATE_REASON_CODES
            ]
            if resource_reasons:
                result["status"] = "BLOCKED_RESOURCE"
                result["failure"] = {
                    "code": "TARGET_EVALUATION_BLOCKED_RESOURCE",
                    "message": "WF-01 必需资源尚不可用，当前目标暂停并等待资源恢复",
                    "retryable": True,
                    "reason_codes": resource_reasons,
                    "details": evidence,
                }
            elif no_candidate_reasons:
                result["status"] = "FAIL"
                result["failure"] = {
                    "code": "TARGET_DESIGN_NO_CANDIDATES",
                    "message": "WF-01 已完成候选生成，但没有产生可进入后续阶段的候选",
                    "retryable": False,
                    "reason_codes": no_candidate_reasons,
                    "details": evidence,
                }
            else:
                result["status"] = "FAIL"
                result["failure"] = {
                    "code": "NO_WF01_QUALIFIED_CANDIDATES",
                    "message": "WF-01 权威合格候选集合为空",
                    "retryable": False,
                    "reason_codes": reason_codes,
                    "details": evidence,
                }

    def execute_target_design(
        self,
        batch_id: str,
        *,
        max_children: int | None = None,
    ) -> dict[str, Any]:
        """Execute N06-N09 while isolating each WF-01 child failure.

        ``max_children`` provides a bounded MCP execution mode.  Direct
        service/REST callers keep the historical all-target behaviour by
        leaving it unset, while the conversational tool advances one WF-01
        child per request so a multi-target panel does not occupy one HTTP
        request for several minutes.
        """
        self._require_gateway()
        if max_children is not None and (
            isinstance(max_children, bool)
            or not isinstance(max_children, int)
            or max_children < 1
        ):
            raise InputValidationError(
                "max_children 必须为正整数",
                details={"max_children": max_children},
            )
        batch = self.store.get_batch(batch_id)
        if (
            batch.get("stage") == "N09"
            and (batch.get("target_design") or {}).get("status") == "COMPLETED"
        ):
            return batch
        if batch.get("stage") not in {"N05", "N06", "N08", "N09"}:
            if batch.get("target_design") is not None:
                return batch
            raise InputValidationError(
                "WF-02 当前状态不能执行靶标设计",
                details={"stage": batch.get("stage"), "status": batch.get("status")},
            )
        if batch.get("target_design") is None:
            targets = (batch.get("user_input_snapshot") or {}).get("targets") or []
            batch["target_design"] = {
                "status": "RUNNING",
                "requested_targets": len(targets),
                "succeeded_targets": 0,
                "failed_targets": 0,
                "failed_target_ids": [],
                "blocked_targets": 0,
                "blocked_target_ids": [],
                "waiting_targets": 0,
                "coverage": {
                    "status": "PENDING",
                    "covered_targets": 0,
                    "requested_targets": len(targets),
                    "complete": False,
                    "message": "单靶标候选生成尚未完成",
                },
                "coverage_gaps": [],
                "p6_summary": _empty_p6_summary(),
                "targets": [
                    {
                        "target_id": target.get("target_id"),
                        "display_name": target.get("display_name"),
                        "input_order": target.get("input_order"),
                        "purpose": "TARGET_DESIGN",
                        "status": "NOT_STARTED",
                        "candidate_count": 0,
                        "candidates": [],
                        "p6_summary": _empty_p6_summary(),
                        "wf01_lineage": [],
                    }
                    for target in targets
                ],
            }
            batch.update(
                {
                    "status": "DESIGNING_TARGETS",
                    "stage": "N06",
                    "next_action": "RUN_TARGET_DESIGN",
                }
            )
            self._record_node(
                batch,
                "N06",
                "RUNNING",
                "开始逐靶标调用 WF-01 Step 1–5",
            )
            self._append_event(
                batch,
                "WF02_TARGET_DESIGN_STARTED",
                status="DESIGNING_TARGETS",
            )
            self._save_batch(batch)

        target_design = batch["target_design"]
        if batch.get("status") == "WAITING_TARGET_RESOURCES":
            blocked_results = [
                item
                for item in target_design.get("targets") or []
                if item.get("status") == "BLOCKED_RESOURCE"
            ]
            retry_cursor = int(target_design.get("resource_retry_cursor") or 0)
            if max_children is None:
                selected_for_retry = blocked_results
            elif blocked_results:
                selected_for_retry = [
                    blocked_results[retry_cursor % len(blocked_results)]
                ]
                target_design["resource_retry_cursor"] = (
                    retry_cursor + 1
                ) % len(blocked_results)
            else:
                selected_for_retry = []
            for result in selected_for_retry:
                previous_failure = copy.deepcopy(result.get("failure") or {})
                if previous_failure:
                    result.setdefault("resource_failure_history", []).append(
                        previous_failure
                    )
                result["resource_retry_count"] = int(
                    result.get("resource_retry_count") or 0
                ) + 1
                for key in (
                    "wf01_batch_id",
                    "wf01_batch_code",
                    "wf01_task_version",
                    "active_wf01_batch_id",
                    "active_wf01_batch_code",
                    "active_wf01_task_version",
                    "child_status",
                    "child_next_action",
                    "child_ui_message",
                    "child_target_id",
                    "wf01_result",
                    "failure",
                ):
                    result.pop(key, None)
                result.update(
                    {
                        "status": "NOT_STARTED",
                        "candidate_count": 0,
                        "candidates": [],
                        "p6_summary": _empty_p6_summary(),
                    }
                )
            batch.update(
                {
                    "status": "DESIGNING_TARGETS",
                    "stage": "N06",
                    "next_action": "RUN_TARGET_DESIGN",
                }
            )
        advanced_children = 0
        for result in target_design.get("targets") or []:
            if result.get("status") in {"PASS", "FAIL", "BLOCKED_RESOURCE"}:
                continue
            try:
                source_target = self._target_source_for_result(batch, result)
                has_template = self._target_has_template(source_target)
                if (
                    max_children is not None
                    and has_template
                    and advanced_children >= max_children
                ):
                    # Missing-template discovery is local and inexpensive, so keep
                    # scanning for all required user inputs after the bounded
                    # child-execution budget has been consumed.
                    continue
                if has_template:
                    advanced_children += 1
                self._advance_target_child(
                    batch,
                    result,
                    source_target=source_target,
                )
            except Exception as exc:
                permanent_input_error = isinstance(exc, InputValidationError)
                result.update(
                    {
                        "status": (
                            "FAIL"
                            if permanent_input_error
                            else "WAITING_TOOL_RETRY"
                        ),
                        "candidate_count": 0,
                        "candidates": [],
                        "p6_summary": {
                            **_empty_p6_summary(),
                            "status": (
                                "NOT_APPLICABLE"
                                if permanent_input_error
                                else "NOT_STARTED"
                            ),
                        },
                        "failure": (
                            _error_payload(exc)
                            if permanent_input_error
                            else {
                                "code": "WF01_CHILD_TOOL_ERROR",
                                "message": "WF-01 子任务调用暂时失败，已保留当前子任务等待重试",
                                "retryable": True,
                                "details": _error_payload(exc),
                            }
                        ),
                    }
                )
            p6_summary = result.get("p6_summary") or _empty_p6_summary()
            if p6_summary.get("status") != "NOT_STARTED":
                self._record_node(
                    batch,
                    "N06",
                    (
                        "P6_DEGRADED"
                        if p6_summary.get("status") in {"PARTIAL", "NOT_EVALUATED"}
                        else "P6_COMPLETED"
                    ),
                    (
                        f"靶标 {result.get('target_id')} P6："
                        f"评估 {p6_summary.get('evaluated_count', 0)}/"
                        f"{p6_summary.get('candidate_count', 0)} 套候选"
                    ),
                    target_id=result.get("target_id"),
                    p6_summary=copy.deepcopy(p6_summary),
                )
            self._record_node(
                batch,
                "N07",
                (
                    "PASS"
                    if result.get("status") == "PASS"
                    else "WAITING"
                    if result.get("status")
                    in {"WAITING_USER", "RUNNING", "WAITING_TOOL_RETRY"}
                    else "BLOCKED_RESOURCE"
                    if result.get("status") == "BLOCKED_RESOURCE"
                    else "FAIL"
                ),
                (
                    f"靶标 {result.get('target_id')} 返回 "
                    f"{result.get('candidate_count', 0)} 套 BLAST 合格候选"
                ),
                target_id=result.get("target_id"),
                child_batch_id=result.get("active_wf01_batch_id"),
            )
            if result.get("status") == "PASS":
                self._record_node(
                    batch,
                    "N09",
                    "PASS",
                    f"保留靶标 {result.get('target_id')} Top {result.get('candidate_count')}",
                    target_id=result.get("target_id"),
                )
            elif result.get("status") == "FAIL":
                current_covered = sum(
                    item.get("status") == "PASS"
                    for item in target_design.get("targets") or []
                )
                self._record_node(
                    batch,
                    "N08",
                    "FAIL_CONTINUE",
                    (
                        f"靶标 {result.get('target_id')} 标记 FAIL，继续其他靶标；"
                        f"当前覆盖 {current_covered}/"
                        f"{target_design.get('requested_targets')}"
                    ),
                    target_id=result.get("target_id"),
                    failure=copy.deepcopy(result.get("failure")),
                    current_coverage={
                        "covered_targets": current_covered,
                        "requested_targets": target_design.get("requested_targets"),
                    },
                )
            elif result.get("status") == "BLOCKED_RESOURCE":
                self._record_node(
                    batch,
                    "N08",
                    "BLOCKED_RESOURCE",
                    f"目标 {result.get('target_id')} 等待必需资源恢复",
                    target_id=result.get("target_id"),
                    failure=copy.deepcopy(result.get("failure")),
                )
            elif result.get("status") == "WAITING_TOOL_RETRY":
                self._record_node(
                    batch,
                    "N06",
                    "WAITING_TOOL",
                    f"目标 {result.get('target_id')} 的 WF-01 子任务等待工具重试",
                    target_id=result.get("target_id"),
                    failure=copy.deepcopy(result.get("failure")),
                )
            self._save_batch(batch)

        results = target_design.get("targets") or []
        remaining_results = [
            item
            for item in results
            if item.get("status")
            not in {
                "PASS",
                "FAIL",
                "BLOCKED_RESOURCE",
                "WAITING_USER",
                "RUNNING",
                "WAITING_TOOL_RETRY",
            }
        ]
        waiting_results = [
            item
            for item in results
            if item.get("status") in {"WAITING_USER", "RUNNING"}
        ]
        tool_retry_results = [
            item
            for item in results
            if item.get("status") == "WAITING_TOOL_RETRY"
        ]
        if max_children is not None and remaining_results and not waiting_results:
            succeeded_targets = sum(
                item.get("status") == "PASS" for item in results
            )
            failed_targets = sum(
                item.get("status") == "FAIL" for item in results
            )
            blocked_targets = sum(
                item.get("status") == "BLOCKED_RESOURCE" for item in results
            )
            completed_targets = succeeded_targets + failed_targets + blocked_targets
            requested_targets = int(target_design["requested_targets"])
            target_design.update(
                {
                    "status": "RUNNING",
                    "succeeded_targets": succeeded_targets,
                    "failed_targets": failed_targets,
                    "failed_target_ids": [
                        str(item.get("target_id"))
                        for item in results
                        if item.get("status") == "FAIL"
                        and item.get("target_id")
                    ],
                    "blocked_targets": blocked_targets,
                    "blocked_target_ids": [
                        str(item.get("target_id"))
                        for item in results
                        if item.get("status") == "BLOCKED_RESOURCE"
                        and item.get("target_id")
                    ],
                    "waiting_targets": 0,
                    "coverage_gaps": [
                        {
                            "target_id": item.get("target_id"),
                            "display_name": item.get("display_name"),
                            "input_order": item.get("input_order"),
                            "reason_code": (item.get("failure") or {}).get(
                                "code"
                            ),
                            "reason_message": (item.get("failure") or {}).get(
                                "message"
                            ),
                            "wf01_batch_id": item.get("active_wf01_batch_id")
                            or item.get("wf01_batch_id"),
                            "failure": copy.deepcopy(item.get("failure") or {}),
                        }
                        for item in results
                        if item.get("status") in {"FAIL", "BLOCKED_RESOURCE"}
                    ],
                    "p6_summary": _aggregate_p6_summaries(
                        [
                            item.get("p6_summary") or _empty_p6_summary()
                            for item in results
                            if item.get("status")
                            in {"PASS", "FAIL", "BLOCKED_RESOURCE"}
                        ]
                    ),
                    "coverage": {
                        "status": "PENDING",
                        "covered_targets": succeeded_targets,
                        "requested_targets": requested_targets,
                        "complete": False,
                        "message": (
                            "候选生成分步进行中：已处理 "
                            f"{completed_targets}/{requested_targets} 个靶标，"
                            f"剩余 {len(remaining_results)} 个"
                        ),
                    },
                }
            )
            batch.update(
                {
                    "status": "DESIGNING_TARGETS",
                    "stage": "N06",
                    "next_action": "CONTINUE_TARGET_DESIGN",
                }
            )
            self._append_event(
                batch,
                "WF02_TARGET_DESIGN_PROGRESS",
                status="DESIGNING_TARGETS",
                completed_targets=completed_targets,
                requested_targets=requested_targets,
                remaining_targets=len(remaining_results),
            )
            return self._save_batch(batch)

        target_design["succeeded_targets"] = sum(
            item.get("status") == "PASS" for item in results
        )
        target_design["failed_targets"] = sum(
            item.get("status") == "FAIL" for item in results
        )
        target_design["failed_target_ids"] = [
            str(item.get("target_id"))
            for item in results
            if item.get("status") == "FAIL" and item.get("target_id")
        ]
        target_design["blocked_targets"] = sum(
            item.get("status") == "BLOCKED_RESOURCE" for item in results
        )
        target_design["blocked_target_ids"] = [
            str(item.get("target_id"))
            for item in results
            if item.get("status") == "BLOCKED_RESOURCE" and item.get("target_id")
        ]
        target_design["waiting_targets"] = sum(
            item.get("status") in {"WAITING_USER", "RUNNING"}
            for item in results
        )
        target_design["tool_retry_targets"] = len(tool_retry_results)
        target_design["coverage_gaps"] = [
            {
                "target_id": item.get("target_id"),
                "display_name": item.get("display_name"),
                "input_order": item.get("input_order"),
                "reason_code": (item.get("failure") or {}).get("code"),
                "reason_message": (item.get("failure") or {}).get("message"),
                "wf01_batch_id": item.get("active_wf01_batch_id")
                or item.get("wf01_batch_id"),
                "failure": copy.deepcopy(item.get("failure") or {}),
            }
            for item in results
            if item.get("status") in {"FAIL", "BLOCKED_RESOURCE"}
        ]
        target_design["p6_summary"] = _aggregate_p6_summaries(
            [
                item.get("p6_summary") or _empty_p6_summary()
                for item in results
                if item.get("status") in {"PASS", "FAIL", "BLOCKED_RESOURCE"}
            ]
        )
        if target_design["waiting_targets"] or tool_retry_results:
            target_design["p6_summary"]["status"] = (
                "PARTIAL"
                if target_design["p6_summary"]["candidate_count"]
                else "NOT_STARTED"
            )
        failed_labels = [
            str(item.get("display_name") or item.get("target_id"))
            for item in results
            if item.get("status") == "FAIL"
        ]
        blocked_labels = [
            str(item.get("display_name") or item.get("target_id"))
            for item in results
            if item.get("status") == "BLOCKED_RESOURCE"
        ]
        reason_codes = [
            str((item.get("failure") or {}).get("code") or "UNKNOWN")
            for item in results
            if item.get("status") == "FAIL"
        ]
        covered_targets = int(target_design["succeeded_targets"])
        requested_targets = int(target_design["requested_targets"])
        if blocked_labels:
            coverage_message = (
                f"流程暂停：目标[{', '.join(blocked_labels)}]等待必需资源恢复，"
                f"当前已完成 {covered_targets}/{requested_targets} 个目标"
            )
        elif failed_labels:
            coverage_message = (
                f"面板不完整：靶标[{', '.join(failed_labels)}]设计失败"
                f"([{', '.join(reason_codes)}])，当前面板覆盖"
                f"{covered_targets}/{requested_targets}靶标"
            )
        elif target_design["waiting_targets"] or tool_retry_results:
            coverage_message = (
                f"候选生成进行中：当前完成 {covered_targets}/"
                f"{requested_targets} 靶标"
            )
        else:
            coverage_message = (
                f"面板候选覆盖完整：{covered_targets}/{requested_targets} 靶标"
            )
        target_design["coverage"] = {
            "status": (
                "BLOCKED"
                if blocked_labels
                else "INCOMPLETE"
                if failed_labels
                else "PENDING"
                if target_design["waiting_targets"] or tool_retry_results
                else "COMPLETE"
            ),
            "covered_targets": covered_targets,
            "requested_targets": requested_targets,
            "complete": (
                not blocked_labels
                and not failed_labels
                and not target_design["waiting_targets"]
                and not tool_retry_results
            ),
            "message": coverage_message,
        }
        multiplex = batch.setdefault(
            "multiplex",
            {
                "panel_size": requested_targets,
                "targets_order": [item.get("target_id") for item in results],
                "failed_targets": [],
            },
        )
        multiplex["failed_targets"] = copy.deepcopy(
            target_design["failed_target_ids"]
        )
        if tool_retry_results:
            target_design["status"] = "WAITING_TOOL_RETRY"
            batch.update(
                {
                    "status": "WAITING_TARGET_TOOL_RETRY",
                    "stage": "N06",
                    "next_action": "RETRY_TARGET_DESIGN",
                }
            )
            self._append_event(
                batch,
                "WF02_TARGET_TOOL_RETRY_REQUIRED",
                status="WAITING_TARGET_TOOL_RETRY",
                target_ids=[
                    str(item.get("target_id")) for item in tool_retry_results
                ],
            )
        elif target_design["waiting_targets"]:
            target_design["status"] = "WAITING_CHILD_INPUT"
            batch.update(
                {
                    "status": "WAITING_CHILD_INPUT",
                    "stage": "N06",
                    "next_action": "WAIT_FOR_CHILD_INPUT",
                }
            )
            self._record_node(
                batch,
                "N06",
                "WAITING_USER",
                f"{target_design['waiting_targets']} 个 WF-01 子任务等待输入或完成",
            )
        elif target_design["blocked_targets"]:
            target_design["status"] = "WAITING_RESOURCE"
            batch.update(
                {
                    "status": "WAITING_TARGET_RESOURCES",
                    "stage": "N08",
                    "next_action": "WAIT_FOR_RESOURCE",
                }
            )
            self._record_node(
                batch,
                "N08",
                "WAITING_RESOURCE",
                f"{target_design['blocked_targets']} 个 WF-01 子任务等待必需资源恢复",
                blocked_target_ids=copy.deepcopy(
                    target_design["blocked_target_ids"]
                ),
            )
            self._append_event(
                batch,
                "WF02_TARGET_RESOURCE_BLOCKED",
                status="WAITING_TARGET_RESOURCES",
                blocked_target_ids=copy.deepcopy(
                    target_design["blocked_target_ids"]
                ),
            )
        else:
            target_design["status"] = "COMPLETED"
            batch.update(
                {
                    "status": "READY_FOR_INTERNAL_CONTROL",
                    "stage": "N09",
                    "next_action": "RUN_INTERNAL_CONTROL",
                }
            )
            self._record_node(
                batch,
                "N06",
                "COMPLETED",
                "全部靶标均已独立完成候选生成与 P6 软性评估循环",
                succeeded_targets=target_design["succeeded_targets"],
                failed_targets=target_design["failed_targets"],
                failed_target_ids=copy.deepcopy(
                    target_design["failed_target_ids"]
                ),
                coverage=copy.deepcopy(target_design["coverage"]),
                p6_summary=copy.deepcopy(target_design["p6_summary"]),
            )
            self._append_event(
                batch,
                "WF02_TARGET_DESIGN_COMPLETED",
                status="READY_FOR_INTERNAL_CONTROL",
                succeeded_targets=target_design["succeeded_targets"],
                failed_targets=target_design["failed_targets"],
                failed_target_ids=copy.deepcopy(
                    target_design["failed_target_ids"]
                ),
                coverage=copy.deepcopy(target_design["coverage"]),
                p6_summary=copy.deepcopy(target_design["p6_summary"]),
            )
        return self._save_batch(batch)

    def _internal_control_payload(
        self,
        batch: dict[str, Any],
        internal_control: dict[str, Any],
    ) -> dict[str, Any]:
        options = batch.get("execution_options_snapshot") or {}
        configured = options.get("internal_control_payload")
        payload = copy.deepcopy(configured) if isinstance(configured, dict) else {}
        payload.update(copy.deepcopy(internal_control.get("input_overrides") or {}))
        payload["host_species"] = _p5_host_species(
            (batch.get("user_input_snapshot") or {}).get("host_species")
        )
        for key in ("allow_fallback", "host_db", "cross_db", "assay_id"):
            if key in options and key not in payload:
                payload[key] = copy.deepcopy(options[key])
        # N09 contains candidate pools rather than one fixed panel.  P5's
        # aggregate cross-check would reject an otherwise usable internal
        # control after any one bad pair, so authoritative cross-compatibility
        # remains with N15/P4 for each concrete panel combination.
        payload.pop("existing_targets", None)
        return payload

    @staticmethod
    def _candidate_gene_count(p5_result: dict[str, Any]) -> int:
        genes = {
            str(item.get("gene"))
            for item in (p5_result.get("gene_attempts") or [])
            if isinstance(item, dict)
            and item.get("gene")
            and str(item.get("status") or "").upper() in {"PASS", "CAUTION"}
        }
        if p5_result.get("gene_selected"):
            genes.add(str(p5_result["gene_selected"]))
        return len(genes)

    @staticmethod
    def _channel_reservation(batch: dict[str, Any]) -> dict[str, Any]:
        runtime = batch.get("runtime_config_snapshot") or {}
        instrument = str(runtime.get("instrument") or "")
        rox_as_reference = runtime.get("rox_as_reference")
        successful_targets = int(
            ((batch.get("target_design") or {}).get("succeeded_targets") or 0)
        )
        plan = _wf02_channel_plan(
            instrument=instrument,
            rox_as_reference=rox_as_reference,
            target_count=successful_targets,
            include_internal=True,
        )
        selected_dye = plan.get("internal_control_dye")
        return {
            **copy.deepcopy(plan),
            "status": "RESERVED" if plan.get("status") == "ASSIGNED" else "BLOCKED",
            "suggested_fluorophore": WF02_INTERNAL_DEFAULT_FLUOROPHORE,
            "fluorophore": selected_dye,
            "quencher": recommended_quencher(str(selected_dye)) if selected_dye else None,
            "assignment_owner": "WF-02",
            "validation_owner": "P4",
            "automatic_alternatives_allowed": True,
            "expert_review_required": (
                str(runtime.get("source") or "SYSTEM_DEFAULT") == "SYSTEM_DEFAULT"
            ),
            "successful_target_count": successful_targets,
            "internal_control_count": 1,
            "p4_validation_status": "PENDING_N15",
        }

    @staticmethod
    def _internal_control_interaction(
        batch: dict[str, Any],
        *,
        code: str,
        message: str,
        stage: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        channel_blocked = code == "WF02_INTERNAL_CHANNEL_BLOCKED"
        options = [
            {
                "value": "CONTINUE_WITHOUT_INTERNAL_CONTROL",
                "label": "明确不含内标继续",
                "description": "保留失败或容量证据，并以不含内标的面板继续。",
            }
        ]
        input_fields: list[dict[str, Any]] = []
        if not channel_blocked:
            options = [
                {
                    "value": "PROVIDE_INTERNAL_CONTROL_DATA",
                    "label": "补充内标数据",
                    "description": "补充候选基因及多品系序列后重新调用 P5。",
                },
                {
                    "value": "RETRY_INTERNAL_CONTROL",
                    "label": "重试内标设计",
                    "description": "服务器资源就绪后使用当前输入重新调用 P5。",
                },
                *options,
            ]
            input_fields = [
                {
                    "key": "preferred_genes",
                    "label": "候选内参基因（逗号分隔）",
                    "inputType": "TEXT",
                    "required": False,
                },
                {
                    "key": "gene_sequences",
                    "label": "候选基因多品系序列（JSON）",
                    "inputType": "TEXTAREA",
                    "required": False,
                },
            ]
        interaction = build_interaction(
            task_id=str(batch["batch_id"]),
            stage=stage,
            blocker_code=code,
            message=message,
            blocking=True,
            context={
                "workflow": "WF-02",
                "field": "internal_control",
                "details": copy.deepcopy(details or {}),
            },
            options=options,
            input_fields=input_fields,
        )
        return {
            "type": "agent_interaction",
            "task": "MULTIPLEX_PANEL",
            "taskId": batch.get("batch_id"),
            "targetId": None,
            **copy.deepcopy(interaction.get("data") or {}),
            "flowControl": {
                "action": "WAIT_FOR_USER",
                "stopCurrentTurn": True,
                "resumeMessageType": "agent_interaction_response",
            },
        }

    def _complete_without_internal_control(
        self,
        batch: dict[str, Any],
        *,
        status: str,
        reason: str,
        decision_source: str = "USER",
        quantity_verdict: str = "NOT_APPLICABLE",
        failure: dict[str, Any] | None = None,
        follow_up_actions: list[str] | None = None,
        preserve_p5_evidence: bool = False,
    ) -> dict[str, Any]:
        internal = batch.get("internal_control") or {}
        existing_reservation = copy.deepcopy(
            internal.get("channel_reservation") or {}
        )
        internal.update(
            {
                "requested": bool(
                    (batch.get("user_input_snapshot") or {}).get(
                        "include_internal_control"
                    )
                ),
                "status": status,
                "decision_source": decision_source,
                "skip_reason": reason,
                "candidate_gene_count": (
                    int(internal.get("candidate_gene_count") or 0)
                    if preserve_p5_evidence
                    else 0
                ),
                "primer_scheme_count": (
                    int(internal.get("primer_scheme_count") or 0)
                    if preserve_p5_evidence
                    else 0
                ),
                "quantity_verdict": quantity_verdict,
                "candidates": (
                    copy.deepcopy(internal.get("candidates") or [])
                    if preserve_p5_evidence
                    else []
                ),
                "combination_pool_member": False,
                "cross_compatibility_status": "NOT_APPLICABLE",
                "channel_reservation": existing_reservation
                or {
                    "policy_version": WF02_INTERNAL_CHANNEL_POLICY_VERSION,
                    "status": "SKIPPED",
                    "fluorophore": None,
                    "quencher": None,
                    "automatic_alternatives_allowed": True,
                    "expert_review_required": True,
                },
                "failure": copy.deepcopy(failure),
                "follow_up_actions": copy.deepcopy(
                    follow_up_actions
                    or (
                        [
                            "CREATE_NEW_VERSION_WITH_INTERNAL_CONTROL_DATA",
                            "CREATE_NEW_VERSION_WITH_DIFFERENT_INSTRUMENT",
                        ]
                        if failure
                        else []
                    )
                ),
                "ui_message": None,
            }
        )
        batch["internal_control"] = internal
        batch.update(
            {
                "status": "READY_FOR_PANEL_COMBINATION",
                "stage": "N14",
                "next_action": "RUN_PANEL_COMBINATION",
            }
        )
        self._record_node(
            batch,
            "N14",
            "FAIL_CONTINUE" if failure else "COMPLETED",
            reason,
            internal_control_requested=internal.get("requested"),
            internal_control_included=False,
            failure=copy.deepcopy(failure),
        )
        self._append_event(
            batch,
            "WF02_INTERNAL_CONTROL_SKIPPED",
            status=status,
            reason=reason,
        )
        return self._save_batch(batch)

    def execute_internal_control(self, batch_id: str) -> dict[str, Any]:
        """Execute N10-N14 by calling P5 directly through the service gateway."""
        batch = self.store.get_batch(batch_id)
        gateway = self._require_gateway(batch)
        internal = batch.get("internal_control") or {}
        if internal.get("status") in {
            "COMPLETED",
            "COMPLETED_WITH_CAUTION",
            "SKIPPED_BY_USER",
            "SKIPPED_AFTER_FAILURE",
            "FAILED_CONTINUE_WITHOUT_INTERNAL",
        }:
            return batch
        if internal.get("status") == "WAITING_USER":
            return batch
        if (batch.get("target_design") or {}).get("status") != "COMPLETED":
            raise InputValidationError(
                "WF-02 必须先完成 N06-N09 才能执行内标设计"
            )
        if batch.get("stage") not in {"N09", "N10", "N11", "N12", "N13"}:
            raise InputValidationError(
                "WF-02 当前状态不能执行内标设计",
                details={"stage": batch.get("stage"), "status": batch.get("status")},
            )

        snapshot = batch.get("user_input_snapshot") or {}
        if not snapshot.get("include_internal_control"):
            if not internal:
                batch["internal_control"] = {
                    "requested": False,
                    "status": "SKIPPED_BY_USER",
                    "attempt_count": 0,
                }
                self._record_node(
                    batch,
                    "N10",
                    "NO_BRANCH",
                    "用户明确关闭内标，进入 N14 跳过路径",
                )
            return self._complete_without_internal_control(
                batch,
                status="SKIPPED_BY_USER",
                reason="用户明确选择不含内标；未调用 P5",
            )

        if not internal:
            internal = {
                "requested": True,
                "status": "RUNNING",
                "decision_source": (
                    ((batch.get("preflight") or {}).get("input_echo") or {}).get(
                        "include_internal_control_source"
                    )
                    or "DEFAULT"
                ),
                "attempt_count": 0,
                "attempt_history": [],
                "input_overrides": {},
                "candidate_gene_count": 0,
                "primer_scheme_count": 0,
                "candidates": [],
                "combination_pool_member": False,
                "cross_compatibility_status": "PENDING_N15_P4",
            }
            batch["internal_control"] = internal
            batch.update(
                {
                    "status": "DESIGNING_INTERNAL_CONTROL",
                    "stage": "N10",
                    "next_action": "RUN_INTERNAL_CONTROL",
                }
            )
            self._record_node(
                batch,
                "N10",
                "YES_BRANCH",
                "内标为需要，进入 P5 设计路径",
            )

        internal["status"] = "RUNNING"
        internal["ui_message"] = None
        internal["attempt_count"] = int(internal.get("attempt_count") or 0) + 1
        batch.update(
            {
                "status": "DESIGNING_INTERNAL_CONTROL",
                "stage": "N11",
                "next_action": "RUN_INTERNAL_CONTROL",
            }
        )
        self._append_event(
            batch,
            "WF02_P5_INTERNAL_CONTROL_STARTED",
            status="RUNNING",
            attempt=internal["attempt_count"],
        )
        self._save_batch(batch)

        p5_payload = self._internal_control_payload(batch, internal)
        try:
            returned = gateway.design_p5_internal_control(p5_payload)
            p5_result = copy.deepcopy(returned) if isinstance(returned, dict) else {}
            p5_result.pop("candidate_objects", None)
        except Exception as exc:
            p5_result = {
                "status": "SERVICE_ERROR",
                "verdict": "FAIL",
                "candidates": [],
                "warnings": ["P5 服务调用失败"],
                "error": _error_payload(exc),
            }

        p5_status = str(p5_result.get("status") or "UNKNOWN").upper()
        p5_verdict = str(p5_result.get("verdict") or "FAIL").upper()
        raw_candidates = [
            copy.deepcopy(item)
            for item in (p5_result.get("candidates") or [])
            if isinstance(item, dict)
        ][:5]
        usable_candidates = (
            raw_candidates if p5_verdict in {"PASS", "CAUTION"} else []
        )
        internal["attempt_history"].append(
            {
                "attempt": internal["attempt_count"],
                "p5_status": p5_status,
                "p5_verdict": p5_verdict,
                "returned_candidate_count": len(raw_candidates),
                "recorded_at": utc_now(),
            }
        )
        internal.update(
            {
                "p5_status": p5_status,
                "p5_verdict": p5_verdict,
                "p5_result": p5_result,
                "selected_gene": p5_result.get("gene_selected"),
                "candidate_gene_count": self._candidate_gene_count(p5_result),
                "primer_scheme_count": len(usable_candidates),
                "candidates": usable_candidates,
                "warnings": copy.deepcopy(p5_result.get("warnings") or []),
                "cross_compatibility_status": "PENDING_N15_P4",
            }
        )
        self._record_node(
            batch,
            "N11",
            "COMPLETED" if usable_candidates else "FAILED",
            (
                f"P5 返回 {len(usable_candidates)} 套可用内标候选"
                if usable_candidates
                else "P5 未返回可继续的内标候选"
            ),
            attempt=internal["attempt_count"],
            p5_status=p5_status,
            p5_verdict=p5_verdict,
        )

        if not usable_candidates:
            failure = {
                "code": p5_status if p5_status != "UNKNOWN" else "P5_INTERNAL_CONTROL_FAILED",
                "message": "P5 未获得可用内标候选；按专家规则自动继续不含内标面板",
                "details": {
                    "recommendation": p5_result.get("recommendation"),
                    "warnings": copy.deepcopy(p5_result.get("warnings") or []),
                    "error": copy.deepcopy(p5_result.get("error")),
                },
            }
            internal.update(
                {
                    "quantity_verdict": "FAIL",
                    "failure": failure,
                    "combination_pool_member": False,
                    "ui_message": None,
                }
            )
            self._record_node(
                batch,
                "N12",
                "FAIL_CONTINUE",
                failure["message"],
                failure=copy.deepcopy(failure),
            )
            self._append_event(
                batch,
                "WF02_INTERNAL_CONTROL_FAILED_CONTINUE",
                status="FAILED_CONTINUE_WITHOUT_INTERNAL",
                attempt=internal["attempt_count"],
                failure=copy.deepcopy(failure),
            )
            return self._complete_without_internal_control(
                batch,
                status="FAILED_CONTINUE_WITHOUT_INTERNAL",
                reason=failure["message"],
                decision_source="AUTOMATIC_EXPERT_POLICY",
                quantity_verdict="FAIL",
                failure=failure,
            )

        scheme_count = len(usable_candidates)
        quantity_verdict = "PASS" if scheme_count >= 3 else "CAUTION"
        internal["quantity_verdict"] = quantity_verdict
        self._record_node(
            batch,
            "N12",
            quantity_verdict,
            (
                f"获得 {scheme_count} 套内标候选，达到推荐数量"
                if quantity_verdict == "PASS"
                else f"仅获得 {scheme_count} 套内标候选，带 CAUTION 继续"
            ),
            candidate_gene_count=internal["candidate_gene_count"],
            primer_scheme_count=scheme_count,
        )

        reservation = self._channel_reservation(batch)
        internal["channel_reservation"] = reservation
        if reservation["status"] != "RESERVED":
            failure = {
                "code": "WF02_INTERNAL_CHANNEL_BLOCKED",
                "message": "未能为内标生成合法通道指派；按专家规则自动继续不含内标面板",
                "details": copy.deepcopy(reservation),
            }
            internal.update(
                {
                    "failure": failure,
                    "combination_pool_member": False,
                    "ui_message": None,
                }
            )
            self._record_node(
                batch,
                "N13",
                "FAIL_CONTINUE",
                failure["message"],
                channel_reservation=copy.deepcopy(reservation),
            )
            self._append_event(
                batch,
                "WF02_INTERNAL_CHANNEL_FAILED_CONTINUE",
                status="FAILED_CONTINUE_WITHOUT_INTERNAL",
                failure=copy.deepcopy(failure),
            )
            return self._complete_without_internal_control(
                batch,
                status="FAILED_CONTINUE_WITHOUT_INTERNAL",
                reason=failure["message"],
                decision_source="AUTOMATIC_EXPERT_POLICY",
                quantity_verdict="FAIL",
                failure=failure,
                preserve_p5_evidence=True,
            )

        internal.update(
            {
                "status": (
                    "COMPLETED_WITH_CAUTION"
                    if quantity_verdict == "CAUTION" or p5_verdict == "CAUTION"
                    else "COMPLETED"
                ),
                "failure": None,
                "ui_message": None,
                "combination_pool_member": True,
            }
        )
        batch.update(
            {
                "status": "READY_FOR_PANEL_COMBINATION",
                "stage": "N13",
                "next_action": "RUN_PANEL_COMBINATION",
            }
        )
        self._record_node(
            batch,
            "N13",
            "COMPLETED",
            "内标候选已加入组合池；WF-02 完成动态通道指派，待 P4 验证",
            channel_reservation=copy.deepcopy(reservation),
            primer_scheme_count=scheme_count,
        )
        self._append_event(
            batch,
            "WF02_INTERNAL_CONTROL_COMPLETED",
            status=internal["status"],
            p5_verdict=p5_verdict,
            quantity_verdict=quantity_verdict,
            primer_scheme_count=scheme_count,
            channel_policy_version=WF02_INTERNAL_CHANNEL_POLICY_VERSION,
        )
        return self._save_batch(batch)

    @staticmethod
    def _single_internal_response(response: dict[str, Any]) -> dict[str, Any]:
        if response.get("type") != "agent_interaction_response":
            raise InputValidationError(
                "内标交互回复 type 必须为 agent_interaction_response"
            )
        if response.get("task") != "MULTIPLEX_PANEL":
            raise InputValidationError("内标交互回复 task 必须为 MULTIPLEX_PANEL")
        submitted = response
        items = response.get("interactions")
        if items is not None:
            if (
                not isinstance(items, list)
                or len(items) != 1
                or not isinstance(items[0], dict)
            ):
                raise InputValidationError("内标交互回复必须只包含一条 interactions 记录")
            submitted = items[0]
        return submitted

    @staticmethod
    def _internal_input_overrides(values: dict[str, Any]) -> dict[str, Any]:
        accepted: dict[str, Any] = {}
        preferred = values.get("preferred_genes")
        if isinstance(preferred, str):
            preferred = [
                item.strip()
                for item in re.split(r"[,，;；\n]+", preferred)
                if item.strip()
            ]
        if isinstance(preferred, list) and preferred:
            accepted["preferred_genes"] = [str(item).strip() for item in preferred if str(item).strip()]
        for key in ("gene_sequences", "gene_accessions", "gene_fasta_paths"):
            selected = values.get(key)
            if isinstance(selected, str) and selected.strip():
                try:
                    selected = json.loads(selected)
                except json.JSONDecodeError as exc:
                    raise InputValidationError(f"{key} 必须为合法 JSON 对象") from exc
            if selected is not None:
                if not isinstance(selected, dict):
                    raise InputValidationError(f"{key} 必须为对象")
                accepted[key] = copy.deepcopy(selected)
        for key in ("host_db", "cross_db"):
            if _text(values.get(key)):
                accepted[key] = _text(values[key])
        return accepted

    def submit_internal_control_response(
        self,
        batch_id: str,
        response: dict[str, Any],
        *,
        bounded: bool = False,
        auto_resume: bool = True,
    ) -> dict[str, Any]:
        """Resolve an N12/N13 internal-control interaction and resume WF-02."""
        batch = self.store.get_batch(batch_id)
        internal = batch.get("internal_control") or {}
        ui_message = internal.get("ui_message") or {}
        if internal.get("status") != "WAITING_USER" or not ui_message:
            raise InputValidationError("WF-02 当前没有待处理的内标交互")
        submitted = self._single_internal_response(response)
        expected_id = ui_message.get("interactionId")
        if expected_id and submitted.get("interactionId") != expected_id:
            raise InputValidationError(
                "内标交互 interactionId 不匹配",
                details={"expected": expected_id, "submitted": submitted.get("interactionId")},
            )
        action = str(submitted.get("answer") or "")
        if action == "CONTINUE_WITHOUT_INTERNAL_CONTROL":
            self._complete_without_internal_control(
                batch,
                status="SKIPPED_AFTER_FAILURE",
                reason="用户在内标设计或通道阻塞后明确选择不含内标继续",
            )
            return (self.run_until_blocked(batch_id, bounded=bounded) if auto_resume
                    else summarize_wf02_batch(self.store.get_batch(batch_id)))
        if action not in {"PROVIDE_INTERNAL_CONTROL_DATA", "RETRY_INTERNAL_CONTROL"}:
            raise InputValidationError(
                "内标交互动作无效",
                details={"answer": action},
            )
        if action == "PROVIDE_INTERNAL_CONTROL_DATA":
            values = submitted.get("fieldValues") or {}
            if not isinstance(values, dict):
                raise InputValidationError("fieldValues 必须为对象")
            overrides = self._internal_input_overrides(values)
            if not overrides:
                raise InputValidationError("请至少补充候选基因、基因序列或数据库路径")
            internal.setdefault("input_overrides", {}).update(overrides)
        internal.update(
            {
                "status": "RUNNING",
                "ui_message": None,
                "failure": None,
                "candidates": [],
                "primer_scheme_count": 0,
                "combination_pool_member": False,
            }
        )
        batch.update(
            {
                "status": "DESIGNING_INTERNAL_CONTROL",
                "stage": "N11",
                "next_action": "RUN_INTERNAL_CONTROL",
            }
        )
        self._append_event(
            batch,
            "WF02_INTERNAL_CONTROL_INPUT_SUBMITTED",
            status="RESUMING",
            action=action,
            interaction_id=expected_id,
        )
        self._save_batch(batch)
        return (self.run_until_blocked(batch_id, bounded=bounded) if auto_resume
                    else summarize_wf02_batch(self.store.get_batch(batch_id)))

    @staticmethod
    def _panel_pool_specs(batch: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
        target_results = sorted(
            ((batch.get("target_design") or {}).get("targets") or []),
            key=lambda item: int(item.get("input_order") or 999),
        )
        specs: list[dict[str, Any]] = []
        missing: list[str] = []
        for result in target_results:
            target_id = str(result.get("target_id") or "")
            candidates = [
                item
                for item in (result.get("candidates") or [])
                if isinstance(item, dict)
            ]
            if result.get("status") != "PASS" or not candidates:
                if target_id:
                    missing.append(target_id)
                continue
            specs.append(
                {
                    "target_id": target_id,
                    "role": "target",
                    "candidate_count": len(candidates),
                }
            )
        internal = batch.get("internal_control") or {}
        if internal.get("combination_pool_member"):
            internal_candidates = [
                item
                for item in (internal.get("candidates") or [])
                if isinstance(item, dict)
            ]
            if internal_candidates:
                specs.append(
                    {
                        "target_id": "internal_control",
                        "role": "internal_control",
                        "candidate_count": len(internal_candidates),
                    }
                )
            else:
                missing.append("internal_control")
        return specs, missing

    def _panel_channel_assignment(
        self,
        batch: dict[str, Any],
        pool_specs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        runtime = batch.get("runtime_config_snapshot") or {}
        instrument = str(
            runtime.get("instrument")
            or self.config["multiplex"]["default_instrument"]
        )
        rox_as_reference = runtime.get("rox_as_reference")
        include_internal = any(
            item.get("role") == "internal_control" for item in pool_specs
        )
        target_specs = [
            item for item in pool_specs if item.get("role") == "target"
        ]
        plan = _wf02_channel_plan(
            instrument=instrument,
            rox_as_reference=rox_as_reference,
            target_count=len(target_specs),
            include_internal=include_internal,
        )
        if plan.get("status") != "ASSIGNED":
            raise InputValidationError(
                "WF-02 无法生成待 P4 验证的通道指派",
                details=copy.deepcopy(plan),
            )
        dyes = list(plan.get("target_dyes") or [])
        assignment = [
            {
                "target_id": spec["target_id"],
                "role": "target",
                "fluorophore": dyes[index],
                "recommended_quencher": recommended_quencher(dyes[index]),
                "assignment_owner": "WF-02",
                "validation_owner": "P4",
                "assignment_basis": "USER_TARGET_INPUT_ORDER",
            }
            for index, spec in enumerate(target_specs)
        ]
        if include_internal:
            assignment.append(
                {
                    "target_id": "internal_control",
                    "role": "internal_control",
                    "fluorophore": plan["internal_control_dye"],
                    "recommended_quencher": recommended_quencher(
                        str(plan["internal_control_dye"])
                    ),
                    "assignment_owner": "WF-02",
                    "validation_owner": "P4",
                    "suggested_fluorophore": (
                        WF02_INTERNAL_DEFAULT_FLUOROPHORE
                    ),
                    "assignment_basis": plan[
                        "internal_control_selection_basis"
                    ],
                }
            )
        return assignment

    @staticmethod
    def _panel_candidate_pools(
        batch: dict[str, Any],
        pool_specs: list[dict[str, Any]],
    ) -> list[list[dict[str, Any]]]:
        target_results = {
            str(item.get("target_id")): item
            for item in ((batch.get("target_design") or {}).get("targets") or [])
        }
        internal = batch.get("internal_control") or {}
        pools: list[list[dict[str, Any]]] = []
        for spec in pool_specs:
            if spec.get("role") == "internal_control":
                source = internal.get("candidates") or []
            else:
                source = (
                    target_results.get(str(spec.get("target_id")), {}).get(
                        "candidates"
                    )
                    or []
                )
            pools.append(
                [copy.deepcopy(item) for item in source if isinstance(item, dict)]
            )
        return pools

    def _initialise_panel_screening(self, batch: dict[str, Any]) -> dict[str, Any]:
        multiplex = batch.setdefault("multiplex", {})
        existing = multiplex.get("screening")
        if isinstance(existing, dict):
            return existing
        pool_specs, missing = self._panel_pool_specs(batch)
        complete_panel_possible = not missing and len(
            [item for item in pool_specs if item.get("role") == "target"]
        ) >= MINIMUM_TARGETS
        runtime = batch.get("runtime_config_snapshot") or {}
        instrument_source = str(runtime.get("source") or "SYSTEM_DEFAULT")
        instrument_evidence = {
            "instrument": runtime.get("instrument"),
            "rox_as_reference": runtime.get("rox_as_reference"),
            "instrument_source": instrument_source,
            "rox_source": runtime.get("rox_source"),
            "calibration_status": (
                "UNVERIFIED_TOOL_DEFAULT"
                if instrument_source == "SYSTEM_DEFAULT"
                else "CONFIGURED_IN_SILICO"
            ),
            "strict_compatibility_effect": (
                "RISK_ONLY"
                if instrument_source == "SYSTEM_DEFAULT"
                else "NONE"
            ),
            "statement": (
                "内置仪器配置仅用于计算，不能替代客户真实仪器和最新校准文件"
            ),
        }
        screening: dict[str, Any] = {
            "status": "RUNNING",
            "policy_version": "wf02-panel-screening-v1",
            "channel_policy_version": WF02_PANEL_CHANNEL_POLICY_VERSION,
            "amplicon_length_policy_version": (
                WF02_AMPLICON_LENGTH_POLICY_VERSION
            ),
            "complete_panel_possible": complete_panel_possible,
            "missing_members": missing,
            "pool_order": copy.deepcopy(pool_specs),
            "pool_sizes": {
                str(item["target_id"]): int(item["candidate_count"])
                for item in pool_specs
            },
            "total_combinations": (
                math.prod(int(item["candidate_count"]) for item in pool_specs)
                if complete_panel_possible
                else 0
            ),
            "combination_batch_size": max(
                1,
                int(
                    self.config["multiplex"].get(
                        "maximum_panel_combinations", 500
                    )
                ),
            ),
            "cursor": 0,
            "strict_count": 0,
            "risk_count": 0,
            "incompatible_count": 0,
            "invalid_count": 0,
            "panels": [],
            "channel_assignment": [],
            "instrument_evidence": instrument_evidence,
            "tool_failure": None,
            "started_at": utc_now(),
        }
        multiplex["screening"] = screening
        self._record_node(
            batch,
            "N15",
            "RUNNING" if complete_panel_possible else "NO_COMPLETE_PANEL",
            (
                f"形成 {screening['total_combinations']} 个完整候选组合"
                if complete_panel_possible
                else "存在无候选靶标，不能形成包含全部请求靶标的完整组合"
            ),
            pool_sizes=copy.deepcopy(screening["pool_sizes"]),
            missing_members=copy.deepcopy(missing),
        )
        if not complete_panel_possible:
            screening.update(
                {
                    "status": "NO_COMPLETE_PANEL",
                    "completed_at": utc_now(),
                }
            )
            for node, message in (
                ("N16", "无完整组合，不生成完整面板通道方案"),
                ("N16A", "无完整组合，P4 运行配置不适用"),
                ("N17", "无完整组合，未调用 P4 交叉二聚体检查"),
                ("N18", "无完整组合，未调用 P4 光谱与仪器检查"),
                ("N19", "无完整组合，R-17 项目规则不适用"),
            ):
                self._record_node(batch, node, "NOT_APPLICABLE", message)
            self._record_node(
                batch,
                "N20",
                "NO_BRANCH",
                "不存在严格兼容的完整面板，进入逐步放宽与部分面板路径",
            )
            batch.update(
                {
                    "status": "READY_FOR_RELAXATION",
                    "stage": "N20",
                    "next_action": "RUN_RELAXATION",
                }
            )
            return screening
        try:
            screening["channel_assignment"] = self._panel_channel_assignment(
                batch, pool_specs
            )
        except Exception as exc:
            screening.update(
                {
                    "status": "WAITING_P4_RETRY",
                    "tool_failure": _error_payload(exc),
                }
            )
            batch.update(
                {
                    "status": "WAITING_P4_RETRY",
                    "stage": "N16A",
                    "next_action": "RETRY_PANEL_SCREENING",
                }
            )
            self._record_node(
                batch,
                "N16A",
                "WAITING_CONFIGURATION",
                "当前 P4 运行配置无法生成通道方案",
                failure=copy.deepcopy(screening["tool_failure"]),
            )
            return screening
        self._record_node(
            batch,
            "N16",
            "COMPLETED",
            "按用户靶标输入顺序生成确定性通道方案，P4 负责验证",
            channel_policy_version=WF02_PANEL_CHANNEL_POLICY_VERSION,
            channel_assignment=copy.deepcopy(screening["channel_assignment"]),
        )
        self._record_node(
            batch,
            "N16A",
            "COMPLETED",
            "已加载冻结的 P4 仪器与 ROX 运行配置",
            instrument_evidence=copy.deepcopy(instrument_evidence),
        )
        return screening

    @staticmethod
    def _panel_identifier(
        entries: list[dict[str, Any]],
        assignment: list[dict[str, Any]],
    ) -> str:
        signature = {
            "members": [
                [item.get("target_id"), item.get("candidate_id")]
                for item in entries
            ],
            "channels": [
                [item.get("target_id"), item.get("fluorophore")]
                for item in assignment
            ],
        }
        return f"wf02-panel-{_canonical_sha256(signature)[:16]}"

    def _finish_panel_screening(
        self,
        batch: dict[str, Any],
        screening: dict[str, Any],
    ) -> dict[str, Any]:
        screening.update(
            {
                "status": "COMPLETED",
                "completed_at": utc_now(),
                "tool_failure": None,
            }
        )
        evaluated_count = (
            int(screening.get("strict_count") or 0)
            + int(screening.get("risk_count") or 0)
            + int(screening.get("incompatible_count") or 0)
        )
        self._record_node(
            batch,
            "N17",
            "COMPLETED",
            f"P4 已完成 {evaluated_count} 个组合的全序列交叉二聚体检查",
        )
        self._record_node(
            batch,
            "N18",
            "COMPLETED",
            f"P4 已完成 {evaluated_count} 个组合的光谱、淬灭基团和仪器检查",
        )
        r17_failure_count = sum(
            (item.get("amplicon_length_check") or {}).get("status")
            == "FAIL"
            for item in screening.get("panels") or []
        )
        self._record_node(
            batch,
            "N19",
            "FAIL_FILTERED" if r17_failure_count else "PASS",
            (
                f"{r17_failure_count} 个组合未通过 R-17 硬门禁并已过滤"
                if r17_failure_count
                else "所有已评估组合均通过 R-17 硬门禁"
            ),
            hard_filter=True,
            policy_version=WF02_AMPLICON_LENGTH_POLICY_VERSION,
        )
        strict_count = int(screening.get("strict_count") or 0)
        if strict_count:
            batch.update(
                {
                    "status": "READY_FOR_BALANCE",
                    "stage": "N20",
                    "next_action": "RUN_BALANCE_ASSESSMENT",
                }
            )
            self._record_node(
                batch,
                "N20",
                "YES_BRANCH",
                f"获得 {strict_count} 个严格兼容的完整面板",
                strict_count=strict_count,
                risk_count=screening.get("risk_count"),
            )
        else:
            batch.update(
                {
                    "status": "READY_FOR_RELAXATION",
                    "stage": "N20",
                    "next_action": "RUN_RELAXATION",
                }
            )
            self._record_node(
                batch,
                "N20",
                "NO_BRANCH",
                "没有严格兼容的完整面板，进入逐步放宽路径",
                risk_count=screening.get("risk_count"),
                incompatible_count=screening.get("incompatible_count"),
            )
        internal = batch.get("internal_control") or {}
        if internal.get("combination_pool_member"):
            internal["cross_compatibility_status"] = "P4_COMPLETED"
            reservation = internal.get("channel_reservation") or {}
            reservation["p4_validation_status"] = "COMPLETED"
        self._append_event(
            batch,
            "WF02_PANEL_SCREENING_COMPLETED",
            status=batch["status"],
            total_combinations=screening.get("total_combinations"),
            strict_count=strict_count,
            risk_count=screening.get("risk_count"),
            incompatible_count=screening.get("incompatible_count"),
            invalid_count=screening.get("invalid_count"),
        )
        return self._save_batch(batch)

    def execute_panel_screening(
        self,
        batch_id: str,
        *,
        max_combinations: int | None = None,
    ) -> dict[str, Any]:
        """Execute one resumable N15-N20 P4 screening batch."""
        batch = self.store.get_batch(batch_id)
        gateway = self._require_gateway(batch)
        if (batch.get("target_design") or {}).get("status") != "COMPLETED":
            raise InputValidationError("WF-02 必须先完成 N06-N09 才能组合面板")
        internal = batch.get("internal_control") or {}
        if internal.get("status") not in {
            "COMPLETED",
            "COMPLETED_WITH_CAUTION",
            "SKIPPED_BY_USER",
            "SKIPPED_AFTER_FAILURE",
            "FAILED_CONTINUE_WITHOUT_INTERNAL",
        }:
            raise InputValidationError("WF-02 必须先完成 N10-N14 才能组合面板")
        multiplex = batch.setdefault("multiplex", {})
        screening = self._initialise_panel_screening(batch)
        if screening.get("status") == "NO_COMPLETE_PANEL":
            return self._save_batch(batch)
        if screening.get("status") == "COMPLETED":
            return batch
        if screening.get("status") == "WAITING_P4_RETRY":
            if not screening.get("channel_assignment"):
                return batch
            screening.update({"status": "RUNNING", "tool_failure": None})
        batch.update(
            {
                "status": "SCREENING_PANELS",
                "stage": "N17",
                "next_action": "CONTINUE_PANEL_SCREENING",
            }
        )
        pool_specs = screening.get("pool_order") or []
        pools = self._panel_candidate_pools(batch, pool_specs)
        assignment = screening.get("channel_assignment") or []
        assignment_by_target = {
            str(item.get("target_id")): item for item in assignment
        }
        cursor = int(screening.get("cursor") or 0)
        batch_size = int(screening.get("combination_batch_size") or 1)
        if max_combinations is not None:
            batch_size = min(batch_size, max(1, int(max_combinations)))
        selected = _product_window(
            pools,
            cursor,
            min(cursor + batch_size, int(screening["total_combinations"])),
        )
        minimum_length_difference = int(
            self.config["multiplex"]["minimum_amplicon_length_difference_bp"]
        )
        runtime = batch.get("runtime_config_snapshot") or {}
        options = batch.get("execution_options_snapshot") or {}
        processed_this_call = 0
        for combination in selected:
            entries: list[dict[str, Any]] = []
            member_refs = [
                {
                    "target_id": spec.get("target_id"),
                    "role": spec.get("role"),
                    "candidate_id": candidate.get("candidate_id"),
                }
                for spec, candidate in zip(pool_specs, combination)
            ]
            try:
                for spec, candidate in zip(pool_specs, combination):
                    target_id = str(spec.get("target_id"))
                    channel = assignment_by_target[target_id]
                    entries.append(
                        _candidate_panel_entry(
                            candidate,
                            target_id=target_id,
                            role=str(spec.get("role")),
                            fluorophore=str(channel.get("fluorophore")),
                            quencher=str(
                                channel.get("recommended_quencher") or ""
                            ),
                        )
                    )
            except Exception as exc:
                screening["panels"].append(
                    {
                        "panel_id": (
                            f"wf02-invalid-{_canonical_sha256(member_refs)[:16]}"
                        ),
                        "members": member_refs,
                        "classification": "INVALID",
                        "invalid_reason": _error_payload(exc),
                    }
                )
                screening["invalid_count"] += 1
                screening["cursor"] += 1
                processed_this_call += 1
                continue
            resolved_assignment = _resolved_panel_channel_assignment(
                assignment, entries
            )
            panel_id = self._panel_identifier(entries, resolved_assignment)
            try:
                returned = gateway.check_p4_panel(
                    {
                        "panel": copy.deepcopy(entries),
                        "instrument": runtime.get("instrument"),
                        "rox_as_reference": runtime.get("rox_as_reference"),
                        "allow_fallback": bool(options.get("allow_fallback", False)),
                    }
                )
                p4_result = (
                    copy.deepcopy(returned) if isinstance(returned, dict) else {}
                )
            except Exception as exc:
                p4_result = {
                    "status": "NOT_EVALUATED",
                    "verdict": None,
                    "error": _error_payload(exc),
                }
            p4_verdict = str(p4_result.get("verdict") or "").upper()
            p4_status = str(
                p4_result.get("status")
                or ("COMPLETED" if p4_verdict else "NOT_EVALUATED")
            ).upper()
            if p4_status != "COMPLETED" or p4_verdict not in {
                "COMPATIBLE",
                "MARGINAL",
                "INCOMPATIBLE",
            }:
                failure = {
                    "code": "P4_NOT_EVALUATED",
                    "message": "P4 未完成当前面板兼容性检查，保留游标等待重试",
                    "details": {
                        "panel_id": panel_id,
                        "p4_status": p4_status,
                        "p4_result": p4_result,
                    },
                }
                screening.update(
                    {"status": "WAITING_P4_RETRY", "tool_failure": failure}
                )
                batch.update(
                    {
                        "status": "WAITING_P4_RETRY",
                        "stage": "N17",
                        "next_action": "RETRY_PANEL_SCREENING",
                    }
                )
                self._record_node(
                    batch,
                    "N17",
                    "WAITING_TOOL",
                    failure["message"],
                    panel_id=panel_id,
                    cursor=screening.get("cursor"),
                )
                self._append_event(
                    batch,
                    "WF02_P4_RETRY_REQUIRED",
                    status="WAITING_P4_RETRY",
                    failure=copy.deepcopy(failure),
                )
                return self._save_batch(batch)
            length_check = _r17_amplicon_length_check(
                entries, minimum_length_difference
            )
            risk_reasons: list[str] = []
            if p4_verdict == "MARGINAL":
                risk_reasons.append("P4_MARGINAL")
            if (
                (screening.get("instrument_evidence") or {}).get(
                    "strict_compatibility_effect"
                )
                == "RISK_ONLY"
            ):
                risk_reasons.append("INSTRUMENT_PROFILE_UNVERIFIED")
            if length_check["status"] == "FAIL":
                classification = "INCOMPATIBLE"
                risk_reasons.append(
                    "R17_MINIMUM_AMPLICON_LENGTH_DIFFERENCE_FAILED"
                )
                screening["incompatible_count"] += 1
            elif p4_verdict == "INCOMPATIBLE":
                classification = "INCOMPATIBLE"
                screening["incompatible_count"] += 1
            elif risk_reasons:
                classification = "RISK"
                screening["risk_count"] += 1
            else:
                classification = "STRICT_COMPATIBLE"
                screening["strict_count"] += 1
            screening["panels"].append(
                {
                    "panel_id": panel_id,
                    "members": member_refs,
                    "p4_input": entries,
                    "channel_assignment": resolved_assignment,
                    "p4_result": p4_result,
                    "p4_verdict": p4_verdict,
                    "amplicon_length_check": length_check,
                    "classification": classification,
                    "risk_reasons": risk_reasons,
                    "instrument_evidence": copy.deepcopy(
                        screening.get("instrument_evidence") or {}
                    ),
                }
            )
            screening["cursor"] += 1
            processed_this_call += 1
        self._append_event(
            batch,
            "WF02_PANEL_SCREENING_BATCH_COMPLETED",
            status="SCREENING_PANELS",
            processed_this_call=processed_this_call,
            cursor=screening.get("cursor"),
            total_combinations=screening.get("total_combinations"),
        )
        if int(screening.get("cursor") or 0) >= int(
            screening.get("total_combinations") or 0
        ):
            return self._finish_panel_screening(batch, screening)
        batch.update(
            {
                "status": "SCREENING_PANELS",
                "stage": "N18",
                "next_action": "CONTINUE_PANEL_SCREENING",
            }
        )
        return self._save_batch(batch)

    @staticmethod
    def _completed_p4_result(result: dict[str, Any]) -> tuple[bool, str]:
        verdict = str(result.get("verdict") or "").upper()
        status = str(
            result.get("status") or ("COMPLETED" if verdict else "NOT_EVALUATED")
        ).upper()
        return (
            status == "COMPLETED"
            and verdict in {"COMPATIBLE", "MARGINAL", "INCOMPATIBLE"},
            verdict,
        )

    @staticmethod
    def _tm_spreads(result: dict[str, Any]) -> tuple[float | None, float | None]:
        spreads = result.get("tm_spread_celsius") or {}
        try:
            primer_spread = float(spreads["primers"])
            probe_spread = float(spreads["probes"])
        except (KeyError, TypeError, ValueError):
            return None, None
        return primer_spread, probe_spread

    @staticmethod
    def _relaxed_panel_risk_reasons(
        *,
        step: str,
        p4_verdict: str,
        length_check: dict[str, Any],
        instrument_evidence: dict[str, Any],
        partial: bool,
    ) -> list[str]:
        reasons = ["DIMER_THRESHOLD_RELAXED"]
        if step in {"N22", "N23"}:
            reasons.append("TM_SPREAD_LIMIT_RELAXED_TO_4C")
        if partial:
            reasons.append("PARTIAL_TARGET_COVERAGE")
        if p4_verdict == "MARGINAL":
            reasons.append("P4_MARGINAL")
        if instrument_evidence.get("strict_compatibility_effect") == "RISK_ONLY":
            reasons.append("INSTRUMENT_PROFILE_UNVERIFIED")
        return list(dict.fromkeys(reasons))

    def _call_relaxed_p4(
        self,
        batch: dict[str, Any],
        entries: list[dict[str, Any]],
        *,
        tm_spread_limit: float,
    ) -> dict[str, Any]:
        runtime = batch.get("runtime_config_snapshot") or {}
        options = batch.get("execution_options_snapshot") or {}
        gateway = self._require_gateway(batch)
        try:
            returned = gateway.check_p4_panel(
                {
                    "panel": copy.deepcopy(entries),
                    "instrument": runtime.get("instrument"),
                    "rox_as_reference": runtime.get("rox_as_reference"),
                    "allow_fallback": bool(options.get("allow_fallback", False)),
                    "relaxed": True,
                    "tm_spread_limit": tm_spread_limit,
                }
            )
            return copy.deepcopy(returned) if isinstance(returned, dict) else {}
        except Exception as exc:
            return {
                "status": "NOT_EVALUATED",
                "verdict": None,
                "error": _error_payload(exc),
            }

    def _new_relaxation_round(
        self,
        screening: dict[str, Any],
        *,
        node: str,
        tm_spread_limit: float,
    ) -> dict[str, Any]:
        source_ids = [
            str(item.get("panel_id"))
            for item in screening.get("panels") or []
            if isinstance(item.get("p4_input"), list) and item.get("p4_input")
        ]
        return {
            "node": node,
            "status": "RUNNING",
            "cross_dimer_dg_kcal": WF02_RELAXED_DIMER_DG_KCAL,
            "tm_spread_limit_celsius": tm_spread_limit,
            "tm_scope": "PRIMERS_AND_PROBES_SEPARATELY",
            "source_panel_ids": source_ids,
            "total_panels": len(source_ids),
            "cursor": 0,
            "accepted_count": 0,
            "rejected_count": 0,
            "results": [],
            "started_at": utc_now(),
        }

    def _initialise_relaxation(self, batch: dict[str, Any]) -> dict[str, Any]:
        multiplex = batch.setdefault("multiplex", {})
        existing = multiplex.get("relaxation")
        if isinstance(existing, dict):
            return existing
        screening = multiplex.get("screening") or {}
        if screening.get("status") not in {"COMPLETED", "NO_COMPLETE_PANEL"}:
            raise InputValidationError("WF-02 必须先完成 N15-N20 才能执行放宽")
        if int(screening.get("strict_count") or 0) > 0:
            raise InputValidationError("已有严格完整面板，不应进入 N21 放宽路径")
        first_round = self._new_relaxation_round(
            screening,
            node="N21",
            tm_spread_limit=WF02_STANDARD_TM_SPREAD_C,
        )
        active_step = "N21" if first_round["total_panels"] else "N23"
        relaxation: dict[str, Any] = {
            "status": "RUNNING",
            "policy_version": WF02_RELAXATION_POLICY_VERSION,
            "decision_status": "TEMPORARY_CONSERVATIVE",
            "expert_confirmation_required": True,
            "active_step": active_step,
            "combination_batch_size": int(
                screening.get("combination_batch_size") or 1
            ),
            "rounds": [first_round],
            "partial_levels": [],
            "active_partial_level_index": 0,
            "full_risk_panels": [],
            "partial_panels": [],
            "maximum_partial_target_count": None,
            "independent_target_schemes": [],
            "relaxation_log": [],
            "failure_suggestions": [],
            "tool_failure": None,
            "started_at": utc_now(),
        }
        multiplex["relaxation"] = relaxation
        if not first_round["total_panels"]:
            for node, message in (
                ("N21", "没有可重评估的完整面板，二聚体放宽不适用"),
                ("N22", "没有可重评估的完整面板，Tm 放宽不适用"),
            ):
                self._record_node(batch, node, "NOT_APPLICABLE", message)
            relaxation["relaxation_log"].extend(
                [
                    {
                        "node": "N21",
                        "status": "NOT_APPLICABLE",
                        "reason": "NO_COMPLETE_PANEL_INPUT",
                    },
                    {
                        "node": "N22",
                        "status": "NOT_APPLICABLE",
                        "reason": "NO_COMPLETE_PANEL_INPUT",
                    },
                ]
            )
        return relaxation

    def _start_partial_search(
        self,
        batch: dict[str, Any],
        relaxation: dict[str, Any],
    ) -> None:
        if relaxation.get("partial_levels"):
            return
        pool_specs, _missing = self._panel_pool_specs(batch)
        target_specs = [
            copy.deepcopy(item) for item in pool_specs if item.get("role") == "target"
        ]
        internal_specs = [
            copy.deepcopy(item)
            for item in pool_specs
            if item.get("role") == "internal_control"
        ]
        requested_ids = [
            str(item.get("target_id"))
            for item in (batch.get("user_input_snapshot") or {}).get("targets") or []
        ]
        maximum_target_count = min(len(target_specs), len(requested_ids) - 1)
        levels: list[dict[str, Any]] = []
        for target_count in range(maximum_target_count, MINIMUM_TARGETS - 1, -1):
            groups: list[dict[str, Any]] = []
            for selected in itertools.combinations(target_specs, target_count):
                selected_specs = [copy.deepcopy(item) for item in selected]
                selected_ids = [str(item["target_id"]) for item in selected_specs]
                group_specs = selected_specs + copy.deepcopy(internal_specs)
                total_combinations = math.prod(
                    int(item["candidate_count"]) for item in group_specs
                )
                try:
                    assignment = self._panel_channel_assignment(batch, group_specs)
                    group_status = "RUNNING"
                    failure = None
                except Exception as exc:
                    assignment = []
                    group_status = "INVALID_CONFIGURATION"
                    failure = _error_payload(exc)
                groups.append(
                    {
                        "group_id": (
                            f"wf02-partial-{_canonical_sha256(selected_ids)[:16]}"
                        ),
                        "status": group_status,
                        "target_ids": selected_ids,
                        "omitted_target_ids": [
                            item for item in requested_ids if item not in selected_ids
                        ],
                        "pool_order": group_specs,
                        "total_combinations": total_combinations,
                        "cursor": 0,
                        "accepted_count": 0,
                        "rejected_count": 0,
                        "invalid_count": 0,
                        "channel_assignment": assignment,
                        "failure": failure,
                        "panels": [],
                    }
                )
            levels.append(
                {
                    "target_count": target_count,
                    "status": "RUNNING",
                    "accepted_count": 0,
                    "groups": groups,
                }
            )
        relaxation["partial_levels"] = levels
        relaxation["active_partial_level_index"] = 0
        relaxation["active_step"] = "N23"

    @staticmethod
    def _round_by_node(
        relaxation: dict[str, Any], node: str
    ) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in relaxation.get("rounds") or []
                if item.get("node") == node
            ),
            None,
        )

    def _pause_relaxation_for_p4(
        self,
        batch: dict[str, Any],
        relaxation: dict[str, Any],
        *,
        node: str,
        reference_id: str,
        p4_result: dict[str, Any],
    ) -> dict[str, Any]:
        failure = {
            "code": "P4_NOT_EVALUATED",
            "message": "P4 未完成放宽路径的当前面板检查，保留游标等待重试",
            "details": {
                "node": node,
                "reference_id": reference_id,
                "p4_result": copy.deepcopy(p4_result),
            },
        }
        relaxation.update({"status": "WAITING_P4_RETRY", "tool_failure": failure})
        batch.update(
            {
                "status": "WAITING_P4_RETRY",
                "stage": node,
                "next_action": "RETRY_RELAXATION",
            }
        )
        self._record_node(
            batch,
            node,
            "WAITING_TOOL",
            failure["message"],
            reference_id=reference_id,
        )
        self._append_event(
            batch,
            "WF02_RELAXATION_P4_RETRY_REQUIRED",
            status="WAITING_P4_RETRY",
            failure=copy.deepcopy(failure),
        )
        return self._save_batch(batch)

    def _finish_full_risk_relaxation(
        self,
        batch: dict[str, Any],
        relaxation: dict[str, Any],
        round_state: dict[str, Any],
    ) -> dict[str, Any]:
        node = str(round_state["node"])
        accepted = [
            copy.deepcopy(item["panel"])
            for item in round_state.get("results") or []
            if item.get("classification") == "RISK_COMPLETE"
        ]
        relaxation.update(
            {
                "status": "COMPLETED_RISK_FULL",
                "active_step": "N24",
                "full_risk_panels": accepted,
                "tool_failure": None,
                "completed_at": utc_now(),
            }
        )
        self._record_node(
            batch,
            node,
            "WARNING",
            f"第 {1 if node == 'N21' else 2} 步放宽获得 {len(accepted)} 个完整风险面板",
            thresholds={
                "cross_dimer_dg_kcal": WF02_RELAXED_DIMER_DG_KCAL,
                "tm_spread_celsius": round_state["tm_spread_limit_celsius"],
            },
            warning_required=True,
        )
        if node == "N21":
            self._record_node(batch, "N22", "NOT_APPLICABLE", "N21 已获得完整风险面板")
        self._record_node(batch, "N23", "NOT_APPLICABLE", "放宽后已获得完整风险面板")
        self._record_node(
            batch,
            "N24",
            "FULL_RISK_PANEL",
            f"放宽后获得 {len(accepted)} 个完整风险面板，进入均衡性评估",
        )
        self._record_node(batch, "N25", "NOT_APPLICABLE", "结果不是部分面板")
        self._record_node(batch, "N26", "NOT_APPLICABLE", "放宽路径未全部失败")
        batch.update(
            {
                "status": "READY_FOR_BALANCE",
                "stage": "N24",
                "next_action": "RUN_BALANCE_ASSESSMENT",
            }
        )
        self._append_event(
            batch,
            "WF02_RELAXATION_COMPLETED",
            status="READY_FOR_BALANCE",
            outcome="FULL_RISK_PANEL",
            accepted_count=len(accepted),
        )
        return self._save_batch(batch)

    def _independent_target_schemes(
        self,
        batch: dict[str, Any],
        omitted_target_ids: list[str],
    ) -> list[dict[str, Any]]:
        results = {
            str(item.get("target_id")): item
            for item in (batch.get("target_design") or {}).get("targets") or []
        }
        schemes: list[dict[str, Any]] = []
        for target_id in omitted_target_ids:
            target = results.get(target_id) or {}
            schemes.append(
                {
                    "target_id": target_id,
                    "status": (
                        "AVAILABLE_AS_SINGLEPLEX"
                        if target.get("candidates")
                        else "REDESIGN_REQUIRED"
                    ),
                    "candidate_ids": [
                        item.get("candidate_id")
                        for item in target.get("candidates") or []
                    ],
                    "wf01_batch_id": target.get("active_wf01_batch_id")
                    or target.get("wf01_batch_id"),
                    "failure": copy.deepcopy(target.get("failure")),
                }
            )
        return schemes

    def _finish_partial_relaxation(
        self,
        batch: dict[str, Any],
        relaxation: dict[str, Any],
        level: dict[str, Any],
    ) -> dict[str, Any]:
        partial_panels = [
            copy.deepcopy(panel)
            for group in level.get("groups") or []
            for panel in group.get("panels") or []
            if panel.get("classification") == "PARTIAL"
        ]
        omitted_ids = list(
            dict.fromkeys(
                target_id
                for panel in partial_panels
                for target_id in panel.get("omitted_target_ids") or []
            )
        )
        independent = self._independent_target_schemes(batch, omitted_ids)
        relaxation["relaxation_log"].append(
            {
                "node": "N23",
                "status": "PARTIAL_PANEL_FOUND",
                "thresholds": {
                    "cross_dimer_dg_kcal": WF02_RELAXED_DIMER_DG_KCAL,
                    "tm_spread_celsius": WF02_RELAXED_TM_SPREAD_C,
                },
                "maximum_partial_target_count": int(level["target_count"]),
                "accepted_count": len(partial_panels),
            }
        )
        relaxation.update(
            {
                "status": "COMPLETED_PARTIAL",
                "active_step": "N25",
                "partial_panels": partial_panels,
                "maximum_partial_target_count": int(level["target_count"]),
                "independent_target_schemes": independent,
                "tool_failure": None,
                "completed_at": utc_now(),
            }
        )
        self._record_node(
            batch,
            "N23",
            "PARTIAL",
            f"最大可同管覆盖 {level['target_count']} 个目标靶标",
            partial_panel_count=len(partial_panels),
        )
        self._record_node(
            batch,
            "N24",
            "PARTIAL_PANEL",
            "放宽后仅获得部分面板，不进入完整面板排名",
        )
        self._record_node(
            batch,
            "N25",
            "COMPLETED",
            f"输出 {len(partial_panels)} 个 PARTIAL 面板及分管方案",
            excluded_from_complete_top3=True,
        )
        self._record_node(batch, "N26", "NOT_APPLICABLE", "已获得部分面板降级结果")
        coverage = copy.deepcopy(
            (batch.get("target_design") or {}).get("coverage") or {}
        )
        target_reason_codes = list(
            dict.fromkeys(
                code
                for item in (batch.get("target_design") or {}).get("targets") or []
                for code in _reason_codes(item.get("failure") or {})
            )
        )
        batch.update(
            {
                "status": "PARTIAL_PANEL_AVAILABLE",
                "stage": "N25",
                "next_action": "REVIEW_PARTIAL_PANEL",
                "outcome": {
                    "workflow_status": "PARTIAL",
                    "primary_failure_stage": (
                        "TARGET_DESIGN"
                        if not bool(coverage.get("complete"))
                        else "PANEL_COMPATIBILITY"
                    ),
                    "coverage_status": coverage,
                    "panel_status": "PARTIAL_PANEL_AVAILABLE",
                    "reason_codes": target_reason_codes
                    or ["NO_COMPLETE_COMPATIBLE_PANEL"],
                },
            }
        )
        self._append_event(
            batch,
            "WF02_RELAXATION_COMPLETED",
            status="PARTIAL_PANEL_AVAILABLE",
            outcome="PARTIAL_PANEL",
            partial_panel_count=len(partial_panels),
            target_count=level["target_count"],
        )
        terminal_report_id = str(
            uuid5(NAMESPACE_URL, f"wf02-report:{batch['batch_id']}")
        )
        batch.update(
            {
                "report_id": terminal_report_id,
                "completed_at": utc_now(),
            }
        )
        self._append_event(
            batch,
            "WF02_TERMINAL_REPORT_COMPLETED",
            status="PARTIAL_PANEL_AVAILABLE",
            report_id=terminal_report_id,
            terminal_stage="N25",
        )
        report = self._build_and_persist_terminal_report(batch)
        batch.update(
            {
                "report_summary": copy.deepcopy(report.get("result_summary") or {}),
                "report_exports": copy.deepcopy(report.get("exports") or {}),
            }
        )
        return self._save_batch(batch)

    def _finish_failed_relaxation(
        self,
        batch: dict[str, Any],
        relaxation: dict[str, Any],
    ) -> dict[str, Any]:
        suggestions = [
            "各靶标改为单独分管检测，并复用各自 WF-01 候选",
            "减少同管靶标数量后创建新的 WF-02 版本",
            "仅在分管 TaqMan 仍不可行时，考虑 SYBR Green + 熔解曲线降级；当前 WF 不生成该方案",
        ]
        requested_ids = [
            str(item.get("target_id"))
            for item in (batch.get("user_input_snapshot") or {}).get("targets") or []
        ]
        relaxation["relaxation_log"].append(
            {
                "node": "N23",
                "status": "NO_PARTIAL_PANEL",
                "thresholds": {
                    "cross_dimer_dg_kcal": WF02_RELAXED_DIMER_DG_KCAL,
                    "tm_spread_celsius": WF02_RELAXED_TM_SPREAD_C,
                },
            }
        )
        relaxation.update(
            {
                "status": "FAILED",
                "active_step": "N26",
                "failure_suggestions": suggestions,
                "independent_target_schemes": self._independent_target_schemes(
                    batch, requested_ids
                ),
                "tool_failure": None,
                "completed_at": utc_now(),
            }
        )
        self._record_node(batch, "N23", "NO_PARTIAL_PANEL", "未找到至少二靶标的兼容部分面板")
        self._record_node(batch, "N24", "ALL_FAILED", "完整放宽和部分面板搜索均失败")
        self._record_node(batch, "N25", "NOT_APPLICABLE", "没有可交付的部分面板")
        self._record_node(
            batch,
            "N26",
            "COMPLETED",
            "本轮中止，并输出分管、减少靶标及 SYBR Green 降级建议",
            suggestions=copy.deepcopy(suggestions),
        )
        target_design = batch.get("target_design") or {}
        coverage = copy.deepcopy(target_design.get("coverage") or {})
        coverage_incomplete = not bool(coverage.get("complete")) or bool(
            target_design.get("failed_target_ids")
        )
        target_reason_codes = list(
            dict.fromkeys(
                code
                for item in target_design.get("targets") or []
                for code in _reason_codes(item.get("failure") or {})
            )
        )
        terminal_status = (
            "FAILED_INCOMPLETE_TARGET_COVERAGE"
            if coverage_incomplete
            else "FAILED_NO_COMPATIBLE_PANEL"
        )
        primary_failure_stage = (
            "TARGET_DESIGN" if coverage_incomplete else "PANEL_COMPATIBILITY"
        )
        reason_codes = target_reason_codes or [
            (
                "INCOMPLETE_TARGET_COVERAGE"
                if coverage_incomplete
                else "NO_COMPATIBLE_PANEL"
            )
        ]
        batch.update(
            {
                "status": terminal_status,
                "stage": "N26",
                "next_action": "REVIEW_FAILURE_GUIDANCE",
                "outcome": {
                    "workflow_status": "FAILED",
                    "primary_failure_stage": primary_failure_stage,
                    "coverage_status": coverage,
                    "panel_status": (
                        "NO_COMPATIBLE_PANEL_FOR_AVAILABLE_TARGETS"
                        if coverage_incomplete
                        else "NO_COMPATIBLE_PANEL"
                    ),
                    "reason_codes": reason_codes,
                },
            }
        )
        self._append_event(
            batch,
            "WF02_RELAXATION_COMPLETED",
            status=terminal_status,
            outcome=(
                "INCOMPLETE_TARGET_COVERAGE"
                if coverage_incomplete
                else "ALL_FAILED"
            ),
            primary_failure_stage=primary_failure_stage,
            reason_codes=copy.deepcopy(reason_codes),
        )
        terminal_report_id = str(
            uuid5(NAMESPACE_URL, f"wf02-report:{batch['batch_id']}")
        )
        batch.update(
            {
                "report_id": terminal_report_id,
                "completed_at": utc_now(),
            }
        )
        self._append_event(
            batch,
            "WF02_TERMINAL_REPORT_COMPLETED",
            status=terminal_status,
            report_id=terminal_report_id,
            terminal_stage="N26",
        )
        report = self._build_and_persist_terminal_report(batch)
        batch.update(
            {
                "report_summary": copy.deepcopy(report.get("result_summary") or {}),
                "report_exports": copy.deepcopy(report.get("exports") or {}),
            }
        )
        return self._save_batch(batch)

    def _process_full_relaxation_round(
        self,
        batch: dict[str, Any],
        relaxation: dict[str, Any],
        round_state: dict[str, Any],
        budget: int,
    ) -> tuple[int, dict[str, Any] | None]:
        screening = (batch.get("multiplex") or {}).get("screening") or {}
        source_by_id = {
            str(item.get("panel_id")): item for item in screening.get("panels") or []
        }
        minimum_length_difference = int(
            self.config["multiplex"]["minimum_amplicon_length_difference_bp"]
        )
        instrument_evidence = screening.get("instrument_evidence") or {}
        source_ids = round_state.get("source_panel_ids") or []
        while budget > 0 and int(round_state.get("cursor") or 0) < len(source_ids):
            source_id = str(source_ids[int(round_state["cursor"])])
            source = source_by_id.get(source_id) or {}
            entries = copy.deepcopy(source.get("p4_input") or [])
            result = self._call_relaxed_p4(
                batch,
                entries,
                tm_spread_limit=float(round_state["tm_spread_limit_celsius"]),
            )
            completed, verdict = self._completed_p4_result(result)
            if not completed:
                return budget, self._pause_relaxation_for_p4(
                    batch,
                    relaxation,
                    node=str(round_state["node"]),
                    reference_id=source_id,
                    p4_result=result,
                )
            primer_spread, probe_spread = self._tm_spreads(result)
            tm_limit = float(round_state["tm_spread_limit_celsius"])
            tm_eligible = (
                primer_spread is not None
                and probe_spread is not None
                and primer_spread <= tm_limit
                and probe_spread <= tm_limit
            )
            length_check = _r17_amplicon_length_check(
                entries, minimum_length_difference
            )
            eligible = (
                verdict in {"COMPATIBLE", "MARGINAL"}
                and tm_eligible
                and length_check.get("status") == "PASS"
            )
            panel = {
                "panel_id": f"{source_id}-{str(round_state['node']).lower()}",
                "source_panel_id": source_id,
                "members": copy.deepcopy(source.get("members") or []),
                "p4_input": entries,
                "channel_assignment": copy.deepcopy(
                    source.get("channel_assignment") or []
                ),
                "p4_result": result,
                "p4_verdict": verdict,
                "amplicon_length_check": length_check,
                "classification": "RISK_COMPLETE" if eligible else "REJECTED",
                "relaxation_applied": [
                    "CROSS_DIMER_DG_GT_NEGATIVE_7"
                ]
                + (
                    ["PRIMER_AND_PROBE_TM_SPREAD_LE_4C"]
                    if round_state["node"] == "N22"
                    else []
                ),
                "risk_reasons": self._relaxed_panel_risk_reasons(
                    step=str(round_state["node"]),
                    p4_verdict=verdict,
                    length_check=length_check,
                    instrument_evidence=instrument_evidence,
                    partial=False,
                ),
                "warnings": ["该面板使用了放宽阈值，必须进行实验验证"],
                "instrument_evidence": copy.deepcopy(instrument_evidence),
            }
            round_state["results"].append(
                {
                    "source_panel_id": source_id,
                    "classification": panel["classification"],
                    "panel": panel,
                }
            )
            if eligible:
                round_state["accepted_count"] += 1
            else:
                round_state["rejected_count"] += 1
            round_state["cursor"] += 1
            budget -= 1
        if int(round_state.get("cursor") or 0) >= len(source_ids):
            round_state.update({"status": "COMPLETED", "completed_at": utc_now()})
        return budget, None

    def _process_partial_level(
        self,
        batch: dict[str, Any],
        relaxation: dict[str, Any],
        level: dict[str, Any],
        budget: int,
    ) -> tuple[int, dict[str, Any] | None]:
        screening = (batch.get("multiplex") or {}).get("screening") or {}
        instrument_evidence = screening.get("instrument_evidence") or {}
        minimum_length_difference = int(
            self.config["multiplex"]["minimum_amplicon_length_difference_bp"]
        )
        for group in level.get("groups") or []:
            if budget <= 0:
                break
            if group.get("status") == "INVALID_CONFIGURATION":
                continue
            if int(group.get("cursor") or 0) >= int(group["total_combinations"]):
                group["status"] = "COMPLETED"
                continue
            specs = group.get("pool_order") or []
            pools = self._panel_candidate_pools(batch, specs)
            assignment = group.get("channel_assignment") or []
            assignment_by_target = {
                str(item.get("target_id")): item for item in assignment
            }
            start = int(group.get("cursor") or 0)
            selected = _product_window(
                pools,
                start,
                min(start + budget, int(group["total_combinations"])),
            )
            for combination in selected:
                member_refs = [
                    {
                        "target_id": spec.get("target_id"),
                        "role": spec.get("role"),
                        "candidate_id": candidate.get("candidate_id"),
                    }
                    for spec, candidate in zip(specs, combination)
                ]
                try:
                    entries = []
                    for spec, candidate in zip(specs, combination):
                        target_id = str(spec.get("target_id"))
                        channel = assignment_by_target[target_id]
                        entries.append(
                            _candidate_panel_entry(
                                candidate,
                                target_id=target_id,
                                role=str(spec.get("role")),
                                fluorophore=str(channel.get("fluorophore")),
                                quencher=str(
                                    channel.get("recommended_quencher") or ""
                                ),
                            )
                        )
                except Exception as exc:
                    group["panels"].append(
                        {
                            "panel_id": f"wf02-partial-invalid-{_canonical_sha256(member_refs)[:16]}",
                            "members": member_refs,
                            "classification": "INVALID",
                            "invalid_reason": _error_payload(exc),
                        }
                    )
                    group["invalid_count"] += 1
                    group["cursor"] += 1
                    budget -= 1
                    if budget <= 0:
                        break
                    continue
                resolved_assignment = _resolved_panel_channel_assignment(
                    assignment, entries
                )
                panel_id = self._panel_identifier(entries, resolved_assignment)
                result = self._call_relaxed_p4(
                    batch,
                    entries,
                    tm_spread_limit=WF02_RELAXED_TM_SPREAD_C,
                )
                completed, verdict = self._completed_p4_result(result)
                if not completed:
                    return budget, self._pause_relaxation_for_p4(
                        batch,
                        relaxation,
                        node="N23",
                        reference_id=panel_id,
                        p4_result=result,
                    )
                primer_spread, probe_spread = self._tm_spreads(result)
                tm_eligible = (
                    primer_spread is not None
                    and probe_spread is not None
                    and primer_spread <= WF02_RELAXED_TM_SPREAD_C
                    and probe_spread <= WF02_RELAXED_TM_SPREAD_C
                )
                length_check = _r17_amplicon_length_check(
                    entries, minimum_length_difference
                )
                eligible = (
                    verdict in {"COMPATIBLE", "MARGINAL"}
                    and tm_eligible
                    and length_check.get("status") == "PASS"
                )
                panel = {
                    "panel_id": f"{panel_id}-partial",
                    "members": member_refs,
                    "included_target_ids": copy.deepcopy(group["target_ids"]),
                    "omitted_target_ids": copy.deepcopy(group["omitted_target_ids"]),
                    "coverage": {
                        "included_targets": len(group["target_ids"]),
                        "requested_targets": len(
                            (batch.get("user_input_snapshot") or {}).get("targets") or []
                        ),
                    },
                    "p4_input": entries,
                    "channel_assignment": resolved_assignment,
                    "p4_result": result,
                    "p4_verdict": verdict,
                    "amplicon_length_check": length_check,
                    "classification": "PARTIAL" if eligible else "INCOMPATIBLE",
                    "excluded_from_complete_top3": True,
                    "relaxation_applied": [
                        "CROSS_DIMER_DG_GT_NEGATIVE_7",
                        "PRIMER_AND_PROBE_TM_SPREAD_LE_4C",
                        "PARTIAL_TARGET_COVERAGE",
                    ],
                    "risk_reasons": self._relaxed_panel_risk_reasons(
                        step="N23",
                        p4_verdict=verdict,
                        length_check=length_check,
                        instrument_evidence=instrument_evidence,
                        partial=True,
                    ),
                    "warnings": [
                        "PARTIAL 仅为降级分管建议，不得作为完整 Top 3 面板交付",
                        "该面板使用了放宽阈值，必须进行实验验证",
                    ],
                    "instrument_evidence": copy.deepcopy(instrument_evidence),
                }
                group["panels"].append(panel)
                if eligible:
                    group["accepted_count"] += 1
                    level["accepted_count"] += 1
                else:
                    group["rejected_count"] += 1
                group["cursor"] += 1
                budget -= 1
                if budget <= 0:
                    break
            if int(group.get("cursor") or 0) >= int(group["total_combinations"]):
                group["status"] = "COMPLETED"
        level_complete = all(
            group.get("status") in {"COMPLETED", "INVALID_CONFIGURATION"}
            for group in level.get("groups") or []
        )
        if level_complete:
            level.update({"status": "COMPLETED", "completed_at": utc_now()})
        return budget, None

    def execute_panel_relaxation(
        self,
        batch_id: str,
        *,
        max_combinations: int | None = None,
    ) -> dict[str, Any]:
        """Execute one resumable N21-N26 relaxation/partial-panel batch."""
        batch = self.store.get_batch(batch_id)
        if batch.get("status") == "READY_FOR_BALANCE":
            return batch
        if batch.get("status") not in {
            "READY_FOR_RELAXATION",
            "RELAXING_PANELS",
            "WAITING_P4_RETRY",
        }:
            raise InputValidationError("当前 WF-02 状态不能执行 N21-N26")
        relaxation = self._initialise_relaxation(batch)
        if relaxation.get("status") in {
            "COMPLETED_RISK_FULL",
            "COMPLETED_PARTIAL",
            "FAILED",
        }:
            return batch
        if relaxation.get("status") == "WAITING_P4_RETRY":
            relaxation.update({"status": "RUNNING", "tool_failure": None})
        batch.update(
            {
                "status": "RELAXING_PANELS",
                "stage": str(relaxation.get("active_step") or "N21"),
                "next_action": "CONTINUE_RELAXATION",
            }
        )
        budget = max(1, int(relaxation.get("combination_batch_size") or 1))
        if max_combinations is not None:
            budget = min(budget, max(1, int(max_combinations)))
        while budget > 0:
            active_step = str(relaxation.get("active_step") or "N21")
            if active_step in {"N21", "N22"}:
                round_state = self._round_by_node(relaxation, active_step)
                if round_state is None:
                    round_state = self._new_relaxation_round(
                        (batch.get("multiplex") or {}).get("screening") or {},
                        node=active_step,
                        tm_spread_limit=(
                            WF02_STANDARD_TM_SPREAD_C
                            if active_step == "N21"
                            else WF02_RELAXED_TM_SPREAD_C
                        ),
                    )
                    relaxation["rounds"].append(round_state)
                budget, paused = self._process_full_relaxation_round(
                    batch, relaxation, round_state, budget
                )
                if paused is not None:
                    return paused
                if round_state.get("status") != "COMPLETED":
                    break
                relaxation["relaxation_log"].append(
                    {
                        "node": active_step,
                        "status": (
                            "RISK_PANEL_FOUND"
                            if int(round_state.get("accepted_count") or 0)
                            else "NO_PANEL"
                        ),
                        "thresholds": {
                            "cross_dimer_dg_kcal": WF02_RELAXED_DIMER_DG_KCAL,
                            "tm_spread_celsius": round_state[
                                "tm_spread_limit_celsius"
                            ],
                        },
                        "accepted_count": int(
                            round_state.get("accepted_count") or 0
                        ),
                    }
                )
                if int(round_state.get("accepted_count") or 0):
                    return self._finish_full_risk_relaxation(
                        batch, relaxation, round_state
                    )
                self._record_node(
                    batch,
                    active_step,
                    "NO_PANEL",
                    (
                        "二聚体放宽后仍无完整面板"
                        if active_step == "N21"
                        else "Tm 极差放宽至 4°C 后仍无完整面板"
                    ),
                    warning_required=True,
                )
                if active_step == "N21":
                    relaxation["active_step"] = "N22"
                    continue
                relaxation["active_step"] = "N23"
                self._start_partial_search(batch, relaxation)
                continue
            if active_step == "N23":
                self._start_partial_search(batch, relaxation)
                levels = relaxation.get("partial_levels") or []
                level_index = int(relaxation.get("active_partial_level_index") or 0)
                if level_index >= len(levels):
                    return self._finish_failed_relaxation(batch, relaxation)
                level = levels[level_index]
                budget, paused = self._process_partial_level(
                    batch, relaxation, level, budget
                )
                if paused is not None:
                    return paused
                if level.get("status") != "COMPLETED":
                    break
                if int(level.get("accepted_count") or 0):
                    return self._finish_partial_relaxation(
                        batch, relaxation, level
                    )
                relaxation["active_partial_level_index"] = level_index + 1
                continue
            break
        batch.update(
            {
                "status": "RELAXING_PANELS",
                "stage": str(relaxation.get("active_step") or "N23"),
                "next_action": "CONTINUE_RELAXATION",
            }
        )
        self._append_event(
            batch,
            "WF02_RELAXATION_BATCH_COMPLETED",
            status="RELAXING_PANELS",
            active_step=relaxation.get("active_step"),
        )
        return self._save_batch(batch)

    @staticmethod
    def _full_panels_for_balance(batch: dict[str, Any]) -> list[dict[str, Any]]:
        multiplex = batch.get("multiplex") or {}
        screening = multiplex.get("screening") or {}
        panels = [
            copy.deepcopy(item)
            for item in screening.get("panels") or []
            if item.get("classification") in {"STRICT_COMPATIBLE", "RISK"}
        ]
        panels.extend(
            copy.deepcopy(item)
            for item in (multiplex.get("relaxation") or {}).get(
                "full_risk_panels"
            )
            or []
            if item.get("classification") == "RISK_COMPLETE"
        )
        unique: dict[str, dict[str, Any]] = {}
        for panel in panels:
            panel_id = str(panel.get("panel_id") or "")
            if panel_id:
                unique.setdefault(panel_id, panel)
        return list(unique.values())

    @staticmethod
    def _balance_assessment(panel: dict[str, Any]) -> dict[str, Any]:
        entries = panel.get("p4_input") or []
        primer_values = [
            (float(entry[key]), str(entry.get("target_id")), str(entry.get("role")))
            for entry in entries
            for key in ("fwd_tm", "rev_tm")
        ]
        probe_values = [
            (float(entry["probe_tm"]), str(entry.get("target_id")), str(entry.get("role")))
            for entry in entries
        ]
        gc_values = [float(entry["gc_percent"]) for entry in entries]
        primer_spread = (
            max(item[0] for item in primer_values)
            - min(item[0] for item in primer_values)
        )
        probe_spread = (
            max(item[0] for item in probe_values)
            - min(item[0] for item in probe_values)
        )
        gc_spread = max(gc_values) - min(gc_values)
        outliers: list[str] = []
        for values, spread in (
            (primer_values, primer_spread),
            (probe_values, probe_spread),
        ):
            if spread <= WF02_STANDARD_TM_SPREAD_C:
                continue
            minimum = min(item[0] for item in values)
            maximum = max(item[0] for item in values)
            for value, target_id, role in values:
                if role == "target" and value in {minimum, maximum}:
                    outliers.append(target_id)
        tm_status = (
            "PASS"
            if primer_spread <= WF02_STANDARD_TM_SPREAD_C
            and probe_spread <= WF02_STANDARD_TM_SPREAD_C
            else "WARNING"
        )
        gc_status = (
            "PASS"
            if gc_spread <= 15.0
            else "CAUTION"
            if gc_spread <= 20.0
            else "WARNING"
        )
        warnings: list[str] = []
        if tm_status == "WARNING":
            warnings.append(
                "Tm_WARNING：引物或探针 Tm 极差超过 3°C"
            )
        if gc_status == "CAUTION":
            warnings.append("GC_CAUTION：扩增子 GC% 极差为 15–20%")
        elif gc_status == "WARNING":
            warnings.append("GC_WARNING：扩增子 GC% 极差超过 20%")
        primer_center = round(
            min(64.0, max(51.0, statistics.median(item[0] for item in primer_values))),
            2,
        )
        return {
            "policy_version": WF02_BALANCE_POLICY_VERSION,
            "primer_tm_spread_celsius": round(primer_spread, 3),
            "probe_tm_spread_celsius": round(probe_spread, 3),
            "tm_limit_celsius": WF02_STANDARD_TM_SPREAD_C,
            "tm_status": tm_status,
            "gc_spread_percent": round(gc_spread, 3),
            "gc_status": gc_status,
            "gc_policy": {
                "pass_max_percent": 15.0,
                "caution_max_percent": 20.0,
                "above_20_status": "WARNING",
            },
            "outlier_target_ids": list(dict.fromkeys(outliers)),
            "redesign_primer_tm_center_celsius": primer_center,
            "warnings": warnings,
        }

    def _initialise_balance(self, batch: dict[str, Any]) -> dict[str, Any]:
        multiplex = batch.setdefault("multiplex", {})
        existing = multiplex.get("balance")
        if isinstance(existing, dict):
            return existing
        panels = self._full_panels_for_balance(batch)
        if not panels:
            raise InputValidationError("N27 没有可评估的完整面板")
        balance = {
            "status": "RUNNING",
            "policy_version": WF02_BALANCE_POLICY_VERSION,
            "maximum_redesign_attempts": WF02_MAX_BALANCE_REDESIGNS,
            "redesign_attempt_count": 0,
            "redesign_attempts": [],
            "active_redesign_jobs": [],
            "original_panels": copy.deepcopy(panels),
            "selected_panels": [],
            "panel_assessments": [],
            "started_at": utc_now(),
        }
        multiplex["balance"] = balance
        return balance

    @staticmethod
    def _target_result_by_id(
        batch: dict[str, Any], target_id: str
    ) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in (batch.get("target_design") or {}).get("targets") or []
                if str(item.get("target_id")) == target_id
            ),
            None,
        )

    def _start_balance_redesign(
        self,
        batch: dict[str, Any],
        balance: dict[str, Any],
        assessment: dict[str, Any],
    ) -> None:
        gateway = self._require_gateway(batch)
        center = float(assessment["redesign_primer_tm_center_celsius"])
        constraints = {
            "primer_min_tm": round(center - 1.0, 2),
            "primer_opt_tm": round(center, 2),
            "primer_max_tm": round(center + 1.0, 2),
        }
        jobs: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        attempt_number = int(balance.get("redesign_attempt_count") or 0) + 1
        for target_id in assessment.get("outlier_target_ids") or []:
            target = self._target_result_by_id(batch, str(target_id))
            if target is None:
                failures.append(
                    {"target_id": target_id, "code": "TARGET_RESULT_MISSING"}
                )
                continue
            source_batch_id = target.get("active_wf01_batch_id") or target.get(
                "wf01_batch_id"
            )
            child_target_id = target.get("child_target_id")
            if not source_batch_id or not child_target_id:
                failures.append(
                    {
                        "target_id": target_id,
                        "code": "WF01_VERSION_SOURCE_MISSING",
                    }
                )
                continue
            try:
                created = gateway.create_wf01_tm_constrained_version(
                    {
                        "source_batch_id": source_batch_id,
                        "target_id": child_target_id,
                        "constraints": constraints,
                        "reason": (
                            f"WF-02 N27 第 {attempt_number} 次 Tm 均衡性定向重设计"
                        ),
                    }
                )
                version_batch_id = str(created.get("batch_id") or "")
                if not version_batch_id:
                    raise InputValidationError("WF-01 M7 结果缺少 batch_id")
            except Exception as exc:
                failures.append(
                    {
                        "target_id": target_id,
                        "code": "WF01_VERSION_CREATE_FAILED",
                        "failure": _error_payload(exc),
                    }
                )
                continue
            job = {
                "target_id": target_id,
                "child_target_id": child_target_id,
                "source_wf01_batch_id": source_batch_id,
                "wf01_batch_id": version_batch_id,
                "wf01_batch_code": created.get("batch_code"),
                "wf01_task_version": created.get("task_version"),
                "constraints": copy.deepcopy(constraints),
                "status": "QUEUED",
            }
            jobs.append(job)
            target.update(
                {
                    "active_wf01_batch_id": version_batch_id,
                    "active_wf01_batch_code": created.get("batch_code"),
                    "active_wf01_task_version": created.get("task_version"),
                }
            )
            target.setdefault("wf01_lineage", []).append(
                {
                    "batch_id": version_batch_id,
                    "batch_code": created.get("batch_code"),
                    "task_version": created.get("task_version"),
                    "purpose": "N27_TM_BALANCE_REDESIGN",
                    "attempt": attempt_number,
                    "constraints": copy.deepcopy(constraints),
                }
            )
        attempt = {
            "attempt": attempt_number,
            "source_panel_id": assessment.get("panel_id"),
            "constraints": constraints,
            "jobs": jobs,
            "failures": failures,
            "started_at": utc_now(),
        }
        balance["redesign_attempt_count"] = attempt_number
        balance["redesign_attempts"].append(attempt)
        balance["active_redesign_jobs"] = jobs
        balance["status"] = "REDESIGNING_TARGETS" if jobs else "REDESIGN_UNAVAILABLE"
        self._append_event(
            batch,
            "WF02_BALANCE_REDESIGN_STARTED",
            status=balance["status"],
            attempt=attempt_number,
            target_ids=[item["target_id"] for item in jobs],
            failures=copy.deepcopy(failures),
        )

    def _advance_balance_redesign_jobs(
        self,
        batch: dict[str, Any],
        balance: dict[str, Any],
    ) -> bool:
        gateway = self._require_gateway(batch)
        waiting = False
        for job in balance.get("active_redesign_jobs") or []:
            if job.get("status") == "COMPLETED":
                continue
            target = self._target_result_by_id(batch, str(job.get("target_id")))
            if target is None:
                job.update(
                    {"status": "FAILED", "failure": {"code": "TARGET_RESULT_MISSING"}}
                )
                continue
            try:
                summary = gateway.run_wf01_v02_until_blocked(
                    str(job["wf01_batch_id"])
                )
            except Exception as exc:
                job.update({"status": "FAILED", "failure": _error_payload(exc)})
                continue
            job["child_status"] = summary.get("status")
            job["child_next_action"] = summary.get("next_action")
            if summary.get("next_action") == "WAIT_FOR_USER":
                job["status"] = "WAITING_USER"
                target["status"] = "WAITING_USER"
                target["child_next_action"] = summary.get("next_action")
                target["child_ui_message"] = copy.deepcopy(summary.get("ui_message"))
                waiting = True
                continue
            if summary.get("next_action") == "WAIT_FOR_STAGE_COMPLETION":
                job["status"] = "RUNNING"
                continue
            if summary.get("next_action") != "COMPLETE":
                job.update(
                    {
                        "status": "FAILED",
                        "failure": {
                            "code": "WF01_REDESIGN_NOT_COMPLETABLE",
                            "details": copy.deepcopy(summary),
                        },
                    }
                )
                continue
            try:
                report = gateway.get_wf01_v02_report(str(job["wf01_batch_id"]))
                candidates, evidence = self._collect_wf01_candidates(report)
                p6_summary = self._assess_candidates_with_p6(candidates, batch)
            except Exception as exc:
                job.update({"status": "FAILED", "failure": _error_payload(exc)})
                continue
            if not candidates:
                job.update(
                    {
                        "status": "FAILED",
                        "failure": {
                            "code": "WF01_REDESIGN_NO_CANDIDATES",
                            "details": evidence,
                        },
                    }
                )
                continue
            target.update(
                {
                    "status": "PASS",
                    "child_status": summary.get("status"),
                    "child_next_action": summary.get("next_action"),
                    "child_ui_message": None,
                    "child_target_id": evidence.get("child_target_id")
                    or target.get("child_target_id"),
                    "wf01_result": evidence,
                    "candidates": candidates,
                    "candidate_count": len(candidates),
                    "p6_summary": p6_summary,
                }
            )
            target.pop("failure", None)
            job.update(
                {
                    "status": "COMPLETED",
                    "candidate_count": len(candidates),
                    "completed_at": utc_now(),
                }
            )
        if waiting:
            balance["status"] = "WAITING_CHILD_INPUT"
            batch.update(
                {
                    "status": "WAITING_CHILD_INPUT",
                    "stage": "N27",
                    "next_action": "WAIT_FOR_CHILD_INPUT",
                }
            )
            return False
        unfinished = [
            item
            for item in balance.get("active_redesign_jobs") or []
            if item.get("status") in {"QUEUED", "RUNNING", "WAITING_USER"}
        ]
        if unfinished:
            balance["status"] = "REDESIGNING_TARGETS"
            batch.update(
                {
                    "status": "BALANCING_PANELS",
                    "stage": "N27",
                    "next_action": "CONTINUE_BALANCE_ASSESSMENT",
                }
            )
            return False
        succeeded = [
            item
            for item in balance.get("active_redesign_jobs") or []
            if item.get("status") == "COMPLETED"
        ]
        current_attempt = (balance.get("redesign_attempts") or [])[-1]
        current_attempt["completed_at"] = utc_now()
        current_attempt["successful_target_ids"] = [
            item["target_id"] for item in succeeded
        ]
        if not succeeded:
            balance["status"] = "REDESIGN_UNAVAILABLE"
            return True
        target_design = batch.get("target_design") or {}
        target_design["p6_summary"] = _aggregate_p6_summaries(
            [
                item.get("p6_summary") or _empty_p6_summary()
                for item in target_design.get("targets") or []
                if item.get("status") in {"PASS", "FAIL"}
            ]
        )
        multiplex = batch.setdefault("multiplex", {})
        multiplex.pop("screening", None)
        multiplex.pop("relaxation", None)
        balance["active_redesign_jobs"] = []
        balance["status"] = "REBUILDING_PANELS"
        batch.update(
            {
                "status": "READY_FOR_PANEL_COMBINATION",
                "stage": "N14",
                "next_action": "RUN_PANEL_COMBINATION",
            }
        )
        return True

    def _rebuild_panels_for_balance(
        self,
        batch: dict[str, Any],
        balance: dict[str, Any],
    ) -> dict[str, Any]:
        for _ in range(10000):
            screening = (batch.get("multiplex") or {}).get("screening") or {}
            if screening.get("status") not in {"COMPLETED", "NO_COMPLETE_PANEL"}:
                batch = self.execute_panel_screening(str(batch["batch_id"]))
                balance = (batch.get("multiplex") or {}).get("balance") or balance
                screening = (batch.get("multiplex") or {}).get("screening") or {}
                if screening.get("status") == "WAITING_P4_RETRY":
                    balance["status"] = "REBUILDING_PANELS"
                    return batch
                if screening.get("status") not in {"COMPLETED", "NO_COMPLETE_PANEL"}:
                    continue
            if batch.get("status") == "READY_FOR_RELAXATION":
                batch = self.execute_panel_relaxation(str(batch["batch_id"]))
                balance = (batch.get("multiplex") or {}).get("balance") or balance
                relaxation = (batch.get("multiplex") or {}).get("relaxation") or {}
                if relaxation.get("status") == "WAITING_P4_RETRY":
                    balance["status"] = "REBUILDING_PANELS"
                    return batch
                if relaxation.get("status") == "RUNNING":
                    continue
            break
        if batch.get("status") == "READY_FOR_BALANCE":
            balance["status"] = "RUNNING"
        else:
            balance["status"] = "REDESIGN_NO_FULL_PANEL"
        return batch

    @staticmethod
    def _best_balance_assessment(
        assessments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return min(
            assessments,
            key=lambda item: (
                max(
                    float(item["balance"]["primer_tm_spread_celsius"]),
                    float(item["balance"]["probe_tm_spread_celsius"]),
                ),
                float(item["balance"]["gc_spread_percent"]),
                str(item.get("panel_id") or ""),
            ),
        )

    def execute_balance_assessment(self, batch_id: str) -> dict[str, Any]:
        """Execute N27, including at most two WF-01 M7 redesign attempts."""
        batch = self.store.get_batch(batch_id)
        balance = self._initialise_balance(batch)
        if balance.get("status") == "COMPLETED":
            return batch
        for _ in range(WF02_MAX_BALANCE_REDESIGNS + 3):
            if balance.get("status") in {
                "REDESIGNING_TARGETS",
                "WAITING_CHILD_INPUT",
            }:
                if not self._advance_balance_redesign_jobs(batch, balance):
                    return self._save_batch(batch)
            if balance.get("status") == "REBUILDING_PANELS":
                batch = self._save_batch(batch)
                batch = self._rebuild_panels_for_balance(batch, balance)
                balance = (batch.get("multiplex") or {}).get("balance") or balance
                if balance.get("status") == "REBUILDING_PANELS":
                    return self._save_batch(batch)
            panels = (
                self._full_panels_for_balance(batch)
                if balance.get("status") != "REDESIGN_NO_FULL_PANEL"
                else []
            )
            if not panels:
                panels = copy.deepcopy(balance.get("original_panels") or [])
            assessments = [
                {
                    "panel_id": panel.get("panel_id"),
                    "panel": panel,
                    "balance": self._balance_assessment(panel),
                }
                for panel in panels
            ]
            balance["panel_assessments"] = [
                {
                    "panel_id": item["panel_id"],
                    "balance": copy.deepcopy(item["balance"]),
                }
                for item in assessments
            ]
            passing = [
                item for item in assessments if item["balance"]["tm_status"] == "PASS"
            ]
            if passing:
                selected = []
                for item in passing:
                    panel = copy.deepcopy(item["panel"])
                    panel["balance"] = copy.deepcopy(item["balance"])
                    selected.append(panel)
                balance.update(
                    {
                        "status": "COMPLETED",
                        "selected_panels": selected,
                        "tm_warning_fallback": False,
                        "completed_at": utc_now(),
                    }
                )
                break
            best = self._best_balance_assessment(assessments)
            best_for_redesign = {
                "panel_id": best["panel_id"],
                **copy.deepcopy(best["balance"]),
            }
            if (
                int(balance.get("redesign_attempt_count") or 0)
                < WF02_MAX_BALANCE_REDESIGNS
                and best["balance"].get("outlier_target_ids")
            ):
                self._start_balance_redesign(batch, balance, best_for_redesign)
                if balance.get("status") == "REDESIGNING_TARGETS":
                    if not self._advance_balance_redesign_jobs(batch, balance):
                        return self._save_batch(batch)
                    continue
            panel = copy.deepcopy(best["panel"])
            fallback_balance = copy.deepcopy(best["balance"])
            fallback_balance["tm_warning_after_max_redesigns"] = True
            panel["balance"] = fallback_balance
            panel.setdefault("warnings", []).append(
                "Tm_WARNING：最多两次定向重设计后仍未满足 3°C 极差，取当前最优组合"
            )
            balance.update(
                {
                    "status": "COMPLETED",
                    "selected_panels": [panel],
                    "tm_warning_fallback": True,
                    "completed_at": utc_now(),
                }
            )
            break
        self._record_node(
            batch,
            "N27",
            "WARNING" if balance.get("tm_warning_fallback") else "COMPLETED",
            (
                "Tm 定向重设计达到上限，保留最优组合并标记 Tm_WARNING"
                if balance.get("tm_warning_fallback")
                else f"{len(balance.get('selected_panels') or [])} 个完整面板通过 Tm 均衡性门槛"
            ),
            redesign_attempt_count=balance.get("redesign_attempt_count"),
            gc_policy="PASS<=15%; CAUTION=15-20%; WARNING>20%",
        )
        batch.update(
            {
                "status": "READY_FOR_SCORING",
                "stage": "N27",
                "next_action": "RUN_PANEL_SCORING",
            }
        )
        self._append_event(
            batch,
            "WF02_BALANCE_COMPLETED",
            status="READY_FOR_SCORING",
            selected_panel_count=len(balance.get("selected_panels") or []),
            redesign_attempt_count=balance.get("redesign_attempt_count"),
        )
        return self._save_batch(batch)

    @staticmethod
    def _normalised_member_score(value: Any) -> float:
        selected = _number(value)
        if selected is None:
            return 0.0
        if selected > 1.0:
            selected /= 100.0
        return min(1.0, max(0.0, float(selected)))

    @staticmethod
    def _compatibility_coefficient(panel: dict[str, Any]) -> tuple[float, str]:
        classification = str(panel.get("classification") or "")
        relaxations = set(panel.get("relaxation_applied") or [])
        if classification == "STRICT_COMPATIBLE":
            return 1.0, "STRICT_COMPATIBLE"
        if "PRIMER_AND_PROBE_TM_SPREAD_LE_4C" in relaxations:
            return 0.80, "TM_AND_DIMER_RELAXED"
        if "CROSS_DIMER_DG_GT_NEGATIVE_7" in relaxations:
            return 0.85, "DIMER_RELAXED"
        if classification in {"RISK", "RISK_COMPLETE"}:
            return 0.90, "RISK_PANEL"
        return 0.75, "UNCLASSIFIED_RISK"

    def _score_panel(self, panel: dict[str, Any]) -> dict[str, Any]:
        member_scores = []
        internal_scores = []
        for entry in panel.get("p4_input") or []:
            item = {
                "target_id": entry.get("target_id"),
                "role": entry.get("role"),
                "candidate_id": entry.get("candidate_id"),
                "raw_score": entry.get("score"),
                "normalised_score": round(
                    self._normalised_member_score(entry.get("score")), 6
                ),
            }
            if entry.get("role") == "internal_control":
                internal_scores.append(item)
            else:
                member_scores.append(item)
        target_quality_sum = sum(item["normalised_score"] for item in member_scores)
        compatibility, compatibility_basis = self._compatibility_coefficient(panel)
        balance = panel.get("balance") or {}
        tm_coefficient = 1.0 if balance.get("tm_status") == "PASS" else 0.85
        final_score = round(
            target_quality_sum * compatibility * tm_coefficient,
            6,
        )
        return {
            "policy_version": WF02_SCORING_POLICY_VERSION,
            "formula": (
                "sum(target_member_normalised_score) * "
                "compatibility_coefficient * tm_balance_coefficient"
            ),
            "target_member_scores": member_scores,
            "target_quality_sum": round(target_quality_sum, 6),
            "internal_control_scores": internal_scores,
            "internal_control_in_numerator": False,
            "internal_control_role": "COMPATIBILITY_GATE_AND_SEPARATE_EVIDENCE",
            "compatibility_coefficient": compatibility,
            "compatibility_basis": compatibility_basis,
            "tm_balance_coefficient": tm_coefficient,
            "gc_in_score": False,
            "final_score": final_score,
            "explanation": [
                f"目标成员归一化质量和={target_quality_sum:.6f}",
                f"兼容性系数={compatibility:.2f}（{compatibility_basis}）",
                f"Tm 均衡性系数={tm_coefficient:.2f}",
                "内标只作为兼容性准入和独立证据，不进入目标质量分子项",
            ],
        }

    @staticmethod
    def _sequence_member_signature(panel: dict[str, Any]) -> str:
        members = sorted(
            [
                {
                    "target_id": entry.get("target_id"),
                    "role": entry.get("role"),
                    "fwd_seq": str(entry.get("fwd_seq") or "").upper(),
                    "rev_seq": str(entry.get("rev_seq") or "").upper(),
                    "probe_seq": str(entry.get("probe_seq") or "").upper(),
                }
                for entry in panel.get("p4_input") or []
            ],
            key=lambda item: (str(item["role"]), str(item["target_id"])),
        )
        return _canonical_sha256(members)

    def _score_deduplicate_and_select(
        self, batch: dict[str, Any]
    ) -> dict[str, Any]:
        multiplex = batch.setdefault("multiplex", {})
        balance = multiplex.get("balance") or {}
        scored: list[dict[str, Any]] = []
        for panel in balance.get("selected_panels") or []:
            item = copy.deepcopy(panel)
            item["score"] = self._score_panel(item)
            item["sequence_signature_sha256"] = self._sequence_member_signature(item)
            item["alternate_channel_assignments"] = []
            scored.append(item)
        scored.sort(
            key=lambda item: (
                -float((item.get("score") or {}).get("final_score") or 0.0),
                0 if item.get("classification") == "STRICT_COMPATIBLE" else 1,
                str(item.get("panel_id") or ""),
            )
        )
        scoring = {
            "status": "COMPLETED",
            "policy_version": WF02_SCORING_POLICY_VERSION,
            "panel_count": len(scored),
            "panels": copy.deepcopy(scored),
            "completed_at": utc_now(),
        }
        multiplex["scoring"] = scoring
        self._record_node(
            batch,
            "N28",
            "COMPLETED",
            f"使用版本化公式完成 {len(scored)} 个面板的可解释评分",
            policy_version=WF02_SCORING_POLICY_VERSION,
            internal_control_in_numerator=False,
        )

        deduplicated: list[dict[str, Any]] = []
        by_signature: dict[str, dict[str, Any]] = {}
        for panel in scored:
            signature = str(panel["sequence_signature_sha256"])
            primary = by_signature.get(signature)
            if primary is None:
                primary = copy.deepcopy(panel)
                by_signature[signature] = primary
                deduplicated.append(primary)
                continue
            variant = {
                "panel_id": panel.get("panel_id"),
                "channel_assignment": copy.deepcopy(
                    panel.get("channel_assignment") or []
                ),
                "score": copy.deepcopy(panel.get("score") or {}),
            }
            primary.setdefault("alternate_channel_assignments", []).append(variant)
        deduplication = {
            "status": "COMPLETED",
            "policy_version": WF02_DEDUP_POLICY_VERSION,
            "input_count": len(scored),
            "unique_count": len(deduplicated),
            "duplicate_count": len(scored) - len(deduplicated),
            "panels": deduplicated,
            "completed_at": utc_now(),
        }
        multiplex["deduplication"] = deduplication
        self._record_node(
            batch,
            "N29",
            "COMPLETED",
            f"按序列成员去重后保留 {len(deduplicated)} 个唯一面板",
            duplicate_count=deduplication["duplicate_count"],
            channel_variants_do_not_occupy_rank=True,
        )

        strict = [
            item
            for item in deduplicated
            if item.get("classification") == "STRICT_COMPATIBLE"
        ]
        risk = [
            item
            for item in deduplicated
            if item.get("classification") != "STRICT_COMPATIBLE"
        ]
        chosen = deduplicated[:WF02_TOP_PANEL_LIMIT]
        top3: list[dict[str, Any]] = []
        for rank, panel in enumerate(chosen, start=1):
            item = copy.deepcopy(panel)
            item["rank"] = rank
            item["tier"] = (
                "STRICT"
                if item.get("classification") == "STRICT_COMPATIBLE"
                else "RISK"
            )
            top3.append(item)
        selected_panel_ids = {
            str(item.get("panel_id")) for item in top3
        }
        selection = {
            "status": "COMPLETED",
            "policy_version": WF02_SELECTION_POLICY_VERSION,
            "maximum_top_panels": WF02_TOP_PANEL_LIMIT,
            "selection_mode": "GLOBAL_SCORE_TOP3",
            "top3": top3,
            "strict_candidate_count": len(strict),
            "risk_candidate_count": len(risk),
            "risk_alternatives": copy.deepcopy(
                [
                    item
                    for item in risk
                    if str(item.get("panel_id")) not in selected_panel_ids
                ][:WF02_TOP_PANEL_LIMIT]
            ),
            "partial_panels_included": False,
            "completed_at": utc_now(),
        }
        multiplex["selection"] = selection
        self._record_node(
            batch,
            "N30",
            "COMPLETED",
            f"按全局评分选出 {len(top3)} 个候选面板",
            selection_mode=selection["selection_mode"],
            partial_panels_included=False,
        )
        return selection

    @staticmethod
    def _candidate_lookup(batch: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
        lookup: dict[tuple[str, str], dict[str, Any]] = {}
        for target in (batch.get("target_design") or {}).get("targets") or []:
            target_id = str(target.get("target_id") or "")
            for candidate in target.get("candidates") or []:
                lookup[(target_id, str(candidate.get("candidate_id") or ""))] = candidate
        for candidate in (batch.get("internal_control") or {}).get("candidates") or []:
            lookup[("internal_control", str(candidate.get("candidate_id") or ""))] = candidate
        return lookup

    @staticmethod
    def _conservation_score(candidate: dict[str, Any]) -> Any:
        if candidate.get("conservation_score") is not None:
            return candidate.get("conservation_score")
        conservation = candidate.get("conservation") or {}
        for key in ("score", "coverage", "coverage_percent", "identity_percent"):
            if conservation.get(key) is not None:
                return conservation.get(key)
        return None

    def _report_panel(
        self,
        panel: dict[str, Any],
        lookup: dict[tuple[str, str], dict[str, Any]],
    ) -> dict[str, Any]:
        members: list[dict[str, Any]] = []
        for entry in panel.get("p4_input") or []:
            target_id = str(entry.get("target_id") or "")
            candidate_id = str(entry.get("candidate_id") or "")
            candidate = lookup.get((target_id, candidate_id)) or {}
            forward = copy.deepcopy(candidate.get("forward_primer") or {})
            reverse = copy.deepcopy(candidate.get("reverse_primer") or {})
            probe = copy.deepcopy(candidate.get("probe") or {})
            forward.setdefault("sequence_5to3", entry.get("fwd_seq"))
            forward.setdefault("tm_celsius", entry.get("fwd_tm"))
            reverse.setdefault("sequence_5to3", entry.get("rev_seq"))
            reverse.setdefault("tm_celsius", entry.get("rev_tm"))
            probe.setdefault("sequence_5to3", entry.get("probe_seq"))
            probe.setdefault("tm_celsius", entry.get("probe_tm"))
            amplicon = copy.deepcopy(candidate.get("amplicon") or {})
            amplicon.setdefault("length_bp", entry.get("amplicon_length_bp"))
            amplicon.setdefault("gc_percent", entry.get("gc_percent"))
            channel = next(
                (
                    item
                    for item in panel.get("channel_assignment") or []
                    if str(item.get("target_id")) == target_id
                ),
                {},
            )
            fluorophore = entry.get("fluorophore") or channel.get(
                "fluorophore"
            )
            quencher = (
                entry.get("quencher")
                or channel.get("quencher")
                or probe.get("quencher")
                or channel.get("recommended_quencher")
            )
            probe["fluorophore"] = fluorophore
            probe["quencher"] = quencher
            members.append(
                {
                    "target_id": target_id,
                    "role": entry.get("role"),
                    "candidate_id": candidate_id,
                    "fluorophore": fluorophore,
                    "quencher": quencher,
                    "fluorophore_source": entry.get("fluorophore_source")
                    or "WF02_CHANNEL_ASSIGNMENT",
                    "quencher_source": entry.get("quencher_source")
                    or channel.get("quencher_source"),
                    "forward_primer": forward,
                    "reverse_primer": reverse,
                    "probe": probe,
                    "amplicon": amplicon,
                    "specificity": copy.deepcopy(candidate.get("specificity") or {}),
                    "blast_summary": copy.deepcopy(
                        (candidate.get("specificity") or {}).get("summary")
                        or (candidate.get("specificity") or {}).get("blast_summary")
                        or {}
                    ),
                    "conservation_score": self._conservation_score(candidate),
                    "evidence_level": candidate.get("evidence_level")
                    or entry.get("evidence_level"),
                    "amplicon_structure": copy.deepcopy(
                        candidate.get("amplicon_structure") or {}
                    ),
                    "warnings": copy.deepcopy(candidate.get("warnings") or []),
                }
            )
        warnings = list(panel.get("warnings") or [])
        warnings.extend((panel.get("balance") or {}).get("warnings") or [])
        warnings.extend(panel.get("risk_reasons") or [])
        primer_tms = [
            float(entry[key])
            for entry in panel.get("p4_input") or []
            for key in ("fwd_tm", "rev_tm")
            if entry.get(key) is not None
        ]
        recommended_annealing = (
            round(statistics.median(primer_tms), 2)
            if primer_tms
            else None
        )
        return {
            "rank": panel.get("rank"),
            "panel_id": panel.get("panel_id"),
            "tier": panel.get("tier"),
            "classification": panel.get("classification"),
            "score": copy.deepcopy(panel.get("score") or {}),
            "sequence_signature_sha256": panel.get("sequence_signature_sha256"),
            "members": members,
            "channel_assignment": copy.deepcopy(panel.get("channel_assignment") or []),
            "alternate_channel_assignments": copy.deepcopy(
                panel.get("alternate_channel_assignments") or []
            ),
            "balance": copy.deepcopy(panel.get("balance") or {}),
            "p4_result": copy.deepcopy(panel.get("p4_result") or {}),
            "cross_dimer_evidence": copy.deepcopy(
                (panel.get("p4_result") or {}).get("heterodimer") or {}
            ),
            "amplicon_length_check": copy.deepcopy(
                panel.get("amplicon_length_check") or {}
            ),
            "relaxation_applied": copy.deepcopy(
                panel.get("relaxation_applied") or []
            ),
            "recommended_annealing_temp_celsius": recommended_annealing,
            "annealing_temperature_basis": "PANEL_PRIMER_TM_MEDIAN",
            "recommendation_policy_version": "wf02-panel-temperature-v2",
            "warnings": list(dict.fromkeys(str(item) for item in warnings if item)),
        }

    @staticmethod
    def _report_source(batch: dict[str, Any]) -> dict[str, Any]:
        return {
            "batch_id": batch.get("batch_id"),
            "batch_code": batch.get("batch_code"),
            "task_version": batch.get("task_version"),
            "source_batch_id": batch.get("source_batch_id"),
            "source_version": batch.get("source_version"),
            "lineage_root_batch_id": batch.get("lineage_root_batch_id"),
        }

    @staticmethod
    def _report_internal_control_summary(
        batch: dict[str, Any],
    ) -> dict[str, Any]:
        internal = batch.get("internal_control") or {}
        internal_requested = bool(
            internal.get(
                "requested",
                (batch.get("user_input_snapshot") or {}).get(
                    "include_internal_control"
                ),
            )
        )
        internal_included = bool(internal.get("combination_pool_member"))
        internal_unavailable = internal_requested and not internal_included
        internal_statement = (
            "用户请求了内标，但本次未获得可用内标；后续结果未将内标计入组合。"
            if internal_unavailable
            else (
                "内标已纳入组合并完成当前阶段验证。"
                if internal_included
                else "用户未请求内标。"
            )
        )
        return {
            "requested": internal_requested,
            "included_in_panel": internal_included,
            "status": internal.get("status", "NOT_STARTED"),
            "p5_status": internal.get("p5_status"),
            "p5_verdict": internal.get("p5_verdict"),
            "failure": copy.deepcopy(internal.get("failure")),
            "channel_reservation": copy.deepcopy(
                internal.get("channel_reservation") or {}
            ),
            "statement": internal_statement,
            "follow_up_actions": copy.deepcopy(
                internal.get("follow_up_actions") or []
            ),
        }

    @staticmethod
    def _report_traceability(batch: dict[str, Any]) -> dict[str, Any]:
        target_design = batch.get("target_design") or {}
        multiplex = batch.get("multiplex") or {}
        screening = multiplex.get("screening") or {}
        relaxation = multiplex.get("relaxation") or {}
        internal = batch.get("internal_control") or {}
        target_executions = [
            {
                "tool": "WF-01",
                "target_id": item.get("target_id"),
                "status": item.get("status"),
                "active_batch_id": item.get("active_wf01_batch_id")
                or item.get("wf01_batch_id"),
                "lineage": copy.deepcopy(item.get("wf01_lineage") or []),
                "input_source": copy.deepcopy(
                    next(
                        (
                            target.get("template_source")
                            for target in (
                                batch.get("user_input_snapshot") or {}
                            ).get("targets")
                            or []
                            if str(target.get("target_id"))
                            == str(item.get("target_id"))
                        ),
                        None,
                    )
                ),
                "failure": copy.deepcopy(item.get("failure")),
            }
            for item in target_design.get("targets") or []
        ]
        return {
            "execution_route": {
                "workflow": "WF-02",
                "nodes": copy.deepcopy(WF02_NODES),
                "terminal_stage": batch.get("stage"),
                "terminal_status": batch.get("status"),
            },
            "dependency_contracts": copy.deepcopy(
                batch.get("dependency_contracts")
                or {
                    "wf01_candidates": wf01_candidate_contract_manifest(),
                }
            ),
            "workflow_trace": copy.deepcopy(batch.get("workflow_trace") or []),
            "events": copy.deepcopy(batch.get("events") or []),
            "target_lineage": copy.deepcopy(target_executions),
            "tool_executions": {
                "wf01_targets": target_executions,
                "p6": copy.deepcopy(target_design.get("p6_summary") or {}),
                "p5": {
                    "status": internal.get("p5_status")
                    or internal.get("status"),
                    "verdict": internal.get("p5_verdict"),
                    "attempt_count": internal.get("attempt_count"),
                    "failure": copy.deepcopy(internal.get("failure")),
                },
                "p4": {
                    "status": screening.get("status"),
                    "processed_combinations": screening.get("cursor"),
                    "total_combinations": screening.get("total_combinations"),
                    "tool_failure": copy.deepcopy(screening.get("tool_failure")),
                },
            },
            "relaxation": {
                "status": relaxation.get("status"),
                "active_step": relaxation.get("active_step"),
                "log": copy.deepcopy(relaxation.get("relaxation_log") or []),
            },
            "instrument_configuration": copy.deepcopy(
                batch.get("runtime_config_snapshot") or {}
            ),
            "channel_assignment": {
                "owner": "WF-02",
                "validation_owner": "P4",
                "policy_version": screening.get("channel_policy_version"),
                "planned": copy.deepcopy(
                    screening.get("channel_assignment") or []
                ),
            },
            "uncompleted_reasons": {
                "outcome": copy.deepcopy(batch.get("outcome") or {}),
                "coverage_gaps": copy.deepcopy(
                    target_design.get("coverage_gaps") or []
                ),
            },
        }

    @staticmethod
    def _canonical_multiplex_report(
        batch: dict[str, Any],
        panels: list[dict[str, Any]],
    ) -> dict[str, Any]:
        target_design = batch.get("target_design") or {}
        multiplex = batch.get("multiplex") or {}
        primary = panels[0] if panels else {}
        p4_result = primary.get("p4_result") or {}
        heterodimer = p4_result.get("heterodimer") or {}
        requested_panel_size = int(
            multiplex.get("panel_size")
            or target_design.get("requested_targets")
            or 0
        )
        primary_members = list(primary.get("members") or [])
        worst_dg_cal_per_mol = heterodimer.get("worst_dg_cal_per_mol")
        if (
            worst_dg_cal_per_mol is None
            and heterodimer.get("worst_dg_kcal") is not None
        ):
            worst_dg_cal_per_mol = round(
                float(heterodimer["worst_dg_kcal"]) * 1000.0,
                1,
            )
        return {
            "status": batch.get("status"),
            # O2 panel_size is the requested target count. An optional
            # auxiliary member is reported separately and never changes it.
            "panel_size": requested_panel_size,
            "requested_panel_size": requested_panel_size,
            "delivered_target_count": sum(
                1 for item in primary_members if item.get("role") == "target"
            ),
            "delivered_member_count": len(primary_members),
            "internal_control_included": any(
                item.get("role") == "internal_control"
                for item in primary_members
            ),
            "targets_order": copy.deepcopy(
                multiplex.get("targets_order")
                or [
                    item.get("target_id")
                    for item in target_design.get("targets") or []
                ]
            ),
            "failed_targets": copy.deepcopy(
                target_design.get("failed_target_ids") or []
            ),
            "blocked_targets": copy.deepcopy(
                target_design.get("blocked_target_ids") or []
            ),
            "coverage": copy.deepcopy(target_design.get("coverage") or {}),
            "coverage_gaps": copy.deepcopy(
                target_design.get("coverage_gaps") or []
            ),
            "primary_panel_id": primary.get("panel_id"),
            "delivered_panel_ids": [
                item.get("panel_id") for item in panels
            ],
            "relaxation_applied": bool(
                primary.get("relaxation_applied")
                or ((multiplex.get("relaxation") or {}).get("status")
                    in {"COMPLETED_RISK_FULL", "COMPLETED_PARTIAL"})
            ),
            "channel_assignment": copy.deepcopy(
                primary.get("channel_assignment") or []
            ),
            "heterodimer": {
                "worst_dg_cal_per_mol": worst_dg_cal_per_mol,
                "worst_pair": copy.deepcopy(
                    heterodimer.get("worst_pair") or []
                ),
                "total_pairs_checked": heterodimer.get("evaluated_pairs"),
            },
            "tm_spread_celsius": copy.deepcopy(
                p4_result.get("tm_spread_celsius") or {}
            ),
            "gc_spread_percent": p4_result.get("gc_spread_percent"),
            "issues": copy.deepcopy(p4_result.get("issues") or []),
            "verdict": p4_result.get("verdict")
            or (batch.get("outcome") or {}).get("panel_status")
            or "NOT_EVALUATED",
        }

    @staticmethod
    def _report_postprocessing(
        report: dict[str, Any],
    ) -> dict[str, Any]:
        panels = list(report.get("top3_panels") or []) or list(
            report.get("partial_panels") or []
        )
        rows = [
            {
                "rank": panel.get("rank"),
                "panel_id": panel.get("panel_id"),
                "tier": panel.get("tier"),
                "classification": panel.get("classification"),
                "final_score": (panel.get("score") or {}).get("final_score"),
                "member_count": len(panel.get("members") or []),
                "warning_count": len(panel.get("warnings") or []),
            }
            for panel in panels
        ]
        header = (
            "| Rank | Panel | Tier | Classification | Score | Members | Warnings |\n"
            "|---:|---|---|---|---:|---:|---:|"
        )
        markdown_rows = [
            "| {rank} | {panel_id} | {tier} | {classification} | {score} | {members} | {warnings} |".format(
                rank=row.get("rank") or "-",
                panel_id=row.get("panel_id") or "-",
                tier=row.get("tier") or "-",
                classification=row.get("classification") or "-",
                score=(
                    row.get("final_score")
                    if row.get("final_score") is not None
                    else "-"
                ),
                members=row.get("member_count"),
                warnings=row.get("warning_count"),
            )
            for row in rows
        ]
        summary = report.get("result_summary") or {}
        outcome = report.get("outcome") or {}
        selected_panel_id = rows[0]["panel_id"] if rows else None
        reason_codes = [str(item) for item in outcome.get("reason_codes") or []]
        explanation_text = (
            f"WF-02 终态为 {summary.get('workflow_status') or summary.get('status')}；"
            f"选择模式为 {report.get('selection_mode')}；"
            f"交付面板数为 {len(rows)}；"
            f"首选面板为 {selected_panel_id or 'NONE'}；"
            f"主失败阶段为 {outcome.get('primary_failure_stage') or 'NONE'}；"
            f"原因码为 {', '.join(reason_codes) if reason_codes else 'NONE'}。"
        )
        return {
            "candidate_comparison": {
                "component": "C29",
                "status": "COMPLETED" if rows else "NOT_APPLICABLE",
                "renderer": "DETERMINISTIC_MARKDOWN_V1",
                "rows": rows,
                "markdown": "\n".join([header, *markdown_rows]),
            },
            "result_explanation": {
                "component": "C30",
                "status": "COMPLETED",
                "renderer": "DETERMINISTIC_STRUCTURED_TEXT_V1",
                "text": explanation_text,
                "facts": {
                    "workflow_status": summary.get("workflow_status")
                    or summary.get("status"),
                    "selection_mode": report.get("selection_mode"),
                    "delivered_panel_count": len(rows),
                    "selected_panel_id": selected_panel_id,
                    "primary_failure_stage": outcome.get(
                        "primary_failure_stage"
                    ),
                    "reason_codes": copy.deepcopy(
                        reason_codes
                    ),
                },
            },
        }

    @staticmethod
    def _export_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _publish_export_metadata(
        self,
        batch_id: str,
        format_name: str,
        path: Path,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Publish exactly one WF-02 terminal export to configured RustFS."""
        publication = self.artifact_publisher.publish(
            path,
            batch_id=batch_id,
            media_type=WF02_EXPORT_MEDIA_TYPES[format_name],
            sha256=str(metadata["sha256"]),
            object_name=f"wf02-{batch_id}-multiplex-report{path.suffix}",
        )
        if publication is None:
            return metadata
        publication = copy.deepcopy(publication)
        publication["renderer_version"] = metadata["renderer_version"]
        published = copy.deepcopy(metadata)
        published["object_storage"] = publication
        published["file_url"] = publication["file_url"]
        return published

    @staticmethod
    def _render_export(
        report: dict[str, Any],
        format_name: str,
    ) -> bytes:
        renderers = {
            "html": render_wf02_html,
            "csv": render_wf02_csv,
            "pdf": render_wf02_pdf,
        }
        return renderers[format_name](report)

    def _ensure_export_available(
        self,
        batch: dict[str, Any],
        format_name: str,
    ) -> tuple[dict[str, Any], Path, dict[str, Any]]:
        """Render and publish exactly one requested export when necessary."""
        selected = str(format_name).lower()
        if selected not in WF02_EXPORT_MEDIA_TYPES:
            raise InputValidationError(
                "WF-02 导出格式无效",
                details={
                    "format": format_name,
                    "supported": sorted(WF02_EXPORT_MEDIA_TYPES),
                },
            )
        if not batch.get("report_id"):
            raise InputValidationError(
                "WF-02 报告尚未生成",
                details={"batch_id": batch.get("batch_id")},
            )

        batch_id = str(batch["batch_id"])
        exports = copy.deepcopy(batch.get("report_exports") or {})
        metadata = exports.get(selected)
        if not isinstance(metadata, dict):
            metadata = {}
        expected_version = WF02_EXPORT_RENDERER_VERSIONS[selected]
        metadata_is_current = (
            metadata.get("renderer_version") == expected_version
        )
        publication = metadata.get("object_storage") or {}
        file_url = str(metadata.get("file_url") or "")
        has_public_object = (
            publication.get("provider") == "rustfs"
            and file_url.startswith(("http://", "https://"))
        )
        published_version = publication.get("renderer_version")
        published_is_current = has_public_object and (
            published_version == expected_version
            or (
                selected == "csv"
                and published_version in {None, ""}
            )
        )

        try:
            path = self.store.get_export_path(batch_id, selected)
        except InputValidationError:
            path = None
        should_render = path is None or not metadata_is_current
        if should_render:
            report = self.store.get_report(batch_id)
            path = self.store.save_export(
                batch_id,
                selected,
                self._render_export(report, selected),
            )

        download_url = f"/v1/wf02/tasks/{batch_id}/exports/{selected}"
        prepared = copy.deepcopy(metadata)
        prepared.update(
            {
                "format": selected,
                "filename": path.name,
                "media_type": WF02_EXPORT_MEDIA_TYPES[selected],
                "renderer_version": expected_version,
                "size_bytes": path.stat().st_size,
                "sha256": self._export_sha256(path),
                "download_url": download_url,
                "file_url": download_url,
                "preview_url": (
                    f"{download_url}/preview" if selected == "html" else None
                ),
            }
        )
        if published_is_current and not should_render:
            current_publication = copy.deepcopy(publication)
            current_publication["renderer_version"] = expected_version
            prepared["object_storage"] = current_publication
            prepared["file_url"] = file_url
        elif self.artifact_publisher.enabled:
            prepared = self._publish_export_metadata(
                batch_id,
                selected,
                path,
                prepared,
            )
        else:
            prepared.pop("object_storage", None)

        if prepared != metadata:
            exports[selected] = prepared
            report = self.store.get_report(batch_id)
            report["exports"] = copy.deepcopy(exports)
            self.store.save_report(report)
            batch["report_exports"] = copy.deepcopy(exports)
            batch["updated_at"] = utc_now()
            self.store.save_batch(batch)
        return batch, path, prepared

    def _ensure_exports_published(
        self,
        batch: dict[str, Any],
    ) -> dict[str, Any]:
        """Keep the default HTML attachment current; defer other formats."""
        if not batch.get("report_id"):
            return batch
        batch, _, _ = self._ensure_export_available(batch, "html")
        return batch

    def _persist_report_and_exports(
        self,
        batch: dict[str, Any],
        report: dict[str, Any],
    ) -> dict[str, Any]:
        self.store.save_report(report)
        batch_id = str(batch["batch_id"])
        path = self.store.save_export(
            batch_id,
            "html",
            render_wf02_html(report),
        )
        download_url = f"/v1/wf02/tasks/{batch_id}/exports/html"
        metadata = {
            "format": "html",
            "filename": path.name,
            "media_type": WF02_EXPORT_MEDIA_TYPES["html"],
            "renderer_version": WF02_EXPORT_RENDERER_VERSIONS["html"],
            "size_bytes": path.stat().st_size,
            "sha256": self._export_sha256(path),
            "download_url": download_url,
            "file_url": download_url,
            "preview_url": f"{download_url}/preview",
        }
        exports = {
            "html": self._publish_export_metadata(
                batch_id,
                "html",
                path,
                metadata,
            )
        }
        report["exports"] = exports
        self.store.save_report(report)
        return report

    def _build_and_persist_terminal_report(
        self,
        batch: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist a queryable report for partial and failed terminal states."""
        lookup = self._candidate_lookup(batch)
        relaxation = (batch.get("multiplex") or {}).get("relaxation") or {}
        partial_panels = [
            self._report_panel(
                {
                    **copy.deepcopy(panel),
                    "rank": rank,
                    "tier": "PARTIAL",
                },
                lookup,
            )
            for rank, panel in enumerate(
                (relaxation.get("partial_panels") or [])[
                    :WF02_TOP_PANEL_LIMIT
                ],
                start=1,
            )
        ]
        is_partial = batch.get("status") == "PARTIAL_PANEL_AVAILABLE"
        selection_mode = (
            "PARTIAL_ONLY"
            if is_partial
            else (
                "INCOMPLETE_TARGET_COVERAGE"
                if batch.get("status") == "FAILED_INCOMPLETE_TARGET_COVERAGE"
                else "NO_COMPATIBLE_PANEL"
            )
        )
        result_status = "PARTIAL" if is_partial else "FAIL"
        internal_summary = self._report_internal_control_summary(batch)
        target_design = batch.get("target_design") or {}
        report_id = str(uuid5(NAMESPACE_URL, f"wf02-report:{batch['batch_id']}"))
        report = {
            "schema_version": WF02_REPORT_SCHEMA_VERSION,
            "report_id": report_id,
            "workflow": "WF-02",
            "workflow_version": batch.get("workflow_version"),
            "policy_version": batch.get("policy_version"),
            "generated_at": utc_now(),
            "source": self._report_source(batch),
            "input_snapshot": copy.deepcopy(batch.get("user_input_snapshot") or {}),
            "runtime_config_snapshot": copy.deepcopy(
                batch.get("runtime_config_snapshot") or {}
            ),
            "version_diff": copy.deepcopy(batch.get("version_diff") or {}),
            "result_summary": {
                "status": result_status,
                "primary_tier": "PARTIAL" if is_partial else None,
                "top3_count": 0,
                "strict_candidate_count": 0,
                "risk_candidate_count": 0,
                "partial_panel_count": len(partial_panels),
                "partial_panel_total": len(
                    relaxation.get("partial_panels") or []
                ),
                "workflow_status": batch.get("status"),
                "coverage_complete": bool(
                    (target_design.get("coverage") or {}).get("complete")
                ),
                "internal_control_requested": internal_summary["requested"],
                "internal_control_included": internal_summary["included_in_panel"],
            },
            "outcome": copy.deepcopy(batch.get("outcome") or {}),
            "internal_control_summary": internal_summary,
            "balance_policy_version": WF02_BALANCE_POLICY_VERSION,
            "scoring_policy_version": WF02_SCORING_POLICY_VERSION,
            "deduplication_policy_version": WF02_DEDUP_POLICY_VERSION,
            "selection_policy_version": WF02_SELECTION_POLICY_VERSION,
            "selection_mode": selection_mode,
            "top3_panels": [],
            "risk_alternatives": [],
            "partial_panels": partial_panels,
            "partial_panels_included": bool(partial_panels),
            "independent_target_schemes": copy.deepcopy(
                relaxation.get("independent_target_schemes") or []
            ),
            "failure_guidance": copy.deepcopy(
                relaxation.get("failure_suggestions") or []
            ),
            "multiplex": self._canonical_multiplex_report(
                batch, partial_panels
            ),
            "traceability": self._report_traceability(batch),
            "experimental_validation_statement": (
                "本报告记录当前工作流的计算结果和未完成原因；终态不是完整通过状态。"
            ),
            "evidence_statement": (
                "报告仅映射已实际执行的工具状态；未执行项保留原始状态且不计为通过。"
            ),
            "available_actions": [
                "PREVIEW",
                "DOWNLOAD_HTML",
                "DOWNLOAD_CSV",
                "DOWNLOAD_PDF",
                "CREATE_NEW_VERSION",
            ],
        }
        report.update(self._report_postprocessing(report))
        return self._persist_report_and_exports(batch, report)

    def _build_and_persist_report(
        self,
        batch: dict[str, Any],
        selection: dict[str, Any],
    ) -> dict[str, Any]:
        lookup = self._candidate_lookup(batch)
        top3 = [self._report_panel(item, lookup) for item in selection.get("top3") or []]
        internal = batch.get("internal_control") or {}
        internal_requested = bool(
            internal.get(
                "requested",
                (batch.get("user_input_snapshot") or {}).get(
                    "include_internal_control"
                ),
            )
        )
        internal_included = bool(internal.get("combination_pool_member"))
        internal_unavailable = internal_requested and not internal_included
        internal_statement = (
            "用户请求了内标，但本次未获得可用内标；目标面板已按专家规则继续组装。"
            if internal_unavailable
            else (
                "内标已纳入面板并由 P4 完成通道与交叉兼容性验证。"
                if internal_included
                else "用户未请求内标。"
            )
        )
        internal_summary = {
            "requested": internal_requested,
            "included_in_panel": internal_included,
            "status": internal.get("status", "NOT_STARTED"),
            "p5_status": internal.get("p5_status"),
            "p5_verdict": internal.get("p5_verdict"),
            "failure": copy.deepcopy(internal.get("failure")),
            "channel_reservation": copy.deepcopy(
                internal.get("channel_reservation") or {}
            ),
            "statement": internal_statement,
            "follow_up_actions": copy.deepcopy(
                internal.get("follow_up_actions") or []
            ),
        }
        source = {
            "batch_id": batch.get("batch_id"),
            "batch_code": batch.get("batch_code"),
            "task_version": batch.get("task_version"),
            "source_batch_id": batch.get("source_batch_id"),
            "source_version": batch.get("source_version"),
            "lineage_root_batch_id": batch.get("lineage_root_batch_id"),
        }
        report_id = str(uuid5(NAMESPACE_URL, f"wf02-report:{batch['batch_id']}"))
        report = {
            "schema_version": WF02_REPORT_SCHEMA_VERSION,
            "report_id": report_id,
            "workflow": "WF-02",
            "workflow_version": batch.get("workflow_version"),
            "policy_version": batch.get("policy_version"),
            "generated_at": utc_now(),
            "source": source,
            "input_snapshot": copy.deepcopy(batch.get("user_input_snapshot") or {}),
            "runtime_config_snapshot": copy.deepcopy(
                batch.get("runtime_config_snapshot") or {}
            ),
            "version_diff": copy.deepcopy(batch.get("version_diff") or {}),
            "result_summary": {
                "status": (
                    "PASS_WITH_WARNING"
                    if internal_unavailable
                    or any(item.get("warnings") for item in top3)
                    else "PASS"
                ),
                "primary_tier": (
                    (top3[0].get("tier") if top3 else None)
                ),
                "top3_count": len(top3),
                "strict_candidate_count": selection.get("strict_candidate_count"),
                "risk_candidate_count": selection.get("risk_candidate_count"),
                "internal_control_requested": internal_requested,
                "internal_control_included": internal_included,
                "workflow_status": "COMPLETED",
            },
            "outcome": {
                "workflow_status": "COMPLETED",
                "primary_failure_stage": None,
                "coverage_status": copy.deepcopy(
                    (batch.get("target_design") or {}).get("coverage") or {}
                ),
                "panel_status": "COMPLETE_PANEL_AVAILABLE",
                "reason_codes": [],
            },
            "internal_control_summary": internal_summary,
            "balance_policy_version": WF02_BALANCE_POLICY_VERSION,
            "scoring_policy_version": WF02_SCORING_POLICY_VERSION,
            "deduplication_policy_version": WF02_DEDUP_POLICY_VERSION,
            "selection_policy_version": selection.get("policy_version"),
            "selection_mode": selection.get("selection_mode"),
            "top3_panels": top3,
            "risk_alternatives": [
                self._report_panel({**copy.deepcopy(item), "rank": None, "tier": "RISK"}, lookup)
                for item in selection.get("risk_alternatives") or []
            ],
            "partial_panels": [],
            "partial_panels_included": False,
            "multiplex": self._canonical_multiplex_report(
                {**batch, "status": "COMPLETED"}, top3
            ),
            "traceability": self._report_traceability(batch),
            "experimental_validation_statement": (
                "本报告为计算与数据库证据支持的体外设计建议；所有面板均须通过实际仪器、"
                "试剂体系和样本条件下的实验验证后方可用于诊断。"
            ),
            "evidence_statement": (
                "序列与单候选证据来自 WF-01/P5/P6；交叉二聚体、光谱、淬灭与仪器证据来自 P4；"
                "评分、分层和报告为版本化 WF-02 项目策略。"
            ),
            "available_actions": ["PREVIEW", "DOWNLOAD_HTML", "DOWNLOAD_CSV", "DOWNLOAD_PDF", "CREATE_NEW_VERSION"],
        }
        report.update(self._report_postprocessing(report))
        return self._persist_report_and_exports(batch, report)

    def execute_scoring_and_reporting(self, batch_id: str) -> dict[str, Any]:
        """Execute N28-N32 and persist the immutable report and exports."""
        batch = self.store.get_batch(batch_id)
        if batch.get("status") == "COMPLETED" and batch.get("report_id"):
            return batch
        if (batch.get("multiplex") or {}).get("balance", {}).get("status") != "COMPLETED":
            raise InputValidationError("WF-02 必须先完成 N27 才能评分和报告")
        selection = self._score_deduplicate_and_select(batch)
        report_id = str(
            uuid5(NAMESPACE_URL, f"wf02-report:{batch['batch_id']}")
        )
        top3_count = len(selection.get("top3") or [])
        self._record_node(
            batch,
            "N31",
            "COMPLETED",
            f"已生成包含 {top3_count} 套候选的完整多重面板报告",
            report_id=report_id,
            export_formats=["html", "csv", "pdf"],
        )
        self._record_node(
            batch,
            "N32",
            "COMPLETED",
            "报告可预览、下载或创建不可变的新设计版本",
            version_overwrites_source=False,
            user_input_and_runtime_diff_separated=True,
        )
        batch.update(
            {
                "status": "COMPLETED",
                "stage": "N32",
                "next_action": "COMPLETE",
                "report_id": report_id,
                "completed_at": utc_now(),
                "outcome": {
                    "workflow_status": "COMPLETED",
                    "primary_failure_stage": None,
                    "coverage_status": copy.deepcopy(
                        (batch.get("target_design") or {}).get("coverage")
                        or {}
                    ),
                    "panel_status": "COMPLETE_PANEL_AVAILABLE",
                    "reason_codes": [],
                },
            }
        )
        self._append_event(
            batch,
            "WF02_REPORT_COMPLETED",
            status="COMPLETED",
            report_id=report_id,
            top3_count=top3_count,
        )
        report = self._build_and_persist_report(batch, selection)
        batch.update(
            {
                "report_summary": copy.deepcopy(
                    report.get("result_summary") or {}
                ),
                "report_exports": copy.deepcopy(report.get("exports") or {}),
            }
        )
        return self._save_batch(batch)

    def get_report(self, batch_id: str) -> dict[str, Any]:
        self.get_batch(batch_id)
        return self.store.get_report(batch_id)

    def get_panel(self, batch_id: str, panel_id: str) -> dict[str, Any]:
        report = self.get_report(batch_id)
        panel = next(
            (
                item
                for item in (report.get("top3_panels") or [])
                + (report.get("partial_panels") or [])
                + (report.get("risk_alternatives") or [])
                if str(item.get("panel_id")) == str(panel_id)
            ),
            None,
        )
        if panel is None:
            raise InputValidationError(
                "WF-02 报告中不存在该面板",
                details={"batch_id": batch_id, "panel_id": panel_id},
            )
        return {
            "schema_version": "wf02-v0.1-panel-detail-v1",
            "batch_id": batch_id,
            "report_id": report.get("report_id"),
            "panel": panel,
        }

    def get_export_path(self, batch_id: str, format_name: str) -> Path:
        with self._batch_lock(batch_id):
            batch = self.store.get_batch(batch_id)
            _, path, _ = self._ensure_export_available(
                batch,
                format_name,
            )
            return path

    def get_export_metadata(self, batch_id: str, format_name: str) -> dict[str, Any]:
        with self._batch_lock(batch_id):
            batch = self.store.get_batch(batch_id)
            _, _, metadata = self._ensure_export_available(
                batch,
                format_name,
            )
            return copy.deepcopy(metadata)

    @staticmethod
    def _version_field_diff(before: Any, after: Any, path: str) -> dict[str, Any] | None:
        if _canonical_sha256(before) == _canonical_sha256(after):
            return None
        if path == "targets":
            return {
                "path": path,
                "before_sha256": _canonical_sha256(before),
                "after_sha256": _canonical_sha256(after),
            }
        return {"path": path, "before": copy.deepcopy(before), "after": copy.deepcopy(after)}

    def create_version(
        self,
        batch_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        source = self.store.get_batch(batch_id)
        if source.get("status") not in {
            "COMPLETED",
            "PARTIAL_PANEL_AVAILABLE",
            "FAILED_INCOMPLETE_TARGET_COVERAGE",
            "FAILED_NO_COMPATIBLE_PANEL",
        } or not source.get("report_id"):
            raise InputValidationError("只有已生成终态报告的 WF-02 任务才能创建新版本")
        options = copy.deepcopy(options or {})
        unknown = sorted(
            set(options) - {"reason", "user_input_changes", "runtime_config_changes"}
        )
        if unknown:
            raise InputValidationError(
                "WF-02 版本请求包含未知字段", details={"unknown_fields": unknown}
            )
        user_changes = options.get("user_input_changes") or {}
        runtime_changes = options.get("runtime_config_changes") or {}
        if not isinstance(user_changes, dict) or not isinstance(runtime_changes, dict):
            raise InputValidationError("版本变更必须为对象")
        unknown_user = sorted(
            set(user_changes) - {"targets", "host_species", "include_internal_control"}
        )
        unknown_runtime = sorted(set(runtime_changes) - {"instrument", "rox_as_reference"})
        if unknown_user or unknown_runtime:
            raise InputValidationError(
                "WF-02 版本变更字段无效",
                details={
                    "unknown_user_input_fields": unknown_user,
                    "unknown_runtime_fields": unknown_runtime,
                },
            )
        source_snapshot = source.get("user_input_snapshot") or {}
        source_runtime = source.get("runtime_config_snapshot") or {}
        targets = [
            copy.deepcopy(item.get("source_input"))
            for item in source_snapshot.get("targets") or []
        ]
        payload: dict[str, Any] = {
            "task_name": f"{source.get('task_name') or 'WF-02'} 新版本",
            "targets": copy.deepcopy(user_changes.get("targets", targets)),
            "host_species": user_changes.get(
                "host_species", source_snapshot.get("host_species")
            ),
            "include_internal_control": user_changes.get(
                "include_internal_control",
                source_snapshot.get("include_internal_control"),
            ),
            "runtime_config": {
                "instrument": runtime_changes.get(
                    "instrument", source_runtime.get("instrument")
                ),
                "rox_as_reference": runtime_changes.get(
                    "rox_as_reference", source_runtime.get("rox_as_reference")
                ),
            },
            **copy.deepcopy(source.get("execution_options_snapshot") or {}),
        }
        preflight = self.preflight(payload)
        if not preflight.get("can_create_task"):
            raise InputValidationError(
                "WF-02 新版本输入存在阻塞项",
                details={"issues": copy.deepcopy(preflight.get("issues") or [])},
            )
        before_user = copy.deepcopy(source_snapshot)
        after_user = copy.deepcopy(preflight.get("user_input_snapshot") or {})
        before_runtime = copy.deepcopy(source_runtime)
        after_runtime = copy.deepcopy(preflight.get("runtime_config_snapshot") or {})
        user_diff = [
            item
            for path in ("targets", "host_species", "include_internal_control")
            if (
                item := self._version_field_diff(
                    before_user.get(path), after_user.get(path), path
                )
            )
        ]
        runtime_diff = [
            item
            for path in ("instrument", "rox_as_reference")
            if (
                item := self._version_field_diff(
                    before_runtime.get(path), after_runtime.get(path), path
                )
            )
        ]
        if not user_diff and not runtime_diff:
            raise InputValidationError("WF-02 新版本未产生有效输入或运行配置差异")
        new_batch = self._create_task(preflight, persist=False)
        lineage_root = str(source.get("lineage_root_batch_id") or source["batch_id"])
        versions = [
            int(item.get("task_version") or 1)
            for item in self.store.iter_batches()
            if str(item.get("lineage_root_batch_id") or item.get("batch_id"))
            == lineage_root
        ]
        new_batch.update(
            {
                "task_version": max(versions or [int(source.get("task_version") or 1)]) + 1,
                "source_batch_id": source.get("batch_id"),
                "source_version": source.get("task_version"),
                "lineage_root_batch_id": lineage_root,
                "version_reason": options.get("reason") or "基于已完成 WF-02 报告再次设计",
                "version_diff": {
                    "user_input_changes": user_diff,
                    "runtime_config_changes": runtime_diff,
                },
            }
        )
        self._append_event(
            new_batch,
            "WF02_VERSION_CREATED",
            status=new_batch["status"],
            source_batch_id=source.get("batch_id"),
            source_version=source.get("task_version"),
            task_version=new_batch["task_version"],
            user_input_change_count=len(user_diff),
            runtime_config_change_count=len(runtime_diff),
        )
        self.store.save_batch(new_batch)
        return new_batch

    def execute_stage6(self, batch_id: str) -> dict[str, Any]:
        batch = self.store.get_batch(batch_id)
        if batch.get("status") == "COMPLETED":
            return batch
        balance = (batch.get("multiplex") or {}).get("balance") or {}
        if balance.get("status") != "COMPLETED":
            batch = self.execute_balance_assessment(batch_id)
        if batch.get("status") == "READY_FOR_SCORING":
            batch = self.execute_scoring_and_reporting(batch_id)
        return batch

    def submit_child_response(
        self,
        batch_id: str,
        target_id: str,
        response: dict[str, Any],
        *,
        bounded: bool = False,
        auto_resume: bool = True,
    ) -> dict[str, Any]:
        """Resolve a local template request or proxy a WF-01 interaction."""
        batch = self.store.get_batch(batch_id)
        gateway = self._require_gateway(batch)
        target_result = next(
            (
                item
                for item in ((batch.get("target_design") or {}).get("targets") or [])
                if item.get("target_id") == target_id
            ),
            None,
        )
        if target_result is None:
            raise InputValidationError(
                "WF-02 任务中不存在该靶标",
                details={"batch_id": batch_id, "target_id": target_id},
            )
        child_batch_id = target_result.get("active_wf01_batch_id")
        if child_batch_id:
            gateway.submit_wf01_v02_interaction_response(
                str(child_batch_id), response
            )
        else:
            ui_message = target_result.get("child_ui_message") or {}
            expected_id = ui_message.get("interactionId")
            if response.get("type") != "agent_interaction_response":
                raise InputValidationError(
                    "子任务交互回复 type 必须为 agent_interaction_response"
                )
            if response.get("task") != "MULTIPLEX_PANEL":
                raise InputValidationError(
                    "本地靶标交互回复 task 必须为 MULTIPLEX_PANEL"
                )
            submitted_item = response
            submitted_items = response.get("interactions")
            if submitted_items is not None:
                if (
                    not isinstance(submitted_items, list)
                    or len(submitted_items) != 1
                    or not isinstance(submitted_items[0], dict)
                ):
                    raise InputValidationError(
                        "本地靶标交互回复必须只包含一条 interactions 记录"
                    )
                submitted_item = submitted_items[0]
            submitted_id = submitted_item.get("interactionId")
            if expected_id and submitted_id != expected_id:
                raise InputValidationError(
                    "子任务交互 interactionId 不匹配",
                    details={"expected": expected_id, "submitted": submitted_id},
                )
            if submitted_item.get("answer") != "PROVIDE_TEMPLATE":
                raise InputValidationError(
                    "本地靶标交互只接受 PROVIDE_TEMPLATE 动作"
                )
            values = submitted_item.get("fieldValues") or {}
            if not isinstance(values, dict):
                raise InputValidationError("fieldValues 必须为对象")
            accepted = {
                key: copy.deepcopy(values[key])
                for key in (
                    "sequence",
                    "fasta_text",
                    "fasta_path",
                    "input_artifact_id",
                )
                if values.get(key)
            }
            if not accepted:
                raise InputValidationError(
                    "请提交 sequence、fasta_text、fasta_path 或 input_artifact_id"
                )
            if len(accepted) != 1:
                raise InputValidationError(
                    "每个检测靶标只能补充一个模板来源",
                    details={"source_fields": sorted(accepted)},
                )
            artifact_id = _text(accepted.get("input_artifact_id"))
            if artifact_id and not ARTIFACT_ID_PATTERN.fullmatch(artifact_id):
                raise InputValidationError(
                    "input_artifact_id 格式无效",
                    details={"input_artifact_id": artifact_id},
                )
            if artifact_id:
                assigned_targets: list[str] = []
                for frozen in (
                    (batch.get("user_input_snapshot") or {}).get("targets") or []
                ):
                    frozen_source = frozen.get("source_input") or {}
                    if (
                        isinstance(frozen_source, dict)
                        and frozen_source.get("input_artifact_id") == artifact_id
                        and frozen.get("target_id") != target_id
                    ):
                        assigned_targets.append(str(frozen.get("target_id")))
                for sibling in (
                    (batch.get("target_design") or {}).get("targets") or []
                ):
                    if (
                        (sibling.get("supplemental_input") or {}).get(
                            "input_artifact_id"
                        )
                        == artifact_id
                        and sibling.get("target_id") != target_id
                    ):
                        assigned_targets.append(str(sibling.get("target_id")))
                if assigned_targets:
                    raise InputValidationError(
                        "同一个上传文件不能绑定多个检测靶标",
                        details={
                            "input_artifact_id": artifact_id,
                            "already_assigned_target_ids": sorted(
                                set(assigned_targets)
                            ),
                            "requested_target_id": target_id,
                        },
                    )
            # N05 freezes the three core user-input categories.  A later
            # template supplement is execution input, so keep it target-local
            # instead of mutating the frozen snapshot or its digest.
            target_result["supplemental_input"] = accepted
            target_result["supplemental_input_sha256"] = _canonical_sha256(
                accepted
            )
        target_result.update(
            {
                "status": "NOT_STARTED",
                "child_next_action": None,
                "child_ui_message": None,
            }
        )
        self._append_event(
            batch,
            "WF02_CHILD_INPUT_SUBMITTED",
            status="RESUMING",
            target_id=target_id,
            child_batch_id=child_batch_id,
        )
        self._save_batch(batch)
        return (self.run_until_blocked(batch_id, bounded=bounded) if auto_resume
                    else summarize_wf02_batch(self.store.get_batch(batch_id)))

    def run_until_blocked(
        self,
        batch_id: str,
        *,
        bounded: bool = False,
        expected_resume_token: str | None = None,
    ) -> dict[str, Any]:
        """Serialize one task advance and optionally enforce compare-and-resume."""
        with self._batch_lock(batch_id):
            before = self.store.get_batch(batch_id)
            before_token = self._resume_token(before)
            if expected_resume_token and expected_resume_token != before_token:
                current = summarize_wf02_batch(before)
                current.update(
                    {
                        "resume_token": before_token,
                        "execution_unit": {
                            "operation_id": str(uuid4()),
                            "status": "STALE_RESUME_TOKEN_NOOP",
                            "state_committed": False,
                            "expected_resume_token": expected_resume_token,
                            "actual_resume_token": before_token,
                        },
                    }
                )
                return current
            result = self._run_until_blocked_serialized(
                batch_id,
                bounded=bounded,
            )
            after = self.store.get_batch(batch_id)
            after_token = self._resume_token(after)
            result.update(
                {
                    "resume_token": after_token,
                    "execution_unit": {
                        "operation_id": str(uuid4()),
                        "status": "COMMITTED" if before_token != after_token else "NOOP",
                        "state_committed": before_token != after_token,
                        "before_resume_token": before_token,
                        "after_resume_token": after_token,
                    },
                }
            )
            return result

    def _run_until_blocked_serialized(
        self,
        batch_id: str,
        *,
        bounded: bool = False,
    ) -> dict[str, Any]:
        """Advance N06-N32 until an interaction or execution boundary.

        Bounded mode is retained for compatibility and interaction submission.
        The persistent background executor uses non-bounded mode and resumes
        from each persisted ``next_action`` without involving the model.
        """
        batch = self.store.get_batch(batch_id)
        balance_status = (
            (batch.get("multiplex") or {}).get("balance") or {}
        ).get("status")
        entered_with_stage6_ready = batch.get("status") in {
            "READY_FOR_BALANCE",
            "BALANCING_PANELS",
            "READY_FOR_SCORING",
        } or balance_status in {
            "RUNNING",
            "REDESIGNING_TARGETS",
            "WAITING_CHILD_INPUT",
            "REDESIGN_UNAVAILABLE",
            "REBUILDING_PANELS",
            "REDESIGN_NO_FULL_PANEL",
            "COMPLETED",
        }
        entered_with_relaxation_ready = batch.get("status") in {
            "READY_FOR_RELAXATION",
            "RELAXING_PANELS",
        } or (batch.get("multiplex") or {}).get("relaxation", {}).get(
            "status"
        ) == "WAITING_P4_RETRY"
        target_results = ((batch.get("target_design") or {}).get("targets") or [])
        if any(item.get("status") == "WAITING_USER" for item in target_results):
            return summarize_wf02_batch(batch)
        if batch.get("stage") in {"N05", "N06", "N08"}:
            batch = self.execute_target_design(
                batch_id,
                max_children=1 if bounded else None,
            )
            if bounded:
                return summarize_wf02_batch(batch)
        target_results = ((batch.get("target_design") or {}).get("targets") or [])
        if any(item.get("status") == "WAITING_USER" for item in target_results):
            return summarize_wf02_batch(batch)
        internal = batch.get("internal_control") or {}
        if internal.get("status") == "WAITING_USER":
            return summarize_wf02_batch(batch)
        if batch.get("stage") in {"N09", "N10", "N11", "N12", "N13"}:
            batch = self.execute_internal_control(batch_id)
            if bounded:
                return summarize_wf02_batch(batch)
        internal = batch.get("internal_control") or {}
        if internal.get("status") == "WAITING_USER":
            return summarize_wf02_batch(batch)
        screening = (batch.get("multiplex") or {}).get("screening") or {}
        if batch.get("stage") in {
            "N13", "N14", "N15", "N16", "N16A", "N17", "N18", "N19"
        } or screening.get("status") == "WAITING_P4_RETRY":
            batch = self.execute_panel_screening(
                batch_id,
                max_combinations=(
                    WF02_BOUNDED_COMBINATION_BUDGET if bounded else None
                ),
            )
            if bounded:
                return summarize_wf02_batch(batch)
        if entered_with_relaxation_ready:
            batch = self.execute_panel_relaxation(
                batch_id,
                max_combinations=(
                    WF02_BOUNDED_COMBINATION_BUDGET if bounded else None
                ),
            )
            if bounded:
                return summarize_wf02_batch(batch)
        if entered_with_stage6_ready:
            batch = self.execute_stage6(batch_id)
        return summarize_wf02_batch(batch)

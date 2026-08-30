"""Canonical Agent D phase-three domain models.

The models deliberately avoid framework dependencies.  The API, CLI, tests and
workflow engine all serialize the same objects.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4


class WorkflowStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class RuleStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARNING = "WARNING"
    NOT_EVALUATED = "NOT_EVALUATED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class EvidenceLevel(str, Enum):
    IN_SILICO_ONLY = "in_silico_only"
    BLAST_VERIFIED = "blast_verified"
    EXPERIMENTALLY_VALIDATED = "experimentally_validated"


class TargetManifestStatus(str, Enum):
    """M1 intake state for one independently executable target."""

    READY_DESIGN_ONLY = "READY_DESIGN_ONLY"
    READY_FULL = "READY_FULL"
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
    BLOCKED = "BLOCKED"


class TargetExecutionPath(str, Enum):
    """Scientific route selected from the target's available context."""

    DESIGN_ONLY = "DESIGN_ONLY"
    FULL_SPECIFICITY = "FULL_SPECIFICITY"
    NOT_EXECUTABLE = "NOT_EXECUTABLE"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {key: jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


@dataclass(slots=True)
class SequenceRecord:
    id: str
    sequence: str
    description: str = ""
    source: str = "user"


@dataclass(slots=True)
class Oligo:
    sequence_5to3: str
    length_nt: int
    tm_celsius: float | None
    gc_percent: float
    position_start: int | None = None
    position_end: int | None = None
    hairpin_dg_kcal: float | None = None
    homodimer_dg_kcal: float | None = None
    three_prime_dimer_dg_kcal: float | None = None
    thermo_method: str = "not_evaluated"


@dataclass(slots=True)
class RuleResult:
    code: str
    category: str
    status: RuleStatus
    actual: Any
    threshold: str
    source: str
    message: str = ""


@dataclass(slots=True)
class SpecificityModeResult:
    mode: str
    verdict: str
    blast_hits: int
    details: list[dict] = field(default_factory=list)
    recommendation: str | None = None
    mode_specific: dict = field(default_factory=dict)
    method: str = "not_run"


@dataclass(slots=True)
class SpecificityResult:
    target: SpecificityModeResult
    host: SpecificityModeResult
    cross_species: SpecificityModeResult
    rating: str
    method: str
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Candidate:
    candidate_id: str
    forward_primer: Oligo
    reverse_primer: Oligo
    probe: Oligo | None
    amplicon_sequence_5to3: str
    amplicon_start: int
    amplicon_end: int
    conservation_score: float | None = None
    conservation_method: str = "not_evaluated"
    conservation_sequence_count: int = 1
    primer3_pair_penalty: float | None = None
    pair_heterodimer_dg_kcal: float | None = None
    three_prime_heterodimer_dg_kcal: float | None = None
    three_prime_involved: bool | None = None
    design_method: str = "primer3"
    mgb_assessment: dict | None = None
    amplicon_structure: dict | None = None
    specificity: SpecificityResult | None = None
    evidence_level: EvidenceLevel = EvidenceLevel.IN_SILICO_ONLY
    rules: list[RuleResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    score: float = 0.0
    rank: int | None = None
    verdict: str = "PENDING"
    metadata: dict = field(default_factory=dict)

    @property
    def length_bp(self) -> int:
        return len(self.amplicon_sequence_5to3)

    def to_dict(self) -> dict:
        data = jsonable(self)
        metadata = data.get("metadata")
        p2_specificity = None
        if isinstance(metadata, dict):
            p2_specificity = metadata.pop("_p2_specificity", None)
        if isinstance(p2_specificity, dict):
            data["specificity"] = p2_specificity
        data["amplicon"] = {
            "sequence_5to3": self.amplicon_sequence_5to3,
            "length_bp": self.length_bp,
            "start": self.amplicon_start,
            "end": self.amplicon_end,
        }
        for key in ("amplicon_sequence_5to3", "amplicon_start", "amplicon_end"):
            data.pop(key, None)
        return data


@dataclass(slots=True)
class WorkflowResult:
    workflow: str
    status: WorkflowStatus
    assay_id: str
    input: dict
    target: dict
    candidates: list[Candidate] = field(default_factory=list)
    specificity: dict | None = None
    variation: dict | None = None
    multiplex: dict | None = None
    internal_control: dict | None = None
    recommended_conditions: dict | None = None
    diagnosis: dict | None = None
    relaxation_metadata: dict = field(default_factory=dict)
    degenerate_strategy: dict | None = None
    self_check: dict = field(default_factory=dict)
    reports: dict = field(default_factory=dict)
    workflow_trace: list[dict] = field(default_factory=list)
    tool_status: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)
    schema_version: str = "3.0.0-phase3"
    job_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=utc_now)
    evidence_level: EvidenceLevel = EvidenceLevel.IN_SILICO_ONLY
    disclaimer: str = (
        "本结果仅供科研设计参考；未经实验验证，不得直接用于临床或生产决策。"
    )

    def to_dict(self) -> dict:
        data = jsonable(self)
        data["candidates"] = [candidate.to_dict() for candidate in self.candidates]
        return data

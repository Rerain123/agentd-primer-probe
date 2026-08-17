"""WF-03 internal-control workflow."""

from __future__ import annotations

from ..config import config_fingerprint
from ..database import DatabaseCatalog
from ..internal_control import InternalControlDesigner
from ..models import EvidenceLevel, WorkflowResult, WorkflowStatus


class WF03InternalControl:
    def __init__(self, config: dict, catalog: DatabaseCatalog | None = None) -> None:
        self.config = config
        self.catalog = catalog or DatabaseCatalog(config)

    def run(self, payload: dict) -> WorkflowResult:
        designer = InternalControlDesigner(self.config, self.catalog)
        internal = designer.design(payload)
        candidates = internal.pop("candidate_objects", [])
        if internal.get("status") == "NOT_CONFIGURED":
            status = WorkflowStatus.PARTIAL
        elif internal.get("verdict") == "FAIL":
            status = WorkflowStatus.FAILED
        elif internal.get("evidence_level") == EvidenceLevel.BLAST_VERIFIED.value:
            status = WorkflowStatus.SUCCEEDED
        else:
            status = WorkflowStatus.PARTIAL
        evidence = EvidenceLevel.BLAST_VERIFIED if internal.get("evidence_level") == EvidenceLevel.BLAST_VERIFIED.value else EvidenceLevel.IN_SILICO_ONLY
        host = str(payload.get("host_species", "unknown"))
        return WorkflowResult(
            workflow="WF-03",
            status=status,
            assay_id=str(payload.get("assay_id") or f"{host}_internal_control_v1"),
            input={key: value for key, value in payload.items() if key not in {"gene_sequences"}},
            target={"host_species": host, "role": "internal_control", "gene_selected": internal.get("gene_selected")},
            candidates=candidates,
            internal_control=internal,
            self_check={"verdict": "PASS" if internal.get("verdict") == "PASS" else "CAUTION", "risks": internal.get("warnings", [])},
            tool_status={"P5": designer.status(allow_fallback=bool(payload.get("allow_fallback"))), "C0": self.catalog.status(), **internal.get("tool_status", {})},
            warnings=internal.get("warnings", []),
            errors=[] if internal.get("verdict") != "FAIL" else [{"code": internal.get("status", "INTERNAL_CONTROL_FAILED"), "message": "内标设计未完成", "details": {"recommendation": internal.get("recommendation")}}],
            provenance={"agentd_version": "0.3.15", "config_schema_version": self.config["schema_version"], "config_sha256": config_fingerprint(self.config)},
            workflow_trace=[{"step": "P5", "status": internal.get("status"), "verdict": internal.get("verdict")}],
            evidence_level=evidence,
        )

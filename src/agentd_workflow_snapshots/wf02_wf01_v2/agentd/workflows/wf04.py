"""WF-04 existing primer/probe validation."""

from __future__ import annotations

import tempfile

from ..config import config_fingerprint
from ..errors import InputValidationError, ToolUnavailableError
from ..mgb import MGBProbeEvaluator
from ..models import EvidenceLevel, WorkflowResult, WorkflowStatus, jsonable
from ..primer3_tool import Primer3Adapter
from ..rules import RuleEngine
from ..scoring import score_candidate
from ..sequence import normalize_sequence
from ..specificity import BlastAdapter, SpecificityEvaluator, write_subject_fasta
from ..structure import ViennaRNAAdapter
from .common import evidence_from_specificity, not_checked_specificity


class WF04ExistingPrimerValidation:
    def __init__(self, config: dict) -> None:
        self.config = config

    def run(self, payload: dict) -> WorkflowResult:
        if not payload.get("forward_primer") or not payload.get("reverse_primer"):
            raise InputValidationError("WF-04需要forward_primer和reverse_primer")
        forward = normalize_sequence(payload["forward_primer"])
        reverse = normalize_sequence(payload["reverse_primer"])
        probe = normalize_sequence(payload["probe"]) if payload.get("probe") else None
        template = normalize_sequence(payload["template"]) if payload.get("template") else None
        allow_fallback = bool(payload.get("allow_fallback", self.config["tools"]["allow_development_fallback"]))
        primer3 = Primer3Adapter(self.config, allow_fallback=allow_fallback)
        blast = BlastAdapter(self.config["tools"]["blastn_executable"], allow_fallback=allow_fallback)
        evaluator = SpecificityEvaluator(blast, primer3)
        mgb = MGBProbeEvaluator(primer3, self.config)
        vienna = ViennaRNAAdapter(self.config)
        rules = RuleEngine()
        candidate = primer3.validate_pair(
            forward,
            reverse,
            probe=probe,
            template=template,
            sequence_id=str(payload.get("target_id", "existing")),
        )
        warnings: list[str] = []
        use_mgb = bool(payload.get("use_mgb") or str(payload.get("probe_type", "")).upper() in {"MGB", "TAQMAN_MGB"})
        if use_mgb:
            if candidate.probe is None:
                raise InputValidationError("MGB评估需要probe序列")
            candidate.mgb_assessment = mgb.assess(
                candidate.probe.sequence_5to3,
                fluorophore=payload.get("fluorophore"),
                snp_position=payload.get("snp_position"),
            )
            candidate.warnings.extend(f"P3:{code}" for code in candidate.mgb_assessment["warnings"])
            candidate.metadata["probe_type"] = "TaqMan_MGB"
        else:
            candidate.metadata["probe_type"] = "TaqMan_BHQ" if candidate.probe else "none"
        if candidate.amplicon_sequence_5to3 and vienna.available:
            try:
                candidate.amplicon_structure = vienna.assess_full(candidate.amplicon_sequence_5to3)
            except Exception as exc:  # optional P6 must not block validation
                candidate.amplicon_structure = vienna.not_evaluated(str(exc))
        else:
            candidate.amplicon_structure = vienna.not_evaluated("ViennaRNA不可用或扩增子缺失")
        with tempfile.TemporaryDirectory(prefix="agentd-wf04-") as temp_dir:
            target_db = payload.get("target_db")
            if not target_db and template:
                target_db = str(write_subject_fasta(template, temp_dir, record_id=str(payload.get("target_id", "target"))))
            try:
                candidate.specificity = evaluator.evaluate(
                    candidate.forward_primer,
                    candidate.reverse_primer,
                    candidate.probe,
                    target_db=target_db,
                    host_db=payload.get("host_db"),
                    cross_db=payload.get("cross_db"),
                    host_species=payload.get("host_species"),
                    target_species=payload.get("target_species"),
                )
            except ToolUnavailableError as exc:
                candidate.specificity = not_checked_specificity(exc.message)
                warnings.append(exc.message)
        candidate.evidence_level = evidence_from_specificity(candidate.specificity)
        candidate.rules = rules.evaluate(candidate)
        rules.apply_verdict(candidate)
        if candidate.mgb_assessment and not candidate.mgb_assessment["all_hard_pass"]:
            candidate.verdict = "FAIL"
            candidate.warnings.append("P3 MGB硬性规则未通过")
        score_candidate(candidate, self.config)
        candidate.rank = 1
        if candidate.verdict == "FAIL" or candidate.specificity.rating == "FAIL":
            status = WorkflowStatus.FAILED
        elif candidate.evidence_level == EvidenceLevel.BLAST_VERIFIED and candidate.verdict in {"PASS", "PASS_WITH_WARNINGS"}:
            status = WorkflowStatus.SUCCEEDED
        else:
            status = WorkflowStatus.PARTIAL
        return WorkflowResult(
            workflow="WF-04",
            status=status,
            assay_id=str(payload.get("assay_id") or f"{payload.get('target_id', 'existing')}_validation_v1"),
            input={key: value for key, value in payload.items() if key != "template"},
            target={
                "target_id": payload.get("target_id"),
                "organism": payload.get("target_species"),
                "template_provided": template is not None,
                "template_length_nt": len(template) if template else None,
            },
            candidates=[candidate],
            specificity=jsonable(candidate.specificity),
            self_check={"verdict": "PASS" if status != WorkflowStatus.FAILED else "CAUTION", "risks": []},
            tool_status={"P1": primer3.status(), "P2_C6": blast.status(), "P3": mgb.status(), "P6": vienna.status(), "C1": {"tool": "C1_sequence_io", "available": True}, "C7": {"tool": "C7_schema", "available": True}},
            warnings=warnings,
            provenance={"agentd_version": "0.3.15", "config_schema_version": self.config["schema_version"], "config_sha256": config_fingerprint(self.config)},
            evidence_level=candidate.evidence_level,
        )

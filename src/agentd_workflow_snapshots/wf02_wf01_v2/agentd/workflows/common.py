"""Shared workflow helpers."""

from __future__ import annotations

from ..models import EvidenceLevel, SpecificityModeResult, SpecificityResult


def not_checked_specificity(message: str) -> SpecificityResult:
    def mode(name: str) -> SpecificityModeResult:
        return SpecificityModeResult(
            mode=name,
            verdict="NOT_CHECKED",
            blast_hits=0,
            recommendation=message,
            method="not_run",
        )

    return SpecificityResult(
        target=mode("check_target"),
        host=mode("check_host"),
        cross_species=mode("check_cross_species"),
        rating="PASS_WITH_WARNINGS",
        method="not_run",
        warnings=[message],
    )


def evidence_from_specificity(result: SpecificityResult) -> EvidenceLevel:
    if (
        result.method == "blastn"
        and result.target.verdict == "PASS"
        and result.host.verdict == "PASS"
        and result.cross_species.verdict in {"PASS", "CAUTION"}
    ):
        required = set(result.target.mode_specific.get("required_query_ids", []))
        perfect = set(result.target.mode_specific.get("perfect_query_ids", []))
        if required:
            target_complete = required.issubset(perfect)
        else:
            target_complete = bool(result.target.details) and all(
                detail.get("identity_pct") == 100.0
                and detail.get("query_coverage_pct") == 100.0
                and detail.get("evalue") is not None
                and detail["evalue"] < 1e-5
                for detail in result.target.details
            )
        if target_complete:
            return EvidenceLevel.BLAST_VERIFIED
    return EvidenceLevel.IN_SILICO_ONLY

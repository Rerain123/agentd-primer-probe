"""Transparent WF-01 candidate scoring."""

from __future__ import annotations

from .models import Candidate, RuleStatus
from .sequence import gc_percent


def _closeness(value: float | None, optimum: float, tolerance: float) -> float:
    if value is None:
        return 0.4
    return max(0.0, 1.0 - abs(value - optimum) / tolerance)


def score_candidate(candidate: Candidate, config: dict) -> float:
    if candidate.verdict == "FAIL":
        candidate.score = 0.0
        return 0.0
    scoring = config["scoring"]
    conservation = candidate.conservation_score if candidate.conservation_score is not None else 0.5
    primer_tm = (
        _closeness(candidate.forward_primer.tm_celsius, 59.0, 9.0)
        + _closeness(candidate.reverse_primer.tm_celsius, 59.0, 9.0)
    ) / 2
    probe_delta_score = 0.4
    if candidate.mgb_assessment and candidate.forward_primer.tm_celsius is not None and candidate.reverse_primer.tm_celsius is not None:
        corrected = candidate.mgb_assessment.get("corrected_tm_celsius")
        probe_delta_score = _closeness(float(corrected) if corrected is not None else None, 68.5, 10.0)
    elif candidate.probe and candidate.probe.tm_celsius is not None and candidate.forward_primer.tm_celsius is not None and candidate.reverse_primer.tm_celsius is not None:
        primer_mean = (candidate.forward_primer.tm_celsius + candidate.reverse_primer.tm_celsius) / 2
        probe_delta_score = _closeness(candidate.probe.tm_celsius - primer_mean, 9.0, 9.0)
    thermodynamics = (primer_tm + probe_delta_score) / 2

    if candidate.specificity is None:
        specificity = 0.4
    elif candidate.specificity.rating == "PASS":
        specificity = 1.0
    elif candidate.specificity.rating == "PASS_WITH_WARNINGS":
        specificity = 0.7
    else:
        specificity = 0.0

    length_score = 1.0 if 70 <= candidate.length_bp <= 150 else 0.6
    amplicon_gc = gc_percent(candidate.amplicon_sequence_5to3) if candidate.amplicon_sequence_5to3 else 0.0
    gc_score = 1.0 if 35 <= amplicon_gc <= 65 else 0.6
    amplicon = (length_score + gc_score) / 2

    raw = (
        conservation * float(scoring["conservation_weight"])
        + thermodynamics * float(scoring["thermodynamics_weight"])
        + specificity * float(scoring["specificity_weight"])
        + amplicon * float(scoring["amplicon_weight"])
    )
    warning_codes = {
        rule.code
        for rule in candidate.rules
        if rule.status in {RuleStatus.WARNING, RuleStatus.NOT_EVALUATED}
    }
    penalty = sum(
        float(percent)
        for code, percent in scoring["warning_penalties_percent"].items()
        if code in warning_codes
    )
    if candidate.amplicon_structure:
        structure_status = candidate.amplicon_structure.get("status")
        if structure_status == "WARNING":
            penalty += float(scoring["warning_penalties_percent"].get("R-SUPPLEMENT_WARNING", 0))
        elif structure_status == "CAUTION":
            penalty += float(scoring["warning_penalties_percent"].get("R-SUPPLEMENT_CAUTION", 0))
    if candidate.conservation_sequence_count < 20:
        penalty += float(scoring["warning_penalties_percent"].get("low_confidence_conservation", 0))
    candidate.metadata["score_components"] = {
        "conservation": round(conservation, 6),
        "thermodynamics": round(thermodynamics, 6),
        "specificity": round(specificity, 6),
        "amplicon": round(amplicon, 6),
        "raw_score": round(raw, 6),
        "penalty_percent": penalty,
    }
    candidate.score = round(max(0.0, raw * (1.0 - min(penalty, 100.0) / 100.0)), 6)
    return candidate.score


def self_check(candidates: list[Candidate]) -> dict:
    risks: list[str] = []
    regions = [candidate.metadata.get("region_index") for candidate in candidates]
    if len(candidates) >= 3 and len(set(regions)) == 1:
        risks.append("Top候选集中于同一保守区，存在区域集中风险")
    if candidates:
        extreme = [
            candidate.candidate_id
            for candidate in candidates
            if not 35 <= gc_percent(candidate.amplicon_sequence_5to3) <= 65
        ]
        if len(extreme) == len(candidates):
            risks.append("所有候选扩增子GC均超出35%-65%，存在GC偏态")
    return {"verdict": "CAUTION" if risks else "PASS", "risks": risks}

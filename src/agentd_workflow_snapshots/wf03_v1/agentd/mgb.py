"""P3 MGB-probe Tm correction and rule assessment.

The MGB contribution is an explicitly labelled engineering approximation.  It
does not claim compatibility with the proprietary Primer Express model.
"""

from __future__ import annotations

from .primer3_tool import Primer3Adapter
from .sequence import gc_percent, normalize_sequence, reverse_complement


def calc_mgb_correction(probe_sequence: str) -> float:
    sequence = normalize_sequence(probe_sequence)
    at_fraction = (sequence.count("A") + sequence.count("T")) / len(sequence)
    if at_fraction >= 0.70:
        correction = 20.0
    elif at_fraction >= 0.55:
        correction = 16.0
    elif at_fraction >= 0.45:
        correction = 13.0
    elif at_fraction >= 0.30:
        correction = 10.0
    else:
        correction = 7.0
    if len(sequence) < 15:
        correction += 2.0
    return correction


def _max_self_complementary_stem(sequence: str) -> int:
    """Conservative sequence-only proxy used for soft rule R-MGB-S4."""
    reverse = reverse_complement(sequence)
    best = 0
    for size in range(2, min(8, len(sequence) // 2) + 1):
        for start in range(0, len(sequence) - size + 1):
            motif = sequence[start : start + size]
            mirror_start = reverse.find(motif)
            if mirror_start >= 0 and abs(start - (len(sequence) - mirror_start - size)) >= 4:
                best = max(best, size)
    return best


class MGBProbeEvaluator:
    def __init__(self, primer3: Primer3Adapter, config: dict) -> None:
        self.primer3 = primer3
        self.config = config

    def status(self) -> dict:
        return {
            "tool": "P3_MGB",
            "available": self.primer3.available,
            "version": "engineering-model-1.0",
            "capability": "P1_DEFAULT" if self.primer3.available else ("DEVELOPMENT_FALLBACK" if self.primer3.allow_fallback else "P0b_UNAVAILABLE"),
            "base_tm_dependency": self.primer3.status(),
            "model": "public_evidence_piecewise_context_approximation",
            "estimated_error_c": 3.0,
        }

    def assess(
        self,
        probe_sequence: str,
        *,
        fluorophore: str | None = None,
        snp_position: int | None = None,
    ) -> dict:
        sequence = normalize_sequence(probe_sequence)
        fluorophore = str(fluorophore or self.config["mgb"]["default_fluorophore"]).upper()
        base_tm = self.primer3.calc_tm(sequence, probe=True)
        correction = calc_mgb_correction(sequence)
        corrected_tm = float(base_tm["tm_celsius"]) + correction
        at_fraction = (sequence.count("A") + sequence.count("T")) / len(sequence)
        hard_rules = {
            "R-MGB-01": sequence[0] != "G" and (fluorophore != "FAM" or len(sequence) == 1 or sequence[1] != "G"),
            "R-MGB-02": 13 <= len(sequence) <= 20,
            "R-MGB-03": not any(base * 4 in sequence for base in "ACGT"),
            "R-MGB-04": sequence[-4:].count("G") <= 2,
            "R-MGB-05": 30.0 <= gc_percent(sequence) <= 80.0,
        }
        soft_rules: dict[str, bool | None] = {
            "R-MGB-S1": sequence.count("C") > sequence.count("G"),
            "R-MGB-S2": "AAAAAA" not in sequence,
            "R-MGB-S3": len(sequence) <= 6 or sequence[3:-3].count("CC") < 2,
            "R-MGB-S4": _max_self_complementary_stem(sequence) <= 2,
            "R-MGB-S5": None,
        }
        if snp_position is not None:
            # Input position is zero based in probe orientation.
            middle_start = len(sequence) // 3
            middle_end = len(sequence) - middle_start - 1
            soft_rules["R-MGB-S5"] = middle_start <= snp_position <= middle_end and snp_position < len(sequence) - 3
        warnings = [code for code, passed in soft_rules.items() if passed is False]
        target_min = float(self.config["mgb"]["target_tm_min_c"])
        target_max = float(self.config["mgb"]["target_tm_max_c"])
        tm_in_target = target_min <= corrected_tm <= target_max
        if corrected_tm < 60.0 or corrected_tm > 78.0 or not all(hard_rules.values()):
            verdict = "FAIL"
        elif warnings or not tm_in_target:
            verdict = "CAUTION"
            if not tm_in_target:
                warnings.append("R-13")
        else:
            verdict = "PASS"
        if 0.35 <= at_fraction <= 0.65:
            confidence = "high"
        elif 0.25 <= at_fraction <= 0.75:
            confidence = "medium"
        else:
            confidence = "low"
        return {
            "applied": True,
            "probe_type": "TaqMan_MGB",
            "fluorophore": fluorophore,
            "base_tm_celsius": round(float(base_tm["tm_celsius"]), 1),
            "base_tm_method": base_tm["method"],
            "at_fraction": round(at_fraction, 3),
            "mgb_correction_celsius": round(correction, 1),
            "corrected_tm_celsius": round(corrected_tm, 1),
            "target_tm_range_celsius": [target_min, target_max],
            "tm_in_target": tm_in_target,
            "confidence": confidence,
            "estimated_error_celsius": 3.0,
            "rules_check": hard_rules,
            "soft_rules_check": soft_rules,
            "soft_rule_methods": {"R-MGB-S4": "sequence_self_complement_proxy", "R-MGB-S5": "input_snp_position_or_not_evaluated"},
            "all_hard_pass": all(hard_rules.values()),
            "warnings": sorted(set(warnings)),
            "verdict": verdict,
            "model": "public_evidence_piecewise_context_approximation",
            "limitations": [
                "MGB修正为公开数据工程近似，典型偏差约±3°C",
                "计算结果不可替代退火温度梯度和湿实验验证",
            ],
        }

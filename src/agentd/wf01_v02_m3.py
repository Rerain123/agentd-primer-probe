"""WF-01 PRD-v0.2 M3 exploration validation, deduplication and scoring.

M3 consumes the candidate occurrences produced by the pure M2 target engine.
It validates the pre-generated finite Primer3 manifest, deduplicates only
within one target, maps completed MAFFT conservation evidence into the
versioned four-dimensional score, and emits a provisional Top10. Specificity
is deliberately absent until M4, so M3 never labels its ranking as final.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import defaultdict
from typing import Any

from .errors import InputValidationError


M3_ENGINE_VERSION = "wf01-v0.2-m3.5-conservation"
SCORING_POLICY_VERSION = "wf01-v0.2-four-dimension-conservation-v5"
SCORING_POLICY_STATUS = (
    "ACTIVE_FOR_IN_SILICO_RANKING_REQUIRES_EXPERIMENTAL_CALIBRATION"
)
FIXED_TOP_CANDIDATES = 10
SCORING_SCALE = {"minimum": 0.0, "maximum": 100.0, "display_decimals": 1}
SCORING_WEIGHTS = {
    "conservation": 0.30,
    "thermodynamics": 0.25,
    "specificity": 0.25,
    "amplicon_features": 0.20,
}
THERMODYNAMICS_COMPONENT_WEIGHTS = {
    "forward_primer_tm": 0.25,
    "reverse_primer_tm": 0.25,
    "primer_pair_delta_tm": 0.25,
    "probe_relative_tm": 0.25,
}
AMPLICON_COMPONENT_WEIGHTS = {
    "length": 0.5,
    "gc_percent": 0.5,
}
SOFT_PENALTY_DEFINITIONS = {
    "R01_SOFT": {
        "penalty_percent": 5.0,
        "condition": "至少一条引物 Tm<55°C",
    },
    "R03_SOFT": {
        "penalty_percent": 2.0,
        "condition": "引物 3′ 端缺少 GC clamp 或连续 3 个相同碱基",
    },
    "R11": {
        "penalty_percent": 3.0,
        "condition": "任一寡核苷酸发卡 ΔG<-3.0 kcal/mol",
    },
    "R12": {
        "penalty_percent": 3.0,
        "condition": "非 3′ 二聚体 -9.0≤ΔG<-6.0 kcal/mol",
    },
    "LOW_CONFIDENCE_CONSERVATION": {
        "penalty_percent": 10.0,
        "condition": "代表序列 N<20、使用放宽保守区或全长回退",
    },
}
SOFT_PENALTIES_PERCENT = {
    code: float(definition["penalty_percent"])
    for code, definition in SOFT_PENALTY_DEFINITIONS.items()
}
PENALTY_CAP_PERCENT = 100.0
CALCULATION_DECIMALS = 6
FINAL_RANK_TIE_BREAKERS = (
    "final_score_desc",
    "total_penalty_percent_asc",
    "conservation_score_desc",
    "thermodynamics_score_desc",
    "specificity_score_desc",
    "amplicon_features_score_desc",
    "primer3_pair_penalty_asc",
    "dedupe_key_asc",
)
HARD_RULE_CODES = tuple(f"R{number:02d}" for number in range(1, 11))
RULE_STATUS_PRIORITY = {
    "FAIL": 5,
    "WARNING": 4,
    "PASS": 3,
    "NOT_EVALUATED": 2,
    "NOT_APPLICABLE": 1,
}
VERDICT_PRIORITY = {
    "REJECTED_HARD_RULE": 3,
    "HARD_RULES_NOT_EVALUATED": 2,
    "AWAITING_SPECIFICITY": 1,
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, value))


def _weighted_score(
    components: dict[str, float],
    weights: dict[str, float],
) -> float:
    """Return a 0–100 weighted score after validating the frozen contract."""
    if set(components) != set(weights):
        raise ValueError("评分分项与分项权重不一致")
    weight_total = sum(float(value) for value in weights.values())
    if not math.isclose(weight_total, 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("评分分项权重之和必须为 1")
    return sum(
        _clamp(float(components[key])) * float(weights[key])
        for key in weights
    )


def _dimension_score(candidate: dict[str, Any], name: str) -> float | None:
    value = (
        (candidate.get("metadata") or {})
        .get("wf01_v02_scoring", {})
        .get("dimensions", {})
        .get(name, {})
        .get("raw_score")
    )
    return None if value is None else float(value)


def final_scoring_rank_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    """Stable final rank matching the public scoring contract."""
    scoring = (candidate.get("metadata") or {}).get("wf01_v02_scoring") or {}
    score = candidate.get("score")
    total_penalty = scoring.get("applied_penalty_percent")
    primer3_penalty = candidate.get("primer3_pair_penalty")

    def descending(value: float | None) -> float:
        return -(float(value) if value is not None else -1.0)

    return (
        descending(None if score is None else float(score)),
        math.inf if total_penalty is None else float(total_penalty),
        descending(_dimension_score(candidate, "conservation")),
        descending(_dimension_score(candidate, "thermodynamics")),
        descending(_dimension_score(candidate, "specificity")),
        descending(_dimension_score(candidate, "amplicon_features")),
        math.inf if primer3_penalty is None else float(primer3_penalty),
        str(candidate.get("dedupe_key") or ""),
    )


def candidate_dedupe_key(candidate: dict[str, Any]) -> str:
    """PRD key: SHA256(forward + reverse + probe), scoped by the caller."""
    forward = str(candidate.get("forward_primer", {}).get("sequence_5to3") or "")
    reverse = str(candidate.get("reverse_primer", {}).get("sequence_5to3") or "")
    probe = str((candidate.get("probe") or {}).get("sequence_5to3") or "")
    return hashlib.sha256(f"{forward}{reverse}{probe}".encode("ascii", errors="ignore")).hexdigest()


def _source(candidate: dict[str, Any]) -> dict[str, Any]:
    metadata = candidate.get("metadata") or {}
    return {
        "candidate_id": candidate.get("candidate_id"),
        "exploration_unit_id": metadata.get("exploration_unit_id"),
        "parameter_run_id": metadata.get("parameter_run_id"),
        "parameter_profile_labels": copy.deepcopy(metadata.get("parameter_profile_labels") or []),
        "effective_parameters": copy.deepcopy(metadata.get("effective_parameters")),
        "region": copy.deepcopy(metadata.get("exploration_region")),
        "primer3_pair_penalty": candidate.get("primer3_pair_penalty"),
        "design_method": candidate.get("design_method"),
    }


def _strictest_rule(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Prefer the strictest completed result, then a stable canonical value."""
    return copy.deepcopy(
        sorted(
            results,
            key=lambda rule: (
                -RULE_STATUS_PRIORITY.get(str(rule.get("status")), 0),
                _canonical(rule),
            ),
        )[0]
    )


def _merged_verdict(rules: list[dict[str, Any]]) -> str:
    rule_map = {str(rule.get("code")): str(rule.get("status")) for rule in rules}
    if any(rule_map.get(code) == "FAIL" for code in HARD_RULE_CODES):
        return "REJECTED_HARD_RULE"
    if any(rule_map.get(code) != "PASS" for code in HARD_RULE_CODES):
        return "HARD_RULES_NOT_EVALUATED"
    return "AWAITING_SPECIFICITY"


def deduplicate_target_candidates(
    candidate_occurrences: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Deduplicate one target and retain every occurrence as source evidence."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidate_occurrences:
        groups[candidate_dedupe_key(candidate)].append(copy.deepcopy(candidate))

    unique: list[dict[str, Any]] = []
    for dedupe_key in sorted(groups):
        occurrences = groups[dedupe_key]
        primary = copy.deepcopy(
            sorted(
                occurrences,
                key=lambda candidate: (
                    -VERDICT_PRIORITY.get(str(candidate.get("verdict")), 0),
                    math.inf
                    if candidate.get("primer3_pair_penalty") is None
                    else float(candidate["primer3_pair_penalty"]),
                    str(candidate.get("candidate_id") or ""),
                ),
            )[0]
        )
        sources = sorted(
            (_source(candidate) for candidate in occurrences),
            key=lambda source: (
                str(source.get("exploration_unit_id") or ""),
                str(source.get("candidate_id") or ""),
            ),
        )
        rule_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for occurrence in occurrences:
            for rule in occurrence.get("rules") or []:
                rule_groups[str(rule.get("code"))].append(rule)
        merged_rules = [
            _strictest_rule(rule_groups[code])
            for code in sorted(rule_groups)
        ]
        conflicts = []
        for code in sorted(rule_groups):
            variants = {_canonical(rule) for rule in rule_groups[code]}
            if len(variants) > 1:
                conflicts.append(
                    {
                        "rule_code": code,
                        "variant_count": len(variants),
                        "selected_status": next(
                            rule.get("status") for rule in merged_rules if rule.get("code") == code
                        ),
                    }
                )
        profile_labels = sorted(
            {
                label
                for source in sources
                for label in source.get("parameter_profile_labels") or []
            },
            key=lambda label: (
                ("user", "default", "L1", "L2").index(label)
                if label in {"user", "default", "L1", "L2"}
                else 99,
                label,
            ),
        )
        primary["rules"] = merged_rules
        primary["verdict"] = _merged_verdict(merged_rules)
        primary["dedupe_key"] = dedupe_key
        primary["rank"] = None
        primary["score"] = None
        metadata = primary.setdefault("metadata", {})
        metadata["dedupe"] = {
            "scope": "TARGET_ONLY",
            "dedupe_key_method": "SHA256(forward+reverse+probe)",
            "occurrence_count": len(occurrences),
            "duplicate_count": len(occurrences) - 1,
            "source_candidate_ids": [source["candidate_id"] for source in sources],
            "sources": sources,
            "merged_parameter_profile_labels": profile_labels,
            "rule_conflicts": conflicts,
            "duplicate_occurrences_do_not_increase_score": True,
        }
        unique.append(primary)

    summary = {
        "scope": "TARGET_ONLY",
        "key_method": "SHA256(forward+reverse+probe)",
        "input_occurrences": len(candidate_occurrences),
        "unique_candidates": len(unique),
        "duplicates_collapsed": len(candidate_occurrences) - len(unique),
        "cross_target_deduplication": False,
    }
    return unique, summary


class WF01V02ScoringEngine:
    """Four-dimensional score with explicit conservation/P2 evidence states."""

    @staticmethod
    def _effective_weights(names: list[str]) -> dict[str, float]:
        total = sum(SCORING_WEIGHTS[name] for name in names)
        if total <= 0:
            return {}
        return {
            name: round(
                SCORING_WEIGHTS[name] / total,
                CALCULATION_DECIMALS,
            )
            for name in names
        }

    @staticmethod
    def _conservation_score(
        candidate: dict[str, Any],
        *,
        include_conservation: bool,
    ) -> tuple[float | None, dict[str, Any]]:
        evidence = copy.deepcopy(
            (candidate.get("metadata") or {}).get(
                "conservation_evidence"
            )
            or {}
        )
        if not include_conservation:
            return None, {
                "status": "NOT_APPLICABLE",
                "reason": "DESIGN_ONLY_ROUTE_EXCLUDES_CONSERVATION_SCORING",
                "method": evidence.get("method"),
                "evidence": evidence,
            }
        if evidence.get("status") != "EVALUATED":
            return None, {
                "status": "NOT_EVALUATED",
                "reason": (
                    evidence.get("reason")
                    or "CONSERVATION_EVIDENCE_NOT_AVAILABLE"
                ),
                "method": evidence.get("method"),
                "evidence": evidence,
            }
        score = evidence.get("score")
        if score is None:
            return None, {
                "status": "NOT_EVALUATED",
                "reason": "CONSERVATION_SCORE_MISSING",
                "method": evidence.get("method"),
                "evidence": evidence,
            }
        normalized = float(score)
        if not 0 <= normalized <= 1:
            raise InputValidationError(
                "保守性分数必须为 0–1",
                details={"conservation_score": score},
            )
        return round(100.0 * normalized, CALCULATION_DECIMALS), {
            "status": "EVALUATED",
            "method": evidence.get("method") or "mafft_shannon",
            "actual": {
                "score_0_to_1": normalized,
                "coverage_percent": evidence.get("coverage_percent"),
                "sequence_count": evidence.get("sequence_count"),
                "confidence": evidence.get("confidence"),
                "region": copy.deepcopy(evidence.get("region") or {}),
            },
            "evidence": evidence,
        }

    @staticmethod
    def _primer_tm_score(value: float) -> float:
        """Do not rank hard-rule-passing primers by absolute Tm."""
        return 100.0 if 50.0 <= value <= 65.0 else 0.0

    def _thermodynamics_score(self, candidate: dict[str, Any]) -> tuple[float | None, dict[str, Any]]:
        forward_tm = candidate.get("forward_primer", {}).get("tm_celsius")
        reverse_tm = candidate.get("reverse_primer", {}).get("tm_celsius")
        probe_tm = (candidate.get("probe") or {}).get("tm_celsius")
        if None in (forward_tm, reverse_tm, probe_tm):
            return None, {
                "status": "NOT_EVALUATED",
                "reason": "MISSING_TM",
            }
        forward_tm = float(forward_tm)
        reverse_tm = float(reverse_tm)
        probe_tm = float(probe_tm)
        mean_primer_tm = (forward_tm + reverse_tm) / 2
        delta_primer_tm = abs(forward_tm - reverse_tm)
        probe_delta_tm = round(
            probe_tm - mean_primer_tm,
            CALCULATION_DECIMALS,
        )
        components = {
            "forward_primer_tm": self._primer_tm_score(forward_tm),
            "reverse_primer_tm": self._primer_tm_score(reverse_tm),
            "primer_pair_delta_tm": _clamp(100.0 * (2.0 - delta_primer_tm) / 2.0),
            "probe_relative_tm": (
                100.0 if 8.0 <= probe_delta_tm <= 10.0 else 0.0
            ),
        }
        score = _weighted_score(
            components,
            THERMODYNAMICS_COMPONENT_WEIGHTS,
        )
        return round(score, CALCULATION_DECIMALS), {
            "status": "EVALUATED",
            "components": {
                key: round(value, CALCULATION_DECIMALS)
                for key, value in components.items()
            },
            "component_weights": copy.deepcopy(
                THERMODYNAMICS_COMPONENT_WEIGHTS
            ),
            "actual": {
                "forward_tm_celsius": forward_tm,
                "reverse_tm_celsius": reverse_tm,
                "primer_delta_tm_celsius": round(
                    delta_primer_tm,
                    CALCULATION_DECIMALS,
                ),
                "probe_delta_tm_celsius": round(
                    probe_delta_tm,
                    CALCULATION_DECIMALS,
                ),
            },
            "thresholds": {
                "primer_tm_hard_celsius": [50.0, 65.0],
                "primer_tm_equal_score_celsius": [50.0, 65.0],
                "primer_tm_no_penalty_celsius": [55.0, 65.0],
                "primer_tm_low_warning_below_celsius": 55.0,
                "primer_delta_tm_hard_max_celsius": 2.0,
                "probe_delta_tm_hard_celsius": [8.0, 10.0],
                "probe_delta_tm_equal_score_celsius": [8.0, 10.0],
            },
            "method": "weighted_R01_R07_interval_score_v4",
            "formula": (
                "0.25×forward_tm + 0.25×reverse_tm + "
                "0.25×primer_delta_tm + 0.25×probe_relative_tm"
            ),
        }

    @staticmethod
    def _amplicon_score(candidate: dict[str, Any]) -> tuple[float | None, dict[str, Any]]:
        amplicon = candidate.get("amplicon") or {}
        length = amplicon.get("length_bp")
        sequence = str(amplicon.get("sequence_5to3") or "")
        if length is None or not sequence:
            return None, {"status": "NOT_EVALUATED", "reason": "MISSING_AMPLICON"}
        length = int(length)
        gc_percent = 100.0 * (sequence.count("G") + sequence.count("C")) / len(sequence)
        if length == 70:
            length_score = 70.0
            boundary_policy = "R02_LOWER_BOUNDARY_REDUCED_TO_70"
        elif 70 < length <= 150:
            length_score = 100.0
            boundary_policy = "RECOMMENDED_PRODUCT_WINDOW"
        elif 150 < length <= 200:
            length_score = 70.0
            boundary_policy = "NON_RECOMMENDED_ALLOWED_FIXED_70"
        else:
            length_score = 0.0
            boundary_policy = "OUTSIDE_HARD_RANGE"
        if 35 <= gc_percent <= 65:
            gc_score = 100.0
        elif gc_percent < 35:
            gc_score = 100.0 * gc_percent / 35.0
        else:
            gc_score = 100.0 * (100.0 - gc_percent) / 35.0
        components = {
            "length": _clamp(length_score),
            "gc_percent": _clamp(gc_score),
        }
        score = _weighted_score(components, AMPLICON_COMPONENT_WEIGHTS)
        return round(score, CALCULATION_DECIMALS), {
            "status": "EVALUATED",
            "components": {
                key: round(value, CALCULATION_DECIMALS)
                for key, value in components.items()
            },
            "component_weights": copy.deepcopy(
                AMPLICON_COMPONENT_WEIGHTS
            ),
            "actual": {
                "length_bp": length,
                "gc_percent": round(gc_percent, CALCULATION_DECIMALS),
            },
            "thresholds": {
                "hard_length_bp": [70, 200],
                "lower_boundary_warning_bp": 70,
                "recommended_length_bp": [71, 150],
                "non_recommended_allowed_length_bp": [151, 200],
                "recommended_length_score": 100.0,
                "non_recommended_length_score": 70.0,
                "preferred_gc_percent": [35.0, 65.0],
            },
            "length_policy": boundary_policy,
            "method": "weighted_amplicon_length_and_gc_v3",
            "formula": "0.5×length_score + 0.5×gc_score",
        }

    @staticmethod
    def _penalties(
        candidate: dict[str, Any],
        *,
        include_conservation: bool = True,
    ) -> tuple[list[dict[str, Any]], float]:
        rule_statuses = {
            str(rule.get("code")): str(rule.get("status"))
            for rule in candidate.get("rules") or []
        }
        items = []
        for code, definition in SOFT_PENALTY_DEFINITIONS.items():
            if code == "LOW_CONFIDENCE_CONSERVATION":
                continue
            if rule_statuses.get(code) != "WARNING":
                continue
            items.append(
                {
                    "rule_code": code,
                    "trigger_status": "WARNING",
                    "condition": definition["condition"],
                    "penalty_percent": float(
                        definition["penalty_percent"]
                    ),
                    "method": "ADDITIVE",
                }
            )
        conservation = (
            (candidate.get("metadata") or {}).get(
                "conservation_evidence"
            )
            or {}
        )
        if (
            include_conservation
            and conservation.get("status") == "EVALUATED"
            and conservation.get("confidence") == "LOW"
        ):
            definition = SOFT_PENALTY_DEFINITIONS[
                "LOW_CONFIDENCE_CONSERVATION"
            ]
            items.append(
                {
                    "rule_code": "LOW_CONFIDENCE_CONSERVATION",
                    "trigger_status": "WARNING",
                    "condition": definition["condition"],
                    "penalty_percent": float(
                        definition["penalty_percent"]
                    ),
                    "method": "ADDITIVE",
                    "evidence": {
                        "sequence_count": conservation.get(
                            "sequence_count"
                        ),
                        "region": copy.deepcopy(
                            conservation.get("region") or {}
                        ),
                        "reasons": copy.deepcopy(
                            conservation.get(
                                "low_confidence_reasons"
                            )
                            or []
                        ),
                    },
                }
            )
        return items, sum(item["penalty_percent"] for item in items)

    def score(
        self,
        candidate: dict[str, Any],
        *,
        specificity_score: float | None = None,
        include_conservation: bool = True,
    ) -> dict[str, Any]:
        conservation, conservation_detail = self._conservation_score(
            candidate,
            include_conservation=include_conservation,
        )
        thermodynamics, thermodynamics_detail = self._thermodynamics_score(candidate)
        amplicon, amplicon_detail = self._amplicon_score(candidate)
        if specificity_score is not None and not 0 <= float(specificity_score) <= 100:
            raise InputValidationError(
                "特异性维度分必须为 0–100",
                details={"specificity_score": specificity_score},
            )
        penalty_items, total_penalty = self._penalties(
            candidate,
            include_conservation=include_conservation,
        )
        applied_penalty = min(total_penalty, PENALTY_CAP_PERCENT)
        dimensions = {
            "conservation": {
                "weight": SCORING_WEIGHTS["conservation"],
                "raw_score": conservation,
                **conservation_detail,
            },
            "thermodynamics": {
                "weight": SCORING_WEIGHTS["thermodynamics"],
                "raw_score": thermodynamics,
                **thermodynamics_detail,
            },
            "specificity": {
                "weight": SCORING_WEIGHTS["specificity"],
                "raw_score": None if specificity_score is None else float(specificity_score),
                "status": "NOT_EVALUATED" if specificity_score is None else "EVALUATED",
                "method": "PENDING_P2" if specificity_score is None else "P2_SPECIFICITY_SCORE",
            },
            "amplicon_features": {
                "weight": SCORING_WEIGHTS["amplicon_features"],
                "raw_score": amplicon,
                **amplicon_detail,
            },
        }
        values = {
            "conservation": conservation,
            "thermodynamics": thermodynamics,
            "specificity": (
                None
                if specificity_score is None
                else float(specificity_score)
            ),
            "amplicon_features": amplicon,
        }
        preliminary_names = [
            *(
                ["conservation"]
                if conservation is not None
                else []
            ),
            "thermodynamics",
            "amplicon_features",
        ]
        if thermodynamics is not None and amplicon is not None:
            preliminary_weights = self._effective_weights(
                preliminary_names
            )
            preliminary_base = sum(
                float(values[name]) * preliminary_weights[name]
                for name in preliminary_names
            )
            preliminary_final = preliminary_base * (
                1.0 - applied_penalty / 100.0
            )
        else:
            preliminary_weights = {}
            preliminary_base = None
            preliminary_final = None

        missing_dimensions = [
            name
            for name, value in (
                ("thermodynamics", thermodynamics),
                ("specificity", specificity_score),
                ("amplicon_features", amplicon),
            )
            if value is None
        ]
        if missing_dimensions:
            base_score = None
            final_score = None
            score_status = (
                "PENDING_SPECIFICITY"
                if missing_dimensions == ["specificity"]
                else "INCOMPLETE_DIMENSIONS"
            )
            final_names: list[str] = []
            effective_weights = preliminary_weights
        else:
            final_names = [
                *(
                    ["conservation"]
                    if conservation is not None
                    else []
                ),
                "thermodynamics",
                "specificity",
                "amplicon_features",
            ]
            effective_weights = self._effective_weights(final_names)
            base_score = sum(
                float(values[name]) * effective_weights[name]
                for name in final_names
            )
            final_score = base_score * (1.0 - applied_penalty / 100.0)
            score_status = "FINAL"
        participating_names = (
            final_names if final_names else preliminary_names
        )
        for name, detail in dimensions.items():
            detail["effective_weight"] = effective_weights.get(name, 0.0)
            detail["participates_in_score"] = name in participating_names
        return {
            "policy_version": SCORING_POLICY_VERSION,
            "policy_status": SCORING_POLICY_STATUS,
            "status": score_status,
            "scale": copy.deepcopy(SCORING_SCALE),
            "weights": copy.deepcopy(SCORING_WEIGHTS),
            "effective_weights": effective_weights,
            "dimensions": dimensions,
            "conservation": copy.deepcopy(dimensions["conservation"]),
            "conservation_scoring_mode": (
                "FOUR_DIMENSION"
                if conservation is not None
                else "RENORMALIZED_WITHOUT_CONSERVATION"
                if include_conservation
                else "DESIGN_ONLY_EXCLUDED"
            ),
            "base_score": (
                None
                if base_score is None
                else round(base_score, CALCULATION_DECIMALS)
            ),
            "penalties": penalty_items,
            "total_penalty_percent": round(
                total_penalty,
                CALCULATION_DECIMALS,
            ),
            "penalty_cap_percent": PENALTY_CAP_PERCENT,
            "applied_penalty_percent": round(
                applied_penalty,
                CALCULATION_DECIMALS,
            ),
            "penalty_application": (
                "final_score = max(0, base_score × "
                "(1 - min(additive_penalty_percent,100)/100))"
            ),
            "missing_dimensions": missing_dimensions,
            "calculation_decimals": CALCULATION_DECIMALS,
            "final_score": (
                None
                if final_score is None
                else round(_clamp(final_score), CALCULATION_DECIMALS)
            ),
            "ranking_contract": {
                "top_n": FIXED_TOP_CANDIDATES,
                "tie_breakers": list(FINAL_RANK_TIE_BREAKERS),
            },
            "pre_specificity_priority": {
                "status": "ORDERING_ONLY_NOT_FINAL_SCORE",
                "participating_dimensions": preliminary_names,
                "effective_weights": preliminary_weights,
                "evaluated_weight": round(
                    sum(
                        SCORING_WEIGHTS[name]
                        for name in preliminary_names
                    ),
                    CALCULATION_DECIMALS,
                ),
                "base": (
                    None
                    if preliminary_base is None
                    else round(preliminary_base, CALCULATION_DECIMALS)
                ),
                "after_penalties": None
                if preliminary_final is None
                else round(
                    _clamp(preliminary_final),
                    CALCULATION_DECIMALS,
                ),
            },
        }


class WF01V02M3Engine:
    """Summarise one target after every M2 Primer3 unit has reached a terminal state."""

    def __init__(self) -> None:
        self.scoring = WF01V02ScoringEngine()

    @staticmethod
    def _pre_rank_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
        score = (
            candidate.get("metadata", {})
            .get("wf01_v02_scoring", {})
            .get("pre_specificity_priority", {})
            .get("after_penalties")
        )
        penalty = candidate.get("primer3_pair_penalty")
        return (
            -(float(score) if score is not None else -1.0),
            math.inf if penalty is None else float(penalty),
            str(candidate.get("dedupe_key") or ""),
        )

    def run_target(self, target: dict[str, Any]) -> dict[str, Any]:
        target_id = str(target.get("target_id") or "")
        m2 = target.get("m2_execution")
        if not isinstance(m2, dict):
            raise InputValidationError(
                "靶标缺少 M2 执行结果，不能进入 M3",
                details={"target_id": target_id},
            )
        manifest = m2.get("exploration_manifest")
        if not isinstance(manifest, dict) or not isinstance(manifest.get("units"), list):
            raise InputValidationError(
                "M2 缺少预生成 ExplorationManifest",
                details={"target_id": target_id},
            )
        units = manifest["units"]
        expected_count = int(manifest.get("expected_unit_count", -1))
        all_terminal = len(units) == expected_count and all(
            unit.get("status") in {"COMPLETED", "TOOL_ERROR"} for unit in units
        )
        all_successful = all_terminal and bool(units) and all(
            unit.get("status") == "COMPLETED" for unit in units
        )
        candidate_occurrences = [
            copy.deepcopy(candidate)
            for bucket in (
                "hard_rule_pass_candidates",
                "hard_rule_rejected_candidates",
                "hard_rule_incomplete_candidates",
            )
            for candidate in m2.get(bucket) or []
        ]
        unique_candidates, dedupe_summary = deduplicate_target_candidates(
            candidate_occurrences
        )
        eligible = [
            candidate
            for candidate in unique_candidates
            if candidate.get("verdict") == "AWAITING_SPECIFICITY"
        ]
        excluded = [
            candidate
            for candidate in unique_candidates
            if candidate.get("verdict") == "REJECTED_HARD_RULE"
        ]
        incomplete = [
            candidate
            for candidate in unique_candidates
            if candidate.get("verdict") == "HARD_RULES_NOT_EVALUATED"
        ]
        for candidate in eligible:
            scoring = self.scoring.score(candidate)
            candidate.setdefault("metadata", {})["wf01_v02_scoring"] = scoring
            candidate["score"] = None
            candidate["rank"] = None
        eligible.sort(key=self._pre_rank_key)
        for rank, candidate in enumerate(eligible, start=1):
            candidate["metadata"]["pre_specificity_rank"] = rank
        provisional_top10 = copy.deepcopy(eligible[:FIXED_TOP_CANDIDATES])
        for rank, candidate in enumerate(provisional_top10, start=1):
            candidate["provisional_rank"] = rank

        generated_after_tenth = False
        if len(candidate_occurrences) > FIXED_TOP_CANDIDATES and len(units) > 1:
            cumulative = 0
            tenth_unit_index = None
            for index, unit in enumerate(units):
                cumulative += int((unit.get("counts") or {}).get("generated", 0))
                if cumulative >= FIXED_TOP_CANDIDATES:
                    tenth_unit_index = index
                    break
            generated_after_tenth = (
                tenth_unit_index is not None
                and any(
                    int((unit.get("counts") or {}).get("generated", 0)) > 0
                    for unit in units[tenth_unit_index + 1 :]
                )
            )

        return {
            "stage": "M3_COMPLETE_AWAITING_M4",
            "engine_version": M3_ENGINE_VERSION,
            "target_id": target_id,
            "manifest_validation": {
                "manifest_id": manifest.get("manifest_id"),
                "manifest_sha256": manifest.get("manifest_sha256"),
                "policy_version": manifest.get("policy_version"),
                "policy_status": manifest.get("policy_status"),
                "created_before_execution": manifest.get("created_before_execution") is True,
                "expected_unit_count": expected_count,
                "terminal_unit_count": sum(
                    unit.get("status") in {"COMPLETED", "TOOL_ERROR"} for unit in units
                ),
                "completed_unit_count": sum(unit.get("status") == "COMPLETED" for unit in units),
                "tool_error_unit_count": sum(unit.get("status") == "TOOL_ERROR" for unit in units),
                "all_units_terminal": all_terminal,
                "all_units_successful": all_successful,
                "generated_candidates_after_tenth_occurrence": generated_after_tenth,
                "candidate_count_did_not_control_termination": len(units) == expected_count,
            },
            "primer3_search_complete": all_successful,
            "search_complete": False,
            "search_incomplete_reason": "M4_SPECIFICITY_NOT_RUN"
            if all_successful
            else "PRIMER3_EXPLORATION_UNIT_INCOMPLETE",
            "deduplication": dedupe_summary,
            "candidate_counts": {
                "generated_occurrences": len(candidate_occurrences),
                "unique_candidates": len(unique_candidates),
                "duplicates_collapsed": dedupe_summary["duplicates_collapsed"],
                "hard_rule_pass_awaiting_specificity": len(eligible),
                "hard_rule_rejected": len(excluded),
                "hard_rule_not_evaluated": len(incomplete),
                "provisional_top10": len(provisional_top10),
            },
            "scoring_contract": {
                "policy_version": SCORING_POLICY_VERSION,
                "weights": copy.deepcopy(SCORING_WEIGHTS),
                "conservation_in_score": True,
                "conservation_fallback": (
                    "RENORMALIZE_REMAINING_DIMENSIONS_WITHOUT_FAKE_SCORE"
                ),
                "soft_penalty_method": "ADDITIVE",
                "final_score_status": "PENDING_M4_SPECIFICITY",
            },
            "ranking_status": "PROVISIONAL_PENDING_M4_SPECIFICITY",
            "fixed_top_candidates": FIXED_TOP_CANDIDATES,
            "top10_is_final": False,
            "provisional_top10": provisional_top10,
            "all_hard_rule_pass_candidates": eligible,
            "all_hard_rule_rejected_candidates": excluded,
            "all_hard_rule_incomplete_candidates": incomplete,
        }

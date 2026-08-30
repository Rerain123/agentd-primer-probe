"""Pure PRD-v0.2 ordinary-TaqMan target execution for WF-01 M2.

M2 aligns multiple templates with MAFFT, selects conserved regions in Python,
then runs Primer3 for each conserved-region/parameter-profile unit.  A single
template bypasses MAFFT and goes directly to Primer3.  Batch persistence,
specificity, deduplication, scoring and Top10 remain owned by later stages.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .alignment import (
    MafftAdapter,
    build_conservation_profile,
    candidate_conservation_evidence,
    find_conserved_regions,
    public_conservation_profile,
)
from .errors import AgentDError
from .models import Candidate, RuleResult, RuleStatus, SequenceRecord
from .primer3_tool import Primer3Adapter


PRD_VERSION = "0.2"
M2_ENGINE_VERSION = "wf01-v0.2-m2.4-mafft-conservation"
M2_PRIMER3_NUM_RETURN = 50
EXPLORATION_POLICY_VERSION = "wf01-v0.2-conserved-region-v2"
HARD_RULE_CODES = frozenset(f"R{number:02d}" for number in range(1, 11))
SOFT_RULE_PENALTIES_PERCENT = {
    "R01_SOFT": 5,
    "R03_SOFT": 2,
    "R11": 3,
    "R12": 3,
}
REQUIRED_THERMO_METADATA = (
    "software",
    "software_version",
    "tm_algorithm",
    "mv_conc_mm",
    "dv_conc_mm",
    "dntp_conc_mm",
    "oligo_conc_nm",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result(
    code: str,
    category: str,
    status: RuleStatus,
    actual: Any,
    threshold: str,
    source: str,
    message: str = "",
) -> RuleResult:
    return RuleResult(
        code=code,
        category=category,
        status=status,
        actual=actual,
        threshold=threshold,
        source=source,
        message=message,
    )


def _not_evaluated(
    code: str,
    category: str,
    threshold: str,
    source: str,
    message: str,
    *,
    actual: Any = None,
) -> RuleResult:
    return _result(code, category, RuleStatus.NOT_EVALUATED, actual, threshold, source, message)


class WF01V02RuleEngine:
    """Evaluate ordinary-TaqMan R01-R21 states without legacy rule leakage."""

    @staticmethod
    def _hard_status(candidate: Candidate) -> str:
        hard = [rule for rule in candidate.rules if rule.code in HARD_RULE_CODES]
        if any(rule.status == RuleStatus.FAIL for rule in hard):
            return "REJECTED_HARD_RULE"
        if len(hard) != len(HARD_RULE_CODES) or any(rule.status != RuleStatus.PASS for rule in hard):
            return "HARD_RULES_NOT_EVALUATED"
        return "AWAITING_SPECIFICITY"

    @staticmethod
    def _rule_map(candidate: Candidate) -> dict[str, RuleResult]:
        return {rule.code: rule for rule in candidate.rules}

    @staticmethod
    def _conservation_rule(candidate: Candidate) -> RuleResult:
        evidence = copy.deepcopy(
            candidate.metadata.get("conservation_evidence") or {}
        )
        if evidence.get("status") != "EVALUATED":
            return _not_evaluated(
                "R16",
                "conservation",
                "MAFFT 多序列保守区及候选结合位点证据已完成",
                "C5/MAFFT + WF01 conservation analysis",
                str(
                    evidence.get("reason")
                    or "未完成多序列保守性评估"
                ),
                actual=evidence,
            )
        score = evidence.get("score")
        confidence = str(evidence.get("confidence") or "LOW")
        status = (
            RuleStatus.PASS
            if score is not None
            and float(score) >= 0.95
            and confidence == "HIGH"
            else RuleStatus.WARNING
        )
        return _result(
            "R16",
            "conservation",
            status,
            evidence,
            "优先 conservation≥0.95、连续区≥150 bp 且代表序列 N≥20；"
            "放宽区或低样本量保留为 WARNING",
            "C5/MAFFT + WF01 Shannon conservation",
            (
                "候选结合位点达到高置信度保守性要求"
                if status == RuleStatus.PASS
                else "保守性已评估，但使用放宽区域、全长回退或代表序列不足"
            ),
        )

    def evaluate(self, candidate: Candidate) -> list[RuleResult]:
        rules: list[RuleResult] = []
        primers = {
            "forward": candidate.forward_primer,
            "reverse": candidate.reverse_primer,
        }
        primer_tms = {name: oligo.tm_celsius for name, oligo in primers.items()}

        if any(value is None for value in primer_tms.values()):
            rules.append(
                _not_evaluated(
                    "R01",
                    "hard",
                    "两条引物 Tm 均为 50–65°C，且 ΔTm≤2°C",
                    "P1.calc_tm",
                    "缺少引物 Tm，不能判定硬规则通过",
                    actual=primer_tms,
                )
            )
            rules.append(
                _not_evaluated(
                    "R01_SOFT",
                    "soft",
                    "任一引物 Tm<55°C 时 WARNING（-5%）",
                    "P1.calc_tm",
                    "缺少引物 Tm",
                    actual=primer_tms,
                )
            )
        else:
            forward_tm = float(primer_tms["forward"])
            reverse_tm = float(primer_tms["reverse"])
            delta_tm = abs(forward_tm - reverse_tm)
            r01_actual = {
                "forward_tm_celsius": round(forward_tm, 6),
                "reverse_tm_celsius": round(reverse_tm, 6),
                "delta_tm_celsius": round(delta_tm, 6),
            }
            hard_pass = 50 <= forward_tm <= 65 and 50 <= reverse_tm <= 65 and delta_tm <= 2
            rules.append(
                _result(
                    "R01",
                    "hard",
                    RuleStatus.PASS if hard_pass else RuleStatus.FAIL,
                    r01_actual,
                    "两条引物 Tm 均为 50–65°C，且 ΔTm≤2°C",
                    "P1.calc_tm",
                )
            )
            low_primers = [name for name, value in primer_tms.items() if float(value) < 55]
            rules.append(
                _result(
                    "R01_SOFT",
                    "soft",
                    RuleStatus.WARNING if low_primers else RuleStatus.PASS,
                    {"primer_names_below_55": low_primers, **r01_actual},
                    "任一引物 Tm<55°C 时 WARNING（-5%）",
                    "P1.calc_tm",
                )
            )

        amplicon_length = candidate.length_bp if candidate.amplicon_sequence_5to3 else None
        if amplicon_length is None:
            rules.append(
                _not_evaluated(
                    "R02",
                    "hard",
                    "扩增子长度 70–200 bp",
                    "P1.design_primers/模板坐标",
                    "扩增子未定位",
                )
            )
            rules.append(
                _not_evaluated(
                    "R02_SOFT",
                    "soft",
                    "扩增子恰为 70 bp 时标记边界警戒",
                    "P1.design_primers/模板坐标",
                    "扩增子未定位",
                )
            )
        else:
            rules.append(
                _result(
                    "R02",
                    "hard",
                    RuleStatus.PASS if 70 <= amplicon_length <= 200 else RuleStatus.FAIL,
                    {"length_bp": amplicon_length},
                    "扩增子长度 70–200 bp",
                    "P1.design_primers/模板坐标",
                )
            )
            rules.append(
                _result(
                    "R02_SOFT",
                    "soft",
                    RuleStatus.WARNING if amplicon_length == 70 else RuleStatus.PASS,
                    {"length_bp": amplicon_length, "amplicon_feature_degraded": amplicon_length == 70},
                    "扩增子恰为 70 bp 时标记边界警戒",
                    "WF01-v0.2规则引擎",
                    "该警戒只降级后续扩增子特征项，不改变硬规则阈值",
                )
            )

        r03_failures: list[dict[str, Any]] = []
        r03_warnings: list[dict[str, Any]] = []
        for name, oligo in primers.items():
            sequence = oligo.sequence_5to3.upper()
            last5 = sequence[-5:]
            last5_gc = last5.count("G") + last5.count("C")
            has_g4 = "GGGG" in sequence
            if last5_gc > 2 or has_g4:
                r03_failures.append(
                    {
                        "primer": name,
                        "last5": last5,
                        "last5_gc_count": last5_gc,
                        "contains_consecutive_g4": has_g4,
                    }
                )
            triple_repeat = any(base * 3 in last5 for base in "ACGT")
            if last5_gc == 0 or triple_repeat:
                r03_warnings.append(
                    {
                        "primer": name,
                        "last5": last5,
                        "last5_gc_count": last5_gc,
                        "contains_triple_repeat": triple_repeat,
                    }
                )
        rules.append(
            _result(
                "R03",
                "hard",
                RuleStatus.FAIL if r03_failures else RuleStatus.PASS,
                {"failures": r03_failures},
                "每条引物末 5 nt 的 G+C≤2，且整条引物不得含连续≥4个 G",
                "WF01-v0.2规则引擎",
            )
        )
        rules.append(
            _result(
                "R03_SOFT",
                "soft",
                RuleStatus.WARNING if r03_warnings else RuleStatus.PASS,
                {"warnings": r03_warnings},
                "末 5 nt 无 G/C 或含连续 3 个相同碱基时 WARNING（-2%）",
                "WF01-v0.2规则引擎",
            )
        )

        primer_lengths = {name: oligo.length_nt for name, oligo in primers.items()}
        rules.append(
            _result(
                "R04",
                "hard",
                RuleStatus.PASS if all(18 <= length <= 28 for length in primer_lengths.values()) else RuleStatus.FAIL,
                primer_lengths,
                "两条引物长度均为 18–28 nt",
                "P1.design_primers",
            )
        )

        probe = candidate.probe
        if probe is None:
            for code, threshold, source in (
                ("R05", "普通 TaqMan 探针 5′ 端不得为 G", "P1.design_primers"),
                ("R06", "探针位于两引物之间、不重叠，且与两侧引物间隔均≥5 bp", "P1坐标"),
                ("R07", "探针 Tm 比两条引物平均 Tm 高 8–10°C", "P1.calc_tm"),
                ("R08", "普通 TaqMan 探针长度 22–32 nt", "P1.design_primers"),
            ):
                rules.append(_not_evaluated(code, "hard", threshold, source, "普通 TaqMan 候选缺少探针"))
        else:
            first_base = probe.sequence_5to3[:1].upper()
            rules.append(
                _result(
                    "R05",
                    "hard",
                    RuleStatus.PASS if first_base and first_base != "G" else RuleStatus.FAIL,
                    {"probe_5_prime_base": first_base},
                    "普通 TaqMan 探针 5′ 端不得为 G",
                    "WF01-v0.2规则引擎",
                )
            )

            positions = {
                "forward_end": candidate.forward_primer.position_end,
                "probe_start": probe.position_start,
                "probe_end": probe.position_end,
                "reverse_start": candidate.reverse_primer.position_start,
            }
            if any(value is None for value in positions.values()):
                rules.append(
                    _not_evaluated(
                        "R06",
                        "hard",
                        "探针位于两引物之间、不重叠，且与两侧引物间隔均≥5 bp",
                        "P1坐标",
                        "引物或探针坐标不完整",
                        actual=positions,
                    )
                )
            else:
                left_gap = int(probe.position_start) - int(candidate.forward_primer.position_end) - 1
                right_gap = int(candidate.reverse_primer.position_start) - int(probe.position_end) - 1
                ordered = (
                    int(candidate.forward_primer.position_end) < int(probe.position_start)
                    and int(probe.position_end) < int(candidate.reverse_primer.position_start)
                )
                rules.append(
                    _result(
                        "R06",
                        "hard",
                        RuleStatus.PASS if ordered and left_gap >= 5 and right_gap >= 5 else RuleStatus.FAIL,
                        {"ordered_non_overlapping": ordered, "left_gap_bp": left_gap, "right_gap_bp": right_gap},
                        "探针位于两引物之间、不重叠，且与两侧引物间隔均≥5 bp",
                        "P1坐标",
                    )
                )

            if probe.tm_celsius is None or any(value is None for value in primer_tms.values()):
                rules.append(
                    _not_evaluated(
                        "R07",
                        "hard",
                        "探针 Tm 比两条引物平均 Tm 高 8–10°C",
                        "P1.calc_tm",
                        "引物或探针 Tm 不完整",
                        actual={"probe_tm_celsius": probe.tm_celsius, **primer_tms},
                    )
                )
            else:
                mean_primer_tm = sum(float(value) for value in primer_tms.values()) / 2
                probe_delta = round(
                    float(probe.tm_celsius) - mean_primer_tm,
                    6,
                )
                rules.append(
                    _result(
                        "R07",
                        "hard",
                        RuleStatus.PASS if 8 <= probe_delta <= 10 else RuleStatus.FAIL,
                        {
                            "probe_tm_celsius": round(float(probe.tm_celsius), 6),
                            "mean_primer_tm_celsius": round(mean_primer_tm, 6),
                            "delta_tm_celsius": probe_delta,
                        },
                        "探针 Tm 比两条引物平均 Tm 高 8–10°C",
                        "P1.calc_tm",
                    )
                )
            rules.append(
                _result(
                    "R08",
                    "hard",
                    RuleStatus.PASS if 22 <= probe.length_nt <= 32 else RuleStatus.FAIL,
                    {"probe_length_nt": probe.length_nt},
                    "普通 TaqMan 探针长度 22–32 nt",
                    "P1.design_primers",
                )
            )

        self_dimer_values = {
            name: oligo.three_prime_dimer_dg_kcal for name, oligo in primers.items()
        }
        if any(value is None for value in self_dimer_values.values()):
            rules.append(
                _not_evaluated(
                    "R09",
                    "hard",
                    "任一引物 3′ 自二聚体 ΔG<-6.0 kcal/mol 时排除",
                    "P1.calc_homodimer(3′端)",
                    "至少一条引物缺少 3′ 自二聚体热力学结果",
                    actual=self_dimer_values,
                )
            )
        else:
            rules.append(
                _result(
                    "R09",
                    "hard",
                    RuleStatus.FAIL
                    if any(float(value) < -6 for value in self_dimer_values.values())
                    else RuleStatus.PASS,
                    {
                        **self_dimer_values,
                        "strong_3_prime_self_complementarity_assessment": "primer3_3_prime_homodimer_dg",
                    },
                    "任一引物 3′ 自二聚体 ΔG<-6.0 kcal/mol 时排除",
                    "P1.calc_homodimer(3′端)",
                )
            )

        if (
            candidate.three_prime_heterodimer_dg_kcal is None
            or candidate.three_prime_involved is None
        ):
            rules.append(
                _not_evaluated(
                    "R10",
                    "hard",
                    "涉及 3′ 端且异二聚体 ΔG<-9.0 kcal/mol 时排除",
                    "P1.calc_heterodimer(3′端)",
                    "缺少 3′ 异二聚体 ΔG 或 3′ 参与判定",
                    actual={
                        "dg_kcal": candidate.three_prime_heterodimer_dg_kcal,
                        "three_prime_involved": candidate.three_prime_involved,
                    },
                )
            )
        else:
            r10_fail = (
                candidate.three_prime_involved is True
                and float(candidate.three_prime_heterodimer_dg_kcal) < -9
            )
            rules.append(
                _result(
                    "R10",
                    "hard",
                    RuleStatus.FAIL if r10_fail else RuleStatus.PASS,
                    {
                        "dg_kcal": candidate.three_prime_heterodimer_dg_kcal,
                        "three_prime_involved": candidate.three_prime_involved,
                    },
                    "涉及 3′ 端且异二聚体 ΔG<-9.0 kcal/mol 时排除",
                    "P1.calc_heterodimer(3′端)",
                )
            )

        all_oligos = {**primers, **({"probe": probe} if probe is not None else {})}
        hairpin_values = {name: oligo.hairpin_dg_kcal for name, oligo in all_oligos.items()}
        if any(value is None for value in hairpin_values.values()):
            rules.append(
                _not_evaluated(
                    "R11",
                    "soft",
                    "任一寡核苷酸发卡 ΔG<-3.0 kcal/mol 时 WARNING（-3%）",
                    "P1.calc_hairpin",
                    "至少一个寡核苷酸缺少发卡热力学结果",
                    actual=hairpin_values,
                )
            )
        else:
            rules.append(
                _result(
                    "R11",
                    "soft",
                    RuleStatus.WARNING
                    if any(float(value) < -3 for value in hairpin_values.values())
                    else RuleStatus.PASS,
                    hairpin_values,
                    "任一寡核苷酸发卡 ΔG<-3.0 kcal/mol 时 WARNING（-3%）",
                    "P1.calc_hairpin",
                )
            )

        non_three_prime_dimers = {
            "forward_homodimer": candidate.forward_primer.homodimer_dg_kcal,
            "reverse_homodimer": candidate.reverse_primer.homodimer_dg_kcal,
            "primer_pair_heterodimer": candidate.pair_heterodimer_dg_kcal,
        }
        if any(value is None for value in non_three_prime_dimers.values()):
            rules.append(
                _not_evaluated(
                    "R12",
                    "soft",
                    "非 3′ 二聚体 -9.0≤ΔG<-6.0 kcal/mol 时 WARNING（-3%）",
                    "P1.calc_homodimer/calc_heterodimer",
                    "至少一个非 3′ 二聚体热力学结果缺失",
                    actual=non_three_prime_dimers,
                )
            )
        else:
            weak = {
                name: value
                for name, value in non_three_prime_dimers.items()
                if -9 <= float(value) < -6
            }
            rules.append(
                _result(
                    "R12",
                    "soft",
                    RuleStatus.WARNING if weak else RuleStatus.PASS,
                    {"all_dg_kcal": non_three_prime_dimers, "warning_dg_kcal": weak},
                    "非 3′ 二聚体 -9.0≤ΔG<-6.0 kcal/mol 时 WARNING（-3%）",
                    "P1.calc_homodimer/calc_heterodimer",
                )
            )

        rules.extend(
            [
                _result(
                    "R13",
                    "scope",
                    RuleStatus.NOT_APPLICABLE,
                    None,
                    "MGB 探针规则",
                    "PRD v0.2 scope",
                    "本期只设计普通 TaqMan 探针，不启用 MGB",
                ),
                _result(
                    "R14",
                    "scope",
                    RuleStatus.NOT_APPLICABLE,
                    None,
                    "多重荧光通道规则",
                    "PRD v0.2 scope",
                    "WF-01 本期不做多重设计",
                ),
                _result(
                    "R15",
                    "scope",
                    RuleStatus.NOT_APPLICABLE,
                    None,
                    "内标规则",
                    "PRD v0.2 scope",
                    "WF-01 本期不做内标设计",
                ),
                self._conservation_rule(candidate),
                _result(
                    "R17",
                    "scope",
                    RuleStatus.NOT_APPLICABLE,
                    None,
                    "多重扩增子规则",
                    "PRD v0.2 scope",
                    "WF-01 本期不做多重设计",
                ),
            ]
        )
        missing_metadata = [
            field for field in REQUIRED_THERMO_METADATA if candidate.metadata.get(field) in (None, "")
        ]
        rules.append(
            _result(
                "R18",
                "metadata",
                RuleStatus.FAIL if missing_metadata else RuleStatus.PASS,
                {
                    "missing_fields": missing_metadata,
                    "metadata": {
                        field: candidate.metadata.get(field) for field in REQUIRED_THERMO_METADATA
                    },
                },
                "热力学软件、版本、算法和浓度元数据完整",
                "P1 metadata",
            )
        )
        rules.append(
            _not_evaluated(
                "R19",
                "specificity",
                "靶标命中和背景证据完整；指定宿主时追加宿主排除证据",
                "M4 specificity engine",
                "M2 尚未运行适用范围内的特异性检查，不得判为 PASS",
            )
        )
        rules.append(
            _result(
                "R20",
                "metadata",
                RuleStatus.PASS,
                {
                    "evidence_level": candidate.evidence_level.value,
                    "experimental_validation": False,
                },
                "明确证据等级和实验验证状态",
                "canonical candidate metadata",
            )
        )
        completeness = {
            "forward_primer": bool(candidate.forward_primer.sequence_5to3),
            "reverse_primer": bool(candidate.reverse_primer.sequence_5to3),
            "probe": bool(candidate.probe and candidate.probe.sequence_5to3),
            "amplicon": bool(candidate.amplicon_sequence_5to3),
            "thermodynamics": not missing_metadata,
            "specificity": False,
            "conservation": (
                (
                    candidate.metadata.get("conservation_evidence") or {}
                ).get("status")
                in {"EVALUATED", "NOT_EVALUATED"}
            ),
            "amplicon_secondary_structure": False,
        }
        rules.append(
            _result(
                "R21",
                "metadata",
                RuleStatus.NOT_EVALUATED,
                completeness,
                "完整报告字段齐全",
                "M6 report assembler",
                "M2 仅完成候选生成和规则计算，完整报告字段尚未汇总",
            )
        )
        return rules

    def apply(self, candidate: Candidate) -> Candidate:
        candidate.rules = self.evaluate(candidate)
        status = self._hard_status(candidate)
        rule_map = self._rule_map(candidate)
        warning_codes = [
            rule.code
            for rule in candidate.rules
            if rule.status in {RuleStatus.WARNING, RuleStatus.NOT_EVALUATED}
            and rule.category == "soft"
        ]
        scientific_warning_codes = [
            rule.code
            for rule in candidate.rules
            if rule.status == RuleStatus.WARNING
            and rule.category == "conservation"
        ]
        penalty_codes = [
            code
            for code in warning_codes
            if code in SOFT_RULE_PENALTIES_PERCENT
        ]
        candidate.warnings = sorted(
            set(
                candidate.warnings
                + warning_codes
                + scientific_warning_codes
            )
        )
        candidate.verdict = status
        candidate.metadata["wf01_v02_rule_summary"] = {
            "hard_rule_status": status,
            "hard_failed_rules": [
                code
                for code in sorted(HARD_RULE_CODES)
                if rule_map.get(code) and rule_map[code].status == RuleStatus.FAIL
            ],
            "hard_not_evaluated_rules": [
                code
                for code in sorted(HARD_RULE_CODES)
                if not rule_map.get(code) or rule_map[code].status == RuleStatus.NOT_EVALUATED
            ],
            "soft_warning_rules": warning_codes,
            "fixed_soft_penalty_percent": sum(
                SOFT_RULE_PENALTIES_PERCENT[code] for code in penalty_codes
            ),
            "amplicon_feature_degraded": (
                rule_map.get("R02_SOFT") is not None
                and rule_map["R02_SOFT"].status == RuleStatus.WARNING
            ),
            "awaiting_specificity": status == "AWAITING_SPECIFICITY",
        }
        return candidate


def _canonical_parameters(parameters: dict[str, Any]) -> str:
    return json.dumps(parameters, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_parameter_runs(
    target_parameters: dict[str, Any],
    default_parameters: dict[str, Any],
    parameter_profiles: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build user/default/L1/L2 runs and merge identical parameter profiles."""
    user_overrides = target_parameters.get("user_overrides") or {}
    effective_user = copy.deepcopy(
        target_parameters.get("effective_user_profile") or default_parameters
    )
    requested: list[tuple[str, dict[str, Any]]] = []
    if user_overrides:
        requested.append(("user", effective_user))
    requested.extend(
        (label, copy.deepcopy(parameter_profiles[label]))
        for label in ("default", "L1", "L2")
    )

    unique: dict[str, dict[str, Any]] = {}
    for label, parameters in requested:
        key = _canonical_parameters(parameters)
        if key not in unique:
            unique[key] = {
                "run_id": f"run-{len(unique) + 1:03d}",
                "profile_labels": [label],
                "parameters": parameters,
            }
        else:
            unique[key]["profile_labels"].append(label)
    return list(unique.values())


class WF01V02TargetEngine:
    """Run the M2 pure target engine; callers own persistence and retry policy."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        default_parameters: dict[str, Any],
        parameter_profiles: dict[str, dict[str, Any]],
        allow_development_fallback: bool = False,
        primer3_adapter: Primer3Adapter | None = None,
        mafft_adapter: MafftAdapter | None = None,
    ) -> None:
        self.default_parameters = copy.deepcopy(default_parameters)
        self.parameter_profiles = copy.deepcopy(parameter_profiles)
        execution_config = copy.deepcopy(config)
        execution_config["maximum_candidates"] = M2_PRIMER3_NUM_RETURN
        execution_config["standard_design"] = self._primer3_parameters(self.default_parameters)
        self.primer3 = primer3_adapter or Primer3Adapter(
            execution_config,
            allow_fallback=allow_development_fallback,
        )
        self.mafft = mafft_adapter or MafftAdapter(
            str(config.get("tools", {}).get("mafft_executable", "mafft")),
            allow_fallback=allow_development_fallback,
        )
        self.conservation_rounds = copy.deepcopy(config.get("conservation_rounds") or [])
        self.rules = WF01V02RuleEngine()

    @staticmethod
    def _template_records(target: dict[str, Any]) -> list[SequenceRecord]:
        raw_templates = target.get("templates")
        if not isinstance(raw_templates, list) or not raw_templates:
            raw_templates = [target.get("template") or {}]
        records: list[SequenceRecord] = []
        for index, template in enumerate(raw_templates, start=1):
            if not isinstance(template, dict):
                continue
            sequence = str(template.get("sequence") or "")
            if not sequence:
                continue
            records.append(
                SequenceRecord(
                    id=str(template.get("record_name") or f"sequence_{index}"),
                    sequence=sequence,
                    description=str(template.get("description") or ""),
                    source=str(template.get("source_file") or "inline"),
                )
            )
        return records

    @staticmethod
    def _shift_candidate(
        candidate: Candidate,
        *,
        offset: int,
        region_index: int,
        region: dict[str, Any],
        conservation_method: str,
        sequence_count: int,
    ) -> Candidate:
        candidate.candidate_id = f"{candidate.candidate_id}-region-{region_index:03d}"
        candidate.amplicon_start += offset
        candidate.amplicon_end += offset
        for oligo in (candidate.forward_primer, candidate.reverse_primer, candidate.probe):
            if oligo is None:
                continue
            if oligo.position_start is not None:
                oligo.position_start += offset
            if oligo.position_end is not None:
                oligo.position_end += offset
        candidate.conservation_score = region.get("conservation_score")
        candidate.conservation_method = conservation_method
        candidate.conservation_sequence_count = sequence_count
        candidate.metadata.update(
            {
                "conservation_method": conservation_method,
                "conservation_sequence_count": sequence_count,
                "conservation_score": region.get("conservation_score"),
                "conserved_region": copy.deepcopy(region),
            }
        )
        return candidate

    @staticmethod
    def _primer3_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
        prepared = copy.deepcopy(parameters)
        prepared["probe_min_tm"] = (
            float(parameters["primer_min_tm"]) + float(parameters["probe_tm_delta_min"])
        )
        prepared["probe_opt_tm"] = (
            float(parameters["primer_opt_tm"])
            + (
                float(parameters["probe_tm_delta_min"])
                + float(parameters["probe_tm_delta_max"])
            )
            / 2
        )
        prepared["probe_max_tm"] = (
            float(parameters["primer_max_tm"]) + float(parameters["probe_tm_delta_max"])
        )
        return prepared

    def run_target(self, target: dict[str, Any]) -> dict[str, Any]:
        target_id = str(target["target_id"])
        records = self._template_records(target)
        if not records:
            raise AgentDError(
                "WF01 v0.2 M2 缺少可执行的模板序列",
                details={"target_id": target_id},
            )
        reference = records[0]
        template = reference.sequence
        source_records = [
            {
                "id": record.id,
                "description": record.description,
                "source": record.source,
                "length_nt": len(record.sequence),
                "checksum_sha256": hashlib.sha256(
                    record.sequence.encode("ascii", errors="ignore")
                ).hexdigest(),
                "role": "REFERENCE" if index == 0 else "ALIGNMENT_MEMBER",
            }
            for index, record in enumerate(records)
        ]
        conservation_profile: dict[str, Any] | None = None
        if len(records) == 1:
            aligned = records
            alignment_method = "single_sequence"
            alignment_warnings = [
                "仅提供1条序列，未执行 MAFFT，不能声称完成群体保守性验证"
            ]
            regions = [
                {
                    "start": 0,
                    "end": len(template) - 1,
                    "length": len(template),
                    "alignment_start": 0,
                    "alignment_end": len(template) - 1,
                    "alignment_length": len(template),
                    "sequence": template,
                    "consensus_sequence": template,
                    "conservation_score": None,
                    "mean_entropy_bits": None,
                    "maximum_entropy_bits": None,
                    "mean_coverage": None,
                    "minimum_coverage": None,
                    "variant_position_count": None,
                    "variant_positions": [],
                    "label": "single_sequence_full_length",
                    "selection_status": "NOT_EVALUATED_SINGLE_SEQUENCE",
                    "selection_round": None,
                    "selection_threshold": None,
                    "minimum_required_length": None,
                    "sequence_count": 1,
                    "confidence": "NOT_EVALUATED",
                    "low_confidence_reasons": [
                        "POPULATION_SEQUENCE_COUNT_BELOW_2"
                    ],
                    "full_length_fallback": False,
                }
            ]
            conservation_method = "not_evaluated_single_sequence"
            conservation_status = "NOT_EVALUATED"
            conservation_reason = "SINGLE_SEQUENCE_NO_POPULATION_CONSERVATION"
            mafft_status = "SKIPPED_SINGLE_SEQUENCE"
            raw_alignment_output = {
                "tool": "MAFFT",
                "status": "SKIPPED_SINGLE_SEQUENCE",
                "method": alignment_method,
                "input_records": [
                    {"id": record.id, "sequence": record.sequence}
                    for record in records
                ],
                "aligned_records": [
                    {"id": record.id, "sequence": record.sequence}
                    for record in records
                ],
            }
        else:
            aligned, alignment_method, alignment_warnings = self.mafft.align(records)
            raw_alignment_output = copy.deepcopy(
                getattr(
                    self.mafft,
                    "last_evidence",
                    {
                        "tool": "MAFFT",
                        "status": "COMPLETED",
                        "input_records": [
                            {"id": record.id, "sequence": record.sequence}
                            for record in records
                        ],
                        "aligned_records": [
                            {"id": record.id, "sequence": record.sequence}
                            for record in aligned
                        ],
                    },
                )
            )
            mafft_status = str(
                raw_alignment_output.get("status") or "COMPLETED"
            )
            if alignment_method == "mafft_shannon":
                conservation_profile = build_conservation_profile(aligned)
                regions, conservation_method = find_conserved_regions(
                    aligned,
                    self.conservation_rounds,
                )
                conservation_status = "EVALUATED"
                conservation_reason = "MAFFT_SHANNON_COMPLETED"
                raw_alignment_output["conservation_profile"] = (
                    public_conservation_profile(conservation_profile)
                )
                if len(records) < 20:
                    alignment_warnings = [
                        *alignment_warnings,
                        (
                            f"代表序列数量 N={len(records)}<20，"
                            "保守性置信度降低"
                        ),
                    ]
            else:
                conservation_method = alignment_method
                conservation_status = "NOT_EVALUATED"
                conservation_reason = (
                    "DEVELOPMENT_ALIGNMENT_FALLBACK_CANNOT_PROVE_CONSERVATION"
                )
                regions = [
                    {
                        "start": 0,
                        "end": len(template) - 1,
                        "length": len(template),
                        "alignment_start": 0,
                        "alignment_end": len(template) - 1,
                        "alignment_length": len(template),
                        "sequence": template,
                        "consensus_sequence": None,
                        "conservation_score": None,
                        "mean_entropy_bits": None,
                        "maximum_entropy_bits": None,
                        "mean_coverage": None,
                        "minimum_coverage": None,
                        "variant_position_count": None,
                        "variant_positions": [],
                        "label": "full_length_fallback",
                        "selection_status": "NOT_EVALUATED_TOOL_FALLBACK",
                        "selection_round": None,
                        "selection_threshold": None,
                        "minimum_required_length": None,
                        "sequence_count": len(records),
                        "confidence": "NOT_EVALUATED",
                        "low_confidence_reasons": [
                            "MAFFT_NOT_ACTUALLY_COMPLETED"
                        ],
                        "full_length_fallback": True,
                    }
                ]
        if not regions:
            raise AgentDError(
                "WF01 v0.2 M2 未找到可供 Primer3 设计的保守区",
                details={"target_id": target_id, "sequence_count": len(records)},
            )
        runs = build_parameter_runs(
            target.get("parameters") or {},
            self.default_parameters,
            self.parameter_profiles,
        )
        manifest_material = {
            "policy_version": EXPLORATION_POLICY_VERSION,
            "target_id": target_id,
            "template_checksum": target.get("template", {}).get("checksum_sha256"),
            "template_checksums": [
                hashlib.sha256(record.sequence.encode("ascii", errors="ignore")).hexdigest()
                for record in records
            ],
            "template_length_nt": len(template),
            "template_count": len(records),
            "source_records": source_records,
            "alignment_method": alignment_method,
            "mafft_status": mafft_status,
            "conservation_status": conservation_status,
            "conservation_method": conservation_method,
            "conservation_rounds": copy.deepcopy(self.conservation_rounds),
            "regions": regions,
            "parameter_runs": runs,
            "primer3_num_return_per_unit": M2_PRIMER3_NUM_RETURN,
        }
        manifest_digest = hashlib.sha256(
            json.dumps(
                manifest_material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        units: list[dict[str, Any]] = []
        for region_index, region in enumerate(regions, start=1):
            for run in runs:
                units.append(
                    {
                    "exploration_unit_id": f"{target_id}-region-{region_index:03d}-{run['run_id']}",
                    "stage": "M2_CONSERVED_REGION",
                    "region_index": region_index,
                    "region": {
                        "type": "WHOLE_TEMPLATE" if len(records) == 1 else "CONSERVED_REGION",
                        "start": int(region["start"]),
                        "end": int(region["end"]),
                        "length_nt": int(region["length"]),
                        "length": int(region["length"]),
                        "label": region.get("label"),
                        "conservation_score": region.get("conservation_score"),
                        "confidence": region.get("confidence"),
                        "selection_status": region.get("selection_status"),
                        "selection_round": region.get("selection_round"),
                        "selection_threshold": region.get(
                            "selection_threshold"
                        ),
                        "minimum_required_length": region.get(
                            "minimum_required_length"
                        ),
                        "variant_position_count": region.get(
                            "variant_position_count"
                        ),
                        "variant_positions": copy.deepcopy(
                            region.get("variant_positions") or []
                        ),
                        "mean_entropy_bits": region.get(
                            "mean_entropy_bits"
                        ),
                        "maximum_entropy_bits": region.get(
                            "maximum_entropy_bits"
                        ),
                        "mean_coverage": region.get("mean_coverage"),
                        "minimum_coverage": region.get(
                            "minimum_coverage"
                        ),
                        "sequence_count": region.get("sequence_count"),
                        "low_confidence_reasons": copy.deepcopy(
                            region.get("low_confidence_reasons") or []
                        ),
                        "full_length_fallback": region.get(
                            "full_length_fallback"
                        ),
                    },
                    "excluded_regions": [],
                    "parameter_run_id": run["run_id"],
                    "parameter_profile_labels": copy.deepcopy(run["profile_labels"]),
                    "effective_parameters": copy.deepcopy(run["parameters"]),
                    "primer3_parameters": self._primer3_parameters(run["parameters"]),
                    "primer3_num_return": M2_PRIMER3_NUM_RETURN,
                    "status": "PENDING",
                    "counts": {
                        "generated": 0,
                        "hard_rule_pass": 0,
                        "hard_rule_rejected": 0,
                        "hard_rule_not_evaluated": 0,
                    },
                    "candidate_ids": [],
                    "started_at": None,
                    "completed_at": None,
                    "tool_error": None,
                    }
                )
        exploration_manifest = {
            "manifest_id": f"{target_id}-{manifest_digest[:16]}",
            "manifest_sha256": manifest_digest,
            "policy_version": EXPLORATION_POLICY_VERSION,
            "policy_status": "ACTIVE",
            "region_policy": "SINGLE_TEMPLATE_FULL_LENGTH_OR_MAFFT_CONSERVED_REGIONS",
            "region_slice_step_nt": None,
            "primer3_num_return_per_unit": M2_PRIMER3_NUM_RETURN,
            "maximum_enumeration": len(units) * M2_PRIMER3_NUM_RETURN,
            "created_before_execution": True,
            "created_at": utc_now(),
            "expected_unit_count": len(units),
            "units": units,
        }

        all_candidates: list[Candidate] = []
        runs_by_id = {run["run_id"]: run for run in runs}
        for unit in units:
            run = runs_by_id[unit["parameter_run_id"]]
            unit_id = unit["exploration_unit_id"]
            primer3_parameters = unit["primer3_parameters"]
            region = unit["region"]
            region_start = int(region["start"])
            region_end = int(region["end"])
            region_sequence = template[region_start : region_end + 1]
            unit["status"] = "RUNNING"
            unit["started_at"] = utc_now()
            try:
                generated = self.primer3.design(
                    region_sequence,
                    sequence_id=f"{target_id}-{run['run_id']}",
                    parameters=primer3_parameters,
                )
            except AgentDError as exc:
                unit["primer3_raw_output"] = copy.deepcopy(
                    getattr(
                        self.primer3,
                        "last_design_evidence",
                        {
                            "tool": "Primer3",
                            "status": "TOOL_ERROR",
                            "error": exc.to_dict(),
                        },
                    )
                )
                unit["status"] = "TOOL_ERROR"
                unit["tool_error"] = exc.to_dict()
                unit["completed_at"] = utc_now()
                continue
            unit["primer3_raw_output"] = copy.deepcopy(
                getattr(
                    self.primer3,
                    "last_design_evidence",
                    {
                        "tool": "Primer3",
                        "status": "COMPLETED",
                        "raw_output": [candidate.to_dict() for candidate in generated],
                    },
                )
            )
            evaluated: list[Candidate] = []
            for raw_candidate in generated:
                candidate = self._shift_candidate(
                    raw_candidate,
                    offset=region_start,
                    region_index=int(unit["region_index"]),
                    region=region,
                    conservation_method=conservation_method,
                    sequence_count=len(records),
                )
                conservation_evidence = candidate_conservation_evidence(
                    conservation_profile,
                    intervals={
                        "forward_primer": (
                            (
                                int(candidate.forward_primer.position_start),
                                int(candidate.forward_primer.position_end),
                            )
                            if candidate.forward_primer.position_start
                            is not None
                            and candidate.forward_primer.position_end
                            is not None
                            else None
                        ),
                        "reverse_primer": (
                            (
                                int(candidate.reverse_primer.position_start),
                                int(candidate.reverse_primer.position_end),
                            )
                            if candidate.reverse_primer.position_start
                            is not None
                            and candidate.reverse_primer.position_end
                            is not None
                            else None
                        ),
                        "probe": (
                            (
                                int(candidate.probe.position_start),
                                int(candidate.probe.position_end),
                            )
                            if candidate.probe is not None
                            and candidate.probe.position_start is not None
                            and candidate.probe.position_end is not None
                            else None
                        ),
                    },
                    region=region,
                    status=conservation_status,
                    method=conservation_method,
                    reason=conservation_reason,
                )
                candidate.conservation_score = conservation_evidence.get(
                    "score"
                )
                candidate.conservation_method = conservation_method
                candidate.conservation_sequence_count = len(records)
                candidate.metadata.update(
                    {
                        "wf01_prd_version": PRD_VERSION,
                        "wf01_v02_engine_version": M2_ENGINE_VERSION,
                        "target_id": target_id,
                        "exploration_unit_id": unit_id,
                        "exploration_region": copy.deepcopy(unit["region"]),
                        "parameter_run_id": run["run_id"],
                        "parameter_profile_labels": copy.deepcopy(run["profile_labels"]),
                        "effective_parameters": copy.deepcopy(run["parameters"]),
                        "probe_tm_generation_window": {
                            "min_celsius": primer3_parameters["probe_min_tm"],
                            "opt_celsius": primer3_parameters["probe_opt_tm"],
                            "max_celsius": primer3_parameters["probe_max_tm"],
                            "final_relative_rule": "R07",
                        },
                        "specificity_status": "PENDING_M4",
                        "conservation_status": conservation_evidence["status"],
                        "conservation_evidence": conservation_evidence,
                        "amplicon_secondary_structure_status": "NOT_EVALUATED",
                    }
                )
                evaluated.append(self.rules.apply(candidate))
            all_candidates.extend(evaluated)
            unit_counts = {
                "generated": len(evaluated),
                "hard_rule_pass": sum(
                    candidate.verdict == "AWAITING_SPECIFICITY" for candidate in evaluated
                ),
                "hard_rule_rejected": sum(
                    candidate.verdict == "REJECTED_HARD_RULE" for candidate in evaluated
                ),
                "hard_rule_not_evaluated": sum(
                    candidate.verdict == "HARD_RULES_NOT_EVALUATED" for candidate in evaluated
                ),
            }
            unit["status"] = "COMPLETED"
            unit["counts"] = unit_counts
            unit["candidate_ids"] = [candidate.candidate_id for candidate in evaluated]
            unit["completed_at"] = utc_now()

        hard_pass = [
            candidate for candidate in all_candidates if candidate.verdict == "AWAITING_SPECIFICITY"
        ]
        hard_rejected = [
            candidate for candidate in all_candidates if candidate.verdict == "REJECTED_HARD_RULE"
        ]
        hard_incomplete = [
            candidate
            for candidate in all_candidates
            if candidate.verdict == "HARD_RULES_NOT_EVALUATED"
        ]
        return {
            "stage": "M2_RULES_COMPLETE_AWAITING_M3_M4",
            "engine_version": M2_ENGINE_VERSION,
            "target_id": target_id,
            "ordinary_taqman_only": True,
            "forbidden_modules_called": [],
            "primer3_status": self.primer3.status(),
            "mafft_status": mafft_status,
            "mafft_tool_status": self.mafft.status(),
            "alignment": {
                "status": (
                    "EVALUATED"
                    if alignment_method == "mafft_shannon"
                    else "NOT_EVALUATED"
                    if len(records) > 1
                    else "NOT_APPLICABLE"
                ),
                "sequence_count": len(records),
                "reference_id": reference.id,
                "source_records": source_records,
                "method": alignment_method,
                "alignment_width": (
                    len(aligned[0].sequence) if aligned else None
                ),
                "warnings": alignment_warnings,
                "raw_tool_output": raw_alignment_output,
            },
            "conservation": {
                "status": conservation_status,
                "reason": conservation_reason,
                "method": conservation_method,
                "sequence_count": len(records),
                "reference_id": reference.id,
                "representative_sequence_target": 20,
                "representative_sequence_count_sufficient": len(records) >= 20,
                "progressive_rounds": copy.deepcopy(
                    self.conservation_rounds
                ),
                "selected_rounds": sorted(
                    {
                        int(region["selection_round"])
                        for region in regions
                        if region.get("selection_round") is not None
                    }
                ),
                "full_length_fallback": any(
                    bool(region.get("full_length_fallback"))
                    for region in regions
                ),
                "profile_summary": (
                    {
                        key: value
                        for key, value in public_conservation_profile(
                            conservation_profile
                        ).items()
                        if key
                        not in {
                            "columns",
                            "reference_columns",
                            "reference_sequence",
                        }
                    }
                    if conservation_profile is not None
                    else None
                ),
                "regions": copy.deepcopy(regions),
            },
            "parameter_runs": runs,
            "exploration_manifest": exploration_manifest,
            "exploration_units": units,
            "primer3_manifest_complete": bool(units)
            and all(unit["status"] == "COMPLETED" for unit in units),
            "candidate_counts": {
                "generated": len(all_candidates),
                "hard_rule_pass": len(hard_pass),
                "hard_rule_rejected": len(hard_rejected),
                "hard_rule_not_evaluated": len(hard_incomplete),
                "awaiting_specificity": len(hard_pass),
            },
            "hard_rule_pass_candidates": [candidate.to_dict() for candidate in hard_pass],
            "hard_rule_rejected_candidates": [candidate.to_dict() for candidate in hard_rejected],
            "hard_rule_incomplete_candidates": [candidate.to_dict() for candidate in hard_incomplete],
            "search_complete": False,
            "search_incomplete_reason": "M3_FINITE_EXPLORATION_MANIFEST_NOT_RUN",
            "specificity_status": "PENDING_M4",
            "deduplication_status": "PENDING_M3",
            "scoring_status": "PENDING_M3",
            "top10_status": "PENDING_M3",
        }

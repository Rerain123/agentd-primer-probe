"""Agent D R-01 through R-21 rule engine.

Rules never manufacture missing measurements.  A required measurement that was
not produced by a scientific tool is reported as NOT_EVALUATED and causes the
candidate/workflow to be marked PARTIAL.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .models import Candidate, EvidenceLevel, RuleResult, RuleStatus


HARD_RULES = {"R-01", "R-02", "R-03", "R-04", "R-05", "R-06", "R-07", "R-08", "R-09", "R-10"}
BLOCKING_RULES = HARD_RULES | {"R-19"}
SOFT_RULES = {"R-01b", "R-03b", "R-11", "R-12", "R-13", "R-SUPPLEMENT"}


def _result(
    code: str,
    category: str,
    status: RuleStatus,
    actual: Any,
    threshold: str,
    source: str,
    message: str = "",
) -> RuleResult:
    return RuleResult(code, category, status, actual, threshold, source, message)


def _not_evaluated(code: str, category: str, threshold: str, source: str, message: str) -> RuleResult:
    return _result(code, category, RuleStatus.NOT_EVALUATED, None, threshold, source, message)


class RuleEngine:
    """Evaluate design, specificity and reporting rules."""

    def evaluate(self, candidate: Candidate) -> list[RuleResult]:
        rules: list[RuleResult] = []
        primers = [candidate.forward_primer, candidate.reverse_primer]
        primer_tms = [oligo.tm_celsius for oligo in primers]

        if any(value is None for value in primer_tms):
            rules.append(_not_evaluated("R-01", "hard", "Tm 50-65°C; ΔTm≤2°C", "P1", "缺少引物Tm"))
        else:
            delta = abs(float(primer_tms[0]) - float(primer_tms[1]))
            passed = all(50 <= float(value) <= 65 for value in primer_tms) and delta <= 2
            rules.append(
                _result(
                    "R-01", "hard", RuleStatus.PASS if passed else RuleStatus.FAIL,
                    {"forward_tm": primer_tms[0], "reverse_tm": primer_tms[1], "delta_tm": round(delta, 3)},
                    "Tm 50-65°C; ΔTm≤2°C", "P1.calc_tm",
                )
            )
            low = [round(float(value), 3) for value in primer_tms if float(value) < 55]
            rules.append(
                _result(
                    "R-01b", "soft", RuleStatus.WARNING if low else RuleStatus.PASS,
                    low, "Tm<55°C→WARNING", "P1.calc_tm",
                )
            )

        length = candidate.length_bp
        if not candidate.amplicon_sequence_5to3:
            rules.append(_not_evaluated("R-02", "hard", "推荐70-150 bp; 绝对50-200 bp", "P1/模板", "未提供或未定位模板扩增子"))
        else:
            if length < 50 or length > 200:
                length_status = RuleStatus.FAIL
            elif length < 70 or length > 150:
                length_status = RuleStatus.WARNING
            else:
                length_status = RuleStatus.PASS
            rules.append(_result("R-02", "hard", length_status, length, "推荐70-150 bp; 绝对50-200 bp", "P1.design_primers"))

        r03_failures: list[dict] = []
        r03b_warnings: list[dict] = []
        for name, oligo in zip(("forward", "reverse"), primers):
            end = oligo.sequence_5to3[-5:]
            end_gc = end.count("G") + end.count("C")
            if end_gc > 2 or "GGGG" in oligo.sequence_5to3:
                r03_failures.append({"primer": name, "last5": end, "gc_count": end_gc, "contains_GGGG": "GGGG" in oligo.sequence_5to3})
            max_run = max(Counter(run).most_common(1)[0][1] for run in [end])
            repeated = any(base * 3 in end for base in "ACGT")
            if end_gc == 0 or repeated:
                r03b_warnings.append({"primer": name, "last5": end, "gc_count": end_gc, "triple_repeat": repeated, "max_base_count": max_run})
        rules.append(_result("R-03", "hard", RuleStatus.FAIL if r03_failures else RuleStatus.PASS, r03_failures, "末5碱基G+C≤2且避免连续≥4个G", "WF规则引擎"))
        rules.append(_result("R-03b", "soft", RuleStatus.WARNING if r03b_warnings else RuleStatus.PASS, r03b_warnings, "无GC clamp或3'端连续≥3相同碱基→WARNING", "WF规则引擎"))

        bad_lengths = {name: oligo.length_nt for name, oligo in zip(("forward", "reverse"), primers) if not 18 <= oligo.length_nt <= 28}
        rules.append(_result("R-04", "hard", RuleStatus.FAIL if bad_lengths else RuleStatus.PASS, bad_lengths or {"forward": primers[0].length_nt, "reverse": primers[1].length_nt}, "18-28 nt", "P1.design_primers"))

        probe = candidate.probe
        if probe is None:
            rules.append(_not_evaluated("R-05", "hard", "探针5'端≠G", "P1", "TaqMan模式缺少探针"))
            rules.append(_not_evaluated("R-06", "hard", "探针与两侧引物间距均≥5 bp", "P1坐标", "缺少探针"))
            rules.append(_not_evaluated("R-07", "hard", "普通探针Tm比引物高8-10°C", "P1.calc_tm", "缺少探针"))
            rules.append(_not_evaluated("R-08", "hard", "普通探针18-30 nt", "P1", "缺少探针"))
        else:
            rules.append(_result("R-05", "hard", RuleStatus.PASS if not probe.sequence_5to3.startswith("G") else RuleStatus.FAIL, probe.sequence_5to3[0], "探针5'端≠G", "WF规则引擎"))
            if None in (primers[0].position_end, primers[1].position_start, probe.position_start, probe.position_end):
                rules.append(_not_evaluated("R-06", "hard", "探针与两侧引物间距均≥5 bp", "P1坐标", "坐标不完整"))
            else:
                left_gap = int(probe.position_start) - int(primers[0].position_end)
                right_gap = int(primers[1].position_start) - int(probe.position_end)
                rules.append(_result("R-06", "hard", RuleStatus.PASS if left_gap >= 5 and right_gap >= 5 else RuleStatus.FAIL, {"left_gap_bp": left_gap, "right_gap_bp": right_gap}, "左右间距均≥5 bp", "P1坐标"))
            if candidate.mgb_assessment:
                rules.append(_result("R-07", "hard", RuleStatus.NOT_APPLICABLE, None, "普通探针Tm比引物高8-10°C", "P1.calc_tm", "MGB探针改由R-13判定"))
            elif probe.tm_celsius is None or any(value is None for value in primer_tms):
                rules.append(_not_evaluated("R-07", "hard", "普通探针Tm比引物高8-10°C", "P1.calc_tm", "Tm不完整"))
            else:
                mean_primer_tm = sum(float(value) for value in primer_tms) / 2
                delta = float(probe.tm_celsius) - mean_primer_tm
                rules.append(_result("R-07", "hard", RuleStatus.PASS if 8 <= delta <= 10 else RuleStatus.FAIL, round(delta, 3), "探针Tm比平均引物Tm高8-10°C", "P1.calc_tm"))
            if candidate.mgb_assessment:
                rules.append(_result("R-08", "hard", RuleStatus.PASS if 13 <= probe.length_nt <= 20 else RuleStatus.FAIL, probe.length_nt, "MGB探针13-20 nt", "P3"))
            else:
                rules.append(_result("R-08", "hard", RuleStatus.PASS if 18 <= probe.length_nt <= 30 else RuleStatus.FAIL, probe.length_nt, "普通探针18-30 nt", "P1.design_primers"))

        end_self_values = {name: oligo.three_prime_dimer_dg_kcal for name, oligo in zip(("forward", "reverse"), primers)}
        measured_self = [value for value in end_self_values.values() if value is not None]
        if not measured_self:
            rules.append(_not_evaluated("R-09", "hard", "3'端自二聚体ΔG≥-6.0 kcal/mol", "P1.calc_homodimer(3'端)", "未获得3'端热力学结果"))
        else:
            rules.append(_result("R-09", "hard", RuleStatus.FAIL if any(value < -6 for value in measured_self) else RuleStatus.PASS, end_self_values, "3'端自二聚体ΔG≥-6.0 kcal/mol", "P1.calc_homodimer(3'端)"))

        if candidate.three_prime_heterodimer_dg_kcal is None:
            rules.append(_not_evaluated("R-10", "hard", "涉及3'端的交叉二聚体ΔG≥-9.0 kcal/mol", "P1.calc_heterodimer(3'端)", "未获得3'端热力学结果"))
        else:
            fail = candidate.three_prime_involved is True and candidate.three_prime_heterodimer_dg_kcal < -9
            rules.append(_result("R-10", "hard", RuleStatus.FAIL if fail else RuleStatus.PASS, {"dg_kcal": candidate.three_prime_heterodimer_dg_kcal, "three_prime_involved": candidate.three_prime_involved}, "涉及3'端且ΔG<-9.0→FAIL", "P1.calc_heterodimer(3'端)"))

        hairpins = {name: oligo.hairpin_dg_kcal for name, oligo in zip(("forward", "reverse", "probe"), primers + ([probe] if probe else []))}
        measured_hairpins = [value for value in hairpins.values() if value is not None]
        if not measured_hairpins:
            rules.append(_not_evaluated("R-11", "soft", "发卡ΔG<-3.0 kcal/mol→WARNING", "P1.calc_hairpin", "未获得发卡热力学结果"))
        else:
            rules.append(_result("R-11", "soft", RuleStatus.WARNING if any(value < -3 for value in measured_hairpins) else RuleStatus.PASS, hairpins, "发卡ΔG<-3.0 kcal/mol→WARNING", "P1.calc_hairpin"))

        dimer_values = [candidate.pair_heterodimer_dg_kcal] + [oligo.homodimer_dg_kcal for oligo in primers]
        measured_dimers = [value for value in dimer_values if value is not None]
        if not measured_dimers:
            rules.append(_not_evaluated("R-12", "soft", "非3'区域ΔG在[-9,-6)→WARNING", "P1热力学", "未获得二聚体热力学结果"))
        else:
            weak = [value for value in measured_dimers if -9 <= value < -6]
            rules.append(_result("R-12", "soft", RuleStatus.WARNING if weak else RuleStatus.PASS, weak, "非3'区域ΔG在[-9,-6)→WARNING", "P1热力学"))

        if candidate.mgb_assessment:
            mgb = candidate.mgb_assessment
            status = RuleStatus.PASS if mgb.get("tm_in_target") else RuleStatus.WARNING
            rules.append(_result("R-13", "soft", status, {"corrected_tm_celsius": mgb.get("corrected_tm_celsius"), "verdict": mgb.get("verdict")}, "MGB有效Tm 65-72°C", "P3"))
        else:
            rules.append(_result("R-13", "soft", RuleStatus.NOT_APPLICABLE, None, "MGB有效Tm 65-72°C", "P3", "未采用MGB修饰"))
        structure = candidate.amplicon_structure
        if not structure or structure.get("status") == "not_evaluated":
            rules.append(_result("R-SUPPLEMENT", "soft", RuleStatus.NOT_EVALUATED, structure, "扩增子结构ΔG<-30→WARNING", "P6", "二级结构未评估"))
        else:
            structure_status = str(structure.get("status"))
            rules.append(
                _result(
                    "R-SUPPLEMENT", "soft",
                    RuleStatus.WARNING if structure_status in {"WARNING", "CAUTION"} else RuleStatus.PASS,
                    {"status": structure_status, "mfe_dg": structure.get("mfe_dg")},
                    "PASS≥-20; CAUTION[-30,-20); WARNING<-30 kcal/mol", "P6",
                )
            )
        rules.append(_result("R-14", "platform", RuleStatus.NOT_APPLICABLE, None, "多重通道规则", "WF-02/P4", "WF-01/WF-04不适用"))
        if candidate.metadata.get("role") == "internal_control":
            fluorophore = candidate.metadata.get("fluorophore")
            concentration = candidate.metadata.get("primer_concentration_nm")
            gene = candidate.metadata.get("gene")
            limiting_ok = gene != "18S rRNA" or (concentration is not None and 50 <= float(concentration) <= 100)
            rules.append(
                _result(
                    "R-15", "platform", RuleStatus.PASS if fluorophore == "Cy5" and limiting_ok else RuleStatus.WARNING,
                    {"gene": gene, "fluorophore": fluorophore, "primer_concentration_nm": concentration},
                    "内标默认Cy5；18S rRNA限量引物50-100 nM", "P5",
                )
            )
        else:
            rules.append(_result("R-15", "platform", RuleStatus.NOT_APPLICABLE, None, "内标规则", "WF-03/P5", "非内标候选不适用"))

        if candidate.conservation_sequence_count >= 20 and candidate.conservation_score is not None:
            r16_status = RuleStatus.PASS if candidate.conservation_score >= 0.95 else RuleStatus.WARNING
            r16_actual: Any = {"score": candidate.conservation_score, "n": candidate.conservation_sequence_count}
        else:
            r16_status = RuleStatus.NOT_EVALUATED
            r16_actual = {"score": candidate.conservation_score, "n": candidate.conservation_sequence_count}
        rules.append(_result("R-16", "platform", r16_status, r16_actual, "广谱检测≥95%且序列数≥20", "C5/WF-01"))
        rules.append(_result("R-17", "platform", RuleStatus.NOT_APPLICABLE, None, "多重扩增子长度差≥20 bp", "WF-02/P4", "单靶标不适用"))

        required_meta = ("software", "software_version", "tm_algorithm", "mv_conc_mm", "dv_conc_mm", "oligo_conc_nm")
        missing_meta = [name for name in required_meta if candidate.metadata.get(name) in (None, "")]
        rules.append(_result("R-18", "metadata", RuleStatus.FAIL if missing_meta else RuleStatus.PASS, {"missing": missing_meta}, "软件/版本/算法/盐浓度/寡核苷酸浓度完整", "P1 metadata"))

        p2_specificity = candidate.metadata.get("_p2_specificity")
        if isinstance(p2_specificity, dict):
            specificity_status = str(
                p2_specificity.get("overall_status") or "NOT_EVALUATED"
            ).upper()
            specificity_actual: Any = {
                "status": specificity_status,
                "reason": p2_specificity.get("reason"),
            }
        elif candidate.specificity is not None:
            rating = str(candidate.specificity.rating).upper()
            specificity_status = {
                "PASS": "PASS",
                "PASS_WITH_WARNINGS": "CAUTION",
                "FAIL": "FAIL",
                "NOT_EVALUATED": "NOT_EVALUATED",
            }.get(rating, "NOT_EVALUATED")
            specificity_actual = rating
        else:
            specificity_status = "NOT_EVALUATED"
            specificity_actual = None

        if specificity_status == "FAIL":
            r19_status = RuleStatus.FAIL
            r19_message = "P2返回失败结论"
        elif (
            specificity_status == "PASS"
            and candidate.evidence_level == EvidenceLevel.BLAST_VERIFIED
        ):
            r19_status = RuleStatus.PASS
            r19_message = None
        elif (
            specificity_status == "CAUTION"
            and candidate.evidence_level == EvidenceLevel.BLAST_VERIFIED
        ):
            r19_status = RuleStatus.WARNING
            r19_message = "P2返回需复核结论"
        else:
            r19_status = RuleStatus.NOT_EVALUATED
            r19_message = "P2必需模式未完整执行"
        rules.append(
            _result(
                "R-19",
                "metadata",
                r19_status,
                specificity_actual,
                "P2必需模式完成并保留正式执行证据",
                "P2/C6",
                r19_message,
            )
        )

        rules.append(_result("R-20", "metadata", RuleStatus.PASS, candidate.evidence_level.value, "证据等级+实验验证声明", "C7"))
        if isinstance(p2_specificity, dict):
            specificity_complete = specificity_status in {
                "PASS",
                "CAUTION",
                "FAIL",
            }
        else:
            specificity_complete = bool(
                candidate.specificity is not None
                and candidate.specificity.method != "not_run"
                and specificity_status != "NOT_EVALUATED"
            )
        completeness = {
            "forward": bool(candidate.forward_primer.sequence_5to3),
            "reverse": bool(candidate.reverse_primer.sequence_5to3),
            "probe": bool(candidate.probe and candidate.probe.sequence_5to3),
            "amplicon": bool(candidate.amplicon_sequence_5to3),
            "tm": all(item.tm_celsius is not None for item in primers + ([probe] if probe else [])),
            "dg": bool(measured_hairpins and measured_dimers),
            "specificity": specificity_complete,
            "conservation": candidate.conservation_score is not None,
        }
        r21_status = RuleStatus.PASS if all(completeness.values()) else RuleStatus.NOT_EVALUATED
        rules.append(_result("R-21", "metadata", r21_status, completeness, "序列/Tm/ΔG/长度/BLAST/保守性完整", "C7"))
        return rules

    @staticmethod
    def apply_verdict(candidate: Candidate) -> Candidate:
        hard_fail = any(rule.code in BLOCKING_RULES and rule.status == RuleStatus.FAIL for rule in candidate.rules)
        missing_hard = any(rule.code in HARD_RULES and rule.status == RuleStatus.NOT_EVALUATED for rule in candidate.rules)
        rule_warnings = [rule.code for rule in candidate.rules if rule.status in {RuleStatus.WARNING, RuleStatus.NOT_EVALUATED}]
        candidate.warnings = sorted(set(candidate.warnings + rule_warnings))
        if hard_fail:
            candidate.verdict = "FAIL"
        elif missing_hard:
            candidate.verdict = "PARTIAL"
        elif candidate.warnings:
            candidate.verdict = "PASS_WITH_WARNINGS"
        else:
            candidate.verdict = "PASS"
        return candidate

"""WF-07 deterministic primer/probe problem diagnosis and optimization routing."""

from __future__ import annotations

from ..config import config_fingerprint
from ..database import DatabaseCatalog
from ..errors import InputValidationError
from ..models import RuleStatus, WorkflowResult, WorkflowStatus, jsonable
from .wf01 import WF01SingleTarget
from .wf04 import WF04ExistingPrimerValidation
from .wf05 import WF05VariationTolerance


class WF07PrimerDiagnosis:
    def __init__(self, config: dict, catalog: DatabaseCatalog | None = None) -> None:
        self.config = config
        self.catalog = catalog or DatabaseCatalog(config)

    @staticmethod
    def _classification(description: str) -> str:
        lowered = description.lower()
        if any(term in lowered for term in ("二聚体", "primer dimer", "低分子条带")):
            return "primer_dimer"
        if any(term in lowered for term in ("非特异", "non-specific", "nonspecific", "杂带")):
            return "non_specific_amplification"
        if any(term in lowered for term in ("灵敏度", "sensitivity", "ct高", "扩增效率低", "不扩增")):
            return "low_sensitivity"
        if any(term in lowered for term in ("背景", "background", "荧光高", "基线高")):
            return "high_background"
        return "undetermined"

    @staticmethod
    def _rule_map(candidate) -> dict:
        return {rule.code: rule for rule in candidate.rules}

    def run(self, payload: dict) -> WorkflowResult:
        description = str(payload.get("problem_description") or "").strip()
        if not description:
            raise InputValidationError("WF-07需要problem_description")
        validation_payload = {
            key: value
            for key, value in payload.items()
            if key not in {"problem_description", "experimental_conditions", "auto_redesign", "sequences", "fasta_path"}
        }
        validation = WF04ExistingPrimerValidation(self.config).run(validation_payload)
        candidate = validation.candidates[0]
        rules = self._rule_map(candidate)
        hard_violations = [
            {"code": rule.code, "actual": rule.actual, "threshold": rule.threshold, "message": rule.message}
            for rule in candidate.rules
            if rule.category == "hard" and rule.status == RuleStatus.FAIL
        ]
        problem_type = self._classification(description)
        evidence: list[dict] = []
        strategies: list[dict] = []
        cause = "自动化诊断无法确定单一根因"
        confidence = "LOW"

        r09 = rules.get("R-09")
        r10 = rules.get("R-10")
        if problem_type == "primer_dimer" or any(rule and rule.status == RuleStatus.FAIL for rule in (r09, r10)):
            measurable = any(rule and rule.status == RuleStatus.FAIL for rule in (r09, r10)) or (
                candidate.pair_heterodimer_dg_kcal is not None and candidate.pair_heterodimer_dg_kcal < -6.0
            )
            cause = "引物自身或交叉二聚体稳定性过高" if measurable else "现象符合引物二聚体，但热力学指标未明确越线"
            confidence = "HIGH" if measurable else "MEDIUM"
            evidence.append({"type": "thermodynamics", "pair_heterodimer_dg_kcal": candidate.pair_heterodimer_dg_kcal, "R-09": r09.status.value if r09 else None, "R-10": r10.status.value if r10 else None})
            strategies.extend(
                [
                    {"priority": 1, "action": "移动引物3′端1-2 nt或更换区域", "route": "WF-01"},
                    {"priority": 2, "action": "在不降低特异性的前提下提高退火温度1-2°C", "route": "experiment"},
                ]
            )
        elif problem_type == "non_specific_amplification":
            specificity_fail = bool(candidate.specificity and candidate.specificity.rating == "FAIL")
            cause = "靶标外命中或引物对3′端非特异延伸" if specificity_fail else "疑似非特异扩增，当前数据库证据不足"
            confidence = "HIGH" if specificity_fail else "MEDIUM"
            evidence.append({"type": "specificity", "result": jsonable(candidate.specificity)})
            strategies.extend(
                [
                    {"priority": 1, "action": "收紧数据库排除条件并更换靶区", "route": "WF-01"},
                    {"priority": 2, "action": "退火温度梯度上调2°C验证", "route": "experiment"},
                ]
            )
        elif problem_type == "low_sensitivity":
            low_tm = rules.get("R-01b") and rules["R-01b"].status == RuleStatus.WARNING
            structure_risk = candidate.amplicon_structure and candidate.amplicon_structure.get("status") in {"CAUTION", "WARNING"}
            mgb_risk = candidate.mgb_assessment and not candidate.mgb_assessment.get("tm_in_target", False)
            quantitative = bool(low_tm or structure_risk or mgb_risk)
            cause = "引物Tm偏低、探针有效Tm偏离或扩增子结构可能降低扩增效率" if quantitative else "可能存在靶标变异、模板质量或PCR抑制因素"
            confidence = "HIGH" if quantitative else "MEDIUM"
            evidence.append({"type": "sensitivity", "low_primer_tm": bool(low_tm), "amplicon_structure": candidate.amplicon_structure, "mgb_assessment": candidate.mgb_assessment})
            strategies.extend(
                [
                    {"priority": 1, "action": "执行代表性序列变异耐受性分析", "route": "WF-05"},
                    {"priority": 2, "action": "缩短扩增子或重新选择低结构风险区域", "route": "WF-01"},
                    {"priority": 3, "action": "评估MGB短探针", "route": "WF-01", "parameters": {"use_mgb": True}},
                ]
            )
        elif problem_type == "high_background":
            probe_g = bool(candidate.probe and candidate.probe.sequence_5to3.startswith("G"))
            cause = "探针5′端G可能淬灭报告荧光" if probe_g else "可能为淬灭基团效率、探针降解或仪器基线因素"
            confidence = "HIGH" if probe_g else "MEDIUM"
            evidence.append({"type": "probe", "probe_5prime_is_G": probe_g, "probe_sequence": candidate.probe.sequence_5to3 if candidate.probe else None})
            strategies.extend(
                [
                    {"priority": 1, "action": "重设计探针避开5′端G并核对淬灭基团", "route": "WF-01"},
                    {"priority": 2, "action": "检查探针冻融、避光保存和仪器光谱校准", "route": "experiment"},
                ]
            )
        else:
            strategies.append({"priority": 1, "action": "补充凝胶图、扩增曲线、模板质量和退火梯度数据", "route": "manual_review"})

        verification: dict = {"redesign": {"status": "NOT_RUN"}, "variation": {"status": "NOT_RUN"}}
        trace = [{"step": "WF-04", "status": validation.status.value}]
        if payload.get("auto_redesign") and payload.get("template") and len(str(payload["template"])) >= int(self.config["minimum_template_length"]):
            redesign_payload = {
                "target_id": payload.get("target_id", "diagnosis_redesign"),
                "target_species": payload.get("target_species"),
                "host_species": payload.get("host_species"),
                "sequence": payload["template"],
                "host_db": payload.get("host_db"),
                "cross_db": payload.get("cross_db"),
                "allow_fallback": payload.get("allow_fallback", False),
                "use_mgb": problem_type in {"low_sensitivity", "high_background"} and bool(payload.get("consider_mgb", True)),
            }
            redesign = WF01SingleTarget(self.config).run(redesign_payload)
            verification["redesign"] = {"status": redesign.status.value, "candidate_count": len(redesign.candidates), "candidates": [item.to_dict() for item in redesign.candidates]}
            trace.append({"step": "WF-01", "status": redesign.status.value, "reason": "diagnostic_optimization"})
        if payload.get("sequences") or payload.get("fasta_path"):
            variation_payload = {
                "target_id": payload.get("target_id"),
                "target_species": payload.get("target_species"),
                "forward_primer": payload["forward_primer"],
                "reverse_primer": payload["reverse_primer"],
                "probe": payload.get("probe"),
                "sequences": payload.get("sequences"),
                "fasta_path": payload.get("fasta_path"),
                "allow_fallback": payload.get("allow_fallback", False),
            }
            variation = WF05VariationTolerance(self.config).run(variation_payload)
            verification["variation"] = {"status": variation.status.value, "result": variation.variation}
            trace.append({"step": "WF-05", "status": variation.status.value, "reason": "diagnostic_variation_check"})
        diagnosis = {
            "problem_description": description,
            "problem_type": problem_type,
            "input_assay": {
                "forward_primer": payload.get("forward_primer"),
                "reverse_primer": payload.get("reverse_primer"),
                "probe": payload.get("probe"),
                "experimental_conditions": payload.get("experimental_conditions"),
            },
            "measurements": {
                "forward_tm_celsius": candidate.forward_primer.tm_celsius,
                "reverse_tm_celsius": candidate.reverse_primer.tm_celsius,
                "pair_heterodimer_dg_kcal": candidate.pair_heterodimer_dg_kcal,
                "specificity": jsonable(candidate.specificity),
            },
            "hard_rule_violations": hard_violations,
            "amplicon_structure": candidate.amplicon_structure,
            "attribution": {"cause": cause, "confidence": confidence, "method": "deterministic_rules", "evidence": evidence},
            "optimization": {"strategies": strategies},
            "verification": verification,
            "manual_information_requested": [] if confidence != "LOW" else ["凝胶电泳图或熔解曲线", "退火温度梯度", "模板浓度与纯度", "试剂批次与仪器校准时间"],
        }
        status = WorkflowStatus.FAILED if validation.status == WorkflowStatus.FAILED and not strategies else WorkflowStatus.PARTIAL
        if validation.status == WorkflowStatus.SUCCEEDED and confidence == "HIGH" and any(item.get("status") == "SUCCEEDED" for item in verification.values() if isinstance(item, dict)):
            status = WorkflowStatus.SUCCEEDED
        warnings = list(validation.warnings)
        if confidence == "LOW":
            warnings.append("自动化诊断无法确定根因，需要补充实验信息")
        return WorkflowResult(
            workflow="WF-07", status=status, assay_id=str(payload.get("assay_id") or f"{payload.get('target_id', 'assay')}_diagnosis_v1"),
            input={key: value for key, value in payload.items() if key not in {"template", "sequences"}},
            target={"target_id": payload.get("target_id"), "organism": payload.get("target_species"), "diagnostic_problem_type": problem_type},
            candidates=[candidate], specificity=jsonable(candidate.specificity), diagnosis=diagnosis,
            self_check={"verdict": "CAUTION" if confidence in {"LOW", "MEDIUM"} else "PASS", "risks": warnings},
            tool_status={**validation.tool_status, "C0": self.catalog.status()}, warnings=sorted(set(warnings)), errors=validation.errors,
            provenance={"agentd_version": "0.3.15", "config_schema_version": self.config["schema_version"], "config_sha256": config_fingerprint(self.config)},
            workflow_trace=trace, evidence_level=validation.evidence_level,
        )

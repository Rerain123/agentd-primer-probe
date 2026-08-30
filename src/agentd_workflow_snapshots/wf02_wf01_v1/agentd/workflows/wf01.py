"""WF-01 single-target TaqMan design."""

from __future__ import annotations

import copy
import math
import tempfile
from pathlib import Path

from ..alignment import MafftAdapter, find_conserved_regions
from ..config import config_fingerprint
from ..degenerate import assess_degenerate_strategy
from ..errors import InputValidationError, ToolUnavailableError
from ..models import Candidate, EvidenceLevel, RuleStatus, SequenceRecord, WorkflowResult, WorkflowStatus
from ..mgb import MGBProbeEvaluator
from ..primer3_tool import Primer3Adapter
from ..rules import HARD_RULES, RuleEngine
from ..scoring import score_candidate, self_check
from ..sequence import format_fasta, normalize_sequence, read_fasta, reverse_complement
from ..specificity import BlastAdapter, SpecificityEvaluator
from ..structure import ViennaRNAAdapter
from .common import evidence_from_specificity, not_checked_specificity


DESIGN_PARAMETER_TYPES = {
    "product_min_bp": int,
    "product_max_bp": int,
    "primer_min_size": int,
    "primer_opt_size": int,
    "primer_max_size": int,
    "primer_min_tm": float,
    "primer_opt_tm": float,
    "primer_max_tm": float,
    "primer_min_gc": float,
    "primer_max_gc": float,
    "probe_min_size": int,
    "probe_opt_size": int,
    "probe_max_size": int,
    "probe_min_tm": float,
    "probe_opt_tm": float,
    "probe_max_tm": float,
}


class WF01SingleTarget:
    def __init__(self, config: dict) -> None:
        self.config = config

    def _records(self, payload: dict) -> list[SequenceRecord]:
        if payload.get("fasta_path"):
            return read_fasta(payload["fasta_path"])
        if payload.get("sequences"):
            records: list[SequenceRecord] = []
            for index, item in enumerate(payload["sequences"]):
                if isinstance(item, str):
                    records.append(SequenceRecord(id=f"sequence_{index+1}", sequence=normalize_sequence(item, allow_iupac=index > 0)))
                else:
                    records.append(
                        SequenceRecord(
                            id=str(item.get("id", f"sequence_{index+1}")),
                            sequence=normalize_sequence(item["sequence"], allow_iupac=index > 0),
                            description=str(item.get("description", "")),
                            source=str(item.get("source", "user")),
                        )
                    )
            return records
        if payload.get("sequence"):
            return [
                SequenceRecord(
                    id=str(payload.get("target_id", "target")),
                    sequence=normalize_sequence(payload["sequence"]),
                    description=str(payload.get("target_species", "")),
                )
            ]
        raise InputValidationError("WF-01需要sequence、sequences或fasta_path")

    def _design_parameter_overrides(self, payload: dict) -> dict:
        supplied = payload.get("design_parameters")
        if supplied in (None, {}):
            return {}
        if not isinstance(supplied, dict):
            raise InputValidationError("design_parameters必须是对象")
        unknown = sorted(set(supplied) - set(DESIGN_PARAMETER_TYPES))
        if unknown:
            raise InputValidationError(
                "design_parameters包含不支持的参数",
                details={"unsupported": unknown, "supported": sorted(DESIGN_PARAMETER_TYPES)},
            )
        converted: dict[str, int | float] = {}
        for key, value in supplied.items():
            if isinstance(value, bool):
                raise InputValidationError(f"{key}必须是数值")
            try:
                numeric_float = float(value)
            except (TypeError, ValueError) as exc:
                raise InputValidationError(f"{key}必须是数值", details={"value": value}) from exc
            if not math.isfinite(numeric_float):
                raise InputValidationError(f"{key}必须是有限数值", details={"value": value})
            if DESIGN_PARAMETER_TYPES[key] is int:
                if not numeric_float.is_integer():
                    raise InputValidationError(f"{key}必须是整数", details={"value": value})
                numeric: int | float = int(numeric_float)
            else:
                numeric = numeric_float
            converted[key] = numeric

        merged = {**self.config["standard_design"], **converted}
        bounds = {
            "primer_min_size": (18, 28),
            "primer_opt_size": (18, 28),
            "primer_max_size": (18, 28),
            "primer_min_tm": (50, 65),
            "primer_opt_tm": (50, 65),
            "primer_max_tm": (50, 65),
            "primer_min_gc": (0, 100),
            "primer_max_gc": (0, 100),
            "probe_min_size": (13, 30),
            "probe_opt_size": (13, 30),
            "probe_max_size": (13, 30),
            "probe_min_tm": (50, 80),
            "probe_opt_tm": (50, 80),
            "probe_max_tm": (50, 80),
        }
        for key, (minimum, maximum) in bounds.items():
            if not minimum <= float(merged[key]) <= maximum:
                raise InputValidationError(
                    f"{key}超出允许范围{minimum}–{maximum}",
                    details={"value": merged[key], "minimum": minimum, "maximum": maximum},
                )
        ordered_groups = [
            ("扩增子长度", ["product_min_bp", "product_max_bp"]),
            ("引物长度", ["primer_min_size", "primer_opt_size", "primer_max_size"]),
            ("引物Tm", ["primer_min_tm", "primer_opt_tm", "primer_max_tm"]),
            ("引物GC", ["primer_min_gc", "primer_max_gc"]),
            ("探针长度", ["probe_min_size", "probe_opt_size", "probe_max_size"]),
            ("探针Tm", ["probe_min_tm", "probe_opt_tm", "probe_max_tm"]),
        ]
        for label, keys in ordered_groups:
            values = [float(merged[key]) for key in keys]
            if values != sorted(values):
                raise InputValidationError(
                    f"{label}参数必须满足最小值≤最适值≤最大值",
                    details={key: merged[key] for key in keys},
                )
        if not 50 <= int(merged["product_min_bp"]) <= int(merged["product_max_bp"]) <= 200:
            raise InputValidationError(
                "扩增子长度必须在50–200 bp绝对范围内",
                details={
                    "product_min_bp": merged["product_min_bp"],
                    "product_max_bp": merged["product_max_bp"],
                },
            )
        return converted

    @staticmethod
    def _shift_candidate(candidate: Candidate, offset: int, region_index: int, region: dict) -> Candidate:
        candidate.candidate_id = f"{candidate.candidate_id}-r{region_index}"
        candidate.amplicon_start += offset
        candidate.amplicon_end += offset
        for oligo in (candidate.forward_primer, candidate.reverse_primer, candidate.probe):
            if oligo and oligo.position_start is not None and oligo.position_end is not None:
                oligo.position_start += offset
                oligo.position_end += offset
        candidate.conservation_score = region.get("conservation_score")
        candidate.metadata.update(
            {
                "region_index": region_index,
                "region_start": region["start"],
                "region_end": region["end"],
                "region_label": region["label"],
            }
        )
        return candidate

    @staticmethod
    def _repair_probe(candidate: Candidate) -> Candidate:
        if candidate.probe and candidate.probe.sequence_5to3.startswith("G"):
            alternative = reverse_complement(candidate.probe.sequence_5to3)
            if not alternative.startswith("G"):
                candidate.probe.sequence_5to3 = alternative
                candidate.warnings.append("R-05补救：探针已切换为反义链")
        return candidate

    @staticmethod
    def _hard_failed(candidate: Candidate) -> bool:
        return any(rule.code in HARD_RULES and rule.status == RuleStatus.FAIL for rule in candidate.rules)

    @staticmethod
    def _rounds_for_region(
        design_rounds: list[tuple[str, dict]],
        region: dict,
    ) -> list[tuple[str, dict]]:
        """Apply the documented short-conserved-region relaxation branch.

        A region shorter than 120 bp still gets the standard attempt. If that
        fails, L1/L2 are physically unlikely to place a standard probe and the
        workflow jumps directly to the L3 short-probe round.
        """
        if int(region.get("length", 0)) >= 120:
            return design_rounds
        selected = [
            (label, parameters)
            for label, parameters in design_rounds
            if label in {"standard", "L3"}
        ]
        return selected or design_rounds

    def run(self, payload: dict) -> WorkflowResult:
        records = self._records(payload)
        reference = records[0]
        reference.sequence = normalize_sequence(reference.sequence)
        if len(reference.sequence) < int(self.config["minimum_template_length"]):
            raise InputValidationError(
                f"靶标序列长度{len(reference.sequence)}nt，小于WF-01最低要求{self.config['minimum_template_length']}nt",
                details={"suggestion": "序列≥50nt时可使用WF-04验证已有引物"},
            )
        requested_top_candidates = payload.get("top_candidates", self.config["top_candidates"])
        maximum_candidates = int(self.config["maximum_candidates"])
        if isinstance(requested_top_candidates, bool):
            raise InputValidationError(
                "top_candidates必须是整数",
                details={"requested": requested_top_candidates, "minimum": 1, "maximum": maximum_candidates},
            )
        try:
            top_candidates = int(requested_top_candidates)
        except (TypeError, ValueError) as exc:
            raise InputValidationError(
                "top_candidates必须是整数",
                details={"requested": requested_top_candidates, "minimum": 1, "maximum": maximum_candidates},
            ) from exc
        if not 1 <= top_candidates <= maximum_candidates:
            raise InputValidationError(
                f"top_candidates必须在1到{maximum_candidates}之间",
                details={"requested": top_candidates, "minimum": 1, "maximum": maximum_candidates},
            )
        collection_target = top_candidates
        design_overrides = self._design_parameter_overrides(payload)
        allow_fallback = bool(payload.get("allow_fallback", self.config["tools"]["allow_development_fallback"]))
        primer3 = Primer3Adapter(self.config, allow_fallback=allow_fallback)
        blast = BlastAdapter(self.config["tools"]["blastn_executable"], allow_fallback=allow_fallback)
        mafft = MafftAdapter(self.config["tools"]["mafft_executable"], allow_fallback=allow_fallback)
        specificity_evaluator = SpecificityEvaluator(blast, primer3)
        mgb = MGBProbeEvaluator(primer3, self.config)
        vienna = ViennaRNAAdapter(self.config)
        rule_engine = RuleEngine()
        workflow_warnings: list[str] = []
        screen_messages: list[str] = []
        errors: list[dict] = []
        attempted_rounds: list[dict] = []
        failure_counts = {"design_zero": 0, "hard_rules": 0, "mgb_hard_rules": 0, "specificity": 0}

        aligned, alignment_method, alignment_warnings = mafft.align(records)
        workflow_warnings.extend(alignment_warnings)
        regions, conservation_method = find_conserved_regions(
            aligned,
            self.config["conservation_rounds"],
            maximum_regions=int(payload.get("maximum_regions", 3)),
        )
        if not regions:
            regions = [{"start": 0, "end": len(reference.sequence) - 1, "length": len(reference.sequence), "conservation_score": None, "label": "full_length_fallback"}]
        if len(records) < 20:
            workflow_warnings.append(f"保守性序列数不足：N={len(records)}<20，结果置信度降低")

        candidates: list[Candidate] = []
        design_rounds = [("standard", self.config["standard_design"])] + [
            (item["label"], item) for item in self.config["relaxation_rounds"]
        ]
        if design_overrides:
            design_rounds = [
                (label, {**parameters, **design_overrides})
                for label, parameters in design_rounds
            ]
        force_mgb = bool(payload.get("use_mgb") or str(payload.get("probe_type", "")).upper() in {"MGB", "TAQMAN_MGB"})
        if force_mgb:
            mgb_settings = self.config["mgb"]
            design_rounds = [
                (
                    label,
                    {
                        **parameters,
                        "probe_min_size": design_overrides.get("probe_min_size", 13),
                        "probe_opt_size": design_overrides.get("probe_opt_size", 18),
                        "probe_max_size": design_overrides.get("probe_max_size", 20),
                        "probe_min_tm": design_overrides.get("probe_min_tm", mgb_settings["design_min_tm_c"]),
                        "probe_opt_tm": design_overrides.get("probe_opt_tm", mgb_settings["design_opt_tm_c"]),
                        "probe_max_tm": design_overrides.get("probe_max_tm", mgb_settings["design_max_tm_c"]),
                    },
                )
                for label, parameters in design_rounds
            ]
        degenerate_strategy = None
        with tempfile.TemporaryDirectory(prefix="agentd-wf01-") as temp_dir:
            target_db = payload.get("target_db")
            if not target_db:
                target_path = Path(temp_dir) / "representative-targets.fasta"
                exact_records = [record for record in records if set(record.sequence) <= set("ACGT")]
                target_path.write_text(format_fasta(exact_records or [reference]), encoding="utf-8")
                target_db = str(target_path)
            for region_index, region in enumerate(regions, start=1):
                region_sequence = reference.sequence[region["start"] : region["end"] + 1]
                successful_round = False
                region_rounds = self._rounds_for_region(design_rounds, region)
                if [label for label, _parameters in region_rounds] != [label for label, _parameters in design_rounds]:
                    screen_messages.append(
                        f"保守区{region_index}长度{region['length']}bp<120bp：标准轮失败后跳过L1/L2，直接执行L3短探针轮"
                    )
                for label, parameters in region_rounds:
                    attempted = {"region_index": region_index, "label": label, "parameters": copy.deepcopy(parameters), "generated": 0, "passed_hard_rules": 0, "passed_specificity": 0}
                    try:
                        designed = primer3.design(region_sequence, sequence_id=reference.id, parameters=parameters)
                    except ToolUnavailableError as exc:
                        errors.append(exc.to_dict())
                        designed = []
                    attempted["generated"] = len(designed)
                    if not designed:
                        failure_counts["design_zero"] += 1
                        attempted_rounds.append(attempted)
                        continue
                    for raw_candidate in designed:
                        candidate = self._shift_candidate(raw_candidate, int(region["start"]), region_index, region)
                        candidate = self._repair_probe(candidate)
                        candidate.metadata["relaxation_label"] = label
                        candidate.conservation_method = conservation_method
                        candidate.conservation_sequence_count = len(records)
                        use_mgb = bool(
                            candidate.probe
                            and (
                                force_mgb
                                or (
                                    payload.get("use_mgb") is not False
                                    and self.config["mgb"]["enabled_for_short_probe"]
                                    and candidate.probe.length_nt <= 20
                                )
                            )
                        )
                        if use_mgb and candidate.probe:
                            candidate.mgb_assessment = mgb.assess(
                                candidate.probe.sequence_5to3,
                                fluorophore=payload.get("fluorophore"),
                            )
                            candidate.warnings.extend(f"P3:{code}" for code in candidate.mgb_assessment["warnings"])
                            candidate.metadata["probe_type"] = "TaqMan_MGB"
                            if not candidate.mgb_assessment["all_hard_pass"]:
                                failure_counts["mgb_hard_rules"] += 1
                                continue
                        else:
                            candidate.metadata["probe_type"] = "TaqMan_BHQ"
                        if candidate.amplicon_sequence_5to3 and vienna.available:
                            try:
                                candidate.amplicon_structure = vienna.assess_full(candidate.amplicon_sequence_5to3)
                            except Exception as exc:  # optional P6 must not block design
                                candidate.amplicon_structure = vienna.not_evaluated(str(exc))
                        else:
                            candidate.amplicon_structure = vienna.not_evaluated("ViennaRNA不可用或扩增子缺失")
                        candidate.rules = rule_engine.evaluate(candidate)
                        rule_engine.apply_verdict(candidate)
                        if self._hard_failed(candidate):
                            failure_counts["hard_rules"] += 1
                            continue
                        attempted["passed_hard_rules"] += 1
                        try:
                            candidate.specificity = specificity_evaluator.evaluate(
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
                            candidate.warnings.append(exc.message)
                        candidate.evidence_level = evidence_from_specificity(candidate.specificity)
                        candidate.rules = rule_engine.evaluate(candidate)
                        rule_engine.apply_verdict(candidate)
                        if candidate.specificity.rating == "FAIL":
                            failure_counts["specificity"] += 1
                            continue
                        score_candidate(candidate, self.config)
                        candidates.append(candidate)
                        attempted["passed_specificity"] += 1
                    attempted_rounds.append(attempted)
                    if attempted["passed_specificity"] > 0:
                        successful_round = True
                        break
                if len(candidates) >= collection_target:
                    break
                if not successful_round:
                    workflow_warnings.append(f"保守区{region_index}未产生合格候选，已尝试全部松弛轮次")

            if candidates:
                inherited = max(candidates, key=lambda item: item.score)
                round_label = str(inherited.metadata.get("relaxation_label", "standard"))
                round_number = {"standard": 0, "L1": 1, "L2": 2, "L3": 3}.get(round_label, 0)
                try:
                    degenerate_strategy = assess_degenerate_strategy(
                        aligned,
                        inherited,
                        primer3,
                        blast,
                        self.config,
                        relaxation_round_inherited=round_number,
                        target_db=target_db,
                        host_db=payload.get("host_db"),
                        cross_db=payload.get("cross_db"),
                    )
                except ToolUnavailableError as exc:
                    degenerate_strategy = {
                        "triggered": True,
                        "feasibility": "NOT_EVALUATED",
                        "specificity": {"status": "NOT_RUN", "reason": exc.message},
                        "limitations": ["科学工具不可用，简并策略未完成验证"],
                    }
                    workflow_warnings.append("简并策略因科学工具不可用而未完成")

        candidates.sort(key=lambda item: (-item.score, item.candidate_id))
        for rank, candidate in enumerate(candidates, start=1):
            candidate.rank = rank
        candidates = candidates[:top_candidates]
        if not candidates:
            status = WorkflowStatus.FAILED
            errors.append(
                {
                    "code": "NO_CANDIDATE",
                    "message": "标准参数及三轮松弛后仍无通过红线和特异性检查的候选",
                    "details": {"failure_counts": failure_counts},
                }
            )
        else:
            complete = all(
                candidate.design_method == "primer3"
                and candidate.evidence_level == EvidenceLevel.BLAST_VERIFIED
                and candidate.verdict in {"PASS", "PASS_WITH_WARNINGS"}
                for candidate in candidates
            )
            status = WorkflowStatus.SUCCEEDED if complete else WorkflowStatus.PARTIAL
            if len(candidates) < top_candidates:
                workflow_warnings.append(f"候选数量不足用户请求：{len(candidates)}<{top_candidates}")

        assay_id = str(payload.get("assay_id") or f"{payload.get('target_id', reference.id)}_{payload.get('gene', 'target')}_v1")
        evidence = EvidenceLevel.BLAST_VERIFIED if candidates and all(item.evidence_level == EvidenceLevel.BLAST_VERIFIED for item in candidates) else EvidenceLevel.IN_SILICO_ONLY
        return WorkflowResult(
            workflow="WF-01",
            status=status,
            assay_id=assay_id,
            input={key: value for key, value in payload.items() if key not in {"sequence", "sequences"}},
            target={
                "target_id": payload.get("target_id", reference.id),
                "organism": payload.get("target_species"),
                "gene": payload.get("gene"),
                "reference_sequence_id": reference.id,
                "reference_length_nt": len(reference.sequence),
                "sequence_count": len(records),
                "conservation_method": conservation_method,
                "alignment_method": alignment_method,
                "regions": regions,
            },
            candidates=candidates,
            relaxation_metadata={
                "requested_top_candidates": top_candidates,
                "returned_candidates": len(candidates),
                "attempted_rounds": attempted_rounds,
                "failure_counts": failure_counts,
                "short_probe_triggered": any(item["label"] == "L3" and item["passed_specificity"] > 0 for item in attempted_rounds),
                "screen_messages": screen_messages,
            },
            degenerate_strategy=degenerate_strategy,
            self_check=self_check(candidates),
            tool_status={"P1": primer3.status(), "P2_C6": blast.status(), "P3": mgb.status(), "P6": vienna.status(), "C5": mafft.status(), "C1": {"tool": "C1_sequence_io", "available": True}, "C7": {"tool": "C7_schema", "available": True}},
            warnings=sorted(set(workflow_warnings)),
            errors=errors,
            provenance={"agentd_version": "0.3.15", "config_schema_version": self.config["schema_version"], "config_sha256": config_fingerprint(self.config)},
            evidence_level=evidence,
        )

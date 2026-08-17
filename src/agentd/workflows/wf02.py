"""WF-02 multiplex qPCR panel workflow."""

from __future__ import annotations

import copy
import itertools
from typing import Iterable

from ..config import config_fingerprint
from ..database import DatabaseCatalog
from ..errors import InputValidationError
from ..internal_control import InternalControlDesigner
from ..models import Candidate, EvidenceLevel, WorkflowResult, WorkflowStatus
from ..multiplex import MultiplexEvaluator, channel_order, recommended_quencher
from ..primer3_tool import Primer3Adapter
from ..sequence import gc_percent
from .wf01 import WF01SingleTarget


def _probe_tm(candidate: Candidate) -> float:
    if candidate.mgb_assessment:
        return float(candidate.mgb_assessment["corrected_tm_celsius"])
    if candidate.probe and candidate.probe.tm_celsius is not None:
        return float(candidate.probe.tm_celsius)
    return 0.0


def _panel_entry(candidate: Candidate, target_id: str, fluorophore: str, *, role: str = "target") -> dict:
    if candidate.probe is None:
        raise InputValidationError("多重面板候选缺少探针", details={"candidate_id": candidate.candidate_id})
    mgb = bool(candidate.mgb_assessment)
    return {
        "target_id": target_id,
        "role": role,
        "candidate_id": candidate.candidate_id,
        "fwd_seq": candidate.forward_primer.sequence_5to3,
        "rev_seq": candidate.reverse_primer.sequence_5to3,
        "probe_seq": candidate.probe.sequence_5to3,
        "fwd_tm": candidate.forward_primer.tm_celsius,
        "rev_tm": candidate.reverse_primer.tm_celsius,
        "probe_tm": _probe_tm(candidate),
        "gc_percent": gc_percent(candidate.amplicon_sequence_5to3),
        "amplicon_length_bp": candidate.length_bp,
        "fluorophore": fluorophore,
        "quencher": recommended_quencher(fluorophore, mgb=mgb),
        "score": candidate.score,
        "evidence_level": candidate.evidence_level.value,
    }


def _length_issues(entries: list[dict], minimum_difference: int) -> list[str]:
    issues: list[str] = []
    for left, right in itertools.combinations(entries, 2):
        difference = abs(int(left["amplicon_length_bp"]) - int(right["amplicon_length_bp"]))
        if difference < minimum_difference:
            issues.append(f"[FAIL] {left['target_id']}-{right['target_id']}扩增子长度差{difference}bp<{minimum_difference}bp")
    return issues


class WF02MultiplexPanel:
    def __init__(self, config: dict, catalog: DatabaseCatalog | None = None) -> None:
        self.config = config
        self.catalog = catalog or DatabaseCatalog(config)

    def _design_targets(self, payload: dict) -> tuple[dict[str, list[Candidate]], list[dict], list[dict]]:
        targets = payload.get("targets")
        if not isinstance(targets, list):
            raise InputValidationError("WF-02需要targets数组")
        settings = self.config["multiplex"]
        if not int(settings["minimum_targets"]) <= len(targets) <= int(settings["maximum_targets"]):
            raise InputValidationError(
                f"WF-02支持{settings['minimum_targets']}-{settings['maximum_targets']}个靶标",
                details={"target_count": len(targets)},
            )
        child_config = copy.deepcopy(self.config)
        child_config["top_candidates"] = 5
        designed: dict[str, list[Candidate]] = {}
        failures: list[dict] = []
        trace: list[dict] = []
        for index, target in enumerate(targets, start=1):
            if not isinstance(target, dict):
                raise InputValidationError("targets条目必须为对象", details={"index": index})
            target_id = str(target.get("target_id") or f"target_{index}")
            child_payload = {
                key: value
                for key, value in payload.items()
                if key not in {"targets", "include_internal_control", "internal_control_payload", "instrument", "rox_as_reference"}
            }
            child_payload.update(target)
            child_payload["target_id"] = target_id
            try:
                result = WF01SingleTarget(child_config).run(child_payload)
            except Exception as exc:
                failures.append({"target_id": target_id, "reason": str(exc), "status": "FAILED"})
                trace.append({"step": "WF-01", "target_id": target_id, "status": "FAILED"})
                continue
            trace.append({"step": "WF-01", "target_id": target_id, "status": result.status.value, "candidate_count": len(result.candidates)})
            if result.candidates:
                for candidate in result.candidates:
                    candidate.metadata.setdefault("role", "target")
                    candidate.metadata.setdefault("target_id", target_id)
                designed[target_id] = result.candidates[:5]
            else:
                failures.append({"target_id": target_id, "reason": result.errors, "status": result.status.value})
        return designed, failures, trace

    def run(self, payload: dict) -> WorkflowResult:
        designed, failed_targets, trace = self._design_targets(payload)
        requested_count = len(payload["targets"])
        warnings: list[str] = []
        include_internal = bool(payload.get("include_internal_control", True))
        internal_result: dict | None = None
        internal_candidate: Candidate | None = None
        if include_internal:
            existing_targets = []
            for target_id, candidates in designed.items():
                if candidates:
                    first = candidates[0]
                    existing_targets.append(
                        {
                            "target_id": target_id,
                            "fwd_seq": first.forward_primer.sequence_5to3,
                            "rev_seq": first.reverse_primer.sequence_5to3,
                            "probe_seq": first.probe.sequence_5to3 if first.probe else None,
                        }
                    )
            internal_payload = {
                "host_species": payload.get("host_species"),
                "allow_fallback": payload.get("allow_fallback", False),
                "host_db": payload.get("host_db"),
                "cross_db": payload.get("cross_db"),
                "existing_targets": existing_targets,
                **(payload.get("internal_control_payload") or {}),
            }
            internal_raw = InternalControlDesigner(self.config, self.catalog).design(internal_payload)
            internal_objects = internal_raw.pop("candidate_objects", [])
            internal_candidate = internal_objects[0] if internal_objects else None
            internal_result = internal_raw
            trace.append({"step": "WF-03/P5", "status": internal_raw.get("status"), "verdict": internal_raw.get("verdict")})
            if internal_candidate is None:
                warnings.append("内标数据或候选暂不可用；面板先按靶标通道构建，服务器接库后需重新验证")

        if len(designed) < 2:
            errors = [{"code": "INSUFFICIENT_TARGETS", "message": "少于2个靶标获得候选，无法组成多重面板", "details": {"failed_targets": failed_targets}}]
            flattened = [candidate for candidates in designed.values() for candidate in candidates[:1]]
            return WorkflowResult(
                workflow="WF-02", status=WorkflowStatus.FAILED, assay_id=str(payload.get("assay_id") or "multiplex_panel_v1"),
                input={key: value for key, value in payload.items() if key != "targets"},
                target={"requested_targets": requested_count, "designed_targets": sorted(designed)}, candidates=flattened,
                multiplex={"verdict": "INCOMPLETE", "failed_targets": failed_targets, "panels": [], "recommendation": "失败靶标回到WF-01独立设计"},
                internal_control=internal_result, self_check={"verdict": "CAUTION", "risks": warnings}, warnings=warnings, errors=errors,
                tool_status={"C0": self.catalog.status()}, provenance={"agentd_version": "0.3.15", "config_schema_version": self.config["schema_version"], "config_sha256": config_fingerprint(self.config)},
                workflow_trace=trace, evidence_level=EvidenceLevel.IN_SILICO_ONLY,
            )

        allow_fallback = bool(payload.get("allow_fallback", self.config["tools"]["allow_development_fallback"]))
        primer3 = Primer3Adapter(self.config, allow_fallback=allow_fallback)
        p4 = MultiplexEvaluator(primer3, self.config)
        instrument = str(payload.get("instrument") or self.config["multiplex"]["default_instrument"])
        rox_as_reference = payload.get("rox_as_reference")
        order = channel_order(instrument, rox_as_reference=rox_as_reference, include_internal=internal_candidate is not None)
        if len(designed) > len(order):
            raise InputValidationError("可用报告通道不足", details={"designed_targets": len(designed), "channels": order})
        target_ids = list(designed)
        assignment = {target_id: order[index] for index, target_id in enumerate(target_ids)}
        if internal_candidate:
            assignment["internal_control"] = "Cy5"

        maximum = int(self.config["multiplex"]["maximum_panel_combinations"])
        combinations: Iterable[tuple[Candidate, ...]] = itertools.islice(itertools.product(*(designed[target_id] for target_id in target_ids)), maximum)
        strict_panels: list[dict] = []
        marginal_panels: list[dict] = []
        length_minimum = int(self.config["multiplex"]["minimum_amplicon_length_difference_bp"])
        combinations_evaluated = 0
        for combo_index, combination in enumerate(combinations, start=1):
            combinations_evaluated += 1
            entries = [_panel_entry(candidate, target_id, assignment[target_id]) for target_id, candidate in zip(target_ids, combination)]
            members = list(combination)
            if internal_candidate:
                entries.append(_panel_entry(internal_candidate, "internal_control", "Cy5", role="internal_control"))
                members.append(internal_candidate)
            compatibility = p4.check_panel(entries, instrument=instrument, rox_as_reference=rox_as_reference)
            spacing_issues = _length_issues(entries, length_minimum)
            if spacing_issues:
                compatibility["issues"].extend(spacing_issues)
                compatibility["verdict"] = "INCOMPATIBLE"
            mean_score = sum(candidate.score for candidate in members) / len(members)
            compatibility_factor = 1.0 if compatibility["verdict"] == "COMPATIBLE" else 0.85
            tm_spread = float(compatibility["tm_spread_celsius"]["primers"])
            tm_factor = max(0.7, 1.0 - max(0.0, tm_spread - 3.0) * 0.05)
            panel_result = {
                "panel_id": f"panel-{combo_index}",
                "members": entries,
                "compatibility": compatibility,
                "score": round(mean_score * compatibility_factor * tm_factor, 6),
                "verdict": compatibility["verdict"],
                "candidate_ids": [candidate.candidate_id for candidate in members],
            }
            if compatibility["verdict"] == "COMPATIBLE":
                strict_panels.append(panel_result)
            elif compatibility["verdict"] == "MARGINAL":
                marginal_panels.append(panel_result)

        relaxation_steps: list[dict] = []
        selected_panels = strict_panels
        if not selected_panels:
            relaxation_steps.append({"step": 1, "change": "交叉二聚体阈值放宽至-7.0 kcal/mol", "applied": True})
            relaxed_panels: list[dict] = []
            for panel in marginal_panels:
                entries = panel["members"]
                compatibility = p4.check_panel(entries, instrument=instrument, rox_as_reference=rox_as_reference, relaxed=True, tm_spread_limit=float(self.config["multiplex"]["tm_spread_relaxed_c"]))
                spacing_issues = _length_issues(entries, length_minimum)
                if spacing_issues:
                    compatibility["issues"].extend(spacing_issues)
                    compatibility["verdict"] = "INCOMPATIBLE"
                if compatibility["verdict"] != "INCOMPATIBLE":
                    panel = {**panel, "compatibility": compatibility, "verdict": compatibility["verdict"], "relaxation_applied": True, "score": round(panel["score"] * 0.95, 6)}
                    relaxed_panels.append(panel)
            selected_panels = relaxed_panels
        if not selected_panels:
            relaxation_steps.append({"step": 2, "change": "建议部分靶标分管检测", "applied": True})
            warnings.append("当前候选无法组成兼容多重面板；建议减少靶标或分管检测")
        selected_panels.sort(key=lambda item: (-item["score"], item["panel_id"]))
        selected_panels = selected_panels[: int(self.config["multiplex"]["top_panels"])]
        if selected_panels:
            selected_ids = set(selected_panels[0]["candidate_ids"])
            selected_candidates = [candidate for candidates in designed.values() for candidate in candidates if candidate.candidate_id in selected_ids]
            if internal_candidate and internal_candidate.candidate_id in selected_ids:
                selected_candidates.append(internal_candidate)
        else:
            selected_candidates = [candidates[0] for candidates in designed.values()]
        evidence = EvidenceLevel.BLAST_VERIFIED if selected_candidates and all(candidate.evidence_level == EvidenceLevel.BLAST_VERIFIED for candidate in selected_candidates) else EvidenceLevel.IN_SILICO_ONLY
        complete_targets = len(designed) == requested_count
        status = WorkflowStatus.SUCCEEDED if selected_panels and complete_targets and evidence == EvidenceLevel.BLAST_VERIFIED and (not include_internal or internal_candidate is not None) else (WorkflowStatus.PARTIAL if selected_panels or designed else WorkflowStatus.FAILED)
        multiplex = {
            "verdict": "COMPATIBLE" if selected_panels and complete_targets else ("PARTIAL" if selected_panels else "INCOMPATIBLE"),
            "requested_targets": requested_count,
            "designed_targets": len(designed),
            "failed_targets": failed_targets,
            "instrument": instrument,
            "channel_assignment": assignment,
            "combinations_evaluated": combinations_evaluated,
            "panels": selected_panels,
            "relaxation_steps": relaxation_steps,
            "panel_incomplete": not complete_targets or (include_internal and internal_candidate is None),
        }
        trace.append({"step": "P4", "status": multiplex["verdict"], "combinations_evaluated": combinations_evaluated, "panels_returned": len(selected_panels)})
        return WorkflowResult(
            workflow="WF-02", status=status, assay_id=str(payload.get("assay_id") or "multiplex_panel_v1"),
            input={key: value for key, value in payload.items() if key != "targets"},
            target={"requested_targets": [target.get("target_id") for target in payload["targets"]], "host_species": payload.get("host_species")},
            candidates=selected_candidates, multiplex=multiplex, internal_control=internal_result,
            self_check={"verdict": "CAUTION" if warnings or failed_targets else "PASS", "risks": warnings + [f"失败靶标:{item['target_id']}" for item in failed_targets]},
            tool_status={"P1": primer3.status(), "P4": p4.status(), "P5": InternalControlDesigner(self.config, self.catalog).status(allow_fallback=allow_fallback), "C0": self.catalog.status()},
            warnings=sorted(set(warnings)), errors=[], provenance={"agentd_version": "0.3.15", "config_schema_version": self.config["schema_version"], "config_sha256": config_fingerprint(self.config)},
            workflow_trace=trace, evidence_level=evidence,
        )

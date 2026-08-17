"""WF-06 end-to-end diagnostic panel orchestration."""

from __future__ import annotations

from ..config import config_fingerprint
from ..database import DatabaseCatalog
from ..errors import InputValidationError
from ..models import Candidate, EvidenceLevel, WorkflowResult, WorkflowStatus
from .wf01 import WF01SingleTarget
from .wf02 import WF02MultiplexPanel
from .wf03 import WF03InternalControl
from .wf05 import WF05VariationTolerance


class WF06CompletePanel:
    def __init__(self, config: dict, catalog: DatabaseCatalog | None = None) -> None:
        self.config = config
        self.catalog = catalog or DatabaseCatalog(config)

    @staticmethod
    def _targets(payload: dict) -> list[dict]:
        if isinstance(payload.get("targets"), list):
            return payload["targets"]
        if any(payload.get(key) for key in ("target_id", "sequence", "sequences", "fasta_path")):
            return [{key: value for key, value in payload.items() if key not in {"include_internal_control", "instrument", "rox_as_reference", "internal_control_payload"}}]
        raise InputValidationError("WF-06需要targets数组或单靶标输入")

    @staticmethod
    def _selected_candidate_for(target_id: str, candidates: list[Candidate], multiplex: dict | None) -> Candidate | None:
        if multiplex and multiplex.get("panels"):
            member = next((item for item in multiplex["panels"][0]["members"] if item["target_id"] == target_id), None)
            if member:
                return next((candidate for candidate in candidates if candidate.candidate_id == member["candidate_id"]), None)
        matched = next(
            (
                candidate
                for candidate in candidates
                if candidate.metadata.get("role") != "internal_control"
                and candidate.metadata.get("target_id") == target_id
            ),
            None,
        )
        if matched is not None or multiplex is not None:
            return matched
        return next((candidate for candidate in candidates if candidate.metadata.get("role") != "internal_control"), None)

    def run(self, payload: dict) -> WorkflowResult:
        targets = self._targets(payload)
        common = {
            key: value
            for key, value in payload.items()
            if key not in {"targets", "sequence", "sequences", "fasta_path", "target_id", "target_species", "gene"}
        }
        include_internal = bool(payload.get("include_internal_control", True))
        trace: list[dict] = []
        warnings: list[str] = []
        errors: list[dict] = []
        candidates: list[Candidate] = []
        multiplex: dict | None = None
        internal_control: dict | None = None
        design_status = WorkflowStatus.FAILED

        if len(targets) == 1:
            target_payload = {**common, **targets[0]}
            design = WF01SingleTarget(self.config).run(target_payload)
            candidates = design.candidates
            design_status = design.status
            warnings.extend(design.warnings)
            errors.extend(design.errors)
            trace.append({"step": "WF-01", "status": design.status.value, "candidate_count": len(candidates)})
            if include_internal:
                existing = []
                if candidates:
                    first = candidates[0]
                    existing.append({"target_id": target_payload.get("target_id"), "fwd_seq": first.forward_primer.sequence_5to3, "rev_seq": first.reverse_primer.sequence_5to3, "probe_seq": first.probe.sequence_5to3 if first.probe else None})
                ic_payload = {
                    "host_species": payload.get("host_species"),
                    "allow_fallback": payload.get("allow_fallback", False),
                    "host_db": payload.get("host_db"),
                    "cross_db": payload.get("cross_db"),
                    "existing_targets": existing,
                    **(payload.get("internal_control_payload") or {}),
                }
                ic_result = WF03InternalControl(self.config, self.catalog).run(ic_payload)
                internal_control = ic_result.internal_control
                candidates.extend(ic_result.candidates)
                warnings.extend(ic_result.warnings)
                trace.append({"step": "WF-03", "status": ic_result.status.value, "candidate_count": len(ic_result.candidates)})
        else:
            multiplex_payload = {
                **common,
                "targets": targets,
                "include_internal_control": include_internal,
                "internal_control_payload": payload.get("internal_control_payload") or {},
            }
            panel_result = WF02MultiplexPanel(self.config, self.catalog).run(multiplex_payload)
            candidates = panel_result.candidates
            multiplex = panel_result.multiplex
            internal_control = panel_result.internal_control
            design_status = panel_result.status
            warnings.extend(panel_result.warnings)
            errors.extend(panel_result.errors)
            trace.extend(panel_result.workflow_trace)
            trace.append({"step": "WF-02", "status": panel_result.status.value})

        variation_targets: list[dict] = []
        for index, target in enumerate(targets, start=1):
            target_id = str(target.get("target_id") or f"target_{index}")
            candidate = self._selected_candidate_for(target_id, candidates, multiplex)
            has_population = bool(target.get("sequences") or target.get("fasta_path"))
            if candidate is None or not has_population:
                variation_targets.append(
                    {
                        "target_id": target_id,
                        "status": "NOT_CONFIGURED" if not has_population else "NO_CANDIDATE",
                        "verdict": "NOT_EVALUATED",
                        "reason": "需要≥2条代表性序列和已选候选" if not has_population else "没有可评估候选",
                    }
                )
                continue
            variation_payload = {
                **common,
                **{key: value for key, value in target.items() if key in {"sequences", "fasta_path", "target_species"}},
                "target_id": target_id,
                "forward_primer": candidate.forward_primer.sequence_5to3,
                "reverse_primer": candidate.reverse_primer.sequence_5to3,
                "probe": candidate.probe.sequence_5to3 if candidate.probe else None,
            }
            try:
                variation_result = WF05VariationTolerance(self.config).run(variation_payload)
                variation_targets.append({"target_id": target_id, "status": variation_result.status.value, **(variation_result.variation or {})})
                trace.append({"step": "WF-05", "target_id": target_id, "status": variation_result.status.value})
            except Exception as exc:
                variation_targets.append({"target_id": target_id, "status": "FAILED", "verdict": "NOT_EVALUATED", "reason": str(exc)})
        evaluated_variations = [item for item in variation_targets if item.get("verdict") not in {"NOT_EVALUATED", None}]
        variation_verdict = (
            "REDESIGN_RECOMMENDED" if any(item.get("verdict") == "REDESIGN_RECOMMENDED" for item in evaluated_variations)
            else ("ACCEPTABLE" if any(item.get("verdict") == "ACCEPTABLE" for item in evaluated_variations) else ("ROBUST" if evaluated_variations else "NOT_EVALUATED"))
        )
        variation = {"targets": variation_targets, "verdict": variation_verdict, "evaluated_targets": len(evaluated_variations), "requested_targets": len(targets)}

        target_candidates = [candidate for candidate in candidates if candidate.metadata.get("role") != "internal_control"]
        primer_tms = [tm for candidate in target_candidates for tm in (candidate.forward_primer.tm_celsius, candidate.reverse_primer.tm_celsius) if tm is not None]
        lowest_tm = min(primer_tms) if primer_tms else None
        if lowest_tm is not None and lowest_tm < 58:
            annealing = 58.0
            annealing_note = "最低引物Tm<58°C：建议58°C退火并延长至90秒"
        else:
            annealing = 60.0
            annealing_note = None
        recommended_conditions = {
            "annealing_temp": {"celsius": annealing, "basis": "standard_TaqMan_two_step"},
            "annealing_temp_note": annealing_note,
            "cycle_count": 40,
            "master_mix": {"type": "probe_based", "examples": ["TaqPath", "PrimeTime"]},
            "primer_conc_nM": 300,
            "probe_conc_nM": 200,
            "rox_reference": bool(payload.get("rox_as_reference", False)),
            "template_input_ng": "50-100",
            "hot_start": {"enabled": True},
        }
        target_reasons = [
            {
                "target_id": target.get("target_id", f"target_{index+1}"),
                "gene": target.get("gene"),
                "selection_method": "user_confirmed" if target.get("gene") else "provided_sequence",
                "reason": target.get("selection_reason") or ("用户指定靶标基因" if target.get("gene") else "用户或数据库Provider提供序列"),
            }
            for index, target in enumerate(targets)
        ]
        if not target_candidates:
            status = WorkflowStatus.FAILED
        elif design_status == WorkflowStatus.SUCCEEDED and variation_verdict != "REDESIGN_RECOMMENDED" and (not include_internal or internal_control and internal_control.get("verdict") != "FAIL"):
            status = WorkflowStatus.SUCCEEDED
        else:
            status = WorkflowStatus.PARTIAL
        if variation_verdict == "NOT_EVALUATED":
            warnings.append("代表性序列数据库尚未接入，WF-05留待服务器部署后执行")
        if include_internal and (not internal_control or internal_control.get("status") == "NOT_CONFIGURED"):
            warnings.append("管家基因数据库尚未接入，内标留待服务器部署后完成")
        evidence = EvidenceLevel.BLAST_VERIFIED if target_candidates and all(candidate.evidence_level == EvidenceLevel.BLAST_VERIFIED for candidate in target_candidates) else EvidenceLevel.IN_SILICO_ONLY
        return WorkflowResult(
            workflow="WF-06", status=status, assay_id=str(payload.get("assay_id") or "complete_diagnostic_panel_v1"),
            input={key: value for key, value in payload.items() if key not in {"targets", "sequence", "sequences"}},
            target={"targets": target_reasons, "host_species": payload.get("host_species"), "sample_type": payload.get("sample_type")},
            candidates=candidates, multiplex=multiplex, internal_control=internal_control, variation=variation,
            recommended_conditions=recommended_conditions,
            self_check={"verdict": "CAUTION" if warnings or errors else "PASS", "risks": sorted(set(warnings))},
            tool_status={"C0": self.catalog.status(), "workflow_components": {"WF-01": True, "WF-02": True, "WF-03": True, "WF-05": True}},
            warnings=sorted(set(warnings)), errors=errors,
            provenance={"agentd_version": "0.3.15", "config_schema_version": self.config["schema_version"], "config_sha256": config_fingerprint(self.config)},
            workflow_trace=trace, evidence_level=evidence,
        )

"""WF-06 end-to-end diagnostic panel orchestration."""

from __future__ import annotations

import copy
from typing import Any

from ..config import config_fingerprint
from ..database import DatabaseCatalog
from ..errors import InputValidationError
from ..models import Candidate, EvidenceLevel, Oligo, WorkflowResult, WorkflowStatus
from .wf01 import WF01SingleTarget
from .wf03 import WF03InternalControl
from .wf05 import WF05VariationTolerance


class WF06CompletePanel:
    def __init__(
        self,
        config: dict,
        catalog: DatabaseCatalog | None = None,
        *,
        wf02_batch_service: Any | None = None,
    ) -> None:
        self.config = config
        self.catalog = catalog or DatabaseCatalog(config)
        self.wf02_batch_service = wf02_batch_service

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
                return next(
                    (
                        candidate
                        for candidate in candidates
                        if candidate.candidate_id == member["candidate_id"]
                        and candidate.metadata.get("target_id") == target_id
                    ),
                    None,
                )
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

    @staticmethod
    def _oligo(value: dict[str, Any]) -> Oligo:
        sequence = str(value.get("sequence_5to3") or "")
        return Oligo(
            sequence_5to3=sequence,
            length_nt=int(value.get("length_nt") or len(sequence)),
            tm_celsius=(
                float(value["tm_celsius"])
                if value.get("tm_celsius") is not None
                else None
            ),
            gc_percent=float(value.get("gc_percent") or 0.0),
            position_start=value.get("position_start"),
            position_end=value.get("position_end"),
            hairpin_dg_kcal=value.get("hairpin_dg_kcal"),
            homodimer_dg_kcal=value.get("homodimer_dg_kcal"),
            three_prime_dimer_dg_kcal=value.get("three_prime_dimer_dg_kcal"),
            thermo_method=str(value.get("thermo_method") or "mapped_from_wf02"),
        )

    @classmethod
    def _candidate(
        cls,
        value: dict[str, Any],
        *,
        target_id: str,
        role: str,
    ) -> Candidate:
        amplicon = value.get("amplicon") or {}
        amplicon_sequence = str(amplicon.get("sequence_5to3") or "")
        if not amplicon_sequence:
            amplicon_sequence = "N" * int(amplicon.get("length_bp") or 0)
        raw_evidence = str(
            value.get("evidence_level") or EvidenceLevel.IN_SILICO_ONLY.value
        )
        try:
            evidence_level = EvidenceLevel(raw_evidence)
        except ValueError:
            evidence_level = EvidenceLevel.IN_SILICO_ONLY
        probe = value.get("probe") or {}
        return Candidate(
            candidate_id=str(value.get("candidate_id") or ""),
            forward_primer=cls._oligo(value.get("forward_primer") or {}),
            reverse_primer=cls._oligo(value.get("reverse_primer") or {}),
            probe=cls._oligo(probe) if probe else None,
            amplicon_sequence_5to3=amplicon_sequence,
            amplicon_start=int(amplicon.get("start") or 0),
            amplicon_end=int(
                amplicon.get("end")
                or max(0, len(amplicon_sequence) - 1)
            ),
            conservation_score=value.get("conservation_score"),
            amplicon_structure=copy.deepcopy(
                value.get("amplicon_structure") or {}
            ),
            evidence_level=evidence_level,
            warnings=[str(item) for item in value.get("warnings") or []],
            score=float(value.get("score") or 0.0),
            rank=value.get("rank"),
            verdict=str(value.get("verdict") or "PENDING"),
            metadata={
                "role": role,
                "target_id": target_id,
                "source_workflow": "WF-02",
            },
        )

    def _run_authoritative_wf02(
        self,
        payload: dict[str, Any],
    ) -> tuple[
        list[Candidate],
        dict[str, Any],
        dict[str, Any] | None,
        WorkflowStatus,
        list[dict[str, Any]],
        list[str],
        list[dict[str, Any]],
    ]:
        if self.wf02_batch_service is None:
            raise InputValidationError(
                "WF-06 多目标路径必须绑定新版 WF-02 批次服务"
            )
        runtime_config = {
            key: payload.get(key)
            for key in ("instrument", "rox_as_reference")
            if payload.get(key) is not None
        }
        wf02_payload = {
            "task_name": payload.get("task_name") or "WF-06::WF-02",
            "targets": copy.deepcopy(payload.get("targets") or []),
            "host_species": payload.get("host_species"),
            "include_internal_control": bool(
                payload.get("include_internal_control", True)
            ),
            "allow_fallback": bool(payload.get("allow_fallback", False)),
        }
        if runtime_config:
            wf02_payload["runtime_config"] = runtime_config
        created = self.wf02_batch_service.create_batch(wf02_payload)
        batch_id = created.get("batch_id")
        if not batch_id:
            issues = copy.deepcopy(
                (created.get("preflight") or {}).get("issues")
                or created.get("issues")
                or []
            )
            return (
                [],
                {
                    "status": "WAITING_INPUT",
                    "implementation": "WF02V01BatchService",
                    "draft_id": created.get("draft_id"),
                    "issues": issues,
                },
                None,
                WorkflowStatus.FAILED,
                [
                    {
                        "step": "WF-02",
                        "status": "WAITING_INPUT",
                        "implementation": "WF02V01BatchService",
                    }
                ],
                [],
                issues,
            )
        batch = self.wf02_batch_service.get_batch(str(batch_id))
        for _ in range(128):
            next_action = str(batch.get("next_action") or "")
            if not next_action.startswith(("RUN_", "CONTINUE_")):
                break
            self.wf02_batch_service.run_until_blocked(str(batch_id))
            updated = self.wf02_batch_service.get_batch(str(batch_id))
            if (
                updated.get("status") == batch.get("status")
                and updated.get("stage") == batch.get("stage")
                and updated.get("next_action") == batch.get("next_action")
            ):
                batch = updated
                break
            batch = updated
        report = None
        if batch.get("report_id"):
            report = self.wf02_batch_service.get_report(str(batch_id))
        target_design = batch.get("target_design") or {}
        candidates: list[Candidate] = []
        for target in target_design.get("targets") or []:
            target_id = str(target.get("target_id") or "")
            candidates.extend(
                self._candidate(item, target_id=target_id, role="target")
                for item in target.get("candidates") or []
            )
        internal = batch.get("internal_control") or None
        if internal:
            candidates.extend(
                self._candidate(
                    item,
                    target_id="internal_control",
                    role="internal_control",
                )
                for item in internal.get("candidates") or []
            )
        panels = copy.deepcopy(
            (report or {}).get("top3_panels")
            or (report or {}).get("partial_panels")
            or []
        )
        multiplex = {
            "status": batch.get("status"),
            "implementation": "WF02V01BatchService",
            "workflow_version": batch.get("workflow_version"),
            "batch_id": batch_id,
            "stage": batch.get("stage"),
            "next_action": batch.get("next_action"),
            "outcome": copy.deepcopy(batch.get("outcome") or {}),
            "panels": panels,
            "canonical_report": copy.deepcopy(report),
        }
        status = (
            WorkflowStatus.SUCCEEDED
            if batch.get("status") == "COMPLETED"
            else WorkflowStatus.PARTIAL
            if candidates
            else WorkflowStatus.FAILED
        )
        trace = copy.deepcopy(batch.get("workflow_trace") or [])
        trace.append(
            {
                "step": "WF-02",
                "status": batch.get("status"),
                "implementation": "WF02V01BatchService",
                "batch_id": batch_id,
            }
        )
        warnings = [
            str(item)
            for panel in panels
            for item in panel.get("warnings") or []
        ]
        errors = [
            copy.deepcopy(item.get("failure") or {})
            for item in target_design.get("targets") or []
            if item.get("status") in {"FAIL", "BLOCKED_RESOURCE"}
        ]
        return candidates, multiplex, internal, status, trace, warnings, errors

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
            multiplex_payload: dict[str, Any] = {
                **copy.deepcopy(common),
                "targets": targets,
                "include_internal_control": include_internal,
            }
            (
                candidates,
                multiplex,
                internal_control,
                design_status,
                wf02_trace,
                wf02_warnings,
                wf02_errors,
            ) = self._run_authoritative_wf02(multiplex_payload)
            warnings.extend(wf02_warnings)
            errors.extend(wf02_errors)
            trace.extend(wf02_trace)

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

"""Versioned WF-04 backend orchestration over existing tool adapters.

This module owns input normalization, node ordering, immutable retry metadata,
and the final deterministic decision. P1, P2, P3, and P6 continue to own all
tool-specific logic.
"""

from __future__ import annotations

import copy
import tempfile
import time
from typing import Any, Protocol

from .. import __version__
from ..config import config_fingerprint
from ..errors import AgentDError, InputValidationError, ToolUnavailableError
from ..mgb import MGBProbeEvaluator
from ..models import (
    Candidate,
    EvidenceLevel,
    SpecificityModeResult,
    SpecificityResult,
    WorkflowResult,
    WorkflowStatus,
    jsonable,
)
from ..primer3_tool import Primer3Adapter
from ..rules import RuleEngine
from ..scoring import score_candidate
from ..sequence import normalize_sequence
from ..specificity import BlastAdapter, SpecificityEvaluator, write_subject_fasta
from ..structure import ViennaRNAAdapter
from .common import evidence_from_specificity


WF04_POLICY_VERSION = "wf04-v0.1-rules-2026-08-25"
WF04_RETRY_STEPS = {
    "WF04_STEP1_P1",
    "WF04_STEP2_P2_A",
    "WF04_STEP3_P2_B",
    "WF04_STEP5_COMPATIBILITY",
}
WF04_RETRY_FIELDS = {
    "target_db",
    "host_db",
    "cross_db",
    "target_species",
    "host_species",
    "template",
    "allow_fallback",
}
_P2_BRANCHES = (
    ("WF04_STEP2_P2_A", "target"),
    ("WF04_STEP3_P2_B", "host"),
    ("WF04_STEP4_P2_C", "cross_species"),
)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def _target_accessions(
    payload: dict,
    target_result: SpecificityModeResult,
) -> list[str]:
    """Collect target identities for the dependent background branch."""

    values: list[str] = []
    target_id = _text(payload.get("target_id"))
    if target_id:
        values.append(target_id)
    resolutions = payload.get("_database_resolution")
    if isinstance(resolutions, list):
        for resolution in resolutions:
            if not isinstance(resolution, dict):
                continue
            if resolution.get("field") != "target_db":
                continue
            version = _text(resolution.get("version"))
            if version:
                values.append(version)
    for hit in target_result.details:
        if not isinstance(hit, dict):
            continue
        accession = _text(hit.get("accession"))
        if accession:
            values.append(accession)
    return _unique(values)


def _duration_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _public_input(payload: dict) -> dict:
    public = {
        key: copy.deepcopy(value)
        for key, value in payload.items()
        if key != "template" and not key.startswith("_")
    }
    input_artifact = payload.get("_wf04_input_artifact")
    if isinstance(input_artifact, dict):
        public["input_artifact"] = copy.deepcopy(input_artifact)
    return public


def normalize_wf04_payload(payload: dict) -> tuple[dict, dict | None]:
    """Validate the public shape and remove private retry context."""

    if not isinstance(payload, dict):
        raise InputValidationError("WF-04输入必须为对象")
    normalized = copy.deepcopy(payload)
    forward = _text(normalized.get("forward_primer"))
    reverse = _text(normalized.get("reverse_primer"))
    if not forward or not reverse:
        raise InputValidationError("WF-04需要forward_primer和reverse_primer")
    normalized["forward_primer"] = normalize_sequence(forward)
    normalized["reverse_primer"] = normalize_sequence(reverse)
    for key in ("probe", "template"):
        value = _text(normalized.get(key))
        if value:
            normalized[key] = normalize_sequence(value)
        else:
            normalized.pop(key, None)
    reentry = normalized.pop("_wf04_retry_context", None)
    if reentry is not None and not isinstance(reentry, dict):
        raise InputValidationError("WF-04重试上下文无效")
    return normalized, reentry


def _mode_from_dict(value: dict, default_mode: str) -> SpecificityModeResult:
    return SpecificityModeResult(
        mode=str(value.get("mode") or default_mode),
        verdict=str(value.get("verdict") or "NOT_CHECKED"),
        blast_hits=int(value.get("blast_hits") or 0),
        details=copy.deepcopy(value.get("details") or []),
        recommendation=value.get("recommendation"),
        mode_specific=copy.deepcopy(value.get("mode_specific") or {}),
        method=str(value.get("method") or "not_run"),
    )


def _not_checked_mode(mode: str, reason: str) -> SpecificityModeResult:
    return SpecificityModeResult(
        mode=mode,
        verdict="NOT_CHECKED",
        blast_hits=0,
        recommendation=reason,
        method="not_run",
    )


def _branch_status(result: SpecificityModeResult, error_code: str | None) -> str:
    if error_code:
        return "ERROR"
    if result.verdict == "NOT_CHECKED" or result.method == "not_run":
        return "NOT_CHECKED"
    if result.method != "blastn":
        return "DEGRADED"
    return "COMPLETED"


def _compact_candidate(candidate: dict | None) -> dict | None:
    if not isinstance(candidate, dict):
        return None
    return {
        "candidate_id": candidate.get("candidate_id"),
        "verdict": candidate.get("verdict"),
        "score": candidate.get("score"),
        "evidence_level": candidate.get("evidence_level"),
        "forward_primer": copy.deepcopy(candidate.get("forward_primer")),
        "reverse_primer": copy.deepcopy(candidate.get("reverse_primer")),
        "probe": copy.deepcopy(candidate.get("probe")),
        "amplicon": copy.deepcopy(candidate.get("amplicon")),
        "warnings": copy.deepcopy(candidate.get("warnings") or []),
    }


def enrich_wf04_result(result: dict) -> dict:
    """Add the v0.1 control fields to the canonical result."""

    if result.get("workflow") != "WF-04":
        return result
    self_check = result.get("self_check") or {}
    provenance = result.get("provenance") or {}
    steps = copy.deepcopy(result.get("workflow_trace") or [])
    retryable_steps = [
        str(step.get("step"))
        for step in steps
        if step.get("retryable") and step.get("step") in WF04_RETRY_STEPS
    ]
    execution_status = str(result.get("status") or WorkflowStatus.FAILED.value)
    decision = str(self_check.get("verdict") or "INCONCLUSIVE")
    retry_allowed = bool(retryable_steps) and (
        execution_status in {WorkflowStatus.PARTIAL.value, WorkflowStatus.FAILED.value}
        or decision == "INCONCLUSIVE"
    )
    result.update(
        {
            "workflow_version": WF04_POLICY_VERSION,
            "parent_job_id": provenance.get("parent_job_id"),
            "execution_status": execution_status,
            "decision": decision,
            "decision_reasons": copy.deepcopy(self_check.get("reason_codes") or []),
            "steps": steps,
            "next_action": "RETRY" if retry_allowed else "END",
            "retry": {
                "allowed": retry_allowed,
                "retryable_steps": retryable_steps,
                "tool": "retry_wf04_validation" if retry_allowed else None,
            },
            "report_tool": "get_wf04_report",
        }
    )
    return result


def summarize_wf04_result(result: dict) -> dict:
    """Return the bounded report consumed by REST and MCP callers."""

    if result.get("workflow") != "WF-04":
        raise InputValidationError("任务不是WF-04结果")
    enriched = enrich_wf04_result(copy.deepcopy(result))
    candidates = enriched.get("candidates") or []
    return {
        "workflow": "WF-04",
        "workflow_version": enriched.get("workflow_version"),
        "job_id": enriched.get("job_id"),
        "parent_job_id": enriched.get("parent_job_id"),
        "execution_status": enriched.get("execution_status"),
        "decision": enriched.get("decision"),
        "decision_reasons": enriched.get("decision_reasons", []),
        "next_action": enriched.get("next_action"),
        "assay_id": enriched.get("assay_id"),
        "evidence_level": enriched.get("evidence_level"),
        "steps": enriched.get("steps", []),
        "retry": enriched.get("retry"),
        "validation_summary": copy.deepcopy(enriched.get("self_check") or {}),
        "warnings": copy.deepcopy(enriched.get("warnings") or []),
        "errors": copy.deepcopy(enriched.get("errors") or []),
        "candidate": _compact_candidate(candidates[0] if candidates else None),
        "report_tool": "get_wf04_report",
    }


class WF04GatewayProtocol(Protocol):
    def validate_pair(self, payload: dict) -> Candidate: ...

    def check_p2(
        self,
        branch: str,
        candidate: Candidate,
        payload: dict,
    ) -> SpecificityModeResult: ...

    def assess_p3(self, candidate: Candidate, payload: dict) -> dict: ...

    def assess_p6(self, candidate: Candidate) -> dict: ...

    def status(self) -> dict[str, dict]: ...


class WF04ToolGateway:
    """Thin injectable boundary over the existing tool adapters."""

    def __init__(self, config: dict, *, allow_fallback: bool) -> None:
        self.primer3 = Primer3Adapter(config, allow_fallback=allow_fallback)
        self.blast = BlastAdapter(
            config["tools"]["blastn_executable"],
            allow_fallback=allow_fallback,
        )
        self.specificity = SpecificityEvaluator(self.blast, self.primer3)
        self.mgb = MGBProbeEvaluator(self.primer3, config)
        self.vienna = ViennaRNAAdapter(config)

    @staticmethod
    def _queries(candidate: Candidate) -> dict[str, str]:
        queries = {
            "forward": candidate.forward_primer.sequence_5to3,
            "reverse": candidate.reverse_primer.sequence_5to3,
        }
        if candidate.probe is not None:
            queries["probe"] = candidate.probe.sequence_5to3
        return queries

    def validate_pair(self, payload: dict) -> Candidate:
        return self.primer3.validate_pair(
            payload["forward_primer"],
            payload["reverse_primer"],
            probe=payload.get("probe"),
            template=payload.get("template"),
            sequence_id=str(payload.get("target_id") or "existing"),
        )

    def check_p2(
        self,
        branch: str,
        candidate: Candidate,
        payload: dict,
    ) -> SpecificityModeResult:
        queries = self._queries(candidate)
        if branch == "target":
            return self.specificity.check_target(queries, payload.get("target_db"))
        if branch == "host":
            return self.specificity.check_host(
                queries,
                payload.get("host_db"),
                payload.get("host_species"),
            )
        if branch == "cross_species":
            return self.specificity.check_cross_species(
                queries,
                payload.get("cross_db"),
                payload.get("target_species"),
                target_accessions=set(payload.get("_target_accessions") or []),
            )
        raise InputValidationError(
            "WF-04包含未知P2分支",
            details={"branch": branch},
        )

    def assess_p3(self, candidate: Candidate, payload: dict) -> dict:
        if candidate.probe is None:
            raise InputValidationError("P3评估需要probe序列")
        return self.mgb.assess(
            candidate.probe.sequence_5to3,
            fluorophore=payload.get("fluorophore"),
            snp_position=payload.get("snp_position"),
        )

    def assess_p6(self, candidate: Candidate) -> dict:
        return self.vienna.assess_full(candidate.amplicon_sequence_5to3)

    def status(self) -> dict[str, dict]:
        return {
            "P1": self.primer3.status(),
            "P2": self.blast.status(),
            "P3": self.mgb.status(),
            "P6": self.vienna.status(),
        }


class WF04V01Validation:
    """Six-node deterministic WF-04 v0.1 backend."""

    def __init__(
        self,
        config: dict,
        *,
        gateway: WF04GatewayProtocol | None = None,
        rule_engine: RuleEngine | None = None,
    ) -> None:
        self.config = config
        self._injected_gateway = gateway
        self.rule_engine = rule_engine or RuleEngine()

    def _gateway(self, payload: dict) -> WF04GatewayProtocol:
        if self._injected_gateway is not None:
            return self._injected_gateway
        allow_fallback = bool(
            payload.get(
                "allow_fallback",
                self.config["tools"]["allow_development_fallback"],
            )
        )
        return WF04ToolGateway(self.config, allow_fallback=allow_fallback)

    @staticmethod
    def _run_p2_branch(
        branch: str,
        gateway: WF04GatewayProtocol,
        candidate: Candidate,
        payload: dict,
        reused: dict | None,
    ) -> tuple[SpecificityModeResult, dict, dict | None]:
        started = time.perf_counter()
        mode_name = {
            "target": "check_target",
            "host": "check_host",
            "cross_species": "check_cross_species",
        }[branch]
        if isinstance(reused, dict):
            result = _mode_from_dict(reused, mode_name)
            return result, {
                "status": "REUSED",
                "reused": True,
                "duration_ms": _duration_ms(started),
                "error_code": None,
            }, None
        try:
            result = gateway.check_p2(branch, candidate, payload)
            return result, {
                "status": _branch_status(result, None),
                "reused": False,
                "duration_ms": _duration_ms(started),
                "error_code": None,
            }, None
        except AgentDError as exc:
            error = exc.to_dict()
        except (OSError, TimeoutError) as exc:
            error = {
                "code": "P2_EXECUTION_ERROR",
                "message": str(exc),
                "details": {"exception_type": type(exc).__name__},
            }
        result = _not_checked_mode(mode_name, str(error.get("message") or "P2执行失败"))
        return result, {
            "status": "ERROR",
            "reused": False,
            "duration_ms": _duration_ms(started),
            "error_code": error.get("code"),
        }, error

    def _provenance(self, reentry: dict | None) -> dict:
        provenance = {
            "agentd_version": __version__,
            "config_schema_version": self.config["schema_version"],
            "config_sha256": config_fingerprint(self.config),
            "workflow_policy_version": WF04_POLICY_VERSION,
            "tool_boundary": {
                "workflow_calls": ["P1", "P2", "P3_OPTIONAL", "P6_OPTIONAL"],
                "scientific_algorithms_implemented_in_workflow": [],
            },
        }
        if reentry and reentry.get("parent_job_id"):
            provenance["parent_job_id"] = reentry["parent_job_id"]
            provenance["retry"] = {
                "reason": reentry.get("reason"),
                "retry_steps": copy.deepcopy(reentry.get("retry_steps") or []),
            }
        return provenance

    def _failed_before_candidate(
        self,
        payload: dict,
        reentry: dict | None,
        gateway: WF04GatewayProtocol,
        error: AgentDError,
        duration_ms: float,
    ) -> WorkflowResult:
        trace = [
            {"step": "WF04_INPUT", "status": "COMPLETED", "retryable": False},
            {
                "step": "WF04_STEP1_P1",
                "status": "ERROR",
                "tool": "P1",
                "retryable": True,
                "reused": False,
                "duration_ms": duration_ms,
                "error_code": error.code,
            },
            {
                "step": "WF04_STEP6_DECISION",
                "status": "INCONCLUSIVE",
                "retryable": False,
                "decision": "INCONCLUSIVE",
            },
        ]
        return WorkflowResult(
            workflow="WF-04",
            status=WorkflowStatus.FAILED,
            assay_id=str(
                payload.get("assay_id")
                or f"{payload.get('target_id', 'existing')}_validation_v01"
            ),
            input=_public_input(payload),
            target={
                "target_id": payload.get("target_id"),
                "template_provided": bool(payload.get("template")),
            },
            candidates=[],
            self_check={
                "verdict": "INCONCLUSIVE",
                "execution_status": WorkflowStatus.FAILED.value,
                "reason_codes": ["P1_EXECUTION_FAILED"],
                "risks": [],
            },
            workflow_trace=trace,
            tool_status={
                **gateway.status(),
                "C7": {"tool": "C7_schema", "available": True},
            },
            errors=[error.to_dict()],
            provenance=self._provenance(reentry),
            evidence_level=EvidenceLevel.IN_SILICO_ONLY,
        )

    @staticmethod
    def _specificity_result(
        modes: dict[str, SpecificityModeResult],
    ) -> tuple[SpecificityResult, bool, bool]:
        critical = (modes["target"], modes["host"])
        complete = all(
            item.method == "blastn" and item.verdict != "NOT_CHECKED"
            for item in critical
        )
        decisive_fail = any(
            item.method == "blastn" and item.verdict == "FAIL"
            for item in critical
        )
        if decisive_fail:
            rating = "FAIL"
        elif not complete:
            rating = "INCONCLUSIVE"
        elif any(item.verdict != "PASS" for item in modes.values()):
            rating = "PASS_WITH_WARNINGS"
        else:
            rating = "PASS"
        method = (
            "blastn"
            if {item.method for item in modes.values()} == {"blastn"}
            else "mixed_or_degraded"
        )
        result = SpecificityResult(
            target=modes["target"],
            host=modes["host"],
            cross_species=modes["cross_species"],
            rating=rating,
            method=method,
            warnings=[
                str(item.recommendation)
                for item in modes.values()
                if item.recommendation
            ],
        )
        return result, complete, decisive_fail

    def run(self, payload: dict) -> WorkflowResult:
        normalized, reentry = normalize_wf04_payload(payload)
        gateway = self._gateway(normalized)
        trace: list[dict] = [
            {
                "step": "WF04_INPUT",
                "status": "COMPLETED",
                "retryable": False,
                "policy_version": WF04_POLICY_VERSION,
            }
        ]
        step1_started = time.perf_counter()
        try:
            candidate = gateway.validate_pair(normalized)
        except InputValidationError:
            raise
        except ToolUnavailableError as exc:
            return self._failed_before_candidate(
                normalized,
                reentry,
                gateway,
                exc,
                _duration_ms(step1_started),
            )
        p1_status = gateway.status().get("P1") or {}
        p1_degraded = p1_status.get("available") is False
        trace.append(
            {
                "step": "WF04_STEP1_P1",
                "status": "DEGRADED" if p1_degraded else "COMPLETED",
                "tool": "P1",
                "retryable": p1_degraded,
                "reused": False,
                "duration_ms": _duration_ms(step1_started),
                "error_code": "P1_DEGRADED" if p1_degraded else None,
            }
        )

        warnings: list[str] = []
        technical_errors: list[dict] = []
        use_p3 = bool(
            normalized.get("use_mgb")
            or str(normalized.get("probe_type", "")).upper()
            in {"MGB", "TAQMAN_MGB"}
        )
        if use_p3 and candidate.probe is None:
            # This is an input-shape check, not an early Step 5 execution.
            raise InputValidationError("P3评估需要probe序列")

        execution_payload = copy.deepcopy(normalized)
        retry_steps = set((reentry or {}).get("retry_steps") or [])
        reused_modes = (reentry or {}).get("reuse_specificity") or {}
        with tempfile.TemporaryDirectory(prefix="agentd-wf04-v01-") as temp_dir:
            if not execution_payload.get("target_db") and execution_payload.get("template"):
                execution_payload["target_db"] = str(
                    write_subject_fasta(
                        execution_payload["template"],
                        temp_dir,
                        record_id=str(execution_payload.get("target_id") or "target"),
                    )
                )
            branch_outputs: dict[
                str,
                tuple[SpecificityModeResult, dict, dict | None],
            ] = {}
            # The numbered expert workflow is the execution contract.  Run
            # target and host checks as Step 2 then Step 3; do not hide a
            # parallel physical schedule behind a sequential audit trace.
            for step, branch in _P2_BRANCHES[:2]:
                reused = reused_modes.get(branch)
                if step in retry_steps:
                    reused = None
                branch_outputs[branch] = self._run_p2_branch(
                    branch,
                    gateway,
                    candidate,
                    execution_payload,
                    reused,
                )
            execution_payload["_target_accessions"] = _target_accessions(
                execution_payload,
                branch_outputs["target"][0],
            )
            cross_step, cross_branch = _P2_BRANCHES[2]
            cross_reused = reused_modes.get(cross_branch)
            if cross_step in retry_steps:
                cross_reused = None
            branch_outputs[cross_branch] = self._run_p2_branch(
                cross_branch,
                gateway,
                candidate,
                execution_payload,
                cross_reused,
            )

        modes: dict[str, SpecificityModeResult] = {}
        branch_meta: dict[str, dict] = {}
        for step, branch in _P2_BRANCHES:
            mode, metadata, error = branch_outputs[branch]
            modes[branch] = mode
            branch_meta[branch] = metadata
            required = branch in {"target", "host"}
            retryable = required and metadata["status"] in {
                "ERROR",
                "NOT_CHECKED",
                "DEGRADED",
            }
            trace.append(
                {
                    "step": step,
                    "status": metadata["status"],
                    "tool": "P2",
                    "retryable": retryable,
                    "reused": metadata["reused"],
                    "duration_ms": metadata["duration_ms"],
                    "error_code": metadata["error_code"],
                    "verdict": mode.verdict,
                    "method": mode.method,
                }
            )
            if error:
                if required:
                    technical_errors.append(error)
                else:
                    warnings.append(str(error.get("code") or "P2_C_ERROR"))
            elif metadata["status"] in {"NOT_CHECKED", "DEGRADED"}:
                code = f"P2_{branch.upper()}_{metadata['status']}"
                if required:
                    technical_errors.append(
                        {
                            "code": code,
                            "message": mode.recommendation or code,
                            "details": {"step": step, "method": mode.method},
                        }
                    )
                else:
                    warnings.append(code)

        specificity, critical_complete, decisive_p2_fail = self._specificity_result(
            modes
        )
        candidate.specificity = specificity
        candidate.evidence_level = evidence_from_specificity(specificity)

        # Step 5 starts only after all three P2 steps have finished.  P1 pair
        # thermodynamic measurements were collected in Step 1; this step adds
        # conditional P3/P6 evidence and performs the compatibility verdict.
        step5_started = time.perf_counter()
        p3_error: dict | None = None
        if use_p3:
            try:
                candidate.mgb_assessment = gateway.assess_p3(candidate, normalized)
                candidate.warnings.extend(
                    f"P3:{code}"
                    for code in candidate.mgb_assessment.get("warnings", [])
                )
                candidate.metadata["probe_type"] = "TaqMan_MGB"
            except AgentDError as exc:
                p3_error = exc.to_dict()
            except (OSError, RuntimeError) as exc:
                p3_error = {
                    "code": "P3_EXECUTION_ERROR",
                    "message": str(exc),
                    "details": {"exception_type": type(exc).__name__},
                }
            if p3_error:
                technical_errors.append(p3_error)
                warnings.append(str(p3_error.get("code") or "P3_EXECUTION_ERROR"))
        else:
            candidate.metadata["probe_type"] = (
                "TaqMan_BHQ" if candidate.probe else "none"
            )

        p6_status = "NOT_APPLICABLE"
        p6_tool_status = gateway.status().get("P6") or {}
        if candidate.amplicon_sequence_5to3:
            if p6_tool_status.get("available") is False:
                candidate.amplicon_structure = {
                    "status": "not_evaluated",
                    "mfe_dg": None,
                    "reason": "P6_UNAVAILABLE",
                }
                p6_status = "SKIPPED"
                warnings.append("P6_UNAVAILABLE")
            else:
                try:
                    candidate.amplicon_structure = gateway.assess_p6(candidate)
                    p6_status = "COMPLETED"
                except Exception as exc:  # optional boundary must not abort WF-04
                    candidate.amplicon_structure = {
                        "status": "not_evaluated",
                        "mfe_dg": None,
                        "reason": type(exc).__name__,
                    }
                    p6_status = "SKIPPED"
                    warnings.append("P6_EXECUTION_SKIPPED")

        candidate.rules = self.rule_engine.evaluate(candidate)
        self.rule_engine.apply_verdict(candidate)
        local_rule_fail = candidate.verdict == "FAIL"
        local_incomplete = candidate.verdict == "PARTIAL"
        if local_incomplete:
            trace[1].update(
                {
                    "status": "DEGRADED",
                    "retryable": True,
                    "error_code": (
                        trace[1].get("error_code")
                        or "P1_REQUIRED_EVIDENCE_INCOMPLETE"
                    ),
                }
            )
        p3_business_fail = bool(
            candidate.mgb_assessment
            and not candidate.mgb_assessment.get("all_hard_pass", True)
        )
        if p3_business_fail:
            candidate.verdict = "FAIL"

        step5_status = "ERROR" if p3_error else (
            "DEGRADED" if p6_status == "SKIPPED" else "COMPLETED"
        )
        trace.append(
            {
                "step": "WF04_STEP5_COMPATIBILITY",
                "status": step5_status,
                "tool": "P1/P3/P6",
                "retryable": p3_error is not None,
                "reused": False,
                "duration_ms": _duration_ms(step5_started),
                "error_code": p3_error.get("code") if p3_error else None,
                "p3_status": (
                    "ERROR"
                    if p3_error
                    else ("COMPLETED" if use_p3 else "NOT_APPLICABLE")
                ),
                "p6_status": p6_status,
            }
        )

        # Step 6 owns scoring and the deterministic final decision.
        score_candidate(candidate, self.config)
        candidate.rank = 1

        reason_codes: list[str] = []
        if local_rule_fail:
            reason_codes.append("LOCAL_RULE_FAILURE")
        if decisive_p2_fail:
            reason_codes.append("P2_DECISION_FAILURE")
        if p3_business_fail:
            reason_codes.append("P3_DECISION_FAILURE")
        if local_incomplete:
            reason_codes.append("LOCAL_RULE_EVIDENCE_INCOMPLETE")
        if not critical_complete:
            reason_codes.append("P2_REQUIRED_EVIDENCE_INCOMPLETE")
        if p3_error:
            reason_codes.append("P3_EXECUTION_FAILED")
        if p1_degraded:
            reason_codes.append("P1_REQUIRED_EVIDENCE_DEGRADED")

        if local_rule_fail or decisive_p2_fail or p3_business_fail:
            decision = "FAIL"
        elif p1_degraded or local_incomplete or not critical_complete or p3_error:
            decision = "INCONCLUSIVE"
        elif (
            candidate.verdict == "PASS_WITH_WARNINGS"
            or specificity.rating == "PASS_WITH_WARNINGS"
            or warnings
            or candidate.warnings
        ):
            decision = "PASS_WITH_WARNINGS"
        else:
            decision = "PASS"

        required_gap = (
            p1_degraded
            or local_incomplete
            or not critical_complete
            or p3_error is not None
        )
        execution_status = (
            WorkflowStatus.PARTIAL if required_gap else WorkflowStatus.SUCCEEDED
        )
        trace.append(
            {
                "step": "WF04_STEP6_DECISION",
                "status": "COMPLETED",
                "retryable": False,
                "decision": decision,
                "execution_status": execution_status.value,
                "reason_codes": reason_codes,
            }
        )

        warnings.extend(candidate.warnings)
        warnings.extend(specificity.warnings)
        warnings = _unique(warnings)
        return WorkflowResult(
            workflow="WF-04",
            status=execution_status,
            assay_id=str(
                normalized.get("assay_id")
                or f"{normalized.get('target_id', 'existing')}_validation_v01"
            ),
            input=_public_input(normalized),
            target={
                "target_id": normalized.get("target_id"),
                "organism": normalized.get("target_species"),
                "template_provided": bool(normalized.get("template")),
                "template_length_nt": (
                    len(normalized["template"])
                    if normalized.get("template")
                    else None
                ),
            },
            candidates=[candidate],
            specificity=jsonable(specificity),
            self_check={
                "verdict": decision,
                "execution_status": execution_status.value,
                "reason_codes": reason_codes,
                "checks": {
                    "P1": trace[1]["status"],
                    "P2_A": branch_meta["target"]["status"],
                    "P2_B": branch_meta["host"]["status"],
                    "P2_C": branch_meta["cross_species"]["status"],
                    "STEP5": step5_status,
                },
                "risks": warnings,
            },
            workflow_trace=trace,
            tool_status={
                **gateway.status(),
                "C1": {"tool": "C1_sequence_io", "available": True},
                "C7": {"tool": "C7_schema", "available": True},
            },
            warnings=warnings,
            errors=technical_errors,
            provenance=self._provenance(reentry),
            evidence_level=candidate.evidence_level,
        )

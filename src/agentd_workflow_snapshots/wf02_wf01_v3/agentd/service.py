"""Application service and phase-three workflow router."""

from __future__ import annotations

import copy
import logging
import os
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from . import __version__
from .audit import AgentAuditLog
from .alignment import MafftAdapter
from .config import PROJECT_ROOT, load_config
from .database import DatabaseCatalog
from .errors import InputValidationError
from .input_artifacts import HiMatInputArtifactStore
from .job_store import JobStore
from .primer3_tool import Primer3Adapter
from .primer3_design_tool import Primer3DesignTool
from .mgb import MGBProbeEvaluator
from .mcp_wf01_input import normalize_wf01_mcp_payload
from .object_storage import RustFSArtifactPublisher
from .multiplex import MultiplexEvaluator
from .internal_control import InternalControlDesigner
from .reports import generate_reports
from .schema import assemble
from .specificity import (
    BlastAdapter,
    P2_POLICY_STATUS as SPECIFICITY_POLICY_STATUS,
    P2_POLICY_VERSION as SPECIFICITY_POLICY_VERSION,
)
from .structure import ViennaRNAAdapter
from .ui_messages import ui_message_for_batch
from .wf01_agent_view import (
    compact_candidate,
    compact_report_page,
    next_stage_for_batch,
    summarize_wf01_batch,
    summarize_wf01_report,
)
from .wf01_v02 import WF01V02BatchService
from .wf01_v02_m3 import (
    FINAL_RANK_TIE_BREAKERS,
    SCORING_POLICY_STATUS,
    SCORING_POLICY_VERSION,
    SCORING_SCALE,
    SCORING_WEIGHTS,
    SOFT_PENALTIES_PERCENT,
)
from .wf02_v01 import WF02V01BatchService, summarize_wf02_batch
from .workflow_delivery import WorkflowTerminalDeliveryStore
from .workflows import (
    WF01SingleTarget,
    WF03InternalControl,
    WF04V01Validation,
    WF05VariationTolerance,
    WF06CompletePanel,
    WF07PrimerDiagnosis,
)
from .workflows.wf03 import (
    WF03_POLICY_VERSION,
    WF03_REENTRY_FIELDS,
    summarize_wf03_result,
)
from .workflows.wf04_v01 import (
    WF04_POLICY_VERSION,
    WF04_RETRY_FIELDS,
    WF04_RETRY_STEPS,
    enrich_wf04_result,
    summarize_wf04_result,
)


LOGGER = logging.getLogger("agentd.service")


class AgentDService:
    def __init__(
        self,
        *,
        config_path: str | Path | None = None,
        config_overrides: dict | None = None,
        state_dir: str | Path | None = None,
        artifact_publisher: RustFSArtifactPublisher | None = None,
        input_artifact_store: HiMatInputArtifactStore | None = None,
    ) -> None:
        self.config = load_config(config_path, overrides=config_overrides)
        self.catalog = DatabaseCatalog(self.config)
        selected_state_dir = state_dir or os.environ.get("AGENTD_STATE_DIR") or PROJECT_ROOT / "runtime" / "jobs"
        self.store = JobStore(selected_state_dir)
        self.input_artifacts = (
            input_artifact_store
            if input_artifact_store is not None
            else HiMatInputArtifactStore(self.store.root)
        )
        self.audit = AgentAuditLog(self.store.root)
        self.p6_vienna = ViennaRNAAdapter(self.config)
        self.wf01_v02 = WF01V02BatchService(
            self.store.root,
            self.config,
            self.catalog,
            artifact_publisher,
        )
        self.workflow_delivery = WorkflowTerminalDeliveryStore(
            self.store.root,
            self.wf01_v02.artifact_publisher,
        )
        self.wf02_v01 = WF02V01BatchService(
            self.store.root,
            self.config,
            gateway=self,
            artifact_resolver=self.input_artifacts.resolve,
        )

    @staticmethod
    def _with_wf01_ui_message(result: dict) -> dict:
        batch = result.get("batch") if isinstance(result.get("batch"), dict) else result
        result["ui_message"] = ui_message_for_batch(batch)
        if (
            isinstance(result["ui_message"], dict)
            and (result["ui_message"].get("flowControl") or {}).get("action")
            == "WAIT_FOR_USER"
        ):
            # This value is authoritative even when an underlying stage had
            # proposed a later action. A pending user interaction always wins.
            result["next_action"] = "WAIT_FOR_USER"
        return result

    def download_himat_file(self, file_id: int) -> dict:
        """Download a HiMat upload and return a model-safe internal reference."""
        return self.input_artifacts.download(file_id)

    def materialize_wf01_input(self, payload: dict) -> dict:
        """Resolve a WF-01 input artifact without exposing its body to MCP."""
        return self.input_artifacts.materialize_wf01_payload(payload)

    def _prepare_wf01_v02_input(
        self,
        payload: dict,
        design_input_form: dict | None = None,
        user_field_updates: dict | None = None,
    ) -> tuple[dict, dict]:
        """Normalize and materialize WF-01 input exactly once at the service boundary."""
        normalized, mapping = normalize_wf01_mcp_payload(
            payload,
            design_input_form,
            user_field_updates,
        )
        return self.materialize_wf01_input(normalized), mapping

    def run(self, workflow: str, payload: dict, *, persist: bool = True) -> dict:
        normalized = workflow.upper().replace("_", "-")
        if normalized in {"WF01", "WF-1"}:
            normalized = "WF-01"
        if normalized in {"WF02", "WF-2"}:
            normalized = "WF-02"
        if normalized in {"WF03", "WF-3"}:
            normalized = "WF-03"
        if normalized in {"WF04", "WF-4"}:
            normalized = "WF-04"
        if normalized in {"WF05", "WF-5"}:
            normalized = "WF-05"
        if normalized in {"WF06", "WF-6"}:
            normalized = "WF-06"
        if normalized in {"WF07", "WF-7"}:
            normalized = "WF-07"
        if normalized == "WF-02":
            # WF-02 has one authoritative implementation; compatibility
            # routes create the same versioned batch and own no algorithms.
            return self.wf02_v01.create_batch(payload, persist=persist)
        factories = {
            "WF-01": WF01SingleTarget,
            "WF-03": WF03InternalControl,
            "WF-04": WF04V01Validation,
            "WF-05": WF05VariationTolerance,
            "WF-06": WF06CompletePanel,
            "WF-07": WF07PrimerDiagnosis,
        }
        if normalized not in factories:
            raise InputValidationError(
                "支持WF-01至WF-07",
                details={
                    "requested": workflow,
                    "supported": [f"WF-{index:02d}" for index in range(1, 8)],
                },
            )
        source_payload = (
            copy.deepcopy(payload)
            if normalized in {"WF-03", "WF-04"}
            else None
        )
        if normalized == "WF-03":
            payload = self.input_artifacts.materialize_wf03_payload(payload)
        elif normalized == "WF-04":
            payload = self.input_artifacts.materialize_wf04_payload(payload)
        else:
            payload = self.input_artifacts.attach_path(payload)
        started = time.perf_counter()
        enriched = self.catalog.enrich_payload(normalized, payload)
        if normalized == "WF-06":
            workflow_instance = WF06CompletePanel(
                self.config,
                self.catalog,
                wf02_batch_service=self.wf02_v01,
            )
        elif normalized in {"WF-03", "WF-07"}:
            workflow_instance = factories[normalized](self.config, self.catalog)
        else:
            workflow_instance = factories[normalized](self.config)
        result = workflow_instance.run(enriched)
        result.tool_status.setdefault("C0", self.catalog.status())
        if enriched.get("_database_resolution"):
            result.provenance["database_resolution"] = enriched["_database_resolution"]
        result.reports = generate_reports(result)
        data = assemble(result)
        if normalized == "WF-04":
            data = enrich_wf04_result(data)
        if persist:
            self.store.save(data)
            if normalized == "WF-03":
                self.store.save_attachment(
                    data["job_id"],
                    "wf03-input",
                    source_payload or {},
                )
            elif normalized == "WF-04":
                self.store.save_attachment(
                    data["job_id"],
                    "wf04-input",
                    source_payload or {},
                )
        LOGGER.info(
            "workflow completed",
            extra={
                "job_id": data["job_id"],
                "workflow": normalized,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            },
        )
        return data

    def get_job(self, job_id: str) -> dict:
        return self.store.get(job_id)

    def run_wf03_internal_control(self, payload: dict) -> dict:
        """Run the deterministic WF-03 backend and persist its full result."""

        return self.run("WF-03", copy.deepcopy(payload), persist=True)

    def get_wf03_result(self, job_id: str) -> dict:
        """Read one persisted WF-03 result, rejecting cross-workflow IDs."""

        result = self.store.get(job_id)
        if result.get("workflow") != "WF-03":
            raise InputValidationError(
                "任务不是WF-03结果",
                details={"job_id": job_id, "workflow": result.get("workflow")},
            )
        return result

    def get_wf03_report(self, job_id: str) -> dict:
        """Return the bounded, deterministic WF-03 report view for an agent."""

        return summarize_wf03_result(
            self.get_wf03_result(job_id),
            include_candidates=True,
        )

    def get_wf03_delivery(self, job_id: str) -> dict:
        """Return the WF-01-style terminal envelope for one WF-03 job."""

        result = self.get_wf03_result(job_id)
        delivery = self.workflow_delivery.build_delivery(result)
        control = summarize_wf03_result(result, include_candidates=False)
        delivery["next_action"] = control["next_action"]
        return delivery

    def retry_wf03_internal_control(
        self,
        job_id: str,
        response: dict | None = None,
    ) -> dict:
        """Create an immutable WF-03 Step-1 re-entry from a failed result.

        The caller supplies only new user evidence or requests a rerun after a
        dependency repair.  The backend restores the original input, applies
        the allow-listed patch, and invokes the same WF-03/P5 implementation.
        """

        previous = self.get_wf03_result(job_id)
        internal = previous.get("internal_control") or {}
        fallback = internal.get("fallback")
        if not isinstance(fallback, dict) or not fallback.get("can_reenter_step1"):
            raise InputValidationError(
                "该WF-03任务当前不需要Step 1重入",
                details={"job_id": job_id, "status": previous.get("status")},
            )
        patch = copy.deepcopy(response or {})
        if not isinstance(patch, dict):
            raise InputValidationError("WF-03重入响应必须为对象")
        reason = str(patch.pop("reason", "用户补充内标基因证据后重入Step 1")).strip()
        unsupported = sorted(set(patch) - WF03_REENTRY_FIELDS)
        if unsupported:
            raise InputValidationError(
                "WF-03重入包含不支持的字段",
                details={
                    "unsupported_fields": unsupported,
                    "accepted_fields": sorted(WF03_REENTRY_FIELDS),
                },
            )
        try:
            source = self.store.get_attachment(job_id, "wf03-input")
        except InputValidationError:
            source = copy.deepcopy(previous.get("input") or {})
        source.pop("_wf03_reentry_context", None)
        for key, value in patch.items():
            if key in {"gene_accessions", "gene_sequences", "gene_fasta_paths"}:
                current = source.get(key)
                if not isinstance(current, dict):
                    current = {}
                if not isinstance(value, dict):
                    raise InputValidationError(
                        f"WF-03重入字段{key}必须为对象"
                    )
                source[key] = {**current, **copy.deepcopy(value)}
            else:
                source[key] = copy.deepcopy(value)
        source["_wf03_reentry_context"] = {
            "parent_job_id": job_id,
            "reason": reason,
            "applied_fields": sorted(patch),
            "parent_failure_reason_code": fallback.get("reason_code"),
        }
        return self.run_wf03_internal_control(source)

    def run_wf04_validation(self, payload: dict) -> dict:
        """Run the authoritative WF-04 v0.1 backend and persist its result."""

        return self.run("WF-04", copy.deepcopy(payload), persist=True)

    def get_wf04_result(self, job_id: str) -> dict:
        """Read one persisted WF-04 result, rejecting cross-workflow IDs."""

        result = self.store.get(job_id)
        if result.get("workflow") != "WF-04":
            raise InputValidationError(
                "任务不是WF-04结果",
                details={"job_id": job_id, "workflow": result.get("workflow")},
            )
        return enrich_wf04_result(result)

    def get_wf04_report(self, job_id: str) -> dict:
        """Return the bounded deterministic WF-04 report used by agents."""

        return summarize_wf04_result(self.get_wf04_result(job_id))

    def get_wf04_delivery(self, job_id: str) -> dict:
        """Return the WF-01-style terminal envelope for one WF-04 job."""

        return self.workflow_delivery.build_delivery(self.get_wf04_result(job_id))

    def get_workflow_terminal_export_path(
        self,
        job_id: str,
        file_name: str,
    ) -> Path:
        """Resolve a WF-03/WF-04 terminal attachment for the shared /data route."""

        return self.workflow_delivery.get_export_path(job_id, file_name)

    def retry_wf04_validation(
        self,
        job_id: str,
        response: dict | None = None,
    ) -> dict:
        """Create an immutable WF-04 child job and reuse unaffected P2 nodes."""

        previous = self.get_wf04_result(job_id)
        retry = previous.get("retry") or {}
        if not retry.get("allowed"):
            raise InputValidationError(
                "该WF-04任务当前不可重试",
                details={
                    "job_id": job_id,
                    "execution_status": previous.get("execution_status"),
                    "decision": previous.get("decision"),
                },
            )
        patch = copy.deepcopy(response or {})
        if not isinstance(patch, dict):
            raise InputValidationError("WF-04重试响应必须为对象")
        reason = str(patch.pop("reason", "重试未完成节点")).strip()
        requested_steps = patch.pop("retry_steps", None)
        if requested_steps is None:
            requested_steps = retry.get("retryable_steps") or []
        if not isinstance(requested_steps, list) or not requested_steps:
            raise InputValidationError("WF-04重试至少需要一个retry_steps节点")
        invalid_steps = sorted(set(requested_steps) - WF04_RETRY_STEPS)
        if invalid_steps:
            raise InputValidationError(
                "WF-04包含不可重试节点",
                details={
                    "invalid_steps": invalid_steps,
                    "accepted_steps": sorted(WF04_RETRY_STEPS),
                },
            )
        unsupported = sorted(set(patch) - WF04_RETRY_FIELDS)
        if unsupported:
            raise InputValidationError(
                "WF-04重试包含不支持的字段",
                details={
                    "unsupported_fields": unsupported,
                    "accepted_fields": sorted(WF04_RETRY_FIELDS),
                },
            )

        # A changed upstream input invalidates the affected completed nodes.
        effective_steps = set(str(step) for step in requested_steps)
        invalidations = {
            "target_db": {"WF04_STEP2_P2_A"},
            "host_db": {"WF04_STEP3_P2_B"},
            "host_species": {"WF04_STEP3_P2_B"},
            "template": {"WF04_STEP1_P1", "WF04_STEP2_P2_A"},
            "allow_fallback": set(WF04_RETRY_STEPS),
        }
        for field in patch:
            effective_steps.update(invalidations.get(field, set()))
        if "WF04_STEP1_P1" in effective_steps:
            effective_steps.update({"WF04_STEP2_P2_A", "WF04_STEP3_P2_B"})

        try:
            source = self.store.get_attachment(job_id, "wf04-input")
        except InputValidationError:
            source = copy.deepcopy(previous.get("input") or {})
        source.pop("_wf04_retry_context", None)
        for key, value in patch.items():
            source[key] = copy.deepcopy(value)

        reuse_specificity: dict[str, dict] = {}
        candidates = previous.get("candidates") or []
        previous_specificity = (
            candidates[0].get("specificity")
            if candidates and isinstance(candidates[0], dict)
            else None
        ) or {}
        step_by_branch = {
            "target": "WF04_STEP2_P2_A",
            "host": "WF04_STEP3_P2_B",
        }
        trace_by_step = {
            str(item.get("step")): item
            for item in previous.get("steps") or []
            if isinstance(item, dict)
        }
        for branch, step in step_by_branch.items():
            trace_item = trace_by_step.get(step) or {}
            mode = previous_specificity.get(branch)
            if (
                step not in effective_steps
                and trace_item.get("status") in {"COMPLETED", "REUSED"}
                and isinstance(mode, dict)
            ):
                reuse_specificity[branch] = copy.deepcopy(mode)

        source["_wf04_retry_context"] = {
            "parent_job_id": job_id,
            "reason": reason,
            "retry_steps": sorted(effective_steps),
            "reuse_specificity": reuse_specificity,
        }
        return self.run_wf04_validation(source)

    def design_with_primer3(self, payload: dict) -> dict:
        """Run standalone Primer3 candidate generation without specificity."""
        return Primer3DesignTool(self.config).run(payload)

    def get_agent_audit_log(
        self,
        *,
        trace_id: str | None = None,
        batch_id: str | None = None,
        operation: str | None = None,
        limit: int = 100,
    ) -> dict:
        """Read redacted observable tool events; never exposes hidden reasoning."""
        return self.audit.read(
            trace_id=trace_id,
            batch_id=batch_id,
            operation=operation,
            limit=limit,
        )

    def preflight_wf01_v02(
        self,
        payload: dict,
        design_input_form: dict | None = None,
        user_field_updates: dict | None = None,
        *,
        include_input_mapping: bool = False,
        require_full_route: bool = False,
    ) -> dict:
        """Validate a PRD-v0.2 batch without starting Primer3 execution."""
        prepared, mapping = self._prepare_wf01_v02_input(
            payload,
            design_input_form,
            user_field_updates,
        )
        result = self.wf01_v02.preflight(
            prepared,
            require_full_route=require_full_route,
        )
        if include_input_mapping:
            result["mcp_input_mapping"] = mapping
        return result

    def create_wf01_v02_batch(
        self,
        payload: dict,
        design_input_form: dict | None = None,
        user_field_updates: dict | None = None,
        *,
        include_input_mapping: bool = False,
        interaction_mode: str = "LEGACY",
        require_full_route: bool = False,
    ) -> dict:
        """Persist a validated v0.2 batch in QUEUED or PENDING_CONFIRMATION."""
        prepared, mapping = self._prepare_wf01_v02_input(
            payload,
            design_input_form,
            user_field_updates,
        )
        result = self._with_wf01_ui_message(
            self.wf01_v02.create_batch(
                prepared,
                interaction_mode=interaction_mode,
                require_full_route=require_full_route,
            )
        )
        if include_input_mapping:
            result["mcp_input_mapping"] = mapping
        return result

    def get_wf01_v02_batch(self, batch_id: str) -> dict:
        return self._with_wf01_ui_message(self.wf01_v02.get_batch(batch_id))

    def revise_wf01_v02_target_manifest(
        self, batch_id: str, payload: dict
    ) -> dict:
        """Edit/remove M1 target drafts and return their fresh preflight state."""
        return self._with_wf01_ui_message(
            self.wf01_v02.revise_target_manifest(batch_id, payload)
        )

    def confirm_wf01_v02_target_manifest(self, batch_id: str) -> dict:
        """Confirm a fully ready target list before automatic execution."""
        result = self.wf01_v02.confirm_target_manifest(batch_id)
        batch = result.get("batch")
        if isinstance(batch, dict):
            self._with_wf01_ui_message(batch)
            result["ui_message"] = batch.get("ui_message")
        return result

    def get_wf01_v02_agent_summary(self, batch_id: str) -> dict:
        """Return the bounded WF-01 control view safe for model context."""
        batch = self._with_wf01_ui_message(self.wf01_v02.get_batch(batch_id))
        return summarize_wf01_batch(batch)

    def preflight_wf02_v01(self, payload: dict) -> dict:
        """Validate and echo WF-02 N01-N03 input without creating a task."""
        return self.wf02_v01.preflight(payload)

    def create_wf02_v01_batch(self, payload: dict) -> dict:
        """Create an N05 task, or an N04 draft containing only blockers."""
        return self.wf02_v01.create_batch(payload)

    def get_wf02_v01_batch(self, batch_id: str) -> dict:
        """Return the persisted WF-02 v0.1 task snapshot."""
        return self.wf02_v01.get_batch(batch_id)

    def get_wf02_v01_agent_summary(
        self,
        batch_id: str,
        *,
        attachment_formats: tuple[str, ...] = ("html",),
    ) -> dict:
        """Return a bounded WF-02 control view safe for model context."""
        return summarize_wf02_batch(
            self.wf02_v01.get_batch(batch_id),
            attachment_formats=attachment_formats,
        )

    def get_wf02_v01_draft(self, draft_id: str) -> dict:
        """Return an unresolved WF-02 N04 confirmation draft."""
        return self.wf02_v01.get_draft(draft_id)

    def submit_wf02_v01_input_response(
        self,
        draft_id: str,
        response: dict,
    ) -> dict:
        """Apply a complete N04 response and create N05 when ready."""
        return self.wf02_v01.submit_input_response(draft_id, response)

    def run_wf02_v01_until_blocked(
        self,
        batch_id: str,
        *,
        bounded: bool = False,
    ) -> dict:
        """Advance WF-02 and return the compact agent control view.

        MCP callers use bounded mode so each HTTP request covers one persisted
        execution unit.  REST and internal callers retain the historical
        all-target behaviour unless they explicitly opt in.
        """
        return self.wf02_v01.run_until_blocked(batch_id, bounded=bounded)

    def get_wf02_v01_report(self, batch_id: str) -> dict:
        """Return the canonical N31 report for one completed WF-02 version."""
        return self.wf02_v01.get_report(batch_id)

    def get_wf02_v01_panel(self, batch_id: str, panel_id: str) -> dict:
        """Return one N30 panel with its sequences, scores, and evidence."""
        return self.wf02_v01.get_panel(batch_id, panel_id)

    def get_wf02_v01_export_path(
        self, batch_id: str, format_name: str
    ) -> Path:
        """Resolve a persisted N31 HTML, CSV, or PDF export."""
        return self.wf02_v01.get_export_path(batch_id, format_name)

    def get_wf02_v01_export_metadata(
        self, batch_id: str, format_name: str
    ) -> dict:
        """Return compact metadata for one persisted WF-02 export."""
        return self.wf02_v01.get_export_metadata(batch_id, format_name)

    def create_wf02_v01_version(
        self, batch_id: str, options: dict | None = None
    ) -> dict:
        """Create an immutable N32 redesign version without changing its source."""
        return summarize_wf02_batch(
            self.wf02_v01.create_version(batch_id, options)
        )

    def submit_wf02_v01_child_response(
        self,
        batch_id: str,
        target_id: str,
        response: dict,
        *,
        bounded: bool = False,
    ) -> dict:
        """Proxy one target-scoped interaction to its WF-01 child and resume."""
        return self.wf02_v01.submit_child_response(
            batch_id,
            target_id,
            response,
            bounded=bounded,
        )

    def submit_wf02_v01_internal_control_response(
        self,
        batch_id: str,
        response: dict,
        *,
        bounded: bool = False,
    ) -> dict:
        """Resume a legacy N12/N13 task that already has a pending interaction."""
        return self.wf02_v01.submit_internal_control_response(
            batch_id,
            response,
            bounded=bounded,
        )

    def design_p5_internal_control(self, payload: dict) -> dict:
        """Run P5 as the shared scientific orchestrator without entering WF-03."""
        prepared = copy.deepcopy(payload)
        enriched = self.catalog.enrich_payload("WF-03", prepared)
        result = InternalControlDesigner(self.config, self.catalog).design(enriched)
        result.pop("candidate_objects", None)
        return result

    def check_p4_panel(self, payload: dict) -> dict:
        """Validate one assigned panel with the existing P4 implementation."""
        panel = payload.get("panel")
        if not isinstance(panel, list) or not panel:
            raise InputValidationError("P4 panel 必须为非空数组")
        allow_fallback = bool(
            payload.get(
                "allow_fallback",
                self.config["tools"]["allow_development_fallback"],
            )
        )
        primer3 = Primer3Adapter(self.config, allow_fallback=allow_fallback)
        evaluator = MultiplexEvaluator(primer3, self.config)
        tool_status = evaluator.status()
        if not tool_status.get("available"):
            return {
                "status": "NOT_EVALUATED",
                "verdict": None,
                "issues": ["P4 的 P1/Primer3 依赖不可用"],
                "tool_status": tool_status,
            }
        result = evaluator.check_panel(
            copy.deepcopy(panel),
            instrument=payload.get("instrument"),
            rox_as_reference=payload.get("rox_as_reference"),
            relaxed=bool(payload.get("relaxed", False)),
            tm_spread_limit=payload.get("tm_spread_limit"),
        )
        result["status"] = "COMPLETED"
        result["tool_status"] = tool_status
        return result

    def create_wf01_tm_constrained_version(self, payload: dict) -> dict:
        """Create a WF-01 M7 version while preserving existing user overrides."""
        source_batch_id = str(payload.get("source_batch_id") or "")
        target_id = str(payload.get("target_id") or "")
        constraints = payload.get("constraints")
        if not source_batch_id or not target_id or not isinstance(constraints, dict):
            raise InputValidationError(
                "WF-01 Tm 定向重设计缺少 source_batch_id、target_id 或 constraints"
            )
        source = self.get_wf01_v02_batch(source_batch_id)
        target = next(
            (
                item
                for item in source.get("targets") or []
                if str(item.get("target_id")) == target_id
            ),
            None,
        )
        if target is None:
            raise InputValidationError(
                "WF-01 源版本不存在指定靶标",
                details={"source_batch_id": source_batch_id, "target_id": target_id},
            )
        parameters = copy.deepcopy(
            (target.get("parameters") or {}).get("user_overrides") or {}
        )
        parameters.update(copy.deepcopy(constraints))
        return self.create_wf01_v02_version(
            source_batch_id,
            {
                "reason": payload.get("reason")
                or "WF-02 N27 Tm 均衡性定向重设计",
                "target_changes": [
                    {
                        "target_id": target_id,
                        "design_parameters": parameters,
                    }
                ],
            },
        )

    def assess_p6_amplicon_structure(self, amplicon_sequence: str) -> dict:
        """Run the WF-02 P6 soft-rule check without mutating WF-01 state."""
        status = self.p6_vienna.status()
        if not status.get("available"):
            result = self.p6_vienna.not_evaluated(
                "ViennaRNA 或 DNA Mathews2004 参数不可用"
            )
            result["tool_status"] = status
            return result
        return self.p6_vienna.assess_full(amplicon_sequence)

    def get_wf01_v02_interactions(self, batch_id: str) -> dict:
        """Return persisted user-interaction payloads for a WF-01 batch."""
        return self._with_wf01_ui_message(
            self.wf01_v02.get_interactions(batch_id)
        )

    def submit_wf01_v02_interaction_response(
        self,
        batch_id: str,
        response: dict,
    ) -> dict:
        """Persist and route a complete WF-01 frontend interaction response."""
        return self._with_wf01_ui_message(
            self.wf01_v02.submit_interaction_response(batch_id, response)
        )

    def execute_wf01_v02_m2(self, batch_id: str, options: dict | None = None) -> dict:
        """Run Primer3 and PRD-v0.2 rules for a queued batch."""
        return self._with_wf01_ui_message(
            self.wf01_v02.execute_m2(batch_id, options)
        )

    def execute_wf01_v02_m3(self, batch_id: str, options: dict | None = None) -> dict:
        """Validate exploration, deduplicate, score and prepare provisional Top10."""
        return self._with_wf01_ui_message(
            self.wf01_v02.execute_m3(batch_id, options)
        )

    def execute_wf01_v02_m4(self, batch_id: str, options: dict | None = None) -> dict:
        """Evaluate three-mode specificity and produce the M4-ranked Top10."""
        return self._with_wf01_ui_message(
            self.wf01_v02.execute_m4(batch_id, options)
        )

    def execute_wf01_v02_m5(self, batch_id: str, options: dict | None = None) -> dict:
        """Assemble final v0.2 target and batch result statuses."""
        return self._with_wf01_ui_message(
            self.wf01_v02.execute_m5(batch_id, options)
        )

    def execute_wf01_v02_m6(self, batch_id: str, options: dict | None = None) -> dict:
        """Build the canonical report and its three default user attachments."""
        return self._with_wf01_ui_message(
            self.wf01_v02.execute_m6(batch_id, options)
        )

    def run_wf01_v02_until_blocked(
        self,
        batch_id: str,
        stage_options: dict | None = None,
    ) -> dict:
        """Run deterministic stages until user input is required or M6 completes.

        Complete scientific snapshots remain persisted by the individual stage
        engines.  Only a compact control-plane view is returned to the caller.
        """
        stage_options = {} if stage_options is None else stage_options
        if not isinstance(stage_options, dict):
            raise InputValidationError("stage_options 必须为对象")
        batch_id = str(self.wf01_v02.get_batch(batch_id)["batch_id"])
        normalized_options: dict[str, dict] = {}
        for raw_stage, value in stage_options.items():
            stage = str(raw_stage).upper()
            if stage not in {"M2", "M3", "M4", "M5", "M6"}:
                raise InputValidationError(
                    "stage_options 包含未知阶段",
                    details={"stage": raw_stage},
                )
            if not isinstance(value, dict):
                raise InputValidationError(
                    "每个阶段的 options 必须为对象",
                    details={"stage": stage},
                )
            normalized_options[stage] = value

        runners = {
            "M2": self.execute_wf01_v02_m2,
            "M3": self.execute_wf01_v02_m3,
            "M4": self.execute_wf01_v02_m4,
            "M5": self.execute_wf01_v02_m5,
            "M6": self.execute_wf01_v02_m6,
        }
        executed_stages: list[str] = []
        for _ in range(len(runners) + 1):
            batch = self._with_wf01_ui_message(
                self.wf01_v02.get_batch(batch_id)
            )
            ui_message = batch.get("ui_message")
            if (
                isinstance(ui_message, dict)
                and (ui_message.get("flowControl") or {}).get("action")
                == "WAIT_FOR_USER"
            ):
                return summarize_wf01_batch(
                    batch,
                    executed_stages=executed_stages,
                )
            stage = next_stage_for_batch(batch)
            if stage is None:
                return summarize_wf01_batch(
                    batch,
                    executed_stages=executed_stages,
                )
            runners[stage](batch_id, normalized_options.get(stage, {}))
            executed_stages.append(stage)
        raise InputValidationError(
            "WF-01 自动推进超过阶段上限",
            details={
                "batch_id": batch_id,
                "executed_stages": executed_stages,
            },
        )

    def create_wf01_v02_version(
        self,
        batch_id: str,
        options: dict | None = None,
    ) -> dict:
        """Create an M7 redesign version with target-level reuse provenance."""
        return self._with_wf01_ui_message(
            self.wf01_v02.create_version(batch_id, options)
        )

    def get_wf01_v02_report(
        self,
        batch_id: str,
        *,
        target_id: str | None = None,
        candidate_scope: str = "top10",
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        """Return the internal report or a non-mutating online view."""
        return self.wf01_v02.get_report(
            batch_id,
            target_id=target_id,
            candidate_scope=candidate_scope,
            page=page,
            page_size=page_size,
        )

    def get_wf01_v02_scientific_artifacts(
        self,
        batch_id: str,
        *,
        stage: str | None = None,
    ) -> dict:
        """List expert-facing raw scientific artifacts without embedding data."""
        self.wf01_v02.store.get(batch_id)
        result = self.wf01_v02.store.list_scientific_artifacts(batch_id)
        selected_stage = str(stage).upper() if stage else None
        if selected_stage and selected_stage not in {
            "M1",
            "M2",
            "M3",
            "M4",
            "M5",
            "M6",
            "M7",
        }:
            raise InputValidationError(
                "科学原始数据阶段无效",
                details={"stage": stage},
            )
        if selected_stage:
            result["artifacts"] = [
                item
                for item in result.get("artifacts") or []
                if item.get("stage") == selected_stage
            ]
        result["artifactCount"] = len(result.get("artifacts") or [])
        return result

    def get_wf01_v02_scientific_artifact_path(
        self,
        batch_id: str,
        artifact_id: str,
    ) -> Path:
        """Return one persisted raw evidence artifact for authenticated download."""
        return self.wf01_v02.store.get_scientific_artifact_path(
            batch_id,
            artifact_id,
        )

    def get_wf01_v02_report_agent_view(
        self,
        batch_id: str,
        *,
        target_id: str | None = None,
        candidate_scope: str = "top10",
        page: int = 1,
        page_size: int = 10,
        include_rule_details: bool = False,
    ) -> dict:
        """Return a compact report summary or compact paginated candidates."""
        if target_id is None:
            report = self.wf01_v02.get_report(batch_id)
            return summarize_wf01_report(report)
        if page_size > 25:
            raise InputValidationError(
                "智能体候选分页最多返回 25 条",
                details={"page_size": page_size, "maximum_page_size": 25},
            )
        view = self.wf01_v02.get_report(
            batch_id,
            target_id=target_id,
            candidate_scope=candidate_scope,
            page=page,
            page_size=page_size,
        )
        return compact_report_page(
            view,
            include_rule_details=include_rule_details,
        )

    def get_wf01_v02_candidate_agent_view(
        self,
        batch_id: str,
        target_id: str,
        candidate_id: str,
        *,
        include_rule_details: bool = True,
    ) -> dict:
        """Return one candidate on explicit request without exposing its peers."""
        report = self.wf01_v02.get_report(batch_id)
        target = next(
            (
                item
                for item in report.get("targets") or []
                if item.get("target_id") == target_id
            ),
            None,
        )
        if target is None:
            raise InputValidationError(
                "报告中不存在所请求的靶标",
                details={"batch_id": batch_id, "target_id": target_id},
            )
        candidate = next(
            (
                item
                for item in target.get("all_primer3_candidates") or []
                if item.get("candidate_id") == candidate_id
            ),
            None,
        )
        if candidate is None:
            raise InputValidationError(
                "报告中不存在所请求的候选",
                details={
                    "batch_id": batch_id,
                    "target_id": target_id,
                    "candidate_id": candidate_id,
                },
            )
        return {
            "response_schema_version": "wf01-agent-view-v1",
            "response_mode": "CANDIDATE_DETAIL",
            "batch_id": batch_id,
            "target_id": target_id,
            "candidate": compact_candidate(
                candidate,
                include_rule_details=include_rule_details,
            ),
        }

    def get_wf01_v02_export_path(
        self,
        batch_id: str,
        format_name: str,
    ) -> Path:
        """Resolve an M6 complete-batch user export."""
        return self.wf01_v02.get_export_path(batch_id, format_name)

    def get_wf01_v02_export_metadata(
        self,
        batch_id: str,
        format_name: str,
    ) -> dict:
        """Return the persisted download URL and publication provenance."""
        return self.wf01_v02.get_export_metadata(batch_id, format_name)

    def get_wf01_v02_export_path_by_filename(self, filename: str) -> Path:
        """Resolve one flat `/data/<filename>` report attachment."""
        return self.wf01_v02.get_export_path_by_filename(filename)

    def tool_status(self, *, allow_fallback: bool = False) -> dict:
        primer3 = Primer3Adapter(self.config, allow_fallback=allow_fallback)
        blast = BlastAdapter(self.config["tools"]["blastn_executable"], allow_fallback=allow_fallback, runtime_config=self.config["tools"].get("blast_runtime"))
        mafft = MafftAdapter(self.config["tools"]["mafft_executable"], allow_fallback=allow_fallback)
        mgb = MGBProbeEvaluator(primer3, self.config)
        vienna = self.p6_vienna
        p4 = MultiplexEvaluator(primer3, self.config)
        p5 = InternalControlDesigner(self.config, self.catalog)
        return {
            "service": "agentd-primer-probe",
            "version": __version__,
            "phase": 3,
            "tools": {
                "P1": primer3.status(),
                "C6": blast.status(),
                "C5": mafft.status(),
                "P3": mgb.status(),
                "P6": vienna.status(),
                "P4": p4.status(),
                "P5": p5.status(allow_fallback=allow_fallback),
                "C0": self.catalog.status(),
                "C1": {"available": True, "implementation": "stdlib"},
                "C7": {"available": True, "schema": str(PROJECT_ROOT / "schemas" / "agentd-output.schema.json")},
                "S3": self.wf01_v02.artifact_publisher.status(),
            },
        }

    def capabilities(self) -> dict:
        return {
            "service": "agentd-primer-probe",
            "version": __version__,
            "phase": 3,
            "mcp": {
                "transport": "streamableHttp",
                "endpoint": "/mcp",
                "authentication": "Bearer JWT",
            },
            "workflows": {
                "WF-01": "single_target_design",
                "WF-02": "multiplex_panel",
                "WF-03": "internal_control",
                "WF-04": "existing_assay_validation",
                "WF-05": "variation_tolerance",
                "WF-06": "complete_panel",
                "WF-07": "diagnosis_optimization",
            },
            "server_integration": {
                "database_catalog_env": "AGENTD_DATABASE_CATALOG",
                "state_dir_env": "AGENTD_STATE_DIR",
                "blast_db_env": "BLASTDB",
                "artifact_storage": self.wf01_v02.artifact_publisher.status(),
            },
            "input_file_transfer": {
                "mcp_tool": "download_himat_file",
                "source_argument": "file_id",
                "downstream_argument": "input_artifact_id",
                "inline_sequence_supported": True,
                "wf01_create_argument": "input_artifact_id",
                "wf03_run_argument": "input_artifact_id",
                "wf04_run_argument": "input_artifact_id",
                "download_base_url_env": "HIMAT_FILE_DOWNLOAD_BASE_URL",
                "model_receives_file_body": False,
                "model_receives_local_path": False,
                "wf01_preflight_sequence_redacted": True,
            },
            "agent_observability": {
                "audit_log": True,
                "mcp_tool": None,
                "rest_endpoint": "GET /v1/audit/agent",
                "records": [
                    "tool_started",
                    "tool_completed",
                    "tool_failed",
                    "redacted_inputs",
                    "field_mapping",
                    "workflow_state",
                    "database_readiness",
                ],
                "chain_of_thought_included": False,
            },
            "backend_data_access": {
                "himat_business_database_direct_read": False,
                "agentd_persisted_batch_read": True,
                "scientific_database_catalog_read": True,
                "confirmation_contract": "WF-01 不使用输入确认表单；缺少靶标时先由模型以普通文本询问",
            },
            "tool_endpoints": {
                "agent_audit_log": "GET /v1/audit/agent",
                "primer3_design": "POST /v1/tools/primer3/design",
                "wf01_target_manifest_edit": "PATCH /v1/wf01/tasks/{batch_id}/targets",
                "wf01_target_manifest_confirm": "POST /v1/wf01/tasks/{batch_id}/confirm",
                "wf03_run": "POST /v1/wf03/internal-control",
                "wf03_retry": "POST /v1/wf03/internal-control/{job_id}/retry",
                "wf03_report": "GET /v1/wf03/internal-control/{job_id}/report",
                "wf04_run": "POST /v1/wf04/validate",
                "wf04_retry": "POST /v1/wf04/validate/{job_id}/retry",
                "wf04_report": "GET /v1/wf04/validate/{job_id}/report",
            },
            "wf03": {
                "policy_version": WF03_POLICY_VERSION,
                "agent_orchestration": {
                    "mode": "LLM_INTENT_ROUTING_ONLY",
                    "mcp_run_tool": "run_wf03_internal_control",
                    "mcp_retry_tool": "retry_wf03_internal_control",
                    "mcp_report_tool": "get_wf03_report",
                },
                "file_intake": {
                    "download_tool": "download_himat_file",
                    "run_argument": "input_artifact_id",
                    "backend_only_parse": True,
                    "required_when_attachment_present": True,
                },
                "direct_scientific_tool": "P5",
                "transitive_tools_owned_by_p5": [
                    "NCBI_ENTREZ",
                    "C5",
                    "P1",
                    "P2",
                    "P6",
                ],
                "immutable_reentry": True,
                "terminal_delivery": {
                    "contract": "primer_design_output",
                    "model_must_relay_verbatim": True,
                    "shared_with": "WF-01",
                },
            },
            "wf04": {
                "policy_version": WF04_POLICY_VERSION,
                "agent_orchestration": {
                    "mode": "LLM_INTENT_ROUTING_ONLY",
                    "mcp_run_tool": "run_wf04_validation",
                    "mcp_retry_tool": "retry_wf04_validation",
                    "mcp_report_tool": "get_wf04_report",
                },
                "file_intake": {
                    "download_tool": "download_himat_file",
                    "run_argument": "input_artifact_id",
                    "backend_only_parse": True,
                    "required_when_attachment_present": True,
                },
                "backend_nodes": [
                    "WF04_STEP1_P1",
                    "WF04_STEP2_P2_A",
                    "WF04_STEP3_P2_B",
                    "WF04_STEP4_P2_C",
                    "WF04_STEP5_COMPATIBILITY",
                    "WF04_STEP6_DECISION",
                ],
                "immutable_retry": True,
                "execution_status_separate_from_decision": True,
                "terminal_delivery": {
                    "contract": "primer_design_output",
                    "model_must_relay_verbatim": True,
                    "shared_with": "WF-01",
                },
                "compatibility_alias": "POST /v1/workflows/WF-04/run",
            },
            "wf02_prd_v0_1": {
                "status": "N01_N32_BALANCE_SCORING_AND_REPORTING",
                "workflow": "ordinary_multiplex_same_tube",
                "agent_orchestration": {
                    "mode": "MCP_CREATE_AND_RUN_UNTIL_BLOCKED",
                    "mcp_create_tool": "create_wf02_batch",
                    "mcp_resume_tool": "run_wf02_until_blocked",
                    "interaction_resume_tool": "submit_wf02_child_response",
                    "internal_control_resume_tool": (
                        "submit_wf02_internal_control_response"
                    ),
                },
                "target_count": {"minimum": 2, "maximum": 5},
                "core_user_inputs": [
                    "targets",
                    "host_species",
                    "include_internal_control",
                ],
                "internal_control_default": True,
                "target_file_input": {
                    "policy_version": "wf02-one-file-per-target-v1",
                    "one_file_per_target": True,
                    "multiple_homologous_sequences_per_file": True,
                    "multi_target_file_auto_split": False,
                    "sequence_mode": "alignment_target",
                    "upload_reference_field": "targets[].input_artifact_id",
                },
                "task_version": ">=1_IMMUTABLE_LINEAGE",
                "mcp_tools": [
                    "create_wf02_batch",
                    "submit_wf02_input_response",
                    "get_wf02_batch",
                    "run_wf02_until_blocked",
                    "submit_wf02_child_response",
                    "submit_wf02_internal_control_response",
                    "create_wf02_version",
                    "get_wf02_report",
                    "get_wf02_panel",
                    "get_wf02_export",
                ],
                "endpoints": {
                    "preflight": "POST /v1/wf02/tasks/preflight",
                    "create_batch": "POST /v1/wf02/tasks",
                    "get_batch": "GET /v1/wf02/tasks/{batch_id}",
                    "get_draft": "GET /v1/wf02/drafts/{draft_id}",
                    "submit_input_response": (
                        "POST /v1/wf02/drafts/{draft_id}/interactions/response"
                    ),
                    "run_until_blocked": (
                        "POST /v1/wf02/tasks/{batch_id}/run-until-blocked"
                    ),
                    "submit_child_response": (
                        "POST /v1/wf02/tasks/{batch_id}/targets/{target_id}/interactions/response"
                    ),
                    "submit_internal_control_response": (
                        "POST /v1/wf02/tasks/{batch_id}/internal-control/interactions/response"
                    ),
                    "create_version": (
                        "POST /v1/wf02/tasks/{batch_id}/versions"
                    ),
                    "get_report": "GET /v1/wf02/tasks/{batch_id}/report",
                    "get_panel": (
                        "GET /v1/wf02/tasks/{batch_id}/panels/{panel_id}"
                    ),
                    "get_export": (
                        "GET /v1/wf02/tasks/{batch_id}/exports/{format_name}"
                    ),
                    "preview_html": (
                        "GET /v1/wf02/tasks/{batch_id}/exports/html/preview"
                    ),
                },
                "compatibility_alias": {
                    "path": "POST /v1/wf02/multiplex",
                    "deprecated": True,
                    "implementation": "WF02V01BatchService",
                },
                "state_isolation": [
                    "wf02-v01-batches",
                    "wf02-v01-drafts",
                    "wf02-v01-reports",
                    "wf02-v01-exports",
                ],
                "wf01_dependency": "COMPLETED_WF01_PUBLIC_SERVICE_CONTRACT_ONLY",
                "p6_dependency": {
                    "service_operation": "assess_p6_amplicon_structure",
                    "mode": "MFE_PLUS_SUBOPT",
                    "blocking": False,
                    "unavailable_behavior": "NOT_EVALUATED_CONTINUE",
                },
                "p5_dependency": {
                    "service_operation": "design_p5_internal_control",
                    "mode": "DIRECT_FROM_WF02",
                    "wf03_nested": False,
                    "channel_policy_version": "wf02-ic-channel-dynamic-v2",
                    "failure_behavior": "FAIL_CONTINUE_WITHOUT_INTERNAL",
                },
                "p4_dependency": {
                    "service_operation": "check_p4_panel",
                    "implementation": "MultiplexEvaluator.check_panel",
                    "wf02_embeds_p4_algorithms": False,
                    "batch_size": self.config["multiplex"][
                        "maximum_panel_combinations"
                    ],
                    "exhaustive_with_cursor": True,
                    "r17_policy_version": "wf02-r17-caution-v1",
                    "relaxation_policy_version": (
                        "wf02-relaxation-conservative-v1"
                    ),
                    "relaxed_cross_dimer_dg_kcal": -7.0,
                    "relaxed_tm_spread_celsius": 4.0,
                },
                "balance_and_delivery": {
                    "balance_policy_version": "wf02-balance-sop-v1",
                    "maximum_wf01_redesign_attempts": 2,
                    "scoring_policy_version": (
                        "wf02-panel-score-conservative-v1"
                    ),
                    "deduplication_policy_version": (
                        "wf02-sequence-members-v1"
                    ),
                    "selection_policy_version": "wf02-layered-top3-v1",
                    "maximum_top_panels": 3,
                    "partial_panels_in_complete_top3": False,
                    "report_schema": "wf02-v0.1-report-v1",
                    "report_formats": ["html", "csv", "pdf"],
                    "source_report_overwrite": False,
                    "version_diff_sections": [
                        "user_input_changes",
                        "runtime_config_changes",
                    ],
                },
                "implemented_nodes": [
                    "N01", "N02", "N03", "N04", "N05", "N06", "N07",
                    "N08", "N09", "N10", "N11", "N12", "N13", "N14",
                    "N15", "N16", "N16A", "N17", "N18", "N19", "N20",
                    "N21", "N22", "N23", "N24", "N25", "N26",
                    "N27", "N28", "N29", "N30", "N31", "N32",
                ],
            },
            "wf01_prd_v0_2": {
                "status": "M7_VERSIONED_REDESIGN_AND_REUSE",
                "workflow": "ordinary_taqman_independent_targets",
                "agent_orchestration": {
                    "mode": "CREATE_AND_RUN_UNTIL_BLOCKED",
                    "mcp_create_tool": "create_wf01_batch",
                    "mcp_resume_tool": "run_wf01_until_blocked",
                    "automatic_stages": ["M2", "M3", "M4", "M5", "M6"],
                    "model_candidate_reasoning_required": False,
                    "model_receives_complete_candidate_pool": False,
                    "default_response_mode": "SUMMARY",
                    "maximum_tool_result_bytes_env": "MCP_MAX_TOOL_RESULT_BYTES",
                    "default_maximum_tool_result_bytes": 65536,
                },
                "conversation_gate": {
                    "required_before_preflight": False,
                    "history_required": False,
                    "natural_language_direct_input": True,
                    "inline_sequence_form_required": False,
                    "uploaded_file_form_required": False,
                    "uploaded_file_flow": [
                        "download_himat_file",
                        "create_wf01_batch",
                    ],
                    "target_organism_required": True,
                    "new_task_route": "FULL_SPECIFICITY",
                    "design_only_fallback_allowed": False,
                    "ui_state_source": "agentd_tool_result.ui_message",
                    "model_json_required": False,
                    "authoritative_mapping": {
                        "create_wf01_batch.target_organism": "batch_defaults.target_organism.name",
                        "create_wf01_batch.host": "batch_defaults.hosts[0].name",
                        "create_wf01_batch.input_artifact_id": "input_artifact_id",
                    },
                },
                "endpoints": {
                    "preflight": "POST /v1/wf01/tasks/preflight",
                    "create_batch": "POST /v1/wf01/tasks",
                    "get_batch": "GET /v1/wf01/tasks/{batch_id}",
                    "get_interactions": "GET /v1/wf01/tasks/{batch_id}/interactions",
                    "revise_target_manifest": "PATCH /v1/wf01/tasks/{batch_id}/targets",
                    "confirm_target_manifest": "POST /v1/wf01/tasks/{batch_id}/confirm",
                    "run_until_blocked": "POST /v1/wf01/tasks/{batch_id}/run-until-blocked",
                    "submit_interaction_response": "POST /v1/wf01/tasks/{batch_id}/interactions/response",
                    "execute_m2": "POST /v1/wf01/tasks/{batch_id}/run-m2",
                    "execute_m3": "POST /v1/wf01/tasks/{batch_id}/run-m3",
                    "execute_m4": "POST /v1/wf01/tasks/{batch_id}/run-m4",
                    "execute_m5": "POST /v1/wf01/tasks/{batch_id}/run-m5",
                    "execute_m6": "POST /v1/wf01/tasks/{batch_id}/run-m6",
                    "create_version": "POST /v1/wf01/tasks/{batch_id}/versions",
                    "get_report": "GET /v1/wf01/tasks/{batch_id}/report",
                    "download_export": "GET /v1/wf01/tasks/{batch_id}/exports/{format}",
                },
                "implemented_rules": [
                    f"R{index:02d}" for index in range(1, 22)
                ],
                "specificity_modes": [
                    "TARGET_HIT",
                    "HOST_EXCLUSION",
                    "RELATED_BACKGROUND_NT",
                ],
                "specificity_policy_version": SPECIFICITY_POLICY_VERSION,
                "specificity_policy_status": SPECIFICITY_POLICY_STATUS,
                "fixed_top_candidates": 10,
                "scoring_policy_version": SCORING_POLICY_VERSION,
                "scoring_policy_status": SCORING_POLICY_STATUS,
                "scoring_scale": dict(SCORING_SCALE),
                "scoring_weights": dict(SCORING_WEIGHTS),
                "scoring_soft_penalties_percent": dict(
                    SOFT_PENALTIES_PERCENT
                ),
                "scoring_tie_breakers": list(FINAL_RANK_TIE_BREAKERS),
                "final_score_status": "AVAILABLE_WHEN_M4_SPECIFICITY_COMPLETE",
                "target_result_statuses": [
                    "SUCCEEDED",
                    "PARTIAL",
                    "PARTIAL_INCOMPLETE",
                    "FAILED",
                ],
                "batch_result_statuses": [
                    "SUCCEEDED",
                    "PARTIALLY_SUCCEEDED",
                    "FAILED",
                ],
                "report_schema_version": "wf01-prd-v0.2-canonical-report-v2",
                "conservation_evidence_policy": (
                    "MAFFT_SHANNON_WHEN_MULTI_SEQUENCE_ELSE_EXPLICIT_NOT_EVALUATED"
                ),
                "user_export_formats": [
                    "html",
                    "process_html",
                    "scientific_data",
                    "csv",
                    "pdf",
                ],
                "default_generated_formats": [
                    "html",
                    "process_html",
                    "scientific_data",
                ],
                "bundled_report_formats": {
                    "scientific_data": ["rules_html"],
                },
                "attachment_publication": {
                    "provider": "rustfs-s3",
                    "download_url_mode": "ABSOLUTE_RUSTFS_OBJECT_URL",
                    "public_base_url": "https://rustfs.ne.scqcyz.cn",
                    "upload_required_when_enabled": True,
                    "integrity_check": "HEAD_OBJECT_SIZE_AND_SHA256_METADATA",
                },
                "on_demand_export_formats": [
                    "csv",
                    "pdf",
                ],
                "user_json_download": False,
                "export_data_scope": "COMPLETE_BATCH",
                "versioned_redesign": {
                    "status": "AVAILABLE",
                    "contract_version": "wf01-prd-v0.2-redesign-v1",
                    "changed_targets": "RERUN_REQUIRED",
                    "unchanged_targets": "REUSED",
                    "source_task_immutable": True,
                },
                "next_stage": "RELEASE_VALIDATION_AND_DECISION_CLOSURE",
            },
        }

    def readiness(self) -> dict:
        tools = self.tool_status()
        database = copy.deepcopy(tools["tools"]["C0"])
        remote_blast_url = os.environ.get("BLAST_SEARCH_SERVICE_URL", "").strip()
        remote_health_url = (
            f"{remote_blast_url.rstrip('/')}/health" if remote_blast_url else None
        )
        remote_blast_healthy = False
        remote_health_error = None
        if remote_health_url:
            try:
                with urlopen(remote_health_url, timeout=2.0) as response:
                    remote_blast_healthy = int(response.status) == 200
                    if not remote_blast_healthy:
                        remote_health_error = f"HTTP {response.status}"
            except (OSError, URLError, ValueError) as exc:
                remote_health_error = str(exc)
        remote_background_keys = {"cross:default:blast", "nt:default:blast"}
        remote_satisfied = [
            key
            for key in database.get("required_missing", [])
            if key in remote_background_keys and remote_blast_healthy
        ]
        if remote_satisfied:
            database["required_missing"] = [
                key
                for key in database.get("required_missing", [])
                if key not in remote_background_keys
            ]
        database["remote_background"] = {
            "configured": bool(remote_blast_url),
            "service_url": remote_blast_url or None,
            "health_url": remote_health_url,
            "satisfies_required_resources": remote_satisfied,
            "health_verified": remote_blast_healthy,
            "health_error": remote_health_error,
            "health_note": "MCP 搜索能力仍会在每次 WF-01 调用时再次验证",
        }
        profile = os.environ.get("AGENTD_RUNTIME_PROFILE") or self.config.get("runtime", {}).get("profile", "unknown")
        production_profile = profile in {"production", "server-production"}
        configured_database_requirement = bool(
            self.config.get("runtime", {}).get("require_databases_for_readiness", False)
        )
        database_requirement_override = os.environ.get("AGENTD_REQUIRE_DATABASES")
        require_databases = (
            configured_database_requirement
            if database_requirement_override is None
            else database_requirement_override.lower() in {"1", "true", "yes"}
        )
        required_tools = ["P1", *(["C5", "C6"] if production_profile else [])]
        missing_tools = [tool_id for tool_id in required_tools if not tools["tools"][tool_id]["available"]]
        core_ready = not missing_tools
        database_ready = bool(database["configured"] and not database["required_missing"])
        ready = core_ready and (database_ready if require_databases else True)
        return {
            "status": "ready" if ready else "not_ready",
            "profile": profile,
            "core_ready": core_ready,
            "required_tools": required_tools,
            "missing_tools": missing_tools,
            "databases_required": require_databases,
            "databases_ready": database_ready,
            "database": database,
        }

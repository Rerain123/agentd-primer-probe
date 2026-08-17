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
from .specificity import BlastAdapter
from .structure import ViennaRNAAdapter
from .ui_messages import build_primer_design_input, ui_message_for_batch
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
from .wf01_v02_m4 import (
    SPECIFICITY_POLICY_STATUS,
    SPECIFICITY_POLICY_VERSION,
)
from .workflows import (
    WF01SingleTarget,
    WF02MultiplexPanel,
    WF03InternalControl,
    WF04ExistingPrimerValidation,
    WF05VariationTolerance,
    WF06CompletePanel,
    WF07PrimerDiagnosis,
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
        self.wf01_v02 = WF01V02BatchService(
            self.store.root,
            self.config,
            self.catalog,
            artifact_publisher,
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

    def prepare_wf01_design_input(self, payload: dict) -> dict:
        """Return the authoritative pre-design form without LLM formatting."""
        form = build_primer_design_input(payload)
        inline_text = payload.get("fasta_text")
        if not isinstance(inline_text, str) or not inline_text.strip():
            sequence = payload.get("sequence")
            if isinstance(sequence, str) and sequence.strip():
                inline_text = sequence
        if isinstance(inline_text, str) and inline_text.strip():
            stripped = inline_text.strip()
            if not stripped.startswith(">"):
                inline_text = f">pasted_sequence\n{stripped}\n"
            artifact = self.input_artifacts.store_inline_fasta(inline_text)
            form["data"]["sourceFile"] = {
                "fileName": artifact["file_name"],
                "fileType": "FASTA",
                "inputMode": "INLINE_SEQUENCE",
                "inputArtifactId": artifact["input_artifact_id"],
            }
        return form

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
        factories = {
            "WF-01": WF01SingleTarget,
            "WF-02": WF02MultiplexPanel,
            "WF-03": WF03InternalControl,
            "WF-04": WF04ExistingPrimerValidation,
            "WF-05": WF05VariationTolerance,
            "WF-06": WF06CompletePanel,
            "WF-07": WF07PrimerDiagnosis,
        }
        if normalized not in factories:
            raise InputValidationError(
                "支持WF-01至WF-07",
                details={"requested": workflow, "supported": sorted(factories)},
            )
        payload = self.input_artifacts.attach_path(payload)
        started = time.perf_counter()
        enriched = self.catalog.enrich_payload(normalized, payload)
        if normalized in {"WF-02", "WF-03", "WF-06", "WF-07"}:
            workflow_instance = factories[normalized](self.config, self.catalog)
        else:
            workflow_instance = factories[normalized](self.config)
        result = workflow_instance.run(enriched)
        result.tool_status.setdefault("C0", self.catalog.status())
        if enriched.get("_database_resolution"):
            result.provenance["database_resolution"] = enriched["_database_resolution"]
        result.reports = generate_reports(result)
        data = assemble(result)
        if persist:
            self.store.save(data)
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
    ) -> dict:
        """Validate a PRD-v0.2 batch without starting Primer3 execution."""
        prepared, mapping = self._prepare_wf01_v02_input(
            payload,
            design_input_form,
            user_field_updates,
        )
        result = self.wf01_v02.preflight(prepared)
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
        """Build the canonical report and its four default user attachments."""
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
        blast = BlastAdapter(self.config["tools"]["blastn_executable"], allow_fallback=allow_fallback)
        mafft = MafftAdapter(self.config["tools"]["mafft_executable"], allow_fallback=allow_fallback)
        mgb = MGBProbeEvaluator(primer3, self.config)
        vienna = ViennaRNAAdapter(self.config)
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
                "inline_sequence_prepare_fields": ["sequence", "fasta_text"],
                "inline_reference_form_field": "sourceFile.inputArtifactId",
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
                "confirmation_contract": (
                    "直接自然语言输入不需要表单确认；文件上传兼容链路继续使用 "
                    "primer_design_input_update"
                ),
            },
            "tool_endpoints": {
                "agent_audit_log": "GET /v1/audit/agent",
                "primer3_design": "POST /v1/tools/primer3/design",
                "wf01_target_manifest_edit": "PATCH /v1/wf01/tasks/{batch_id}/targets",
                "wf01_target_manifest_confirm": "POST /v1/wf01/tasks/{batch_id}/confirm",
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
                    "uploaded_file_form_flow_unchanged": True,
                    "ui_state_source": "agentd_tool_result.ui_message",
                    "model_json_required": False,
                    "assistant_message_type": "primer_design_input",
                    "user_message_type": "primer_design_input_update",
                    "assistant_status": "WAITING_USER",
                    "waiting_next_action": "WAIT_FOR_USER",
                    "stop_current_turn": True,
                    "schema_resources": [
                        "oligosmith://contracts/primer-design-input",
                        "oligosmith://contracts/primer-design-input-update",
                    ],
                    "authoritative_mapping": {
                        "merged.designInput.targetOrganism": "batch_defaults.target_organism.name",
                        "merged.designInput.host": "batch_defaults.hosts[0].name",
                        "merged.designInput.coverageRequirement": "batch_defaults.coverage_requirement",
                    },
                },
                "endpoints": {
                    "prepare_design_input": "POST /v1/wf01/design-input",
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
                "report_schema_version": "wf01-prd-v0.2-canonical-report-v1",
                "user_export_formats": [
                    "html",
                    "process_html",
                    "rules_html",
                    "scientific_data",
                    "csv",
                    "pdf",
                ],
                "default_generated_formats": [
                    "html",
                    "process_html",
                    "rules_html",
                    "scientific_data",
                ],
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

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentd.config import load_config
from agentd.service import AgentDService
from agentd_workflow_snapshots.wf04_v1.agentd.errors import (
    InputValidationError,
    ToolUnavailableError,
)
from agentd_workflow_snapshots.wf04_v1.agentd.models import (
    Candidate,
    Oligo,
    RuleResult,
    RuleStatus,
    SpecificityModeResult,
)
from agentd_workflow_snapshots.wf04_v1.agentd.schema import (
    assemble,
    validate_output,
)
from agentd_workflow_snapshots.wf04_v1.agentd.workflows.wf04_v01 import (
    WF04_POLICY_VERSION,
    WF04V01Validation,
    enrich_wf04_result,
    summarize_wf04_result,
)


RULE_CODES = (
    "R-01",
    "R-01b",
    "R-02",
    "R-03",
    "R-03b",
    "R-04",
    "R-05",
    "R-06",
    "R-07",
    "R-08",
    "R-09",
    "R-10",
    "R-11",
    "R-12",
    "R-13",
    "R-SUPPLEMENT",
    "R-14",
    "R-15",
    "R-16",
    "R-17",
    "R-18",
    "R-19",
    "R-20",
    "R-21",
)


def fixture_candidate() -> Candidate:
    forward = Oligo(
        sequence_5to3="ACGTACGTACGTACGTACGA",
        length_nt=20,
        tm_celsius=60.0,
        gc_percent=50.0,
        position_start=0,
        position_end=19,
        hairpin_dg_kcal=-1.0,
        homodimer_dg_kcal=-2.0,
        three_prime_dimer_dg_kcal=-1.0,
        thermo_method="P1",
    )
    reverse = Oligo(
        sequence_5to3="TCGTACGTACGTACGTACGT",
        length_nt=20,
        tm_celsius=60.5,
        gc_percent=50.0,
        position_start=100,
        position_end=119,
        hairpin_dg_kcal=-1.0,
        homodimer_dg_kcal=-2.0,
        three_prime_dimer_dg_kcal=-1.0,
        thermo_method="P1",
    )
    probe = Oligo(
        sequence_5to3="CACGTACGTACGTACGTACG",
        length_nt=20,
        tm_celsius=69.0,
        gc_percent=55.0,
        position_start=40,
        position_end=59,
        hairpin_dg_kcal=-1.0,
        homodimer_dg_kcal=-2.0,
        three_prime_dimer_dg_kcal=-1.0,
        thermo_method="P1",
    )
    return Candidate(
        candidate_id="WF04-FIXTURE-1",
        forward_primer=forward,
        reverse_primer=reverse,
        probe=probe,
        amplicon_sequence_5to3="ACGT" * 30,
        amplicon_start=0,
        amplicon_end=119,
        conservation_score=0.99,
        conservation_method="fixture",
        conservation_sequence_count=20,
        pair_heterodimer_dg_kcal=-2.0,
        three_prime_heterodimer_dg_kcal=-1.0,
        three_prime_involved=False,
        metadata={
            "software": "fixture",
            "software_version": "1",
            "tm_algorithm": "fixture",
            "mv_conc_mm": 50,
            "dv_conc_mm": 1.5,
            "oligo_conc_nm": 250,
        },
    )


class PassingRuleEngine:
    def evaluate(self, _candidate: Candidate) -> list[RuleResult]:
        return [
            RuleResult(
                code=code,
                category="contract",
                status=RuleStatus.PASS,
                actual=True,
                threshold="fixture",
                source="fixture",
            )
            for code in RULE_CODES
        ]

    @staticmethod
    def apply_verdict(candidate: Candidate) -> Candidate:
        candidate.verdict = "PASS"
        return candidate


class PartialRuleEngine(PassingRuleEngine):
    @staticmethod
    def apply_verdict(candidate: Candidate) -> Candidate:
        candidate.verdict = "PARTIAL"
        return candidate


class RecordingRuleEngine(PassingRuleEngine):
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def evaluate(self, candidate: Candidate) -> list[RuleResult]:
        self.events.append("RULES")
        return super().evaluate(candidate)


class FakeGateway:
    def __init__(
        self,
        *,
        verdicts: dict[str, str] | None = None,
        errors: dict[str, Exception] | None = None,
        unavailable: set[str] | None = None,
        details_by_branch: dict[str, list[dict]] | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.verdicts = verdicts or {}
        self.errors = errors or {}
        self.unavailable = unavailable or set()
        self.details_by_branch = details_by_branch or {}
        self.events = events
        self.p2_calls: list[str] = []
        self.p2_payloads: dict[str, dict] = {}
        self.p1_calls = 0

    def validate_pair(self, _payload: dict) -> Candidate:
        if self.events is not None:
            self.events.append("P1")
        self.p1_calls += 1
        return copy.deepcopy(fixture_candidate())

    def check_p2(
        self,
        branch: str,
        _candidate: Candidate,
        _payload: dict,
    ) -> SpecificityModeResult:
        if self.events is not None:
            self.events.append(f"P2:{branch}")
        self.p2_calls.append(branch)
        self.p2_payloads[branch] = copy.deepcopy(_payload)
        if branch in self.errors:
            raise self.errors[branch]
        details = copy.deepcopy(self.details_by_branch.get(branch) or [])
        return SpecificityModeResult(
            mode={
                "target": "check_target",
                "host": "check_host",
                "cross_species": "check_cross_species",
            }[branch],
            verdict=self.verdicts.get(branch, "PASS"),
            blast_hits=len(details) if details else 1,
            details=details,
            method="blastn",
        )

    def assess_p3(self, _candidate: Candidate, _payload: dict) -> dict:
        if self.events is not None:
            self.events.append("P3")
        return {
            "all_hard_pass": True,
            "warnings": [],
            "tm_in_target": True,
            "corrected_tm_celsius": 68.0,
            "verdict": "PASS",
        }

    def assess_p6(self, _candidate: Candidate) -> dict:
        if self.events is not None:
            self.events.append("P6")
        return {"status": "PASS", "mfe_dg": -10.0}

    def status(self) -> dict[str, dict]:
        return {
            name: {"tool": name, "available": name not in self.unavailable}
            for name in ("P1", "P2", "P3", "P6")
        }


def payload() -> dict:
    return {
        "assay_id": "wf04-fixture",
        "target_id": "fixture",
        "forward_primer": "ACGTACGTACGTACGTACGA",
        "reverse_primer": "TCGTACGTACGTACGTACGT",
        "probe": "CACGTACGTACGTACGTACG",
    }


def workflow(gateway: FakeGateway) -> WF04V01Validation:
    return WF04V01Validation(
        load_config(),
        gateway=gateway,
        rule_engine=PassingRuleEngine(),
    )


def assembled(gateway: FakeGateway) -> dict:
    return enrich_wf04_result(assemble(workflow(gateway).run(payload())))


def test_v01_backend_runs_six_ordered_nodes_and_emits_separate_statuses() -> None:
    result = assembled(FakeGateway())
    validate_output(result)
    assert result["workflow_version"] == WF04_POLICY_VERSION
    assert result["execution_status"] == "SUCCEEDED"
    assert result["decision"] == "PASS"
    assert result["next_action"] == "END"
    assert [step["step"] for step in result["steps"]] == [
        "WF04_INPUT",
        "WF04_STEP1_P1",
        "WF04_STEP2_P2_A",
        "WF04_STEP3_P2_B",
        "WF04_STEP4_P2_C",
        "WF04_STEP5_COMPATIBILITY",
        "WF04_STEP6_DECISION",
    ]
    assert summarize_wf04_result(result)["report_tool"] == "get_wf04_report"


def test_v01_backend_executes_the_expert_steps_in_real_order() -> None:
    events: list[str] = []
    gateway = FakeGateway(events=events)
    source = payload()
    source["use_mgb"] = True

    result = enrich_wf04_result(
        assemble(
            WF04V01Validation(
                load_config(),
                gateway=gateway,
                rule_engine=RecordingRuleEngine(events),
            ).run(source)
        )
    )

    assert events == [
        "P1",
        "P2:target",
        "P2:host",
        "P2:cross_species",
        "P3",
        "P6",
        "RULES",
    ]
    assert [step["step"] for step in result["steps"]] == [
        "WF04_INPUT",
        "WF04_STEP1_P1",
        "WF04_STEP2_P2_A",
        "WF04_STEP3_P2_B",
        "WF04_STEP4_P2_C",
        "WF04_STEP5_COMPATIBILITY",
        "WF04_STEP6_DECISION",
    ]


def test_completed_execution_can_have_a_fail_decision() -> None:
    result = assembled(FakeGateway(verdicts={"target": "FAIL"}))
    assert result["execution_status"] == "SUCCEEDED"
    assert result["decision"] == "FAIL"
    assert result["status"] == "SUCCEEDED"
    assert result["retry"]["allowed"] is False
    assert "P2_DECISION_FAILURE" in result["decision_reasons"]


def test_completed_caution_node_is_not_promoted_to_pass() -> None:
    result = assembled(FakeGateway(verdicts={"host": "CAUTION"}))
    assert result["execution_status"] == "SUCCEEDED"
    assert result["decision"] == "PASS_WITH_WARNINGS"


def test_background_node_waits_for_target_identity_and_excludes_private_context() -> None:
    gateway = FakeGateway(
        details_by_branch={
            "target": [{"accession": "TARGET_RECORD.2"}],
        }
    )
    source = payload()
    source["_database_resolution"] = [
        {
            "field": "target_db",
            "version": "CATALOG_RECORD.3",
        }
    ]

    result = enrich_wf04_result(assemble(workflow(gateway).run(source)))

    assert gateway.p2_calls.index("cross_species") > gateway.p2_calls.index("target")
    assert gateway.p2_calls.index("cross_species") > gateway.p2_calls.index("host")
    assert set(gateway.p2_payloads["cross_species"]["_target_accessions"]) == {
        "fixture",
        "TARGET_RECORD.2",
        "CATALOG_RECORD.3",
    }
    assert "_target_accessions" not in result["input"]


def test_degraded_required_tool_is_inconclusive_even_when_nodes_return() -> None:
    result = assembled(FakeGateway(unavailable={"P1"}))
    assert result["execution_status"] == "PARTIAL"
    assert result["decision"] == "INCONCLUSIVE"
    assert "P1_REQUIRED_EVIDENCE_DEGRADED" in result["decision_reasons"]
    assert "WF04_STEP1_P1" in result["retry"]["retryable_steps"]


def test_required_node_gap_is_partial_and_retryable() -> None:
    result = assembled(
        FakeGateway(errors={"host": ToolUnavailableError("fixture unavailable")})
    )
    assert result["execution_status"] == "PARTIAL"
    assert result["decision"] == "INCONCLUSIVE"
    assert result["next_action"] == "RETRY"
    assert result["retry"]["retryable_steps"] == ["WF04_STEP3_P2_B"]
    assert result["errors"][0]["code"] == "TOOL_UNAVAILABLE"


def test_incomplete_local_evidence_is_not_reported_as_pass() -> None:
    result = enrich_wf04_result(
        assemble(
            WF04V01Validation(
                load_config(),
                gateway=FakeGateway(),
                rule_engine=PartialRuleEngine(),
            ).run(payload())
        )
    )
    assert result["execution_status"] == "PARTIAL"
    assert result["decision"] == "INCONCLUSIVE"
    assert "LOCAL_RULE_EVIDENCE_INCOMPLETE" in result["decision_reasons"]
    assert "WF04_STEP1_P1" in result["retry"]["retryable_steps"]


def test_service_retry_creates_child_and_reuses_unaffected_successful_node(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first_gateway = FakeGateway(
        errors={"host": ToolUnavailableError("fixture unavailable")}
    )
    second_gateway = FakeGateway()
    gateways = [first_gateway, second_gateway]

    def factory(config: dict) -> WF04V01Validation:
        return WF04V01Validation(
            config,
            gateway=gateways.pop(0),
            rule_engine=PassingRuleEngine(),
        )

    monkeypatch.setattr("agentd.service.WF04V01Validation", factory)
    service = AgentDService(state_dir=tmp_path)
    first = service.run_wf04_validation(payload())
    second = service.retry_wf04_validation(first["job_id"], {})
    report = service.get_wf04_report(second["job_id"])

    assert first["job_id"] != second["job_id"]
    assert second["parent_job_id"] == first["job_id"]
    assert second["provenance"]["parent_job_id"] == first["job_id"]
    assert report["execution_status"] == "SUCCEEDED"
    assert report["decision"] == "PASS"
    assert set(second_gateway.p2_calls) == {"host", "cross_species"}
    target_step = next(
        step for step in second["steps"] if step["step"] == "WF04_STEP2_P2_A"
    )
    assert target_step["status"] == "REUSED"
    assert (tmp_path / f"{first['job_id']}.json").exists()
    assert (tmp_path / f"{second['job_id']}.json").exists()


def test_json_contracts_validate_input_retry_and_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    schemas = {
        name: json.loads(
            (ROOT / "schemas" / f"wf04-v01-{name}.schema.json").read_text(
                encoding="utf-8"
            )
        )
        for name in ("input", "retry", "report")
    }
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)
    Draft202012Validator(schemas["input"]).validate(payload())
    Draft202012Validator(schemas["input"]).validate(
        {"input_artifact_id": "himat-" + "a" * 32}
    )
    Draft202012Validator(schemas["retry"]).validate(
        {"retry_steps": ["WF04_STEP3_P2_B"], "reason": "fixture retry"}
    )

    monkeypatch.setattr(
        "agentd.service.WF04V01Validation",
        lambda config: WF04V01Validation(
            config,
            gateway=FakeGateway(),
            rule_engine=PassingRuleEngine(),
        ),
    )
    service = AgentDService(state_dir=tmp_path)
    full = service.run_wf04_validation(payload())
    Draft202012Validator(schemas["report"]).validate(
        service.get_wf04_report(full["job_id"])
    )


def test_service_materializes_wf04_upload_before_workflow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "agentd.service.WF04V01Validation",
        lambda config: WF04V01Validation(
            config,
            gateway=FakeGateway(),
            rule_engine=PassingRuleEngine(),
        ),
    )
    service = AgentDService(state_dir=tmp_path)
    artifact = service.input_artifacts.store_inline_fasta(
        "\n".join(
            (
                ">forward",
                "ACGTACGTACGTACGTACGA",
                ">reverse",
                "TCGTACGTACGTACGTACGT",
                ">probe",
                "CACGTACGTACGTACGTACG",
            )
        )
        + "\n",
        file_name="wf04-input.fasta",
    )

    result = service.run_wf04_validation(
        {"input_artifact_id": artifact["input_artifact_id"]}
    )

    assert result["input"]["input_artifact"]["input_artifact_id"] == artifact[
        "input_artifact_id"
    ]
    private_input = service.store.get_attachment(result["job_id"], "wf04-input")
    assert private_input == {"input_artifact_id": artifact["input_artifact_id"]}


def test_capabilities_publish_wf04_backend_contract(tmp_path: Path) -> None:
    capabilities = AgentDService(state_dir=tmp_path).capabilities()
    wf04 = capabilities["wf04"]
    assert wf04["policy_version"] == WF04_POLICY_VERSION
    assert wf04["agent_orchestration"] == {
        "mode": "LLM_INTENT_ROUTING_ONLY",
        "mcp_run_tool": "run_wf04_validation",
        "mcp_retry_tool": "retry_wf04_validation",
        "mcp_report_tool": "get_wf04_report",
    }
    assert wf04["immutable_retry"] is True
    assert wf04["execution_status_separate_from_decision"] is True
    assert wf04["file_intake"] == {
        "download_tool": "download_himat_file",
        "run_argument": "input_artifact_id",
        "backend_only_parse": True,
        "required_when_attachment_present": True,
    }
    assert wf04["terminal_delivery"]["shared_with"] == "WF-01"
    assert capabilities["tool_endpoints"]["wf04_retry"].endswith("/{job_id}/retry")


def test_invalid_input_stops_before_gateway() -> None:
    gateway = FakeGateway()
    with pytest.raises(InputValidationError, match="forward_primer"):
        workflow(gateway).run({"reverse_primer": "ACGT"})
    assert gateway.p1_calls == 0

from __future__ import annotations

import copy
import inspect
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
from agentd.database import DatabaseCatalog
from agentd.errors import InputValidationError
from agentd.models import (
    Candidate,
    EvidenceLevel,
    Oligo,
    RuleResult,
    RuleStatus,
)
from agentd.service import AgentDService
from agentd.workflows import wf03 as wf03_module
from agentd.workflows.wf03 import WF03InternalControl, summarize_wf03_result


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


def candidate(index: int, *, structure_status: str = "PASS") -> Candidate:
    forward = Oligo(
        sequence_5to3="ACGTACGTACGTACGTACGA",
        length_nt=20,
        tm_celsius=60.0,
        gc_percent=50.0,
        position_start=0,
        position_end=19,
        thermo_method="P1",
    )
    reverse = Oligo(
        sequence_5to3="TCGTACGTACGTACGTACGT",
        length_nt=20,
        tm_celsius=60.5,
        gc_percent=50.0,
        position_start=80,
        position_end=99,
        thermo_method="P1",
    )
    probe = Oligo(
        sequence_5to3="CACGTACGTACGTACGTACG",
        length_nt=20,
        tm_celsius=69.0,
        gc_percent=55.0,
        position_start=35,
        position_end=54,
        thermo_method="P1",
    )
    item = Candidate(
        candidate_id=f"IC-{index}",
        forward_primer=forward,
        reverse_primer=reverse,
        probe=probe,
        amplicon_sequence_5to3="ACGT" * 25,
        amplicon_start=0,
        amplicon_end=99,
        conservation_score=0.995,
        conservation_method="MAFFT",
        conservation_sequence_count=5,
        amplicon_structure={
            "status": structure_status,
            "mfe_dg": -10.0 if structure_status == "PASS" else None,
            "tool": "ViennaRNA" if structure_status == "PASS" else "not_evaluated",
        },
        evidence_level=EvidenceLevel.BLAST_VERIFIED,
        score=0.9 - index / 100,
        rank=index,
        verdict="PASS",
        metadata={
            "role": "internal_control",
            "gene": "ACTB",
            "fluorophore": "Cy5",
            "quencher": "BHQ-2",
        },
    )
    item.rules = [
        RuleResult(
            code=code,
            category=(
                "platform"
                if code in {"R-14", "R-15", "R-16", "R-17"}
                else ("metadata" if code in {"R-18", "R-19", "R-20", "R-21"} else "hard")
            ),
            status=RuleStatus.PASS,
            actual=True,
            threshold="P5 contract",
            source="P5",
        )
        for code in RULE_CODES
    ]
    return item


def p5_success(
    count: int = 3,
    *,
    verdict: str = "PASS",
    blast_verified: bool = True,
    structure_status: str = "PASS",
) -> dict:
    items = [candidate(index, structure_status=structure_status) for index in range(1, count + 1)]
    evidence = (
        EvidenceLevel.BLAST_VERIFIED.value
        if blast_verified
        else EvidenceLevel.IN_SILICO_ONLY.value
    )
    for item in items:
        item.evidence_level = EvidenceLevel(evidence)
    return {
        "status": "COMPLETED",
        "verdict": verdict,
        "gene_selected": "ACTB",
        "refseq_accession": "NM_TEST.1",
        "conservation": 0.995,
        "conservation_status": "PASS",
        "gene_attempts": [
            {
                "gene": "ACTB",
                "status": "PASS",
                "conservation": 0.995,
                "sequence_count": 5,
                "alignment_method": "mafft",
            }
        ],
        "candidates": [item.to_dict() for item in items],
        "candidate_objects": items,
        "cross_check": None,
        "specificity_check": {
            "verdict": "PASS" if blast_verified else "CAUTION",
            "blast_verified": blast_verified,
            "modes": {},
        },
        "evidence_level": evidence,
        "warnings": [],
        "tool_status": {
            "P1": {"tool": "P1", "available": True},
            "P2_C6": {"tool": "P2", "available": blast_verified},
            "P6": {"tool": "P6", "available": structure_status == "PASS"},
        },
    }


def p5_low_conservation_failure() -> dict:
    return {
        "status": "FAILED",
        "verdict": "FAIL",
        "gene_attempts": [
            {"gene": "ACTB", "status": "FAIL", "conservation": 0.94},
            {"gene": "GAPDH", "status": "FAIL", "conservation": 0.95},
            {"gene": "18S rRNA", "status": "FAIL", "conservation": 0.96},
        ],
        "candidates": [],
        "warnings": ["未获得满足保守性要求的管家基因序列"],
        "recommendation": "请提供自定义管家基因",
    }


class FakeP5:
    results: list[dict] = []
    calls: list[dict] = []

    def __init__(self, config: dict, catalog: DatabaseCatalog) -> None:
        self.config = config
        self.catalog = catalog

    def status(self, *, allow_fallback: bool = False) -> dict:
        return {
            "tool": "P5_Internal_Control",
            "available": True,
            "dependencies": {
                "C5": {"tool": "C5", "available": True},
                "P1": {"tool": "P1", "available": True},
                "P2_C6": {"tool": "P2", "available": True},
            },
        }

    def design(self, payload: dict) -> dict:
        self.__class__.calls.append(copy.deepcopy(payload))
        return copy.deepcopy(self.__class__.results.pop(0))


@pytest.fixture(autouse=True)
def reset_fake() -> None:
    FakeP5.results = []
    FakeP5.calls = []


def test_wf03_is_only_an_orchestration_layer() -> None:
    source = inspect.getsource(wf03_module)
    assert "InternalControlDesigner" in source
    for forbidden in (
        "Primer3Adapter",
        "BlastAdapter",
        "MafftAdapter",
        "ViennaRNAAdapter",
        "calc_heterodimer",
        "calc_tm",
        "0.97",
        "0.99",
    ):
        assert forbidden not in source


def test_success_maps_p5_evidence_and_canonicalizes_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentd.workflows.wf03.InternalControlDesigner", FakeP5)
    output = p5_success()
    output["cross_check"] = {
        "performed": True,
        "heterodimer_pairs_checked": 9,
        "worst_heterodimer_dg_kcal": -2.0,
        "verdict": "PASS",
    }
    FakeP5.results = [output]
    config = load_config()
    result = WF03InternalControl(config, DatabaseCatalog(config)).run(
        {
            "host_species": "猪",
            "existing_targets": [
                {
                    "target_id": "pathogen",
                    "forward_primer": {"sequence_5to3": "ACGTACGTACGTACGTACGA"},
                    "reverse_primer": {"sequence_5to3": "TCGTACGTACGTACGTACGT"},
                }
            ],
        }
    )
    assert result.status.value == "SUCCEEDED"
    assert result.target["host_species"] == "pig"
    assert result.evidence_level == EvidenceLevel.BLAST_VERIFIED
    assert len(result.candidates) == 3
    assert FakeP5.calls[0]["host_species"] == "pig"
    assert result.provenance["tool_boundary"]["workflow_calls"] == ["P5"]
    assert result.provenance["tool_boundary"]["scientific_algorithms_implemented_in_workflow"] == []
    assert result.tool_status["P3"]["status"] == "NOT_APPLICABLE"
    assert result.tool_status["P4"]["status"] == "NOT_APPLICABLE"


def test_less_than_three_candidates_is_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentd.workflows.wf03.InternalControlDesigner", FakeP5)
    FakeP5.results = [p5_success(count=2)]
    config = load_config()
    result = WF03InternalControl(config, DatabaseCatalog(config)).run(
        {"host_species": "pig"}
    )
    assert result.status.value == "PARTIAL"
    assert result.internal_control["validation_summary"]["candidate_quantity"] == {
        "status": "CAUTION",
        "actual": 2,
        "required": 3,
    }
    assert any("少于WF-03要求的3套" in warning for warning in result.warnings)


def test_failure_returns_deterministic_reentry_and_exogenous_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agentd.workflows.wf03.InternalControlDesigner", FakeP5)
    FakeP5.results = [p5_low_conservation_failure()]
    config = load_config()
    result = WF03InternalControl(config, DatabaseCatalog(config)).run(
        {"host_species": "cattle"}
    )
    fallback = result.internal_control["fallback"]
    assert result.status.value == "FAILED"
    assert fallback["reason_code"] == "ALL_HOUSEKEEPING_GENES_LOW_CONSERVATION"
    assert fallback["reentry_tool"] == "retry_wf03_internal_control"
    assert fallback["options"][1]["genes"] == ["RPLP0", "B2M", "SDHA", "TBP", "HMBS"]
    assert fallback["options"][2]["action"] == "USE_EXOGENOUS_HETEROLOGOUS_CONTROL"
    summary = summarize_wf03_result(result.to_dict())
    assert summary["next_action"] == "WAIT_FOR_USER"


def test_p6_not_evaluated_is_never_mapped_to_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentd.workflows.wf03.InternalControlDesigner", FakeP5)
    FakeP5.results = [p5_success(structure_status="not_evaluated")]
    config = load_config()
    result = WF03InternalControl(config, DatabaseCatalog(config)).run(
        {"host_species": "chicken"}
    )
    structure = result.internal_control["validation_summary"]["amplicon_structure"]
    assert structure["status"] == "NOT_EVALUATED"
    assert any("不得按PASS解释" in warning for warning in result.warnings)
    assert result.self_check["verdict"] == "CAUTION"


def test_claimed_blast_evidence_requires_p2_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentd.workflows.wf03.InternalControlDesigner", FakeP5)
    output = p5_success(blast_verified=False)
    output["evidence_level"] = EvidenceLevel.BLAST_VERIFIED.value
    FakeP5.results = [output]
    config = load_config()
    result = WF03InternalControl(config, DatabaseCatalog(config)).run(
        {"host_species": "pig"}
    )
    assert result.evidence_level == EvidenceLevel.IN_SILICO_ONLY
    assert result.status.value == "PARTIAL"


def test_verified_blast_caution_keeps_evidence_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agentd.workflows.wf03.InternalControlDesigner", FakeP5)
    output = p5_success(verdict="CAUTION", blast_verified=True)
    output["specificity_check"]["verdict"] = "CAUTION"
    FakeP5.results = [output]
    config = load_config()

    result = WF03InternalControl(config, DatabaseCatalog(config)).run(
        {"host_species": "pig"}
    )

    assert result.status.value == "PARTIAL"
    assert result.internal_control["verdict"] == "CAUTION"
    assert result.evidence_level == EvidenceLevel.BLAST_VERIFIED
    assert result.internal_control["validation_summary"]["specificity"] == {
        "status": "CAUTION",
        "evidence_level": "blast_verified",
        "source": "P5/P2",
    }
    assert summarize_wf03_result(
        result.to_dict(), include_candidates=False
    )["evidence_level"] == "blast_verified"
    assert not any("evidence_level保持in_silico_only" in item for item in result.warnings)


def test_p2_not_evaluated_remains_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentd.workflows.wf03.InternalControlDesigner", FakeP5)
    output = p5_success(blast_verified=False)
    output["specificity_check"].update(
        {
            "verdict": "NOT_EVALUATED",
            "blast_verified": False,
            "reason": "P2_REQUIRED_RESOURCE_NOT_RESOLVED",
        }
    )
    FakeP5.results = [output]
    config = load_config()

    result = WF03InternalControl(config, DatabaseCatalog(config)).run(
        {"host_species": "pig"}
    )

    assert result.status.value == "PARTIAL"
    assert result.internal_control["validation_summary"]["specificity"][
        "status"
    ] == "NOT_EVALUATED"
    assert result.evidence_level == EvidenceLevel.IN_SILICO_ONLY


def test_service_retry_restores_input_and_creates_immutable_child(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "agentd_workflow_snapshots.wf03_v1.agentd.workflows.wf03.InternalControlDesigner",
        FakeP5,
    )
    FakeP5.results = [p5_low_conservation_failure(), p5_success()]
    service = AgentDService(state_dir=tmp_path)
    first = service.run_wf03_internal_control({"host_species": "pig"})
    second = service.retry_wf03_internal_control(
        first["job_id"],
        {
            "preferred_genes": [
                {"gene": "TBP", "refseq_accession": "NM_CUSTOM.1"}
            ],
            "reason": "用户提供TBP RefSeq",
        },
    )
    assert first["job_id"] != second["job_id"]
    assert second["provenance"]["parent_job_id"] == first["job_id"]
    assert FakeP5.calls[1]["preferred_genes"] == ["TBP"]
    assert FakeP5.calls[1]["gene_accessions"] == {"TBP": "NM_CUSTOM.1"}
    assert any(
        step["step"] == "WF03_STEP1_REENTRY"
        for step in second["workflow_trace"]
    )
    assert service.get_wf03_report(second["job_id"])["next_action"] == "END"
    assert (tmp_path / f"{first['job_id']}.json").exists()
    assert (tmp_path / f"{second['job_id']}.json").exists()


def test_service_materializes_wf03_upload_only_inside_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "agentd_workflow_snapshots.wf03_v1.agentd.workflows.wf03.InternalControlDesigner",
        FakeP5,
    )
    FakeP5.results = [p5_success()]
    service = AgentDService(state_dir=tmp_path)
    artifact = service.input_artifacts.store_inline_fasta(
        ">record-1\n" + "ACGT" * 40 + "\n",
        file_name="custom-input.fasta",
    )

    result = service.run_wf03_internal_control(
        {
            "host_species": "pig",
            "input_artifact_id": artifact["input_artifact_id"],
            "input_gene": "CUSTOM_INPUT",
        }
    )

    backend_path = Path(FakeP5.calls[0]["gene_fasta_paths"]["CUSTOM_INPUT"])
    assert backend_path.is_file()
    assert FakeP5.calls[0]["preferred_genes"] == ["CUSTOM_INPUT"]
    assert "gene_fasta_paths" not in result["input"]
    assert result["input"]["gene_fasta_inputs"] == ["CUSTOM_INPUT"]
    assert result["input"]["input_artifact"]["input_artifact_id"] == artifact[
        "input_artifact_id"
    ]
    private_input = service.store.get_attachment(result["job_id"], "wf03-input")
    assert private_input["input_artifact_id"] == artifact["input_artifact_id"]
    assert "gene_fasta_paths" not in private_input


def test_invalid_inputs_stop_before_p5(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentd.workflows.wf03.InternalControlDesigner", FakeP5)
    config = load_config()
    workflow = WF03InternalControl(config, DatabaseCatalog(config))
    with pytest.raises(InputValidationError, match="host_species"):
        workflow.run({})
    with pytest.raises(InputValidationError, match="正、反向引物"):
        workflow.run(
            {
                "host_species": "pig",
                "existing_targets": [{"target_id": "bad", "fwd_seq": "ACGT"}],
            }
        )
    assert FakeP5.calls == []


def test_wf03_json_contracts_validate_backend_and_mcp_shapes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_schema = json.loads(
        (ROOT / "schemas" / "wf03-v01-input.schema.json").read_text(encoding="utf-8")
    )
    retry_schema = json.loads(
        (ROOT / "schemas" / "wf03-v01-retry.schema.json").read_text(encoding="utf-8")
    )
    report_schema = json.loads(
        (ROOT / "schemas" / "wf03-v01-report.schema.json").read_text(encoding="utf-8")
    )
    for schema in (input_schema, retry_schema, report_schema):
        Draft202012Validator.check_schema(schema)
    initial_payload = {
        "host_species": "pig",
        "preferred_genes": [
            {"gene": "ACTB", "refseq_accession": "NM_TEST.1"}
        ],
    }
    retry_payload = {
        "preferred_genes": ["TBP"],
        "gene_accessions": {"TBP": "NM_TEST.2"},
        "reason": "用户补充RefSeq",
    }
    Draft202012Validator(input_schema).validate(initial_payload)
    Draft202012Validator(input_schema).validate(
        {
            "host_species": "pig",
            "input_artifact_id": "himat-" + "a" * 32,
            "input_gene": "CUSTOM_INPUT",
        }
    )
    Draft202012Validator(retry_schema).validate(retry_payload)

    monkeypatch.setattr("agentd.workflows.wf03.InternalControlDesigner", FakeP5)
    FakeP5.results = [p5_success()]
    service = AgentDService(state_dir=tmp_path)
    full = service.run_wf03_internal_control(initial_payload)
    report = service.get_wf03_report(full["job_id"])
    Draft202012Validator(report_schema).validate(report)


def test_capabilities_declare_intent_only_wf03_architecture(tmp_path: Path) -> None:
    capabilities = AgentDService(state_dir=tmp_path).capabilities()
    wf03 = capabilities["wf03"]
    assert wf03["agent_orchestration"] == {
        "mode": "LLM_INTENT_ROUTING_ONLY",
        "mcp_run_tool": "run_wf03_internal_control",
        "mcp_retry_tool": "retry_wf03_internal_control",
        "mcp_report_tool": "get_wf03_report",
    }
    assert wf03["direct_scientific_tool"] == "P5"
    assert wf03["file_intake"] == {
        "download_tool": "download_himat_file",
        "run_argument": "input_artifact_id",
        "backend_only_parse": True,
        "required_when_attachment_present": True,
    }
    assert wf03["terminal_delivery"] == {
        "contract": "primer_design_output",
        "model_must_relay_verbatim": True,
        "shared_with": "WF-01",
    }
    assert capabilities["tool_endpoints"]["wf03_retry"].endswith("/{job_id}/retry")

from __future__ import annotations

import inspect
import importlib
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

class FakeWF03Service:
    def __init__(self) -> None:
        self.run_payload: dict | None = None
        self.retry_call: tuple[str, dict] | None = None
        self.report_ids: list[str] = []

    def run_wf03_internal_control(self, payload: dict) -> dict:
        self.run_payload = payload
        return {"workflow": "WF-03", "job_id": "job-1"}

    def retry_wf03_internal_control(self, job_id: str, response: dict) -> dict:
        self.retry_call = (job_id, response)
        return {"workflow": "WF-03", "job_id": "job-2"}

    def get_wf03_delivery(self, job_id: str) -> dict:
        self.report_ids.append(job_id)
        return {
            "workflow": "WF-03",
            "job_id": job_id,
            "status": "SUCCEEDED",
            "next_action": "END",
        }


def direct_invoke(_operation: str, callback, **_kwargs):
    return callback()


@pytest.fixture
def mcp_server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("MCP_AUTH_REQUIRED", "false")
    monkeypatch.setenv("AGENTD_STATE_DIR", str(tmp_path))
    sys.modules.pop("agentd.mcp_server", None)
    sys.modules.pop("agentd.api", None)
    return importlib.import_module("agentd.mcp_server")


def test_run_mcp_tool_only_routes_user_input_to_backend(monkeypatch, mcp_server) -> None:
    service = FakeWF03Service()
    monkeypatch.setattr(mcp_server, "service", service)
    monkeypatch.setattr(mcp_server, "_invoke", direct_invoke)
    result = mcp_server.run_wf03_internal_control(
        "pig",
        input_artifact_id="himat-" + "a" * 32,
        input_gene="CUSTOM_INPUT",
        existing_targets=[
            {
                "target_id": "virus",
                "fwd_seq": "ACGT",
                "rev_seq": "TGCA",
            }
        ],
        preferred_genes=[{"gene": "TBP", "refseq_accession": "NM_TEST.1"}],
    )
    assert result["job_id"] == "job-1"
    assert service.run_payload == {
        "host_species": "pig",
        "input_artifact_id": "himat-" + "a" * 32,
        "input_gene": "CUSTOM_INPUT",
        "existing_targets": [
            {
                "target_id": "virus",
                "fwd_seq": "ACGT",
                "rev_seq": "TGCA",
            }
        ],
        "preferred_genes": [
            {"gene": "TBP", "refseq_accession": "NM_TEST.1"}
        ],
    }
    assert service.report_ids == ["job-1"]


def test_retry_mcp_tool_delegates_immutable_reentry(monkeypatch, mcp_server) -> None:
    service = FakeWF03Service()
    monkeypatch.setattr(mcp_server, "service", service)
    monkeypatch.setattr(mcp_server, "_invoke", direct_invoke)
    result = mcp_server.retry_wf03_internal_control(
        "job-1",
        preferred_genes=["RPLP0"],
        gene_accessions={"RPLP0": "NM_TEST.2"},
        reason="用户补充RefSeq",
    )
    assert result["job_id"] == "job-2"
    assert service.retry_call == (
        "job-1",
        {
            "preferred_genes": ["RPLP0"],
            "gene_accessions": {"RPLP0": "NM_TEST.2"},
            "reason": "用户补充RefSeq",
        },
    )
    assert service.report_ids == ["job-2"]


def test_mcp_layer_contains_no_scientific_implementation(mcp_server) -> None:
    source = "\n".join(
        (
            inspect.getsource(mcp_server.run_wf03_internal_control),
            inspect.getsource(mcp_server.retry_wf03_internal_control),
            inspect.getsource(mcp_server.get_wf03_report),
        )
    )
    assert "service.run_wf03_internal_control" in source
    assert "service.get_wf03_delivery" in source
    for forbidden in (
        "InternalControlDesigner",
        "Primer3Adapter",
        "BlastAdapter",
        "MafftAdapter",
        "ViennaRNAAdapter",
        "calc_tm",
        "calc_heterodimer",
    ):
        assert forbidden not in source

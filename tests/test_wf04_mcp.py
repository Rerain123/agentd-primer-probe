from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class FakeWF04Service:
    def __init__(self) -> None:
        self.run_payload: dict | None = None
        self.retry_call: tuple[str, dict] | None = None
        self.report_ids: list[str] = []

    def run_wf04_validation(self, payload: dict) -> dict:
        self.run_payload = payload
        return {"workflow": "WF-04", "job_id": "job-1"}

    def retry_wf04_validation(self, job_id: str, response: dict) -> dict:
        self.retry_call = (job_id, response)
        return {"workflow": "WF-04", "job_id": "job-2"}

    def get_wf04_delivery(self, job_id: str) -> dict:
        self.report_ids.append(job_id)
        return {
            "workflow": "WF-04",
            "job_id": job_id,
            "execution_status": "SUCCEEDED",
            "decision": "PASS",
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


def test_run_tool_only_maps_arguments_to_backend(monkeypatch, mcp_server) -> None:
    service = FakeWF04Service()
    monkeypatch.setattr(mcp_server, "service", service)
    monkeypatch.setattr(mcp_server, "_invoke", direct_invoke)
    result = mcp_server.run_wf04_validation(
        "ACGTACGTACGTACGTACGA",
        "TCGTACGTACGTACGTACGT",
        probe="CACGTACGTACGTACGTACG",
        target_id="fixture",
    )
    assert result["job_id"] == "job-1"
    assert service.run_payload == {
        "forward_primer": "ACGTACGTACGTACGTACGA",
        "reverse_primer": "TCGTACGTACGTACGTACGT",
        "use_mgb": False,
        "probe": "CACGTACGTACGTACGTACG",
        "target_id": "fixture",
    }
    assert service.report_ids == ["job-1"]


def test_run_tool_accepts_wf01_style_artifact_reference(monkeypatch, mcp_server) -> None:
    service = FakeWF04Service()
    monkeypatch.setattr(mcp_server, "service", service)
    monkeypatch.setattr(mcp_server, "_invoke", direct_invoke)

    result = mcp_server.run_wf04_validation(
        input_artifact_id="himat-" + "a" * 32,
        target_id="fixture",
    )

    assert result["job_id"] == "job-1"
    assert service.run_payload == {
        "use_mgb": False,
        "input_artifact_id": "himat-" + "a" * 32,
        "target_id": "fixture",
    }


def test_retry_tool_delegates_immutable_child_creation(monkeypatch, mcp_server) -> None:
    service = FakeWF04Service()
    monkeypatch.setattr(mcp_server, "service", service)
    monkeypatch.setattr(mcp_server, "_invoke", direct_invoke)
    result = mcp_server.retry_wf04_validation(
        "job-1",
        retry_steps=["WF04_STEP3_P2_B"],
        host_db="replacement-db",
        reason="fixture retry",
    )
    assert result["job_id"] == "job-2"
    assert service.retry_call == (
        "job-1",
        {
            "retry_steps": ["WF04_STEP3_P2_B"],
            "host_db": "replacement-db",
            "reason": "fixture retry",
        },
    )
    assert service.report_ids == ["job-2"]


def test_report_tool_is_a_bounded_service_read(monkeypatch, mcp_server) -> None:
    service = FakeWF04Service()
    monkeypatch.setattr(mcp_server, "service", service)
    monkeypatch.setattr(mcp_server, "_invoke", direct_invoke)
    result = mcp_server.get_wf04_report("job-9")
    assert result["job_id"] == "job-9"
    assert service.report_ids == ["job-9"]


def test_mcp_layer_contains_no_workflow_implementation(mcp_server) -> None:
    source = "\n".join(
        (
            inspect.getsource(mcp_server.run_wf04_validation),
            inspect.getsource(mcp_server.retry_wf04_validation),
            inspect.getsource(mcp_server.get_wf04_report),
        )
    )
    assert "service.run_wf04_validation" in source
    assert "service.get_wf04_delivery" in source
    for forbidden in (
        "Primer3Adapter",
        "BlastAdapter",
        "SpecificityEvaluator",
        "MGBProbeEvaluator",
        "ViennaRNAAdapter",
        "RuleEngine",
        "calc_tm",
        "calc_heterodimer",
    ):
        assert forbidden not in source

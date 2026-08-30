from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@unittest.skipUnless(importlib.util.find_spec("fastapi") is not None, "FastAPI extra not installed")
class ApiTests(unittest.TestCase):
    def test_openapi_operation_text_is_chinese(self) -> None:
        from agentd.api import app

        for path, path_item in app.openapi()["paths"].items():
            for method, operation in path_item.items():
                if not isinstance(operation, dict):
                    continue
                self.assertRegex(
                    operation["summary"],
                    r"[\u4e00-\u9fff]",
                    f"{method.upper()} {path} 的 OpenAPI summary 应包含中文",
                )
                if "description" in operation:
                    self.assertRegex(
                        operation["description"],
                        r"[\u4e00-\u9fff]",
                        f"{method.upper()} {path} 的 OpenAPI description 应包含中文",
                    )

    def test_cli_serve_propagates_service_overrides(self) -> None:
        from agentd.cli import main

        fake_uvicorn = types.SimpleNamespace(run=Mock())
        previous_state = os.environ.get("AGENTD_STATE_DIR")
        previous_config = os.environ.get("AGENTD_CONFIG")
        with tempfile.TemporaryDirectory(prefix="agentd-api-cli-") as state_dir:
            config_path = ROOT / "config" / "defaults.json"
            try:
                with patch.dict(sys.modules, {"uvicorn": fake_uvicorn}):
                    exit_code = main(
                        [
                            "--config",
                            str(config_path),
                            "--state-dir",
                            state_dir,
                            "serve",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            "8765",
                        ]
                    )
                self.assertEqual(exit_code, 0)
                self.assertEqual(os.environ["AGENTD_CONFIG"], str(config_path.resolve()))
                self.assertEqual(os.environ["AGENTD_STATE_DIR"], str(Path(state_dir).resolve()))
                fake_uvicorn.run.assert_called_once_with(
                    "agentd.mcp_server:app",
                    host="127.0.0.1",
                    port=8765,
                    factory=False,
                )
            finally:
                if previous_state is None:
                    os.environ.pop("AGENTD_STATE_DIR", None)
                else:
                    os.environ["AGENTD_STATE_DIR"] = previous_state
                if previous_config is None:
                    os.environ.pop("AGENTD_CONFIG", None)
                else:
                    os.environ["AGENTD_CONFIG"] = previous_config

    def test_health_and_wf04(self) -> None:
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory(prefix="agentd-api-tests-") as state_dir:
            previous = os.environ.get("AGENTD_STATE_DIR")
            os.environ["AGENTD_STATE_DIR"] = state_dir
            try:
                import agentd.api as api_module
                from agentd.service import AgentDService

                with patch.object(
                    api_module,
                    "service",
                    AgentDService(state_dir=state_dir),
                ):
                    client = TestClient(api_module.app)
                    interface = client.get("/")
                    self.assertEqual(interface.status_code, 200)
                    self.assertIn("Agent D", interface.text)
                    self.assertIn('id="topCandidates"', interface.text)
                    self.assertNotIn('id="enableRemoteBlast"', interface.text)
                    self.assertIn('id="hostSpecies"', interface.text)
                    self.assertIn('id="productMin"', interface.text)
                    stylesheet = client.get("/assets/app.css")
                    self.assertEqual(stylesheet.status_code, 200)
                    self.assertIn("[hidden]", stylesheet.text)
                    script = client.get("/assets/app.js")
                    self.assertEqual(script.status_code, 200)
                    self.assertIn("top_candidates", script.text)
                    self.assertIn("design_parameters", script.text)
                    self.assertNotIn("startBlastReview", script.text)
                    self.assertNotIn("remote_blast_review", script.text)
                    health = client.get("/health")
                    self.assertEqual(health.status_code, 200)
                    self.assertEqual(health.json()["phase"], 3)
                    self.assertEqual(client.get("/ready").status_code, 200)
                    capabilities = client.get("/v1/capabilities")
                    self.assertEqual(capabilities.status_code, 200)
                    self.assertEqual(
                        set(capabilities.json()["workflows"]),
                        {"WF-01", "WF-02", "WF-03", "WF-04", "WF-05"},
                    )
                    self.assertEqual(
                        client.post("/v1/wf06/complete-panel", json={}).status_code,
                        404,
                    )
                    self.assertEqual(
                        client.post("/v1/wf07/diagnose", json={}).status_code,
                        404,
                    )
                    self.assertNotIn(
                        "ncbi_blast_submit",
                        capabilities.json()["tool_endpoints"],
                    )
                    self.assertNotIn(
                        "blast_review_start",
                        capabilities.json()["tool_endpoints"],
                    )
                    self.assertEqual(
                        capabilities.json()["tool_endpoints"]["primer3_design"],
                        "POST /v1/tools/primer3/design",
                    )
                    tools = client.get("/v1/tool-status").json()["tools"]
                    self.assertIn("C6", tools)
                    self.assertNotIn("C6_REMOTE", tools)
                    self.assertEqual(
                        client.post("/v1/tools/blast/submit", json={}).status_code,
                        404,
                    )
                    self.assertEqual(
                        client.post("/v1/jobs/example/blast-review").status_code,
                        404,
                    )
                    payload = {
                        "assay_id": "wf04-api-fixture",
                        "target_id": "fixture-target",
                        "target_species": "fixture",
                        "forward_primer": "GCGAGGAAGATGTACGGATA",
                        "reverse_primer": "GCTACAGGCTCCCTTCTAA",
                        "probe": "ATCGCGGTTAACCTAGTCCCTGTGC",
                        "template": (
                            "TGGGCGAACTTGGTCACCCCGAAGTATCTGATGAGATGATCACCGAGAGCC"
                            "GGGGCGAGGAAGATGTACGGATACTTTCCGCACAGGGACTAGGTTAACCGCGA"
                            "TTTCTTATCCTGCGATAGCCGGCCGTGTAAACCTTTCTTAGGCATGGCAGAAA"
                            "ATGCAATCATATAACGGGGTTAGAAGGGAGCCTGTAGCATGCTGCCCGATTTC"
                            "CCGTGTACCCCTGTCGCTGCGAAGTATATCCAGAGGTGCCGGTGCTAGCC"
                        ),
                        "host_species": "fixture-host",
                        "host_db": str(ROOT / "testdata" / "host.fasta"),
                        "cross_db": str(
                            ROOT / "testdata" / "cross_species.fasta"
                        ),
                        "allow_fallback": True,
                    }
                    response = client.post("/v1/wf04/validate", json=payload)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(len(response.json()["candidates"]), 1)

                    wf05_payload = {
                        "target_id": payload["target_id"],
                        "target_species": payload["target_species"],
                        "forward_primer": payload["forward_primer"],
                        "reverse_primer": payload["reverse_primer"],
                        "probe": payload["probe"],
                        "sequences": [
                            {"id": "reference", "sequence": payload["template"]},
                            {"id": "second", "sequence": payload["template"]},
                        ],
                        "allow_fallback": True,
                    }
                    wf05 = client.post("/v1/wf05/variation", json=wf05_payload)
                    self.assertEqual(wf05.status_code, 200, wf05.text)
                    self.assertEqual(
                        wf05.json()["workflow_version"],
                        "wf05-v0.1-rules-2026-08-26",
                    )
                    wf05_report = client.get(
                        f"/v1/wf05/variation/{wf05.json()['job_id']}/report"
                    )
                    self.assertEqual(wf05_report.status_code, 200, wf05_report.text)
                    self.assertNotIn(
                        "per_sequence",
                        wf05_report.json()["variation"],
                    )
            finally:
                if previous is None:
                    os.environ.pop("AGENTD_STATE_DIR", None)
                else:
                    os.environ["AGENTD_STATE_DIR"] = previous

    def test_standalone_primer3_endpoint_is_sequence_only(self) -> None:
        from fastapi.testclient import TestClient
        import agentd.api as api_module
        from agentd.service import AgentDService

        expected = {
            "tool": "P1_Primer3_DESIGN",
            "status": "COMPLETED",
            "candidate_count": 1,
            "candidates": [{"candidate_id": "p3-1"}],
            "specificity": {"status": "NOT_EVALUATED"},
        }
        with tempfile.TemporaryDirectory(prefix="agentd-api-primer3-") as state_dir:
            isolated_service = AgentDService(state_dir=state_dir)
            with (
                patch.object(api_module, "service", isolated_service),
                patch.object(
                    isolated_service,
                    "design_with_primer3",
                    return_value=expected,
                ) as design,
            ):
                response = TestClient(api_module.app).post(
                    "/v1/tools/primer3/design",
                    json={"sequence": "ACGT" * 30},
                )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), expected)
        design.assert_called_once_with({"sequence": "ACGT" * 30})

    def test_removed_wf01_design_input_endpoint_returns_404(self) -> None:
        from fastapi.testclient import TestClient
        import agentd.api as api_module

        response = TestClient(api_module.app).post(
            "/v1/wf01/design-input",
            json={"designInput": {"targetOrganism": "测试病毒"}},
        )

        self.assertEqual(response.status_code, 404, response.text)

    def test_wf01_create_rejects_design_only_before_persistence(self) -> None:
        from fastapi.testclient import TestClient
        import agentd.api as api_module
        from agentd.service import AgentDService

        with tempfile.TemporaryDirectory(prefix="agentd-api-full-route-") as state_dir:
            isolated_service = AgentDService(state_dir=state_dir)
            with patch.object(api_module, "service", isolated_service):
                client = TestClient(api_module.app)
                preflight_response = client.post(
                    "/v1/wf01/tasks/preflight",
                    json={"sequence": "ACGT" * 40},
                )
                response = client.post(
                    "/v1/wf01/tasks",
                    json={"sequence": "ACGT" * 40},
                )

            self.assertEqual(
                preflight_response.status_code,
                422,
                preflight_response.text,
            )
            self.assertEqual(response.status_code, 422, response.text)
            detail = response.json()["detail"]
            self.assertEqual(detail["code"], "INVALID_INPUT")
            self.assertEqual(
                detail["details"]["required_execution_path"],
                "FULL_SPECIFICITY",
            )
            self.assertFalse(
                detail["details"]["design_only_fallback_allowed"]
            )
            batch_dir = Path(state_dir) / "wf01-v02-batches"
            self.assertFalse(batch_dir.exists())

    def test_wf01_interaction_response_endpoint_queues_confirmed_batch(self) -> None:
        from fastapi.testclient import TestClient
        import agentd.api as api_module
        from agentd.service import AgentDService

        with tempfile.TemporaryDirectory(prefix="agentd-api-interaction-") as state_dir:
            isolated_service = AgentDService(state_dir=state_dir)
            with patch.object(api_module, "service", isolated_service):
                client = TestClient(api_module.app)
                direct_preflight = client.post(
                    "/v1/wf01/tasks/preflight",
                    json={
                        "fasta_text": ">target_001\n" + "ACGT" * 30,
                        "designInput": {
                            "targetOrganism": "Synthetic test virus",
                            "host": "duck",
                            "coverageRequirement": "覆盖多个分型/毒株",
                        },
                    },
                )
                self.assertEqual(
                    direct_preflight.status_code, 200, direct_preflight.text
                )
                self.assertTrue(direct_preflight.json()["can_auto_run"])

                created = client.post(
                    "/v1/wf01/tasks",
                    json={
                        "task_name": "api-confirmation",
                        "fasta_text": ">target_001\n" + "ACGT" * 30,
                        "batch_defaults": {
                            "target_organism": {"name": "Synthetic test virus"},
                            "hosts": [{"name": "sheep"}],
                        },
                    },
                )
                self.assertEqual(created.status_code, 200, created.text)
                batch = created.json()
                self.assertEqual(batch["ui_message"]["type"], "agent_interaction")
                self.assertEqual(batch["next_action"], "WAIT_FOR_USER")
                self.assertTrue(batch["ui_message"]["flowControl"]["stopCurrentTurn"])
                self.assertIn("interactionId", batch["ui_message"])
                self.assertNotIn("interactions", batch["ui_message"])
                interaction_id = batch["agent_interactions"][0]["data"][
                    "interactionId"
                ]

                submitted = client.post(
                    f"/v1/wf01/tasks/{batch['batch_id']}/interactions/response",
                    json={
                        "type": "agent_interaction_response",
                        "task": "PRIMER_DESIGN",
                        "interactions": [
                            {
                                "interactionId": interaction_id,
                                "answer": "CONTINUE_WITH_NOT_EVALUATED",
                            }
                        ],
                    },
                )
                self.assertEqual(submitted.status_code, 200, submitted.text)
                self.assertEqual(submitted.json()["status"], "QUEUED")
                self.assertIsNone(submitted.json()["ui_message"])

                fetched = client.get(f"/v1/wf01/tasks/{batch['batch_id']}")
                self.assertEqual(fetched.status_code, 200, fetched.text)
                self.assertEqual(fetched.json()["status"], "QUEUED")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.errors import InputValidationError
from agentd.schema import validate_output
from agentd.service import AgentDService
from agentd.workflows.wf01 import WF01SingleTarget


def load_example(name: str) -> dict:
    payload = json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))
    for key in ("host_db", "cross_db", "target_db"):
        if payload.get(key):
            payload[key] = str(ROOT / payload[key])
    return payload


class WorkflowTests(unittest.TestCase):
    def test_wf01_development_closed_loop(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-tests-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            output = service.run("WF-01", load_example("wf01-request.json"), persist=True)
            validate_output(output)
            self.assertEqual(output["status"], "PARTIAL")
            self.assertGreaterEqual(len(output["candidates"]), 1)
            self.assertLessEqual(len(output["candidates"]), 3)
            self.assertEqual(output["evidence_level"], "in_silico_only")
            self.assertTrue((Path(state_dir) / f"{output['job_id']}.json").exists())
            for candidate in output["candidates"]:
                self.assertTrue(candidate["amplicon"]["sequence_5to3"])
                self.assertEqual(len(candidate["rules"]), 24)
                self.assertNotEqual(candidate["verdict"], "FAIL")

    def test_wf04_existing_pair(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-tests-") as state_dir:
            output = AgentDService(state_dir=state_dir).run(
                "WF-04", load_example("wf04-request.json"), persist=False
            )
            validate_output(output)
            self.assertIn(output["status"], {"PARTIAL", "SUCCEEDED"})
            self.assertEqual(len(output["candidates"]), 1)
            self.assertEqual(output["candidates"][0]["amplicon"]["length_bp"], 141)

    def test_wf01_rejects_short_template(self) -> None:
        service = AgentDService(state_dir=ROOT / "runtime" / "test-jobs")
        with self.assertRaises(InputValidationError):
            service.run(
                "WF-01",
                {"target_id": "short", "sequence": "ACGT" * 19 + "ACG", "allow_fallback": True},
                persist=False,
            )

    def test_wf01_respects_requested_candidate_count(self) -> None:
        payload = load_example("wf01-request.json")
        payload["top_candidates"] = 10
        with tempfile.TemporaryDirectory(prefix="agentd-tests-") as state_dir:
            output = AgentDService(state_dir=state_dir).run("WF-01", payload, persist=False)
        validate_output(output)
        self.assertEqual(len(output["candidates"]), 10)
        self.assertEqual(output["relaxation_metadata"]["requested_top_candidates"], 10)
        self.assertEqual(output["relaxation_metadata"]["returned_candidates"], 10)

    def test_wf01_rejects_candidate_count_above_configured_maximum(self) -> None:
        payload = load_example("wf01-request.json")
        payload["top_candidates"] = 51
        with tempfile.TemporaryDirectory(prefix="agentd-tests-") as state_dir:
            with self.assertRaises(InputValidationError):
                AgentDService(state_dir=state_dir).run("WF-01", payload, persist=False)

    def test_wf01_applies_user_design_parameters_to_all_rounds(self) -> None:
        payload = load_example("wf01-request.json")
        payload["design_parameters"] = {
            "product_min_bp": 90,
            "product_max_bp": 130,
            "primer_min_gc": 40,
            "primer_max_gc": 60,
        }
        with tempfile.TemporaryDirectory(prefix="agentd-tests-") as state_dir:
            output = AgentDService(state_dir=state_dir).run("WF-01", payload, persist=False)
        for attempted in output["relaxation_metadata"]["attempted_rounds"]:
            self.assertEqual(attempted["parameters"]["product_min_bp"], 90)
            self.assertEqual(attempted["parameters"]["product_max_bp"], 130)
            self.assertEqual(attempted["parameters"]["primer_min_gc"], 40)
            self.assertEqual(attempted["parameters"]["primer_max_gc"], 60)

    def test_wf01_rejects_inverted_design_parameter_range(self) -> None:
        payload = load_example("wf01-request.json")
        payload["design_parameters"] = {"product_min_bp": 160, "product_max_bp": 100}
        with tempfile.TemporaryDirectory(prefix="agentd-tests-") as state_dir:
            with self.assertRaises(InputValidationError):
                AgentDService(state_dir=state_dir).run("WF-01", payload, persist=False)

    def test_wf01_short_region_skips_l1_l2_and_jumps_to_l3(self) -> None:
        rounds = [
            ("standard", {}),
            ("L1", {}),
            ("L2", {}),
            ("L3", {}),
        ]
        selected = WF01SingleTarget._rounds_for_region(rounds, {"length": 119})
        self.assertEqual([label for label, _parameters in selected], ["standard", "L3"])
        unmodified = WF01SingleTarget._rounds_for_region(rounds, {"length": 120})
        self.assertEqual([label for label, _parameters in unmodified], ["standard", "L1", "L2", "L3"])


if __name__ == "__main__":
    unittest.main()

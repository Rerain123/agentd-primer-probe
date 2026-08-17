from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.schema import validate_output
from agentd.service import AgentDService


class WF05Tests(unittest.TestCase):
    def test_high_frequency_three_prime_variant_recommends_redesign(self) -> None:
        source = json.loads((ROOT / "examples" / "wf04-request.json").read_text(encoding="utf-8"))
        template = source["template"]
        forward_start = template.index(source["forward_primer"])
        variant_position = forward_start + len(source["forward_primer"]) - 1
        replacement = "C" if template[variant_position] != "C" else "A"
        mutant = template[:variant_position] + replacement + template[variant_position + 1 :]
        sequences = [{"id": "reference", "sequence": template}]
        sequences.extend({"id": f"variant_{index}", "sequence": mutant} for index in range(2))
        sequences.extend({"id": f"wild_{index}", "sequence": template} for index in range(27))
        payload = {
            "target_id": source["target_id"],
            "target_species": source["target_species"],
            "forward_primer": source["forward_primer"],
            "reverse_primer": source["reverse_primer"],
            "probe": source["probe"],
            "sequences": sequences,
            "allow_fallback": True,
        }
        with tempfile.TemporaryDirectory(prefix="agentd-wf05-") as state_dir:
            output = AgentDService(state_dir=state_dir).run("WF-05", payload, persist=False)
        validate_output(output)
        variation = output["variation"]
        self.assertEqual(variation["sequences_analyzed"], 30)
        self.assertAlmostEqual(variation["coverage_100pct_match"], 28 / 30, places=5)
        self.assertEqual(variation["verdict"], "REDESIGN_RECOMMENDED")
        self.assertEqual(variation["redesign_route"], "WF-01")
        self.assertEqual(variation["high_risk_sites"][0]["risk_level"], "HIGH")
        self.assertIn("C29_candidate_comparison_markdown", output["reports"])


if __name__ == "__main__":
    unittest.main()

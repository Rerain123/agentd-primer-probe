"""Self-checks for the M0 PRD-v0.2 baseline assets.

These tests intentionally validate the frozen requirements and fixtures, not
the legacy WF-01 implementation. Behavioural tests are added in M1-M6.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
M0_ROOT = ROOT / "testdata" / "wf01_prd_v0_2"


def read_fasta_records(path: Path) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    identifier: str | None = None
    sequence_parts: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(">"):
            if identifier is not None:
                records.append((identifier, "".join(sequence_parts)))
            identifier = line[1:].split(maxsplit=1)[0]
            sequence_parts = []
        elif line.strip():
            sequence_parts.append(line.strip())
    if identifier is not None:
        records.append((identifier, "".join(sequence_parts)))
    return records


class WF01PrdV02M0AssetsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = json.loads((M0_ROOT / "acceptance_manifest.json").read_text(encoding="utf-8"))

    def test_frozen_scope_constants_match_prd(self) -> None:
        scope = self.manifest["scope"]
        parameters = self.manifest["parameters"]
        self.assertEqual(scope["template_model"], "one_fasta_record_equals_one_independent_target")
        self.assertEqual(scope["fixed_top_candidates"], 10)
        self.assertEqual(scope["minimum_template_length_nt"], 80)
        self.assertEqual(scope["allowed_parameter_profiles"], ["user", "default", "L1", "L2"])
        self.assertIn("L3", scope["excluded_capabilities"])
        self.assertIn("MGB", scope["excluded_capabilities"])
        self.assertEqual(parameters["amplicon_length_bp"]["L1"], [70, 180])
        self.assertEqual(parameters["amplicon_length_bp"]["L2"], [70, 200])
        self.assertEqual(parameters["probe_length_nt"], {"minimum": 22, "maximum": 32})

    def test_frozen_scoring_and_statuses_match_prd(self) -> None:
        scoring = self.manifest["scoring"]
        self.assertFalse(scoring["conservation_in_score"])
        self.assertEqual(
            scoring["weights"],
            {"thermodynamics": 0.357, "specificity": 0.357, "amplicon_features": 0.286},
        )
        self.assertEqual(
            self.manifest["allowed_rule_statuses"],
            ["PASS", "WARNING", "FAIL", "NOT_EVALUATED", "NOT_APPLICABLE"],
        )

    def test_acceptance_cases_only_reference_registered_requirements(self) -> None:
        requirements = set(self.manifest["requirements"])
        self.assertGreaterEqual(len(requirements), 25)
        for case in self.manifest["acceptance_cases"]:
            self.assertTrue(case["id"].startswith("AC-"))
            self.assertTrue(set(case["requirements"]).issubset(requirements), case["id"])

    def test_fasta_fixtures_have_the_declared_preflight_properties(self) -> None:
        valid = read_fasta_records(M0_ROOT / "multi_target_valid.fasta")
        self.assertEqual([identifier for identifier, _sequence in valid], ["target_001", "target_002", "target_003"])
        self.assertTrue(all(len(sequence) >= 80 for _identifier, sequence in valid))

        short = read_fasta_records(M0_ROOT / "short_79.fasta")
        self.assertEqual(len(short), 1)
        self.assertEqual(len(short[0][1]), 79)

        duplicate = read_fasta_records(M0_ROOT / "duplicate_ids.fasta")
        self.assertEqual([identifier for identifier, _sequence in duplicate], ["duplicate_target", "duplicate_target"])


if __name__ == "__main__":
    unittest.main()

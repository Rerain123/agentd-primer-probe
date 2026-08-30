from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.workflow_contracts.wf01_candidate_v1 import (
    WF01_CANDIDATE_CONTRACT_VERSION,
    WF01_CANONICAL_REPORT_SCHEMA_VERSION,
    select_wf01_candidates,
    wf01_candidate_contract_manifest,
)


class WF01CandidateContractV1Tests(unittest.TestCase):
    @staticmethod
    def _report(candidates: list[dict]) -> dict:
        return {
            "schema_version": WF01_CANONICAL_REPORT_SCHEMA_VERSION,
            "targets": [
                {
                    "target_id": "target-1",
                    "all_qualified_candidates": copy.deepcopy(candidates),
                    "top10": [{"candidate_id": "top10-must-not-win"}],
                    "conclusion": {"status": "PASS_WITH_WARNINGS"},
                }
            ],
        }

    def test_preserves_upstream_order_and_treats_status_fields_as_opaque(self) -> None:
        report = self._report(
            [
                {
                    "candidate_id": "upstream-first",
                    "verdict": "PASS_WITH_WARNINGS",
                    "specificity": {"overall_status": "CAUTION"},
                    "rules": [{"code": "R19", "status": "WARNING"}],
                },
                {
                    "candidate_id": "upstream-second",
                    "verdict": "OPAQUE_UPSTREAM_VALUE",
                    "specificity": {"overall_status": "OPAQUE"},
                    "rules": [{"code": "R19", "status": "OPAQUE"}],
                },
            ]
        )

        selected, evidence = select_wf01_candidates(report, limit=1)

        self.assertEqual(
            [candidate["candidate_id"] for candidate in selected],
            ["upstream-first"],
        )
        self.assertEqual(evidence["contract_status"], "VALID")
        self.assertEqual(
            evidence["source_collection"], "all_qualified_candidates"
        )
        self.assertEqual(
            evidence["downstream_business_reinterpretation"], "DISABLED"
        )
        selected[0]["candidate_id"] = "mutated"
        self.assertEqual(
            report["targets"][0]["all_qualified_candidates"][0][
                "candidate_id"
            ],
            "upstream-first",
        )

    def test_rejects_schema_drift_instead_of_silently_changing_behavior(self) -> None:
        report = self._report([{"candidate_id": "candidate-1"}])
        report["schema_version"] = "wf01-future-schema"

        selected, evidence = select_wf01_candidates(report)

        self.assertEqual(selected, [])
        self.assertEqual(evidence["contract_status"], "INVALID")
        self.assertEqual(evidence["code"], "WF01_REPORT_SCHEMA_UNSUPPORTED")

    def test_rejects_duplicate_candidate_identity_as_contract_corruption(self) -> None:
        report = self._report(
            [
                {"candidate_id": "duplicate"},
                {"candidate_id": "duplicate"},
            ]
        )

        selected, evidence = select_wf01_candidates(report)

        self.assertEqual(selected, [])
        self.assertEqual(evidence["contract_status"], "INVALID")
        self.assertEqual(evidence["code"], "WF01_REPORT_DUPLICATE_CANDIDATE_ID")

    def test_manifest_is_versioned_and_disables_downstream_reinterpretation(self) -> None:
        manifest = wf01_candidate_contract_manifest()

        self.assertEqual(
            manifest["contract_version"], WF01_CANDIDATE_CONTRACT_VERSION
        )
        self.assertEqual(
            manifest["downstream_business_reinterpretation"], "DISABLED"
        )


if __name__ == "__main__":
    unittest.main()

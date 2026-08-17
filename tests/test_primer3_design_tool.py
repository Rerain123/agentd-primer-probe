from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.config import load_config
from agentd.errors import InputValidationError
from agentd.models import Candidate, Oligo
from agentd.primer3_design_tool import Primer3DesignTool


def candidate() -> Candidate:
    return Candidate(
        candidate_id="sequence_only-p3-1",
        forward_primer=Oligo("ACGTACGTACGTACGTACGT", 20, 60.0, 50.0),
        reverse_primer=Oligo("TGCATGCATGCATGCATGCA", 20, 60.0, 50.0),
        probe=Oligo("CGTACGTACGTACGTACGTACG", 22, 69.0, 54.5),
        amplicon_sequence_5to3="ACGT" * 25,
        amplicon_start=0,
        amplicon_end=99,
        primer3_pair_penalty=0.5,
    )


class FakePrimer3Adapter:
    def __init__(self, config, *, allow_fallback=False) -> None:
        self.config = copy.deepcopy(config)
        self.allow_fallback = allow_fallback

    def design(self, template, *, sequence_id, parameters, target_region=None):
        self.call = {
            "template": template,
            "sequence_id": sequence_id,
            "parameters": copy.deepcopy(parameters),
        }
        return [candidate()]

    def status(self):
        return {"tool": "P1_Primer3", "available": True, "version": "test"}


class Primer3DesignToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tool = Primer3DesignTool(load_config())

    def test_sequence_only_returns_candidates_and_not_evaluated_specificity(self) -> None:
        with patch(
            "agentd.primer3_design_tool.Primer3Adapter",
            FakePrimer3Adapter,
        ):
            result = self.tool.run(
                {"sequence": "ACGT" * 30, "sequence_id": "sequence_only"}
            )

        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["specificity"]["status"], "NOT_EVALUATED")
        self.assertEqual(
            result["candidates"][0]["forward_primer"]["sequence_5to3"],
            "ACGTACGTACGTACGTACGT",
        )

    def test_79_nt_is_rejected_before_primer3(self) -> None:
        with self.assertRaises(InputValidationError) as caught:
            self.tool.run({"sequence": "A" * 79})
        self.assertEqual(caught.exception.details["minimum_length_nt"], 80)

    def test_ambiguous_or_illegal_sequence_is_rejected(self) -> None:
        with self.assertRaises(InputValidationError) as caught:
            self.tool.run({"sequence": "A" * 79 + "N"})
        self.assertEqual(caught.exception.details["invalid_bases"], ["N"])

    def test_invalid_parameter_range_is_rejected_before_primer3(self) -> None:
        with self.assertRaises(InputValidationError) as caught:
            self.tool.run(
                {
                    "sequence": "ACGT" * 30,
                    "parameters": {"primer_min_size": 12},
                }
            )
        self.assertEqual(caught.exception.details["minimum"], 18)

    def test_inverted_parameter_order_is_rejected_before_primer3(self) -> None:
        with self.assertRaises(InputValidationError) as caught:
            self.tool.run(
                {
                    "sequence": "ACGT" * 30,
                    "parameters": {
                        "product_min_bp": 150,
                        "product_max_bp": 100,
                    },
                }
            )
        self.assertEqual(caught.exception.details["field"], "parameters")


if __name__ == "__main__":
    unittest.main()

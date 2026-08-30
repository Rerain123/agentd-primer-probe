from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.alignment import (
    MafftAdapter,
    build_conservation_profile,
    candidate_conservation_evidence,
    find_conserved_regions,
)
from agentd.errors import ToolUnavailableError
from agentd.models import SequenceRecord


ROUNDS = [
    {"minimum_conservation": 0.95, "minimum_length": 150, "label": "standard"},
    {"minimum_conservation": 0.92, "minimum_length": 120, "label": "relaxed_1"},
    {"minimum_conservation": 0.90, "minimum_length": 90, "label": "relaxed_2"},
]


class MafftAdapterTests(unittest.TestCase):
    def test_real_mafft_path_validates_and_preserves_all_records(self) -> None:
        records = [
            SequenceRecord(id="strain-a", sequence="ACGT"),
            SequenceRecord(id="strain-b", sequence="ACTGT"),
        ]
        completed = SimpleNamespace(
            returncode=0,
            stdout=">strain-b\nACTGT\n>strain-a\nAC-GT\n",
            stderr="",
        )
        adapter = MafftAdapter()

        with (
            patch("agentd.alignment.shutil.which", return_value="/usr/bin/mafft"),
            patch("agentd.alignment.subprocess.run", return_value=completed) as run,
        ):
            aligned, method, warnings = adapter.align(records)

        self.assertEqual(method, "mafft_shannon")
        self.assertEqual(warnings, [])
        self.assertEqual([record.id for record in aligned], ["strain-a", "strain-b"])
        self.assertEqual([record.sequence for record in aligned], ["AC-GT", "ACTGT"])
        self.assertEqual(adapter.last_evidence["status"], "COMPLETED")
        self.assertEqual(adapter.last_evidence["alignment_width"], 5)
        self.assertEqual(len(adapter.last_evidence["input_records"]), 2)
        self.assertEqual(len(adapter.last_evidence["aligned_records"]), 2)
        self.assertIn("--auto", run.call_args.args[0])

    def test_invalid_mafft_output_is_not_treated_as_evaluated(self) -> None:
        records = [
            SequenceRecord(id="strain-a", sequence="ACGT"),
            SequenceRecord(id="strain-b", sequence="ACGT"),
        ]
        completed = SimpleNamespace(
            returncode=0,
            stdout=">strain-a\nACGT\n",
            stderr="",
        )
        adapter = MafftAdapter()

        with (
            patch("agentd.alignment.shutil.which", return_value="/usr/bin/mafft"),
            patch("agentd.alignment.subprocess.run", return_value=completed),
        ):
            with self.assertRaises(ToolUnavailableError):
                adapter.align(records)

        self.assertEqual(adapter.last_evidence["status"], "FAILED")


class ConservationAnalysisTests(unittest.TestCase):
    def test_gap_and_internal_insertion_reduce_binding_site_evidence(self) -> None:
        aligned = [
            SequenceRecord(id="reference", sequence="AC-GT"),
            SequenceRecord(id="with-insertion", sequence="ACTGT"),
        ]
        profile = build_conservation_profile(aligned)
        region = {
            "start": 0,
            "end": 3,
            "length": 4,
            "label": "standard",
            "selection_status": "SELECTED_CONSERVED_REGION",
            "selection_threshold": 0.95,
            "conservation_score": 0.9,
            "confidence": "LOW",
            "low_confidence_reasons": [
                "REPRESENTATIVE_SEQUENCE_COUNT_BELOW_20"
            ],
        }

        evidence = candidate_conservation_evidence(
            profile,
            intervals={"forward_primer": (0, 3), "reverse_primer": None, "probe": None},
            region=region,
            status="EVALUATED",
            method="mafft_shannon",
        )

        self.assertEqual(evidence["status"], "EVALUATED")
        self.assertEqual(evidence["coverage_percent"], 50.0)
        self.assertEqual(evidence["score"], 0.9)
        site = evidence["binding_sites"]["forward_primer"]
        self.assertEqual(site["exact_match_sequence_count"], 1)
        self.assertEqual(site["internal_insertion_column_count"], 1)
        self.assertEqual(site["maximum_internal_insertion_base_count"], 1)
        self.assertLess(site["mean_identity_percent"], 100.0)

    def test_progressive_rounds_and_full_length_fallback_are_explicit(self) -> None:
        strict_records = [
            SequenceRecord(id=f"s{index:02d}", sequence="A" * 160)
            for index in range(20)
        ]
        strict, method = find_conserved_regions(strict_records, ROUNDS)
        self.assertEqual(method, "mafft_shannon")
        self.assertEqual(strict[0]["selection_round"], 1)
        self.assertEqual(strict[0]["length"], 160)
        self.assertEqual(strict[0]["confidence"], "HIGH")

        relaxed_records = [
            *[
                SequenceRecord(id=f"a{index:02d}", sequence="A" * 160)
                for index in range(19)
            ],
            SequenceRecord(id="variant", sequence="C" * 160),
        ]
        relaxed, _method = find_conserved_regions(relaxed_records, ROUNDS)
        self.assertEqual(relaxed[0]["selection_round"], 2)
        self.assertEqual(relaxed[0]["label"], "relaxed_1")
        self.assertIn(
            "RELAXED_CONSERVATION_THRESHOLD",
            relaxed[0]["low_confidence_reasons"],
        )

        fallback_records = [
            *[
                SequenceRecord(id=f"fa{index:02d}", sequence="A" * 160)
                for index in range(10)
            ],
            *[
                SequenceRecord(id=f"fc{index:02d}", sequence="C" * 160)
                for index in range(10)
            ],
        ]
        fallback, _method = find_conserved_regions(fallback_records, ROUNDS)
        self.assertTrue(fallback[0]["full_length_fallback"])
        self.assertEqual(
            fallback[0]["selection_status"],
            "NO_QUALIFYING_CONSERVED_REGION",
        )
        self.assertEqual(fallback[0]["confidence"], "LOW")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.config import load_config
from agentd.degenerate import assess_degenerate_strategy, expand_iupac
from agentd.mgb import MGBProbeEvaluator, calc_mgb_correction
from agentd.models import SequenceRecord
from agentd.primer3_tool import Primer3Adapter
from agentd.specificity import BlastAdapter
from agentd.structure import ViennaRNAAdapter


class PhaseTwoToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.primer3 = Primer3Adapter(self.config, allow_fallback=True)

    def test_mgb_correction_and_rules(self) -> None:
        self.assertGreater(calc_mgb_correction("ATATATATATATATA"), calc_mgb_correction("GCGCGCGCGCGCGCG"))
        result = MGBProbeEvaluator(self.primer3, self.config).assess("ACCTATACGACGTTGCA")
        self.assertTrue(result["all_hard_pass"])
        self.assertTrue(65 <= result["corrected_tm_celsius"] <= 72)
        self.assertEqual(result["model"], "public_evidence_piecewise_context_approximation")

    def test_mgb_fam_second_g_is_hard_failure(self) -> None:
        result = MGBProbeEvaluator(self.primer3, self.config).assess("AGCTATACGACGTTGCA", fluorophore="FAM")
        self.assertFalse(result["rules_check"]["R-MGB-01"])
        self.assertEqual(result["verdict"], "FAIL")

    def test_viennarna_or_explicit_unavailable_status(self) -> None:
        adapter = ViennaRNAAdapter(self.config)
        status = adapter.status()
        self.assertIn("available", status)
        if adapter.available:
            result = adapter.assess("ACGT" * 25)
            self.assertIn(result["status"], {"PASS", "CAUTION", "WARNING"})
            self.assertEqual(result["parameter_set"], "SantaLucia2004_DNA")
            self.assertEqual(len(result["mfe_structure"]), 100)
            full = adapter.assess_full("ACGT" * 25)
            self.assertIn(full["status"], {"PASS", "CAUTION", "WARNING"})
            self.assertEqual(full["parameter_set"], "SantaLucia2004_DNA")
            self.assertLessEqual(len(full["subopt_structures"]), 10)
            self.assertIn("most_stable_dg", full)
            self.assertEqual(full["temperature_celsius"], 60.0)
            self.assertEqual(full["salt_na_mm"], 50.0)
        else:
            self.assertEqual(adapter.not_evaluated("missing")["status"], "not_evaluated")

    def test_degenerate_enumeration_and_tm_spread(self) -> None:
        payload = {
            "forward_primer": "GCGAGGAAGATGTACGGATA",
            "reverse_primer": "GCTACAGGCTCCCTTCTAA",
            "probe": "ATCGCGGTTAACCTAGTCCCTGTGC",
            "template": (
                "TGGGCGAACTTGGTCACCCCGAAGTATCTGATGAGATGATCACCGAGAGCCGGGGC"
                "GAGGAAGATGTACGGATACTTTCCGCACAGGGACTAGGTTAACCGCGATTTCTTATCC"
                "TGCGATAGCCGGCCGTGTAAACCTTTCTTAGGCATGGCAGAAAATGCAATCATATAACG"
                "GGGTTAGAAGGGAGCCTGTAGCATGCTGCCCGATTTCCCGTGTACCCCTGTCGCTGCGA"
                "AGTATATCCAGAGGTGCCGGTGCTAGCC"
            ),
        }
        template = payload["template"]
        candidate = self.primer3.validate_pair(
            payload["forward_primer"],
            payload["reverse_primer"],
            probe=payload["probe"],
            template=template,
        )
        start = candidate.forward_primer.position_start
        self.assertIsNotNone(start)
        first_position = int(start) + 3
        second_position = int(start) + 8

        def mutate(position: int, replacement: str) -> str:
            return template[:position] + replacement + template[position + 1 :]

        first_alt = "A" if template[first_position] != "A" else "C"
        second_alt = "T" if template[second_position] != "T" else "G"
        aligned = [SequenceRecord(id="ref", sequence=template)]
        aligned.extend(SequenceRecord(id=f"a{i}", sequence=mutate(first_position, first_alt)) for i in range(2))
        aligned.extend(SequenceRecord(id=f"b{i}", sequence=mutate(second_position, second_alt)) for i in range(2))
        strategy = assess_degenerate_strategy(
            aligned,
            candidate,
            self.primer3,
            BlastAdapter(allow_fallback=True),
            self.config,
            relaxation_round_inherited=0,
            target_db=None,
            host_db=None,
            cross_db=None,
        )
        self.assertIsNotNone(strategy)
        self.assertTrue(strategy["triggered"])
        self.assertEqual(len(strategy["variant_positions"]), 2)
        self.assertEqual(strategy["combinations_tested"], 4)
        self.assertIn(strategy["feasibility"], {"FEASIBLE", "MARGINAL", "INFEASIBLE"})
        self.assertEqual(len(expand_iupac("AR")), 2)


if __name__ == "__main__":
    unittest.main()

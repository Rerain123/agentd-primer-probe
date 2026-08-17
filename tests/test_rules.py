from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.config import load_config
from agentd.primer3_tool import Primer3Adapter
from agentd.rules import RuleEngine


class RuleEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.adapter = Primer3Adapter(self.config, allow_fallback=True)

    def test_all_rules_are_emitted(self) -> None:
        template = (ROOT / "testdata" / "target.fasta").read_text(encoding="utf-8").splitlines()[1]
        candidate = self.adapter.validate_pair(
            "AACTTGGTCACCCCGAAGTA",
            "CGGAAAGTATCCGTACATCT",
            probe="AGATGATCACCGAGAGCCGGG",
            template=template,
        )
        candidate.rules = RuleEngine().evaluate(candidate)
        codes = {rule.code for rule in candidate.rules}
        expected = {
            "R-01", "R-01b", "R-02", "R-03", "R-03b", "R-04", "R-05", "R-06",
            "R-07", "R-08", "R-09", "R-10", "R-11", "R-12", "R-13", "R-SUPPLEMENT",
            "R-14", "R-15", "R-16", "R-17", "R-18", "R-19", "R-20", "R-21",
        }
        self.assertEqual(codes, expected)

    def test_probe_starting_with_g_fails_r05(self) -> None:
        template = (ROOT / "testdata" / "target.fasta").read_text(encoding="utf-8").splitlines()[1]
        candidate = self.adapter.validate_pair(
            "AACTTGGTCACCCCGAAGTA",
            "CGGAAAGTATCCGTACATCT",
            probe="GATGATCACCGAGAGCCGGG",
            template=template,
        )
        r05 = next(rule for rule in RuleEngine().evaluate(candidate) if rule.code == "R-05")
        self.assertEqual(r05.status.value, "FAIL")


if __name__ == "__main__":
    unittest.main()


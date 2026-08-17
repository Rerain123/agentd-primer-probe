from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.errors import InputValidationError
from agentd.sequence import find_amplicon, gc_percent, normalize_sequence, reverse_complement


class SequenceTests(unittest.TestCase):
    def test_normalize_and_reverse_complement(self) -> None:
        self.assertEqual(normalize_sequence(" acgu\n"), "ACGT")
        self.assertEqual(reverse_complement("ACGTAA"), "TTACGT")
        self.assertEqual(gc_percent("GGAA"), 50.0)

    def test_invalid_base_is_rejected(self) -> None:
        with self.assertRaises(InputValidationError):
            normalize_sequence("ACGTZ")

    def test_find_amplicon(self) -> None:
        template = "TTTAAACCCGGGTTTAAACCCGGG"
        forward = "AAACCC"
        reverse = reverse_complement("AAACCC")
        located = find_amplicon(template, forward, reverse)
        self.assertIsNotNone(located)
        self.assertGreaterEqual(len(located[2]), 12)


if __name__ == "__main__":
    unittest.main()


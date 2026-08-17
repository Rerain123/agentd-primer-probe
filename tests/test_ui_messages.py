from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.ui_messages import build_primer_design_input


class PrimerDesignInputFileNameTests(unittest.TestCase):
    @staticmethod
    def _file_name(payload: dict) -> str:
        return build_primer_design_input(payload)["data"]["sourceFile"]["fileName"]

    def test_single_file_preserves_uploaded_display_name(self) -> None:
        self.assertEqual(
            self._file_name(
                {
                    "uploaded_file_names": [
                        "CSFV_NC_002657.1_5UTR_1-373.fasta"
                    ],
                    "sourceFile": {"fileName": "uploaded.fasta"},
                }
            ),
            "CSFV_NC_002657.1_5UTR_1-373.fasta",
        )

    def test_multiple_files_use_fixed_label(self) -> None:
        for field in ("uploaded_file_names", "sequence_files", "attachments"):
            with self.subTest(field=field):
                self.assertEqual(
                    self._file_name(
                        {
                            field: [
                                {"fileName": "first.fasta"},
                                {"fileName": "second.fasta"},
                            ]
                        }
                    ),
                    "多个文件",
                )

    def test_common_single_attachment_aliases_are_supported(self) -> None:
        self.assertEqual(
            self._file_name(
                {
                    "attachments": [
                        {"originalName": "上传的真实名称.fasta"}
                    ]
                }
            ),
            "上传的真实名称.fasta",
        )
        self.assertEqual(
            self._file_name({"file_name": "C:\\fakepath\\sample.fasta"}),
            "sample.fasta",
        )


if __name__ == "__main__":
    unittest.main()

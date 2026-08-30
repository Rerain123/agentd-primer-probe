from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.config import load_config
from agentd.database import DatabaseCatalog
from agentd.entrez_sequences import EntrezSequenceError, NCBIEntrezProvider
from agentd.errors import ToolUnavailableError
from agentd.internal_control import InternalControlDesigner
from agentd.models import SequenceRecord


def record(identifier: str, base: str) -> SequenceRecord:
    return SequenceRecord(
        id=identifier,
        sequence=(base * 400)[:320],
        source="NCBI Entrez nucleotide",
    )


class NCBIEntrezProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.provider = NCBIEntrezProvider(self.config)

    def test_fetch_prepends_refseq_and_deduplicates_homologs(self) -> None:
        reference = record("NM_001444420.1", "ATGC")
        homologs = [
            record("duplicate", "ATGC"),
            record("homolog_1", "AAGC"),
            record("homolog_2", "ATCC"),
            record("homolog_3", "ATGG"),
            record("homolog_4", "ACGC"),
        ]
        with patch.object(
            self.provider,
            "_fetch_fasta_records",
            side_effect=[[reference], homologs],
        ), patch.object(
            self.provider,
            "_search_ids",
            return_value=["1", "2", "3", "4", "5"],
        ):
            records, source = self.provider.fetch_housekeeping_records(
                host_species="pig",
                gene="ACTB",
                refseq_accession="NM_001444420.1",
                minimum_records=5,
            )

        self.assertEqual(records[0].id, "NM_001444420.1")
        self.assertEqual(len(records), 5)
        self.assertEqual(source["method"], "ncbi_entrez_live")
        self.assertEqual(source["refseq_accession"], "NM_001444420.1")
        self.assertEqual(source["accessions"], [item.id for item in records])

    def test_fetch_rejects_insufficient_unique_sequences(self) -> None:
        reference = record("NM_001444420.1", "ATGC")
        with patch.object(
            self.provider,
            "_fetch_fasta_records",
            side_effect=[[reference], [reference]],
        ), patch.object(self.provider, "_search_ids", return_value=["1"]):
            with self.assertRaisesRegex(EntrezSequenceError, "至少需要5条"):
                self.provider.fetch_housekeeping_records(
                    host_species="pig",
                    gene="ACTB",
                    refseq_accession="NM_001444420.1",
                    minimum_records=5,
                )

    def test_p5_uses_entrez_when_no_request_or_catalog_sequences_exist(self) -> None:
        designer = InternalControlDesigner(self.config, DatabaseCatalog(self.config))
        records = [record(f"actb_{index}", base) for index, base in enumerate(
            ("ATGC", "AAGC", "ATCC", "ATGG", "ACGC"), start=1
        )]
        source = {
            "method": "ncbi_entrez_live",
            "refseq_accession": "NM_001444420.1",
            "sequence_count": 5,
        }
        with patch.object(
            designer.entrez,
            "fetch_housekeeping_records",
            return_value=(records, source),
        ) as fetch, patch(
            "agentd.internal_control.MafftAdapter.align",
            return_value=(records, "mafft", []),
        ), patch(
            "agentd.internal_control._alignment_conservation",
            return_value=1.0,
        ), patch(
            "agentd.internal_control.Primer3Adapter.design",
            side_effect=ToolUnavailableError("fixture stop after sequence acquisition"),
        ):
            result = designer.design(
                {
                    "host_species": "pig",
                    "preferred_genes": ["ACTB"],
                    "allow_fallback": True,
                }
            )

        fetch.assert_called_once_with(
            host_species="pig",
            gene="ACTB",
            refseq_accession="NM_001444420.1",
            minimum_records=5,
        )
        self.assertEqual(result["status"], "TOOL_UNAVAILABLE")
        self.assertEqual(
            result["gene_attempts"][0]["data_source"]["method"],
            "ncbi_entrez_live",
        )


if __name__ == "__main__":
    unittest.main()

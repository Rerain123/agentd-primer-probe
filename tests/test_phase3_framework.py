from __future__ import annotations

import json
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.config import load_config
from agentd.database import DatabaseCatalog
from agentd.errors import InputValidationError
from agentd.multiplex import MultiplexEvaluator, channel_order
from agentd.primer3_tool import Primer3Adapter
from agentd.service import AgentDService


def wf02_request() -> dict:
    sequence = (
        "TGGGCGAACTTGGTCACCCCGAAGTATCTGATGAGATGATCACCGAGAGCCGGGGCGAGGAAG"
        "ATGTACGGATACTTTCCGCACAGGGACTAGGTTAACCGCGATTTCTTATCCTGCGATAGCCGGC"
        "CGTGTAAACCTTTCTTAGGCATGGCAGAAAATGCAATCATATAACGGGGTTAGAAGGGAGCCTGT"
        "AGCATGCTGCCCGATTTCCCGTGTACCCCTGTCGCTGCGAAGTATATCCAGAGGTGCCGGTGCTA"
        "GCC"
    )
    return {
        "assay_id": "fixture_multiplex_v1",
        "host_species": "pig",
        "instrument": "CFX96",
        "include_internal_control": False,
        "allow_fallback": True,
        "targets": [
            {
                "target_id": "fixture_A",
                "target_species": "Synthetic virus A",
                "gene": "gene_A",
                "sequence": sequence,
            },
            {
                "target_id": "fixture_B",
                "target_species": "Synthetic virus B",
                "gene": "gene_B",
                "sequence": sequence,
            },
        ],
    }


class PhaseThreeFrameworkTests(unittest.TestCase):
    def test_database_catalog_enriches_logical_resources(self) -> None:
        config = load_config(overrides={"databases": {"catalog_path": str(ROOT / "config" / "database-catalog.example.json")}})
        catalog = DatabaseCatalog(config)
        enriched = catalog.enrich_payload("WF-01", {"target_id": "fixture_target", "host_species": "pig"})
        self.assertTrue(Path(enriched["fasta_path"]).exists())
        self.assertTrue(Path(enriched["target_db"]).exists())
        self.assertTrue(Path(enriched["host_db"]).exists())
        self.assertTrue(catalog.status()["configured"])

    def test_database_catalog_normalizes_host_selector_aliases(self) -> None:
        config = load_config(
            overrides={
                "databases": {
                    "catalog_path": str(
                        ROOT / "config" / "database-catalog.example.json"
                    )
                }
            }
        )
        catalog = DatabaseCatalog(config)

        for selector in ("pig", "PIG", "猪", "猪 (pig)", "pig（猪）"):
            with self.subTest(selector=selector):
                enriched = catalog.enrich_payload(
                    "WF-04",
                    {"host_species": selector},
                )
                self.assertTrue(Path(enriched["host_db"]).exists())
                resolution = enriched["_database_resolution"][0]
                self.assertEqual(resolution["key"], "host:pig:blast")
                self.assertIn(
                    resolution["selector_match_method"],
                    {"canonical", "embedded_canonical", "configured_alias"},
                )

        unresolved = catalog.enrich_payload(
            "WF-04",
            {"host_species": "unconfigured-selector"},
        )
        self.assertNotIn("host_db", unresolved)

    def test_database_catalog_recognizes_blast_index_prefix(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-blast-prefix-") as temp_dir:
            temp_path = Path(temp_dir)
            prefix = temp_path / "host_db"
            Path(f"{prefix}.nin").write_bytes(b"fixture")
            catalog_path = temp_path / "catalog.json"
            catalog_path.write_text(
                json.dumps(
                    {
                        "resources": {
                            "host:pig:blast": {
                                "kind": "blast_database",
                                "path": str(prefix),
                                "version": "fixture-1",
                                "required": True,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            catalog = DatabaseCatalog(load_config(), catalog_path=catalog_path)
            self.assertEqual(catalog.resolve_path("host:pig:blast"), str(prefix.resolve()))
            self.assertEqual(catalog.status()["required_missing"], [])

    def test_database_catalog_rejects_placeholder_version_for_readiness(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-catalog-version-") as temp_dir:
            temp_path = Path(temp_dir)
            fasta = temp_path / "internal.fasta"
            fasta.write_text(">one\n" + "ATGC" * 20 + "\n", encoding="utf-8")
            catalog_path = temp_path / "catalog.json"
            catalog_path.write_text(
                json.dumps(
                    {
                        "resources": {
                            "target:fixture:sequences": {
                                "kind": "fasta",
                                "path": str(fasta),
                                "version": "SET_ON_SERVER",
                                "required": True,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            status = DatabaseCatalog(load_config(), catalog_path=catalog_path).status()
        self.assertEqual(status["required_missing"], ["target:fixture:sequences"])
        self.assertTrue(status["resources"][0]["path_available"])
        self.assertFalse(status["resources"][0]["version_valid"])

    def test_readiness_reports_remote_background_substitution_explicitly(self) -> None:
        class HealthyResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with tempfile.TemporaryDirectory(prefix="agentd-ready-remote-") as temp_dir:
            root = Path(temp_dir)
            catalog_path = root / "catalog.json"
            catalog_path.write_text(
                json.dumps(
                    {
                        "resources": {
                            "cross:default:blast": {
                                "kind": "blast_database",
                                "path": str(root / "missing" / "db"),
                                "version": "SET_ON_SERVER",
                                "required": True,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                "os.environ",
                {
                    "AGENTD_DATABASE_CATALOG": str(catalog_path),
                    "AGENTD_REQUIRE_DATABASES": "true",
                    "BLAST_SEARCH_SERVICE_URL": "http://host.docker.internal:8090",
                },
                clear=False,
            ):
                with patch("agentd.service.urlopen", return_value=HealthyResponse()):
                    readiness = AgentDService(state_dir=root / "state").readiness()

        remote = readiness["database"]["remote_background"]
        self.assertEqual(
            remote["satisfies_required_resources"],
            ["cross:default:blast"],
        )
        self.assertTrue(remote["health_verified"])
        self.assertEqual(readiness["database"]["required_missing"], [])

    def test_p4_detects_duplicate_fluorophore(self) -> None:
        config = load_config()
        evaluator = MultiplexEvaluator(Primer3Adapter(config, allow_fallback=True), config)
        base = {
            "fwd_seq": "GCGAGGAAGATGTACGGATA",
            "rev_seq": "GCTACAGGCTCCCTTCTAA",
            "probe_seq": "ATCGCGGTTAACCTAGTCCCTGTGC",
            "fwd_tm": 60.0,
            "rev_tm": 60.1,
            "probe_tm": 69.0,
            "gc_percent": 48.0,
            "quencher": "BHQ-1",
        }
        report = evaluator.check_panel(
            [{**base, "target_id": "A", "fluorophore": "FAM"}, {**base, "target_id": "B", "fluorophore": "FAM"}],
            instrument="CFX96",
        )
        self.assertEqual(report["verdict"], "INCOMPATIBLE")
        self.assertEqual(report["spectral_check"]["status"], "FAIL")

    def test_p4_cross_dimer_boundaries_are_exclusive(self) -> None:
        config = load_config()
        primer3 = Mock()
        base = {
            "fwd_seq": "GCGAGGAAGATGTACGGATA",
            "rev_seq": "GCTACAGGCTCCCTTCTAA",
            "probe_seq": "ATCGCGGTTAACCTAGTCCCTGTGC",
            "fwd_tm": 60.0,
            "rev_tm": 60.1,
            "probe_tm": 69.0,
            "gc_percent": 48.0,
        }
        panel = [
            {
                **base,
                "target_id": "A",
                "fluorophore": "FAM",
                "quencher": "BHQ-1",
            },
            {
                **base,
                "target_id": "B",
                "fluorophore": "Cy5",
                "quencher": "BHQ-2",
            },
        ]
        evaluator = MultiplexEvaluator(primer3, config)

        primer3.calc_heterodimer.return_value = {"dg_kcal": -6.0}
        standard = evaluator.check_panel(panel, instrument="CFX96")
        self.assertEqual(standard["verdict"], "MARGINAL")
        self.assertTrue(
            any("-6.0" in issue for issue in standard["issues"])
        )

        primer3.calc_heterodimer.return_value = {"dg_kcal": -7.0}
        relaxed = evaluator.check_panel(
            panel,
            instrument="CFX96",
            relaxed=True,
        )
        self.assertEqual(relaxed["verdict"], "INCOMPATIBLE")
        self.assertTrue(
            any(issue.startswith("[FAIL]") for issue in relaxed["issues"])
        )

    def test_channel_allocation_reserves_internal_control_and_rox(self) -> None:
        self.assertEqual(len(channel_order("CFX96", rox_as_reference=False, include_internal=True)), 4)
        self.assertEqual(len(channel_order("QuantStudio 5", rox_as_reference=True, include_internal=True)), 3)

    def test_retired_wf06_wf07_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-phase3-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            for workflow in ("WF-06", "WF06", "WF-07", "WF07"):
                with self.subTest(workflow=workflow):
                    with self.assertRaises(InputValidationError) as raised:
                        service.run(workflow, {}, persist=False)
                    self.assertEqual(
                        raised.exception.details["supported"],
                        ["WF-01", "WF-02", "WF-03", "WF-04", "WF-05"],
                    )

    def test_direct_wf02_route_uses_only_v01_task_service(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-wf02-v01-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            output = service.run(
                "WF-02",
                wf02_request(),
                persist=False,
            )
            self.assertFalse(
                (Path(state_dir) / "wf02-v01-batches").exists()
            )
        self.assertEqual(output["workflow"], "WF-02")
        self.assertEqual(output["schema_version"], "wf02-v0.1-batch-v1")
        self.assertEqual(output["stage"], "N05")
        self.assertEqual(output["status"], "READY_FOR_TARGET_DESIGN")
        self.assertNotIn("multiplex", output)

    def test_readiness_does_not_require_server_databases_locally(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-ready-") as state_dir:
            readiness = AgentDService(state_dir=state_dir).readiness()
        self.assertEqual(readiness["status"], "ready")
        self.assertFalse(readiness["databases_required"])

    def test_production_readiness_requires_scientific_tools_and_databases(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-production-ready-") as state_dir:
            readiness = AgentDService(
                state_dir=state_dir,
                config_overrides={
                    "runtime": {
                        "profile": "server-production",
                        "require_databases_for_readiness": True,
                    }
                },
            ).readiness()
        self.assertEqual(readiness["status"], "not_ready")
        self.assertTrue(readiness["databases_required"])
        self.assertIn("C5", readiness["required_tools"])
        self.assertIn("C6", readiness["required_tools"])

    def test_p5_requires_five_homolog_sequences(self) -> None:
        payload = {
            "host_species": "pig",
            "preferred_genes": ["ACTB"],
            "gene_sequences": {"ACTB": ["ATGC" * 80]},
            "allow_fallback": True,
        }
        with tempfile.TemporaryDirectory(prefix="agentd-p5-minimum-") as state_dir:
            output = AgentDService(state_dir=state_dir).run("WF-03", payload, persist=False)
        self.assertEqual(output["status"], "FAILED")
        self.assertEqual(output["internal_control"]["gene_attempts"][0]["status"], "INSUFFICIENT_SEQUENCES")


if __name__ == "__main__":
    unittest.main()

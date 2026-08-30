from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.config import load_config
from agentd.database import DatabaseCatalog
from agentd.errors import ToolUnavailableError
from agentd.remote_blast import NCBIBlastMCPProvider
from agentd.specificity import (
    P2_POLICY_STATUS,
    P2BatchSearchProvider,
    P2SpecificityBatchService,
    SpecificityEvaluator,
)
from agentd.service import AgentDService
from agentd.wf01_v02_m4 import (
    WF01V02M4Engine,
    WF01V02SpecificityResourceResolver,
)


def candidate(index: int, *, penalty: float = 0.0) -> dict:
    suffix = "A" * index
    forward = f"GACCACTTTGTCAAGCTCAT{suffix}"
    reverse = f"TCTCTTCCTCTTGTGCTCTT{suffix}"
    probe = f"ACATGGCCTCCAAGGAGTAAGACCC{suffix}"
    amplicon = ("ACGT" * 35)[: 100 + index]

    def oligo(sequence: str, tm: float) -> dict:
        return {
            "sequence_5to3": sequence,
            "length_nt": len(sequence),
            "tm_celsius": tm,
            "gc_percent": 50.0,
            "hairpin_dg_kcal": -1.0,
            "homodimer_dg_kcal": -2.0,
            "three_prime_dimer_dg_kcal": -2.0,
            "thermo_method": "primer3_santalucia_1998",
        }

    return {
        "candidate_id": f"target_001-p3-{index + 1}",
        "dedupe_key": f"dedupe-{index:03d}",
        "forward_primer": oligo(forward, 60.0),
        "reverse_primer": oligo(reverse, 60.0),
        "probe": oligo(probe, 69.0),
        "amplicon": {
            "sequence_5to3": amplicon,
            "length_bp": len(amplicon),
            "start": 0,
            "end": len(amplicon) - 1,
        },
        "primer3_pair_penalty": penalty,
        "pair_heterodimer_dg_kcal": -5.0,
        "three_prime_heterodimer_dg_kcal": -5.0,
        "three_prime_involved": False,
        "design_method": "primer3",
        "evidence_level": "in_silico_only",
        "verdict": "AWAITING_SPECIFICITY",
        "score": None,
        "rank": None,
        "rules": [
            {
                "code": "R19",
                "category": "specificity",
                "status": "NOT_EVALUATED",
            }
        ],
        "metadata": {},
    }


def object_(
    role: str,
    ordinal: int,
    *,
    name: str,
    accession: str | None = None,
) -> dict:
    return {
        "object_id": f"target_001:{role.lower()}:{ordinal}",
        "role": role,
        "original_input": {"name": name, "accession": accession},
        "identifier_type": "ACCESSION" if accession else "NAME",
        "identifier": accession or name,
        "normalized_name": name,
        "scientific_name": name if " " in name else None,
        "taxid": None,
        "accession": accession,
        "resolution_status": "NORMALIZED",
    }


def m4_target(
    candidates: list[dict],
    *,
    include_related: bool = True,
    primer3_complete: bool = True,
) -> dict:
    return {
        "target_id": "target_001",
        "status": "EXPLORING",
        "target_object": object_(
            "TARGET",
            1,
            name="Synthetic test virus",
            accession="TARGET_ACC",
        ),
        "host_objects": [object_("HOST", 1, name="Sus scrofa")],
        "related_objects": (
            [object_("RELATED", 1, name="Related test virus")]
            if include_related
            else []
        ),
        "background_objects": [],
        "m3_execution": {
            "stage": "M3_COMPLETE_AWAITING_M4",
            "primer3_search_complete": primer3_complete,
            "all_hard_rule_pass_candidates": copy.deepcopy(candidates),
            "all_hard_rule_rejected_candidates": [],
            "all_hard_rule_incomplete_candidates": [],
        },
    }


def resource(key: str, role: str, *, ready: bool = True) -> dict:
    return {
        "key": key,
        "kind": "blast_database",
        "path": f"C:/fixture/{key.replace(':', '_')}",
        "uri": None,
        "version": "fixture-2026.07" if ready else None,
        "version_valid": ready,
        "exists": ready,
        "scope": {"role": role},
        "resolution_status": "RESOLVED" if ready else "NOT_RESOLVED",
        "resolution_reason": None if ready else "DATABASE_VERSION_MISSING",
    }


class _SuccessfulBlastAdapter:
    def status(self) -> dict:
        return {"available": True, "method": "blastn"}

    def search(self, queries, database, *, evalue, mode):
        return [], "blastn"


class _CapturingBlastAdapter:
    def __init__(self) -> None:
        self.database_path: Path | None = None
        self.database_content: str | None = None
        self.last_search_evidence: dict = {}

    def status(self) -> dict:
        return {"available": True, "method": "blastn"}

    def search(self, queries, database, *, evalue, mode):
        path = Path(database)
        self.database_path = path
        self.database_content = path.read_text(encoding="utf-8")
        self.last_search_evidence = {
            "status": "COMPLETED",
            "database": str(path),
            "mode": mode,
        }
        return [], "blastn"


class _UnavailablePrimer3Adapter:
    available = False

    def status(self) -> dict:
        return {"available": False}


class P2BatchSearchProviderTests(unittest.TestCase):
    def test_fallback_raw_evidence_uses_current_resource_path(self) -> None:
        provider = P2BatchSearchProvider(
            {},
            blast_adapter=_SuccessfulBlastAdapter(),
            primer3_adapter=_UnavailablePrimer3Adapter(),
        )
        database_resource = resource("host:pig:blast", "HOST")

        result = provider.search(
            {"c0000_forward": "ACGT"},
            database_resource,
            mode="HOST_EXCLUSION",
            evalue=0.1,
        )

        evidence = result["raw_tool_output"]
        self.assertEqual(evidence["database"], database_resource["path"])
        self.assertEqual(evidence["mode"], "HOST_EXCLUSION")
        self.assertEqual(evidence["evalue"], 0.1)
        self.assertEqual(evidence["raw_output"], [])

    def test_inline_resource_is_materialized_only_for_the_search_call(self) -> None:
        blast = _CapturingBlastAdapter()
        provider = P2BatchSearchProvider(
            {},
            blast_adapter=blast,
            primer3_adapter=_UnavailablePrimer3Adapter(),
        )
        sequence = "ACGT" * 20
        database_resource = {
            "key": "target:target_001:input-snapshot",
            "kind": "inline_fasta",
            "path": None,
            "version": "sha256:fixture",
            "version_valid": True,
            "exists": True,
            "resolution_status": "RESOLVED",
            "record_count": 1,
            "records": [
                {
                    "id": "input_0001",
                    "sequence": sequence,
                    "checksum_sha256": hashlib.sha256(
                        sequence.encode("ascii")
                    ).hexdigest(),
                }
            ],
        }

        result = provider.search(
            {"c0001_forward": "ACGT"},
            database_resource,
            mode="TARGET_HIT",
            evalue=1000.0,
        )

        self.assertIn(sequence, blast.database_content or "")
        self.assertIsNotNone(blast.database_path)
        self.assertFalse(blast.database_path.exists())
        self.assertEqual(
            result["raw_tool_output"]["database"],
            database_resource["key"],
        )
        self.assertEqual(
            result["raw_tool_output"]["materialization"],
            "TASK_LOCAL_TEMPORARY_FASTA",
        )


class NCBIBlastMCPProviderTests(unittest.TestCase):
    def test_normalizes_remote_hit_and_restores_batch_query_id(self) -> None:
        queries = {
            "c0001_forward": "GACCACTTTGTCAAGCTCAT",
            "c0001_reverse": "TCTCTTCCTCTTGTGCTCTT",
        }
        raw_hit = {
            "subject_id": "NC_999999.1",
            "subject_accession": "NC_999999.1",
            "subject_desc": "Related test virus isolate X",
            "score": 40,
            "evalue": 1e-20,
            "identity_pct": 95.0,
            "align_length": 20,
            "gaps": 0,
            "query_start": 1,
            "query_end": 20,
            "subject_start": 500,
            "subject_end": 519,
            "alignment": {
                "query_seq": "GACCACTTTGTCAAGCTCAT",
                "match_line": "||||||||||||||||||| ",
                "subject_seq": "GACCACTTTGTCAAGCTCAA",
            },
            "hsps": [{"bit_score": 38.2}],
        }

        normalized = NCBIBlastMCPProvider.normalize_hits([raw_hit], queries)[0]

        self.assertEqual(normalized["query_id"], "c0001_forward")
        self.assertEqual(normalized["accession"], "NC_999999.1")
        self.assertEqual(normalized["query_coverage_pct"], 100.0)
        self.assertEqual(normalized["mismatches"], 1)
        self.assertEqual(normalized["three_prime_mismatches"], 1)
        self.assertEqual(normalized["bitscore"], 38.2)
        self.assertEqual(normalized["strand"], "plus")

    def test_identical_queries_share_the_same_remote_hit(self) -> None:
        raw_hit = {
            "query_start": 1,
            "query_end": 4,
            "alignment": {"query_seq": "ACGT", "subject_seq": "ACGT"},
        }
        normalized = NCBIBlastMCPProvider.normalize_hits(
            [raw_hit],
            {"first": "ACGT", "second": "ACGT"},
        )
        self.assertEqual(
            {item["query_id"] for item in normalized},
            {"first", "second"},
        )


class FakeResolver:
    def __init__(
        self,
        *,
        target_ready: bool = True,
        host_ready: bool = True,
        related_ready: bool = True,
    ) -> None:
        self.target_ready = target_ready
        self.host_ready = host_ready
        self.related_ready = related_ready

    def resolve_target(self, target: dict) -> dict:
        return resource("target:fixture:blast", "TARGET", ready=self.target_ready)

    def resolve_host(self, object_value: dict) -> dict:
        return resource("host:pig:blast", "HOST", ready=self.host_ready)

    def resolve_related_background(
        self,
        objects: list[dict],
        *,
        target_object: dict | None = None,
    ) -> dict:
        return resource(
            "nt:default:blast",
            "RELATED_BACKGROUND",
            ready=self.related_ready,
        )


def hit(
    query_id: str,
    *,
    accession: str,
    identity: float = 100.0,
    coverage: float = 100.0,
    evalue: float = 1e-20,
    start: int = 100,
    dg: float | None = -10.0,
    scientific_name: str | None = None,
    strand: str = "plus",
) -> dict:
    return {
        "query_id": query_id,
        "accession": accession,
        "identity_pct": identity,
        "alignment_length": 20,
        "query_coverage_pct": coverage,
        "mismatches": 0,
        "gap_opens": 0,
        "query_start": 1,
        "query_end": 20,
        "subject_start": start,
        "subject_end": start + 19 if strand in {"+", "plus"} else start - 19,
        "evalue": evalue,
        "bitscore": 40.0,
        "hit_sequence": "ACGTACGTACGTACGTACGT",
        "strand": strand,
        "title": f"fixture [{scientific_name}]" if scientific_name else "fixture",
        "scientific_name": scientific_name,
        "three_prime_mismatches": 0,
        "thermodynamics": None if dg is None else {"dg_kcal": dg},
    }


class FakeProvider:
    def __init__(
        self,
        *,
        method: str = "blastn",
        target_hit_evalue: float = 1e-20,
        missing_target_probe: bool = False,
        host_fail_first: bool = False,
        host_missing_thermo: bool = False,
        related_caution_first: bool = False,
        error_mode: str | None = None,
    ) -> None:
        self.method = method
        self.target_hit_evalue = target_hit_evalue
        self.missing_target_probe = missing_target_probe
        self.host_fail_first = host_fail_first
        self.host_missing_thermo = host_missing_thermo
        self.related_caution_first = related_caution_first
        self.error_mode = error_mode
        self.calls: list[dict] = []

    @staticmethod
    def status() -> dict:
        return {
            "blast": {"available": True, "version": "fixture"},
            "primer3": {"available": True, "version": "fixture"},
        }

    def search(
        self,
        queries: dict[str, str],
        resource_value: dict,
        *,
        mode: str,
        evalue: float,
    ) -> dict:
        self.calls.append(
            {
                "mode": mode,
                "query_count": len(queries),
                "resource_key": resource_value["key"],
                "evalue": evalue,
            }
        )
        if self.error_mode == mode:
            raise ToolUnavailableError("fixture search failure")
        hits: list[dict] = []
        if mode == "TARGET_HIT":
            for query_id in queries:
                if self.missing_target_probe and query_id == "c0001_probe":
                    continue
                hits.append(
                    hit(
                        query_id,
                        accession="TARGET_ACC",
                        evalue=self.target_hit_evalue,
                    )
                )
        elif mode == "HOST_EXCLUSION":
            if self.host_fail_first:
                hits.extend(
                    [
                        hit(
                            "c0001_forward",
                            accession="HOST_ACC",
                            start=100,
                            dg=-7.0,
                        ),
                        hit(
                            "c0001_reverse",
                            accession="HOST_ACC",
                            start=200,
                            dg=-7.0,
                            strand="minus",
                        ),
                    ]
                )
            elif self.host_missing_thermo:
                hits.append(
                    hit(
                        "c0001_forward",
                        accession="HOST_ACC",
                        dg=None,
                    )
                )
        elif mode == "RELATED_BACKGROUND_NT" and self.related_caution_first:
            hits.append(
                hit(
                    "c0001_forward",
                    accession="RELATED_ACC",
                    identity=99.0,
                    dg=-5.0,
                    scientific_name="Related test virus",
                )
            )
        return {"hits": hits, "method": self.method, "tool": self.status()}


class FakeRemoteProvider:
    def __init__(
        self,
        *,
        configured: bool = True,
        error: bool = False,
    ) -> None:
        self.configured = configured
        self.error = error
        self.calls: list[dict] = []
        self.last_search_evidence: dict = {}

    def status(self) -> dict:
        return {
            "tool": "REMOTE_BACKGROUND_FIXTURE",
            "configured": self.configured,
        }

    @staticmethod
    def resource(*, scope: dict) -> dict:
        return {
            "key": "nt:public:remote-mcp",
            "kind": "remote_blast_service",
            "path": None,
            "uri": "http://blast.example/sse",
            "version": "NCBI_NT_PUBLIC_LIVE",
            "version_valid": True,
            "exists": None,
            "scope": copy.deepcopy(scope),
            "resolution_status": "RESOLVED",
            "resolution_reason": None,
            "version_semantics": "LIVE_PUBLIC_DATABASE_NOT_A_FIXED_SNAPSHOT",
        }

    def search(
        self,
        queries: dict,
        *,
        evalue: float,
        exclude_organisms=None,
    ) -> dict:
        self.calls.append(
            {
                "queries": copy.deepcopy(queries),
                "evalue": evalue,
                "exclude_organisms": copy.deepcopy(exclude_organisms),
            }
        )
        if self.error:
            self.last_search_evidence = {"status": "FAILED"}
            raise ToolUnavailableError("remote fixture failure")
        return {
            "hits": [],
            "method": "blastn",
            "provider": "remote_fixture",
            "raw_tool_output": {"status": "COMPLETED"},
        }


class WF01V02M4ResourceTests(unittest.TestCase):
    def test_ac_spe_02_resolver_requires_existing_versioned_database(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-m4-catalog-") as temp_dir:
            root = Path(temp_dir)
            target_fasta = root / "target.fasta"
            target_fasta.write_text(">target\nACGTACGT\n", encoding="utf-8")
            catalog_path = root / "catalog.json"
            catalog_path.write_text(
                json.dumps(
                    {
                        "resources": {
                            "target:TARGET_ACC:blast": {
                                "kind": "blast_database",
                                "path": "target.fasta",
                                "version": "target-v1",
                            },
                            "host:Sus scrofa:blast": {
                                "kind": "blast_database",
                                "path": "target.fasta",
                                "version": "SET_ON_SERVER",
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            catalog = DatabaseCatalog(load_config(), catalog_path=catalog_path)
            resolver = WF01V02SpecificityResourceResolver(catalog)
            target = m4_target([candidate(0)])
            target_resource = resolver.resolve_target(target)
            host_resource = resolver.resolve_host(target["host_objects"][0])
            self.assertEqual(target_resource["resolution_status"], "RESOLVED")
            self.assertEqual(target_resource["version"], "target-v1")
            self.assertEqual(host_resource["resolution_status"], "NOT_RESOLVED")
            self.assertEqual(
                host_resource["resolution_reason"],
                "DATABASE_VERSION_MISSING",
            )

    def test_target_input_snapshot_resolves_without_catalog_database(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-empty-catalog-") as temp_dir:
            catalog_path = Path(temp_dir) / "catalog.json"
            catalog_path.write_text('{"resources": {}}', encoding="utf-8")
            resolver = WF01V02SpecificityResourceResolver(
                DatabaseCatalog(load_config(), catalog_path=catalog_path)
            )
            target = m4_target([candidate(0)])
            sequence = "ACGT" * 40
            target["template"] = {
                "template_id": "target_001:template",
                "record_name": "fixture",
                "sequence": sequence,
            }
            target["templates"] = [copy.deepcopy(target["template"])]

            resolved = resolver.resolve_target(target)

        self.assertEqual(resolved["resolution_status"], "RESOLVED")
        self.assertEqual(resolved["kind"], "inline_fasta")
        self.assertEqual(resolved["record_count"], 1)
        self.assertRegex(resolved["version"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(resolved["records"][0]["sequence"], sequence)


class WF01V02M4SpecificityTests(unittest.TestCase):
    def engine(
        self,
        provider: FakeProvider,
        *,
        resolver: FakeResolver | None = None,
        remote_provider: FakeRemoteProvider | None = None,
    ) -> WF01V02M4Engine:
        return WF01V02M4Engine(
            DatabaseCatalog(load_config()),
            load_config(),
            resolver=resolver or FakeResolver(),
            provider=provider,
            remote_provider=remote_provider,
        )

    def test_ac_spe_01_and_02_three_modes_keep_complete_evidence(self) -> None:
        provider = FakeProvider()
        result = self.engine(provider).run_target(m4_target([candidate(0)]))
        selected = result["top10"][0]
        specificity = selected["specificity"]

        self.assertEqual([call["mode"] for call in provider.calls], [
            "TARGET_HIT",
            "HOST_EXCLUSION",
            "RELATED_BACKGROUND_NT",
        ])
        self.assertEqual(specificity["overall_status"], "PASS")
        self.assertEqual(specificity["target_hit"]["status"], "PASS")
        self.assertEqual(specificity["host_exclusion"]["status"], "PASS")
        self.assertEqual(specificity["related_background_nt"]["status"], "PASS")
        self.assertEqual(
            specificity["target_hit"]["objects"][0]["original_input"]["accession"],
            "TARGET_ACC",
        )
        self.assertEqual(
            specificity["target_hit"]["database"]["version"],
            "fixture-2026.07",
        )
        self.assertEqual(
            specificity["target_hit"]["thresholds"]["search_evalue"],
            1000.0,
        )
        self.assertFalse(
            specificity["target_hit"]["thresholds"]["decision_uses_evalue"]
        )
        target_hit = specificity["target_hit"]["hits"][0]
        self.assertEqual(target_hit["subject_start"], 100)
        self.assertEqual(target_hit["subject_end"], 119)
        self.assertEqual(selected["evidence_level"], "blast_verified")
        self.assertIsNotNone(selected["score"])
        self.assertEqual(selected["rank"], 1)
        self.assertEqual(
            next(rule for rule in selected["rules"] if rule["code"] == "R19")[
                "status"
            ],
            "PASS",
        )
        self.assertEqual(
            next(rule for rule in selected["rules"] if rule["code"] == "R20")[
                "actual"
            ]["evidence_level"],
            "blast_verified",
        )
        self.assertFalse(
            selected["metadata"]["wf01_v02_rule_summary"][
                "awaiting_specificity"
            ]
        )
        self.assertTrue(result["specificity_complete"])
        self.assertTrue(result["technical_search_complete"])
        self.assertFalse(result["search_complete"])
        self.assertEqual(result["policy_status"], P2_POLICY_STATUS)
        self.assertEqual(result["specificity_tool"], "P2")
        raw_searches = result["raw_search_outputs"]
        self.assertEqual(
            raw_searches["target"]["result"]["raw_tool_output"]["mode"],
            "TARGET_HIT",
        )
        self.assertEqual(
            raw_searches["hosts"][0]["result"]["raw_tool_output"]["mode"],
            "HOST_EXCLUSION",
        )
        self.assertEqual(
            raw_searches["related_background"]["result"]["raw_tool_output"][
                "mode"
            ],
            "RELATED_BACKGROUND_NT",
        )
        self.assertTrue(
            raw_searches["target"]["result"]["raw_tool_output"]["raw_output"]
        )

    def test_ac_spe_01_searches_all_candidates_in_one_batch_per_mode(self) -> None:
        provider = FakeProvider()
        result = self.engine(provider).run_target(
            m4_target([candidate(0), candidate(1)])
        )
        self.assertEqual(len(provider.calls), 3)
        self.assertTrue(all(call["query_count"] == 6 for call in provider.calls))
        self.assertEqual(result["candidate_counts"]["specificity_qualified"], 2)

    def test_full_specificity_calls_p2_once_and_maps_its_result(self) -> None:
        provider = FakeProvider()
        p2_service = P2SpecificityBatchService(
            load_config(),
            provider=provider,
        )
        engine = WF01V02M4Engine(
            DatabaseCatalog(load_config()),
            load_config(),
            resolver=FakeResolver(),
            p2_service=p2_service,
        )

        with patch.object(
            p2_service,
            "evaluate",
            wraps=p2_service.evaluate,
        ) as evaluate:
            result = engine.run_target(
                m4_target([candidate(0), candidate(1)])
            )

        evaluate.assert_called_once()
        self.assertEqual(result["specificity_tool"], "P2")
        self.assertEqual(
            result["ranking_basis"],
            "M4_MAPPING_OF_P2_SPECIFICITY_SCORE",
        )

    def test_exact_short_target_hit_is_not_rejected_by_database_size_evalue(self) -> None:
        provider = FakeProvider(target_hit_evalue=1.14e-5)

        result = self.engine(provider).run_target(m4_target([candidate(0)]))

        target_call = next(
            call for call in provider.calls if call["mode"] == "TARGET_HIT"
        )
        self.assertEqual(target_call["evalue"], 1000.0)
        self.assertEqual(result["candidate_counts"]["specificity_qualified"], 1)
        self.assertEqual(result["top10"][0]["specificity"]["target_hit"]["status"], "PASS")

    def test_ac_spe_03_unresolved_database_never_passes_or_scores(self) -> None:
        provider = FakeProvider()
        result = self.engine(
            provider,
            resolver=FakeResolver(target_ready=False),
        ).run_target(m4_target([candidate(0)]))
        incomplete = result["all_specificity_incomplete_candidates"][0]
        self.assertEqual(incomplete["specificity"]["overall_status"], "NOT_EVALUATED")
        self.assertEqual(
            incomplete["specificity"]["target_hit"]["reason"],
            "TARGET_DATABASE_OR_VERSION_NOT_RESOLVED",
        )
        self.assertEqual(incomplete["evidence_level"], "in_silico_only")
        self.assertIsNone(incomplete["score"])
        self.assertIsNone(incomplete["rank"])
        self.assertFalse(result["specificity_complete"])
        self.assertFalse(result["technical_search_complete"])
        self.assertFalse(result["top10"])
        self.assertNotIn("TARGET_HIT", [call["mode"] for call in provider.calls])

    def test_unresolved_local_background_stays_not_evaluated(self) -> None:
        provider = FakeProvider()
        remote = FakeRemoteProvider(configured=False)

        result = self.engine(
            provider,
            resolver=FakeResolver(related_ready=False),
            remote_provider=remote,
        ).run_target(m4_target([candidate(0)]))

        selected = result["all_specificity_incomplete_candidates"][0]
        self.assertEqual(
            selected["specificity"]["overall_status"],
            "NOT_EVALUATED",
        )
        self.assertEqual(
            selected["specificity"]["related_background_nt"]["reason"],
            "NT_DATABASE_OR_VERSION_NOT_RESOLVED",
        )
        self.assertIsNone(selected["score"])
        self.assertNotIn(
            "RELATED_BACKGROUND_NT",
            [call["mode"] for call in provider.calls],
        )
        remote_status = result["remote_background_provider_status"]
        self.assertEqual(remote_status["status"], "NOT_CONFIGURED")
        self.assertEqual(
            remote_status["reason"],
            "LOCAL_RELATED_BACKGROUND_NOT_RESOLVED",
        )
        self.assertFalse(remote.calls)

    def test_unresolved_local_background_uses_remote_provider(self) -> None:
        local = FakeProvider()
        remote = FakeRemoteProvider()

        result = self.engine(
            local,
            resolver=FakeResolver(related_ready=False),
            remote_provider=remote,
        ).run_target(m4_target([candidate(0)]))

        selected = result["top10"][0]
        self.assertEqual(selected["specificity"]["overall_status"], "PASS")
        self.assertEqual(
            selected["specificity"]["related_background_nt"]["status"],
            "PASS",
        )
        self.assertEqual(
            result["resource_resolution"]["related_background"][
                "resource_key"
            ],
            "nt:public:remote-mcp",
        )
        self.assertEqual(
            result["remote_background_provider_status"]["status"],
            "USED",
        )
        self.assertEqual(len(remote.calls), 1)
        self.assertEqual(
            remote.calls[0]["exclude_organisms"],
            ["Synthetic test virus"],
        )
        self.assertNotIn(
            "RELATED_BACKGROUND_NT",
            [call["mode"] for call in local.calls],
        )

    def test_remote_background_error_remains_not_evaluated(self) -> None:
        result = self.engine(
            FakeProvider(),
            resolver=FakeResolver(related_ready=False),
            remote_provider=FakeRemoteProvider(error=True),
        ).run_target(m4_target([candidate(0)]))

        selected = result["all_specificity_incomplete_candidates"][0]
        self.assertEqual(selected["specificity"]["overall_status"], "NOT_EVALUATED")
        self.assertEqual(
            selected["specificity"]["related_background_nt"]["reason"],
            "NT_SEARCH_TOOL_ERROR",
        )
        self.assertEqual(
            result["remote_background_provider_status"]["status"],
            "ERROR",
        )
        self.assertIsNone(selected["score"])

    def test_resolved_local_background_is_preferred_over_remote(self) -> None:
        local = FakeProvider()
        remote = FakeRemoteProvider()

        result = self.engine(
            local,
            remote_provider=remote,
        ).run_target(m4_target([candidate(0)]))

        self.assertEqual(result["top10"][0]["specificity"]["overall_status"], "PASS")
        self.assertFalse(remote.calls)
        self.assertEqual(
            result["remote_background_provider_status"],
            {
                "status": "NOT_INITIALIZED",
                "reason": "LOCAL_RELATED_BACKGROUND_USED",
            },
        )

    def test_sequence_only_marks_required_specificity_not_evaluated(self) -> None:
        provider = FakeProvider()
        target = m4_target([candidate(0)])
        target["target_object"] = None
        target["host_objects"] = []
        target["related_objects"] = []
        target["background_objects"] = []
        target["specificity_missing_fields"] = ["target_organism"]

        result = self.engine(provider).run_target(target)

        selected = result["all_specificity_incomplete_candidates"][0]
        self.assertEqual(selected["specificity"]["overall_status"], "NOT_EVALUATED")
        self.assertEqual(
            selected["specificity"]["target_hit"]["reason"],
            "TARGET_OBJECT_MISSING",
        )
        self.assertEqual(
            selected["specificity"]["host_exclusion"]["status"],
            "NOT_APPLICABLE",
        )
        self.assertEqual(
            selected["specificity"]["host_exclusion"]["reason"],
            "HOST_NOT_PROVIDED_OPTIONAL_CHECK_SKIPPED",
        )
        self.assertEqual(
            selected["metadata"]["specificity_follow_up"]["missing_fields"],
            ["target_organism"],
        )
        self.assertFalse(provider.calls)
        self.assertIsNone(result["resource_resolution"]["target"])

    def test_target_without_host_runs_required_full_specificity_modes(self) -> None:
        provider = FakeProvider()
        target = m4_target([candidate(0)])
        target["execution_path"] = "FULL_SPECIFICITY"
        target["host_objects"] = []

        result = self.engine(provider).run_target(target)

        self.assertEqual(
            [call["mode"] for call in provider.calls],
            ["TARGET_HIT", "RELATED_BACKGROUND_NT"],
        )
        selected = result["top10"][0]
        specificity = selected["specificity"]
        self.assertEqual(specificity["overall_status"], "PASS")
        self.assertFalse(specificity["host_exclusion_requested"])
        self.assertEqual(
            specificity["host_exclusion"]["status"],
            "NOT_APPLICABLE",
        )
        self.assertEqual(selected["evidence_level"], "blast_verified")
        self.assertTrue(result["specificity_complete"])
        self.assertTrue(result["technical_search_complete"])

    def test_design_only_path_is_authoritative_and_skips_specificity_tools(self) -> None:
        target = m4_target([candidate(0)])
        target["execution_path"] = "DESIGN_ONLY"

        with patch(
            "agentd.wf01_v02_m4.P2SpecificityBatchService",
            side_effect=AssertionError("DESIGN_ONLY must not initialize P2"),
        ):
            result = WF01V02M4Engine(
                DatabaseCatalog(load_config()),
                load_config(),
                resolver=FakeResolver(),
            ).run_target(target)

        self.assertEqual(
            result["specificity_execution_status"],
            "SKIPPED_MISSING_CONTEXT",
        )
        self.assertEqual(result["ranking_status"], "FINAL_DESIGN_ONLY")
        self.assertEqual(result["ranking_basis"], "M4_DESIGN_SCORE")
        self.assertTrue(result["top10_is_final_under_current_policy"])
        self.assertEqual(result["provider_status"]["status"], "NOT_INITIALIZED")
        selected = result["all_specificity_incomplete_candidates"][0]
        self.assertEqual(
            selected["specificity"]["overall_status"],
            "NOT_EVALUATED",
        )
        self.assertIsNotNone(selected["score"])
        self.assertEqual(selected["rank"], 1)
        self.assertEqual(result["top10"][0]["candidate_id"], selected["candidate_id"])
        scoring = selected["metadata"]["wf01_v02_scoring"]
        self.assertEqual(scoring["status"], "FINAL_DESIGN_ONLY")
        self.assertEqual(scoring["score_type"], "DESIGN_SCORE")
        self.assertEqual(scoring["score_label"], "设计评分")
        self.assertEqual(
            scoring["score_scope"],
            ["thermodynamics", "amplicon_features"],
        )
        self.assertEqual(scoring["effective_weights"]["specificity"], 0.0)
        self.assertAlmostEqual(
            sum(scoring["effective_weights"].values()),
            1.0,
        )

    def test_ac_spe_03_tool_error_and_development_fallback_are_not_pass(self) -> None:
        tool_error = self.engine(
            FakeProvider(error_mode="TARGET_HIT")
        ).run_target(m4_target([candidate(0)]))
        self.assertEqual(
            tool_error["all_specificity_incomplete_candidates"][0][
                "specificity"
            ]["target_hit"]["reason"],
            "TARGET_SEARCH_TOOL_ERROR",
        )

        fallback = self.engine(
            FakeProvider(method="exact_fasta_development_fallback")
        ).run_target(m4_target([candidate(0)]))
        selected = fallback["all_specificity_incomplete_candidates"][0]
        self.assertEqual(selected["specificity"]["overall_status"], "NOT_EVALUATED")
        self.assertEqual(selected["evidence_level"], "in_silico_only")
        self.assertIsNone(selected["score"])

    def test_host_compatible_amplicon_fails_candidate(self) -> None:
        result = self.engine(
            FakeProvider(host_fail_first=True)
        ).run_target(m4_target([candidate(0), candidate(1)]))
        self.assertEqual(result["candidate_counts"]["specificity_failed"], 1)
        rejected = result["all_specificity_failed_candidates"][0]
        self.assertEqual(rejected["verdict"], "REJECTED_SPECIFICITY")
        self.assertEqual(
            rejected["specificity"]["host_exclusion"]["status"],
            "FAIL",
        )
        self.assertEqual(result["top10"][0]["candidate_id"], "target_001-p3-2")

    def test_host_hit_without_thermodynamics_is_p2_caution(self) -> None:
        result = self.engine(
            FakeProvider(host_missing_thermo=True)
        ).run_target(m4_target([candidate(0)]))
        selected = result["all_qualified_candidates"][0]
        host = selected["specificity"]["host_exclusion"]["object_results"][0]
        self.assertEqual(host["status"], "CAUTION")
        self.assertEqual(selected["specificity"]["overall_status"], "CAUTION")
        self.assertEqual(len(host["hits"]), 1)

    def test_final_specificity_score_reranks_m3_candidates(self) -> None:
        provider = FakeProvider(related_caution_first=True)
        result = self.engine(provider).run_target(
            m4_target([candidate(0, penalty=0.0), candidate(1, penalty=5.0)])
        )
        first, second = result["all_qualified_candidates"]
        self.assertEqual(first["candidate_id"], "target_001-p3-2")
        self.assertEqual(first["specificity"]["overall_status"], "PASS")
        self.assertEqual(second["specificity"]["overall_status"], "CAUTION")
        self.assertGreater(first["score"], second["score"])
        self.assertEqual([first["rank"], second["rank"]], [1, 2])
        self.assertEqual(second["verdict"], "PASS_WITH_WARNINGS")
        scoring = first["metadata"]["wf01_v02_scoring"]
        self.assertEqual(scoring["score_type"], "COMPOSITE_SCORE")
        self.assertEqual(scoring["score_label"], "综合评分")
        self.assertEqual(
            scoring["score_scope"],
            ["thermodynamics", "specificity", "amplicon_features"],
        )

    def test_full_specificity_uses_four_dimensions_with_mafft_evidence(self) -> None:
        value = candidate(0)
        value["metadata"]["conservation_evidence"] = {
            "status": "EVALUATED",
            "reason": "MAFFT_ALIGNMENT_AND_SHANNON_PROFILE_COMPLETE",
            "method": "mafft_shannon",
            "score": 0.96,
            "coverage_percent": 100.0,
            "sequence_count": 20,
            "confidence": "HIGH",
            "region": {
                "start": 0,
                "end": 159,
                "length": 160,
                "round": "STRICT",
            },
        }

        result = self.engine(FakeProvider()).run_target(m4_target([value]))

        scoring = result["top10"][0]["metadata"]["wf01_v02_scoring"]
        self.assertEqual(scoring["conservation_scoring_mode"], "FOUR_DIMENSION")
        self.assertEqual(
            scoring["score_scope"],
            [
                "conservation",
                "thermodynamics",
                "specificity",
                "amplicon_features",
            ],
        )
        self.assertEqual(
            scoring["effective_weights"],
            {
                "conservation": 0.3,
                "thermodynamics": 0.25,
                "specificity": 0.25,
                "amplicon_features": 0.2,
            },
        )
        conservation = scoring["dimensions"]["conservation"]
        self.assertEqual(conservation["raw_score"], 96.0)
        self.assertTrue(conservation["participates_in_score"])
        self.assertEqual(
            scoring["dimensions"]["specificity"]["method"],
            "P2_SPECIFICITY_SCORE",
        )

    def test_target_and_host_trigger_broad_nt_background_evaluation(self) -> None:
        provider = FakeProvider()
        result = self.engine(provider).run_target(
            m4_target([candidate(0)], include_related=False)
        )
        selected = result["top10"][0]
        self.assertEqual(
            selected["specificity"]["related_background_nt"]["status"],
            "PASS",
        )
        self.assertIn(
            "RELATED_BACKGROUND_NT",
            [call["mode"] for call in provider.calls],
        )

    def test_target_missing_required_oligo_hit_fails(self) -> None:
        result = self.engine(
            FakeProvider(missing_target_probe=True)
        ).run_target(m4_target([candidate(0)]))
        rejected = result["all_specificity_failed_candidates"][0]
        self.assertEqual(rejected["specificity"]["target_hit"]["status"], "FAIL")
        self.assertFalse(result["top10"])

    def test_p2_related_hits_are_caution_without_m4_pair_policy(self) -> None:
        queries = {
            "forward": "GACCACTTTGTCAAGCTCAT",
            "reverse": "TCTCTTCCTCTTGTGCTCTT",
            "probe": "ACATGGCCTCCAAGGAGTAAGACCC",
        }
        hits = [
            hit("forward", accession="NON_TARGET", start=100),
            hit(
                "reverse",
                accession="NON_TARGET",
                start=200,
                strand="minus",
            ),
        ]
        result = SpecificityEvaluator(
            _SuccessfulBlastAdapter(),
            _UnavailablePrimer3Adapter(),
        ).evaluate_cross_species_hits(
            queries,
            hits,
            method="blastn",
            target_species="Synthetic test virus",
            target_accessions={"TARGET_ACC"},
        )
        self.assertEqual(result.verdict, "CAUTION")
        self.assertEqual(result.blast_hits, 2)

    def test_m4_source_contains_no_specificity_strategy_or_tool_adapter(self) -> None:
        source = (ROOT / "src" / "agentd" / "wf01_v02_m4.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "THRESHOLDS =",
            "class WF01V02SpecificityPolicy",
            "class WF01V02BlastSearchProvider",
            "NCBIBlastMCPProvider",
            "identity_pct",
            "query_coverage_pct",
            "three_prime_mismatches",
            "dg_kcal",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("P2SpecificityBatchService", source)


class WF01V02M4BatchIsolationTests(unittest.TestCase):
    def test_specificity_failure_in_one_target_preserves_other_target_result(
        self,
    ) -> None:
        payload = {
            "sequence_mode": "independent_targets",
            "fasta_text": ">first\n"
            + "ACGT" * 40
            + "\n>second\n"
            + "TGCA" * 40,
            "batch_defaults": {
                "target_organism": {"name": "Synthetic test virus"},
                "hosts": [{"name": "pig"}],
            },
        }
        with tempfile.TemporaryDirectory(prefix="agentd-m4-isolation-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            created = service.create_wf01_v02_batch(payload)
            batch = service.get_wf01_v02_batch(created["batch_id"])
            batch["status"] = "EXPLORING"
            batch["m2_outcome"] = "COMPLETED"
            batch["m3_outcome"] = "COMPLETED_WITH_BLOCKED_TARGETS"

            completed_target = m4_target([candidate(0)], include_related=False)
            completed_target["target_id"] = batch["targets"][0]["target_id"]
            completed_target["target_object"] = batch["targets"][0][
                "target_object"
            ]
            completed_target["host_objects"] = batch["targets"][0][
                "host_objects"
            ]
            blocked_target = copy.deepcopy(batch["targets"][1])
            blocked_target["status"] = "EXPLORING"
            blocked_target["m3_execution"] = {
                "stage": "M3_BLOCKED",
                "primer3_search_complete": False,
            }
            batch["targets"] = [completed_target, blocked_target]
            service.wf01_v02.store.save(batch)

            result = service.execute_wf01_v02_m4(created["batch_id"])
            self.assertEqual(
                result["m4_outcome"],
                "COMPLETED_WITH_BLOCKED_TARGETS",
            )
            self.assertEqual(
                result["targets"][0]["m4_execution"]["stage"],
                "M4_COMPLETE_AWAITING_M5",
            )
            self.assertEqual(
                result["targets"][1]["m4_execution"]["stage"],
                "M4_BLOCKED",
            )
            self.assertEqual(len(result["targets"]), 2)
            self.assertFalse(result["technical_search_complete"])

    def test_unexpected_runtime_error_does_not_strand_batch_in_running(self) -> None:
        payload = {
            "fasta_text": ">target\n" + "ACGT" * 40,
            "batch_defaults": {
                "target_organism": {"name": "Synthetic test virus"},
                "hosts": [{"name": "pig"}],
            },
        }
        with tempfile.TemporaryDirectory(prefix="agentd-m4-recovery-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            created = service.create_wf01_v02_batch(payload)
            batch = service.get_wf01_v02_batch(created["batch_id"])
            batch["status"] = "EXPLORING"
            batch["m2_outcome"] = "COMPLETED"
            batch["m3_outcome"] = "COMPLETED"
            target = m4_target([candidate(0)], include_related=False)
            target["target_object"] = batch["targets"][0]["target_object"]
            target["host_objects"] = batch["targets"][0]["host_objects"]
            batch["targets"] = [target]
            service.wf01_v02.store.save(batch)

            with patch.object(
                WF01V02M4Engine,
                "run_target",
                side_effect=RuntimeError("simulated adapter crash"),
            ):
                result = service.execute_wf01_v02_m4(created["batch_id"])

            self.assertEqual(result["status"], "EXPLORING")
            self.assertEqual(
                result["m4_outcome"],
                "COMPLETED_WITH_BLOCKED_TARGETS",
            )
            execution = result["targets"][0]["m4_execution"]
            self.assertEqual(execution["stage"], "M4_BLOCKED")
            self.assertEqual(
                execution["error"]["code"],
                "M4_UNEXPECTED_RUNTIME_ERROR",
            )
            self.assertEqual(
                result["agent_interactions"][-1]["data"]["status"],
                "WAITING_USER",
            )

    def test_direct_mode_runtime_error_does_not_create_popup_and_can_reach_m5(
        self,
    ) -> None:
        payload = {
            "fasta_text": ">target\n" + "ACGT" * 40,
            "batch_defaults": {
                "target_organism": {"name": "Synthetic test virus"},
                "hosts": [{"name": "pig"}],
            },
        }
        with tempfile.TemporaryDirectory(prefix="agentd-m4-no-popup-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            created = service.create_wf01_v02_batch(
                payload,
                interaction_mode="NO_POPUPS",
            )
            batch = service.get_wf01_v02_batch(created["batch_id"])
            batch["status"] = "EXPLORING"
            batch["m2_outcome"] = "COMPLETED"
            batch["m3_outcome"] = "COMPLETED"
            target = m4_target([candidate(0)], include_related=False)
            target["target_object"] = batch["targets"][0]["target_object"]
            target["host_objects"] = batch["targets"][0]["host_objects"]
            batch["targets"] = [target]
            service.wf01_v02.store.save(batch)

            with patch.object(
                WF01V02M4Engine,
                "run_target",
                side_effect=RuntimeError("simulated adapter crash"),
            ):
                after_m4 = service.execute_wf01_v02_m4(created["batch_id"])

            self.assertFalse(after_m4["agent_interactions"])
            self.assertEqual(
                after_m4["m4_outcome"],
                "COMPLETED_WITH_BLOCKED_TARGETS",
            )
            after_m5 = service.execute_wf01_v02_m5(created["batch_id"])
            self.assertEqual(after_m5["m5_outcome"], "COMPLETED")
            self.assertFalse(after_m5["agent_interactions"])
            self.assertIn(
                after_m5["targets"][0]["status"],
                {"PARTIAL_INCOMPLETE", "FAILED"},
            )


@unittest.skipUnless(
    importlib.util.find_spec("fastapi") is not None,
    "FastAPI extra not installed",
)
class WF01V02M4ApiTests(unittest.TestCase):
    def test_run_m4_endpoint_persists_safe_incomplete_result_without_databases(
        self,
    ) -> None:
        from fastapi.testclient import TestClient
        from agentd import api

        payload = {
            "fasta_text": ">api_target\n" + "ACGT" * 40,
            "batch_defaults": {
                "target_organism": {"name": "Synthetic test virus"},
                "hosts": [{"name": "pig"}],
            },
        }
        with tempfile.TemporaryDirectory(prefix="agentd-m4-api-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            created = service.create_wf01_v02_batch(payload)
            batch = service.get_wf01_v02_batch(created["batch_id"])
            batch["status"] = "EXPLORING"
            batch["m2_outcome"] = "COMPLETED"
            batch["m3_outcome"] = "COMPLETED"
            target = m4_target([candidate(0)], include_related=False)
            target["target_object"] = batch["targets"][0]["target_object"]
            target["host_objects"] = batch["targets"][0]["host_objects"]
            batch["targets"] = [target]
            service.wf01_v02.store.save(batch)

            with patch.object(api, "service", service):
                client = TestClient(api.app)
                response = client.post(
                    f"/v1/wf01/tasks/{created['batch_id']}/run-m4",
                    json={},
                )
                self.assertEqual(response.status_code, 200)
                result = response.json()
                self.assertEqual(result["m4_outcome"], "COMPLETED")
                self.assertFalse(result["specificity_complete"])
                self.assertFalse(result["technical_search_complete"])
                self.assertFalse(result["search_complete"])
                self.assertEqual(
                    result["targets"][0]["m4_execution"]["candidate_counts"][
                        "specificity_not_evaluated"
                    ],
                    1,
                )
                self.assertEqual(
                    result["events"][-1]["event_type"],
                    "M4_EXECUTION_COMPLETED",
                )
                rerun = client.post(
                    f"/v1/wf01/tasks/{created['batch_id']}/run-m4",
                    json={},
                )
                self.assertEqual(rerun.status_code, 422)


if __name__ == "__main__":
    unittest.main()

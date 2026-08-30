from __future__ import annotations

import copy
import inspect
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentd.config import load_config
from agentd.database import DatabaseCatalog
from agentd.errors import InputValidationError
from agentd.internal_control import InternalControlDesigner
from agentd.models import Candidate, EvidenceLevel, Oligo, RuleStatus
from agentd.rules import RuleEngine
from agentd.specificity import P2SpecificityBatchService
from agentd.workflows.wf03 import WF03InternalControl


def resource(key: str, *, ready: bool = True) -> dict:
    return {
        "key": key,
        "kind": "blast_database",
        "path": f"C:/fixture/{key.replace(':', '_')}",
        "version": "fixture-1" if ready else "SET_ON_SERVER",
        "version_valid": ready,
        "exists": ready,
        "scope": {"role": "fixture"},
        "resolution_status": "RESOLVED" if ready else "NOT_RESOLVED",
        "resolution_reason": None if ready else "DATABASE_PATH_UNAVAILABLE",
    }


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    @staticmethod
    def status() -> dict:
        return {
            "blast": {"available": True},
            "primer3": {"available": True},
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
        hits = []
        if mode == "P5_PRIMARY_MATCH":
            hits = [
                {
                    "query_id": query_id,
                    "accession": f"fixture-{query_id}",
                    "identity_pct": 100.0,
                    "query_coverage_pct": 100.0,
                    "evalue": 1e-20,
                }
                for query_id in queries
            ]
        return {"hits": hits, "method": "blastn", "tool": self.status()}


class FakeRemoteProvider:
    def __init__(self, *, configured: bool = True) -> None:
        self.configured = configured
        self.calls: list[dict] = []

    def status(self) -> dict:
        return {"tool": "REMOTE_FIXTURE", "configured": self.configured}

    @staticmethod
    def resource(*, scope: dict) -> dict:
        return {
            "key": "remote:fixture",
            "kind": "remote_service",
            "uri": "http://fixture.invalid",
            "version": "fixture-live",
            "version_valid": True,
            "exists": None,
            "scope": copy.deepcopy(scope),
            "resolution_status": "RESOLVED",
            "resolution_reason": None,
        }

    def search(
        self,
        queries: dict[str, str],
        *,
        evalue: float,
        exclude_organisms: list[str],
    ) -> dict:
        self.calls.append(
            {
                "query_count": len(queries),
                "evalue": evalue,
                "exclude_organisms": list(exclude_organisms),
            }
        )
        return {"hits": [], "method": "blastn"}


def p2_inputs() -> dict:
    queries = {
        "p5_c0001_forward": "ACGTACGTACGTACGTACGA",
        "p5_c0001_reverse": "TCGTACGTACGTACGTACGT",
        "p5_c0001_probe": "CACGTACGTACGTACGTACG",
    }
    return {
        "queries": queries,
        "query_maps": [
            {
                "forward": "p5_c0001_forward",
                "reverse": "p5_c0001_reverse",
                "probe": "p5_c0001_probe",
            }
        ],
        "primary_object": {
            "object_id": "p5:fixture",
            "identifier": "fixture",
            "scientific_name": "Fixture object",
        },
        "primary_resource": resource("host:fixture:blast"),
        "related_background_objects": [],
        "related_resource": resource("cross:default:blast"),
    }


def candidate(index: int = 1) -> Candidate:
    def oligo(sequence: str, start: int, tm: float = 60.0) -> Oligo:
        return Oligo(
            sequence_5to3=sequence,
            length_nt=len(sequence),
            tm_celsius=tm,
            gc_percent=50.0,
            position_start=start,
            position_end=start + len(sequence) - 1,
            hairpin_dg_kcal=-1.0,
            homodimer_dg_kcal=-2.0,
            thermo_method="P1",
        )

    return Candidate(
        candidate_id=f"P5-{index}",
        forward_primer=oligo("ACGTACGTACGTACGTACGA", 0),
        reverse_primer=oligo("TCGTACGTACGTACGTACGT", 80),
        probe=oligo("CACGTACGTACGTACGTACG", 35, 69.0),
        amplicon_sequence_5to3="ACGT" * 25,
        amplicon_start=0,
        amplicon_end=99,
        conservation_score=0.99,
        conservation_sequence_count=5,
        metadata={"role": "internal_control"},
    )


def test_p5_profile_batches_and_maps_each_candidate() -> None:
    provider = FakeProvider()
    service = P2SpecificityBatchService(load_config(), provider=provider)

    result = service.evaluate_p5_batch(**p2_inputs())

    assert result["overall_status"] == "PASS"
    assert [call["mode"] for call in provider.calls] == [
        "P5_PRIMARY_MATCH",
        "RELATED_BACKGROUND_NT",
    ]
    selected = result["candidate_results"][0]
    assert selected["specificity"]["host_verdict"] == "PASS"
    assert selected["specificity"]["cross_species_verdict"] == "PASS"
    assert selected["evidence_level"] == "blast_verified"

    item = candidate()
    InternalControlDesigner._apply_p2_candidate_results([item], result)
    item.rules = RuleEngine().evaluate(item)
    r19 = next(rule for rule in item.rules if rule.code == "R-19")
    assert r19.status == RuleStatus.PASS
    assert item.evidence_level == EvidenceLevel.BLAST_VERIFIED
    assert item.to_dict()["specificity"]["overall_status"] == "PASS"


def test_p5_design_calls_p2_and_serializes_per_candidate_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config(
        overrides={
            "databases": {
                "catalog_path": str(
                    ROOT / "config" / "database-catalog.example.json"
                )
            }
        }
    )
    provider = FakeProvider()
    designer = InternalControlDesigner(
        config,
        DatabaseCatalog(config),
        p2_service=P2SpecificityBatchService(config, provider=provider),
    )
    monkeypatch.setattr(
        "agentd.internal_control.MafftAdapter.align",
        lambda _self, records: (records, "mafft", []),
    )
    monkeypatch.setattr(
        "agentd.internal_control._alignment_conservation",
        lambda _records: 1.0,
    )
    monkeypatch.setattr(
        "agentd.internal_control.Primer3Adapter.design",
        lambda *_args, **_kwargs: [candidate()],
    )
    sequences = [
        {"id": f"fixture-{index}", "sequence": "ACGT" * 100}
        for index in range(5)
    ]

    result = designer.design(
        {
            "host_species": "pig",
            "preferred_genes": ["ACTB"],
            "gene_sequences": {"ACTB": sequences},
        }
    )

    assert result["status"] == "COMPLETED", result
    assert result["specificity_check"]["verdict"] == "PASS"
    assert result["candidates"][0]["specificity"]["overall_status"] == "PASS"
    assert [call["mode"] for call in provider.calls] == [
        "P5_PRIMARY_MATCH",
        "RELATED_BACKGROUND_NT",
    ]


def test_wf03_consumes_real_p5_p2_candidate_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    p2_service = P2SpecificityBatchService(config, provider=FakeProvider())
    monkeypatch.setattr(
        InternalControlDesigner,
        "_get_p2_service",
        lambda _self, *, allow_fallback: p2_service,
    )
    monkeypatch.setattr(
        "agentd.internal_control.MafftAdapter.align",
        lambda _self, records: (records, "mafft", []),
    )
    monkeypatch.setattr(
        "agentd.internal_control._alignment_conservation",
        lambda _records: 1.0,
    )
    monkeypatch.setattr(
        "agentd.internal_control.Primer3Adapter.design",
        lambda *_args, **_kwargs: [candidate(index) for index in range(1, 4)],
    )
    sequences = [
        {"id": f"fixture-{index}", "sequence": "ACGT" * 100}
        for index in range(5)
    ]

    result = WF03InternalControl(config, catalog).run(
        {
            "host_species": "pig",
            "preferred_genes": ["ACTB"],
            "gene_sequences": {"ACTB": sequences},
        }
    )

    assert result.status.value == "SUCCEEDED"
    assert result.evidence_level == EvidenceLevel.BLAST_VERIFIED
    assert len(result.candidates) == 3
    assert all(
        item.to_dict()["specificity"]["overall_status"] == "PASS"
        for item in result.candidates
    )


def test_p5_profile_uses_remote_when_local_related_resource_is_unresolved() -> None:
    provider = FakeProvider()
    remote = FakeRemoteProvider()
    service = P2SpecificityBatchService(
        load_config(),
        provider=provider,
        remote_background_provider=remote,
    )
    inputs = p2_inputs()
    inputs["related_resource"] = resource("cross:default:blast", ready=False)

    result = service.evaluate_p5_batch(**inputs)

    assert result["overall_status"] == "PASS"
    assert result["remote_background_provider_status"]["status"] == "USED"
    assert len(remote.calls) == 1
    assert "RELATED_BACKGROUND_NT" not in {
        call["mode"] for call in provider.calls
    }


def test_p5_profile_keeps_missing_resource_not_evaluated() -> None:
    service = P2SpecificityBatchService(
        load_config(),
        provider=FakeProvider(),
        remote_background_provider=FakeRemoteProvider(configured=False),
    )
    inputs = p2_inputs()
    inputs["related_resource"] = resource("cross:default:blast", ready=False)

    result = service.evaluate_p5_batch(**inputs)

    selected = result["candidate_results"][0]
    assert result["overall_status"] == "NOT_EVALUATED"
    assert selected["specificity"]["cross_species_verdict"] == "NOT_EVALUATED"
    assert selected["evidence_level"] == "in_silico_only"


def test_versioned_resolution_uses_later_ready_fallback(
    tmp_path: Path,
) -> None:
    ready = tmp_path / "ready.fasta"
    ready.write_text(">fixture\nACGTACGT\n", encoding="utf-8")
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "resources": {
                    "nt:default:blast": {
                        "kind": "blast_database",
                        "path": "SET_ON_SERVER",
                        "version": "SET_ON_SERVER",
                    },
                    "cross:default:blast": {
                        "kind": "blast_database",
                        "path": str(ready),
                        "version": "fixture-1",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    catalog = DatabaseCatalog(load_config(), catalog_path=catalog_path)

    selected = catalog.resolve_versioned(
        "nt:default:blast",
        "cross:default:blast",
        scope={"role": "fixture"},
    )

    assert selected is not None
    assert selected["key"] == "cross:default:blast"
    assert selected["resolution_status"] == "RESOLVED"


def test_p5_rejects_mismatched_p2_candidate_count() -> None:
    with pytest.raises(InputValidationError):
        InternalControlDesigner._apply_p2_candidate_results(
            [candidate()],
            {"candidate_results": []},
        )


def test_p5_source_uses_p2_service_not_low_level_adapter() -> None:
    source = inspect.getsource(InternalControlDesigner)
    assert "P2SpecificityBatchService" in source
    assert "BlastAdapter" not in source

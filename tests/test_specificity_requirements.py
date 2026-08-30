from __future__ import annotations

import copy
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.models import EvidenceLevel
from agentd.specificity import BlastAdapter, P2_THRESHOLDS, SpecificityEvaluator
from agentd.workflows.common import evidence_from_specificity


class _StaticBlast:
    def __init__(self, hits_by_mode: dict[str, list[dict]], *, method: str = "blastn") -> None:
        self.hits_by_mode = hits_by_mode
        self.method = method
        self.calls: list[dict] = []

    def search(self, queries, database, *, evalue, mode):
        self.calls.append({"evalue": evalue, "mode": mode})
        return copy.deepcopy(self.hits_by_mode.get(mode, [])), self.method


class _StaticPrimer3:
    available = True


class _RequirementEvaluator(SpecificityEvaluator):
    """Use precomputed P1 values to exercise only the documented P2 table."""

    def _add_thermodynamics(self, hits: list[dict], queries: dict[str, str]) -> None:
        return None


def _hit(
    query_id: str,
    *,
    identity: float = 100.0,
    coverage: float = 100.0,
    evalue: float = 1e-20,
    dg: float = -10.0,
    three_prime_mismatches: int = 0,
    accession: str = "subject-1",
    start: int = 100,
    end: int = 119,
    title: str = "",
) -> dict:
    return {
        "query_id": query_id,
        "identity_pct": identity,
        "query_coverage_pct": coverage,
        "evalue": evalue,
        "thermodynamics": {"dg_kcal": dg},
        "three_prime_mismatches": three_prime_mismatches,
        "accession": accession,
        "subject_start": start,
        "subject_end": end,
        "title": title,
    }


class SpecificityRequirementTests(unittest.TestCase):
    def test_target_search_filters_only_policy_relevant_short_hsps(self) -> None:
        adapter = BlastAdapter()
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )
        with patch(
            "agentd.specificity.subprocess.run",
            return_value=completed,
        ) as run:
            adapter._blast_search(
                {"forward": "A" * 20},
                Path("fixture-target-db"),
                evalue=1000.0,
                mode="TARGET_HIT",
            )

        command = run.call_args.args[0]
        self.assertEqual(
            command[command.index("-perc_identity") + 1],
            str(P2_THRESHOLDS["TARGET_HIT"]["caution_identity_pct_min"]),
        )
        self.assertEqual(
            command[command.index("-qcov_hsp_perc") + 1],
            str(P2_THRESHOLDS["TARGET_HIT"]["caution_coverage_pct_min"]),
        )
        self.assertEqual(command[command.index("-max_hsps") + 1], "100")

    def test_host_search_does_not_inherit_target_retrieval_filter(self) -> None:
        adapter = BlastAdapter()
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )
        with patch(
            "agentd.specificity.subprocess.run",
            return_value=completed,
        ) as run:
            adapter._blast_search(
                {"forward": "A" * 20},
                Path("fixture-host-db"),
                evalue=0.1,
                mode="HOST_EXCLUSION",
            )

        command = run.call_args.args[0]
        self.assertNotIn("-perc_identity", command)
        self.assertNotIn("-qcov_hsp_perc", command)
        self.assertNotIn("-max_hsps", command)

    def test_parser_counts_query_three_prime_mismatches(self) -> None:
        row = (
            "forward\tsubject-1\t80\t5\t1\t0\t1\t5\t10\t14\t1e-8\t25\t"
            "AACCG\tAACTG\tplus\tSynthetic subject"
        )
        hit = BlastAdapter._parse_tabular(row, {"forward": "AACCG"})[0]
        self.assertEqual(hit["three_prime_mismatches"], 1)
        self.assertEqual(hit["query_coverage_pct"], 100.0)

    def test_target_uses_identity_dg_and_three_prime_table(self) -> None:
        queries = {"forward": "A" * 20, "reverse": "C" * 20, "probe": "T" * 20}
        passing = [_hit(query_id) for query_id in queries]
        evaluator = _RequirementEvaluator(_StaticBlast({"target": passing}), _StaticPrimer3())
        result = evaluator.check_target(queries, "target-db")
        self.assertEqual(result.verdict, "PASS")
        self.assertEqual(set(result.mode_specific["perfect_query_ids"]), set(queries))

        caution_hits = copy.deepcopy(passing)
        caution_hits[1]["thermodynamics"]["dg_kcal"] = -8.0
        evaluator = _RequirementEvaluator(_StaticBlast({"target": caution_hits}), _StaticPrimer3())
        result = evaluator.check_target(queries, "target-db")
        self.assertEqual(result.verdict, "CAUTION")
        self.assertEqual(result.mode_specific["per_query_verdict"]["reverse"], "CAUTION")

        evaluator = _RequirementEvaluator(_StaticBlast({"target": passing[:1]}), _StaticPrimer3())
        result = evaluator.check_target(queries, "target-db")
        self.assertEqual(result.verdict, "FAIL")

    def test_target_exact_short_hit_uses_permissive_retrieval_not_evalue_gate(self) -> None:
        queries = {"forward": "A" * 20, "reverse": "C" * 20}
        hits = [_hit(query_id, evalue=1.14e-5) for query_id in queries]
        blast = _StaticBlast({"target": hits})
        evaluator = _RequirementEvaluator(blast, _StaticPrimer3())

        result = evaluator.check_target(queries, "target-db")

        self.assertEqual(blast.calls[0], {"evalue": 1000.0, "mode": "target"})
        self.assertEqual(result.verdict, "PASS")
        self.assertEqual(set(result.mode_specific["perfect_query_ids"]), set(queries))

    def test_host_single_hit_is_caution_even_when_hit_level_risk_is_high(self) -> None:
        queries = {"forward": "A" * 20, "reverse": "C" * 20}
        high_risk = [_hit("forward", evalue=1e-3, dg=-7.0, three_prime_mismatches=1)]
        evaluator = _RequirementEvaluator(_StaticBlast({"host": high_risk}), _StaticPrimer3())
        result = evaluator.check_host(queries, "host-db", "fixture-host")
        self.assertEqual(result.verdict, "CAUTION")
        self.assertEqual(result.mode_specific["high_risk_hit_count"], 1)
        self.assertEqual(result.mode_specific["compatible_amplicons"], [])

        lower_risk = [_hit("forward", evalue=1e-3, dg=-7.0, three_prime_mismatches=2)]
        evaluator = _RequirementEvaluator(_StaticBlast({"host": lower_risk}), _StaticPrimer3())
        self.assertEqual(
            evaluator.check_host(queries, "host-db", "fixture-host").verdict,
            "CAUTION",
        )

    def test_compatible_host_primer_hits_are_fail(self) -> None:
        queries = {"forward": "A" * 20, "reverse": "C" * 20}
        hits = [
            _hit("forward", evalue=1e-3, dg=-5.0, three_prime_mismatches=2, accession="chr1", start=100),
            _hit("reverse", evalue=1e-3, dg=-5.0, three_prime_mismatches=2, accession="chr1", start=350),
        ]
        evaluator = _RequirementEvaluator(_StaticBlast({"host": hits}), _StaticPrimer3())
        result = evaluator.check_host(queries, "host-db", "pig")
        self.assertEqual(result.verdict, "FAIL")
        self.assertEqual(len(result.mode_specific["compatible_amplicons"]), 1)

    def test_cross_species_excludes_target_and_reports_same_genus(self) -> None:
        queries = {"forward": "A" * 20, "reverse": "C" * 20}
        hits = [
            _hit("forward", title="record [Pasteurella multocida]"),
            _hit("forward", accession="other", title="record [Pasteurella canis]"),
        ]
        evaluator = _RequirementEvaluator(_StaticBlast({"cross_species": hits}), _StaticPrimer3())
        result = evaluator.check_cross_species(queries, "nt", "Pasteurella multocida")
        self.assertEqual(result.verdict, "CAUTION")
        self.assertEqual(result.blast_hits, 1)
        self.assertEqual(result.mode_specific["same_genus_species"], ["Pasteurella canis"])

    def test_cross_species_excludes_target_accession_across_blast_id_forms(self) -> None:
        queries = {"forward": "A" * 20, "reverse": "C" * 20}
        hits = [
            _hit("forward", accession="ref|TARGET_RECORD.2|"),
            _hit("forward", accession="BACKGROUND_RECORD.1"),
        ]
        evaluator = _RequirementEvaluator(
            _StaticBlast({"cross_species": hits}),
            _StaticPrimer3(),
        )

        result = evaluator.check_cross_species(
            queries,
            "background-db",
            None,
            target_accessions={"TARGET_RECORD.1"},
        )

        self.assertEqual(result.verdict, "CAUTION")
        self.assertEqual(result.blast_hits, 1)
        self.assertEqual(result.mode_specific["excluded_target_hit_count"], 1)

    def test_combined_regression_keeps_self_hit_out_and_single_host_hit_soft(self) -> None:
        queries = {"forward": "A" * 20, "reverse": "C" * 20}
        target_hits = [
            _hit(query_id, accession="TARGET_RECORD.1")
            for query_id in queries
        ]
        host_hits = [
            _hit(
                "forward",
                accession="HOST_RECORD.1",
                evalue=1e-3,
                dg=-7.0,
                three_prime_mismatches=1,
            )
        ]
        background_hits = [
            _hit("forward", accession="ref|TARGET_RECORD.2|")
        ]
        evaluator = _RequirementEvaluator(
            _StaticBlast(
                {
                    "target": target_hits,
                    "host": host_hits,
                    "cross_species": background_hits,
                }
            ),
            _StaticPrimer3(),
        )

        result = evaluator.evaluate(
            type("Oligo", (), {"sequence_5to3": queries["forward"]})(),
            type("Oligo", (), {"sequence_5to3": queries["reverse"]})(),
            None,
            target_db="target-db",
            host_db="host-db",
            cross_db="background-db",
            host_species="fixture-host",
            target_species=None,
        )

        self.assertEqual(result.target.verdict, "PASS")
        self.assertEqual(result.host.verdict, "CAUTION")
        self.assertEqual(result.cross_species.verdict, "PASS")
        self.assertEqual(result.rating, "PASS_WITH_WARNINGS")

    def test_exact_target_evidence_is_per_query_not_all_returned_hits(self) -> None:
        queries = {"forward": "A" * 20, "reverse": "C" * 20}
        target_hits = [
            _hit("forward"),
            _hit("reverse"),
            _hit("forward", accession="weak-extra", identity=80.0, dg=-4.0),
        ]
        evaluator = _RequirementEvaluator(
            _StaticBlast({"target": target_hits, "host": [], "cross_species": []}),
            _StaticPrimer3(),
        )
        result = evaluator.evaluate(
            type("Oligo", (), {"sequence_5to3": queries["forward"]})(),
            type("Oligo", (), {"sequence_5to3": queries["reverse"]})(),
            None,
            target_db="target-db",
            host_db="host-db",
            cross_db="nt",
            host_species="pig",
            target_species="Synthetic virus",
        )
        self.assertEqual(result.rating, "PASS")
        self.assertEqual(evidence_from_specificity(result), EvidenceLevel.BLAST_VERIFIED)


if __name__ == "__main__":
    unittest.main()

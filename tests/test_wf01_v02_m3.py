from __future__ import annotations

import copy
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.config import load_config
from agentd.models import Candidate, Oligo
from agentd.service import AgentDService
from agentd.wf01_v02 import DEFAULT_PARAMETERS, PARAMETER_PROFILES
from agentd.wf01_v02_engine import WF01V02RuleEngine, WF01V02TargetEngine
from agentd.wf01_v02_m3 import (
    AMPLICON_COMPONENT_WEIGHTS,
    FIXED_TOP_CANDIDATES,
    FINAL_RANK_TIE_BREAKERS,
    SCORING_POLICY_STATUS,
    SCORING_POLICY_VERSION,
    SCORING_SCALE,
    SCORING_WEIGHTS,
    THERMODYNAMICS_COMPONENT_WEIGHTS,
    WF01V02M3Engine,
    WF01V02ScoringEngine,
    candidate_dedupe_key,
    deduplicate_target_candidates,
    final_scoring_rank_key,
)


def _oligo(
    sequence: str,
    tm: float,
    start: int,
    *,
    hairpin: float = -1.0,
    homodimer: float = -2.0,
    three_prime_dimer: float = -2.0,
) -> Oligo:
    return Oligo(
        sequence_5to3=sequence,
        length_nt=len(sequence),
        tm_celsius=tm,
        gc_percent=40.0,
        position_start=start,
        position_end=start + len(sequence) - 1,
        hairpin_dg_kcal=hairpin,
        homodimer_dg_kcal=homodimer,
        three_prime_dimer_dg_kcal=three_prime_dimer,
        thermo_method="primer3_santalucia_1998",
    )


def candidate_occurrence(
    index: int,
    *,
    unit_id: str = "target_001-unit-001",
    profile: str = "default",
    forward_tm: float = 60.0,
    reverse_tm: float = 60.0,
    probe_tm: float = 69.0,
    forward_hairpin: float = -1.0,
    pair_dg: float = -5.0,
    amplicon_length: int = 100,
    primer3_penalty: float | None = None,
) -> dict:
    prefix = "".join("C" if index & (1 << bit) else "A" for bit in range(4))
    forward_sequence = prefix + "ATATATATATATATGC"
    reverse_sequence = "TATATATATATATATATACG"
    probe_sequence = "ACACACACACACACACACACAC"
    amplicon = ("ACGT" * ((amplicon_length + 3) // 4))[:amplicon_length]
    candidate = Candidate(
        candidate_id=f"target_001-p3-{index + 1}",
        forward_primer=_oligo(
            forward_sequence,
            forward_tm,
            0,
            hairpin=forward_hairpin,
        ),
        reverse_primer=_oligo(
            reverse_sequence,
            reverse_tm,
            amplicon_length - len(reverse_sequence),
        ),
        probe=_oligo(probe_sequence, probe_tm, 25),
        amplicon_sequence_5to3=amplicon,
        amplicon_start=0,
        amplicon_end=amplicon_length - 1,
        primer3_pair_penalty=float(index if primer3_penalty is None else primer3_penalty),
        pair_heterodimer_dg_kcal=pair_dg,
        three_prime_heterodimer_dg_kcal=-5.0,
        three_prime_involved=False,
        metadata={
            "software": "primer3-py",
            "software_version": "test",
            "tm_algorithm": "santalucia",
            "mv_conc_mm": 50.0,
            "dv_conc_mm": 1.5,
            "dntp_conc_mm": 0.6,
            "oligo_conc_nm": 250.0,
            "target_id": "target_001",
            "exploration_unit_id": unit_id,
            "parameter_run_id": "run-001",
            "parameter_profile_labels": [profile],
            "effective_parameters": copy.deepcopy(DEFAULT_PARAMETERS),
        },
    )
    return WF01V02RuleEngine().apply(candidate).to_dict()


def m3_target(
    candidates: list[dict],
    *,
    statuses: tuple[str, ...] = ("COMPLETED", "COMPLETED", "COMPLETED"),
    counts: tuple[int, ...] | None = None,
) -> dict:
    counts = counts or tuple(
        [len(candidates)] + [0] * (len(statuses) - 1)
    )
    units = [
        {
            "exploration_unit_id": f"target_001-unit-{index:03d}",
            "status": status,
            "counts": {"generated": counts[index - 1]},
            "tool_error": {"code": "P1_UNAVAILABLE"} if status == "TOOL_ERROR" else None,
        }
        for index, status in enumerate(statuses, start=1)
    ]
    return {
        "target_id": "target_001",
        "status": "EXPLORING",
        "m2_execution": {
            "stage": "M2_RULES_COMPLETE_AWAITING_M3_M4",
            "exploration_manifest": {
                "manifest_id": "target_001-test",
                "manifest_sha256": "a" * 64,
                "policy_version": "wf01-v0.2-whole-template-v1",
                "policy_status": "PROVISIONAL_PENDING_DEC_01_APPROVAL",
                "created_before_execution": True,
                "expected_unit_count": len(units),
                "units": units,
            },
            "hard_rule_pass_candidates": copy.deepcopy(candidates),
            "hard_rule_rejected_candidates": [],
            "hard_rule_incomplete_candidates": [],
        },
    }


class ManyCandidatePrimer3Adapter:
    def __init__(self, per_call: int = 12) -> None:
        self.per_call = per_call
        self.calls: list[str] = []

    def design(self, template: str, *, sequence_id: str, parameters: dict, target_region=None):
        self.calls.append(sequence_id)
        result = []
        for index in range(self.per_call):
            item = candidate_occurrence(index)
            item["candidate_id"] = f"{sequence_id}-fake-{index + 1}"
            metadata = item["metadata"]
            metadata["parameter_profile_labels"] = [
                "default" if "run-001" in sequence_id else "L1" if "run-002" in sequence_id else "L2"
            ]
            result.append(item)
        # The M2 engine expects Candidate domain objects.
        return [_candidate_from_dict(item) for item in result]

    @staticmethod
    def status() -> dict:
        return {"tool": "P1_Primer3", "available": True, "version": "test"}


def _candidate_from_dict(data: dict) -> Candidate:
    def oligo(raw: dict) -> Oligo:
        return Oligo(**raw)

    amplicon = data["amplicon"]
    return Candidate(
        candidate_id=data["candidate_id"],
        forward_primer=oligo(data["forward_primer"]),
        reverse_primer=oligo(data["reverse_primer"]),
        probe=oligo(data["probe"]),
        amplicon_sequence_5to3=amplicon["sequence_5to3"],
        amplicon_start=amplicon["start"],
        amplicon_end=amplicon["end"],
        primer3_pair_penalty=data["primer3_pair_penalty"],
        pair_heterodimer_dg_kcal=data["pair_heterodimer_dg_kcal"],
        three_prime_heterodimer_dg_kcal=data["three_prime_heterodimer_dg_kcal"],
        three_prime_involved=data["three_prime_involved"],
        design_method=data["design_method"],
        metadata=copy.deepcopy(data["metadata"]),
    )


class WF01V02M3ManifestTests(unittest.TestCase):
    def test_ac_exe_04_manifest_exists_before_execution_and_no_top10_early_stop(self) -> None:
        fake = ManyCandidatePrimer3Adapter(per_call=12)
        engine = WF01V02TargetEngine(
            load_config(),
            default_parameters=DEFAULT_PARAMETERS,
            parameter_profiles=PARAMETER_PROFILES,
            primer3_adapter=fake,
        )
        m2 = engine.run_target(
            {
                "target_id": "target_001",
                "template": {"sequence": "ACGT" * 40, "checksum_sha256": "b" * 64},
                "parameters": {
                    "user_overrides": {},
                    "effective_user_profile": copy.deepcopy(DEFAULT_PARAMETERS),
                },
            }
        )
        manifest = m2["exploration_manifest"]
        self.assertTrue(manifest["created_before_execution"])
        self.assertEqual(manifest["expected_unit_count"], 3)
        self.assertEqual(manifest["maximum_enumeration"], 150)
        self.assertEqual(len(fake.calls), 3)
        self.assertTrue(all(unit["status"] == "COMPLETED" for unit in manifest["units"]))

        result = WF01V02M3Engine().run_target(
            {
                "target_id": "target_001",
                "status": "EXPLORING",
                "m2_execution": m2,
            }
        )
        self.assertTrue(result["manifest_validation"]["candidate_count_did_not_control_termination"])
        self.assertTrue(result["manifest_validation"]["generated_candidates_after_tenth_occurrence"])
        self.assertTrue(result["primer3_search_complete"])
        self.assertFalse(result["search_complete"])

    def test_tool_error_unit_prevents_primer3_search_complete(self) -> None:
        result = WF01V02M3Engine().run_target(
            m3_target(
                [candidate_occurrence(0)],
                statuses=("COMPLETED", "TOOL_ERROR"),
                counts=(1, 0),
            )
        )
        self.assertFalse(result["primer3_search_complete"])
        self.assertEqual(
            result["search_incomplete_reason"],
            "PRIMER3_EXPLORATION_UNIT_INCOMPLETE",
        )


class WF01V02M3DeduplicationTests(unittest.TestCase):
    def test_ac_ded_01_same_target_duplicate_merges_all_sources_without_score_gain(self) -> None:
        first = candidate_occurrence(0, unit_id="target_001-unit-001", profile="default")
        duplicate = copy.deepcopy(first)
        duplicate["candidate_id"] = "target_001-p3-duplicate"
        duplicate["metadata"]["exploration_unit_id"] = "target_001-unit-002"
        duplicate["metadata"]["parameter_profile_labels"] = ["L1"]
        unique, summary = deduplicate_target_candidates([first, duplicate])
        self.assertEqual(summary["unique_candidates"], 1)
        self.assertEqual(summary["duplicates_collapsed"], 1)
        dedupe = unique[0]["metadata"]["dedupe"]
        self.assertEqual(dedupe["occurrence_count"], 2)
        self.assertEqual(dedupe["merged_parameter_profile_labels"], ["default", "L1"])
        self.assertTrue(dedupe["duplicate_occurrences_do_not_increase_score"])
        self.assertEqual(unique[0]["dedupe_key"], candidate_dedupe_key(first))

    def test_ac_ded_02_identical_candidates_in_two_targets_are_not_cross_merged(self) -> None:
        candidate = candidate_occurrence(0)
        first = WF01V02M3Engine().run_target(m3_target([candidate]))
        second_target = m3_target([candidate])
        second_target["target_id"] = "target_002"
        second_target["m2_execution"]["exploration_manifest"]["manifest_id"] = "target_002-test"
        second = WF01V02M3Engine().run_target(second_target)
        self.assertEqual(first["deduplication"]["unique_candidates"], 1)
        self.assertEqual(second["deduplication"]["unique_candidates"], 1)
        self.assertFalse(first["deduplication"]["cross_target_deduplication"])


class WF01V02M3ScoringTests(unittest.TestCase):
    def test_primer_tm_55_60_and_65_are_equal_without_tm_penalty(self) -> None:
        at_55 = WF01V02ScoringEngine().score(
            candidate_occurrence(
                0,
                forward_tm=55.0,
                reverse_tm=55.0,
                probe_tm=64.0,
            ),
            specificity_score=100.0,
        )
        at_60 = WF01V02ScoringEngine().score(
            candidate_occurrence(
                1,
                forward_tm=60.0,
                reverse_tm=60.0,
                probe_tm=69.0,
            ),
            specificity_score=100.0,
        )
        at_65 = WF01V02ScoringEngine().score(
            candidate_occurrence(
                2,
                forward_tm=65.0,
                reverse_tm=65.0,
                probe_tm=74.0,
            ),
            specificity_score=100.0,
        )

        for scoring in (at_55, at_60, at_65):
            thermodynamics = scoring["dimensions"]["thermodynamics"]
            self.assertEqual(thermodynamics["components"]["forward_primer_tm"], 100.0)
            self.assertEqual(thermodynamics["components"]["reverse_primer_tm"], 100.0)
            self.assertEqual(thermodynamics["raw_score"], 100.0)
            self.assertFalse(any(item["rule_code"] == "R01_SOFT" for item in scoring["penalties"]))
        self.assertEqual(at_55["final_score"], at_60["final_score"])
        self.assertEqual(at_60["final_score"], at_65["final_score"])

    def test_primer_tm_50_to_below_55_uses_only_r01_soft_penalty(self) -> None:
        scoring = WF01V02ScoringEngine().score(
            candidate_occurrence(
                0,
                forward_tm=54.0,
                reverse_tm=54.0,
                probe_tm=63.0,
            ),
            specificity_score=100.0,
        )

        thermodynamics = scoring["dimensions"]["thermodynamics"]
        self.assertEqual(thermodynamics["raw_score"], 100.0)
        self.assertEqual(
            [item["rule_code"] for item in scoring["penalties"]],
            ["R01_SOFT"],
        )
        self.assertEqual(scoring["total_penalty_percent"], 5.0)

    def test_probe_relative_tm_8_to_10_are_equal_and_outside_scores_zero(self) -> None:
        component_scores = {}
        for probe_delta in (7.99, 8.0, 9.0, 10.0, 10.01):
            scoring = WF01V02ScoringEngine().score(
                candidate_occurrence(
                    0,
                    forward_tm=60.0,
                    reverse_tm=60.0,
                    probe_tm=60.0 + probe_delta,
                ),
                specificity_score=100.0,
            )
            component_scores[probe_delta] = scoring["dimensions"][
                "thermodynamics"
            ]["components"]["probe_relative_tm"]

        self.assertEqual(component_scores[7.99], 0.0)
        self.assertEqual(component_scores[8.0], 100.0)
        self.assertEqual(component_scores[9.0], 100.0)
        self.assertEqual(component_scores[10.0], 100.0)
        self.assertEqual(component_scores[10.01], 0.0)

        decimal_boundary = WF01V02ScoringEngine().score(
            candidate_occurrence(
                0,
                forward_tm=60.1,
                reverse_tm=60.1,
                probe_tm=68.1,
            ),
            specificity_score=100.0,
        )
        self.assertEqual(
            decimal_boundary["dimensions"]["thermodynamics"]["components"][
                "probe_relative_tm"
            ],
            100.0,
        )

    def test_ac_sco_01_soft_penalties_are_additive_and_final_formula_is_explainable(self) -> None:
        candidate = candidate_occurrence(
            0,
            forward_tm=54.0,
            reverse_tm=54.0,
            probe_tm=63.0,
            forward_hairpin=-3.1,
            pair_dg=-9.0,
        )
        scoring = WF01V02ScoringEngine().score(candidate, specificity_score=100.0)
        self.assertEqual(
            [item["rule_code"] for item in scoring["penalties"]],
            ["R01_SOFT", "R11", "R12"],
        )
        self.assertEqual(scoring["total_penalty_percent"], 11.0)
        self.assertEqual(scoring["applied_penalty_percent"], 11.0)
        self.assertEqual(scoring["status"], "FINAL")
        self.assertAlmostEqual(
            scoring["final_score"],
            scoring["base_score"] * 0.89,
            places=5,
        )

    def test_ac_sco_01_amplicon_70_only_reduces_amplicon_dimension(self) -> None:
        boundary = WF01V02ScoringEngine().score(candidate_occurrence(0, amplicon_length=70))
        standard = WF01V02ScoringEngine().score(candidate_occurrence(0, amplicon_length=100))
        self.assertEqual(
            boundary["dimensions"]["amplicon_features"]["components"]["length"],
            70.0,
        )
        self.assertEqual(
            standard["dimensions"]["amplicon_features"]["components"]["length"],
            100.0,
        )
        self.assertFalse(any(item["rule_code"] == "R02_SOFT" for item in boundary["penalties"]))

    def test_amplicon_length_uses_confirmed_fixed_interval_scores(self) -> None:
        expected_scores = {
            70: 70.0,
            71: 100.0,
            150: 100.0,
            151: 70.0,
            180: 70.0,
            200: 70.0,
        }
        for length, expected in expected_scores.items():
            with self.subTest(length=length):
                scoring = WF01V02ScoringEngine().score(
                    candidate_occurrence(0, amplicon_length=length)
                )
                self.assertEqual(
                    scoring["dimensions"]["amplicon_features"]["components"][
                        "length"
                    ],
                    expected,
                )

    def test_missing_mafft_evidence_is_not_faked_and_weights_are_renormalized(self) -> None:
        candidate = candidate_occurrence(0)
        # A legacy scalar without traceable MAFFT evidence must never count.
        candidate["conservation_score"] = 0.0
        scoring = WF01V02ScoringEngine().score(candidate)
        self.assertEqual(scoring["weights"], SCORING_WEIGHTS)
        self.assertFalse(scoring["conservation"]["participates_in_score"])
        self.assertEqual(scoring["conservation"]["weight"], 0.3)
        self.assertEqual(scoring["conservation"]["effective_weight"], 0.0)
        self.assertEqual(
            scoring["conservation_scoring_mode"],
            "RENORMALIZED_WITHOUT_CONSERVATION",
        )
        self.assertEqual(scoring["status"], "PENDING_SPECIFICITY")
        self.assertIsNone(scoring["base_score"])
        self.assertIsNone(scoring["final_score"])
        self.assertIsNotNone(scoring["pre_specificity_priority"]["after_penalties"])

    def test_traceable_mafft_evidence_enters_four_dimension_score(self) -> None:
        candidate = candidate_occurrence(0)
        candidate["metadata"]["conservation_evidence"] = {
            "status": "EVALUATED",
            "reason": "MAFFT_SHANNON_BINDING_SITES_EVALUATED",
            "method": "mafft_shannon",
            "sequence_count": 20,
            "score": 0.96,
            "coverage_percent": 95.0,
            "confidence": "HIGH",
            "region": {"selection_round": 1},
        }

        scoring = WF01V02ScoringEngine().score(
            candidate,
            specificity_score=100.0,
        )

        self.assertEqual(scoring["status"], "FINAL")
        self.assertEqual(scoring["conservation_scoring_mode"], "FOUR_DIMENSION")
        self.assertEqual(scoring["effective_weights"], SCORING_WEIGHTS)
        self.assertEqual(scoring["dimensions"]["conservation"]["raw_score"], 96.0)
        self.assertTrue(
            scoring["dimensions"]["conservation"]["participates_in_score"]
        )
        self.assertEqual(scoring["base_score"], 98.8)
        self.assertEqual(scoring["final_score"], 98.8)

    def test_low_confidence_conservation_has_explicit_penalty(self) -> None:
        candidate = candidate_occurrence(0)
        candidate["metadata"]["conservation_evidence"] = {
            "status": "EVALUATED",
            "reason": "MAFFT_SHANNON_BINDING_SITES_EVALUATED",
            "method": "mafft_shannon",
            "sequence_count": 8,
            "score": 0.96,
            "coverage_percent": 87.5,
            "confidence": "LOW",
            "low_confidence_reasons": [
                "REPRESENTATIVE_SEQUENCE_COUNT_BELOW_20"
            ],
            "region": {"selection_round": 1},
        }

        scoring = WF01V02ScoringEngine().score(
            candidate,
            specificity_score=100.0,
        )

        penalty = next(
            item
            for item in scoring["penalties"]
            if item["rule_code"] == "LOW_CONFIDENCE_CONSERVATION"
        )
        self.assertEqual(penalty["penalty_percent"], 10.0)
        self.assertEqual(scoring["applied_penalty_percent"], 10.0)
        self.assertEqual(scoring["final_score"], 88.92)

    def test_scoring_contract_is_versioned_complete_and_auditable(self) -> None:
        scoring = WF01V02ScoringEngine().score(
            candidate_occurrence(0),
            specificity_score=100.0,
        )
        self.assertEqual(scoring["policy_version"], SCORING_POLICY_VERSION)
        self.assertEqual(scoring["policy_status"], SCORING_POLICY_STATUS)
        self.assertEqual(scoring["scale"], SCORING_SCALE)
        self.assertEqual(scoring["missing_dimensions"], [])
        self.assertEqual(scoring["calculation_decimals"], 6)
        self.assertEqual(
            scoring["dimensions"]["thermodynamics"]["component_weights"],
            THERMODYNAMICS_COMPONENT_WEIGHTS,
        )
        self.assertEqual(
            scoring["dimensions"]["amplicon_features"]["component_weights"],
            AMPLICON_COMPONENT_WEIGHTS,
        )
        self.assertEqual(
            scoring["ranking_contract"]["tie_breakers"],
            list(FINAL_RANK_TIE_BREAKERS),
        )
        self.assertIn("min(additive_penalty_percent,100)", scoring["penalty_application"])

    def test_missing_non_specificity_dimension_blocks_final_score_explicitly(self) -> None:
        candidate = candidate_occurrence(0)
        candidate["forward_primer"]["tm_celsius"] = None
        scoring = WF01V02ScoringEngine().score(
            candidate,
            specificity_score=100.0,
        )
        self.assertEqual(scoring["status"], "INCOMPLETE_DIMENSIONS")
        self.assertEqual(scoring["missing_dimensions"], ["thermodynamics"])
        self.assertIsNone(scoring["base_score"])
        self.assertIsNone(scoring["final_score"])

    def test_final_tie_break_prefers_less_penalty_then_dimension_scores(self) -> None:
        first = candidate_occurrence(0)
        second = candidate_occurrence(1)
        for value in (first, second):
            value["score"] = 90.0
            value["dedupe_key"] = candidate_dedupe_key(value)
            value.setdefault("metadata", {})["wf01_v02_scoring"] = {
                "applied_penalty_percent": 0.0,
                "dimensions": {
                    "thermodynamics": {"raw_score": 90.0},
                    "specificity": {"raw_score": 100.0},
                    "amplicon_features": {"raw_score": 90.0},
                },
            }
        second["metadata"]["wf01_v02_scoring"]["applied_penalty_percent"] = 2.0
        ranked = sorted([second, first], key=final_scoring_rank_key)
        self.assertIs(ranked[0], first)


class WF01V02M3Top10Tests(unittest.TestCase):
    def test_fixed_top10_keeps_all_unique_eligible_and_excluded_candidates(self) -> None:
        candidates = [candidate_occurrence(index) for index in range(12)]
        result = WF01V02M3Engine().run_target(
            m3_target(candidates, counts=(10, 1, 1))
        )
        self.assertEqual(result["fixed_top_candidates"], FIXED_TOP_CANDIDATES)
        self.assertEqual(len(result["provisional_top10"]), 10)
        self.assertEqual(len(result["all_hard_rule_pass_candidates"]), 12)
        self.assertFalse(result["top10_is_final"])
        self.assertEqual(result["ranking_status"], "PROVISIONAL_PENDING_M4_SPECIFICITY")
        self.assertTrue(all(candidate["score"] is None for candidate in result["provisional_top10"]))


@unittest.skipUnless(importlib.util.find_spec("fastapi") is not None, "FastAPI extra not installed")
class WF01V02M3ApiTests(unittest.TestCase):
    def test_run_m3_endpoint_persists_provisional_top10(self) -> None:
        from fastapi.testclient import TestClient
        from agentd import api

        payload = {
            "fasta_text": ">api_target\n" + "ACGT" * 40,
            "batch_defaults": {
                "target_organism": {"name": "Synthetic test virus"},
                "hosts": [{"name": "pig"}],
            },
        }
        with tempfile.TemporaryDirectory(prefix="agentd-m3-api-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            fake = ManyCandidatePrimer3Adapter(per_call=4)
            with (
                patch.object(api, "service", service),
                patch("agentd.wf01_v02_engine.Primer3Adapter", return_value=fake),
            ):
                client = TestClient(api.app)
                created = client.post("/v1/wf01/tasks", json=payload).json()
                batch_id = created["batch_id"]
                m2 = client.post(f"/v1/wf01/tasks/{batch_id}/run-m2", json={})
                self.assertEqual(m2.status_code, 200)
                m3 = client.post(f"/v1/wf01/tasks/{batch_id}/run-m3", json={})
                self.assertEqual(m3.status_code, 200)
                data = m3.json()
                self.assertEqual(data["m3_outcome"], "COMPLETED")
                self.assertTrue(data["primer3_search_complete"])
                self.assertFalse(data["search_complete"])
                self.assertEqual(
                    data["targets"][0]["m3_execution"]["stage"],
                    "M3_COMPLETE_AWAITING_M4",
                )
                self.assertEqual(
                    data["events"][-1]["event_type"],
                    "M3_EXECUTION_COMPLETED",
                )
                rerun = client.post(f"/v1/wf01/tasks/{batch_id}/run-m3", json={})
                self.assertEqual(rerun.status_code, 422)


if __name__ == "__main__":
    unittest.main()

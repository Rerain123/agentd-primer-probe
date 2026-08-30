from __future__ import annotations

import copy
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
from agentd.models import Candidate, Oligo, RuleStatus, SequenceRecord
from agentd.service import AgentDService
from agentd.wf01_v02 import DEFAULT_PARAMETERS, PARAMETER_PROFILES
from agentd.wf01_v02_engine import (
    WF01V02RuleEngine,
    WF01V02TargetEngine,
    build_parameter_runs,
)


def _oligo(
    sequence: str,
    tm: float | None,
    start: int | None,
    *,
    hairpin: float | None = -1.0,
    homodimer: float | None = -2.0,
    three_prime_dimer: float | None = -2.0,
) -> Oligo:
    return Oligo(
        sequence_5to3=sequence,
        length_nt=len(sequence),
        tm_celsius=tm,
        gc_percent=40.0,
        position_start=start,
        position_end=None if start is None else start + len(sequence) - 1,
        hairpin_dg_kcal=hairpin,
        homodimer_dg_kcal=homodimer,
        three_prime_dimer_dg_kcal=three_prime_dimer,
        thermo_method="primer3_santalucia_1998",
    )


def valid_candidate(
    *,
    candidate_id: str = "target_001-p3-1",
    amplicon_length: int = 100,
    forward_sequence: str = "ATATATATATATATATATGC",
    reverse_sequence: str = "TATATATATATATATATACG",
    probe_sequence: str = "ACACACACACACACACACACAC",
    forward_tm: float | None = 60.0,
    reverse_tm: float | None = 60.0,
    probe_tm: float | None = 69.0,
    forward_start: int | None = 0,
    probe_start: int | None = 25,
    reverse_start: int | None = None,
    forward_hairpin: float | None = -1.0,
    pair_dg: float | None = -5.0,
    forward_three_prime_dg: float | None = -2.0,
    reverse_three_prime_dg: float | None = -2.0,
    three_prime_pair_dg: float | None = -5.0,
    three_prime_involved: bool | None = False,
) -> Candidate:
    if reverse_start is None:
        reverse_start = amplicon_length - len(reverse_sequence)
    amplicon = ("ACGT" * ((amplicon_length + 3) // 4))[:amplicon_length]
    return Candidate(
        candidate_id=candidate_id,
        forward_primer=_oligo(
            forward_sequence,
            forward_tm,
            forward_start,
            hairpin=forward_hairpin,
            three_prime_dimer=forward_three_prime_dg,
        ),
        reverse_primer=_oligo(
            reverse_sequence,
            reverse_tm,
            reverse_start,
            three_prime_dimer=reverse_three_prime_dg,
        ),
        probe=_oligo(probe_sequence, probe_tm, probe_start),
        amplicon_sequence_5to3=amplicon,
        amplicon_start=0,
        amplicon_end=amplicon_length - 1,
        pair_heterodimer_dg_kcal=pair_dg,
        three_prime_heterodimer_dg_kcal=three_prime_pair_dg,
        three_prime_involved=three_prime_involved,
        metadata={
            "software": "primer3-py",
            "software_version": "test",
            "tm_algorithm": "santalucia",
            "mv_conc_mm": 50.0,
            "dv_conc_mm": 1.5,
            "dntp_conc_mm": 0.6,
            "oligo_conc_nm": 250.0,
        },
    )


def _rules(candidate: Candidate) -> dict[str, object]:
    return {rule.code: rule for rule in WF01V02RuleEngine().evaluate(candidate)}


class FakePrimer3Adapter:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def design(self, template: str, *, sequence_id: str, parameters: dict, target_region=None):
        self.calls.append(
            {
                "template": template,
                "sequence_id": sequence_id,
                "parameters": copy.deepcopy(parameters),
                "target_region": target_region,
            }
        )
        return [valid_candidate(candidate_id=f"{sequence_id}-fake-1")]

    @staticmethod
    def status() -> dict:
        return {
            "tool": "P1_Primer3",
            "available": True,
            "version": "test",
            "fallback_enabled": False,
        }


class FakeMafftAdapter:
    def __init__(self) -> None:
        self.calls: list[list[SequenceRecord]] = []

    def align(self, records: list[SequenceRecord]):
        self.calls.append(copy.deepcopy(records))
        return records, "mafft_shannon", []

    @staticmethod
    def status() -> dict:
        return {
            "tool": "C5_MAFFT",
            "available": True,
            "executable": "/usr/bin/mafft",
            "fallback_available": True,
        }


class DevelopmentFallbackMafftAdapter(FakeMafftAdapter):
    def align(self, records: list[SequenceRecord]):
        self.calls.append(copy.deepcopy(records))
        self.last_evidence = {
            "tool": "MAFFT",
            "status": "DEVELOPMENT_FALLBACK",
            "method": "prealigned_shannon_fallback",
            "input_records": [
                {"id": record.id, "sequence": record.sequence}
                for record in records
            ],
            "aligned_records": [
                {"id": record.id, "sequence": record.sequence}
                for record in records
            ],
        }
        return (
            records,
            "prealigned_shannon_fallback",
            ["MAFFT 不可用；仅供开发降级"],
        )


class WF01V02M2ParameterTests(unittest.TestCase):
    def test_ac_par_01_default_profile_matches_prd(self) -> None:
        default = PARAMETER_PROFILES["default"]
        self.assertEqual(
            {
                "primer_tm": (default["primer_min_tm"], default["primer_max_tm"]),
                "product": (default["product_min_bp"], default["product_max_bp"]),
                "primer_length": (default["primer_min_size"], default["primer_max_size"]),
                "probe_length": (default["probe_min_size"], default["probe_max_size"]),
            },
            {
                "primer_tm": (58.0, 62.0),
                "product": (70, 150),
                "primer_length": (18, 28),
                "probe_length": (22, 32),
            },
        )

    def test_ac_par_02_only_l1_l2_profiles_match_prd(self) -> None:
        self.assertEqual(set(PARAMETER_PROFILES), {"default", "L1", "L2"})
        self.assertEqual(
            (
                PARAMETER_PROFILES["L1"]["primer_min_tm"],
                PARAMETER_PROFILES["L1"]["primer_max_tm"],
                PARAMETER_PROFILES["L1"]["product_min_bp"],
                PARAMETER_PROFILES["L1"]["product_max_bp"],
            ),
            (58.0, 65.0, 70, 180),
        )
        self.assertEqual(
            (
                PARAMETER_PROFILES["L2"]["primer_min_tm"],
                PARAMETER_PROFILES["L2"]["primer_max_tm"],
                PARAMETER_PROFILES["L2"]["product_min_bp"],
                PARAMETER_PROFILES["L2"]["product_max_bp"],
            ),
            (50.0, 65.0, 70, 200),
        )

    def test_ac_par_03_identical_user_and_l1_profiles_run_once(self) -> None:
        runs = build_parameter_runs(
            {
                "user_overrides": {"primer_max_tm": 65.0, "product_max_bp": 180},
                "effective_user_profile": copy.deepcopy(PARAMETER_PROFILES["L1"]),
            },
            DEFAULT_PARAMETERS,
            PARAMETER_PROFILES,
        )
        self.assertEqual(len(runs), 3)
        self.assertIn(["user", "L1"], [run["profile_labels"] for run in runs])

    def test_ac_par_03_unique_user_profile_adds_one_run(self) -> None:
        user = {**DEFAULT_PARAMETERS, "product_min_bp": 90}
        runs = build_parameter_runs(
            {
                "user_overrides": {"product_min_bp": 90},
                "effective_user_profile": user,
            },
            DEFAULT_PARAMETERS,
            PARAMETER_PROFILES,
        )
        self.assertEqual(len(runs), 4)
        self.assertEqual([run["profile_labels"] for run in runs], [["user"], ["default"], ["L1"], ["L2"]])


class WF01V02M2RuleTests(unittest.TestCase):
    def test_ac_rul_01_r01_to_r04_hard_boundaries(self) -> None:
        passing = _rules(valid_candidate())
        for code in ("R01", "R02", "R03", "R04"):
            self.assertEqual(passing[code].status, RuleStatus.PASS)

        failing = _rules(
            valid_candidate(
                amplicon_length=69,
                forward_sequence="ATATATATATATATGCG",
                forward_tm=49.99,
            )
        )
        for code in ("R01", "R02", "R03", "R04"):
            self.assertEqual(failing[code].status, RuleStatus.FAIL)

        above_maximum = _rules(
            valid_candidate(
                forward_tm=65.01,
                reverse_tm=65.01,
                probe_tm=74.01,
            )
        )
        self.assertEqual(above_maximum["R01"].status, RuleStatus.FAIL)

    def test_ac_rul_02_r05_to_r08_are_ordinary_probe_hard_rules(self) -> None:
        passing = _rules(valid_candidate())
        for code in ("R05", "R06", "R07", "R08"):
            self.assertEqual(passing[code].status, RuleStatus.PASS)

        failing = _rules(
            valid_candidate(
                probe_sequence="GACACACACACACACACACAC",
                probe_tm=67.99,
                probe_start=24,
            )
        )
        for code in ("R05", "R06", "R07", "R08"):
            self.assertEqual(failing[code].status, RuleStatus.FAIL)
        self.assertEqual(failing["R13"].status, RuleStatus.NOT_APPLICABLE)

    def test_r07_probe_tm_interval_is_inclusive(self) -> None:
        for probe_tm in (68.0, 69.0, 70.0):
            with self.subTest(probe_tm=probe_tm):
                rules = _rules(
                    valid_candidate(
                        forward_tm=60.0,
                        reverse_tm=60.0,
                        probe_tm=probe_tm,
                    )
                )
                self.assertEqual(rules["R07"].status, RuleStatus.PASS)

        for probe_tm in (67.99, 70.01):
            with self.subTest(probe_tm=probe_tm):
                rules = _rules(
                    valid_candidate(
                        forward_tm=60.0,
                        reverse_tm=60.0,
                        probe_tm=probe_tm,
                    )
                )
                self.assertEqual(rules["R07"].status, RuleStatus.FAIL)

        decimal_boundary = _rules(
            valid_candidate(
                forward_tm=60.1,
                reverse_tm=60.1,
                probe_tm=68.1,
            )
        )
        self.assertEqual(decimal_boundary["R07"].status, RuleStatus.PASS)

    def test_ac_rul_03_r09_r10_dg_boundaries(self) -> None:
        boundary = _rules(
            valid_candidate(
                forward_three_prime_dg=-6.0,
                three_prime_pair_dg=-9.0,
                three_prime_involved=True,
            )
        )
        self.assertEqual(boundary["R09"].status, RuleStatus.PASS)
        self.assertEqual(boundary["R10"].status, RuleStatus.PASS)

        failing = _rules(
            valid_candidate(
                forward_three_prime_dg=-6.001,
                three_prime_pair_dg=-9.001,
                three_prime_involved=True,
            )
        )
        self.assertEqual(failing["R09"].status, RuleStatus.FAIL)
        self.assertEqual(failing["R10"].status, RuleStatus.FAIL)

        not_three_prime = _rules(
            valid_candidate(three_prime_pair_dg=-12.0, three_prime_involved=False)
        )
        self.assertEqual(not_three_prime["R10"].status, RuleStatus.PASS)

    def test_ac_rul_04_hard_failure_is_never_relaxed(self) -> None:
        candidate = valid_candidate(forward_tm=49.99)
        applied = WF01V02RuleEngine().apply(candidate)
        self.assertEqual(applied.verdict, "REJECTED_HARD_RULE")
        self.assertIn("R01", applied.metadata["wf01_v02_rule_summary"]["hard_failed_rules"])

    def test_ac_rul_05_r11_r12_are_retained_as_additive_warnings(self) -> None:
        candidate = valid_candidate(
            forward_tm=54.0,
            reverse_tm=54.0,
            probe_tm=63.0,
            forward_hairpin=-3.001,
            pair_dg=-9.0,
        )
        applied = WF01V02RuleEngine().apply(candidate)
        rules = {rule.code: rule for rule in applied.rules}
        self.assertEqual(rules["R11"].status, RuleStatus.WARNING)
        self.assertEqual(rules["R12"].status, RuleStatus.WARNING)
        self.assertEqual(applied.verdict, "AWAITING_SPECIFICITY")
        self.assertEqual(applied.metadata["wf01_v02_rule_summary"]["fixed_soft_penalty_percent"], 11)

    def test_m2_r13_to_r21_states_do_not_claim_unrun_evidence(self) -> None:
        rules = _rules(valid_candidate())
        for code in ("R13", "R14", "R15", "R17"):
            self.assertEqual(rules[code].status, RuleStatus.NOT_APPLICABLE)
        for code in ("R16", "R19", "R21"):
            self.assertEqual(rules[code].status, RuleStatus.NOT_EVALUATED)
        self.assertEqual(rules["R18"].status, RuleStatus.PASS)
        self.assertEqual(rules["R20"].status, RuleStatus.PASS)


class WF01V02M2ExecutionTests(unittest.TestCase):
    @staticmethod
    def target(*, user_parameters: dict | None = None) -> dict:
        return {
            "target_id": "target_001",
            "template": {"sequence": "ACGT" * 40},
            "parameters": user_parameters
            or {
                "source": "batch_default",
                "user_overrides": {},
                "effective_user_profile": copy.deepcopy(DEFAULT_PARAMETERS),
            },
        }

    def test_ac_scp_01_and_ac_exe_02_target_engine_uses_only_clean_m2_units(self) -> None:
        fake = FakePrimer3Adapter()
        mafft = FakeMafftAdapter()
        engine = WF01V02TargetEngine(
            load_config(),
            default_parameters=DEFAULT_PARAMETERS,
            parameter_profiles=PARAMETER_PROFILES,
            primer3_adapter=fake,
            mafft_adapter=mafft,
        )
        result = engine.run_target(self.target())
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(
            [unit["parameter_profile_labels"] for unit in result["exploration_units"]],
            [["default"], ["L1"], ["L2"]],
        )
        self.assertEqual(result["forbidden_modules_called"], [])
        self.assertTrue(result["ordinary_taqman_only"])
        self.assertFalse(result["search_complete"])
        self.assertEqual(result["specificity_status"], "PENDING_M4")
        self.assertEqual(result["top10_status"], "PENDING_M3")
        self.assertEqual(result["candidate_counts"]["generated"], 3)
        self.assertEqual(result["candidate_counts"]["awaiting_specificity"], 3)
        self.assertEqual(mafft.calls, [])
        self.assertEqual(result["mafft_status"], "SKIPPED_SINGLE_SEQUENCE")
        self.assertEqual(result["conservation"]["status"], "NOT_EVALUATED")
        self.assertEqual(
            result["conservation"]["reason"],
            "SINGLE_SEQUENCE_NO_POPULATION_CONSERVATION",
        )
        single_candidate = result["hard_rule_pass_candidates"][0]
        self.assertIsNone(single_candidate["conservation_score"])
        self.assertEqual(
            single_candidate["metadata"]["conservation_evidence"]["status"],
            "NOT_EVALUATED",
        )
        self.assertEqual(
            next(
                rule
                for rule in single_candidate["rules"]
                if rule["code"] == "R16"
            )["status"],
            "NOT_EVALUATED",
        )
        self.assertEqual(result["alignment"]["raw_tool_output"]["tool"], "MAFFT")
        self.assertTrue(
            all(
                unit["primer3_raw_output"]["tool"] == "Primer3"
                and unit["primer3_raw_output"]["raw_output"]
                for unit in result["exploration_units"]
            )
        )

    def test_multiple_templates_run_mafft_then_conserved_region_then_primer3(self) -> None:
        primer3 = FakePrimer3Adapter()
        mafft = FakeMafftAdapter()
        engine = WF01V02TargetEngine(
            load_config(),
            default_parameters=DEFAULT_PARAMETERS,
            parameter_profiles=PARAMETER_PROFILES,
            primer3_adapter=primer3,
            mafft_adapter=mafft,
        )
        target = self.target()
        target["templates"] = [
            {"record_name": "strain-a", "sequence": "ACGT" * 40},
            {"record_name": "strain-b", "sequence": "ACGT" * 40},
        ]

        result = engine.run_target(target)

        self.assertEqual(len(mafft.calls), 1)
        self.assertEqual([record.id for record in mafft.calls[0]], ["strain-a", "strain-b"])
        self.assertEqual(result["mafft_status"], "COMPLETED")
        self.assertEqual(result["alignment"]["sequence_count"], 2)
        self.assertEqual(result["alignment"]["method"], "mafft_shannon")
        self.assertEqual(
            len(result["alignment"]["raw_tool_output"]["input_records"]),
            2,
        )
        self.assertEqual(
            len(result["alignment"]["raw_tool_output"]["aligned_records"]),
            2,
        )
        self.assertEqual(result["conservation"]["method"], "mafft_shannon")
        self.assertEqual(result["conservation"]["status"], "EVALUATED")
        self.assertTrue(result["conservation"]["regions"])
        self.assertEqual(len(primer3.calls), 3)
        self.assertTrue(all(len(call["template"]) == 160 for call in primer3.calls))
        self.assertTrue(
            all(
                unit["stage"] == "M2_CONSERVED_REGION"
                and unit["region"]["type"] == "CONSERVED_REGION"
                for unit in result["exploration_units"]
            )
        )
        candidate = result["hard_rule_pass_candidates"][0]
        self.assertEqual(candidate["conservation_method"], "mafft_shannon")
        self.assertEqual(candidate["conservation_sequence_count"], 2)
        self.assertEqual(candidate["metadata"]["conservation_status"], "EVALUATED")
        evidence = candidate["metadata"]["conservation_evidence"]
        self.assertEqual(evidence["score"], 1.0)
        self.assertEqual(evidence["coverage_percent"], 100.0)
        self.assertEqual(evidence["confidence"], "LOW")
        self.assertEqual(set(evidence["binding_sites"]), {"forward_primer", "reverse_primer", "probe"})
        self.assertEqual(
            next(rule for rule in candidate["rules"] if rule["code"] == "R16")["status"],
            "WARNING",
        )

    def test_development_alignment_fallback_never_claims_conservation(self) -> None:
        primer3 = FakePrimer3Adapter()
        mafft = DevelopmentFallbackMafftAdapter()
        engine = WF01V02TargetEngine(
            load_config(),
            default_parameters=DEFAULT_PARAMETERS,
            parameter_profiles=PARAMETER_PROFILES,
            primer3_adapter=primer3,
            mafft_adapter=mafft,
        )
        target = self.target()
        target["templates"] = [
            {"record_name": "strain-a", "sequence": "ACGT" * 40},
            {"record_name": "strain-b", "sequence": "ACGT" * 40},
        ]

        result = engine.run_target(target)

        self.assertEqual(result["mafft_status"], "DEVELOPMENT_FALLBACK")
        self.assertEqual(result["alignment"]["status"], "NOT_EVALUATED")
        self.assertEqual(result["conservation"]["status"], "NOT_EVALUATED")
        self.assertTrue(result["conservation"]["full_length_fallback"])
        candidate = result["hard_rule_pass_candidates"][0]
        evidence = candidate["metadata"]["conservation_evidence"]
        self.assertEqual(evidence["status"], "NOT_EVALUATED")
        self.assertIsNone(evidence["score"])

    def test_ac_exe_01_queued_batch_runs_m2_and_remains_exploring(self) -> None:
        payload = {
            "fasta_text": ">one\n" + "ACGT" * 40,
            "batch_defaults": {
                "target_organism": {"name": "Synthetic test virus"},
                "hosts": [{"name": "pig"}],
            },
        }
        with tempfile.TemporaryDirectory(prefix="agentd-m2-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            created = service.create_wf01_v02_batch(payload)
            fake = FakePrimer3Adapter()
            with patch("agentd.wf01_v02_engine.Primer3Adapter", return_value=fake):
                executed = service.execute_wf01_v02_m2(created["batch_id"])

            self.assertEqual(executed["status"], "EXPLORING")
            self.assertEqual(executed["m2_outcome"], "COMPLETED")
            self.assertFalse(executed["search_complete"])
            self.assertEqual(executed["targets"][0]["status"], "EXPLORING")
            self.assertEqual(
                executed["targets"][0]["m2_execution"]["specificity_status"],
                "PENDING_M4",
            )
            self.assertEqual(
                [event["event_seq"] for event in executed["events"]],
                list(range(1, len(executed["events"]) + 1)),
            )
            persisted = service.get_wf01_v02_batch(created["batch_id"])
            self.assertEqual(persisted["events"][-1]["event_type"], "M2_EXECUTION_COMPLETED")
            self.assertNotIn(
                "raw_tool_output",
                persisted["targets"][0]["m2_execution"]["alignment"],
            )
            self.assertTrue(
                all(
                    "primer3_raw_output" not in unit
                    for unit in persisted["targets"][0]["m2_execution"][
                        "exploration_units"
                    ]
                )
            )
            artifacts = service.get_wf01_v02_scientific_artifacts(
                created["batch_id"]
            )
            self.assertEqual(
                {item["stage"] for item in artifacts["artifacts"]},
                {"M1", "M2"},
            )
            m2_artifact = next(
                item for item in artifacts["artifacts"] if item["stage"] == "M2"
            )
            artifact_path = service.get_wf01_v02_scientific_artifact_path(
                created["batch_id"],
                m2_artifact["artifactId"],
            )
            raw_document = json.loads(
                artifact_path.read_text(encoding="utf-8")
            )
            self.assertEqual(
                raw_document["dataScope"],
                "RAW_SCIENTIFIC_STAGE_OUTPUT",
            )
            self.assertIn(
                "raw_tool_output",
                raw_document["rawData"]["alignment"],
            )
            self.assertTrue(
                all(
                    "primer3_raw_output" in unit
                    for unit in raw_document["rawData"]["exploration_units"]
                )
            )

    def test_m2_rejects_pending_confirmation_and_rerun(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-m2-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            pending = service.create_wf01_v02_batch(
                {
                    "fasta_text": ">one\n" + "ACGT" * 19 + "ACG",
                    "batch_defaults": {
                        "target_organism": {"name": "Synthetic test virus"},
                        "hosts": [{"name": "pig"}],
                    },
                }
            )
            with self.assertRaisesRegex(Exception, "只有 QUEUED"):
                service.execute_wf01_v02_m2(pending["batch_id"])

            ready = service.create_wf01_v02_batch(
                {
                    "fasta_text": ">two\n" + "ACGT" * 40,
                    "batch_defaults": {
                        "target_organism": {"name": "Synthetic test virus"},
                        "hosts": [{"name": "pig"}],
                    },
                }
            )
            with patch(
                "agentd.wf01_v02_engine.Primer3Adapter",
                return_value=FakePrimer3Adapter(),
            ):
                service.execute_wf01_v02_m2(ready["batch_id"])
            with self.assertRaisesRegex(Exception, "只有 QUEUED"):
                service.execute_wf01_v02_m2(ready["batch_id"])


@unittest.skipUnless(importlib.util.find_spec("fastapi") is not None, "FastAPI extra not installed")
class WF01V02M2ApiTests(unittest.TestCase):
    def test_run_m2_endpoint_executes_and_persists_batch(self) -> None:
        from fastapi.testclient import TestClient
        from agentd import api

        payload = {
            "fasta_text": ">api_target\n" + "ACGT" * 40,
            "batch_defaults": {
                "target_organism": {"name": "Synthetic test virus"},
                "hosts": [{"name": "pig"}],
            },
        }
        with tempfile.TemporaryDirectory(prefix="agentd-m2-api-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            with (
                patch.object(api, "service", service),
                patch(
                    "agentd.wf01_v02_engine.Primer3Adapter",
                    return_value=FakePrimer3Adapter(),
                ),
            ):
                client = TestClient(api.app)
                created = client.post("/v1/wf01/tasks", json=payload)
                self.assertEqual(created.status_code, 200)
                batch_id = created.json()["batch_id"]
                executed = client.post(f"/v1/wf01/tasks/{batch_id}/run-m2", json={})
                self.assertEqual(executed.status_code, 200)
                self.assertEqual(executed.json()["status"], "EXPLORING")
                fetched = client.get(f"/v1/wf01/tasks/{batch_id}")
                self.assertEqual(fetched.json()["events"][-1]["event_type"], "M2_EXECUTION_COMPLETED")


if __name__ == "__main__":
    unittest.main()

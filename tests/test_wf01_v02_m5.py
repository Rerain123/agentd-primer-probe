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

from agentd.service import AgentDService
from agentd.wf01_v02_m5 import WF01V02M5Engine


def candidate(
    index: int,
    *,
    qualified: bool = True,
    database_reason: str | None = None,
) -> dict:
    value = {
        "candidate_id": f"target_001-candidate-{index + 1}",
        "dedupe_key": f"dedupe-{index:03d}",
        "primer3_pair_penalty": float(index),
        "score": round(95.0 - index, 3) if qualified else None,
        "rank": index + 1 if qualified else None,
        "verdict": "PASS" if qualified else "SPECIFICITY_NOT_EVALUATED",
        "metadata": {"pre_specificity_rank": index + 1},
    }
    if not qualified:
        reason = database_reason or "TARGET_DATABASE_OR_VERSION_NOT_RESOLVED"
        value["specificity"] = {
            "overall_status": "NOT_EVALUATED",
            "target_hit": {
                "mode": "TARGET_HIT",
                "status": "NOT_EVALUATED",
                "objects": [
                    {
                        "object_id": "target_001:target:1",
                        "original_input": {"name": "Synthetic test virus"},
                    }
                ],
                "database": {
                    "resource_key": "target:fixture:blast",
                    "version": None,
                },
                "reason": reason,
                "error": None,
            },
            "host_exclusion": {
                "mode": "HOST_EXCLUSION",
                "status": "PASS",
                "object_results": [],
            },
            "related_background_nt": {
                "mode": "RELATED_BACKGROUND_NT",
                "status": "NOT_APPLICABLE",
            },
        }
    return value


def target(
    *,
    target_id: str = "target_001",
    qualified_count: int = 0,
    incomplete_count: int = 0,
    search_complete: bool = False,
    technical_search_complete: bool | None = None,
    m4_stage: str = "M4_COMPLETE_AWAITING_M5",
) -> dict:
    qualified = [candidate(index) for index in range(qualified_count)]
    incomplete = [
        candidate(qualified_count + index, qualified=False)
        for index in range(incomplete_count)
    ]
    technical = (
        search_complete
        if technical_search_complete is None
        else technical_search_complete
    )
    return {
        "target_id": target_id,
        "status": "EXPLORING",
        "m2_execution": {
            "exploration_manifest": {
                "units": [
                    {
                        "exploration_unit_id": f"{target_id}-unit-001",
                        "status": "COMPLETED",
                        "tool_error": None,
                    }
                ]
            }
        },
        "m3_execution": {
            "stage": "M3_COMPLETE_AWAITING_M4",
            "primer3_search_complete": technical,
            "all_hard_rule_pass_candidates": copy.deepcopy(
                qualified + incomplete
            ),
        },
        "m4_execution": {
            "stage": m4_stage,
            "target_id": target_id,
            "search_complete": search_complete,
            "technical_search_complete": technical,
            "search_incomplete_reason": (
                None
                if search_complete
                else "SPECIFICITY_OR_PRIMER3_INCOMPLETE"
            ),
            "all_qualified_candidates": qualified,
            "all_specificity_incomplete_candidates": incomplete,
            "all_specificity_failed_candidates": [],
            "hard_rule_rejected_candidates": [],
            "hard_rule_incomplete_candidates": [],
        },
    }


class WF01V02M5TargetStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = WF01V02M5Engine()

    def test_ac_sta_01_four_target_statuses_follow_search_and_count_matrix(
        self,
    ) -> None:
        cases = [
            (
                target(qualified_count=10, search_complete=True),
                "SUCCEEDED",
                10,
            ),
            (
                target(qualified_count=9, search_complete=True),
                "PARTIAL",
                9,
            ),
            (
                target(
                    qualified_count=10,
                    search_complete=False,
                    technical_search_complete=False,
                ),
                "PARTIAL_INCOMPLETE",
                10,
            ),
            (
                target(qualified_count=0, search_complete=True),
                "FAILED",
                0,
            ),
        ]
        for input_target, expected_status, expected_count in cases:
            with self.subTest(expected_status=expected_status):
                result = self.engine.run_target(input_target)
                self.assertEqual(result["status"], expected_status)
                self.assertEqual(
                    result["candidate_counts"]["deliverable"],
                    expected_count,
                )
                self.assertLessEqual(len(result["top10"]), 10)
                self.assertEqual(
                    result["search_complete"],
                    expected_status in {"SUCCEEDED", "PARTIAL", "FAILED"},
                )

    def test_ac_sta_01_incomplete_specificity_candidate_remains_deliverable(
        self,
    ) -> None:
        result = self.engine.run_target(
            target(
                incomplete_count=1,
                search_complete=False,
                technical_search_complete=False,
            )
        )
        self.assertEqual(result["status"], "PARTIAL_INCOMPLETE")
        self.assertEqual(result["candidate_counts"]["deliverable"], 1)
        self.assertEqual(result["candidate_counts"]["qualified"], 0)
        self.assertEqual(result["candidate_counts"]["incomplete_evidence"], 1)
        selected = result["top10"][0]
        self.assertIsNone(selected["score"])
        self.assertIsNone(selected["rank"])
        self.assertEqual(
            selected["metadata"]["m5_delivery"]["status"],
            "INCOMPLETE_EVIDENCE",
        )
        self.assertEqual(
            selected["metadata"]["m5_delivery"]["selection_basis"],
            "M3_PRE_SPECIFICITY_PRIORITY",
        )
        self.assertIn(
            "TARGET_DATABASE_OR_VERSION_NOT_RESOLVED",
            result["incomplete_reasons"],
        )
        self.assertIn(
            "CONFIGURE_VERSIONED_SPECIFICITY_DATABASES",
            {
                recommendation["action"]
                for recommendation in result["recommendations"]
            },
        )

    def test_ac_sta_01_zero_candidates_is_failed_even_when_search_complete(
        self,
    ) -> None:
        result = self.engine.run_target(
            target(qualified_count=0, search_complete=True)
        )
        self.assertEqual(result["status"], "FAILED")
        self.assertTrue(result["search_complete"])
        self.assertEqual(
            result["status_reason"],
            "NO_DELIVERABLE_CANDIDATES",
        )
        self.assertIn(
            "REDESIGN_WITH_UNCHANGED_HARD_THRESHOLDS",
            {
                recommendation["action"]
                for recommendation in result["recommendations"]
            },
        )

    def test_design_only_score_and_rank_remain_visible_in_delivery(self) -> None:
        input_target = target(
            incomplete_count=2,
            search_complete=False,
            technical_search_complete=False,
        )
        candidates = input_target["m4_execution"][
            "all_specificity_incomplete_candidates"
        ]
        for rank, value in enumerate(candidates, start=1):
            value["score"] = 91.0 - rank
            value["rank"] = rank
            value.setdefault("metadata", {})["wf01_v02_scoring"] = {
                "status": "FINAL_DESIGN_ONLY",
                "score_type": "DESIGN_SCORE",
                "score_label": "设计评分",
                "final_score": value["score"],
            }

        result = self.engine.run_target(input_target)

        self.assertEqual(
            [value["score"] for value in result["top10"]],
            [90.0, 89.0],
        )
        self.assertEqual(
            [value["rank"] for value in result["top10"]],
            [1, 2],
        )
        self.assertTrue(
            all(
                value["metadata"]["m5_delivery"]["selection_basis"]
                == "M4_DESIGN_SCORE"
                for value in result["top10"]
            )
        )

    def test_scoring_gap_cannot_be_declared_complete(self) -> None:
        input_target = target(qualified_count=1, search_complete=True)
        input_target["m4_execution"]["all_qualified_candidates"][0]["score"] = None
        result = self.engine.run_target(input_target)
        self.assertEqual(result["status"], "PARTIAL_INCOMPLETE")
        self.assertFalse(result["search_complete"])
        self.assertEqual(result["candidate_counts"]["incomplete_evidence"], 1)
        self.assertIn(
            "M4_QUALIFIED_CANDIDATE_SCORE_OR_RANK_MISSING",
            result["incomplete_reasons"],
        )


class WF01V02M5InterruptionTests(unittest.TestCase):
    def test_ac_sta_03_interruption_records_completed_and_incomplete_scope(
        self,
    ) -> None:
        input_target = target(qualified_count=2)
        input_target["m2_execution"]["exploration_manifest"]["units"].append(
            {
                "exploration_unit_id": "target_001-unit-002",
                "status": "TOOL_ERROR",
                "tool_error": {
                    "code": "TOOL_UNAVAILABLE",
                    "message": "Primer3 interrupted",
                },
            }
        )
        input_target["m3_execution"]["primer3_search_complete"] = False
        input_target["m4_execution"] = {
            "stage": "M4_BLOCKED",
            "search_complete": False,
            "technical_search_complete": False,
            "search_incomplete_reason": "M4_INPUT_INCOMPLETE",
            "error": {
                "code": "TOOL_UNAVAILABLE",
                "message": "BLAST interrupted",
                "details": {},
            },
        }
        result = WF01V02M5Engine().run_target(input_target)
        self.assertEqual(result["status"], "PARTIAL_INCOMPLETE")
        self.assertEqual(result["candidate_counts"]["deliverable"], 2)
        self.assertIn(
            "EXPLORATION_UNIT",
            {item["scope_type"] for item in result["incomplete_scope"]},
        )
        self.assertIn(
            "STAGE",
            {item["scope_type"] for item in result["incomplete_scope"]},
        )
        self.assertTrue(
            any(item["error"] for item in result["incomplete_scope"])
        )
        actions = {
            recommendation["action"]
            for recommendation in result["recommendations"]
        }
        self.assertIn("RETRY_AFTER_TOOL_RECOVERY", actions)
        self.assertIn("RETRY_INCOMPLETE_EXPLORATION_UNITS", actions)

    def test_ac_spe_03_not_evaluated_never_becomes_complete_success(self) -> None:
        result = WF01V02M5Engine().run_target(
            target(
                incomplete_count=12,
                search_complete=False,
                technical_search_complete=False,
            )
        )
        self.assertEqual(result["status"], "PARTIAL_INCOMPLETE")
        self.assertFalse(result["search_complete"])
        self.assertEqual(len(result["top10"]), 10)
        self.assertTrue(
            all(
                item["metadata"]["m5_delivery"]["status"]
                == "INCOMPLETE_EVIDENCE"
                for item in result["top10"]
            )
        )


class WF01V02M5BatchStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = WF01V02M5Engine()

    def result(
        self,
        *,
        target_id: str,
        qualified_count: int,
        search_complete: bool,
    ) -> dict:
        return self.engine.run_target(
            target(
                target_id=target_id,
                qualified_count=qualified_count,
                search_complete=search_complete,
                technical_search_complete=search_complete,
            )
        )

    def test_ac_sta_02_all_successful_targets_make_successful_batch(self) -> None:
        summary = self.engine.summarize_batch(
            [
                self.result(
                    target_id="target_001",
                    qualified_count=10,
                    search_complete=True,
                ),
                self.result(
                    target_id="target_002",
                    qualified_count=12,
                    search_complete=True,
                ),
            ]
        )
        self.assertEqual(summary["status"], "SUCCEEDED")
        self.assertEqual(summary["target_counts"]["succeeded"], 2)
        self.assertTrue(summary["search_complete"])

    def test_ac_exe_03_and_sta_02_mixed_targets_are_partially_successful(
        self,
    ) -> None:
        succeeded = self.result(
            target_id="target_001",
            qualified_count=10,
            search_complete=True,
        )
        failed = self.result(
            target_id="target_002",
            qualified_count=0,
            search_complete=True,
        )
        summary = self.engine.summarize_batch([succeeded, failed])
        self.assertEqual(summary["status"], "PARTIALLY_SUCCEEDED")
        self.assertEqual(summary["failed_target_ids"], ["target_002"])
        self.assertEqual(summary["target_counts"]["deliverable"], 1)
        self.assertEqual(len(summary["target_status_matrix"]), 2)

    def test_ac_sta_02_all_failed_targets_make_failed_batch(self) -> None:
        summary = self.engine.summarize_batch(
            [
                self.result(
                    target_id="target_001",
                    qualified_count=0,
                    search_complete=True,
                ),
                self.result(
                    target_id="target_002",
                    qualified_count=0,
                    search_complete=False,
                ),
            ]
        )
        self.assertEqual(summary["status"], "FAILED")
        self.assertEqual(summary["target_counts"]["failed"], 2)
        self.assertEqual(
            summary["status_reason"],
            "NO_TARGET_HAS_DELIVERABLE_CANDIDATES",
        )


@unittest.skipUnless(
    importlib.util.find_spec("fastapi") is not None,
    "FastAPI extra not installed",
)
class WF01V02M5ApiTests(unittest.TestCase):
    def test_run_m5_endpoint_persists_status_matrix_and_rejects_rerun(
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
        with tempfile.TemporaryDirectory(prefix="agentd-m5-api-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            created = service.create_wf01_v02_batch(payload)
            batch = service.get_wf01_v02_batch(created["batch_id"])
            completed_target = target(
                qualified_count=10,
                search_complete=True,
                technical_search_complete=True,
            )
            completed_target["target_object"] = batch["targets"][0][
                "target_object"
            ]
            completed_target["host_objects"] = batch["targets"][0][
                "host_objects"
            ]
            batch["status"] = "EXPLORING"
            batch["m2_outcome"] = "COMPLETED"
            batch["m3_outcome"] = "COMPLETED"
            batch["m4_outcome"] = "COMPLETED"
            batch["targets"] = [completed_target]
            service.wf01_v02.store.save(batch)

            with patch.object(api, "service", service):
                client = TestClient(api.app)
                response = client.post(
                    f"/v1/wf01/tasks/{created['batch_id']}/run-m5",
                    json={},
                )
                self.assertEqual(response.status_code, 200)
                result = response.json()
                self.assertEqual(result["status"], "SUCCEEDED")
                self.assertEqual(result["m5_outcome"], "COMPLETED")
                self.assertEqual(
                    result["targets"][0]["status"],
                    "SUCCEEDED",
                )
                self.assertEqual(
                    result["target_status_matrix"][0]["target_id"],
                    "target_001",
                )
                self.assertTrue(result["search_complete"])
                self.assertEqual(
                    result["events"][-1]["event_type"],
                    "M5_SUMMARIZATION_COMPLETED",
                )
                self.assertEqual(
                    [event["event_seq"] for event in result["events"]],
                    list(range(1, len(result["events"]) + 1)),
                )
                rerun = client.post(
                    f"/v1/wf01/tasks/{created['batch_id']}/run-m5",
                    json={},
                )
                self.assertEqual(rerun.status_code, 422)

                capabilities = client.get("/v1/capabilities").json()[
                    "wf01_prd_v0_2"
                ]
                self.assertEqual(
                    capabilities["status"],
                    "M7_VERSIONED_REDESIGN_AND_REUSE",
                )
                self.assertEqual(
                    capabilities["endpoints"]["execute_m5"],
                    "POST /v1/wf01/tasks/{batch_id}/run-m5",
                )

    def test_incomplete_evidence_batch_is_partially_succeeded_and_preserved(
        self,
    ) -> None:
        payload = {
            "fasta_text": ">incomplete\n" + "ACGT" * 40,
            "batch_defaults": {
                "target_organism": {"name": "Synthetic test virus"},
                "hosts": [{"name": "pig"}],
            },
        }
        with tempfile.TemporaryDirectory(prefix="agentd-m5-incomplete-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            created = service.create_wf01_v02_batch(payload)
            batch = service.get_wf01_v02_batch(created["batch_id"])
            incomplete_target = target(
                incomplete_count=2,
                search_complete=False,
                technical_search_complete=False,
            )
            batch["status"] = "EXPLORING"
            batch["m4_outcome"] = "COMPLETED"
            batch["targets"] = [incomplete_target]
            service.wf01_v02.store.save(batch)

            result = service.execute_wf01_v02_m5(created["batch_id"])
            self.assertEqual(result["status"], "PARTIALLY_SUCCEEDED")
            self.assertEqual(
                result["targets"][0]["status"],
                "PARTIAL_INCOMPLETE",
            )
            self.assertEqual(len(result["targets"][0]["top10"]), 2)
            self.assertFalse(result["search_complete"])
            self.assertTrue(result["recommendations"])
            self.assertFalse(result["agent_interactions"])
            notice = result["targets"][0]["m5_execution"][
                "candidate_notice"
            ]
            self.assertEqual(notice["status"], "INSUFFICIENT")
            self.assertEqual(notice["code"], "INSUFFICIENT_CANDIDATES")
            self.assertIn("候选不足", notice["message"])

    def test_zero_candidates_auto_generates_report_with_reason(self) -> None:
        payload = {
            "fasta_text": ">zero_candidates\n" + "ACGT" * 40,
            "batch_defaults": {
                "target_organism": {"name": "Synthetic test virus"},
                "hosts": [{"name": "pig"}],
            },
        }
        with tempfile.TemporaryDirectory(prefix="agentd-m5-zero-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            created = service.create_wf01_v02_batch(payload)
            batch = service.get_wf01_v02_batch(created["batch_id"])
            zero_result = target(
                qualified_count=0,
                search_complete=True,
                technical_search_complete=True,
            )
            batch_target = batch["targets"][0]
            for key in ("status", "m2_execution", "m3_execution", "m4_execution"):
                batch_target[key] = copy.deepcopy(zero_result[key])
            batch["status"] = "EXPLORING"
            batch["m2_outcome"] = "COMPLETED"
            batch["m3_outcome"] = "COMPLETED"
            batch["m4_outcome"] = "COMPLETED"
            service.wf01_v02.store.save(batch)

            after_m5 = service.execute_wf01_v02_m5(created["batch_id"])
            self.assertEqual(after_m5["status"], "FAILED")
            self.assertFalse(after_m5["agent_interactions"])
            notice = after_m5["targets"][0]["m5_execution"][
                "candidate_notice"
            ]
            self.assertEqual(notice["code"], "NO_DELIVERABLE_CANDIDATES")
            self.assertIn("PRIMER3_GENERATED_NO_CANDIDATES", notice["reason_codes"])
            self.assertIn("已生成空结果报告", notice["message"])

            completed = service.run_wf01_v02_until_blocked(created["batch_id"])
            self.assertEqual(completed["executed_stages"], ["M6"])
            self.assertEqual(completed["next_action"], "COMPLETE")
            self.assertEqual(completed["candidate_counts"]["deliverable"], 0)
            self.assertEqual(
                completed["ui_message"]["data"]["status"],
                "COMPLETED",
            )
            self.assertTrue(
                any(
                    "未获得可交付候选" in issue
                    for issue in completed["ui_message"]["data"]["issues"]
                )
            )

    def test_m5_requires_completed_m4(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-m5-guard-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            created = service.create_wf01_v02_batch(
                {
                    "fasta_text": ">guard\n" + "ACGT" * 40,
                    "batch_defaults": {
                        "target_organism": {"name": "Synthetic test virus"},
                        "hosts": [{"name": "pig"}],
                    },
                }
            )
            with self.assertRaisesRegex(Exception, "只有已完成 M4"):
                service.execute_wf01_v02_m5(created["batch_id"])

    def test_m5_rejects_non_object_options(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-m5-options-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            with self.assertRaisesRegex(Exception, "运行选项必须为对象"):
                service.execute_wf01_v02_m5("unused", [])


if __name__ == "__main__":
    unittest.main()

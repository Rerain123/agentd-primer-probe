from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.service import AgentDService
from agentd.wf01_agent_view import (
    compact_candidate,
    next_action_for_batch,
    next_stage_for_batch,
    serialized_size_bytes,
    summarize_wf01_batch,
)


def _candidate(index: int) -> dict:
    rules = [
        {
            "code": f"R{rule_index:02d}",
            "category": "hard",
            "status": "PASS" if rule_index % 7 else "WARNING",
            "actual": {"value": index + rule_index},
            "threshold": "a repeated scientific threshold " * 5,
            "source": "WF01-v0.2 repeated source",
            "message": "",
        }
        for rule_index in range(1, 25)
    ]
    return {
        "candidate_id": f"candidate-{index:03d}",
        "verdict": "AWAITING_SPECIFICITY",
        "forward_primer": {
            "sequence_5to3": "ACGT" * 5,
            "length_nt": 20,
            "tm_celsius": 60.0,
            "gc_percent": 50.0,
        },
        "reverse_primer": {
            "sequence_5to3": "TGCA" * 5,
            "length_nt": 20,
            "tm_celsius": 60.0,
            "gc_percent": 50.0,
        },
        "probe": {
            "sequence_5to3": "AC" * 12,
            "length_nt": 24,
            "tm_celsius": 69.0,
            "gc_percent": 50.0,
        },
        "amplicon": {
            "sequence_5to3": "ACGT" * 25,
            "length_bp": 100,
        },
        "metadata": {"large_repeated_value": "metadata" * 100},
        "rules": rules,
    }


def _large_batch() -> dict:
    candidates = [_candidate(index) for index in range(150)]
    return {
        "batch_id": "batch-large",
        "workflow": "WF-01",
        "task_version": 1,
        "status": "EXPLORING",
        "target_total": 1,
        "target_counts": {"exploring": 1},
        "m2_outcome": "COMPLETED",
        "targets": [
            {
                "target_id": "target_001",
                "ordinal": 1,
                "status": "EXPLORING",
                "candidate_counts": {
                    "generated": 150,
                    "hard_rule_pass": 150,
                },
                "m2_execution": {
                    "hard_rule_pass_candidates": candidates,
                    "hard_rule_rejected_candidates": [],
                },
            }
        ],
        "agent_interactions": [],
    }


class WF01AgentViewTests(unittest.TestCase):
    def test_route_message_uses_backend_execution_path(self) -> None:
        design_only = _large_batch()
        design_only["targets"][0]["execution_path"] = "DESIGN_ONLY"
        design_only["targets"][0]["specificity_status"] = (
            "NOT_EVALUATED_MISSING_ORGANISM_CONTEXT"
        )

        design_summary = summarize_wf01_batch(design_only)

        self.assertEqual(design_summary["route_summary"]["mode"], "DESIGN_ONLY")
        self.assertIn("基础设计路线", design_summary["route_message"])
        self.assertIn("特异性未评估", design_summary["route_message"])

        full = _large_batch()
        target = full["targets"][0]
        target["execution_path"] = "FULL_SPECIFICITY"
        target["target_object"] = {
            "original_input": {"name": "猪瘟病毒"},
            "normalized_name": "猪瘟病毒",
        }
        target["host_objects"] = [
            {
                "original_input": {"name": "猪"},
                "normalized_name": "pig",
            }
        ]
        target["m4_execution"] = {
            "specificity_execution_status": "COMPLETED"
        }

        full_summary = summarize_wf01_batch(full)

        self.assertEqual(
            full_summary["route_summary"]["mode"],
            "FULL_SPECIFICITY",
        )
        self.assertEqual(
            full_summary["route_summary"]["target_organisms"],
            ["猪瘟病毒"],
        )
        self.assertEqual(full_summary["route_summary"]["hosts"], ["猪"])
        self.assertIn("完整特异性路线完成", full_summary["route_message"])
        self.assertIn("靶标命中、宿主排除和综合背景数据库评估", full_summary["route_message"])

        target["host_objects"] = []
        no_host_summary = summarize_wf01_batch(full)
        self.assertEqual(
            no_host_summary["route_summary"]["mode"],
            "FULL_SPECIFICITY",
        )
        self.assertEqual(no_host_summary["route_summary"]["hosts"], [])
        self.assertIn(
            "靶标命中和综合背景数据库评估",
            no_host_summary["route_message"],
        )
        self.assertIn(
            "宿主专库排除不适用",
            no_host_summary["route_message"],
        )

    def test_large_candidate_pool_is_not_copied_into_agent_summary(self) -> None:
        batch = _large_batch()
        batch["targets"][0]["m5_execution"] = {
            "top10": [_candidate(index) for index in range(10)],
        }
        full_size = serialized_size_bytes(batch)
        summary = summarize_wf01_batch(batch)

        self.assertGreater(full_size, 1_000_000)
        self.assertLess(serialized_size_bytes(summary), 16_000)
        self.assertEqual(summary["next_action"], "RUN_M3")
        self.assertEqual(len(summary["targets"][0]["top_candidates"]), 10)
        self.assertNotIn("m2_execution", summary["targets"][0])
        self.assertNotIn("hard_rule_pass_candidates", str(summary))

    def test_candidate_rules_are_collapsed_unless_explicitly_requested(self) -> None:
        candidate = _candidate(1)
        compact = compact_candidate(candidate)
        detailed = compact_candidate(candidate, include_rule_details=True)

        self.assertNotIn("rules", compact)
        self.assertEqual(compact["rule_summary"]["total"], 24)
        self.assertEqual(len(detailed["rules"]), 24)

    def test_waiting_interaction_is_a_hard_boundary(self) -> None:
        batch = _large_batch()
        batch["agent_interactions"] = [
            {
                "data": {
                    "interactionId": "interaction-1",
                    "status": "WAITING_USER",
                }
            }
        ]
        self.assertEqual(next_action_for_batch(batch), "WAIT_FOR_USER")

    def test_running_stage_is_an_in_flight_hard_boundary(self) -> None:
        batch = _large_batch()
        batch["status"] = "RUNNING"
        batch["m3_outcome"] = "COMPLETED"

        self.assertIsNone(next_stage_for_batch(batch))
        self.assertEqual(
            next_action_for_batch(batch),
            "WAIT_FOR_STAGE_COMPLETION",
        )
        summary = summarize_wf01_batch(batch)
        self.assertEqual(summary["status"], "RUNNING")
        self.assertEqual(
            summary["next_action"],
            "WAIT_FOR_STAGE_COMPLETION",
        )
        self.assertEqual(
            summary["conversation_context"]["lifecycleState"],
            "IN_PROGRESS",
        )
        self.assertEqual(
            summary["flowControl"],
            {
                "action": "WAIT_FOR_STAGE_COMPLETION",
                "stopCurrentTurn": True,
                "retryInCurrentTurn": False,
            },
        )

    def test_missing_html_defaults_to_top10_and_offers_all_on_request(self) -> None:
        batch = _large_batch()
        deliverable = [_candidate(index) for index in range(33)]
        batch["targets"][0]["m5_execution"] = {
            "all_deliverable_candidates": deliverable,
        }
        batch["m5_outcome"] = "COMPLETED"
        batch["m6_outcome"] = "COMPLETED"
        batch["m6_execution"] = {
            "exports": {},
            "export_status": "FAILED",
            "export_failures": {
                "html": {
                    "code": "M6_HTML_EXPORT_FAILED",
                    "message": "simulated",
                }
            },
        }

        summary = summarize_wf01_batch(batch)
        fallback = summary["text_fallback"]
        target = fallback["targets"][0]
        self.assertEqual(fallback["default_candidate_scope"], "top10")
        self.assertEqual(target["candidate_total"], 33)
        self.assertEqual(len(target["default_results"]), 10)
        self.assertEqual(target["all_candidates_on_request"]["page_count"], 2)
        self.assertEqual(
            target["all_candidates_on_request"]["arguments"]["candidate_scope"],
            "deliverable",
        )
        self.assertEqual(
            target["all_candidates_on_request"]["condition"],
            "USER_EXPLICITLY_REQUESTS_ALL_CANDIDATES",
        )

    def test_completed_summary_exposes_only_flat_artifact_urls(self) -> None:
        batch = _large_batch()
        batch["m5_outcome"] = "COMPLETED"
        batch["m6_outcome"] = "COMPLETED"
        batch["m6_execution"] = {
            "exports": {
                "html": {
                    "filename": "CSFV_引物探针设计报告.html",
                    "file_url": "/data/CSFV_引物探针设计报告.html",
                    "download_endpoint": "/v1/wf01/tasks/batch/exports/html",
                },
                "rules_html": {
                    "filename": "legacy-rules.html",
                    "file_url": "/data/legacy-rules.html",
                },
            },
            "export_status": "COMPLETE",
            "export_failures": {},
        }

        summary = summarize_wf01_batch(batch)
        self.assertRegex(
            summary["batch_code"],
            r"^WF01-\d{6}-\d{4}-[A-F0-9]{4}$",
        )
        self.assertEqual(
            summary["artifacts"],
            {"htmlUrl": "/data/CSFV_引物探针设计报告.html"},
        )
        self.assertEqual(summary["report"]["generated_formats"], ["html"])
        self.assertNotIn("rulesHtmlUrl", summary["artifacts"])
        self.assertNotIn("exports", summary["report"])
        context = summary["conversation_context"]
        self.assertEqual(context["activeBatchId"], batch["batch_id"])
        self.assertEqual(context["activeBatchCode"], summary["batch_code"])
        self.assertEqual(context["lifecycleState"], "COMPLETED")
        self.assertTrue(context["retainAcrossTurns"])
        self.assertTrue(
            context["clearOnlyOnExplicitNewDesignOrTaskSwitch"]
        )
        self.assertEqual(
            context["followUpTools"]["textualTop10"],
            "get_wf01_report",
        )
        self.assertEqual(
            context["newDesignTool"], "create_wf01_batch"
        )


class _FakeBatchService:
    def __init__(self, *, stop_after_m2: bool = False) -> None:
        self.stop_after_m2 = stop_after_m2
        self.batch = {
            "batch_id": "batch-auto",
            "workflow": "WF-01",
            "task_version": 1,
            "status": "QUEUED",
            "target_total": 0,
            "targets": [],
            "agent_interactions": [],
        }

    def get_batch(self, _batch_id: str) -> dict:
        return copy.deepcopy(self.batch)


class WF01AutomaticRunnerTests(unittest.TestCase):
    @staticmethod
    def _service(*, stop_after_m2: bool = False) -> AgentDService:
        service = AgentDService.__new__(AgentDService)
        service.wf01_v02 = _FakeBatchService(stop_after_m2=stop_after_m2)

        def runner(stage: str):
            def execute(_batch_id: str, _options: dict | None = None) -> dict:
                batch = service.wf01_v02.batch
                batch[f"{stage.lower()}_outcome"] = "COMPLETED"
                batch["status"] = "EXPLORING"
                if stage == "M2" and service.wf01_v02.stop_after_m2:
                    batch["agent_interactions"] = [
                        {
                            "data": {
                                "interactionId": "interaction-stop",
                                "status": "WAITING_USER",
                                "message": "需要用户确认",
                            }
                        }
                    ]
                if stage == "M6":
                    batch["m6_execution"] = {
                        "stage": "M6_COMPLETE",
                        "exports": {},
                    }
                return copy.deepcopy(batch)

            return execute

        service.execute_wf01_v02_m2 = runner("M2")
        service.execute_wf01_v02_m3 = runner("M3")
        service.execute_wf01_v02_m4 = runner("M4")
        service.execute_wf01_v02_m5 = runner("M5")
        service.execute_wf01_v02_m6 = runner("M6")
        return service

    def test_runner_executes_all_stages_until_completion(self) -> None:
        service = self._service()
        result = service.run_wf01_v02_until_blocked("batch-auto")

        self.assertEqual(result["executed_stages"], ["M2", "M3", "M4", "M5", "M6"])
        self.assertEqual(result["next_action"], "COMPLETE")
        self.assertEqual(result["response_mode"], "SUMMARY")

    def test_runner_stops_when_user_interaction_is_created(self) -> None:
        service = self._service(stop_after_m2=True)
        result = service.run_wf01_v02_until_blocked("batch-auto")

        self.assertEqual(result["executed_stages"], ["M2"])
        self.assertEqual(result["next_action"], "WAIT_FOR_USER")
        self.assertEqual(result["pending_interaction_count"], 1)

    def test_runner_does_not_reenter_a_running_stage(self) -> None:
        service = self._service()
        service.wf01_v02.batch["status"] = "RUNNING"
        service.wf01_v02.batch["m3_outcome"] = "COMPLETED"

        result = service.run_wf01_v02_until_blocked("batch-auto")

        self.assertEqual(result["executed_stages"], [])
        self.assertEqual(result["status"], "RUNNING")
        self.assertEqual(
            result["next_action"],
            "WAIT_FOR_STAGE_COMPLETION",
        )


if __name__ == "__main__":
    unittest.main()

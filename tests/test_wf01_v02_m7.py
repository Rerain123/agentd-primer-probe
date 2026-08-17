from __future__ import annotations

import copy
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.service import AgentDService
from agentd.interactions import build_candidate_shortfall_interaction


def _m5_result(target_id: str) -> dict:
    return {
        "stage": "M5_COMPLETE_AWAITING_M6",
        "engine_version": "fixture-m5",
        "target_id": target_id,
        "status": "FAILED",
        "status_reason": "NO_DELIVERABLE_CANDIDATES",
        "search_complete": False,
        "technical_search_complete": False,
        "candidate_counts": {
            "deliverable": 0,
            "qualified": 0,
            "incomplete_evidence": 0,
            "top10": 0,
            "hard_rule_rejected": 0,
            "hard_rule_incomplete": 0,
            "specificity_failed": 0,
        },
        "top10": [],
        "all_deliverable_candidates": [],
        "qualified_candidate_ids": [],
        "incomplete_candidate_ids": [],
        "completed_scope": [],
        "incomplete_scope": [],
        "incomplete_reasons": [],
        "recommendations": [],
        "result_summary": {"status": "FAILED"},
        "summarized_at": "2026-08-03T00:00:00+00:00",
    }


def _target(index: int) -> dict:
    target_id = f"target_{index:03d}"
    sequence = ("ACGT" * 40)[:160]
    m5 = _m5_result(target_id)
    return {
        "target_id": target_id,
        "ordinal": index,
        "status": "FAILED",
        "template": {
            "template_id": f"{target_id}:template",
            "record_name": f"record_{index:03d}",
            "original_record_name": f"record_{index:03d}",
            "description": f"fixture {index}",
            "sequence": sequence,
            "length_nt": len(sequence),
            "source_file": "fixture.fasta",
            "source_record_ordinal": index,
            "validation_status": "VALID",
            "checksum_sha256": "a" * 64,
        },
        "target_object": {
            "object_id": f"{target_id}:target:1",
            "role": "TARGET",
            "original_input": {"name": "Synthetic test virus"},
            "identifier_type": "NAME",
            "identifier": "Synthetic test virus",
            "normalized_name": "Synthetic test virus",
            "scientific_name": None,
            "taxid": None,
            "accession": None,
            "resolution_status": "NORMALIZED",
        },
        "host_objects": [
            {
                "object_id": f"{target_id}:host:1",
                "role": "HOST",
                "original_input": {"name": "pig"},
                "identifier_type": "NAME",
                "identifier": "pig",
                "normalized_name": "pig",
                "scientific_name": "Sus scrofa",
                "taxid": None,
                "accession": None,
                "resolution_status": "NORMALIZED",
            }
        ],
        "related_objects": [],
        "background_objects": [],
        "parameters": {
            "source": "target_override",
            "user_overrides": {"primer_opt_tm": 60.0},
            "effective_user_profile": {"primer_opt_tm": 60.0},
        },
        "issues": [],
        "m2_execution": {
            "stage": "M2_COMPLETE_AWAITING_M3",
            "exploration_manifest": {"units": []},
            "candidate_counts": {},
            "hard_rule_pass_candidates": [],
        },
        "m3_execution": {
            "stage": "M3_COMPLETE_AWAITING_M4",
            "primer3_search_complete": True,
            "candidate_counts": {},
            "provisional_top10": [],
        },
        "m4_execution": {
            "stage": "M4_COMPLETE_AWAITING_M5",
            "target_id": target_id,
            "search_complete": False,
            "technical_search_complete": False,
            "specificity_complete": False,
            "candidate_counts": {},
            "top10": [],
            "all_qualified_candidates": [],
            "all_specificity_incomplete_candidates": [],
            "all_specificity_failed_candidates": [],
            "hard_rule_rejected_candidates": [],
            "hard_rule_incomplete_candidates": [],
        },
        "m5_execution": m5,
        "candidate_counts": copy.deepcopy(m5["candidate_counts"]),
        "top10": [],
        "search_complete": False,
        "technical_search_complete": False,
        "result_summary": {"status": "FAILED"},
        "incomplete_reasons": [],
        "recommendations": [],
    }


def source_batch() -> dict:
    batch_id = str(uuid4())
    targets = [_target(1), _target(2)]
    return {
        "batch_id": batch_id,
        "task_version": 1,
        "source_batch_id": None,
        "source_version": None,
        "lineage_root_batch_id": batch_id,
        "workflow": "WF-01",
        "prd_version": "0.2",
        "status": "FAILED",
        "created_at": "2026-08-03T00:00:00+00:00",
        "updated_at": "2026-08-03T00:01:00+00:00",
        "preflight": {
            "normalised_input": {"task_name": "M7 fixture batch"},
        },
        "targets": targets,
        "target_total": 2,
        "target_counts": {"total": 2, "failed": 2},
        "candidate_counts": {"deliverable": 0},
        "events": [],
        "m2_outcome": "COMPLETED",
        "m3_outcome": "COMPLETED",
        "m4_outcome": "COMPLETED",
        "m5_outcome": "COMPLETED",
        "m5_execution": {
            "status": "FAILED",
            "status_reason": "NO_TARGET_HAS_DELIVERABLE_CANDIDATES",
        },
        "search_complete": False,
        "technical_search_complete": False,
        "report_id": None,
    }


def redesign_request() -> dict:
    return {
        "reason": "替换 target_001 的模板序列后重新设计",
        "target_changes": [
            {
                "target_id": "target_001",
                "template": {"sequence": ("TGCA" * 40)[:160]},
            }
        ],
    }


class _FakeM2Engine:
    calls: list[str] = []

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def run_target(self, target: dict) -> dict:
        self.calls.append(target["target_id"])
        return {
            "stage": "M2_COMPLETE_AWAITING_M3",
            "target_id": target["target_id"],
            "exploration_manifest": {"units": []},
            "candidate_counts": {},
            "hard_rule_pass_candidates": [],
        }


class _FakeM3Engine:
    calls: list[str] = []

    def run_target(self, target: dict) -> dict:
        self.calls.append(target["target_id"])
        return {
            "stage": "M3_COMPLETE_AWAITING_M4",
            "target_id": target["target_id"],
            "primer3_search_complete": True,
            "candidate_counts": {},
            "provisional_top10": [],
        }


class _FakeM4Engine:
    calls: list[str] = []

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def run_target(self, target: dict) -> dict:
        self.calls.append(target["target_id"])
        return {
            "stage": "M4_COMPLETE_AWAITING_M5",
            "target_id": target["target_id"],
            "search_complete": False,
            "technical_search_complete": False,
            "specificity_complete": False,
            "candidate_counts": {},
            "top10": [],
            "all_qualified_candidates": [],
            "all_specificity_incomplete_candidates": [],
            "all_specificity_failed_candidates": [],
            "hard_rule_rejected_candidates": [],
            "hard_rule_incomplete_candidates": [],
        }


class WF01V02M7VersionTests(unittest.TestCase):
    def test_interaction_adjust_constraints_creates_queued_new_version(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-m7-route-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            source = source_batch()
            interaction = build_candidate_shortfall_interaction(
                source["batch_id"],
                target_id="target_001",
                candidate_count=0,
            )
            source["agent_interactions"] = [interaction]
            service.wf01_v02.store.save(source)

            result = service.submit_wf01_v02_interaction_response(
                source["batch_id"],
                {
                    "type": "agent_interaction_response",
                    "task": "PRIMER_DESIGN",
                    "interactionId": interaction["data"]["interactionId"],
                    "answer": "ADJUST_CONSTRAINTS",
                    "fieldValues": {
                        "target_changes": redesign_request()["target_changes"]
                    },
                },
            )

            self.assertTrue(result["new_version_created"])
            self.assertNotEqual(result["batch_id"], source["batch_id"])
            self.assertEqual(result["status"], "QUEUED")
            self.assertEqual(result["next_action"], "RUN_M2")
            self.assertEqual(result["batch"]["task_version"], 2)
            persisted_source = service.get_wf01_v02_batch(source["batch_id"])
            self.assertEqual(
                persisted_source["agent_interactions"][0]["data"]["status"],
                "RESOLVED",
            )
            self.assertEqual(
                persisted_source["agent_interactions"][0]["data"][
                    "userResponse"
                ]["routedBatchId"],
                result["batch_id"],
            )

            replay = service.submit_wf01_v02_interaction_response(
                source["batch_id"],
                {
                    "type": "agent_interaction_response",
                    "task": "PRIMER_DESIGN",
                    "interactionId": interaction["data"]["interactionId"],
                    "answer": "ADJUST_CONSTRAINTS",
                    "fieldValues": {
                        "target_changes": redesign_request()["target_changes"]
                    },
                },
            )
            self.assertTrue(replay["submission_replayed"])
            self.assertEqual(replay["batch_id"], result["batch_id"])

    def test_ac_ver_01_creates_immutable_version_with_diff_and_reuse(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-m7-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            source = source_batch()
            service.wf01_v02.store.save(source)
            before = service.get_wf01_v02_batch(source["batch_id"])

            version = service.create_wf01_v02_version(
                source["batch_id"], redesign_request()
            )

            self.assertNotEqual(version["batch_id"], source["batch_id"])
            self.assertEqual(version["task_version"], 2)
            self.assertEqual(version["source_batch_id"], source["batch_id"])
            self.assertEqual(version["source_version"], 1)
            self.assertEqual(version["lineage_root_batch_id"], source["batch_id"])
            self.assertEqual(version["status"], "QUEUED")
            self.assertEqual(
                version["m7_execution"]["rerun_target_ids"], ["target_001"]
            )
            self.assertEqual(
                version["m7_execution"]["reused_target_ids"], ["target_002"]
            )
            self.assertEqual(
                version["targets"][0]["m7_provenance"]["disposition"],
                "RERUN_REQUIRED",
            )
            self.assertNotIn("m2_execution", version["targets"][0])
            self.assertEqual(
                version["targets"][1]["m7_provenance"]["disposition"],
                "REUSED",
            )
            self.assertEqual(
                version["targets"][1]["m5_execution"],
                source["targets"][1]["m5_execution"],
            )
            fields = version["version_diff"][0]["fields"]
            self.assertEqual(fields[0]["path"], "template.sequence")
            self.assertIn("sha256", fields[0]["before"])
            self.assertEqual(
                service.get_wf01_v02_batch(source["batch_id"]), before
            )
            branch = service.create_wf01_v02_version(
                source["batch_id"],
                {
                    "target_changes": [
                        {
                            "target_id": "target_002",
                            "design_parameters": {"primer_opt_tm": 61.0},
                        }
                    ]
                },
            )
            self.assertEqual(branch["task_version"], 3)
            self.assertEqual(branch["source_version"], 1)
            self.assertEqual(
                branch["lineage_root_batch_id"], source["batch_id"]
            )

    def test_no_effective_change_does_not_create_an_empty_version(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-m7-noop-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            source = source_batch()
            service.wf01_v02.store.save(source)
            with self.assertRaisesRegex(Exception, "未产生有效输入差异"):
                service.create_wf01_v02_version(
                    source["batch_id"],
                    {
                        "target_changes": [
                            {
                                "target_id": "target_001",
                                "template": {
                                    "sequence": source["targets"][0]["template"][
                                        "sequence"
                                    ]
                                },
                            }
                        ]
                    },
                )

    def test_m2_to_m5_execute_only_changed_target_and_emit_reuse_events(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-m7-run-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            source = source_batch()
            service.wf01_v02.store.save(source)
            version = service.create_wf01_v02_version(
                source["batch_id"], redesign_request()
            )
            _FakeM2Engine.calls = []
            _FakeM3Engine.calls = []
            _FakeM4Engine.calls = []
            with patch("agentd.wf01_v02_engine.WF01V02TargetEngine", _FakeM2Engine), patch(
                "agentd.wf01_v02_m3.WF01V02M3Engine", _FakeM3Engine
            ), patch("agentd.wf01_v02_m4.WF01V02M4Engine", _FakeM4Engine):
                after_m2 = service.execute_wf01_v02_m2(version["batch_id"])
                after_m3 = service.execute_wf01_v02_m3(version["batch_id"])
                after_m4 = service.execute_wf01_v02_m4(version["batch_id"])
                after_m5 = service.execute_wf01_v02_m5(version["batch_id"])
                waiting = [
                    item
                    for item in after_m5.get("agent_interactions") or []
                    if item["data"]["status"] == "WAITING_USER"
                ]
                if waiting:
                    service.submit_wf01_v02_interaction_response(
                        version["batch_id"],
                        {
                            "type": "agent_interaction_response",
                            "task": "PRIMER_DESIGN",
                            "interactions": [
                                {
                                    "interactionId": item["data"]["interactionId"],
                                    "answer": "EXPORT_CURRENT_REPORT",
                                }
                                for item in waiting
                            ],
                        },
                    )
                after_m6 = service.execute_wf01_v02_m6(version["batch_id"])

            self.assertEqual(_FakeM2Engine.calls, ["target_001"])
            self.assertEqual(_FakeM3Engine.calls, ["target_001"])
            self.assertEqual(_FakeM4Engine.calls, ["target_001"])
            self.assertEqual(
                after_m2["targets"][1]["m2_execution"],
                source["targets"][1]["m2_execution"],
            )
            self.assertEqual(
                after_m3["targets"][1]["m3_execution"],
                source["targets"][1]["m3_execution"],
            )
            self.assertEqual(
                after_m4["targets"][1]["m4_execution"],
                source["targets"][1]["m4_execution"],
            )
            self.assertEqual(
                after_m5["targets"][1]["m5_execution"],
                source["targets"][1]["m5_execution"],
            )
            event_types = [item["event_type"] for item in after_m5["events"]]
            for event_type in (
                "TARGET_M2_REUSED",
                "TARGET_M3_REUSED",
                "TARGET_M4_REUSED",
                "TARGET_M5_REUSED",
            ):
                self.assertIn(event_type, event_types)
            self.assertEqual(after_m6["m6_outcome"], "COMPLETED")
            report = service.get_wf01_v02_report(version["batch_id"])
            self.assertEqual(report["version_diff"]["status"], "M7_APPLIED")
            self.assertEqual(
                report["version_diff"]["rerun_target_ids"], ["target_001"]
            )
            html = service.get_wf01_v02_export_path(
                version["batch_id"], "html"
            ).read_text(encoding="utf-8")
            # Version provenance remains available in the canonical report, while
            # the user-facing delivery HTML deliberately omits process details.
            self.assertNotIn("版本差异与靶标处置", html)
            self.assertNotIn("M7_APPLIED", html)


@unittest.skipUnless(
    importlib.util.find_spec("fastapi") is not None,
    "FastAPI extra not installed",
)
class WF01V02M7ApiTests(unittest.TestCase):
    def test_api_creates_version_and_advertises_m7_contract(self) -> None:
        from fastapi.testclient import TestClient
        from agentd import api

        with tempfile.TemporaryDirectory(prefix="agentd-m7-api-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            source = source_batch()
            service.wf01_v02.store.save(source)
            with patch.object(api, "service", service):
                client = TestClient(api.app)
                response = client.post(
                    f"/v1/wf01/tasks/{source['batch_id']}/versions",
                    json=redesign_request(),
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["task_version"], 2)
                capabilities = client.get("/v1/capabilities").json()[
                    "wf01_prd_v0_2"
                ]
                self.assertEqual(
                    capabilities["status"],
                    "M7_VERSIONED_REDESIGN_AND_REUSE",
                )
                self.assertEqual(
                    capabilities["endpoints"]["create_version"],
                    "POST /v1/wf01/tasks/{batch_id}/versions",
                )


if __name__ == "__main__":
    unittest.main()

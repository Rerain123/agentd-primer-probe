from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch
from urllib.parse import quote
from uuid import uuid4

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd import api
from agentd.config import load_config
from agentd.errors import InputValidationError, PersistenceError
from agentd.service import AgentDService
from agentd.wf02_v01 import WF02V01BatchService, summarize_wf02_batch
from agentd.wf02_v01_report import (
    WF02_EXPORT_RENDERER_VERSIONS,
    render_wf02_pdf,
)


class RecordingWF02ArtifactPublisher:
    enabled = True

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.uploads: list[dict[str, Any]] = []

    def publish(
        self,
        path: Path,
        *,
        batch_id: str,
        media_type: str,
        sha256: str,
        object_name: str | None = None,
    ) -> dict[str, Any]:
        if self.fail:
            raise PersistenceError("simulated WF-02 RustFS failure")
        published_name = object_name or path.name
        value = {
            "provider": "rustfs",
            "endpoint_url": "https://rustfs.ne.scqcyz.cn",
            "bucket": "wens",
            "object_key": f"data/{published_name}",
            "etag": "fixture-etag",
            "version_id": None,
            "size_bytes": path.stat().st_size,
            "sha256": sha256,
            "file_url": (
                "https://rustfs.ne.scqcyz.cn/wens/data/"
                + quote(published_name, safe="")
            ),
            "published_at": "2026-08-21T00:00:00+00:00",
        }
        self.uploads.append(
            {
                "path": path,
                "batch_id": batch_id,
                "media_type": media_type,
                "object_name": published_name,
                **value,
            }
        )
        return value


def _candidate(
    index: int,
    *,
    score: float | None = None,
    evidence_level: str = "blast_verified",
    specificity_status: str = "PASS",
    r19_status: str = "PASS",
    amplicon_sequence: str | None = None,
    forward_tm: float | None = None,
    reverse_tm: float | None = None,
    probe_tm: float | None = None,
    amplicon_gc_percent: float | None = None,
) -> dict[str, Any]:
    suffix = "ACGT"[(index - 1) % 4]
    selected_amplicon = (
        amplicon_sequence
        if amplicon_sequence is not None
        else ("ACGT" * 25) + ("A" * index)
    )
    return {
        "candidate_id": f"candidate-{index:02d}",
        "rank": index,
        "score": score if score is not None else 100.0 - index,
        "verdict": "PASS",
        "evidence_level": evidence_level,
        "specificity": {"overall_status": specificity_status},
        "rules": [{"code": "R19", "status": r19_status}],
        "forward_primer": {
            "sequence_5to3": f"ACGTACGTACGTACG{suffix}",
            "tm_celsius": forward_tm if forward_tm is not None else 59.0 + index * 0.1,
            "gc_percent": 50.0,
        },
        "reverse_primer": {
            "sequence_5to3": f"TGCATGCATGCATGC{suffix}",
            "tm_celsius": reverse_tm if reverse_tm is not None else 59.2 + index * 0.1,
            "gc_percent": 50.0,
        },
        "probe": {
            "sequence_5to3": f"GATTACAGATTACA{suffix}",
            "tm_celsius": probe_tm if probe_tm is not None else 68.0 + index * 0.1,
            "gc_percent": 45.0,
        },
        "amplicon": {
            "length_bp": len(selected_amplicon) if selected_amplicon else 100 + index,
            **(
                {"gc_percent": amplicon_gc_percent}
                if amplicon_gc_percent is not None
                else {}
            ),
            **(
                {"sequence_5to3": selected_amplicon}
                if selected_amplicon
                else {}
            ),
        },
    }


def _p4_outcome(
    verdict: str,
    *,
    primer_spread: float = 1.0,
    probe_spread: float = 1.0,
    status: str = "COMPLETED",
) -> dict[str, Any]:
    return {
        "status": status,
        "verdict": verdict,
        "spectral_check": {"status": "PASS"},
        "quencher_check": {"all_matched": True, "mismatches": []},
        "instrument_check": {
            "instrument": "CFX96",
            "all_supported": True,
            "rox_conflict": False,
            "available_channels": 5,
        },
        "heterodimer": {
            "total_pairs": 15,
            "evaluated_pairs": 15,
            "worst_dg_kcal": -6.5,
        },
        "tm_spread_celsius": {
            "primers": primer_spread,
            "probes": probe_spread,
        },
        "gc_spread_percent": 5.0,
        "issues": [] if verdict == "COMPATIBLE" else ["test outcome"],
    }


class FakeWF01Gateway:
    def __init__(
        self,
        behavior_by_target: dict[str, dict[str, Any]],
        *,
        p6_outcomes: dict[str, Any] | None = None,
        p5_outcomes: list[dict[str, Any] | Exception] | None = None,
        p4_outcomes: list[dict[str, Any] | Exception] | None = None,
        redesign_candidates_by_target: dict[str, list[list[dict[str, Any]]]] | None = None,
    ) -> None:
        self.behavior_by_target = copy.deepcopy(behavior_by_target)
        self.p6_outcomes = copy.deepcopy(p6_outcomes or {})
        self.children: dict[str, dict[str, Any]] = {}
        self.created_payloads: list[dict[str, Any]] = []
        self.created_options: list[dict[str, Any]] = []
        self.run_calls: list[str] = []
        self.submitted_responses: list[tuple[str, dict[str, Any]]] = []
        self.p6_calls: list[str] = []
        self.p5_calls: list[dict[str, Any]] = []
        self.p4_calls: list[dict[str, Any]] = []
        self.p5_outcomes = list(p5_outcomes or [])
        self.p4_outcomes = list(p4_outcomes or [])
        self.redesign_candidates_by_target = copy.deepcopy(
            redesign_candidates_by_target or {}
        )
        self.version_calls: list[dict[str, Any]] = []

    def create_wf01_v02_batch(
        self,
        payload: dict,
        **kwargs: Any,
    ) -> dict:
        prepared = copy.deepcopy(payload)
        self.created_payloads.append(prepared)
        self.created_options.append(copy.deepcopy(kwargs))
        target_id = str(prepared.get("task_name") or "").rsplit("::", 1)[-1]
        child_id = str(uuid4())
        self.children[child_id] = {
            "target_id": target_id,
            "resolved": False,
            "candidates": None,
        }
        return {
            "batch_id": child_id,
            "batch_code": f"WF01-{target_id}",
            "task_version": 1,
        }

    def run_wf01_v02_until_blocked(
        self,
        batch_id: str,
        stage_options: dict | None = None,
    ) -> dict:
        self.run_calls.append(batch_id)
        child = self.children[batch_id]
        target_id = child["target_id"]
        behavior = self.behavior_by_target[target_id]
        if behavior.get("raise_on_run"):
            raise RuntimeError(f"simulated WF-01 failure for {target_id}")
        if behavior.get("wait_for_user") and not child["resolved"]:
            return {
                "batch_id": batch_id,
                "batch_code": f"WF01-{target_id}",
                "task_version": 1,
                "status": "PENDING_CONFIRMATION",
                "next_action": "WAIT_FOR_USER",
                "ui_message": {
                    "type": "agent_interaction",
                    "task": "PRIMER_DESIGN",
                    "taskId": batch_id,
                    "interactionId": f"interaction-{target_id}",
                    "question": f"请确认 {target_id}",
                    "flowControl": {
                        "action": "WAIT_FOR_USER",
                        "stopCurrentTurn": True,
                    },
                },
            }
        return {
            "batch_id": batch_id,
            "batch_code": f"WF01-{target_id}",
            "task_version": 1,
            "status": "COMPLETED",
            "next_action": "COMPLETE",
        }

    def get_wf01_v02_report(
        self,
        batch_id: str,
        **kwargs: Any,
    ) -> dict:
        target_id = self.children[batch_id]["target_id"]
        behavior = self.behavior_by_target[target_id]
        if behavior.get("missing_report_target"):
            return {"batch_id": batch_id, "targets": []}
        conclusion = copy.deepcopy(behavior.get("conclusion", "PASS"))
        if not isinstance(conclusion, dict):
            conclusion = {"status": conclusion}
        reported_candidates = copy.deepcopy(
            self.children[batch_id].get("candidates")
            or behavior.get("candidates")
            or []
        )
        return {
            "schema_version": "wf01-prd-v0.2-canonical-report-v2",
            "batch_id": batch_id,
            "targets": [
                {
                    "target_id": target_id,
                    "top10": copy.deepcopy(
                        behavior.get("top10_candidates", reported_candidates)
                    ),
                    "all_qualified_candidates": copy.deepcopy(
                        behavior.get(
                            "qualified_candidates",
                            reported_candidates,
                        )
                    ),
                    "conclusion": conclusion,
                }
            ],
        }

    def submit_wf01_v02_interaction_response(
        self,
        batch_id: str,
        response: dict,
    ) -> dict:
        self.children[batch_id]["resolved"] = True
        self.submitted_responses.append((batch_id, copy.deepcopy(response)))
        return {"batch_id": batch_id, "status": "QUEUED"}

    def create_wf01_tm_constrained_version(self, payload: dict) -> dict:
        self.version_calls.append(copy.deepcopy(payload))
        source = self.children[str(payload["source_batch_id"])]
        target_id = str(source["target_id"])
        outcomes = self.redesign_candidates_by_target.get(target_id) or []
        candidates = outcomes.pop(0) if outcomes else None
        child_id = str(uuid4())
        self.children[child_id] = {
            "target_id": target_id,
            "resolved": True,
            "candidates": copy.deepcopy(candidates),
        }
        return {
            "batch_id": child_id,
            "batch_code": f"WF01-{target_id}-V",
            "task_version": 2,
        }

    def assess_p6_amplicon_structure(self, amplicon_sequence: str) -> dict:
        self.p6_calls.append(amplicon_sequence)
        outcome = self.p6_outcomes.get(amplicon_sequence)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, dict):
            return copy.deepcopy(outcome)
        return {
            "status": "PASS",
            "mfe_dg": -12.5,
            "mfe_structure": "." * len(amplicon_sequence),
            "most_stable_dg": -12.5,
            "subopt_structures": [],
            "ensemble_diversity": "0/0",
            "temperature_celsius": 60.0,
            "salt_na_mm": 50.0,
            "parameter_set": "SantaLucia2004_DNA",
            "confidence": "medium",
            "tool": "ViennaRNA",
            "tool_version": "test",
            "warnings": [],
        }

    def design_p5_internal_control(self, payload: dict) -> dict:
        self.p5_calls.append(copy.deepcopy(payload))
        if self.p5_outcomes:
            outcome = self.p5_outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return copy.deepcopy(outcome)
        return {
            "status": "COMPLETED",
            "verdict": "PASS",
            "gene_selected": "ACTB",
            "gene_attempts": [
                {"gene": "ACTB", "status": "PASS", "conservation": 0.995},
                {"gene": "GAPDH", "status": "PASS", "conservation": 0.992},
            ],
            "candidates": [
                _candidate(
                    index,
                    amplicon_sequence="ACGT" * (40 + (index - 1) * 5),
                )
                for index in range(1, 4)
            ],
            "warnings": [],
        }

    def check_p4_panel(self, payload: dict) -> dict:
        self.p4_calls.append(copy.deepcopy(payload))
        if self.p4_outcomes:
            outcome = self.p4_outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return copy.deepcopy(outcome)
        return {
            "status": "COMPLETED",
            "verdict": "COMPATIBLE",
            "spectral_check": {"status": "PASS"},
            "quencher_check": {"all_matched": True, "mismatches": []},
            "instrument_check": {
                "instrument": payload.get("instrument") or "CFX96",
                "all_supported": True,
                "rox_conflict": False,
                "available_channels": 5,
            },
            "heterodimer": {
                "total_pairs": 15,
                "evaluated_pairs": 15,
                "worst_dg_kcal": -5.0,
            },
            "tm_spread_celsius": {"primers": 1.0, "probes": 1.0},
            "gc_spread_percent": 5.0,
            "issues": [],
        }


class WF02V01TargetDesignTests(unittest.TestCase):
    @staticmethod
    def _target(target_id: str, *, with_sequence: bool = True) -> dict[str, Any]:
        target: dict[str, Any] = {
            "target_id": target_id,
            "display_name": f"靶标 {target_id}",
        }
        if with_sequence:
            target["sequence"] = "ACGT" * 40
        return target

    @classmethod
    def _payload(cls, *target_ids: str) -> dict[str, Any]:
        return {
            "task_name": "WF-02 N06-N09 测试",
            "targets": [cls._target(target_id) for target_id in target_ids],
            "host_species": "猪",
            "include_internal_control": True,
        }

    @staticmethod
    def _service(
        state_dir: str,
        gateway: FakeWF01Gateway,
    ) -> WF02V01BatchService:
        return WF02V01BatchService(
            state_dir,
            load_config(),
            gateway=gateway,
        )

    @staticmethod
    def _validate_batch(batch: dict[str, Any]) -> None:
        schema = json.loads(
            (ROOT / "schemas" / "wf02-v01-batch.schema.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator(schema).validate(batch)

    @staticmethod
    def _validate_report(report: dict[str, Any]) -> None:
        schema = json.loads(
            (ROOT / "schemas" / "wf02-v01-report.schema.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator(schema).validate(report)

    def test_wf02_orchestrator_has_no_embedded_model_primer3_or_blast_client(self) -> None:
        source = (ROOT / "src" / "agentd" / "wf02_v01.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "Primer3Adapter(",
            "MultiplexEvaluator(",
            "BlastAdapter(",
            "ViennaRNAAdapter(",
            "WF01V02BatchService(",
            "import openai",
            "from openai",
            "import anthropic",
            "from anthropic",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        self.assertIn("gateway.create_wf01_v02_batch", source)
        self.assertIn("gateway.run_wf01_v02_until_blocked", source)
        self.assertIn("gateway.get_wf01_v02_report", source)
        self.assertIn("gateway.assess_p6_amplicon_structure", source)
        self.assertIn("gateway.design_p5_internal_control", source)
        self.assertIn("gateway.check_p4_panel", source)
        self.assertIn("select_wf01_candidates", source)
        self.assertNotIn("_candidate_blast_passed", source)

    def test_wf01_child_repairs_empty_default_and_requires_full_route(self) -> None:
        gateway = FakeWF01Gateway({"A": {"candidates": []}})
        with tempfile.TemporaryDirectory(prefix="wf02-child-route-") as state_dir:
            service = self._service(state_dir, gateway)
            payload = service._wf01_child_payload(
                {"batch_id": "parent", "user_input_snapshot": {}},
                {
                    "target_id": "A",
                    "display_name": "Target A",
                    "source_input": {
                        "sequence": "ACGT" * 40,
                        "target_organism": "Target A",
                        "batch_defaults": {"target_organism": None},
                    },
                },
            )
            gateway.create_wf01_v02_batch(
                payload,
                require_full_route=True,
            )

        self.assertEqual(
            payload["batch_defaults"]["target_organism"],
            {"name": "Target A"},
        )
        self.assertEqual(
            gateway.created_options,
            [{"require_full_route": True}],
        )

    def test_service_gateway_forwards_relaxation_options_to_existing_p4(self) -> None:
        service = object.__new__(AgentDService)
        service.config = load_config()
        panel = [
            {
                "target_id": "A",
                "fwd_seq": "ACGTACGTACGTACGT",
                "rev_seq": "TGCATGCATGCATGCA",
                "probe_seq": "GATTACAGATTACAGA",
                "fwd_tm": 59.0,
                "rev_tm": 59.2,
                "probe_tm": 68.0,
                "gc_percent": 50.0,
                "fluorophore": "FAM",
                "quencher": "BHQ-1",
            }
        ]
        with patch("agentd.service.Primer3Adapter") as primer3_class, patch(
            "agentd.service.MultiplexEvaluator"
        ) as evaluator_class:
            evaluator = evaluator_class.return_value
            evaluator.status.return_value = {"available": True}
            evaluator.check_panel.return_value = {
                "verdict": "COMPATIBLE",
                "issues": [],
            }

            result = AgentDService.check_p4_panel(
                service,
                {
                    "panel": panel,
                    "instrument": "CFX96",
                    "rox_as_reference": False,
                    "relaxed": True,
                    "tm_spread_limit": 4.0,
                },
            )

            primer3_class.assert_called_once()
            evaluator_class.assert_called_once()
            evaluator.check_panel.assert_called_once_with(
                panel,
                instrument="CFX96",
                rox_as_reference=False,
                relaxed=True,
                tm_spread_limit=4.0,
            )
            self.assertEqual(result["status"], "COMPLETED")

    def test_n06_n09_keeps_upstream_qualified_top5_and_isolates_zero(self) -> None:
        candidates_a = [_candidate(index) for index in range(1, 8)]
        candidates_a[0]["specificity"]["overall_status"] = "CAUTION"
        candidates_a[0]["rules"][0]["status"] = "WARNING"
        candidates_a[0]["verdict"] = "PASS_WITH_WARNINGS"
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": candidates_a},
                "B": {"candidates": []},
                "C": {
                    "candidates": [
                        _candidate(1, amplicon_sequence="ACGT" * 35),
                        _candidate(2, evidence_level="in_silico_only"),
                    ]
                },
            }
        )
        with tempfile.TemporaryDirectory(prefix="wf02-n06-n09-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(self._payload("A", "B", "C"))

            summary = service.run_until_blocked(created["batch_id"])
            persisted = service.get_batch(created["batch_id"])

            self.assertEqual(summary["status"], "READY_FOR_RELAXATION")
            self.assertEqual(summary["stage"], "N20")
            self.assertEqual(summary["next_action"], "RUN_RELAXATION")
            self.assertTrue(summary["phase_02_complete"])
            self.assertTrue(summary["phase_03_complete"])
            self.assertTrue(summary["phase_04_complete"])
            self.assertEqual(summary["target_design"]["succeeded"], 2)
            self.assertEqual(summary["target_design"]["failed"], 1)
            self.assertEqual(summary["target_design"]["waiting"], 0)
            self.assertEqual(summary["target_design"]["failed_target_ids"], ["B"])
            self.assertEqual(summary["multiplex"]["failed_targets"], ["B"])
            self.assertEqual(
                summary["target_design"]["coverage"]["status"], "INCOMPLETE"
            )
            self.assertEqual(
                summary["target_design"]["coverage"]["covered_targets"], 2
            )
            self.assertIn(
                "当前面板覆盖2/3靶标",
                summary["target_design"]["coverage"]["message"],
            )
            self.assertEqual(len(summary["target_design"]["coverage_gaps"]), 1)
            gap = summary["target_design"]["coverage_gaps"][0]
            self.assertEqual(gap["target_id"], "B")
            self.assertEqual(gap["input_order"], 2)
            self.assertEqual(
                gap["reason_code"], "NO_WF01_QUALIFIED_CANDIDATES"
            )
            self.assertEqual(
                gap["failure"]["code"], "NO_WF01_QUALIFIED_CANDIDATES"
            )

            results = {
                item["target_id"]: item
                for item in persisted["target_design"]["targets"]
            }
            self.assertEqual(results["A"]["candidate_count"], 5)
            self.assertEqual(
                [item["candidate_id"] for item in results["A"]["candidates"]],
                [f"candidate-{index:02d}" for index in range(1, 6)],
            )
            self.assertEqual(
                results["A"]["candidates"][0]["specificity"]["overall_status"],
                "CAUTION",
            )
            self.assertEqual(
                results["A"]["candidates"][0]["rules"][0]["status"],
                "WARNING",
            )
            self.assertEqual(
                results["A"]["wf01_result"]["contract_version"],
                "wf01-candidate-contract-v1",
            )
            self.assertEqual(
                results["A"]["wf01_result"][
                    "downstream_business_reinterpretation"
                ],
                "DISABLED",
            )
            self.assertEqual(results["B"]["status"], "FAIL")
            self.assertEqual(
                results["B"]["failure"]["code"],
                "NO_WF01_QUALIFIED_CANDIDATES",
            )
            self.assertEqual(results["C"]["candidate_count"], 2)
            self.assertEqual(results["A"]["p6_summary"]["evaluated_count"], 5)
            self.assertEqual(results["C"]["p6_summary"]["evaluated_count"], 2)
            self.assertEqual(len(gateway.p6_calls), 7)
            self.assertEqual(len(gateway.created_payloads), 3)
            self.assertEqual(
                gateway.created_options,
                [{"require_full_route": True}] * 3,
            )
            self.assertEqual(
                len({item["wf01_batch_id"] for item in results.values()}),
                3,
            )
            self.assertTrue(
                all(
                    payload["batch_defaults"]["hosts"] == [{"name": "猪"}]
                    for payload in gateway.created_payloads
                )
            )
            self.assertIn(
                "B",
                {
                    item.get("target_id")
                    for item in persisted["workflow_trace"]
                    if item["node"] == "N08"
                },
            )
            self.assertEqual(
                {
                    item.get("target_id")
                    for item in persisted["workflow_trace"]
                    if item["node"] == "N09" and item["status"] == "PASS"
                },
                {"A", "C"},
            )
            self._validate_batch(persisted)

            continued = service.run_until_blocked(created["batch_id"])
            self.assertEqual(continued["stage"], "N25")
            self.assertEqual(continued["status"], "PARTIAL_PANEL_AVAILABLE")
            self.assertTrue(continued["phase_05_complete"])
            self.assertEqual(len(gateway.created_payloads), 3)
            relaxation = service.get_batch(created["batch_id"])["multiplex"][
                "relaxation"
            ]
            self.assertEqual(relaxation["status"], "COMPLETED_PARTIAL")
            self.assertEqual(relaxation["maximum_partial_target_count"], 2)
            self.assertTrue(
                all(
                    panel["classification"] == "PARTIAL"
                    and panel["excluded_from_complete_top3"]
                    for panel in relaxation["partial_panels"]
                )
            )
            self.assertTrue(
                all(
                    panel["included_target_ids"] == ["A", "C"]
                    and panel["omitted_target_ids"] == ["B"]
                    for panel in relaxation["partial_panels"]
                )
            )
            self.assertEqual(
                relaxation["independent_target_schemes"][0]["target_id"], "B"
            )
            self.assertEqual(
                relaxation["independent_target_schemes"][0]["status"],
                "REDESIGN_REQUIRED",
            )
            report = service.get_report(created["batch_id"])
            self.assertEqual(report["result_summary"]["status"], "PARTIAL")
            self.assertEqual(report["selection_mode"], "PARTIAL_ONLY")
            self.assertTrue(report["partial_panels_included"])
            self.assertGreater(len(report["partial_panels"]), 0)
            self.assertEqual(
                continued["ui_message"]["type"], "primer_design_output"
            )
            self.assertEqual(
                continued["conversation_context"]["lifecycleState"],
                "PARTIAL_PANEL_AVAILABLE",
            )
            self.assertEqual(
                report["candidate_comparison"]["component"], "C29"
            )
            self.assertEqual(report["result_explanation"]["component"], "C30")
            self.assertIn(
                "WF02_TERMINAL_REPORT_COMPLETED",
                {
                    item["event_type"]
                    for item in report["traceability"]["events"]
                },
            )
            partial_panel = report["partial_panels"][0]
            self.assertEqual(report["multiplex"]["panel_size"], 3)
            self.assertEqual(report["multiplex"]["requested_panel_size"], 3)
            self.assertEqual(
                report["multiplex"]["delivered_target_count"], 2
            )
            self.assertEqual(
                report["multiplex"]["delivered_member_count"],
                len(partial_panel["members"]),
            )
            self.assertEqual(
                service.get_panel(
                    created["batch_id"], partial_panel["panel_id"]
                )["panel"]["tier"],
                "PARTIAL",
            )
            self._validate_report(report)
            self._validate_batch(service.get_batch(created["batch_id"]))

    def test_target_resource_failure_pauses_at_n08_without_downstream_work(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": [_candidate(1)]},
                "B": {
                    "candidates": [],
                    "conclusion": {
                        "status": "FAIL",
                        "reason_codes": [
                            "TARGET_DATABASE_OR_VERSION_NOT_RESOLVED"
                        ],
                    },
                },
            }
        )
        payload = self._payload("A", "B")
        payload["include_internal_control"] = False
        with tempfile.TemporaryDirectory(prefix="wf02-resource-block-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)

            waiting = service.run_until_blocked(created["batch_id"])
            persisted = service.get_batch(created["batch_id"])
            blocked = next(
                item
                for item in persisted["target_design"]["targets"]
                if item["target_id"] == "B"
            )

            self.assertEqual(waiting["status"], "WAITING_TARGET_RESOURCES")
            self.assertEqual(waiting["stage"], "N08")
            self.assertEqual(waiting["next_action"], "WAIT_FOR_RESOURCE")
            self.assertEqual(waiting["target_design"]["blocked_target_ids"], ["B"])
            self.assertEqual(blocked["status"], "BLOCKED_RESOURCE")
            self.assertEqual(
                blocked["failure"]["reason_codes"],
                ["TARGET_DATABASE_OR_VERSION_NOT_RESOLVED"],
            )
            self.assertEqual(gateway.p5_calls, [])
            self.assertEqual(gateway.p4_calls, [])
            self.assertNotIn("screening", persisted.get("multiplex") or {})
            self._validate_batch(persisted)

    def test_primer3_no_candidate_reason_is_preserved_as_target_failure(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": [_candidate(1)]},
                "B": {
                    "candidates": [],
                    "conclusion": {
                        "status": "FAIL",
                        "reason_codes": ["PRIMER3_GENERATED_NO_CANDIDATES"],
                    },
                },
            }
        )
        payload = self._payload("A", "B")
        payload["include_internal_control"] = False
        with tempfile.TemporaryDirectory(prefix="wf02-no-candidate-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)
            service.run_until_blocked(created["batch_id"])
            persisted = service.get_batch(created["batch_id"])
            failed = next(
                item
                for item in persisted["target_design"]["targets"]
                if item["target_id"] == "B"
            )

            self.assertEqual(failed["status"], "FAIL")
            self.assertEqual(
                failed["failure"]["code"], "TARGET_DESIGN_NO_CANDIDATES"
            )
            self.assertFalse(failed["failure"]["retryable"])
            self.assertEqual(
                failed["failure"]["reason_codes"],
                ["PRIMER3_GENERATED_NO_CANDIDATES"],
            )

    def test_resume_token_prevents_stale_retry_from_advancing_twice(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": [_candidate(1)]},
                "B": {"candidates": [_candidate(1)]},
            }
        )
        payload = self._payload("A", "B")
        payload["include_internal_control"] = False
        with tempfile.TemporaryDirectory(prefix="wf02-resume-token-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)

            first = service.run_until_blocked(created["batch_id"], bounded=True)
            token = first["resume_token"]
            second = service.run_until_blocked(
                created["batch_id"],
                bounded=True,
                expected_resume_token=token,
            )
            run_count = len(gateway.run_calls)
            stale = service.run_until_blocked(
                created["batch_id"],
                bounded=True,
                expected_resume_token=token,
            )

            self.assertNotEqual(second["resume_token"], token)
            self.assertEqual(
                stale["execution_unit"]["status"], "STALE_RESUME_TOKEN_NOOP"
            )
            self.assertFalse(stale["execution_unit"]["state_committed"])
            self.assertEqual(len(gateway.run_calls), run_count)

    def test_bounded_run_advances_one_target_and_returns_phase_boundary(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": [_candidate(1)]},
                "B": {"candidates": [_candidate(1)]},
                "C": {"candidates": [_candidate(1)]},
            }
        )
        with tempfile.TemporaryDirectory(prefix="wf02-bounded-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(self._payload("A", "B", "C"))

            first = service.run_until_blocked(
                created["batch_id"],
                bounded=True,
            )
            self.assertEqual(first["stage"], "N06")
            self.assertEqual(first["status"], "DESIGNING_TARGETS")
            self.assertEqual(first["next_action"], "CONTINUE_TARGET_DESIGN")
            self.assertEqual(first["target_design"]["succeeded"], 1)
            self.assertEqual(len(gateway.created_payloads), 1)
            self.assertEqual(len(gateway.p5_calls), 0)
            self._validate_batch(service.get_batch(created["batch_id"]))

            second = service.run_until_blocked(
                created["batch_id"],
                bounded=True,
            )
            self.assertEqual(second["stage"], "N06")
            self.assertEqual(second["next_action"], "CONTINUE_TARGET_DESIGN")
            self.assertEqual(second["target_design"]["succeeded"], 2)
            self.assertEqual(len(gateway.created_payloads), 2)
            self.assertEqual(len(gateway.p5_calls), 0)
            self._validate_batch(service.get_batch(created["batch_id"]))

            third = service.run_until_blocked(
                created["batch_id"],
                bounded=True,
            )
            self.assertEqual(third["stage"], "N09")
            self.assertEqual(third["status"], "READY_FOR_INTERNAL_CONTROL")
            self.assertEqual(third["next_action"], "RUN_INTERNAL_CONTROL")
            self.assertTrue(third["phase_02_complete"])
            self.assertEqual(len(gateway.created_payloads), 3)
            self.assertEqual(len(gateway.p5_calls), 0)

            fourth = service.run_until_blocked(
                created["batch_id"],
                bounded=True,
            )
            self.assertTrue(fourth["phase_03_complete"])
            self.assertEqual(len(gateway.p5_calls), 1)
            self._validate_batch(service.get_batch(created["batch_id"]))

    def test_bounded_run_collects_all_missing_templates_without_starting_children(
        self,
    ) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": [_candidate(1)]},
                "B": {"candidates": [_candidate(1)]},
                "C": {"candidates": [_candidate(1)]},
            }
        )
        payload = self._payload("A", "B", "C")
        for target in payload["targets"]:
            target.pop("sequence")
        with tempfile.TemporaryDirectory(prefix="wf02-bounded-input-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)

            waiting = service.run_until_blocked(
                created["batch_id"],
                bounded=True,
            )

            self.assertEqual(waiting["next_action"], "WAIT_FOR_CHILD_INPUT")
            self.assertEqual(
                [item["target_id"] for item in waiting["pending_children"]],
                ["A", "B", "C"],
            )
            self.assertEqual(len(gateway.created_payloads), 0)
            self._validate_batch(service.get_batch(created["batch_id"]))

    def test_p6_warning_and_degraded_evaluation_are_soft_and_persisted(self) -> None:
        warning_sequence = "ACGT" * 25
        error_sequence = ("TGCA" * 25) + "A"
        gateway = FakeWF01Gateway(
            {
                "A": {
                    "candidates": [
                        _candidate(1, amplicon_sequence=warning_sequence),
                        _candidate(2, amplicon_sequence=error_sequence),
                        _candidate(3, amplicon_sequence=""),
                    ]
                },
                "B": {"candidates": [_candidate(1)]},
            },
            p6_outcomes={
                warning_sequence: {
                    "status": "WARNING",
                    "mfe_dg": -34.2,
                    "mfe_structure": "(" * 50 + ")" * 50,
                    "most_stable_dg": -34.2,
                    "subopt_structures": [],
                    "tool": "ViennaRNA",
                    "warnings": ["扩增区域存在稳定二级结构"],
                },
                error_sequence: RuntimeError("simulated P6 failure"),
            },
        )
        with tempfile.TemporaryDirectory(prefix="wf02-p6-soft-rule-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(self._payload("A", "B"))

            summary = service.run_until_blocked(created["batch_id"])
            persisted = service.get_batch(created["batch_id"])
            result = persisted["target_design"]["targets"][0]

            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["candidate_count"], 3)
            self.assertEqual(result["p6_summary"]["status"], "PARTIAL")
            self.assertEqual(result["p6_summary"]["evaluated_count"], 1)
            self.assertEqual(result["p6_summary"]["warning_count"], 1)
            self.assertEqual(result["p6_summary"]["not_evaluated_count"], 2)
            self.assertEqual(
                result["candidates"][0]["amplicon_structure"]["status"],
                "WARNING",
            )
            self.assertIn(
                "扩增区域存在稳定二级结构",
                result["candidates"][0]["warnings"],
            )
            self.assertEqual(
                result["candidates"][1]["amplicon_structure"]["reason_code"],
                "P6_ASSESSMENT_FAILED",
            )
            self.assertEqual(
                result["candidates"][2]["amplicon_structure"]["reason_code"],
                "AMPLICON_SEQUENCE_MISSING",
            )
            self.assertEqual(len(gateway.p6_calls), 3)
            self.assertEqual(summary["target_design"]["p6_summary"]["status"], "PARTIAL")
            self._validate_batch(persisted)

    def test_waiting_wf01_child_pauses_and_resume_continues_same_child(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {"wait_for_user": True, "candidates": [_candidate(1)]},
                "B": {"candidates": [_candidate(1)]},
            }
        )
        with tempfile.TemporaryDirectory(prefix="wf02-child-wait-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(self._payload("A", "B"))

            waiting = service.run_until_blocked(created["batch_id"])
            self.assertEqual(waiting["status"], "WAITING_CHILD_INPUT")
            self.assertEqual(waiting["stage"], "N06")
            self.assertEqual(waiting["pending_child_count"], 1)
            self.assertEqual(waiting["pending_children"][0]["target_id"], "A")
            self.assertEqual(waiting["ui_message"]["type"], "agent_interaction")
            self.assertEqual(waiting["target_design"]["succeeded"], 1)
            self.assertEqual(waiting["target_design"]["waiting"], 1)
            child_id = waiting["pending_children"][0]["wf01_batch_id"]

            completed = service.submit_child_response(
                created["batch_id"],
                "A",
                {
                    "type": "agent_interaction_response",
                    "task": "PRIMER_DESIGN",
                    "interactionId": "interaction-A",
                    "answer": "CONTINUE",
                },
            )

            self.assertEqual(completed["stage"], "N20")
            self.assertEqual(completed["target_design"]["succeeded"], 2)
            self.assertEqual(len(gateway.created_payloads), 2)
            self.assertEqual(gateway.submitted_responses[0][0], child_id)
            result_a = next(
                item
                for item in service.get_batch(created["batch_id"])[
                    "target_design"
                ]["targets"]
                if item["target_id"] == "A"
            )
            self.assertEqual(result_a["wf01_batch_id"], child_id)
            self.assertEqual(len(result_a["wf01_lineage"]), 1)

    def test_missing_template_is_interaction_and_does_not_mutate_n05_snapshot(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": [_candidate(1)]},
                "B": {"candidates": [_candidate(1)]},
            }
        )
        payload = self._payload("A", "B")
        payload["targets"][0] = "A"
        with tempfile.TemporaryDirectory(prefix="wf02-template-wait-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)
            frozen_hash = created["input_snapshot_sha256"]

            waiting = service.run_until_blocked(created["batch_id"])
            self.assertEqual(waiting["status"], "WAITING_CHILD_INPUT")
            self.assertEqual(waiting["pending_children"][0]["target_id"], "A")
            self.assertEqual(len(gateway.created_payloads), 1)
            interaction_id = waiting["ui_message"]["interactionId"]

            completed = service.submit_child_response(
                created["batch_id"],
                "A",
                {
                    "type": "agent_interaction_response",
                    "task": "MULTIPLEX_PANEL",
                    "interactions": [
                        {
                            "interactionId": interaction_id,
                            "answer": "PROVIDE_TEMPLATE",
                            "fieldValues": {"sequence": "TGCA" * 40},
                        }
                    ],
                },
            )

            self.assertEqual(completed["stage"], "N20")
            self.assertEqual(completed["target_design"]["succeeded"], 2)
            self.assertEqual(len(gateway.created_payloads), 2)
            persisted = service.get_batch(created["batch_id"])
            self.assertEqual(persisted["input_snapshot_sha256"], frozen_hash)
            source_a = next(
                item
                for item in persisted["user_input_snapshot"]["targets"]
                if item["target_id"] == "A"
            )["source_input"]
            self.assertEqual(source_a, "A")
            result_a = next(
                item
                for item in persisted["target_design"]["targets"]
                if item["target_id"] == "A"
            )
            self.assertEqual(result_a["supplemental_input"]["sequence"], "TGCA" * 40)
            self.assertRegex(
                result_a["supplemental_input_sha256"],
                r"^[0-9a-f]{64}$",
            )
            self._validate_batch(persisted)

    def test_child_exception_is_retryable_and_loop_continues(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": [_candidate(1)]},
                "B": {"raise_on_run": True},
                "C": {"candidates": [_candidate(1)]},
                "D": {"missing_report_target": True},
            }
        )
        with tempfile.TemporaryDirectory(prefix="wf02-child-error-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(self._payload("A", "B", "C", "D"))

            waiting = service.run_until_blocked(created["batch_id"])
            persisted = service.get_batch(created["batch_id"])

            self.assertEqual(waiting["status"], "WAITING_TARGET_TOOL_RETRY")
            self.assertEqual(waiting["stage"], "N06")
            self.assertEqual(waiting["next_action"], "RETRY_TARGET_DESIGN")
            self.assertEqual(waiting["target_design"]["succeeded"], 2)
            self.assertEqual(waiting["target_design"]["failed"], 1)
            self.assertEqual(waiting["target_design"]["tool_retry"], 1)
            results = {
                item["target_id"]: item
                for item in persisted["target_design"]["targets"]
            }
            self.assertEqual(results["A"]["status"], "PASS")
            self.assertEqual(results["B"]["status"], "WAITING_TOOL_RETRY")
            self.assertEqual(
                results["B"]["failure"]["code"],
                "WF01_CHILD_TOOL_ERROR",
            )
            self.assertTrue(results["B"]["failure"]["retryable"])
            self.assertEqual(results["C"]["status"], "PASS")
            self.assertEqual(results["D"]["status"], "FAIL")
            self.assertEqual(
                results["D"]["failure"]["code"],
                "WF01_REPORT_INVALID",
            )
            self.assertEqual(len(gateway.run_calls), 4)

            gateway.behavior_by_target["B"] = {"candidates": [_candidate(1)]}
            completed = service.run_until_blocked(created["batch_id"])
            self.assertEqual(completed["stage"], "N20")
            self.assertEqual(completed["target_design"]["succeeded"], 3)
            self.assertEqual(completed["target_design"]["failed"], 1)

    def test_rest_run_and_child_response_endpoints_reach_n09(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {"wait_for_user": True, "candidates": [_candidate(1)]},
                "B": {"candidates": [_candidate(1)]},
            }
        )
        with tempfile.TemporaryDirectory(prefix="wf02-rest-n09-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            service.wf02_v01.gateway = gateway
            with patch.object(api, "service", service):
                with TestClient(api.app) as client:
                    created_response = client.post(
                        "/v1/wf02/tasks",
                        json=self._payload("A", "B"),
                    )
                    self.assertEqual(
                        created_response.status_code,
                        200,
                        created_response.text,
                    )
                    batch_id = created_response.json()["batch_id"]

                    waiting_response = client.post(
                        f"/v1/wf02/tasks/{batch_id}/run-until-blocked"
                    )
                    self.assertEqual(
                        waiting_response.status_code,
                        200,
                        waiting_response.text,
                    )
                    waiting = waiting_response.json()
                    self.assertEqual(waiting["status"], "WAITING_CHILD_INPUT")
                    self.assertEqual(waiting["pending_child_count"], 1)

                    resumed_response = client.post(
                        (
                            f"/v1/wf02/tasks/{batch_id}/targets/A/"
                            "interactions/response"
                        ),
                        json={
                            "type": "agent_interaction_response",
                            "task": "PRIMER_DESIGN",
                            "interactionId": "interaction-A",
                            "answer": "CONTINUE",
                        },
                    )
                    self.assertEqual(
                        resumed_response.status_code,
                        200,
                        resumed_response.text,
                    )
                    resumed = resumed_response.json()
                    self.assertEqual(resumed["stage"], "N20")
                    self.assertEqual(resumed["target_design"]["succeeded"], 2)

    def test_n10_n13_calls_p5_directly_and_assigns_default_cy5(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": [_candidate(1)]},
                "B": {"candidates": [_candidate(1)]},
            }
        )
        with tempfile.TemporaryDirectory(prefix="wf02-n10-n13-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(self._payload("A", "B"))

            summary = service.run_until_blocked(created["batch_id"])
            persisted = service.get_batch(created["batch_id"])

            self.assertEqual(summary["stage"], "N20")
            self.assertEqual(summary["status"], "READY_FOR_RELAXATION")
            self.assertTrue(summary["phase_03_complete"])
            self.assertEqual(
                persisted["multiplex"]["screening"]["strict_count"], 0
            )
            self.assertTrue(
                all(
                    "INSTRUMENT_PROFILE_UNVERIFIED" in panel["risk_reasons"]
                    for panel in persisted["multiplex"]["screening"]["panels"]
                )
            )
            self.assertEqual(len(gateway.p5_calls), 1)
            self.assertEqual(gateway.p5_calls[0]["host_species"], "pig")
            self.assertNotIn("existing_targets", gateway.p5_calls[0])
            internal = persisted["internal_control"]
            self.assertEqual(internal["status"], "COMPLETED")
            self.assertEqual(internal["candidate_gene_count"], 2)
            self.assertEqual(internal["primer_scheme_count"], 3)
            self.assertEqual(internal["quantity_verdict"], "PASS")
            self.assertTrue(internal["combination_pool_member"])
            self.assertEqual(
                internal["cross_compatibility_status"], "P4_COMPLETED"
            )
            reservation = internal["channel_reservation"]
            self.assertEqual(reservation["status"], "RESERVED")
            self.assertEqual(reservation["fluorophore"], "Cy5")
            self.assertEqual(reservation["quencher"], "BHQ-2")
            self.assertTrue(reservation["automatic_alternatives_allowed"])
            self.assertEqual(reservation["assignment_owner"], "WF-02")
            self.assertEqual(reservation["validation_owner"], "P4")
            self.assertEqual(reservation["p4_validation_status"], "COMPLETED")
            self.assertEqual(reservation["required_signal_channels"], 3)
            self.assertEqual(
                [
                    item["node"]
                    for item in persisted["workflow_trace"]
                    if item["node"] in {"N10", "N11", "N12", "N13"}
                ],
                ["N10", "N11", "N12", "N13"],
            )
            self._validate_batch(persisted)

    def test_each_target_multifasta_is_forwarded_as_one_alignment_target(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": [_candidate(1)]},
                "B": {"candidates": [_candidate(1)]},
            }
        )
        payload = self._payload("A", "B")
        payload["include_internal_control"] = False
        payload["targets"][0].pop("sequence")
        payload["targets"][0]["fasta_text"] = (
            ">A_strain_1\n" + "ACGT" * 40 + "\n"
            ">A_strain_2\n" + "ACGT" * 39 + "ACGA\n"
        )
        with tempfile.TemporaryDirectory(prefix="wf02-multifasta-target-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)

            service.run_until_blocked(created["batch_id"])

            self.assertEqual(len(gateway.created_payloads), 2)
            first = gateway.created_payloads[0]
            self.assertEqual(first["sequence_mode"], "alignment_target")
            self.assertEqual(first["fasta_text"].count(">"), 2)
            self.assertNotIn("target_manifest", first)

    def test_supplemental_uploaded_file_cannot_be_reused_for_another_target(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": [_candidate(1)]},
                "B": {"candidates": [_candidate(1)]},
            }
        )
        payload = self._payload("A", "B")
        payload["include_internal_control"] = False
        for target in payload["targets"]:
            target.pop("sequence")
        artifact_id = "himat-" + "a" * 32
        with tempfile.TemporaryDirectory(prefix="wf02-supplement-file-map-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)
            waiting_a = service.run_until_blocked(created["batch_id"])

            waiting_b = service.submit_child_response(
                created["batch_id"],
                "A",
                {
                    "type": "agent_interaction_response",
                    "task": "MULTIPLEX_PANEL",
                    "interactionId": waiting_a["ui_message"]["interactionId"],
                    "answer": "PROVIDE_TEMPLATE",
                    "fieldValues": {"input_artifact_id": artifact_id},
                },
            )
            self.assertEqual(waiting_b["pending_children"][0]["target_id"], "B")

            with self.assertRaises(InputValidationError):
                service.submit_child_response(
                    created["batch_id"],
                    "B",
                    {
                        "type": "agent_interaction_response",
                        "task": "MULTIPLEX_PANEL",
                        "interactionId": waiting_b["ui_message"]["interactionId"],
                        "answer": "PROVIDE_TEMPLATE",
                        "fieldValues": {"input_artifact_id": artifact_id},
                    },
                )

    def test_n14_explicit_input_skip_never_calls_p5(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": [_candidate(1)]},
                "B": {"candidates": [_candidate(1)]},
            }
        )
        payload = self._payload("A", "B")
        payload["include_internal_control"] = False
        with tempfile.TemporaryDirectory(prefix="wf02-n14-skip-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)

            summary = service.run_until_blocked(created["batch_id"])
            persisted = service.get_batch(created["batch_id"])

            self.assertEqual(summary["stage"], "N20")
            self.assertEqual(summary["status"], "READY_FOR_RELAXATION")
            self.assertEqual(gateway.p5_calls, [])
            self.assertEqual(
                persisted["internal_control"]["status"], "SKIPPED_BY_USER"
            )
            self.assertFalse(
                persisted["internal_control"]["combination_pool_member"]
            )
            self.assertEqual(
                persisted["internal_control"]["channel_reservation"]["status"],
                "SKIPPED",
            )
            self._validate_batch(persisted)

    def test_one_or_two_internal_schemes_continue_with_caution(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": [_candidate(1)]},
                "B": {"candidates": [_candidate(1)]},
            },
            p5_outcomes=[
                {
                    "status": "COMPLETED",
                    "verdict": "PASS",
                    "gene_selected": "ACTB",
                    "gene_attempts": [{"gene": "ACTB", "status": "PASS"}],
                    "candidates": [_candidate(1), _candidate(2)],
                    "warnings": [],
                }
            ],
        )
        with tempfile.TemporaryDirectory(prefix="wf02-n12-caution-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(self._payload("A", "B"))

            summary = service.run_until_blocked(created["batch_id"])
            internal = service.get_batch(created["batch_id"])["internal_control"]

            self.assertEqual(summary["stage"], "N20")
            self.assertEqual(internal["status"], "COMPLETED_WITH_CAUTION")
            self.assertEqual(internal["quantity_verdict"], "CAUTION")
            self.assertEqual(internal["primer_scheme_count"], 2)

    def test_p5_failure_automatically_continues_without_internal_control(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": [_candidate(1)]},
                "B": {"candidates": [_candidate(1)]},
            },
            p5_outcomes=[
                {
                    "status": "NOT_CONFIGURED",
                    "verdict": "FAIL",
                    "candidates": [],
                    "warnings": ["缺少内标序列资源"],
                    "recommendation": "提供多品系基因序列",
                },
            ],
        )
        with tempfile.TemporaryDirectory(prefix="wf02-p5-fail-continue-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(self._payload("A", "B"))

            completed = service.run_until_blocked(created["batch_id"])

            self.assertEqual(completed["stage"], "N20")
            self.assertFalse(completed["pending_internal_control"])
            self.assertNotIn("ui_message", completed)
            self.assertEqual(len(gateway.p5_calls), 1)
            persisted = service.get_batch(created["batch_id"])
            internal = persisted["internal_control"]
            self.assertEqual(
                internal["status"], "FAILED_CONTINUE_WITHOUT_INTERNAL"
            )
            self.assertEqual(internal["p5_status"], "NOT_CONFIGURED")
            self.assertEqual(internal["p5_verdict"], "FAIL")
            self.assertFalse(internal["combination_pool_member"])
            self.assertEqual(internal["attempt_count"], 1)
            self.assertEqual(len(internal["attempt_history"]), 1)
            self.assertEqual(len(gateway.p4_calls), 1)
            self.assertEqual(len(gateway.p4_calls[0]["panel"]), 2)
            self._validate_batch(persisted)

    def test_channel_capacity_block_automatically_continues_without_internal(self) -> None:
        gateway = FakeWF01Gateway(
            {
                target_id: {"candidates": [_candidate(1)]}
                for target_id in ("A", "B", "C", "D")
            }
        )
        payload = self._payload("A", "B", "C", "D")
        payload["runtime_config"] = {"instrument": "Roche LightCycler 96"}
        with tempfile.TemporaryDirectory(prefix="wf02-channel-block-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)

            completed = service.run_until_blocked(created["batch_id"])
            self.assertEqual(completed["stage"], "N20")
            self.assertFalse(completed["pending_internal_control"])
            persisted = service.get_batch(created["batch_id"])
            internal = persisted["internal_control"]
            reservation = internal["channel_reservation"]
            self.assertEqual(reservation["status"], "BLOCKED")
            self.assertEqual(reservation["required_signal_channels"], 5)
            self.assertEqual(reservation["available_report_channels"], 4)
            self.assertEqual(
                internal["status"], "FAILED_CONTINUE_WITHOUT_INTERNAL"
            )
            self.assertEqual(internal["p5_status"], "COMPLETED")
            self.assertFalse(internal["combination_pool_member"])
            self.assertEqual(len(gateway.p4_calls), 1)
            self.assertEqual(len(gateway.p4_calls[0]["panel"]), 4)
            self._validate_batch(persisted)

    def test_five_targets_use_all_cfx96_channels_and_drop_requested_internal(self) -> None:
        target_ids = ("A", "B", "C", "D", "E")
        gateway = FakeWF01Gateway(
            {
                target_id: {"candidates": [_candidate(1)]}
                for target_id in target_ids
            }
        )
        payload = self._payload(*target_ids)
        payload["runtime_config"] = {"instrument": "CFX96"}
        with tempfile.TemporaryDirectory(prefix="wf02-five-targets-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)

            summary = service.run_until_blocked(created["batch_id"])
            persisted = service.get_batch(created["batch_id"])

            self.assertEqual(summary["stage"], "N20")
            internal = persisted["internal_control"]
            self.assertTrue(internal["requested"])
            self.assertEqual(
                internal["status"], "FAILED_CONTINUE_WITHOUT_INTERNAL"
            )
            self.assertEqual(
                internal["channel_reservation"]["required_signal_channels"], 6
            )
            assignment = persisted["multiplex"]["screening"]["channel_assignment"]
            self.assertEqual(len(assignment), 5)
            self.assertEqual(
                [item["fluorophore"] for item in assignment],
                ["FAM", "VIC", "ROX", "Cy5", "Cy5.5"],
            )
            self.assertEqual(len(gateway.p4_calls), 1)
            self.assertEqual(len(gateway.p4_calls[0]["panel"]), 5)
            self._validate_batch(persisted)

    def test_rest_p5_failure_returns_completed_target_only_path(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": [_candidate(1)]},
                "B": {"candidates": [_candidate(1)]},
            },
            p5_outcomes=[
                {
                    "status": "NOT_CONFIGURED",
                    "verdict": "FAIL",
                    "candidates": [],
                    "warnings": ["缺少内标资源"],
                }
            ],
        )
        with tempfile.TemporaryDirectory(prefix="wf02-rest-internal-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            service.wf02_v01.gateway = gateway
            with patch.object(api, "service", service):
                with TestClient(api.app) as client:
                    created = client.post(
                        "/v1/wf02/tasks", json=self._payload("A", "B")
                    ).json()
                    waiting_response = client.post(
                        f"/v1/wf02/tasks/{created['batch_id']}/run-until-blocked"
                    )
                    self.assertEqual(waiting_response.status_code, 200)
                    completed = waiting_response.json()
                    self.assertEqual(completed["stage"], "N26")
                    self.assertEqual(
                        completed["status"],
                        "FAILED_NO_COMPATIBLE_PANEL",
                    )
                    self.assertFalse(completed["pending_internal_control"])
                    self.assertEqual(
                        completed["background_execution"]["state"],
                        "COMPLETED",
                    )
                    self.assertEqual(
                        completed["ui_message"]["type"],
                        "primer_design_output",
                    )

                    invalid_response = client.post(
                        (
                            f"/v1/wf02/tasks/{created['batch_id']}/"
                            "internal-control/interactions/response"
                        ),
                        json={
                            "type": "agent_interaction_response",
                            "task": "MULTIPLEX_PANEL",
                            "interactionId": "no-pending-interaction",
                            "answer": "CONTINUE_WITHOUT_INTERNAL_CONTROL",
                        },
                    )

                    self.assertEqual(invalid_response.status_code, 422)

    def test_internal_channel_falls_back_to_supported_far_red_and_p4_validates(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": [_candidate(1)]},
                "B": {"candidates": [_candidate(1)]},
            }
        )
        payload = self._payload("A", "B")
        payload["runtime_config"] = {"instrument": "Roche LightCycler 96"}
        with tempfile.TemporaryDirectory(prefix="wf02-channel-fallback-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)

            summary = service.run_until_blocked(created["batch_id"])
            persisted = service.get_batch(created["batch_id"])

            self.assertEqual(summary["stage"], "N20")
            reservation = persisted["internal_control"]["channel_reservation"]
            self.assertEqual(reservation["suggested_fluorophore"], "Cy5")
            self.assertEqual(reservation["fluorophore"], "Cy5.5")
            self.assertEqual(
                reservation["internal_control_selection_basis"],
                "FARTHEST_SUPPORTED_FAR_RED_FALLBACK",
            )
            assignment = persisted["multiplex"]["screening"]["channel_assignment"]
            self.assertEqual(assignment[-1]["target_id"], "internal_control")
            self.assertEqual(assignment[-1]["fluorophore"], "Cy5.5")
            self.assertEqual(len(gateway.p4_calls), 3)
            self.assertTrue(
                all(
                    call["panel"][-1]["fluorophore"] == "Cy5.5"
                    for call in gateway.p4_calls
                )
            )
            self._validate_batch(persisted)

    def test_n15_n20_calls_existing_p4_for_every_complete_combination(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {
                    "candidates": [
                        _candidate(1, amplicon_sequence="ACGT" * 20)
                    ]
                },
                "B": {
                    "candidates": [
                        _candidate(1, amplicon_sequence="ACGT" * 30)
                    ]
                },
            }
        )
        payload = self._payload("A", "B")
        payload["runtime_config"] = {"instrument": "CFX96"}
        with tempfile.TemporaryDirectory(prefix="wf02-n15-n20-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)

            summary = service.run_until_blocked(created["batch_id"])
            persisted = service.get_batch(created["batch_id"])
            screening = persisted["multiplex"]["screening"]

            self.assertEqual(summary["stage"], "N20")
            self.assertEqual(summary["status"], "READY_FOR_BALANCE")
            self.assertEqual(summary["next_action"], "RUN_BALANCE_ASSESSMENT")
            self.assertTrue(summary["phase_04_complete"])
            self.assertEqual(screening["total_combinations"], 3)
            self.assertEqual(screening["cursor"], 3)
            self.assertEqual(screening["strict_count"], 3)
            self.assertEqual(screening["risk_count"], 0)
            self.assertEqual(len(gateway.p4_calls), 3)
            self.assertTrue(
                all(len(call["panel"]) == 3 for call in gateway.p4_calls)
            )
            self.assertEqual(
                [
                    (item["target_id"], item["fluorophore"])
                    for item in screening["channel_assignment"]
                ],
                [("A", "FAM"), ("B", "VIC"), ("internal_control", "Cy5")],
            )
            self.assertTrue(
                all(
                    panel["p4_verdict"] == "COMPATIBLE"
                    and panel["classification"] == "STRICT_COMPATIBLE"
                    for panel in screening["panels"]
                )
            )
            self._validate_batch(persisted)

    def test_n19_length_rule_is_hard_filter_without_rewriting_p4_result(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {
                    "candidates": [
                        _candidate(1, amplicon_sequence="ACGT" * 25)
                    ]
                },
                "B": {
                    "candidates": [
                        _candidate(1, amplicon_sequence=("ACGT" * 26) + "AC")
                    ]
                },
            }
        )
        payload = self._payload("A", "B")
        payload["include_internal_control"] = False
        payload["runtime_config"] = {"instrument": "CFX96"}
        with tempfile.TemporaryDirectory(prefix="wf02-r17-hard-filter-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)

            summary = service.run_until_blocked(created["batch_id"])
            screening = service.get_batch(created["batch_id"])["multiplex"][
                "screening"
            ]
            panel = screening["panels"][0]

            self.assertEqual(summary["status"], "READY_FOR_RELAXATION")
            self.assertEqual(panel["p4_verdict"], "COMPATIBLE")
            self.assertEqual(panel["p4_result"]["verdict"], "COMPATIBLE")
            self.assertEqual(panel["classification"], "INCOMPATIBLE")
            self.assertEqual(
                panel["amplicon_length_check"]["status"],
                "FAIL",
            )
            self.assertTrue(panel["amplicon_length_check"]["hard_filter"])
            self.assertEqual(
                panel["amplicon_length_check"]["scope"], "ALL_PANEL_MEMBERS"
            )
            self.assertIn(
                "R17_MINIMUM_AMPLICON_LENGTH_DIFFERENCE_FAILED",
                panel["risk_reasons"],
            )

    def test_channel_projection_preserves_candidate_quencher_everywhere(self) -> None:
        candidate_a = _candidate(1, amplicon_sequence="ACGT" * 20)
        candidate_b = _candidate(1, amplicon_sequence="ACGT" * 30)
        candidate_a["probe"].update(
            {"fluorophore": "SOURCE-A", "quencher": "MGB-NFQ"}
        )
        candidate_b["probe"].update(
            {"fluorophore": "SOURCE-B", "quencher": "BHQ-1"}
        )
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": [candidate_a]},
                "B": {"candidates": [candidate_b]},
            }
        )
        payload = self._payload("A", "B")
        payload["include_internal_control"] = False
        payload["runtime_config"] = {"instrument": "CFX96"}
        with tempfile.TemporaryDirectory(prefix="wf02-channel-projection-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)
            service.run_until_blocked(created["batch_id"])

            p4_entries = gateway.p4_calls[0]["panel"]
            self.assertEqual(
                [item["fluorophore"] for item in p4_entries],
                ["FAM", "VIC"],
            )
            self.assertEqual(
                [item["quencher"] for item in p4_entries],
                ["MGB-NFQ", "BHQ-1"],
            )
            self.assertEqual(
                [item["quencher_source"] for item in p4_entries],
                ["CANDIDATE_PROBE", "CANDIDATE_PROBE"],
            )
            screening = service.get_batch(created["batch_id"])["multiplex"][
                "screening"
            ]
            assignment = screening["panels"][0]["channel_assignment"]
            self.assertEqual(
                [item["quencher"] for item in assignment],
                ["MGB-NFQ", "BHQ-1"],
            )

            service.run_until_blocked(created["batch_id"])
            report = service.get_report(created["batch_id"])
            members = report["top3_panels"][0]["members"]
            self.assertEqual(
                [item["quencher"] for item in members],
                ["MGB-NFQ", "BHQ-1"],
            )
            self.assertEqual(
                [item["probe"]["quencher"] for item in members],
                ["MGB-NFQ", "BHQ-1"],
            )
            self.assertEqual(
                [item["probe"]["fluorophore"] for item in members],
                ["FAM", "VIC"],
            )
            self._validate_report(report)

    def test_n30_selects_global_score_top3_across_strict_and_risk(self) -> None:
        def panel(
            panel_id: str,
            classification: str,
            score: float,
            suffix: str,
        ) -> dict[str, Any]:
            return {
                "panel_id": panel_id,
                "classification": classification,
                "forced_score": score,
                "p4_input": [
                    {
                        "target_id": "A",
                        "candidate_id": f"A-{suffix}",
                        "fwd_seq": f"ACGT{suffix}",
                        "rev_seq": f"TGCA{suffix}",
                        "probe_seq": f"GATT{suffix}",
                    },
                    {
                        "target_id": "B",
                        "candidate_id": f"B-{suffix}",
                        "fwd_seq": f"CCGT{suffix}",
                        "rev_seq": f"AGCA{suffix}",
                        "probe_seq": f"CATT{suffix}",
                    },
                ],
                "channel_assignment": [],
            }

        with tempfile.TemporaryDirectory(prefix="wf02-global-top3-") as state_dir:
            service = self._service(state_dir, FakeWF01Gateway({}))
            batch = {
                "batch_id": str(uuid4()),
                "workflow_trace": [],
                "multiplex": {
                    "balance": {
                        "selected_panels": [
                            panel("risk-high", "RISK_COMPLETE", 100.0, "A"),
                            panel("strict-mid", "STRICT_COMPATIBLE", 90.0, "B"),
                            panel("risk-mid", "RISK_COMPLETE", 80.0, "C"),
                            panel("strict-low", "STRICT_COMPATIBLE", 70.0, "D"),
                        ]
                    }
                },
            }
            with patch.object(
                service,
                "_score_panel",
                side_effect=lambda item: {
                    "final_score": item["forced_score"]
                },
            ):
                selection = service._score_deduplicate_and_select(batch)

        self.assertEqual(selection["selection_mode"], "GLOBAL_SCORE_TOP3")
        self.assertEqual(
            [item["panel_id"] for item in selection["top3"]],
            ["risk-high", "strict-mid", "risk-mid"],
        )
        self.assertEqual(
            [item["tier"] for item in selection["top3"]],
            ["RISK", "STRICT", "RISK"],
        )

    def test_panel_screening_uses_persisted_batches_without_truncation(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {
                    "candidates": [
                        _candidate(1, amplicon_sequence="ACGT" * 20),
                        _candidate(2, amplicon_sequence="ACGT" * 21),
                    ]
                },
                "B": {
                    "candidates": [
                        _candidate(1, amplicon_sequence="ACGT" * 30),
                        _candidate(2, amplicon_sequence="ACGT" * 31),
                    ]
                },
            }
        )
        payload = self._payload("A", "B")
        payload["include_internal_control"] = False
        payload["runtime_config"] = {"instrument": "CFX96"}
        with tempfile.TemporaryDirectory(prefix="wf02-p4-cursor-") as state_dir:
            service = WF02V01BatchService(
                state_dir,
                load_config(
                    overrides={
                        "multiplex": {"maximum_panel_combinations": 2}
                    }
                ),
                gateway=gateway,
            )
            created = service.create_batch(payload)

            first = service.run_until_blocked(created["batch_id"])
            first_persisted = service.get_batch(created["batch_id"])
            self.assertEqual(first["stage"], "N18")
            self.assertEqual(first["status"], "SCREENING_PANELS")
            self.assertEqual(first["next_action"], "CONTINUE_PANEL_SCREENING")
            self.assertEqual(
                first_persisted["multiplex"]["screening"]["cursor"], 2
            )
            self._validate_batch(first_persisted)

            completed = service.run_until_blocked(created["batch_id"])
            persisted = service.get_batch(created["batch_id"])
            screening = persisted["multiplex"]["screening"]
            self.assertEqual(completed["stage"], "N20")
            self.assertEqual(screening["total_combinations"], 4)
            self.assertEqual(screening["cursor"], 4)
            self.assertEqual(len(screening["panels"]), 4)
            self.assertEqual(len(gateway.p4_calls), 4)
            self._validate_batch(persisted)

    def test_p4_not_evaluated_keeps_cursor_and_retries_same_panel(self) -> None:
        compatible = {
            "status": "COMPLETED",
            "verdict": "COMPATIBLE",
            "issues": [],
        }
        gateway = FakeWF01Gateway(
            {
                "A": {
                    "candidates": [
                        _candidate(1, amplicon_sequence="ACGT" * 20)
                    ]
                },
                "B": {
                    "candidates": [
                        _candidate(1, amplicon_sequence="ACGT" * 30)
                    ]
                },
            },
            p4_outcomes=[
                {
                    "status": "NOT_EVALUATED",
                    "verdict": None,
                    "issues": ["Primer3 unavailable"],
                },
                compatible,
            ],
        )
        payload = self._payload("A", "B")
        payload["include_internal_control"] = False
        payload["runtime_config"] = {"instrument": "CFX96"}
        with tempfile.TemporaryDirectory(prefix="wf02-p4-retry-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)

            waiting = service.run_until_blocked(created["batch_id"])
            screening = service.get_batch(created["batch_id"])["multiplex"][
                "screening"
            ]
            self.assertEqual(waiting["stage"], "N17")
            self.assertEqual(waiting["status"], "WAITING_P4_RETRY")
            self.assertEqual(screening["cursor"], 0)
            self.assertEqual(len(screening["panels"]), 0)
            self.assertEqual(waiting["flowControl"]["resumeTool"], "run_wf02_until_blocked")

            completed = service.run_until_blocked(created["batch_id"])
            screening = service.get_batch(created["batch_id"])["multiplex"][
                "screening"
            ]
            self.assertEqual(completed["stage"], "N20")
            self.assertEqual(completed["status"], "READY_FOR_BALANCE")
            self.assertEqual(screening["cursor"], 1)
            self.assertEqual(len(gateway.p4_calls), 2)
            self._validate_batch(service.get_batch(created["batch_id"]))

    def test_n21_calls_existing_p4_with_relaxed_dimer_threshold(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": [_candidate(1, amplicon_sequence="ACGT" * 20)]},
                "B": {"candidates": [_candidate(1, amplicon_sequence="ACGT" * 30)]},
            },
            p4_outcomes=[
                _p4_outcome("INCOMPATIBLE"),
                _p4_outcome("COMPATIBLE"),
            ],
        )
        payload = self._payload("A", "B")
        payload["include_internal_control"] = False
        payload["runtime_config"] = {"instrument": "CFX96"}
        with tempfile.TemporaryDirectory(prefix="wf02-n21-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)

            screened = service.run_until_blocked(created["batch_id"])
            self.assertEqual(screened["stage"], "N20")
            self.assertEqual(screened["status"], "READY_FOR_RELAXATION")

            relaxed = service.run_until_blocked(created["batch_id"])
            persisted = service.get_batch(created["batch_id"])
            relaxation = persisted["multiplex"]["relaxation"]

            self.assertEqual(relaxed["stage"], "N24")
            self.assertEqual(relaxed["status"], "READY_FOR_BALANCE")
            self.assertTrue(relaxed["phase_05_complete"])
            self.assertEqual(relaxation["status"], "COMPLETED_RISK_FULL")
            self.assertEqual(len(relaxation["full_risk_panels"]), 1)
            self.assertEqual(
                relaxation["full_risk_panels"][0]["classification"],
                "RISK_COMPLETE",
            )
            self.assertIn(
                "DIMER_THRESHOLD_RELAXED",
                relaxation["full_risk_panels"][0]["risk_reasons"],
            )
            self.assertTrue(gateway.p4_calls[1]["relaxed"])
            self.assertEqual(gateway.p4_calls[1]["tm_spread_limit"], 3.0)
            self.assertEqual(
                [
                    item["node"]
                    for item in persisted["workflow_trace"]
                    if item["node"] in {"N21", "N22", "N23", "N24", "N25", "N26"}
                ],
                ["N21", "N22", "N23", "N24", "N25", "N26"],
            )
            self._validate_batch(persisted)

    def test_n22_relaxes_primer_and_probe_tm_spreads_separately_to_4c(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": [_candidate(1, amplicon_sequence="ACGT" * 20)]},
                "B": {"candidates": [_candidate(1, amplicon_sequence="ACGT" * 30)]},
            },
            p4_outcomes=[
                _p4_outcome("INCOMPATIBLE", primer_spread=3.5, probe_spread=3.8),
                _p4_outcome("MARGINAL", primer_spread=3.5, probe_spread=3.8),
                _p4_outcome("COMPATIBLE", primer_spread=3.5, probe_spread=3.8),
            ],
        )
        payload = self._payload("A", "B")
        payload["include_internal_control"] = False
        payload["runtime_config"] = {"instrument": "CFX96"}
        with tempfile.TemporaryDirectory(prefix="wf02-n22-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)
            service.run_until_blocked(created["batch_id"])

            relaxed = service.run_until_blocked(created["batch_id"])
            persisted = service.get_batch(created["batch_id"])
            relaxation = persisted["multiplex"]["relaxation"]

            self.assertEqual(relaxed["stage"], "N24")
            self.assertEqual(relaxation["status"], "COMPLETED_RISK_FULL")
            self.assertEqual(
                [call.get("tm_spread_limit") for call in gateway.p4_calls],
                [None, 3.0, 4.0],
            )
            panel = relaxation["full_risk_panels"][0]
            self.assertIn(
                "PRIMER_AND_PROBE_TM_SPREAD_LE_4C",
                panel["relaxation_applied"],
            )
            self.assertIn(
                "TM_SPREAD_LIMIT_RELAXED_TO_4C", panel["risk_reasons"]
            )
            round_n22 = next(
                item for item in relaxation["rounds"] if item["node"] == "N22"
            )
            self.assertEqual(
                round_n22["tm_scope"], "PRIMERS_AND_PROBES_SEPARATELY"
            )
            self._validate_batch(persisted)

    def test_n26_stops_after_all_relaxations_fail_and_returns_guidance(self) -> None:
        incompatible = _p4_outcome("INCOMPATIBLE", primer_spread=5.0, probe_spread=5.0)
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": [_candidate(1)]},
                "B": {"candidates": [_candidate(1)]},
            },
            p4_outcomes=[incompatible, incompatible, incompatible],
        )
        payload = self._payload("A", "B")
        payload["include_internal_control"] = False
        payload["runtime_config"] = {"instrument": "CFX96"}
        with tempfile.TemporaryDirectory(prefix="wf02-n26-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)
            service.run_until_blocked(created["batch_id"])

            failed = service.run_until_blocked(created["batch_id"])
            persisted = service.get_batch(created["batch_id"])
            relaxation = persisted["multiplex"]["relaxation"]

            self.assertEqual(failed["stage"], "N26")
            self.assertEqual(failed["status"], "FAILED_NO_COMPATIBLE_PANEL")
            self.assertEqual(failed["next_action"], "REVIEW_FAILURE_GUIDANCE")
            self.assertEqual(relaxation["status"], "FAILED")
            self.assertEqual(len(relaxation["failure_suggestions"]), 3)
            self.assertIn("SYBR Green", relaxation["failure_suggestions"][2])
            self.assertEqual(
                {item["status"] for item in relaxation["independent_target_schemes"]},
                {"AVAILABLE_AS_SINGLEPLEX"},
            )
            report = service.get_report(created["batch_id"])
            self.assertEqual(report["result_summary"]["status"], "FAIL")
            self.assertEqual(report["selection_mode"], "NO_COMPATIBLE_PANEL")
            self.assertEqual(report["top3_panels"], [])
            self.assertEqual(
                report["outcome"]["primary_failure_stage"],
                "PANEL_COMPATIBILITY",
            )
            self.assertEqual(set(report["exports"]), {"html"})
            self.assertEqual(failed["ui_message"]["type"], "primer_design_output")
            self.assertEqual(
                failed["conversation_context"]["lifecycleState"],
                "FAILED_NO_COMPATIBLE_PANEL",
            )
            self.assertEqual(
                report["candidate_comparison"]["status"], "NOT_APPLICABLE"
            )
            self.assertEqual(report["result_explanation"]["status"], "COMPLETED")
            self.assertIn(
                "WF02_TERMINAL_REPORT_COMPLETED",
                {
                    item["event_type"]
                    for item in report["traceability"]["events"]
                },
            )
            self._validate_report(report)
            self._validate_batch(persisted)

    def test_n26_uses_incomplete_coverage_terminal_status_and_report(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": [_candidate(1)]},
                "B": {
                    "candidates": [],
                    "conclusion": {
                        "status": "FAIL",
                        "reason_codes": ["PRIMER3_GENERATED_NO_CANDIDATES"],
                    },
                },
            }
        )
        payload = self._payload("A", "B")
        payload["include_internal_control"] = False
        with tempfile.TemporaryDirectory(prefix="wf02-incomplete-terminal-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)
            service.run_until_blocked(created["batch_id"])

            failed = service.run_until_blocked(created["batch_id"])
            persisted = service.get_batch(created["batch_id"])
            report = service.get_report(created["batch_id"])

            self.assertEqual(
                failed["status"], "FAILED_INCOMPLETE_TARGET_COVERAGE"
            )
            self.assertEqual(failed["stage"], "N26")
            self.assertEqual(
                persisted["outcome"]["primary_failure_stage"], "TARGET_DESIGN"
            )
            self.assertEqual(
                persisted["outcome"]["panel_status"],
                "NO_COMPATIBLE_PANEL_FOR_AVAILABLE_TARGETS",
            )
            self.assertIn(
                "PRIMER3_GENERATED_NO_CANDIDATES",
                persisted["outcome"]["reason_codes"],
            )
            self.assertEqual(
                report["selection_mode"], "INCOMPLETE_TARGET_COVERAGE"
            )
            self.assertEqual(report["result_summary"]["status"], "FAIL")
            self.assertEqual(report["top3_panels"], [])
            self.assertEqual(failed["ui_message"]["type"], "primer_design_output")
            self.assertIn(
                "PRIMER3_GENERATED_NO_CANDIDATES",
                failed["ui_message"]["data"]["issues"][-1],
            )
            output_schema = json.loads(
                (
                    ROOT
                    / "schemas"
                    / "primer-design-output.schema.json"
                ).read_text(encoding="utf-8")
            )
            Draft202012Validator(output_schema).validate(
                failed["ui_message"]
            )
            html_export = service.get_export_path(
                created["batch_id"], "html"
            ).read_text(encoding="utf-8")
            csv_export = service.get_export_path(
                created["batch_id"], "csv"
            ).read_text(encoding="utf-8-sig")
            self.assertIn("PRIMER3_GENERATED_NO_CANDIDATES", html_export)
            self.assertIn("PRIMER3_GENERATED_NO_CANDIDATES", csv_export)
            new_version = service.create_version(
                created["batch_id"],
                {
                    "reason": "retry terminal workflow",
                    "user_input_changes": {
                        "include_internal_control": True
                    },
                },
            )
            self.assertEqual(
                new_version["source_batch_id"], created["batch_id"]
            )
            self._validate_batch(persisted)
            self._validate_report(report)

    def test_relaxation_p4_retry_keeps_round_cursor_and_same_panel(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": [_candidate(1, amplicon_sequence="ACGT" * 20)]},
                "B": {"candidates": [_candidate(1, amplicon_sequence="ACGT" * 30)]},
            },
            p4_outcomes=[
                _p4_outcome("INCOMPATIBLE"),
                {"status": "NOT_EVALUATED", "verdict": None},
                _p4_outcome("COMPATIBLE"),
            ],
        )
        payload = self._payload("A", "B")
        payload["include_internal_control"] = False
        payload["runtime_config"] = {"instrument": "CFX96"}
        with tempfile.TemporaryDirectory(prefix="wf02-relax-retry-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)
            service.run_until_blocked(created["batch_id"])

            waiting = service.run_until_blocked(created["batch_id"])
            persisted = service.get_batch(created["batch_id"])
            round_n21 = persisted["multiplex"]["relaxation"]["rounds"][0]
            self.assertEqual(waiting["stage"], "N21")
            self.assertEqual(waiting["status"], "WAITING_P4_RETRY")
            self.assertEqual(waiting["next_action"], "RETRY_RELAXATION")
            self.assertEqual(round_n21["cursor"], 0)
            self.assertEqual(waiting["flowControl"]["resumeTool"], "run_wf02_until_blocked")

            completed = service.run_until_blocked(created["batch_id"])
            self.assertEqual(completed["stage"], "N24")
            self.assertEqual(completed["status"], "READY_FOR_BALANCE")
            self.assertEqual(
                gateway.p4_calls[1]["panel"], gateway.p4_calls[2]["panel"]
            )
            self._validate_batch(service.get_batch(created["batch_id"]))

    def test_relaxation_uses_persisted_batch_cursor_without_truncation(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {
                    "candidates": [
                        _candidate(1, amplicon_sequence="ACGT" * 20),
                        _candidate(2, amplicon_sequence="ACGT" * 21),
                    ]
                },
                "B": {
                    "candidates": [
                        _candidate(1, amplicon_sequence="ACGT" * 30),
                        _candidate(2, amplicon_sequence="ACGT" * 31),
                    ]
                },
            },
            p4_outcomes=(
                [_p4_outcome("INCOMPATIBLE") for _ in range(4)]
                + [_p4_outcome("COMPATIBLE") for _ in range(4)]
            ),
        )
        payload = self._payload("A", "B")
        payload["include_internal_control"] = False
        payload["runtime_config"] = {"instrument": "CFX96"}
        with tempfile.TemporaryDirectory(prefix="wf02-relax-cursor-") as state_dir:
            service = WF02V01BatchService(
                state_dir,
                load_config(
                    overrides={"multiplex": {"maximum_panel_combinations": 2}}
                ),
                gateway=gateway,
            )
            created = service.create_batch(payload)

            first_screen = service.run_until_blocked(created["batch_id"])
            self.assertEqual(first_screen["status"], "SCREENING_PANELS")
            screened = service.run_until_blocked(created["batch_id"])
            self.assertEqual(screened["status"], "READY_FOR_RELAXATION")

            first_relax = service.run_until_blocked(created["batch_id"])
            persisted = service.get_batch(created["batch_id"])
            round_n21 = persisted["multiplex"]["relaxation"]["rounds"][0]
            self.assertEqual(first_relax["status"], "RELAXING_PANELS")
            self.assertEqual(first_relax["next_action"], "CONTINUE_RELAXATION")
            self.assertEqual(round_n21["cursor"], 2)
            self.assertEqual(round_n21["accepted_count"], 2)
            self.assertEqual(
                first_relax["execution_progress"],
                {
                    "phase": "RELAXATION",
                    "step": "N21",
                    "status": "RUNNING",
                    "processed_units": 2,
                    "total_units": 4,
                    "remaining_units": 2,
                    "accepted_units": 2,
                    "rejected_units": 0,
                },
            )
            first_resume_token = first_relax["resume_token"]

            completed = service.run_until_blocked(created["batch_id"])
            persisted = service.get_batch(created["batch_id"])
            round_n21 = persisted["multiplex"]["relaxation"]["rounds"][0]
            self.assertEqual(completed["status"], "READY_FOR_BALANCE")
            self.assertEqual(round_n21["cursor"], 4)
            self.assertEqual(round_n21["accepted_count"], 4)
            self.assertNotEqual(completed["resume_token"], first_resume_token)
            self.assertEqual(len(gateway.p4_calls), 8)
            self._validate_batch(persisted)

    def test_three_target_relaxation_cursor_progresses_beyond_eighty_of_125(
        self,
    ) -> None:
        candidates = [
            _candidate(index, amplicon_sequence="ACGT" * (20 + index))
            for index in range(1, 6)
        ]
        gateway = FakeWF01Gateway(
            {
                target_id: {"candidates": copy.deepcopy(candidates)}
                for target_id in ("A", "B", "C")
            },
            p4_outcomes=[_p4_outcome("INCOMPATIBLE") for _ in range(125)],
        )
        payload = self._payload("A", "B", "C")
        payload["include_internal_control"] = False
        payload["runtime_config"] = {"instrument": "CFX96"}
        with tempfile.TemporaryDirectory(
            prefix="wf02-three-target-relax-"
        ) as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)

            screened = service.run_until_blocked(created["batch_id"])
            self.assertEqual(screened["status"], "READY_FOR_RELAXATION")
            self.assertEqual(
                screened["multiplex"]["screening"]["total_combinations"],
                125,
            )

            previous_token = screened["resume_token"]
            current = screened
            for expected_cursor in range(8, 81, 8):
                current = service.run_until_blocked(
                    created["batch_id"],
                    bounded=True,
                    expected_resume_token=previous_token,
                )
                self.assertEqual(
                    current["execution_progress"]["processed_units"],
                    expected_cursor,
                )
                self.assertNotEqual(current["resume_token"], previous_token)
                previous_token = current["resume_token"]

            advanced = service.run_until_blocked(
                created["batch_id"],
                bounded=True,
                expected_resume_token=previous_token,
            )
            self.assertEqual(
                advanced["execution_progress"]["processed_units"],
                88,
            )
            self.assertTrue(advanced["execution_unit"]["state_committed"])
            self._validate_batch(service.get_batch(created["batch_id"]))

    def test_n27_to_n32_builds_scored_top3_report_and_exports(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {
                    "candidates": [
                        _candidate(
                            1,
                            score=96.0,
                            amplicon_sequence="ACGT" * 20,
                            amplicon_gc_percent=50.0,
                        )
                    ]
                },
                "B": {
                    "candidates": [
                        _candidate(
                            1,
                            score=92.0,
                            amplicon_sequence="ACGT" * 30,
                            amplicon_gc_percent=55.0,
                        )
                    ]
                },
            }
        )
        payload = self._payload("A", "B")
        payload["include_internal_control"] = False
        payload["runtime_config"] = {"instrument": "CFX96"}
        with tempfile.TemporaryDirectory(prefix="wf02-stage6-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)

            screened = service.run_until_blocked(created["batch_id"])
            self.assertEqual(screened["stage"], "N20")
            self.assertEqual(screened["status"], "READY_FOR_BALANCE")

            completed = service.run_until_blocked(created["batch_id"])
            persisted = service.get_batch(created["batch_id"])
            report = service.get_report(created["batch_id"])

            self.assertEqual(completed["status"], "COMPLETED")
            self.assertEqual(completed["stage"], "N32")
            self.assertTrue(completed["phase_06_complete"])
            self.assertEqual(completed["implemented_through_node"], "N32")
            self.assertEqual(
                completed["ui_message"]["type"], "primer_design_output"
            )
            self.assertEqual(
                [
                    item["fileType"]
                    for item in completed["ui_message"]["data"]["attachments"]
                ],
                ["HTML"],
            )
            output_schema = json.loads(
                (ROOT / "schemas" / "primer-design-output.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            Draft202012Validator(output_schema).validate(
                completed["ui_message"]
            )
            self.assertEqual(
                [
                    item["node"]
                    for item in persisted["workflow_trace"]
                    if item["node"] in {"N27", "N28", "N29", "N30", "N31", "N32"}
                ],
                ["N27", "N28", "N29", "N30", "N31", "N32"],
            )
            self.assertEqual(report["selection_mode"], "GLOBAL_SCORE_TOP3")
            self.assertEqual(len(report["top3_panels"]), 1)
            self.assertEqual(report["multiplex"]["status"], "COMPLETED")
            self.assertEqual(
                report["traceability"]["execution_route"]["terminal_stage"],
                "N32",
            )
            self.assertEqual(
                [
                    item["node"]
                    for item in report["traceability"]["workflow_trace"]
                    if item["node"] in {"N31", "N32"}
                ],
                ["N31", "N32"],
            )
            self.assertEqual(
                report["candidate_comparison"]["component"], "C29"
            )
            self.assertEqual(report["result_explanation"]["component"], "C30")
            panel = report["top3_panels"][0]
            self.assertGreater(panel["score"]["final_score"], 0)
            self.assertFalse(panel["score"]["internal_control_in_numerator"])
            self.assertEqual(len(panel["members"]), 2)
            self.assertIn("experimental_validation_statement", report)
            self.assertEqual(set(report["exports"]), {"html"})
            for deferred_format in ("csv", "pdf"):
                with self.assertRaises(InputValidationError):
                    service.store.get_export_path(
                        created["batch_id"],
                        deferred_format,
                    )
            for format_name, header in {
                "html": b"<!doctype html>",
                "csv": b"\xef\xbb\xbf",
                "pdf": b"%PDF",
            }.items():
                content = service.get_export_path(
                    created["batch_id"], format_name
                ).read_bytes()
                self.assertTrue(content.startswith(header))
            self.assertEqual(
                set(service.get_report(created["batch_id"])["exports"]),
                {"html", "csv", "pdf"},
            )
            self._validate_batch(persisted)
            self._validate_report(report)

            api_facade = Mock()
            api_facade.get_wf02_v01_report.side_effect = service.get_report
            api_facade.get_wf02_v01_panel.side_effect = service.get_panel
            api_facade.get_wf02_v01_export_path.side_effect = (
                service.get_export_path
            )
            api_facade.create_wf02_v01_version.side_effect = (
                service.create_version
            )
            with patch.object(api, "service", api_facade):
                with TestClient(api.app) as client:
                    report_response = client.get(
                        f"/v1/wf02/tasks/{created['batch_id']}/report"
                    )
                    self.assertEqual(report_response.status_code, 200)
                    panel_response = client.get(
                        f"/v1/wf02/tasks/{created['batch_id']}/panels/{panel['panel_id']}"
                    )
                    self.assertEqual(panel_response.status_code, 200)
                    self.assertEqual(
                        panel_response.json()["panel"]["panel_id"],
                        panel["panel_id"],
                    )
                    for format_name in ("html", "csv", "pdf"):
                        download = client.get(
                            f"/v1/wf02/tasks/{created['batch_id']}/exports/{format_name}"
                        )
                        self.assertEqual(download.status_code, 200)
                    preview = client.get(
                        f"/v1/wf02/tasks/{created['batch_id']}/exports/html/preview"
                    )
                    self.assertEqual(preview.status_code, 200)
                    self.assertEqual(
                        preview.headers["content-disposition"], "inline"
                    )

    def test_ready_for_scoring_handles_oversized_pdf_cell_and_is_reentrant(
        self,
    ) -> None:
        candidate_a = _candidate(
            1,
            score=95.0,
            amplicon_sequence="ACGT" * 25,
            amplicon_gc_percent=50.0,
        )
        candidate_b = _candidate(
            1,
            score=92.0,
            amplicon_sequence="ACGT" * 30,
            amplicon_gc_percent=55.0,
        )
        candidate_a["specificity"]["raw_output"] = "X" * 200_000
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": [candidate_a]},
                "B": {"candidates": [candidate_b]},
            }
        )
        payload = self._payload("A", "B")
        payload["include_internal_control"] = False
        payload["runtime_config"] = {"instrument": "CFX96"}

        with tempfile.TemporaryDirectory(
            prefix="wf02-stage6-large-pdf-cell-"
        ) as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)

            screened = service.run_until_blocked(created["batch_id"])
            self.assertEqual(screened["status"], "READY_FOR_BALANCE")
            balanced = service.execute_balance_assessment(created["batch_id"])
            self.assertEqual(balanced["status"], "READY_FOR_SCORING")
            self.assertEqual(balanced["stage"], "N27")

            completed = service.run_until_blocked(created["batch_id"])
            self.assertEqual(completed["status"], "COMPLETED")
            self.assertEqual(completed["stage"], "N32")
            pdf_content = service.get_export_path(
                created["batch_id"], "pdf"
            ).read_bytes()
            self.assertTrue(pdf_content.startswith(b"%PDF"))

            persisted = service.get_batch(created["batch_id"])
            trace_before_replay = copy.deepcopy(persisted["workflow_trace"])
            replayed = service.run_until_blocked(created["batch_id"])
            self.assertEqual(replayed["status"], "COMPLETED")
            self.assertEqual(
                service.get_batch(created["batch_id"])["workflow_trace"],
                trace_before_replay,
            )

    def test_completed_exports_publish_to_rustfs_and_use_public_urls(
        self,
    ) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {
                    "candidates": [
                        _candidate(
                            1,
                            score=95.0,
                            amplicon_sequence="ACGT" * 25,
                            amplicon_gc_percent=50.0,
                        )
                    ]
                },
                "B": {
                    "candidates": [
                        _candidate(
                            1,
                            score=92.0,
                            amplicon_sequence="ACGT" * 30,
                            amplicon_gc_percent=55.0,
                        )
                    ]
                },
            }
        )
        publisher = RecordingWF02ArtifactPublisher()
        payload = self._payload("A", "B")
        payload["include_internal_control"] = False
        payload["runtime_config"] = {"instrument": "CFX96"}

        with tempfile.TemporaryDirectory(
            prefix="wf02-rustfs-publication-"
        ) as state_dir:
            service = WF02V01BatchService(
                state_dir,
                load_config(),
                gateway=gateway,
                artifact_publisher=publisher,
            )
            created = service.create_batch(payload)
            service.run_until_blocked(created["batch_id"])
            completed = service.run_until_blocked(created["batch_id"])
            report = service.get_report(created["batch_id"])

            self.assertEqual(completed["status"], "COMPLETED")
            self.assertEqual(len(publisher.uploads), 1)
            self.assertEqual(
                {item["media_type"] for item in publisher.uploads},
                {"text/html; charset=utf-8"},
            )
            self.assertEqual(set(report["exports"]), {"html"})
            for upload in publisher.uploads:
                self.assertEqual(upload["batch_id"], created["batch_id"])
                self.assertTrue(
                    str(upload["object_name"]).startswith(
                        f"wf02-{created['batch_id']}-multiplex-report."
                    )
                )
                self.assertTrue(str(upload["object_key"]).startswith("data/"))
            attachment_urls = [
                item["fileUrl"]
                for item in completed["ui_message"]["data"]["attachments"]
            ]
            self.assertEqual(
                attachment_urls,
                [publisher.uploads[0]["file_url"]],
            )

            self.assertTrue(
                service.get_export_path(
                    created["batch_id"],
                    "csv",
                ).read_bytes().startswith(b"\xef\xbb\xbf")
            )
            self.assertTrue(
                service.get_export_path(
                    created["batch_id"],
                    "pdf",
                ).read_bytes().startswith(b"%PDF")
            )
            self.assertEqual(len(publisher.uploads), 3)
            self.assertEqual(
                {item["media_type"] for item in publisher.uploads},
                {
                    "text/html; charset=utf-8",
                    "text/csv; charset=utf-8",
                    "application/pdf",
                },
            )
            report = service.get_report(created["batch_id"])
            self.assertEqual(set(report["exports"]), {"html", "csv", "pdf"})
            for metadata in report["exports"].values():
                self.assertTrue(
                    str(metadata["file_url"]).startswith(
                        "https://rustfs.ne.scqcyz.cn/wens/data/"
                    )
                )
                self.assertEqual(
                    metadata["object_storage"]["provider"],
                    "rustfs",
                )
            requested_summary = summarize_wf02_batch(
                service.get_batch(created["batch_id"]),
                attachment_formats=("pdf",),
            )
            self.assertEqual(
                [
                    item["fileType"]
                    for item in requested_summary["ui_message"]["data"][
                        "attachments"
                    ]
                ],
                ["PDF"],
            )
            self._validate_report(report)

    def test_completed_legacy_batch_publishes_existing_exports_on_query(
        self,
    ) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {
                    "candidates": [
                        _candidate(
                            1,
                            score=95.0,
                            amplicon_sequence="ACGT" * 25,
                            amplicon_gc_percent=50.0,
                        )
                    ]
                },
                "B": {
                    "candidates": [
                        _candidate(
                            1,
                            score=92.0,
                            amplicon_sequence="ACGT" * 30,
                            amplicon_gc_percent=55.0,
                        )
                    ]
                },
            }
        )
        payload = self._payload("A", "B")
        payload["include_internal_control"] = False
        payload["runtime_config"] = {"instrument": "CFX96"}

        with tempfile.TemporaryDirectory(
            prefix="wf02-rustfs-legacy-publication-"
        ) as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)
            service.run_until_blocked(created["batch_id"])
            service.run_until_blocked(created["batch_id"])

            legacy_batch = service.store.get_batch(created["batch_id"])
            legacy_report = service.store.get_report(created["batch_id"])
            for export_group in (
                legacy_batch["report_exports"],
                legacy_report["exports"],
            ):
                for metadata in export_group.values():
                    metadata.pop("media_type", None)
                    metadata.pop("sha256", None)
                    metadata.pop("file_url", None)
                    metadata.pop("object_storage", None)
            service.store.save_batch(legacy_batch)
            service.store.save_report(legacy_report)

            publisher = RecordingWF02ArtifactPublisher()
            service.artifact_publisher = publisher
            migrated = service.get_batch(created["batch_id"])
            migrated_report = service.get_report(created["batch_id"])

            self.assertEqual(len(publisher.uploads), 1)
            self.assertEqual(set(migrated["report_exports"]), {"html"})
            for metadata in migrated["report_exports"].values():
                self.assertTrue(str(metadata["file_url"]).startswith("https://"))
                self.assertEqual(
                    metadata["object_storage"]["bucket"],
                    "wens",
                )
            self.assertEqual(
                migrated_report["exports"],
                migrated["report_exports"],
            )
            self.assertEqual(
                len(
                    summarize_wf02_batch(migrated)["ui_message"]["data"][
                        "attachments"
                    ]
                ),
                1,
            )

    def test_completed_published_legacy_reading_exports_refresh_on_query(
        self,
    ) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {
                    "candidates": [
                        _candidate(
                            1,
                            score=95.0,
                            amplicon_sequence="ACGT" * 25,
                            amplicon_gc_percent=50.0,
                        )
                    ]
                },
                "B": {
                    "candidates": [
                        _candidate(
                            1,
                            score=92.0,
                            amplicon_sequence="ACGT" * 30,
                            amplicon_gc_percent=55.0,
                        )
                    ]
                },
            }
        )
        publisher = RecordingWF02ArtifactPublisher()
        payload = self._payload("A", "B")
        payload["include_internal_control"] = False
        payload["runtime_config"] = {"instrument": "CFX96"}

        with tempfile.TemporaryDirectory(
            prefix="wf02-renderer-migration-"
        ) as state_dir:
            service = WF02V01BatchService(
                state_dir,
                load_config(),
                gateway=gateway,
                artifact_publisher=publisher,
            )
            created = service.create_batch(payload)
            service.run_until_blocked(created["batch_id"])
            service.run_until_blocked(created["batch_id"])
            service.get_export_path(created["batch_id"], "pdf")

            legacy_batch = service.store.get_batch(created["batch_id"])
            legacy_report = service.store.get_report(created["batch_id"])
            for export_group in (
                legacy_batch["report_exports"],
                legacy_report["exports"],
            ):
                for metadata in export_group.values():
                    metadata.pop("renderer_version", None)
                    (metadata.get("object_storage") or {}).pop(
                        "renderer_version",
                        None,
                    )
            service.store.save_batch(legacy_batch)
            service.store.save_report(legacy_report)

            html_path = service.store.get_export_path(
                created["batch_id"],
                "html",
            )
            pdf_path = service.store.get_export_path(
                created["batch_id"],
                "pdf",
            )
            html_path.write_bytes(
                b"<html><body><pre>{\"raw_tabular_fields\":true}</pre></body></html>"
            )
            pdf_path.write_bytes(b"legacy-pdf-placeholder")
            publisher.uploads.clear()

            migrated = service.get_batch(created["batch_id"])
            migrated_report = service.get_report(created["batch_id"])

            self.assertEqual(len(publisher.uploads), 1)
            self.assertEqual(
                {item["media_type"] for item in publisher.uploads},
                {"text/html; charset=utf-8"},
            )
            refreshed_html = html_path.read_text(encoding="utf-8")
            self.assertIn('class="key-metrics"', refreshed_html)
            self.assertNotIn("<pre", refreshed_html)
            self.assertNotIn("raw_tabular_fields", refreshed_html)
            self.assertEqual(
                pdf_path.read_bytes(),
                b"legacy-pdf-placeholder",
            )
            self.assertEqual(
                migrated["report_exports"]["html"]["renderer_version"],
                WF02_EXPORT_RENDERER_VERSIONS["html"],
            )
            self.assertNotIn(
                "renderer_version",
                migrated["report_exports"]["pdf"],
            )

            refreshed_pdf = service.get_export_path(
                created["batch_id"],
                "pdf",
            )
            self.assertTrue(refreshed_pdf.read_bytes().startswith(b"%PDF"))
            self.assertEqual(len(publisher.uploads), 2)
            migrated = service.get_batch(created["batch_id"])
            migrated_report = service.get_report(created["batch_id"])

            for name, metadata in migrated["report_exports"].items():
                expected_version = WF02_EXPORT_RENDERER_VERSIONS[name]
                self.assertEqual(
                    metadata["renderer_version"],
                    expected_version,
                )
                self.assertEqual(
                    metadata["object_storage"]["renderer_version"],
                    expected_version,
                )
            self.assertEqual(
                migrated_report["exports"],
                migrated["report_exports"],
            )

    def test_rustfs_failure_keeps_scoring_retryable(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {
                    "candidates": [
                        _candidate(
                            1,
                            score=95.0,
                            amplicon_sequence="ACGT" * 25,
                            amplicon_gc_percent=50.0,
                        )
                    ]
                },
                "B": {
                    "candidates": [
                        _candidate(
                            1,
                            score=92.0,
                            amplicon_sequence="ACGT" * 30,
                            amplicon_gc_percent=55.0,
                        )
                    ]
                },
            }
        )
        publisher = RecordingWF02ArtifactPublisher(fail=True)
        payload = self._payload("A", "B")
        payload["include_internal_control"] = False
        payload["runtime_config"] = {"instrument": "CFX96"}

        with tempfile.TemporaryDirectory(
            prefix="wf02-rustfs-retry-"
        ) as state_dir:
            service = WF02V01BatchService(
                state_dir,
                load_config(),
                gateway=gateway,
                artifact_publisher=publisher,
            )
            created = service.create_batch(payload)
            service.run_until_blocked(created["batch_id"])

            with self.assertRaises(PersistenceError):
                service.run_until_blocked(created["batch_id"])

            retryable = service.store.get_batch(created["batch_id"])
            self.assertEqual(retryable["status"], "READY_FOR_SCORING")
            self.assertEqual(retryable["stage"], "N27")

            publisher.fail = False
            completed = service.run_until_blocked(created["batch_id"])
            self.assertEqual(completed["status"], "COMPLETED")
            self.assertEqual(len(publisher.uploads), 1)
            self.assertEqual(
                publisher.uploads[0]["media_type"],
                "text/html; charset=utf-8",
            )

    def test_pdf_layout_error_uses_compact_fallback(self) -> None:
        from reportlab.platypus import SimpleDocTemplate
        from reportlab.platypus.doctemplate import LayoutError

        original_build = SimpleDocTemplate.build
        build_calls = 0

        def fail_first_build(
            document: SimpleDocTemplate,
            flowables: list[Any],
            *args: Any,
            **kwargs: Any,
        ) -> Any:
            nonlocal build_calls
            build_calls += 1
            if build_calls == 1:
                raise LayoutError("simulated oversized flowable")
            return original_build(document, flowables, *args, **kwargs)

        report = {
            "source": {"batch_code": "WF02-TEST", "task_version": 1},
            "result_summary": {"status": "PASS"},
            "outcome": {"workflow_status": "COMPLETED"},
            "top3_panels": [
                {
                    "rank": 1,
                    "panel_id": "panel-001",
                    "tier": "STRICT",
                    "score": {"final_score": 95.0},
                    "members": [
                        {
                            "target_id": "A",
                            "candidate_id": "candidate-01",
                            "forward_primer": {},
                            "reverse_primer": {},
                            "probe": {},
                            "specificity": {"overall_status": "PASS"},
                        }
                    ],
                }
            ],
        }
        with patch.object(SimpleDocTemplate, "build", new=fail_first_build):
            content = render_wf02_pdf(report)

        self.assertEqual(build_calls, 2)
        self.assertTrue(content.startswith(b"%PDF"))

    def test_completed_ui_uses_signed_absolute_attachment_urls(self) -> None:
        batch_id = "45558ea7-a3c0-4bfa-bc46-1a895374a6a6"
        batch = {
            "batch_id": batch_id,
            "batch_code": "WF02-TEST",
            "status": "COMPLETED",
            "stage": "N32",
            "next_action": "COMPLETE",
            "report_id": "11111111-2222-4333-8444-555555555555",
            "task_name": "signed attachment fixture",
            "report_summary": {"status": "PASS", "top3_count": 1},
            "report_exports": {
                format_name: {
                    "format": format_name,
                    "filename": f"wf02-multiplex-report.{format_name}",
                    "size_bytes": 100,
                    "download_url": (
                        f"/v1/wf02/tasks/{batch_id}/exports/{format_name}"
                    ),
                    "preview_url": None,
                }
                for format_name in ("html", "csv", "pdf")
            },
        }
        secret = "test-only-secret-with-at-least-thirty-two-bytes"
        with patch.dict(
            os.environ,
            {
                "MCP_PUBLIC_BASE_URL": "https://oligosmith.example.test",
                "MCP_AUTH_REQUIRED": "true",
                "MCP_JWT_SECRET": secret,
            },
            clear=False,
        ):
            summary = summarize_wf02_batch(batch)
            requested_pdf_summary = summarize_wf02_batch(
                batch,
                attachment_formats=("pdf",),
            )

        attachments = summary["ui_message"]["data"]["attachments"]
        self.assertEqual(
            [item["fileType"] for item in attachments],
            ["HTML"],
        )
        requested_pdf = requested_pdf_summary["ui_message"]["data"][
            "attachments"
        ]
        self.assertEqual(
            [item["fileType"] for item in requested_pdf],
            ["PDF"],
        )
        for attachment in [*attachments, *requested_pdf]:
            file_url = str(attachment["fileUrl"])
            self.assertTrue(
                file_url.startswith(
                    f"https://oligosmith.example.test/v1/wf02/tasks/{batch_id}/exports/"
                )
            )
            self.assertIn("expires=", file_url)
            self.assertIn("signature=", file_url)
            self.assertNotIn(secret, file_url)

    def test_n27_reuses_wf01_versions_twice_then_marks_tm_warning(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {
                    "candidates": [
                        _candidate(
                            1,
                            forward_tm=55.0,
                            reverse_tm=55.2,
                            probe_tm=65.0,
                            amplicon_sequence="ACGT" * 20,
                        )
                    ]
                },
                "B": {
                    "candidates": [
                        _candidate(
                            1,
                            forward_tm=63.0,
                            reverse_tm=63.2,
                            probe_tm=73.0,
                            amplicon_sequence="ACGT" * 30,
                        )
                    ]
                },
            }
        )
        payload = self._payload("A", "B")
        payload["include_internal_control"] = False
        payload["runtime_config"] = {"instrument": "CFX96"}
        with tempfile.TemporaryDirectory(prefix="wf02-stage6-redesign-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)
            service.run_until_blocked(created["batch_id"])

            completed = service.run_until_blocked(created["batch_id"])
            persisted = service.get_batch(created["batch_id"])
            report = service.get_report(created["batch_id"])
            balance = persisted["multiplex"]["balance"]

            self.assertEqual(completed["status"], "COMPLETED")
            self.assertEqual(balance["redesign_attempt_count"], 2)
            self.assertTrue(balance["tm_warning_fallback"])
            self.assertEqual(len(gateway.version_calls), 4)
            self.assertTrue(
                all(
                    call["constraints"]["primer_max_tm"]
                    - call["constraints"]["primer_min_tm"]
                    == 2.0
                    for call in gateway.version_calls
                )
            )
            self.assertTrue(
                any(
                    "Tm_WARNING" in warning
                    for warning in report["top3_panels"][0]["warnings"]
                )
            )
            self._validate_batch(persisted)
            self._validate_report(report)

    def test_n32_creates_immutable_version_with_separate_runtime_diff(self) -> None:
        gateway = FakeWF01Gateway(
            {
                "A": {"candidates": [_candidate(1, amplicon_sequence="ACGT" * 20)]},
                "B": {"candidates": [_candidate(1, amplicon_sequence="ACGT" * 30)]},
            }
        )
        payload = self._payload("A", "B")
        payload["include_internal_control"] = False
        payload["runtime_config"] = {"instrument": "CFX96"}
        with tempfile.TemporaryDirectory(prefix="wf02-stage6-version-") as state_dir:
            service = self._service(state_dir, gateway)
            created = service.create_batch(payload)
            service.run_until_blocked(created["batch_id"])
            service.run_until_blocked(created["batch_id"])
            source_before = service.get_batch(created["batch_id"])
            report_before = service.get_report(created["batch_id"])

            new_version = service.create_version(
                created["batch_id"],
                {
                    "reason": "更换仪器验证",
                    "runtime_config_changes": {"instrument": "QuantStudio 5"},
                },
            )

            source_after = service.get_batch(created["batch_id"])
            report_after = service.get_report(created["batch_id"])
            self.assertEqual(source_before, source_after)
            self.assertEqual(report_before, report_after)
            self.assertEqual(new_version["task_version"], 2)
            self.assertEqual(new_version["source_batch_id"], created["batch_id"])
            self.assertEqual(new_version["status"], "READY_FOR_TARGET_DESIGN")
            self.assertEqual(
                new_version["version_diff"]["user_input_changes"], []
            )
            self.assertGreaterEqual(
                len(new_version["version_diff"]["runtime_config_changes"]), 1
            )
            self._validate_batch(new_version)


if __name__ == "__main__":
    unittest.main()

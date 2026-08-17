from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.service import AgentDService
from agentd.identifiers import wf01_batch_code
from agentd.wf01_v02 import WF01V02BatchStore


M1_DATA = ROOT / "testdata" / "wf01_prd_v0_2"


def valid_payload() -> dict:
    return {
        "task_name": "m1_fixture_batch",
        "fasta_text": (M1_DATA / "multi_target_valid.fasta").read_text(encoding="utf-8"),
        "batch_defaults": {
            "target_organism": {"name": "Synthetic test virus"},
            "hosts": [{"name": "pig"}],
        },
    }


class WF01V02PreflightTests(unittest.TestCase):
    def test_readable_batch_code_uses_beijing_creation_time(self) -> None:
        self.assertEqual(
            wf01_batch_code(
                "8ec624e9-3846-4883-ac7f-b9b28d01fa07",
                "2026-08-08T05:06:50.705243+00:00",
            ),
            "WF01-260808-1306-8EC6",
        )

    def test_historical_batch_gets_a_non_persisted_readable_code(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-old-batch-") as state_dir:
            batch_id = "8ec624e9-3846-4883-ac7f-b9b28d01fa07"
            root = Path(state_dir) / "wf01-v02-batches"
            root.mkdir(parents=True)
            (root / f"{batch_id}.json").write_text(
                json.dumps(
                    {
                        "batch_id": batch_id,
                        "workflow": "WF-01",
                        "created_at": "2026-08-08T05:06:50.705243+00:00",
                    }
                ),
                encoding="utf-8",
            )

            fetched = WF01V02BatchStore(state_dir).get(batch_id)

            self.assertEqual(fetched["batch_code"], "WF01-260808-1306-8EC6")

    def setUp(self) -> None:
        self.state_dir = tempfile.TemporaryDirectory(prefix="agentd-m1-")
        self.service = AgentDService(state_dir=self.state_dir.name)

    def tearDown(self) -> None:
        self.state_dir.cleanup()

    def test_ac_in_01_multifasta_creates_one_alignment_target_in_order(self) -> None:
        result = self.service.preflight_wf01_v02(valid_payload())
        self.assertTrue(result["can_auto_run"])
        self.assertEqual(result["preflight_status"], "READY_TO_QUEUE")
        self.assertEqual(len(result["targets"]), 1)
        target = result["targets"][0]
        self.assertEqual(
            [template["record_name"] for template in target["templates"]],
            ["target_001", "target_002", "target_003"],
        )
        self.assertEqual(target["target_id"], "target_001")
        self.assertEqual(target["template_count"], 3)
        self.assertTrue(target["alignment_required"])
        self.assertEqual(result["fixed_top_candidates"], 10)
        self.assertEqual(set(result["parameter_profiles"]), {"default", "L1", "L2"})

    def test_ac_in_02_rejects_per_record_override_for_alignment_target(self) -> None:
        payload = valid_payload()
        payload["target_overrides"] = {
            "target_002": {
                "hosts": [{"name": "chicken"}],
                "design_parameters": {"product_min_bp": 90, "product_max_bp": 140},
            }
        }
        result = self.service.preflight_wf01_v02(payload)
        self.assertFalse(result["can_auto_run"])
        issue = next(
            item
            for item in result["issues"]
            if item["code"] == "ALIGNMENT_TARGET_OVERRIDE_NOT_SUPPORTED"
        )
        self.assertEqual(issue["severity"], "BLOCKER")

    def test_ac_in_02_multiple_sequence_files_preserve_file_sources(self) -> None:
        payload = {
            "sequence_files": [
                {"file_name": "alpha.fasta", "fasta_text": ">alpha\n" + "ACGT" * 30},
                {"file_name": "beta.fasta", "fasta_text": ">beta\n" + "TGCA" * 30},
            ],
            "batch_defaults": {"target_organism": {"name": "Synthetic test virus"}, "hosts": [{"name": "pig"}]},
        }
        result = self.service.preflight_wf01_v02(payload)
        self.assertTrue(result["can_auto_run"])
        target = result["targets"][0]
        self.assertEqual(
            [template["record_name"] for template in target["templates"]],
            ["alpha", "beta"],
        )
        self.assertEqual(
            [template["source_file"] for template in target["templates"]],
            ["alpha.fasta", "beta.fasta"],
        )

    def test_ac_in_03_short_template_is_blocking(self) -> None:
        payload = valid_payload()
        payload["fasta_text"] = (M1_DATA / "short_79.fasta").read_text(encoding="utf-8")
        result = self.service.preflight_wf01_v02(payload)
        self.assertFalse(result["can_auto_run"])
        issue = next(item for item in result["issues"] if item["code"] == "TEMPLATE_TOO_SHORT")
        self.assertEqual(issue["severity"], "BLOCKER")
        self.assertEqual(issue["evidence"]["minimum_length_nt"], 80)

    def test_ac_in_03_exactly_80_nt_template_passes_length_preflight(self) -> None:
        payload = valid_payload()
        payload["fasta_text"] = ">exactly_80\n" + "ACGT" * 20
        result = self.service.preflight_wf01_v02(payload)

        self.assertTrue(result["can_auto_run"])
        self.assertFalse(
            any(issue["code"] == "TEMPLATE_TOO_SHORT" for issue in result["issues"])
        )

    def test_ac_in_04_duplicate_ids_are_reported_at_batch_scope(self) -> None:
        payload = valid_payload()
        payload["fasta_text"] = (M1_DATA / "duplicate_ids.fasta").read_text(encoding="utf-8")
        result = self.service.preflight_wf01_v02(payload)
        issue = next(item for item in result["issues"] if item["code"] == "DUPLICATE_RECORD_ID")
        self.assertEqual(issue["scope"], "BATCH")
        self.assertFalse(result["can_auto_run"])
        self.assertTrue(result["target_manifest"]["batch_requires_user_action"])

    def test_illegal_template_bases_are_a_target_blocker(self) -> None:
        payload = valid_payload()
        payload["fasta_text"] = ">bad\n" + "ACGT" * 20 + "Z"
        result = self.service.preflight_wf01_v02(payload)
        issue = next(item for item in result["issues"] if item["code"] == "ILLEGAL_BASES")
        self.assertEqual(issue["scope"], "TARGET")
        self.assertEqual(issue["severity"], "BLOCKER")

    def test_ac_in_05_external_identifiers_do_not_replace_template(self) -> None:
        result = self.service.preflight_wf01_v02(
            {"task_name": "missing_template", "batch_defaults": {"target_organism": {"accession": "NC_000000.0"}, "hosts": [{"name": "pig"}]}}
        )
        self.assertFalse(result["can_auto_run"])
        self.assertTrue(any(item["code"] == "MISSING_TEMPLATE" for item in result["issues"]))

    def test_sequence_only_is_queued_and_specificity_context_is_deferred(self) -> None:
        result = self.service.preflight_wf01_v02(
            {"fasta_text": ">sequence_only\n" + "ACGT" * 30}
        )

        self.assertTrue(result["can_auto_run"])
        self.assertTrue(result["primer3_execution_eligible"])
        self.assertEqual(
            result["specificity_plan"]["mode"],
            "NOT_EVALUATED_MISSING_TARGET_CONTEXT",
        )
        target = result["targets"][0]
        self.assertEqual(target["status"], "QUEUED")
        self.assertEqual(target["manifest_status"], "READY_DESIGN_ONLY")
        self.assertEqual(target["execution_path"], "DESIGN_ONLY")
        self.assertFalse(target["specificity_input_complete"])
        self.assertEqual(
            target["specificity_missing_fields"],
            ["target_organism"],
        )
        issues = {item["code"]: item for item in result["issues"]}
        self.assertEqual(issues["MISSING_TARGET_ORGANISM"]["severity"], "WARNING")
        self.assertNotIn("MISSING_HOST", issues)

    def test_plain_unlabelled_sequence_gets_generated_id_without_pausing(self) -> None:
        result = self.service.preflight_wf01_v02(
            {"sequence": "ACGT" * 30}
        )

        self.assertTrue(result["can_auto_run"])
        self.assertEqual(result["targets"][0]["manifest_status"], "READY_DESIGN_ONLY")
        self.assertEqual(result["targets"][0]["template"]["record_name"], "sequence_1")

    def test_sequence_with_target_and_host_selects_full_specificity_plan(self) -> None:
        result = self.service.preflight_wf01_v02(valid_payload())

        self.assertTrue(result["can_auto_run"])
        self.assertEqual(
            result["specificity_plan"]["mode"],
            "FULL_SPECIFICITY_EVALUATION",
        )
        self.assertTrue(result["targets"][0]["specificity_input_complete"])
        self.assertEqual(result["targets"][0]["manifest_status"], "READY_FULL")
        self.assertEqual(result["targets"][0]["execution_path"], "FULL_SPECIFICITY")

    def test_sequence_with_target_but_no_host_selects_full_specificity_plan(self) -> None:
        payload = valid_payload()
        payload["batch_defaults"].pop("hosts")

        result = self.service.preflight_wf01_v02(payload)

        self.assertTrue(result["can_auto_run"])
        self.assertEqual(
            result["specificity_plan"]["mode"],
            "FULL_SPECIFICITY_EVALUATION",
        )
        self.assertEqual(
            result["specificity_plan"]["required_modes"],
            ["TARGET_HIT", "RELATED_BACKGROUND_NT"],
        )
        self.assertEqual(
            result["specificity_plan"]["optional_modes"],
            ["HOST_EXCLUSION"],
        )
        target = result["targets"][0]
        self.assertTrue(target["specificity_input_complete"])
        self.assertFalse(target["host_exclusion_requested"])
        self.assertEqual(target["specificity_missing_fields"], [])
        self.assertEqual(target["manifest_status"], "READY_FULL")
        self.assertEqual(target["execution_path"], "FULL_SPECIFICITY")

    def test_independent_manifest_assigns_per_target_four_state_statuses(self) -> None:
        sequence = "ACGT" * 30
        result = self.service.preflight_wf01_v02(
            {
                "target_manifest": {
                    "targets": [
                        {
                            "target_id": "design_only",
                            "template": {"record_name": "design_only", "sequence": sequence},
                        },
                        {
                            "target_id": "full",
                            "template": {"record_name": "full", "sequence": sequence},
                            "target_organism": {"name": "Synthetic test virus"},
                            "hosts": [{"name": "pig"}],
                        },
                        {
                            "target_id": "confirmation",
                            "template": {"record_name": "confirmation", "sequence": sequence},
                            "target_organism": {"name": "Synthetic test virus"},
                            "hosts": [{"name": "sheep"}],
                        },
                        {
                            "target_id": "blocked",
                            "template": {"record_name": "blocked", "sequence": "ACGT" * 19},
                        },
                    ]
                }
            }
        )

        self.assertFalse(result["can_auto_run"])
        statuses = {
            target["target_id"]: target["manifest_status"]
            for target in result["targets"]
        }
        self.assertEqual(
            statuses,
            {
                "design_only": "READY_DESIGN_ONLY",
                "full": "READY_FULL",
                "confirmation": "NEEDS_CONFIRMATION",
                "blocked": "BLOCKED",
            },
        )
        self.assertEqual(
            result["target_manifest"]["problem_target_ids"],
            ["confirmation", "blocked"],
        )

    def test_manifest_revision_can_fix_and_remove_problem_targets_then_confirm(self) -> None:
        sequence = "ACGT" * 30
        batch = self.service.create_wf01_v02_batch(
            {
                "sequence_mode": "independent_targets",
                "template_records": [
                    {"record_name": "keep", "sequence": sequence},
                    {"record_name": "remove", "sequence": "ACGT" * 19},
                ],
                "target_overrides": {
                    "keep": {
                        "target_organism": {"name": "Synthetic test virus"},
                        "hosts": [{"name": "sheep"}],
                    }
                },
            }
        )
        self.assertEqual(batch["status"], "PENDING_CONFIRMATION")

        revised = self.service.revise_wf01_v02_target_manifest(
            batch["batch_id"],
            {
                "target_changes": [
                    {
                        "target_id": "target_001",
                        "changes": {"hosts": [{"name": "pig"}]},
                    }
                ],
                "remove_target_ids": ["target_002"],
            },
        )

        self.assertEqual(revised["status"], "QUEUED")
        self.assertEqual(revised["target_total"], 1)
        self.assertEqual(revised["targets"][0]["manifest_status"], "READY_FULL")
        self.assertFalse(revised["agent_interactions"])
        confirmed = self.service.confirm_wf01_v02_target_manifest(batch["batch_id"])
        self.assertTrue(confirmed["started"])
        self.assertEqual(confirmed["next_action"], "RUN_M2")

    def test_manifest_confirm_refuses_to_start_while_problem_targets_remain(self) -> None:
        batch = self.service.create_wf01_v02_batch(
            {"fasta_text": ">short\n" + "ACGT" * 19}
        )

        confirmed = self.service.confirm_wf01_v02_target_manifest(batch["batch_id"])

        self.assertFalse(confirmed["started"])
        self.assertEqual(confirmed["next_action"], "RESOLVE_TARGET_MANIFEST")
        self.assertEqual(confirmed["problem_target_ids"], ["target_001"])

    def test_manifest_confirm_refuses_batch_scope_duplicate_record_names(self) -> None:
        batch = self.service.create_wf01_v02_batch(
            {
                "sequence_mode": "independent_targets",
                "template_records": [
                    {"record_name": "duplicate", "sequence": "ACGT" * 30},
                    {"record_name": "duplicate", "sequence": "TGCA" * 30},
                ],
            }
        )

        confirmed = self.service.confirm_wf01_v02_target_manifest(batch["batch_id"])

        self.assertFalse(confirmed["started"])
        self.assertEqual(confirmed["problem_target_ids"], [])
        self.assertEqual(confirmed["batch_issues"][0]["code"], "DUPLICATE_RECORD_ID")

    def test_parameters_and_out_of_scope_requests_are_blocking(self) -> None:
        payload = valid_payload()
        payload["top_candidates"] = 3
        payload["use_mgb"] = True
        payload["batch_defaults"]["design_parameters"] = {"probe_min_size": 18}
        result = self.service.preflight_wf01_v02(payload)
        codes = {item["code"] for item in result["issues"]}
        self.assertIn("TOP_CANDIDATES_IS_SYSTEM_FIXED", codes)
        self.assertIn("OUT_OF_SCOPE_CAPABILITY", codes)
        self.assertIn("PARAMETER_OUT_OF_HARD_RANGE", codes)

    def test_unsupported_host_and_iupac_are_confirmation_items(self) -> None:
        payload = valid_payload()
        payload["fasta_text"] = ">ambiguous\n" + "ACGT" * 20 + "N"
        payload["batch_defaults"]["hosts"] = [{"name": "sheep"}]
        result = self.service.preflight_wf01_v02(payload)
        issues = {item["code"]: item for item in result["issues"]}
        self.assertEqual(issues["AMBIGUOUS_BASES_REQUIRE_CONFIRMATION"]["severity"], "CONFIRMATION")
        self.assertEqual(issues["UNSUPPORTED_HOST_DATABASE"]["severity"], "CONFIRMATION")

    def test_direct_mode_continues_when_host_database_is_not_configured(self) -> None:
        payload = valid_payload()
        payload["batch_defaults"]["hosts"] = [{"name": "sheep"}]

        batch = self.service.create_wf01_v02_batch(
            payload,
            interaction_mode="NO_POPUPS",
        )

        self.assertEqual(batch["status"], "QUEUED")
        self.assertFalse(batch["agent_interactions"])
        target = batch["targets"][0]
        self.assertEqual(target["execution_path"], "FULL_SPECIFICITY")
        issue = next(
            item
            for item in target["issues"]
            if item["code"] == "UNSUPPORTED_HOST_DATABASE"
        )
        self.assertEqual(issue["severity"], "WARNING")
        self.assertIn("未评估", issue["message"])

    def test_create_and_get_batch_persists_without_running_primer3(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-m1-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            created = service.create_wf01_v02_batch(valid_payload())
            self.assertEqual(created["status"], "QUEUED")
            self.assertRegex(
                created["batch_code"],
                r"^WF01-\d{6}-\d{4}-[A-F0-9]{4}$",
            )
            self.assertEqual(created["target_total"], 1)
            self.assertEqual(created["targets"][0]["template_count"], 3)
            self.assertEqual(created["events"][0]["event_type"], "BATCH_CREATED")
            self.assertTrue((Path(state_dir) / "wf01-v02-batches" / f"{created['batch_id']}.json").exists())
            fetched = service.get_wf01_v02_batch(created["batch_id"])
            self.assertEqual(fetched["batch_id"], created["batch_id"])
            self.assertEqual(fetched["batch_code"], created["batch_code"])
            self.assertFalse(fetched["search_complete"])

    def test_labelled_natural_language_populates_missing_defaults_without_guessing(self) -> None:
        payload = {
            "fasta_text": (M1_DATA / "multi_target_valid.fasta").read_text(encoding="utf-8"),
            "natural_language": "靶标物种：Synthetic test virus；宿主：猪",
        }
        result = self.service.preflight_wf01_v02(payload)
        self.assertTrue(result["can_auto_run"])
        self.assertEqual(result["intent_extraction"]["method"], "M1_LABELLED_TEXT_ONLY")
        self.assertEqual(result["intent_extraction"]["extracted_fields"]["target_organism"], {"name": "Synthetic test virus"})
        self.assertEqual(result["targets"][0]["host_objects"][0]["normalized_name"], "pig")


@unittest.skipUnless(importlib.util.find_spec("fastapi") is not None, "FastAPI extra not installed")
class WF01V02ApiTests(unittest.TestCase):
    def test_preflight_create_and_get_endpoints_use_v02_contract(self) -> None:
        from fastapi.testclient import TestClient
        from agentd import api

        with tempfile.TemporaryDirectory(prefix="agentd-m1-api-") as state_dir:
            with patch.object(api, "service", AgentDService(state_dir=state_dir)):
                client = TestClient(api.app)
                response = client.post("/v1/wf01/tasks/preflight", json=valid_payload())
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["can_auto_run"])
                created = client.post("/v1/wf01/tasks", json=valid_payload())
                self.assertEqual(created.status_code, 200)
                self.assertEqual(created.json()["status"], "QUEUED")
                fetched = client.get(f"/v1/wf01/tasks/{created.json()['batch_id']}")
                self.assertEqual(fetched.status_code, 200)
                self.assertEqual(fetched.json()["batch_id"], created.json()["batch_id"])

                revised = client.patch(
                    f"/v1/wf01/tasks/{created.json()['batch_id']}/targets",
                    json={
                        "target_changes": [
                            {
                                "target_id": "target_001",
                                "changes": {"hosts": [{"name": "pig"}]},
                            }
                        ]
                    },
                )
                self.assertEqual(revised.status_code, 200, revised.text)
                confirmed = client.post(
                    f"/v1/wf01/tasks/{created.json()['batch_id']}/confirm"
                )
                self.assertEqual(confirmed.status_code, 200, confirmed.text)
                self.assertTrue(confirmed.json()["started"])

                with patch.object(
                    api.service,
                    "run_wf01_v02_until_blocked",
                    return_value={"batch_id": created.json()["batch_id"], "next_action": "COMPLETE"},
                ):
                    advanced = client.post(
                        f"/v1/wf01/tasks/{created.json()['batch_id']}/run-until-blocked",
                        json={},
                    )
                self.assertEqual(advanced.status_code, 200, advanced.text)
                self.assertEqual(advanced.json()["next_action"], "COMPLETE")


if __name__ == "__main__":
    unittest.main()

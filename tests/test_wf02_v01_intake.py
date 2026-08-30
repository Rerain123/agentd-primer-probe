from __future__ import annotations

import ast
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd import api
from agentd.errors import InputValidationError, PersistenceError
from agentd.service import AgentDService


class WF02V01IntakeTests(unittest.TestCase):
    def _service(self, state_dir: str) -> AgentDService:
        return AgentDService(state_dir=state_dir)

    @staticmethod
    def _valid_payload() -> dict:
        return {
            "task_name": "猪源双靶标普通多重",
            "targets": [
                {"target_id": "csfv", "display_name": "猪瘟病毒"},
                "非洲猪瘟病毒",
            ],
            "host_species": "猪",
        }

    def test_n01_to_n05_creates_isolated_immutable_input_snapshot(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf02-v01-valid-") as state_dir:
            service = self._service(state_dir)
            payload = self._valid_payload()
            original = copy.deepcopy(payload)

            batch = service.create_wf02_v01_batch(payload)

            self.assertEqual(payload, original)
            self.assertEqual(batch["workflow"], "WF-02")
            self.assertEqual(batch["workflow_version"], "wf02-v0.1")
            self.assertEqual(batch["task_version"], 1)
            self.assertEqual(batch["status"], "READY_FOR_TARGET_DESIGN")
            self.assertEqual(batch["stage"], "N05")
            self.assertEqual(batch["next_action"], "RUN_TARGET_DESIGN")
            self.assertEqual(
                [item["node"] for item in batch["workflow_trace"]],
                ["N01", "N02", "N03", "N05"],
            )
            snapshot = batch["user_input_snapshot"]
            self.assertEqual(snapshot["host_species"], "猪")
            self.assertTrue(snapshot["include_internal_control"])
            self.assertEqual(len(snapshot["targets"]), 2)
            self.assertEqual(
                batch["preflight"]["input_echo"][
                    "include_internal_control_source"
                ],
                "DEFAULT",
            )
            runtime = batch["runtime_config_snapshot"]
            self.assertEqual(runtime["instrument"], "CFX96")
            self.assertFalse(runtime["rox_as_reference"])
            self.assertEqual(runtime["rox_source"], "INSTRUMENT_DEFAULT")
            self.assertFalse(runtime["is_core_user_input"])
            self.assertRegex(batch["input_snapshot_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(batch["rule_snapshot"]["config_sha256"], r"^[0-9a-f]{64}$")

            batch_path = (
                Path(state_dir)
                / "wf02-v01-batches"
                / f"{batch['batch_id']}.json"
            )
            self.assertTrue(batch_path.is_file())
            self.assertFalse((Path(state_dir) / "wf01-v02-batches").exists())

            payload["targets"][0]["display_name"] = "外部对象被修改"
            batch["user_input_snapshot"]["targets"][0]["display_name"] = (
                "返回对象被修改"
            )
            stored = service.get_wf02_v01_batch(batch["batch_id"])
            self.assertEqual(
                stored["user_input_snapshot"]["targets"][0]["display_name"],
                "猪瘟病毒",
            )

            schema = json.loads(
                (ROOT / "schemas" / "wf02-v01-batch.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            Draft202012Validator(schema).validate(stored)

    def test_n03_rejects_empty_count_duplicate_and_ambiguous_targets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf02-v01-invalid-") as state_dir:
            service = self._service(state_dir)
            cases = (
                (
                    {"targets": [], "host_species": "猪"},
                    "WF02_TARGETS_REQUIRED",
                ),
                (
                    {"targets": ["猪瘟病毒"], "host_species": "猪"},
                    "WF02_TARGET_COUNT_OUT_OF_RANGE",
                ),
                (
                    {
                        "targets": [f"靶标-{index}" for index in range(1, 7)],
                        "host_species": "猪",
                    },
                    "WF02_TARGET_COUNT_OUT_OF_RANGE",
                ),
                (
                    {
                        "targets": ["猪瘟 病毒", "猪瘟病毒"],
                        "host_species": "猪",
                    },
                    "WF02_TARGET_IDENTITY_CONFLICT",
                ),
                (
                    {
                        "targets": [
                            {
                                "display_name": "PCV",
                                "ambiguous": True,
                                "identity_candidates": ["PCV2", "PCV3"],
                            },
                            "猪瘟病毒",
                        ],
                        "host_species": "猪",
                    },
                    "WF02_TARGET_IDENTITY_AMBIGUOUS",
                ),
            )
            for payload, expected_code in cases:
                with self.subTest(expected_code=expected_code):
                    result = service.preflight_wf02_v01(payload)
                    self.assertFalse(result["can_create_task"])
                    self.assertEqual(result["stage"], "N04")
                    self.assertIn(
                        expected_code,
                        {issue["code"] for issue in result["issues"]},
                    )

    def test_n03_accepts_five_target_multiplicity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf02-v01-five-targets-") as state_dir:
            service = self._service(state_dir)
            result = service.preflight_wf02_v01(
                {
                    "targets": [f"靶标-{index}" for index in range(1, 6)],
                    "host_species": "猪",
                    "include_internal_control": False,
                }
            )

        self.assertTrue(result["can_create_task"])
        self.assertEqual(result["input_echo"]["target_count"], 5)
        self.assertEqual(result["rule_snapshot"]["maximum_targets"], 5)

    def test_upload_intake_manifest_blocks_missing_or_failed_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf02-v01-upload-manifest-") as state_dir:
            service = self._service(state_dir)
            result = service.preflight_wf02_v01(
                {
                    "targets": ["A", "B", "C"],
                    "host_species": "猪",
                    "include_internal_control": False,
                    "upload_intake_manifest": {
                        "expected_target_count": 4,
                        "failed_file_ids": [104],
                    },
                }
            )

        self.assertFalse(result["can_create_task"])
        self.assertEqual(result["stage"], "N04")
        codes = {item["code"] for item in result["issues"]}
        self.assertIn("WF02_UPLOAD_TARGET_COUNT_MISMATCH", codes)
        self.assertIn("WF02_UPLOAD_DOWNLOADS_INCOMPLETE", codes)
        self.assertEqual(
            result["input_echo"]["upload_intake_manifest"],
            {
                "policy_version": "wf02-upload-intake-v1",
                "expected_target_count": 4,
                "received_target_count": 3,
                "failed_file_ids": [104],
                "complete": False,
            },
        )

    def test_complete_upload_intake_manifest_is_frozen_in_batch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf02-v01-upload-complete-") as state_dir:
            service = self._service(state_dir)
            batch = service.create_wf02_v01_batch(
                {
                    "targets": ["A", "B"],
                    "host_species": "猪",
                    "include_internal_control": False,
                    "upload_intake_manifest": {
                        "expected_target_count": 2,
                        "failed_file_ids": [],
                    },
                }
            )

            persisted = service.get_wf02_v01_batch(batch["batch_id"])
            manifest = persisted["user_input_snapshot"]["upload_intake_manifest"]
            self.assertTrue(manifest["complete"])
            self.assertEqual(manifest["received_target_count"], 2)
            self.assertEqual(
                persisted["preflight"]["input_echo"]["upload_intake_manifest"],
                manifest,
            )
            schema = json.loads(
                (ROOT / "schemas" / "wf02-v01-batch.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            Draft202012Validator(schema).validate(persisted)

    def test_target_files_map_one_to_one_and_multifasta_stays_one_target(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf02-v01-file-map-") as state_dir:
            service = self._service(state_dir)
            first = service.input_artifacts.store_inline_fasta(
                ">A_strain_1\n" + "ACGT" * 40 + "\n"
                ">A_strain_2\n" + "ACGT" * 39 + "ACGA\n",
                file_name="target_A.fasta",
            )
            second = service.input_artifacts.store_inline_fasta(
                ">B_strain_1\n" + "TGCA" * 40 + "\n",
                file_name="target_B.fasta",
            )
            batch = service.create_wf02_v01_batch(
                {
                    "targets": [
                        {
                            "target_id": "A",
                            "display_name": "靶标 A",
                            "input_artifact_id": first["input_artifact_id"],
                        },
                        {
                            "target_id": "B",
                            "display_name": "靶标 B",
                            "input_artifact_id": second["input_artifact_id"],
                        },
                    ],
                    "host_species": "猪",
                    "include_internal_control": False,
                }
            )

            targets = batch["user_input_snapshot"]["targets"]
            self.assertEqual(
                [item["template_source"]["file_name"] for item in targets],
                ["target_A.fasta", "target_B.fasta"],
            )
            self.assertTrue(
                all(
                    item["template_source"]["source_kind"]
                    == "UPLOADED_FASTA_FILE"
                    and item["template_source"]["file_count"] == 1
                    and item["template_source"]["sequence_mode"]
                    == "alignment_target"
                    and item["template_source"][
                        "multiple_homologous_sequences_allowed"
                    ]
                    for item in targets
                )
            )
            self.assertEqual(
                batch["rule_snapshot"]["target_file_policy_version"],
                "wf02-one-file-per-target-v1",
            )
            self.assertTrue(batch["rule_snapshot"]["one_file_per_target"])
            self.assertFalse(
                batch["rule_snapshot"]["multi_target_file_auto_split"]
            )

            child_payload = service.wf02_v01._wf01_child_payload(
                batch, targets[0]
            )
            child_preflight = service.preflight_wf01_v02(child_payload)
            self.assertEqual(
                child_preflight["normalised_input"]["sequence_mode"],
                "alignment_target",
            )
            self.assertEqual(child_preflight["target_manifest"]["target_total"], 1)
            self.assertEqual(child_preflight["targets"][0]["template_count"], 2)
            self.assertTrue(child_preflight["targets"][0]["alignment_required"])

            schema = json.loads(
                (ROOT / "schemas" / "wf02-v01-batch.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            Draft202012Validator(schema).validate(batch)

    def test_target_file_policy_rejects_ambiguous_or_reused_file_mapping(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf02-v01-file-reject-") as state_dir:
            service = self._service(state_dir)
            artifact = service.input_artifacts.store_inline_fasta(
                ">shared\n" + "ACGT" * 40 + "\n",
                file_name="shared.fasta",
            )
            cases = (
                (
                    {
                        "targets": [
                            {
                                "target_id": "A",
                                "input_artifact_id": artifact["input_artifact_id"],
                            },
                            {
                                "target_id": "B",
                                "input_artifact_id": artifact["input_artifact_id"],
                            },
                        ],
                        "host_species": "猪",
                    },
                    "WF02_FILE_ASSIGNED_TO_MULTIPLE_TARGETS",
                ),
                (
                    {
                        "targets": [
                            {
                                "target_id": "A",
                                "sequence_files": [
                                    {"fasta_text": ">A1\n" + "ACGT" * 30},
                                    {"fasta_text": ">A2\n" + "ACGT" * 30},
                                ],
                            },
                            {"target_id": "B"},
                        ],
                        "host_species": "猪",
                    },
                    "WF02_MULTIPLE_FILES_FOR_ONE_TARGET",
                ),
                (
                    {
                        "targets": [
                            {
                                "target_id": "A",
                                "fasta_text": ">A1\n" + "ACGT" * 30,
                                "sequence_mode": "independent_targets",
                            },
                            {"target_id": "B"},
                        ],
                        "host_species": "猪",
                    },
                    "WF02_TARGET_FILE_MODE_INVALID",
                ),
                (
                    {
                        "targets": [
                            {
                                "target_id": "A",
                                "sequence": "ACGT" * 30,
                                "fasta_text": ">A1\n" + "ACGT" * 30,
                            },
                            {"target_id": "B"},
                        ],
                        "host_species": "猪",
                    },
                    "WF02_TARGET_TEMPLATE_SOURCE_CONFLICT",
                ),
            )
            for payload, expected_code in cases:
                with self.subTest(expected_code=expected_code):
                    result = service.preflight_wf02_v01(payload)
                    self.assertFalse(result["can_create_task"])
                    self.assertIn(
                        expected_code,
                        {issue["code"] for issue in result["issues"]},
                    )

    def test_n03_detects_conflicting_aliases_without_losing_values(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf02-v01-conflict-") as state_dir:
            result = self._service(state_dir).preflight_wf02_v01(
                {
                    "targets": ["猪瘟病毒", "非洲猪瘟病毒"],
                    "targetList": ["猪瘟病毒", "圆环病毒2型"],
                    "host_species": "猪",
                    "host": "鸭",
                    "include_internal_control": True,
                    "need_internal_control": False,
                }
            )
        issue_codes = {issue["code"] for issue in result["issues"]}
        self.assertEqual(
            issue_codes,
            {
                "WF02_TARGETS_ALIAS_CONFLICT",
                "WF02_HOST_SPECIES_CONFLICT",
                "WF02_INTERNAL_CONTROL_CONFLICT",
            },
        )
        self.assertEqual(
            result["input_echo"]["targets"],
            ["猪瘟病毒", "非洲猪瘟病毒"],
        )
        self.assertEqual(result["input_echo"]["host_species"], "猪")

    def test_n04_canonical_edits_remove_conflicting_aliases(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf02-v01-alias-edit-") as state_dir:
            service = self._service(state_dir)
            draft = service.create_wf02_v01_batch(
                {
                    "targets": ["猪瘟病毒", "非洲猪瘟病毒"],
                    "targetList": ["猪瘟病毒", "圆环病毒2型"],
                    "host_species": "猪",
                    "host": "鸭",
                    "include_internal_control": True,
                    "need_internal_control": False,
                }
            )
            corrections = {
                "targets": ["猪瘟病毒", "非洲猪瘟病毒"],
                "host_species": "猪",
                "include_internal_control": True,
            }
            response = {
                "type": "agent_interaction_response",
                "task": "MULTIPLEX_PANEL",
                "interactions": [
                    {
                        "interactionId": item["interactionId"],
                        "answer": "EDIT",
                        "fieldValues": {
                            item["field"]: corrections[item["field"]]
                        },
                    }
                    for item in draft["ui_message"]["interactions"]
                ],
            }
            created = service.submit_wf02_v01_input_response(
                draft["draft_id"], response
            )

            self.assertEqual(created["stage"], "N05")
            persisted_draft = json.loads(
                (
                    Path(state_dir)
                    / "wf02-v01-drafts"
                    / f"{draft['draft_id']}.json"
                ).read_text(encoding="utf-8")
            )
            self.assertNotIn("targetList", persisted_draft["payload"])
            self.assertNotIn("host", persisted_draft["payload"])
            self.assertNotIn("need_internal_control", persisted_draft["payload"])

    def test_runtime_configuration_is_separate_and_accepts_legacy_top_level(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf02-v01-runtime-") as state_dir:
            batch = self._service(state_dir).create_wf02_v01_batch(
                {
                    **self._valid_payload(),
                    "instrument": "QuantStudio 5",
                }
            )
        runtime = batch["runtime_config_snapshot"]
        self.assertEqual(runtime["instrument"], "QuantStudio 5")
        self.assertTrue(runtime["rox_as_reference"])
        self.assertEqual(runtime["source"], "USER_OVERRIDE")
        self.assertEqual(runtime["rox_source"], "INSTRUMENT_DEFAULT")
        self.assertNotIn("instrument", batch["user_input_snapshot"])

    def test_n04_asks_only_for_host_and_preserves_targets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf02-v01-draft-") as state_dir:
            service = self._service(state_dir)
            draft = service.create_wf02_v01_batch(
                {
                    "targets": ["猪瘟病毒", "非洲猪瘟病毒"],
                    "include_internal_control": False,
                }
            )

            self.assertEqual(draft["status"], "WAITING_USER")
            self.assertEqual(draft["stage"], "N04")
            self.assertEqual(
                [issue["code"] for issue in draft["preflight"]["issues"]],
                ["WF02_HOST_SPECIES_REQUIRED"],
            )
            self.assertEqual(
                draft["preflight"]["input_echo"]["targets"],
                ["猪瘟病毒", "非洲猪瘟病毒"],
            )
            self.assertFalse(
                draft["preflight"]["input_echo"]["include_internal_control"]
            )
            message = draft["ui_message"]
            self.assertEqual(message["type"], "agent_interaction")
            self.assertEqual(message["task"], "MULTIPLEX_PANEL")
            self.assertEqual(message["field"], "host_species")
            self.assertEqual(message["flowControl"]["action"], "WAIT_FOR_USER")
            self.assertTrue(message["flowControl"]["stopCurrentTurn"])
            interaction_schema = json.loads(
                (ROOT / "schemas" / "agent-interaction.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            Draft202012Validator(interaction_schema).validate(message)

            stored_path = (
                Path(state_dir)
                / "wf02-v01-drafts"
                / f"{draft['draft_id']}.json"
            )
            persisted = json.loads(stored_path.read_text(encoding="utf-8"))
            self.assertEqual(
                persisted["payload"]["targets"],
                ["猪瘟病毒", "非洲猪瘟病毒"],
            )
            draft_schema = json.loads(
                (
                    ROOT
                    / "schemas"
                    / "wf02-v01-input-draft.schema.json"
                ).read_text(encoding="utf-8")
            )
            Draft202012Validator(draft_schema).validate(persisted)

    def test_n04_confirmation_creates_n05_task_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf02-v01-resume-") as state_dir:
            service = self._service(state_dir)
            draft = service.create_wf02_v01_batch(
                {"targets": ["猪瘟病毒", "非洲猪瘟病毒"]}
            )
            response = {
                "type": "agent_interaction_response",
                "task": "MULTIPLEX_PANEL",
                "interactionId": draft["ui_message"]["interactionId"],
                "answer": "EDIT",
                "fieldValues": {"host_species": "猪"},
            }

            created = service.submit_wf02_v01_input_response(
                draft["draft_id"], response
            )
            self.assertFalse(created["submission_replayed"])
            self.assertEqual(created["batch"]["stage"], "N05")
            self.assertEqual(
                created["batch"]["user_input_snapshot"]["host_species"],
                "猪",
            )
            self.assertEqual(
                [
                    item["display_name"]
                    for item in created["batch"]["user_input_snapshot"][
                        "targets"
                    ]
                ],
                ["猪瘟病毒", "非洲猪瘟病毒"],
            )
            self.assertTrue(
                created["batch"]["user_input_snapshot"][
                    "include_internal_control"
                ]
            )

            resolved = service.get_wf02_v01_draft(draft["draft_id"])
            self.assertEqual(resolved["status"], "RESOLVED")
            self.assertIsNone(resolved["ui_message"])
            resolved_document = json.loads(
                (
                    Path(state_dir)
                    / "wf02-v01-drafts"
                    / f"{draft['draft_id']}.json"
                ).read_text(encoding="utf-8")
            )
            draft_schema = json.loads(
                (
                    ROOT
                    / "schemas"
                    / "wf02-v01-input-draft.schema.json"
                ).read_text(encoding="utf-8")
            )
            Draft202012Validator(draft_schema).validate(resolved_document)

            replayed = service.submit_wf02_v01_input_response(
                draft["draft_id"], response
            )
            self.assertTrue(replayed["submission_replayed"])
            self.assertEqual(replayed["batch_id"], created["batch_id"])

    def test_n04_recovery_does_not_duplicate_task_after_partial_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf02-v01-recovery-") as state_dir:
            service = self._service(state_dir)
            draft = service.create_wf02_v01_batch(
                {"targets": ["猪瘟病毒", "非洲猪瘟病毒"]}
            )
            response = {
                "type": "agent_interaction_response",
                "task": "MULTIPLEX_PANEL",
                "interactionId": draft["ui_message"]["interactionId"],
                "answer": "EDIT",
                "fieldValues": {"host_species": "猪"},
            }

            store = service.wf02_v01.store
            with patch.object(
                store,
                "save_draft",
                side_effect=PersistenceError("模拟草稿落盘失败"),
            ):
                with self.assertRaises(PersistenceError):
                    service.submit_wf02_v01_input_response(
                        draft["draft_id"], response
                    )

            batch_paths = list(
                (Path(state_dir) / "wf02-v01-batches").glob("*.json")
            )
            self.assertEqual(len(batch_paths), 1)
            first_batch_id = batch_paths[0].stem

            recovered = service.submit_wf02_v01_input_response(
                draft["draft_id"], response
            )
            self.assertEqual(recovered["batch_id"], first_batch_id)
            self.assertEqual(
                len(list((Path(state_dir) / "wf02-v01-batches").glob("*.json"))),
                1,
            )

    def test_n04_requires_all_current_blockers_in_one_response(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf02-v01-complete-") as state_dir:
            service = self._service(state_dir)
            draft = service.create_wf02_v01_batch({})
            interactions = draft["ui_message"]["interactions"]
            self.assertEqual(len(interactions), 2)
            partial = {
                "type": "agent_interaction_response",
                "task": "MULTIPLEX_PANEL",
                "interactionId": interactions[0]["interactionId"],
                "answer": "EDIT",
                "fieldValues": {"targets": ["猪瘟病毒", "非洲猪瘟病毒"]},
            }
            with self.assertRaises(InputValidationError):
                service.submit_wf02_v01_input_response(
                    draft["draft_id"], partial
                )

            unchanged = service.get_wf02_v01_draft(draft["draft_id"])
            self.assertEqual(unchanged["status"], "WAITING_USER")
            self.assertEqual(len(unchanged["ui_message"]["interactions"]), 2)

    def test_n04_invalid_edit_reopens_only_the_remaining_blocker(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf02-v01-reopen-") as state_dir:
            service = self._service(state_dir)
            draft = service.create_wf02_v01_batch(
                {"targets": ["猪瘟病毒", "非洲猪瘟病毒"]}
            )

            reopened = service.submit_wf02_v01_input_response(
                draft["draft_id"],
                {
                    "type": "agent_interaction_response",
                    "task": "MULTIPLEX_PANEL",
                    "interactionId": draft["ui_message"]["interactionId"],
                    "answer": "EDIT",
                    "fieldValues": {"host_species": "   "},
                },
            )
            self.assertEqual(reopened["status"], "WAITING_USER")
            self.assertEqual(reopened["ui_message"]["field"], "host_species")
            self.assertEqual(
                [issue["code"] for issue in reopened["preflight"]["issues"]],
                ["WF02_HOST_SPECIES_REQUIRED"],
            )
            self.assertEqual(
                reopened["preflight"]["input_echo"]["targets"],
                ["猪瘟病毒", "非洲猪瘟病毒"],
            )

            created = service.submit_wf02_v01_input_response(
                draft["draft_id"],
                {
                    "type": "agent_interaction_response",
                    "task": "MULTIPLEX_PANEL",
                    "interactionId": reopened["ui_message"]["interactionId"],
                    "answer": "EDIT",
                    "fieldValues": {"host_species": "猪"},
                },
            )
            self.assertEqual(created["stage"], "N05")

    def test_rest_first_stage_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf02-v01-api-") as state_dir:
            isolated_service = self._service(state_dir)
            with patch.object(api, "service", isolated_service):
                with TestClient(api.app) as client:
                    preflight = client.post(
                        "/v1/wf02/tasks/preflight",
                        json=self._valid_payload(),
                    )
                    self.assertEqual(preflight.status_code, 200, preflight.text)
                    self.assertTrue(preflight.json()["can_create_task"])

                    created = client.post(
                        "/v1/wf02/tasks",
                        json=self._valid_payload(),
                    )
                    self.assertEqual(created.status_code, 200, created.text)
                    batch = created.json()
                    fetched = client.get(
                        f"/v1/wf02/tasks/{batch['batch_id']}"
                    )
                    self.assertEqual(fetched.status_code, 200, fetched.text)
                    self.assertEqual(fetched.json()["batch_id"], batch["batch_id"])

                    blocked = client.post(
                        "/v1/wf02/tasks",
                        json={"targets": ["猪瘟病毒", "非洲猪瘟病毒"]},
                    )
                    self.assertEqual(blocked.status_code, 200, blocked.text)
                    draft = blocked.json()
                    fetched_draft = client.get(
                        f"/v1/wf02/drafts/{draft['draft_id']}"
                    )
                    self.assertEqual(
                        fetched_draft.status_code,
                        200,
                        fetched_draft.text,
                    )
                    resumed = client.post(
                        (
                            f"/v1/wf02/drafts/{draft['draft_id']}"
                            "/interactions/response"
                        ),
                        json={
                            "type": "agent_interaction_response",
                            "task": "MULTIPLEX_PANEL",
                            "interactionId": draft["ui_message"][
                                "interactionId"
                            ],
                            "answer": "EDIT",
                            "fieldValues": {"host_species": "猪"},
                        },
                    )
                    self.assertEqual(resumed.status_code, 200, resumed.text)
                    self.assertEqual(resumed.json()["stage"], "N05")

                    missing = client.get("/v1/wf02/tasks/not-a-uuid")
                    self.assertEqual(missing.status_code, 404)

                    compatibility = client.post(
                        "/v1/wf02/multiplex",
                        json=self._valid_payload(),
                    )
                    self.assertEqual(
                        compatibility.status_code,
                        200,
                        compatibility.text,
                    )
                    self.assertEqual(
                        compatibility.json()["schema_version"],
                        "wf02-v0.1-batch-v1",
                    )
                    self.assertNotIn("multiplex", compatibility.json())

    def test_wf01_modules_do_not_import_wf02(self) -> None:
        violations: list[str] = []
        wf01_paths = sorted((ROOT / "src" / "agentd").glob("wf01*.py"))
        wf01_paths.extend(
            sorted((ROOT / "src" / "agentd" / "workflows").glob("wf01*.py"))
        )
        for path in wf01_paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                module = ""
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                elif isinstance(node, ast.Import):
                    module = ",".join(alias.name for alias in node.names)
                if "wf02" in module.casefold():
                    violations.append(f"{path.name}:{node.lineno}:{module}")
        self.assertEqual(violations, [])

    def test_capabilities_publish_only_the_new_direct_wf02_service(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf02-v01-capabilities-") as state_dir:
            capabilities = self._service(state_dir).capabilities()[
                "wf02_prd_v0_1"
            ]
        self.assertEqual(
            capabilities["mcp_tools"],
            [
                "create_wf02_batch",
                "submit_wf02_input_response",
                "get_wf02_batch",
                "run_wf02_until_blocked",
                "submit_wf02_child_response",
                "submit_wf02_internal_control_response",
                "create_wf02_version",
                "get_wf02_report",
                "get_wf02_panel",
                "get_wf02_export",
            ],
        )
        self.assertEqual(
            capabilities["status"],
            "N01_N32_BALANCE_SCORING_AND_REPORTING",
        )
        self.assertEqual(
            capabilities["implemented_nodes"],
            [
                "N01", "N02", "N03", "N04", "N05", "N06", "N07",
                "N08", "N09", "N10", "N11", "N12", "N13", "N14",
                "N15", "N16", "N16A", "N17", "N18", "N19", "N20",
                "N21", "N22", "N23", "N24", "N25", "N26",
                "N27", "N28", "N29", "N30", "N31", "N32",
            ],
        )
        self.assertEqual(
            capabilities["compatibility_alias"]["implementation"],
            "WF02V01BatchService",
        )
        self.assertTrue(capabilities["compatibility_alias"]["deprecated"])
        self.assertEqual(
            capabilities["state_isolation"],
            [
                "wf02-v01-batches",
                "wf02-v01-drafts",
                "wf02-v01-reports",
                "wf02-v01-exports",
            ],
        )
        self.assertEqual(
            capabilities["wf01_dependency"],
            "COMPLETED_WF01_PUBLIC_SERVICE_CONTRACT_ONLY",
        )
        self.assertEqual(
            capabilities["target_file_input"],
            {
                "policy_version": "wf02-one-file-per-target-v1",
                "one_file_per_target": True,
                "multiple_homologous_sequences_per_file": True,
                "multi_target_file_auto_split": False,
                "sequence_mode": "alignment_target",
                "upload_reference_field": "targets[].input_artifact_id",
            },
        )
        self.assertEqual(
            capabilities["p6_dependency"],
            {
                "service_operation": "assess_p6_amplicon_structure",
                "mode": "MFE_PLUS_SUBOPT",
                "blocking": False,
                "unavailable_behavior": "NOT_EVALUATED_CONTINUE",
            },
        )
        self.assertEqual(
            capabilities["p5_dependency"],
            {
                "service_operation": "design_p5_internal_control",
                "mode": "DIRECT_FROM_WF02",
                "wf03_nested": False,
                "channel_policy_version": "wf02-ic-channel-dynamic-v2",
                "failure_behavior": "FAIL_CONTINUE_WITHOUT_INTERNAL",
            },
        )
        self.assertEqual(
            capabilities["p4_dependency"],
            {
                "service_operation": "check_p4_panel",
                "implementation": "MultiplexEvaluator.check_panel",
                "wf02_embeds_p4_algorithms": False,
                "batch_size": 500,
                "exhaustive_with_cursor": True,
                "r17_policy_version": "wf02-r17-caution-v1",
                "relaxation_policy_version": "wf02-relaxation-conservative-v1",
                "relaxed_cross_dimer_dg_kcal": -7.0,
                "relaxed_tm_spread_celsius": 4.0,
            },
        )
        self.assertEqual(
            capabilities["balance_and_delivery"]["report_formats"],
            ["html", "csv", "pdf"],
        )
        self.assertFalse(
            capabilities["balance_and_delivery"]["source_report_overwrite"]
        )


if __name__ == "__main__":
    unittest.main()

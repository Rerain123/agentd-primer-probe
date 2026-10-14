from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.interactions import (
    INTERACTION_TYPE,
    build_candidate_shortfall_interaction,
)
from agentd.errors import InputValidationError
from agentd.service import AgentDService


class AgentInteractionTests(unittest.TestCase):
    @staticmethod
    def _unsupported_host_payload() -> dict:
        return {
            "task_name": "unsupported-host-confirmation",
            "fasta_text": ">target_001\n" + "ACGT" * 30,
            "batch_defaults": {
                "target_organism": {"name": "Synthetic test virus"},
                "hosts": [{"name": "unconfigured_test_host"}],
            },
        }

    def test_completed_primer_design_output_contract_accepts_real_exports(self) -> None:
        try:
            from jsonschema import Draft202012Validator
        except ImportError:  # pragma: no cover - jsonschema is a dev dependency
            self.skipTest("jsonschema is not installed")

        schema = json.loads(
            (ROOT / "schemas" / "primer-design-output.schema.json").read_text(
                encoding="utf-8"
            )
        )
        payload = {
            "type": "primer_design_output",
            "data": {
                "status": "COMPLETED",
                "reportTitle": "CSFV 引物探针设计报告",
                "summaryText": "已完成设计，并已生成可下载报告。",
                "primaryAttachmentId": "attachment_html",
                "attachments": [
                    {
                        "attachmentId": "attachment_html",
                        "fileName": "CSFV_引物探针设计报告.html",
                        "fileType": "HTML",
                        "mimeType": "text/html; charset=utf-8",
                        "fileUrl": "/data/CSFV_引物探针设计报告.html",
                    },
                    {
                        "attachmentId": "attachment_process_html",
                        "fileName": "CSFV_科学过程详情报告.html",
                        "fileType": "HTML",
                        "mimeType": "text/html; charset=utf-8",
                        "fileUrl": "/data/CSFV_科学过程详情报告.html",
                    },
                    {
                        "attachmentId": "attachment_scientific_data",
                        "fileName": "CSFV_科学工具原始数据.zip",
                        "fileType": "ZIP",
                        "mimeType": "application/zip",
                        "fileUrl": "/data/CSFV_科学工具原始数据.zip",
                    },
                ],
                "issues": [],
            },
        }

        errors = list(Draft202012Validator(schema).iter_errors(payload))
        self.assertEqual(errors, [])

    def test_failed_primer_design_output_cannot_advertise_attachments(self) -> None:
        try:
            from jsonschema import Draft202012Validator
        except ImportError:  # pragma: no cover - jsonschema is a dev dependency
            self.skipTest("jsonschema is not installed")

        schema = json.loads(
            (ROOT / "schemas" / "primer-design-output.schema.json").read_text(
                encoding="utf-8"
            )
        )
        payload = {
            "type": "primer_design_output",
            "data": {
                "status": "FAILED",
                "reportTitle": "CSFV 引物探针设计报告",
                "summaryText": "报告生成失败。",
                "primaryAttachmentId": "attachment_html",
                "attachments": [
                    {
                        "attachmentId": "attachment_html",
                        "fileName": "wf01-report.html",
                        "fileType": "HTML",
                        "mimeType": "text/html; charset=utf-8",
                        "fileUrl": "/data/wf01-report.html",
                    }
                ],
                "issues": ["M6_RENDER_ERROR: 报告生成失败。"],
            },
        }

        errors = list(Draft202012Validator(schema).iter_errors(payload))
        self.assertTrue(errors)

    def test_output_contract_rejects_legacy_report_paths_and_xlsx(self) -> None:
        try:
            from jsonschema import Draft202012Validator
        except ImportError:  # pragma: no cover - jsonschema is a dev dependency
            self.skipTest("jsonschema is not installed")

        schema = json.loads(
            (ROOT / "schemas" / "primer-design-output.schema.json").read_text(
                encoding="utf-8"
            )
        )
        payload = {
            "type": "primer_design_output",
            "data": {
                "status": "COMPLETED",
                "reportTitle": "设计报告",
                "summaryText": "已完成设计。",
                "primaryAttachmentId": "attachment_xlsx",
                "attachments": [
                    {
                        "attachmentId": "attachment_xlsx",
                        "fileName": "wf01-report.xlsx",
                        "fileType": "XLSX",
                        "mimeType": "application/octet-stream",
                        "fileUrl": "/data/reports/batch_001/wf01-report.xlsx",
                    }
                ],
                "issues": [],
            },
        }

        errors = list(Draft202012Validator(schema).iter_errors(payload))
        self.assertGreaterEqual(len(errors), 2)

    def test_popup_contract_supports_an_ordered_variable_length_question_list(self) -> None:
        try:
            from jsonschema import Draft202012Validator
        except ImportError:  # pragma: no cover - jsonschema is a dev dependency
            self.skipTest("jsonschema is not installed")

        schema = json.loads(
            (ROOT / "schemas" / "agent-interaction.schema.json").read_text(
                encoding="utf-8"
            )
        )
        payload = {
            "type": "agent_interaction",
            "task": "PRIMER_DESIGN",
            "flowControl": {
                "action": "WAIT_FOR_USER",
                "stopCurrentTurn": True,
                "resumeMessageType": "agent_interaction_response",
            },
            "interactions": [
                {
                    "interactionId": "interaction_001",
                    "reason": "候选数量不足。",
                    "question": "下一步如何处理？",
                    "inputType": "SINGLE_SELECT",
                    "options": [
                        {"value": "ACCEPT_CURRENT", "label": "接受当前结果"}
                    ],
                    "inputFields": [],
                    "answer": None,
                },
                {
                    "interactionId": "interaction_002",
                    "reason": "需要确认宿主。",
                    "question": "请选择宿主。",
                    "inputType": "FORM",
                    "options": [],
                    "inputFields": [{"key": "host", "inputType": "SELECT"}],
                    "answer": None,
                },
            ],
        }

        errors = list(Draft202012Validator(schema).iter_errors(payload))
        self.assertEqual(errors, [])

    def test_popup_contract_accepts_flat_single_question_shape(self) -> None:
        try:
            from jsonschema import Draft202012Validator
        except ImportError:  # pragma: no cover - jsonschema is a dev dependency
            self.skipTest("jsonschema is not installed")

        schema = json.loads(
            (ROOT / "schemas" / "agent-interaction.schema.json").read_text(
                encoding="utf-8"
            )
        )
        payload = {
            "type": "agent_interaction",
            "task": "PRIMER_DESIGN",
            "flowControl": {
                "action": "WAIT_FOR_USER",
                "stopCurrentTurn": True,
                "resumeMessageType": "agent_interaction_response",
            },
            "interactionId": "interaction_001",
            "reason": "当前仅获得6套有效候选，少于期望的10套。",
            "question": "下一步如何处理？",
            "inputType": "SINGLE_SELECT",
            "options": [
                {"value": "ACCEPT_CURRENT", "label": "接受当前结果"}
            ],
            "answer": None,
        }

        errors = list(Draft202012Validator(schema).iter_errors(payload))
        self.assertEqual(errors, [])

    def test_popup_contract_requires_at_least_one_question(self) -> None:
        try:
            from jsonschema import Draft202012Validator
        except ImportError:  # pragma: no cover - jsonschema is a dev dependency
            self.skipTest("jsonschema is not installed")

        schema = json.loads(
            (ROOT / "schemas" / "agent-interaction.schema.json").read_text(
                encoding="utf-8"
            )
        )
        payload = {
            "type": "agent_interaction",
            "task": "PRIMER_DESIGN",
            "flowControl": {
                "action": "WAIT_FOR_USER",
                "stopCurrentTurn": True,
                "resumeMessageType": "agent_interaction_response",
            },
            "interactions": [],
        }

        errors = list(Draft202012Validator(schema).iter_errors(payload))
        self.assertTrue(errors)

    def test_candidate_shortfall_is_a_structured_waiting_user_payload(self) -> None:
        interaction = build_candidate_shortfall_interaction(
            "batch_001",
            target_id="target_001",
            candidate_count=1,
        )
        self.assertEqual(interaction["type"], INTERACTION_TYPE)
        self.assertEqual(interaction["data"]["status"], "WAITING_USER")
        self.assertEqual(
            interaction["data"]["blocker"]["code"],
            "INSUFFICIENT_CANDIDATES",
        )
        self.assertEqual(interaction["data"]["context"]["candidateCount"], 1)
        self.assertIn(
            "CONTINUE_SEARCH",
            [item["value"] for item in interaction["data"]["options"]],
        )

    def test_missing_specificity_context_does_not_create_agent_interactions(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-interaction-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            batch = service.create_wf01_v02_batch(
                {
                    "task_name": "missing-inputs",
                    "fasta_text": ">target_001\n" + "ACGT" * 30,
                    "batch_defaults": {},
                }
            )
            self.assertEqual(batch["status"], "QUEUED")
            self.assertFalse(batch["agent_interactions"])
            self.assertEqual(
                service.get_wf01_v02_agent_summary(batch["batch_id"])[
                    "next_action"
                ],
                "RUN_M2",
            )
            self.assertIsNone(batch["ui_message"])

    def test_pending_interaction_blocks_every_later_scientific_stage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-interaction-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            batch = service.create_wf01_v02_batch(
                self._unsupported_host_payload()
            )

            for stage in (
                service.execute_wf01_v02_m2,
                service.execute_wf01_v02_m3,
                service.execute_wf01_v02_m4,
                service.execute_wf01_v02_m5,
                service.execute_wf01_v02_m6,
            ):
                with self.subTest(stage=stage.__name__):
                    with self.assertRaisesRegex(
                        InputValidationError,
                        "等待用户回复",
                    ) as caught:
                        stage(batch["batch_id"])
                    self.assertEqual(
                        caught.exception.details["next_action"],
                        "WAIT_FOR_USER",
                    )

    def test_continue_with_not_evaluated_resolves_and_queues_batch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-interaction-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            batch = service.create_wf01_v02_batch(
                self._unsupported_host_payload()
            )
            self.assertEqual(batch["status"], "PENDING_CONFIRMATION")
            interaction = batch["agent_interactions"][0]
            interaction_id = interaction["data"]["interactionId"]

            response = {
                "type": "agent_interaction_response",
                "task": "PRIMER_DESIGN",
                "interactions": [
                    {
                        "interactionId": interaction_id,
                        "answer": "CONTINUE_WITH_NOT_EVALUATED",
                        "fieldValues": {},
                        "customInstruction": None,
                    }
                ],
            }
            result = service.submit_wf01_v02_interaction_response(
                batch["batch_id"], response
            )

            self.assertEqual(result["status"], "QUEUED")
            self.assertEqual(result["next_action"], "RUN_M2")
            self.assertFalse(result["submission_replayed"])
            persisted = service.get_wf01_v02_batch(batch["batch_id"])
            self.assertEqual(persisted["status"], "QUEUED")
            self.assertEqual(persisted["targets"][0]["status"], "QUEUED")
            self.assertTrue(persisted["preflight"]["can_auto_run"])
            self.assertEqual(
                persisted["agent_interactions"][0]["data"]["status"],
                "RESOLVED",
            )
            self.assertEqual(
                persisted["agent_interactions"][0]["data"]["userResponse"][
                    "selectedOption"
                ],
                "CONTINUE_WITH_NOT_EVALUATED",
            )
            self.assertEqual(
                persisted["preflight"]["issues"][0]["resolution"]["status"],
                "ACCEPTED",
            )
            self.assertEqual(
                persisted["targets"][0]["confirmation_overrides"][0]["policy"],
                "CONTINUE_WITH_NOT_EVALUATED",
            )

            replay = service.submit_wf01_v02_interaction_response(
                batch["batch_id"], response
            )
            self.assertTrue(replay["submission_replayed"])
            self.assertEqual(replay["status"], "QUEUED")

            m2 = service.execute_wf01_v02_m2(
                batch["batch_id"], {"allow_development_fallback": True}
            )
            self.assertEqual(m2["status"], "EXPLORING")
            self.assertIn(
                m2["m2_outcome"],
                {"COMPLETED", "COMPLETED_WITH_TARGET_ERRORS"},
            )

    def test_flat_interaction_response_resolves_and_queues_batch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-interaction-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            batch = service.create_wf01_v02_batch(
                self._unsupported_host_payload()
            )
            interaction_id = batch["agent_interactions"][0]["data"][
                "interactionId"
            ]

            result = service.submit_wf01_v02_interaction_response(
                batch["batch_id"],
                {
                    "type": "agent_interaction_response",
                    "task": "PRIMER_DESIGN",
                    "interactionId": interaction_id,
                    "answer": "CONTINUE_WITH_NOT_EVALUATED",
                },
            )

            self.assertEqual(result["status"], "QUEUED")
            self.assertEqual(result["next_action"], "RUN_M2")
            self.assertIsNone(result["ui_message"])

    def test_unrouted_interaction_action_does_not_mutate_batch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-interaction-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            batch = service.create_wf01_v02_batch(
                self._unsupported_host_payload()
            )
            interaction_id = batch["agent_interactions"][0]["data"][
                "interactionId"
            ]

            with self.assertRaisesRegex(
                InputValidationError, "尚未接入确定性路由"
            ):
                service.submit_wf01_v02_interaction_response(
                    batch["batch_id"],
                    {
                        "type": "agent_interaction_response",
                        "task": "PRIMER_DESIGN",
                        "interactions": [
                            {
                                "interactionId": interaction_id,
                                "answer": "EDIT",
                            }
                        ],
                    },
                )

            persisted = service.get_wf01_v02_batch(batch["batch_id"])
            self.assertEqual(persisted["status"], "PENDING_CONFIRMATION")
            self.assertEqual(
                persisted["agent_interactions"][0]["data"]["status"],
                "WAITING_USER",
            )


if __name__ == "__main__":
    unittest.main()

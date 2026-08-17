from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.errors import InputValidationError
from agentd.mcp_wf01_input import normalize_wf01_mcp_payload
from agentd.wf01_v02 import WF01V02Preflight


def design_input_form() -> dict:
    return {
        "type": "primer_design_input",
        "data": {
            "status": "WAITING_USER",
            "confirmed": False,
            "canStart": True,
            "summaryText": "猪瘟病毒（CSFV）·宿主：鸭·覆盖多个分型/毒株",
            "sourceFile": {"fileName": "CSFV_5UTR.fasta", "fileType": "FASTA"},
            "designInput": {
                "targetOrganism": "猪瘟病毒（CSFV）",
                "host": "猪",
                "coverageRequirement": "覆盖多个分型/毒株",
            },
            "issues": [],
        },
    }


def user_update(**fields: str) -> dict:
    return {
        "type": "primer_design_input_update",
        "data": {"designInput": fields},
    }


class MCPWF01InputTests(unittest.TestCase):
    def test_legacy_user_update_must_match_design_input_interaction_id(self) -> None:
        form = design_input_form()
        form_id = "design-input-0123456789abcdef0123456789abcdef"
        form["interactionId"] = form_id
        form["flowControl"] = {
            "action": "WAIT_FOR_USER",
            "stopCurrentTurn": True,
            "resumeMessageType": "primer_design_input_update",
            "deduplicationKey": form_id,
            "onDuplicate": "KEEP_EXISTING",
        }
        update = user_update(host="鸭")
        update["interactionId"] = (
            "design-input-fedcba9876543210fedcba9876543210"
        )

        with self.assertRaises(InputValidationError) as caught:
            normalize_wf01_mcp_payload(
                {"fasta_text": ">seq\n" + "ACGT" * 60},
                form,
                update,
            )
        self.assertIn("interactionId", caught.exception.message)

    def test_direct_payload_fallback_does_not_depend_on_model_json(self) -> None:
        normalized, mapping = normalize_wf01_mcp_payload(
            {
                "task_name": "CSFV",
                "fasta_text": ">CSFV_01\n" + "ACGT" * 60,
                "designInput": {
                    "targetOrganism": "猪瘟病毒（CSFV）",
                    "host": "鸭",
                    "coverageRequirement": "覆盖多个分型/毒株",
                },
                "sourceFile": {
                    "fileName": "CSFV_5UTR.fasta",
                    "fileType": "FASTA",
                },
            }
        )

        self.assertEqual(
            mapping["input_mode"], "DIRECT_MODEL_INDEPENDENT_FALLBACK"
        )
        self.assertEqual(
            normalized["batch_defaults"]["target_organism"],
            {"name": "猪瘟病毒（CSFV）"},
        )
        self.assertEqual(
            normalized["batch_defaults"]["hosts"], [{"name": "鸭"}]
        )
        self.assertNotIn("designInput", normalized)
        self.assertNotIn("sourceFile", normalized)

        preflight = WF01V02Preflight(normalized).run()
        self.assertTrue(preflight["can_auto_run"])

    def test_form_without_user_field_update_cannot_start_preflight(self) -> None:
        with self.assertRaises(InputValidationError) as caught:
            normalize_wf01_mcp_payload(
                {"fasta_text": ">seq\n" + "ACGT" * 60},
                design_input_form(),
                {},
            )
        details = caught.exception.details
        self.assertIn(
            "user_field_updates.targetOrganism",
            details["invalid_or_missing_fields"],
        )
        self.assertTrue(details["retry_in_current_turn"])
        self.assertTrue(details["model_must_not_rebuild_messages"])
        self.assertTrue(details["reuse_current_context_only"])
        self.assertEqual(details["maximum_mapping_retries"], 1)
        self.assertIn("复用最近一次", details["retry_policy"])
        self.assertIn("不要改写对象", details["retry_policy"])
        self.assertNotIn("expected_assistant_form", details)
        self.assertNotIn("expected_user_update", details)

    def test_unchanged_form_confirmation_inherits_all_prefilled_fields(self) -> None:
        normalized, mapping = normalize_wf01_mcp_payload(
            {"fasta_text": ">seq\n" + "ACGT" * 60},
            design_input_form(),
            user_update(),
        )

        self.assertEqual(
            normalized["batch_defaults"]["target_organism"],
            {"name": "猪瘟病毒（CSFV）"},
        )
        self.assertEqual(normalized["batch_defaults"]["hosts"], [{"name": "猪"}])
        self.assertEqual(
            normalized["batch_defaults"]["coverage_requirement"],
            "覆盖多个分型/毒株",
        )
        self.assertEqual(mapping["user_updated_fields"], [])
        self.assertEqual(
            mapping["field_sources"],
            {
                "targetOrganism": "design_input_form",
                "host": "design_input_form",
                "coverageRequirement": "design_input_form",
            },
        )
        self.assertTrue(WF01V02Preflight(normalized).run()["can_auto_run"])

    def test_unchanged_form_confirmation_accepts_empty_data(self) -> None:
        normalized, mapping = normalize_wf01_mcp_payload(
            {"fasta_text": ">seq\n" + "ACGT" * 60},
            design_input_form(),
            {"type": "primer_design_input_update", "data": {}},
        )

        self.assertEqual(mapping["user_updated_fields"], [])
        self.assertEqual(normalized["batch_defaults"]["hosts"], [{"name": "猪"}])
        self.assertTrue(WF01V02Preflight(normalized).run()["can_auto_run"])

    def test_sequence_only_form_can_start_without_organism_context(self) -> None:
        form = design_input_form()
        form["data"]["canStart"] = True
        form["data"]["designInput"] = {
            "targetOrganism": "",
            "host": "",
            "coverageRequirement": "",
        }
        normalized, mapping = normalize_wf01_mcp_payload(
            {"fasta_text": ">seq\n" + "ACGT" * 30},
            form,
            user_update(),
        )

        self.assertNotIn("batch_defaults", normalized)
        self.assertFalse(mapping["specificity_context_complete"])
        self.assertEqual(
            mapping["missing_specificity_fields"],
            ["targetOrganism"],
        )
        preflight = WF01V02Preflight(normalized).run()
        self.assertTrue(preflight["can_auto_run"])
        self.assertFalse(preflight["targets"][0]["specificity_input_complete"])

    def test_target_without_host_is_complete_specificity_context(self) -> None:
        form = design_input_form()
        form["data"]["designInput"]["host"] = ""

        normalized, mapping = normalize_wf01_mcp_payload(
            {"fasta_text": ">seq\n" + "ACGT" * 30},
            form,
            user_update(),
        )

        self.assertTrue(mapping["specificity_context_complete"])
        self.assertEqual(mapping["missing_specificity_fields"], [])
        preflight = WF01V02Preflight(normalized).run()
        self.assertEqual(
            preflight["targets"][0]["execution_path"],
            "FULL_SPECIFICITY",
        )

    def test_confirmed_form_snapshot_is_accepted_with_separate_user_update(self) -> None:
        form = design_input_form()
        form["data"]["confirmed"] = True

        normalized, mapping = normalize_wf01_mcp_payload(
            {"fasta_text": ">seq\n" + "ACGT" * 60},
            form,
            user_update(host="鸭"),
        )

        self.assertEqual(normalized["batch_defaults"]["hosts"], [{"name": "鸭"}])
        self.assertEqual(mapping["input_mode"], "FORM_PLUS_USER_UPDATE")

    def test_user_field_delta_overrides_prefill_and_agent_aliases(self) -> None:
        normalized, mapping = normalize_wf01_mcp_payload(
            {
                "target_object": {"normalized_name": "错误的旧靶标"},
                "host_objects": [{"normalized_name": "猪"}],
                "fasta_text": ">CSFV_01\n" + "ACGT" * 60,
            },
            design_input_form(),
            user_update(host="鸭"),
        )
        self.assertEqual(
            normalized["batch_defaults"]["target_organism"],
            {"name": "猪瘟病毒（CSFV）"},
        )
        self.assertEqual(normalized["batch_defaults"]["hosts"], [{"name": "鸭"}])
        self.assertEqual(
            normalized["batch_defaults"]["coverage_requirement"],
            "覆盖多个分型/毒株",
        )
        self.assertEqual(normalized["sequence_files"][0]["file_name"], "CSFV_5UTR.fasta")
        self.assertNotIn("target_object", normalized)
        self.assertNotIn("host_objects", normalized)
        self.assertIn("target_object", mapping["mapped_aliases"])
        self.assertEqual(mapping["user_updated_fields"], ["host"])
        self.assertEqual(mapping["input_mode"], "FORM_PLUS_USER_UPDATE")
        self.assertEqual(
            mapping["field_sources"],
            {
                "targetOrganism": "design_input_form",
                "host": "user_field_updates",
                "coverageRequirement": "design_input_form",
            },
        )
        self.assertEqual(
            mapping["effective_design_input"],
            {
                "targetOrganism": "猪瘟病毒（CSFV）",
                "host": "鸭",
                "coverageRequirement": "覆盖多个分型/毒株",
            },
        )
        self.assertTrue(mapping["do_not_retry_with_alternate_field_names"])

        preflight = WF01V02Preflight(normalized).run()
        self.assertTrue(preflight["can_auto_run"])
        target = preflight["targets"][0]
        self.assertEqual(target["target_object"]["identifier"], "猪瘟病毒（CSFV）")
        self.assertEqual(target["host_objects"][0]["identifier"], "鸭")
        self.assertEqual(target["host_objects"][0]["normalized_name"], "duck")

    def test_input_artifact_or_fasta_input_is_required(self) -> None:
        with self.assertRaises(InputValidationError) as caught:
            normalize_wf01_mcp_payload(
                {},
                design_input_form(),
                user_update(host="鸭"),
            )
        self.assertIn("上传文件引用或直接序列输入", caught.exception.message)
        self.assertIn(
            "input_artifact_id",
            caught.exception.details["required_payload_fields"],
        )

    def test_inline_artifact_reference_is_recovered_from_form(self) -> None:
        form = design_input_form()
        artifact_id = "himat-0123456789abcdef0123456789abcdef"
        form["data"]["sourceFile"] = {
            "fileName": "直接输入序列.fasta",
            "fileType": "FASTA",
            "inputMode": "INLINE_SEQUENCE",
            "inputArtifactId": artifact_id,
        }

        normalized, mapping = normalize_wf01_mcp_payload(
            {},
            form,
            user_update(host="猪"),
        )

        self.assertEqual(normalized["input_artifact_id"], artifact_id)
        self.assertIn("sourceFile.inputArtifactId", mapping["mapped_aliases"])
        self.assertEqual(
            mapping["authoritative_mapping"][
                "assistant.data.sourceFile.inputArtifactId"
            ],
            "input_artifact_id",
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.schema import validate_output
from agentd.errors import InputValidationError
from agentd.input_artifacts import WF05_PUBLIC_INPUT_FIELDS
from agentd.service import AgentDService
from agentd_workflow_snapshots.wf05_v1.agentd.workflows.wf05_v01 import (
    WF05_MAX_SEQUENCE_RECORDS,
    WF05_MAX_SINGLE_SEQUENCE_CHARS,
    WF05_RETRY_FIELDS,
    summarize_wf05_result,
)
from agentd_workflow_snapshots.wf05_v1.agentd.wf05_report_html import (
    render_workflow_report_html,
)
from agentd_workflow_snapshots.wf05_v1.agentd.errors import (
    ToolUnavailableError as SnapshotToolUnavailableError,
)


FORWARD = "GCGAGGAAGATGTACGGATA"
REVERSE = "GCTACAGGCTCCCTTCTAA"
PROBE = "ATCGCGGTTAACCTAGTCCCTGTGC"
TEMPLATE = (
    "TGGGCGAACTTGGTCACCCCGAAGTATCTGATGAGATGATCACCGAGAGCCGGGG"
    "GCGAGGAAGATGTACGGATACTTTCCGCACAGGGACTAGGTTAACCGCGATTTCTT"
    "ATCCTGCGATAGCCGGCCGTGTAAACCTTTCTTAGGCATGGCAGAAAATGCAATCA"
    "TATAACGGGGTTAGAAGGGAGCCTGTAGCATGCTGCCCGATTTCCCGTGTACCCCT"
    "GTCGCTGCGAAGTATATCCAGAGGTGCCGGTGCTAGCC"
)
INPUT_SCHEMA = json.loads(
    (ROOT / "schemas" / "wf05-v01-input.schema.json").read_text(encoding="utf-8")
)
REPORT_SCHEMA = json.loads(
    (ROOT / "schemas" / "wf05-v01-report.schema.json").read_text(encoding="utf-8")
)
RETRY_SCHEMA = json.loads(
    (ROOT / "schemas" / "wf05-v01-retry.schema.json").read_text(encoding="utf-8")
)


def payload_with_sequences(sequences: list[dict]) -> dict:
    return {
        "target_id": "fixture_target",
        "target_species": "fixture_context",
        "forward_primer": FORWARD,
        "reverse_primer": REVERSE,
        "probe": PROBE,
        "sequences": sequences,
        "allow_fallback": True,
    }


class WF05Tests(unittest.TestCase):
    def test_public_input_contract_has_one_allowlist(self) -> None:
        schema_fields = set(INPUT_SCHEMA["properties"])
        self.assertEqual(schema_fields, set(WF05_PUBLIC_INPUT_FIELDS))
        self.assertEqual(schema_fields, set(WF05_RETRY_FIELDS))
        self.assertEqual(
            set(RETRY_SCHEMA["properties"]),
            schema_fields | {"reason"},
        )
        self.assertEqual(
            INPUT_SCHEMA["properties"]["sequences"]["maxItems"],
            WF05_MAX_SEQUENCE_RECORDS,
        )
        self.assertEqual(
            RETRY_SCHEMA["properties"]["sequences"]["maxItems"],
            WF05_MAX_SEQUENCE_RECORDS,
        )
        self.assertEqual(
            INPUT_SCHEMA["$defs"]["sequence"]["maxLength"],
            WF05_MAX_SINGLE_SEQUENCE_CHARS,
        )

    def test_high_frequency_three_prime_variant_recommends_redesign(self) -> None:
        forward_start = TEMPLATE.index(FORWARD)
        variant_position = forward_start + len(FORWARD) - 1
        replacement = "C" if TEMPLATE[variant_position] != "C" else "A"
        mutant = TEMPLATE[:variant_position] + replacement + TEMPLATE[variant_position + 1 :]
        sequences = [{"id": "reference", "sequence": TEMPLATE}]
        sequences.extend({"id": f"variant_{index}", "sequence": mutant} for index in range(2))
        sequences.extend({"id": f"wild_{index}", "sequence": TEMPLATE} for index in range(27))
        with tempfile.TemporaryDirectory(prefix="agentd-wf05-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            payload = payload_with_sequences(sequences)
            Draft202012Validator(INPUT_SCHEMA).validate(payload)
            output = service.run("WF-05", payload, persist=False)
        validate_output(output)
        Draft202012Validator(REPORT_SCHEMA).validate(
            summarize_wf05_result(output)
        )
        variation = output["variation"]
        self.assertEqual(variation["sequences_analyzed"], 30)
        self.assertAlmostEqual(variation["coverage_100pct_match"], 28 / 30, places=5)
        self.assertEqual(variation["verdict"], "REDESIGN_RECOMMENDED")
        self.assertEqual(variation["redesign_route"], "WF-01")
        self.assertFalse(variation["redesign_handoff"]["automatic_execution"])
        self.assertEqual(variation["high_risk_sites"][0]["risk_level"], "HIGH")
        self.assertIn("C29_candidate_comparison_markdown", output["reports"])
        self.assertEqual(output["workflow_version"], "wf05-v0.1-rules-2026-08-26")
        self.assertEqual(output["decision"], "REDESIGN_RECOMMENDED")
        self.assertEqual(
            output["next_action"],
            "RETRY" if output["execution_status"] == "PARTIAL" else "HANDOFF_WF01",
        )
        self.assertEqual(len(output["steps"]), 6)
        self.assertNotIn("sequences", output["input"])
        self.assertEqual(
            output["provenance"]["existing_tool_invocations"],
            ["C0", "P1", "C5", "C7"],
        )
        self.assertEqual(
            set(output["provenance"]["existing_tool_bindings"]),
            {"C0", "C5", "C7", "P1", "C21"},
        )
        self.assertEqual(
            output["provenance"]["existing_tool_bindings"]["C21"]["binding"],
            "NOT_AVAILABLE_NOT_EVALUATED",
        )
        self.assertEqual(
            set(output["tool_status"]),
            {"C0", "C5", "C7", "P1", "C21"},
        )

        rendered_source = dict(output)
        rendered_source["warnings"] = ["</script><script>alert(1)</script>"]
        rendered = render_workflow_report_html(rendered_source).decode("utf-8")
        visible = rendered.split(
            '<script type="application/json" id="workflow-report-data">',
            1,
        )[0]
        self.assertIn('id="workflow-report-data"', rendered)
        self.assertNotIn("</script><script>alert(1)</script>", rendered)
        self.assertIn(FORWARD, rendered)
        self.assertIn("per_sequence", rendered)
        self.assertIn("变异耐受性评估报告", visible)
        self.assertIn("建议重新设计", visible)
        self.assertIn("30 条", visible)
        self.assertIn("完全匹配", visible)
        self.assertIn("重点变异位点", visible)
        self.assertIn("当前方案", visible)
        self.assertIn("多序列比对", visible)
        self.assertIn("正向引物", visible)
        self.assertIn("定稿前", visible)
        for internal_term in (
            "SUCCEEDED",
            "REDESIGN_RECOMMENDED",
            "HANDOFF_WF01",
            "WF05_STEP",
            "EXACT_COVERAGE_BELOW_ACCEPTABLE_THRESHOLD",
            "HIGH_FREQUENCY_THREE_PRIME_VARIANT",
            "fixture_target",
            "fixture_context",
        ):
            self.assertNotIn(internal_term, visible)

    def test_persistence_and_retry_use_the_wf05_namespace(self) -> None:
        forward_start = TEMPLATE.index(FORWARD)
        variant_position = forward_start + len(FORWARD) - 1
        replacement = "C" if TEMPLATE[variant_position] != "C" else "A"
        mutant = TEMPLATE[:variant_position] + replacement + TEMPLATE[variant_position + 1 :]
        initial_sequences = [{"id": "reference", "sequence": TEMPLATE}]
        initial_sequences.extend(
            {"id": f"variant_{index}", "sequence": mutant}
            for index in range(2)
        )
        initial_sequences.extend(
            {"id": f"wild_{index}", "sequence": TEMPLATE}
            for index in range(27)
        )
        replacement_sequences = [
            {"id": f"wild_{index}", "sequence": TEMPLATE}
            for index in range(30)
        ]
        replacement_sequences[0]["id"] = "reference"

        with tempfile.TemporaryDirectory(prefix="agentd-wf05-retry-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            parent = service.run_wf05_variation(
                payload_with_sequences(initial_sequences)
            )
            self.assertEqual(
                (service.wf05_store_v1.root / f"{parent['job_id']}.json").is_file(),
                True,
            )
            child = service.retry_wf05_variation(
                parent["job_id"],
                {
                    "sequences": replacement_sequences,
                    "reason": "replace sequence set",
                },
            )
            report = service.get_wf05_report(child["job_id"])
            parent_after_retry = service.get_wf05_result(parent["job_id"])

        self.assertNotEqual(child["job_id"], parent["job_id"])
        self.assertEqual(child["parent_job_id"], parent["job_id"])
        self.assertEqual(child["decision"], "ROBUST")
        self.assertEqual(parent_after_retry["job_id"], parent["job_id"])
        self.assertEqual(parent_after_retry["decision"], parent["decision"])
        self.assertNotIn("per_sequence", report["variation"])

    def test_retry_requires_the_private_parent_input_attachment(self) -> None:
        sequences = [
            {"id": "reference", "sequence": TEMPLATE},
            {"id": "second", "sequence": TEMPLATE},
        ]
        with tempfile.TemporaryDirectory(prefix="agentd-wf05-source-guard-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            parent = service.run_wf05_variation(payload_with_sequences(sequences))
            attachment = (
                service.wf05_store_v1.root
                / f"{parent['job_id']}.wf05-input.json"
            )
            attachment.unlink()

            with self.assertRaisesRegex(
                InputValidationError,
                "原始输入不可用",
            ):
                service.retry_wf05_variation(parent["job_id"], {})

    def test_retry_rejects_empty_reason(self) -> None:
        sequences = [
            {"id": "reference", "sequence": TEMPLATE},
            {"id": "second", "sequence": TEMPLATE},
        ]
        with tempfile.TemporaryDirectory(prefix="agentd-wf05-reason-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            parent = service.run_wf05_variation(payload_with_sequences(sequences))
            with self.assertRaisesRegex(InputValidationError, "原因"):
                service.retry_wf05_variation(
                    parent["job_id"],
                    {"reason": "   "},
                )

    def test_dependency_failure_is_persisted_and_retryable(self) -> None:
        sequences = [
            {"id": "reference", "sequence": TEMPLATE},
            {"id": "second", "sequence": TEMPLATE},
        ]
        with tempfile.TemporaryDirectory(prefix="agentd-wf05-failure-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            with patch(
                "agentd_workflow_snapshots.wf05_v1.agentd.workflows."
                "wf05_v01.MafftAdapter.align",
                side_effect=SnapshotToolUnavailableError("fixture dependency failure"),
            ):
                failed = service.run_wf05_variation(
                    payload_with_sequences(sequences)
                )
            report = service.get_wf05_report(failed["job_id"])
            delivery = service.get_wf05_delivery(failed["job_id"])
            child = service.retry_wf05_variation(
                failed["job_id"],
                {"reason": "dependency restored"},
            )

        Draft202012Validator(REPORT_SCHEMA).validate(report)
        self.assertEqual(failed["execution_status"], "FAILED")
        self.assertEqual(failed["decision"], "INCONCLUSIVE")
        self.assertEqual(failed["next_action"], "RETRY")
        self.assertEqual(len(failed["steps"]), 6)
        self.assertEqual(failed["steps"][1]["status"], "ERROR")
        self.assertTrue(failed["retry"]["allowed"])
        self.assertEqual(delivery["ui_message"]["type"], "primer_design_output")
        self.assertEqual(child["parent_job_id"], failed["job_id"])

        rendered = render_workflow_report_html(failed).decode("utf-8")
        visible = rendered.split(
            '<script type="application/json" id="workflow-report-data">',
            1,
        )[0]
        self.assertIn("本次分析未完成", visible)
        self.assertIn("恢复所需计算条件后可以重试", visible)
        self.assertNotIn("fixture dependency failure", visible)
        self.assertNotIn("TOOL_UNAVAILABLE", visible)
        self.assertNotIn("WF05_STEP", visible)

    def test_tool_snapshot_integrity_fails_closed_before_c0(self) -> None:
        sequences = [
            {"id": "reference", "sequence": TEMPLATE},
            {"id": "second", "sequence": TEMPLATE},
        ]
        with tempfile.TemporaryDirectory(prefix="agentd-wf05-integrity-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            with patch(
                "agentd.service.verify_wf05_existing_tool_snapshots",
                side_effect=RuntimeError("fixture integrity failure"),
            ), patch.object(
                service.wf05_catalog_v1,
                "enrich_payload",
            ) as catalog_call:
                with self.assertRaisesRegex(RuntimeError, "integrity failure"):
                    service.run(
                        "WF-05",
                        payload_with_sequences(sequences),
                        persist=False,
                    )
                catalog_call.assert_not_called()

    def test_record_count_resource_limit_is_enforced_before_execution(self) -> None:
        sequences = [
            {"id": f"record-{index}", "sequence": TEMPLATE}
            for index in range(WF05_MAX_SEQUENCE_RECORDS + 1)
        ]
        with tempfile.TemporaryDirectory(prefix="agentd-wf05-limit-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            with self.assertRaisesRegex(InputValidationError, "资源上限"):
                service.run(
                    "WF-05",
                    payload_with_sequences(sequences),
                    persist=False,
                )


if __name__ == "__main__":
    unittest.main()

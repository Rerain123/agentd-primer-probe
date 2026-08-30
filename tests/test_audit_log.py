from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.audit import AgentAuditLog
from agentd.errors import InputValidationError


class AgentAuditLogTests(unittest.TestCase):
    def test_tool_events_preserve_field_sources_and_redact_sensitive_data(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-audit-") as state_dir:
            audit = AgentAuditLog(state_dir)
            fasta = ">avian_1\n" + "ACGT" * 80
            trace_id = audit.start(
                "wf01_preflight",
                {
                    "payload": {
                        "fasta_text": fasta,
                        "authorization": "Bearer do-not-store",
                    },
                    "design_input_form": {
                        "data": {
                            "designInput": {
                                "targetOrganism": "Avian influenza virus",
                                "host": "鸡",
                                "coverageRequirement": "单一毒株",
                            }
                        }
                    },
                    "user_field_updates": {
                        "type": "primer_design_input_update",
                        "data": {
                            "designInput": {
                                "host": "鸡",
                                "coverageRequirement": "单一毒株",
                            }
                        },
                    },
                },
            )
            audit.complete(
                trace_id,
                "wf01_preflight",
                {
                    "preflight_status": "READY_TO_QUEUE",
                    "next_action": "CREATE_BATCH",
                    "normalised_input": {
                        "batch_defaults": {
                            "target_organism": {"name": "Avian influenza virus"},
                            "hosts": [{"name": "鸡"}],
                            "coverage_requirement": "单一毒株",
                        }
                    },
                    "mcp_input_mapping": {
                        "input_mode": "FORM_PLUS_USER_UPDATE",
                        "user_updated_fields": ["host", "coverageRequirement"],
                    },
                },
            )

            result = audit.read(operation="wf01_preflight")
            self.assertEqual(result["count"], 2)
            self.assertFalse(result["chain_of_thought_included"])
            started, completed = result["events"]
            fasta_summary = started["inputs"]["payload"]["fasta_text"]
            self.assertTrue(fasta_summary["redacted"])
            self.assertEqual(fasta_summary["record_count"], 1)
            self.assertEqual(
                started["inputs"]["payload"]["authorization"],
                "[REDACTED]",
            )
            self.assertEqual(
                started["inputs"]["user_field_updates"]["data"]["designInput"][
                    "coverageRequirement"
                ],
                "单一毒株",
            )
            self.assertEqual(
                completed["result"]["mcp_input_mapping"]["user_updated_fields"],
                ["host", "coverageRequirement"],
            )
            self.assertEqual(completed["output_source"], "tool_result")
            self.assertEqual(
                completed["output"]["mcp_input_mapping"]["user_updated_fields"],
                ["host", "coverageRequirement"],
            )
            self.assertNotIn(fasta, json.dumps(result, ensure_ascii=False))

    def test_completed_terminal_tool_records_exact_client_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-audit-") as state_dir:
            audit = AgentAuditLog(state_dir)
            card = {
                "data": {
                    "issues": [],
                    "status": "COMPLETED",
                    "attachments": [
                        {
                            "fileUrl": (
                                "https://rustfs.ne.scqcyz.cn/wens/data/"
                                "WF04-TEST_report.html"
                            ),
                            "fileName": "WF04-TEST_report.html",
                            "fileType": "HTML",
                            "mimeType": "text/html; charset=utf-8",
                            "attachmentId": "attachment_html",
                        }
                    ],
                    "reportTitle": "WF-04 终态报告",
                    "summaryText": "完整结果见 HTML 附件。",
                    "primaryAttachmentId": "attachment_html",
                },
                "type": "primer_design_output",
            }
            trace_id = audit.start(
                "wf04_get_report",
                {"job_id": "00000000-0000-4000-8000-000000000004"},
            )
            audit.complete(
                trace_id,
                "wf04_get_report",
                {
                    "workflow": "WF-04",
                    "job_id": "00000000-0000-4000-8000-000000000004",
                    "status": "SUCCEEDED",
                    "ui_message": card,
                    "internal_result": {"not_returned_to_client": True},
                },
            )

            completed = audit.read(trace_id=trace_id)["events"][-1]
            self.assertEqual(completed["event"], "TOOL_COMPLETED")
            self.assertEqual(completed["output_source"], "ui_message")
            self.assertEqual(completed["output"], card)
            self.assertNotIn("internal_result", completed["output"])

    def test_failed_tool_is_queryable_and_limit_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-audit-") as state_dir:
            audit = AgentAuditLog(state_dir)
            trace_id = audit.start("wf01_create_batch", {"batch_id": "batch-1"})
            audit.fail(
                trace_id,
                "wf01_create_batch",
                InputValidationError(
                    "缺少用户字段",
                    details={"next_action": "WAIT_FOR_USER"},
                ),
            )
            result = audit.read(trace_id=trace_id)
            self.assertEqual(result["events"][-1]["event"], "TOOL_FAILED")
            self.assertEqual(
                result["events"][-1]["error"]["details"]["next_action"],
                "WAIT_FOR_USER",
            )
            with self.assertRaises(InputValidationError):
                audit.read(limit=0)


if __name__ == "__main__":
    unittest.main()

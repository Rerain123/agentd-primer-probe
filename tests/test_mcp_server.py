from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import jwt
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class MCPServerTests(unittest.TestCase):
    secret = "test-only-secret-with-at-least-thirty-two-bytes"

    def _load_app(self, state_dir: str, *, public_artifacts: bool = False):
        settings = {
            "AGENTD_PROJECT_ROOT": str(ROOT),
            "AGENTD_STATE_DIR": state_dir,
            "MCP_AUTH_REQUIRED": "true",
            "MCP_JWT_SECRET": self.secret,
            "MCP_JWT_ALGORITHM": "HS256",
            "MCP_JWT_REQUIRE_EXP": "true",
            "MCP_PUBLIC_WF01_ARTIFACTS": (
                "true" if public_artifacts else "false"
            ),
            "MCP_PUBLIC_BASE_URL": "http://testserver",
        }
        with patch.dict(os.environ, settings, clear=False):
            sys.modules.pop("agentd.mcp_server", None)
            # api owns the module-level service, so it must be reloaded under
            # this test's isolated AGENTD_STATE_DIR as well.
            sys.modules.pop("agentd.api", None)
            module = importlib.import_module("agentd.mcp_server")
        return module.app

    def _token(self, subject: str = "mcp-test") -> str:
        now = datetime.now(timezone.utc)
        return jwt.encode(
            {"sub": subject, "iat": now, "exp": now + timedelta(minutes=5)},
            self.secret,
            algorithm="HS256",
        )

    @staticmethod
    def _mcp_headers(token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-11-25",
        }

    def test_health_is_public_and_mcp_requires_valid_jwt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="oligosmith-mcp-") as state_dir:
            app = self._load_app(state_dir)
            with TestClient(app) as client:
                self.assertEqual(client.get("/health").status_code, 200)
                missing = client.post("/mcp", json={})
                self.assertEqual(missing.status_code, 401)
                self.assertEqual(missing.headers["www-authenticate"], "Bearer")
                invalid = client.post(
                    "/mcp",
                    headers=self._mcp_headers("not-a-jwt"),
                    json={},
                )
                self.assertEqual(invalid.status_code, 401)

    def test_duplicate_design_input_call_returns_only_one_renderable_form(self) -> None:
        with tempfile.TemporaryDirectory(prefix="oligosmith-mcp-dedup-") as state_dir:
            app = self._load_app(state_dir)
            payload = {
                "designInput": {
                    "targetOrganism": "猪瘟病毒",
                    "host": "猪",
                    "coverageRequirement": "单一毒株",
                },
                "sourceFile": {
                    "fileName": "uploaded.fasta",
                    "fileType": "FASTA",
                },
            }

            def call_tool(
                client: TestClient,
                *,
                request_id: int,
                token: str,
                turn_id: str | None,
                file_names: list[str] | None = None,
            ):
                params = {
                    "name": "prepare_wf01_design_input",
                    "arguments": {
                        "payload": payload,
                        "file_names": file_names
                        or ["CSFV_NC_002657.1_5UTR_1-373.fasta"],
                    },
                }
                if turn_id is not None:
                    params["_meta"] = {"turnId": turn_id}
                return client.post(
                    "/mcp",
                    headers=self._mcp_headers(token),
                    json={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "tools/call",
                        "params": params,
                    },
                )

            with TestClient(app) as client:
                token = self._token("same-user")
                first = call_tool(
                    client,
                    request_id=1,
                    token=token,
                    turn_id="turn-1",
                )
                duplicate = call_tool(
                    client,
                    request_id=2,
                    token=token,
                    turn_id="turn-1",
                )
                next_turn = call_tool(
                    client,
                    request_id=3,
                    token=token,
                    turn_id="turn-2",
                )

                self.assertEqual(first.status_code, 200, first.text)
                self.assertEqual(duplicate.status_code, 200, duplicate.text)
                self.assertEqual(next_turn.status_code, 200, next_turn.text)
                first_result = first.json()["result"]
                duplicate_result = duplicate.json()["result"]
                next_turn_result = next_turn.json()["result"]
                self.assertEqual(
                    first_result["structuredContent"]["type"],
                    "primer_design_input",
                )
                self.assertEqual(
                    first_result["structuredContent"]["data"]["sourceFile"][
                        "fileName"
                    ],
                    "CSFV_NC_002657.1_5UTR_1-373.fasta",
                )
                self.assertEqual(
                    duplicate_result["structuredContent"]["status"],
                    "DUPLICATE_SUPPRESSED",
                )
                self.assertTrue(
                    duplicate_result["structuredContent"]["duplicate_suppressed"]
                )
                self.assertNotEqual(
                    duplicate_result["structuredContent"].get("type"),
                    "primer_design_input",
                )
                self.assertEqual(
                    next_turn_result["structuredContent"]["type"],
                    "primer_design_input",
                )

                multiple_files = call_tool(
                    client,
                    request_id=4,
                    token=token,
                    turn_id="turn-3",
                    file_names=["first.fasta", "second.fasta"],
                )
                self.assertEqual(
                    multiple_files.json()["result"]["structuredContent"]["data"][
                        "sourceFile"
                    ]["fileName"],
                    "多个文件",
                )

                fallback_first = call_tool(
                    client,
                    request_id=5,
                    token=self._token("fallback-user"),
                    turn_id=None,
                )
                fallback_duplicate = call_tool(
                    client,
                    request_id=6,
                    token=self._token("fallback-user"),
                    turn_id=None,
                )
                other_user = call_tool(
                    client,
                    request_id=7,
                    token=self._token("other-user"),
                    turn_id=None,
                )
                self.assertEqual(
                    fallback_first.json()["result"]["structuredContent"]["type"],
                    "primer_design_input",
                )
                self.assertEqual(
                    fallback_duplicate.json()["result"]["structuredContent"][
                        "status"
                    ],
                    "DUPLICATE_SUPPRESSED",
                )
                self.assertEqual(
                    other_user.json()["result"]["structuredContent"]["type"],
                    "primer_design_input",
                )

    def test_direct_sequence_survives_form_confirmation_without_recopy(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="oligosmith-inline-sequence-"
        ) as state_dir:
            app = self._load_app(state_dir)
            module = sys.modules["agentd.mcp_server"]
            headers = self._mcp_headers(self._token("inline-user"))
            sequence = "ACGT" * 60
            with TestClient(app) as client:
                prepared = client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 81,
                        "method": "tools/call",
                        "params": {
                            "name": "prepare_wf01_design_input",
                            "arguments": {
                                "payload": {
                                    "sequence": sequence,
                                    "designInput": {
                                        "targetOrganism": "猪瘟病毒（CSFV）",
                                        "host": "猪",
                                        "coverageRequirement": "单一毒株",
                                    },
                                }
                            },
                        },
                    },
                )
                self.assertEqual(prepared.status_code, 200, prepared.text)
                prepared_result = prepared.json()["result"]
                self.assertFalse(prepared_result["isError"], prepared.text)
                form = prepared_result["structuredContent"]
                source = form["data"]["sourceFile"]
                self.assertEqual(source["fileName"], "直接输入序列.fasta")
                self.assertEqual(source["inputMode"], "INLINE_SEQUENCE")
                self.assertRegex(
                    source["inputArtifactId"],
                    r"^himat-[0-9a-f]{32}$",
                )
                self.assertNotIn(sequence, prepared_result["content"][0]["text"])

                with patch.object(
                    module.service,
                    "run_wf01_v02_until_blocked",
                    side_effect=lambda batch_id: (
                        module.service.get_wf01_v02_agent_summary(batch_id)
                    ),
                ) as auto_run:
                    created = client.post(
                        "/mcp",
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": 83,
                            "method": "tools/call",
                            "params": {
                                "name": "create_wf01_batch",
                                "arguments": {
                                    "payload": {},
                                    "design_input_form": form,
                                    "user_field_updates": {
                                        "type": "primer_design_input_update",
                                        "data": {"designInput": {}},
                                    },
                                },
                            },
                        },
                    )
                self.assertEqual(auto_run.call_count, 1)
            self.assertEqual(created.status_code, 200, created.text)
            created_result = created.json()["result"]
            self.assertFalse(created_result["isError"], created.text)
            created_content = created_result["structuredContent"]
            self.assertEqual(created_content["status"], "QUEUED")
            self.assertEqual(
                created_content["target_manifest"]["targets"][0]["execution_path"],
                "FULL_SPECIFICITY",
            )
            self.assertRegex(
                created_content["batch_id"],
                r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            )
            self.assertIn(
                "sourceFile.inputArtifactId",
                created_content["mcp_input_mapping"]["mapped_aliases"],
            )

    def test_public_artifacts_exempt_only_read_only_delivery_routes(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="oligosmith-public-artifacts-"
        ) as state_dir:
            app = self._load_app(state_dir, public_artifacts=True)
            with TestClient(app) as client:
                public_gets = (
                    "/data/report.html",
                    "/data/raw-data.zip",
                    "/data/reports/batch-1/report.pdf",
                    "/v1/wf01/tasks/batch-1/report",
                    "/v1/wf01/tasks/batch-1/exports/html",
                    "/v1/wf01/tasks/batch-1/exports/html/preview",
                    "/v1/wf01/tasks/batch-1/exports/csv",
                    "/v1/wf01/tasks/batch-1/exports/pdf",
                    "/v1/wf01/tasks/batch-1/exports/scientific_data",
                    "/v1/wf01/tasks/batch-1/scientific-artifacts",
                    "/v1/wf01/tasks/batch-1/scientific-artifacts/artifact-1",
                )
                for path in public_gets:
                    response = client.get(path)
                    self.assertNotEqual(response.status_code, 401, path)

                self.assertEqual(client.post("/mcp", json={}).status_code, 401)
                self.assertEqual(
                    client.post("/v1/wf01/tasks", json={}).status_code,
                    401,
                )
                self.assertEqual(
                    client.post(
                        "/v1/wf01/tasks/batch-1/exports/html",
                        json={},
                    ).status_code,
                    401,
                )

    def test_export_tool_uses_persisted_public_rustfs_url(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="oligosmith-export-url-"
        ) as state_dir:
            self._load_app(state_dir, public_artifacts=True)
            module = sys.modules["agentd.mcp_server"]
            report_path = Path(state_dir) / "测试_引物探针设计报告.html"
            report_path.write_text("<html></html>", encoding="utf-8")
            expected = (
                "https://rustfs.ne.scqcyz.cn/wens/data/"
                "%E6%B5%8B%E8%AF%95_%E5%BC%95%E7%89%A9%E6%8E%A2%E9%92%88"
                "%E8%AE%BE%E8%AE%A1%E6%8A%A5%E5%91%8A.html"
            )
            output_payload = {
                "data": {
                    "status": "COMPLETED",
                    "reportTitle": "测试引物探针设计报告",
                    "summaryText": "已完成设计与报告生成。",
                    "primaryAttachmentId": "attachment_html",
                    "attachments": [
                        {
                            "attachmentId": "attachment_html",
                            "fileName": report_path.name,
                            "fileType": "HTML",
                            "mimeType": "text/html; charset=utf-8",
                            "fileUrl": expected,
                        }
                    ],
                    "issues": [],
                },
                "type": "primer_design_output",
            }
            with patch.object(
                module.service,
                "get_wf01_v02_export_path",
                return_value=report_path,
            ), patch.object(
                module.service,
                "get_wf01_v02_export_metadata",
                return_value={"file_url": expected},
            ), patch.object(
                module.service,
                "get_wf01_v02_agent_summary",
                return_value={"ui_message": output_payload},
            ):
                result = module.get_wf01_export("batch-1", "html")

            result_dump = result.model_dump(by_alias=True, exclude_none=True)
            self.assertEqual(result_dump["structuredContent"], output_payload)
            self.assertEqual(
                json.loads(result_dump["content"][0]["text"]),
                output_payload,
            )
            self.assertEqual(
                result_dump["content"][0]["text"],
                json.dumps(
                    output_payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )

    def test_completed_batch_query_replays_exact_report_card_json(self) -> None:
        with tempfile.TemporaryDirectory(prefix="oligosmith-query-ui-") as state_dir:
            app = self._load_app(state_dir)
            module = sys.modules["agentd.mcp_server"]
            output_payload = {
                "data": {
                    "status": "COMPLETED",
                    "reportTitle": "再次查看测试报告",
                    "summaryText": "任务已完成，可预览或下载附件。",
                    "primaryAttachmentId": "attachment_html",
                    "attachments": [
                        {
                            "attachmentId": "attachment_html",
                            "fileName": "report.html",
                            "fileType": "HTML",
                            "mimeType": "text/html; charset=utf-8",
                            "fileUrl": "/data/report.html",
                        }
                    ],
                    "issues": [],
                },
                "type": "primer_design_output",
            }
            summary = {
                "batch_id": "batch-completed",
                "status": "COMPLETED",
                "next_action": "COMPLETE",
                "ui_message": output_payload,
            }
            with patch.object(
                module.service,
                "get_wf01_v02_agent_summary",
                return_value=summary,
            ):
                with TestClient(app) as client:
                    response = client.post(
                        "/mcp",
                        headers=self._mcp_headers(self._token()),
                        json={
                            "jsonrpc": "2.0",
                            "id": 29,
                            "method": "tools/call",
                            "params": {
                                "name": "get_wf01_batch",
                                "arguments": {"batch_id": "batch-completed"},
                            },
                        },
                    )

            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()["result"]
            self.assertFalse(result["isError"], response.text)
            self.assertEqual(result["structuredContent"], output_payload)
            self.assertEqual(
                result["content"][0]["text"],
                json.dumps(
                    output_payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
            self.assertEqual(json.loads(result["content"][0]["text"]), output_payload)

    def test_final_report_popup_is_separate_from_route_text(self) -> None:
        with tempfile.TemporaryDirectory(prefix="oligosmith-final-ui-") as state_dir:
            app = self._load_app(state_dir)
            module = sys.modules["agentd.mcp_server"]
            final_payload = {
                "data": {
                    "status": "COMPLETED",
                    "reportTitle": "测试引物探针设计报告",
                    "summaryText": "已完成设计与报告生成。",
                    "primaryAttachmentId": "attachment_html",
                    "attachments": [
                        {
                            "attachmentId": "attachment_html",
                            "fileName": "report.html",
                            "fileType": "HTML",
                            "mimeType": "text/html; charset=utf-8",
                            "fileUrl": "/data/report.html",
                        }
                    ],
                    "issues": [],
                },
                "type": "primer_design_output",
            }
            control_summary = {
                "response_schema_version": "wf01-agent-view-v1",
                "response_mode": "SUMMARY",
                "batch_id": "batch-final",
                "status": "COMPLETED",
                "next_action": "COMPLETE",
                "route_message": "已按完整特异性路线完成。",
                "ui_message": final_payload,
                "targets": [
                    {
                        "target_id": "target-001",
                        "top_candidates": [
                            {
                                "candidate_id": f"candidate-{rank:03d}",
                                "rank": rank,
                                "score": 100 - rank,
                                "verdict": "PASS",
                                "evidence_level": "blast_verified",
                                "forward_primer": {
                                    "sequence_5to3": f"ACGTACGTACGTACGTA{rank:02d}"
                                },
                                "reverse_primer": {
                                    "sequence_5to3": f"TGCATGCATGCATGCA{rank:02d}"
                                },
                                "probe": {
                                    "sequence_5to3": f"GATTACAGATTACA{rank:02d}"
                                },
                                "amplicon": {"length_bp": 100 + rank},
                            }
                            for rank in range(1, 5)
                        ],
                    }
                ],
            }
            with patch.object(
                module.service,
                "run_wf01_v02_until_blocked",
                return_value=control_summary,
            ):
                direct_result = module.run_wf01_until_blocked("batch-final")
                direct_dump = direct_result.model_dump(
                    by_alias=True,
                    exclude_none=True,
                )
                self.assertEqual(direct_dump["structuredContent"], final_payload)
                self.assertEqual(len(direct_dump["content"]), 1)
                self.assertEqual(
                    json.loads(direct_dump["content"][0]["text"]),
                    final_payload,
                )
                with TestClient(app) as client:
                    response = client.post(
                        "/mcp",
                        headers=self._mcp_headers(self._token()),
                        json={
                            "jsonrpc": "2.0",
                            "id": 30,
                            "method": "tools/call",
                            "params": {
                                "name": "run_wf01_until_blocked",
                                "arguments": {"batch_id": "batch-final"},
                            },
                        },
                    )

            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()["result"]
            self.assertFalse(result["isError"], response.text)
            self.assertEqual(result["structuredContent"], final_payload)
            self.assertEqual(
                list(result["structuredContent"]),
                ["data", "type"],
            )
            self.assertNotIn("ui_message", result["structuredContent"])
            self.assertNotIn("blockId", result["structuredContent"])
            self.assertNotEqual(
                result["structuredContent"].get("type"),
                "block_data",
            )
            self.assertEqual(len(result["content"]), 1)
            self.assertEqual(result["content"][0]["type"], "text")
            output_text = result["content"][0]["text"]
            self.assertEqual(json.loads(output_text), final_payload)
            self.assertEqual(
                output_text,
                json.dumps(
                    final_payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )

    def test_create_batch_auto_runs_and_returns_final_ui_message(self) -> None:
        with tempfile.TemporaryDirectory(prefix="oligosmith-create-run-") as state_dir:
            app = self._load_app(state_dir)
            module = sys.modules["agentd.mcp_server"]
            final_payload = {
                "data": {
                    "status": "COMPLETED",
                    "reportTitle": "自动运行测试报告",
                    "summaryText": "创建后已经自动完成。",
                    "primaryAttachmentId": "attachment_html",
                    "attachments": [],
                    "issues": [],
                },
                "type": "primer_design_output",
            }

            def completed(batch_id: str) -> dict:
                return {
                    "batch_id": batch_id,
                    "status": "COMPLETED",
                    "next_action": "COMPLETE",
                    "route_message": (
                        "已按基础设计路线完成：Primer3、规则过滤、去重和候选排序；"
                        "因未提供靶标，未启动 BLAST+，特异性未评估。"
                    ),
                    "ui_message": final_payload,
                }

            service_module = sys.modules["agentd.service"]
            original_normalize = service_module.normalize_wf01_mcp_payload
            original_materialize = (
                module.service.input_artifacts.materialize_wf01_payload
            )
            with patch.object(
                service_module,
                "normalize_wf01_mcp_payload",
                wraps=original_normalize,
            ) as normalize_input, patch.object(
                module.service.input_artifacts,
                "materialize_wf01_payload",
                wraps=original_materialize,
            ) as materialize_input, patch.object(
                module.service,
                "run_wf01_v02_until_blocked",
                side_effect=completed,
            ) as auto_run:
                with TestClient(app) as client:
                    response = client.post(
                        "/mcp",
                        headers=self._mcp_headers(self._token()),
                        json={
                            "jsonrpc": "2.0",
                            "id": 32,
                            "method": "tools/call",
                            "params": {
                                "name": "create_wf01_batch",
                                "arguments": {
                                    "sequence": "ACGT" * 30
                                },
                            },
                        },
                    )

            normalize_input.assert_called_once()
            materialize_input.assert_called_once()
            auto_run.assert_called_once()
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()["result"]
            self.assertFalse(result["isError"], response.text)
            self.assertEqual(result["structuredContent"], final_payload)
            self.assertEqual(
                json.loads(result["content"][0]["text"]),
                final_payload,
            )

    def test_interaction_ui_message_is_mirrored_as_exact_bare_json(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="oligosmith-interaction-ui-"
        ) as state_dir:
            app = self._load_app(state_dir)
            module = sys.modules["agentd.mcp_server"]
            interaction_payload = {
                "type": "agent_interaction",
                "flowControl": {
                    "action": "WAIT_FOR_USER",
                    "stopCurrentTurn": True,
                    "resumeMessageType": "agent_interaction_response",
                },
                "interactionId": "interaction-1",
                "question": "目前只找到 0 组候选，下一步怎么处理？",
                "description": "请选择下一步处理方式。",
                "options": [
                    {
                        "value": "CONTINUE_SEARCH",
                        "label": "继续尝试",
                        "description": "扩大可设计范围后继续搜索。",
                    },
                    {
                        "value": "EXPORT_CURRENT_REPORT",
                        "label": "先输出报告",
                        "description": "停止搜索并生成当前阶段报告。",
                    },
                ],
            }
            control_summary = {
                "response_schema_version": "wf01-agent-view-v1",
                "response_mode": "SUMMARY",
                "batch_id": "batch-waiting",
                "status": "WAITING_USER",
                "next_action": "WAIT_FOR_USER",
                "ui_message": interaction_payload,
            }
            with patch.object(
                module.service,
                "run_wf01_v02_until_blocked",
                return_value=control_summary,
            ):
                with TestClient(app) as client:
                    response = client.post(
                        "/mcp",
                        headers=self._mcp_headers(self._token()),
                        json={
                            "jsonrpc": "2.0",
                            "id": 31,
                            "method": "tools/call",
                            "params": {
                                "name": "run_wf01_until_blocked",
                                "arguments": {"batch_id": "batch-waiting"},
                            },
                        },
                    )

            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()["result"]
            self.assertFalse(result["isError"], response.text)
            self.assertEqual(result["structuredContent"], interaction_payload)
            self.assertEqual(len(result["content"]), 1)
            self.assertEqual(result["content"][0]["type"], "text")
            mirrored = result["content"][0]["text"]
            self.assertEqual(json.loads(mirrored), interaction_payload)
            self.assertTrue(mirrored.startswith('{"type":"agent_interaction"'))
            self.assertNotIn("```", mirrored)
            self.assertNotIn('"structuredContent":', mirrored)
            self.assertNotIn('"content":', mirrored)

    def test_initialize_lists_focused_wf01_tools_and_routes_inputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="oligosmith-mcp-") as state_dir:
            app = self._load_app(state_dir)
            headers = self._mcp_headers(self._token())
            with TestClient(app) as client:
                initialized = client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-11-25",
                            "capabilities": {},
                            "clientInfo": {"name": "test-client", "version": "1.0"},
                        },
                    },
                )
                self.assertEqual(initialized.status_code, 200, initialized.text)
                self.assertEqual(initialized.json()["result"]["serverInfo"]["name"], "OligoSmith")
                instructions = initialized.json()["result"]["instructions"]
                self.assertLessEqual(len(instructions), 1200)
                for required_instruction in (
                    "引物候选必须来自工具",
                    "禁止自行生成",
                    "DESIGN_ONLY",
                    "FULL_SPECIFICITY",
                    "80 nt",
                    "0 候选",
                    "prepare_wf01_design_input",
                    "download_himat_file",
                    "create_wf01_batch",
                    "run_wf01_until_blocked",
                    "primer_design_output",
                    "紧凑裸 JSON",
                    "保留 batch_id",
                    "下载地址",
                    "get_wf01_report",
                    "create_wf01_version",
                ):
                    self.assertIn(required_instruction, instructions)

                listed = client.post(
                    "/mcp",
                    headers=headers,
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                )
                self.assertEqual(listed.status_code, 200, listed.text)
                tool_names = {item["name"] for item in listed.json()["result"]["tools"]}
                expected_tools = {
                    "prepare_wf01_design_input",
                    "download_himat_file",
                    "create_wf01_batch",
                    "get_wf01_batch",
                    "revise_wf01_target_manifest",
                    "submit_wf01_interaction_response",
                    "run_wf01_until_blocked",
                    "create_wf01_version",
                    "get_wf01_report",
                    "get_wf01_candidate",
                    "get_wf01_export",
                }
                self.assertEqual(tool_names, expected_tools)
                for hidden_tool in (
                    "get_service_status",
                    "get_capabilities",
                    "design_primers_with_primer3",
                    "get_agent_audit_log",
                    "run_wf02_multiplex_panel",
                    "run_wf03_internal_control",
                    "run_wf04_validate_assay",
                    "run_wf05_variation_tolerance",
                    "run_wf06_complete_panel",
                    "run_wf07_diagnose_assay",
                    "preflight_wf01_batch",
                    "get_wf01_scientific_artifacts",
                    "get_wf01_interactions",
                    "confirm_wf01_target_manifest",
                    "get_job",
                    "submit_remote_blast",
                    "poll_remote_blast",
                    "start_blast_review",
                    "poll_blast_review",
                ):
                    self.assertNotIn(hidden_tool, tool_names)
                for tool in listed.json()["result"]["tools"]:
                    self.assertTrue(
                        any("\u4e00" <= char <= "\u9fff" for char in tool["description"]),
                        f"工具描述必须包含中文：{tool['name']}",
                    )
                download_tool = next(
                    item
                    for item in listed.json()["result"]["tools"]
                    if item["name"] == "download_himat_file"
                )
                self.assertEqual(
                    download_tool["inputSchema"]["required"], ["file_id"]
                )
                self.assertEqual(
                    download_tool["inputSchema"]["properties"]["file_id"]["type"],
                    "integer",
                )
                resources = client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 20,
                        "method": "resources/list",
                        "params": {},
                    },
                )
                self.assertEqual(resources.status_code, 200, resources.text)
                for resource in resources.json()["result"]["resources"]:
                    description = resource.get("description") or ""
                    self.assertTrue(
                        any("\u4e00" <= char <= "\u9fff" for char in description),
                        f"资源描述必须包含中文：{resource.get('name')}",
                    )
                create_batch_tool = next(
                    item for item in listed.json()["result"]["tools"]
                    if item["name"] == "create_wf01_batch"
                )
                self.assertEqual(
                    set(create_batch_tool["inputSchema"].get("required", [])),
                    set(),
                )
                self.assertIn(
                    "sequence",
                    create_batch_tool["inputSchema"]["properties"],
                )
                self.assertIn(
                    "target_organism",
                    create_batch_tool["inputSchema"]["properties"],
                )
                self.assertIn(
                    "host",
                    create_batch_tool["inputSchema"]["properties"],
                )
                prepare_tool = next(
                    item for item in listed.json()["result"]["tools"]
                    if item["name"] == "prepare_wf01_design_input"
                )
                self.assertEqual(
                    set(prepare_tool["inputSchema"]["required"]),
                    {"payload"},
                )
                file_names_schema = prepare_tool["inputSchema"]["properties"][
                    "file_names"
                ]
                array_schema = next(
                    (
                        branch
                        for branch in file_names_schema.get("anyOf", [])
                        if branch.get("type") == "array"
                    ),
                    file_names_schema,
                )
                self.assertEqual(array_schema["minItems"], 1)
                self.assertIn("文件上传兼容链路", prepare_tool["description"])
                self.assertIn("create_wf01_batch", prepare_tool["description"])

                run_until_blocked_tool = next(
                    item for item in listed.json()["result"]["tools"]
                    if item["name"] == "run_wf01_until_blocked"
                )
                self.assertIn("仅用于问题修正", run_until_blocked_tool["description"])
                self.assertIn("报告生成完成", run_until_blocked_tool["description"])
                self.assertIn("每个恢复轮次最多调用一次", run_until_blocked_tool["description"])
                self.assertIn(
                    "WAIT_FOR_STAGE_COMPLETION",
                    run_until_blocked_tool["description"],
                )

                report_tool = next(
                    item for item in listed.json()["result"]["tools"]
                    if item["name"] == "get_wf01_report"
                )
                self.assertIn(
                    "跟进请求",
                    report_tool["description"],
                )
                self.assertIn(
                    "排名前 10",
                    report_tool["description"],
                )

                create_tool = next(
                    item for item in listed.json()["result"]["tools"]
                    if item["name"] == "create_wf01_batch"
                )
                self.assertIn("唯一入口", create_tool["description"])
                self.assertIn("后端自动选择", create_tool["description"])
                self.assertIn("运行到报告", create_tool["description"])

                module = sys.modules["agentd.mcp_server"]
                with patch.object(
                    module.service,
                    "run_wf01_v02_until_blocked",
                    side_effect=lambda batch_id: (
                        module.service.get_wf01_v02_agent_summary(batch_id)
                    ),
                ) as direct_auto_run:
                    direct_sequence = client.post(
                        "/mcp",
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": 21,
                            "method": "tools/call",
                            "params": {
                                "name": "create_wf01_batch",
                                "arguments": {
                                    "sequence": "ACGT" * 30,
                                }
                            },
                        },
                    )
                direct_auto_run.assert_called_once()
                self.assertEqual(direct_sequence.status_code, 200, direct_sequence.text)
                direct_content = direct_sequence.json()["result"]["structuredContent"]
                self.assertEqual(
                    direct_content["target_manifest"]["targets"][0][
                        "execution_path"
                    ],
                    "DESIGN_ONLY",
                )

                with patch.object(
                    module.service,
                    "run_wf01_v02_until_blocked",
                    side_effect=lambda batch_id: (
                        module.service.get_wf01_v02_agent_summary(batch_id)
                    ),
                ) as full_auto_run:
                    full_context = client.post(
                        "/mcp",
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": 22,
                            "method": "tools/call",
                            "params": {
                                "name": "create_wf01_batch",
                                "arguments": {
                                    "sequence": "TGCA" * 30,
                                    "target_organism": "猪瘟病毒",
                                    "host": "猪",
                                }
                            },
                        },
                    )
                full_auto_run.assert_called_once()
                self.assertEqual(full_context.status_code, 200, full_context.text)
                full_content = full_context.json()["result"]["structuredContent"]
                self.assertEqual(
                    full_content["target_manifest"]["targets"][0][
                        "execution_path"
                    ],
                    "FULL_SPECIFICITY",
                )

                with patch.object(
                    module.service,
                    "run_wf01_v02_until_blocked",
                ) as blocked_auto_run:
                    blocked = client.post(
                        "/mcp",
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": 23,
                            "method": "tools/call",
                            "params": {
                                "name": "create_wf01_batch",
                                "arguments": {
                                    "sequence": "ACGT" * 10
                                },
                            },
                        },
                    )
                blocked_auto_run.assert_not_called()
                self.assertEqual(blocked.status_code, 200, blocked.text)
                blocked_result = blocked.json()["result"]
                self.assertTrue(blocked_result["isError"], blocked.text)
                self.assertNotIn("structuredContent", blocked_result)
                error_text = blocked_result["content"][0]["text"]
                self.assertIn('"code": "INVALID_INPUT"', error_text)
                self.assertIn('"minimum_template_length_nt": 80', error_text)
                self.assertIn('"show_popup": false', error_text)

                run_until_blocked_tool = next(
                    item for item in listed.json()["result"]["tools"]
                    if item["name"] == "run_wf01_until_blocked"
                )
                self.assertIn("content[0].text", run_until_blocked_tool["description"])
                self.assertIn("实际路线", run_until_blocked_tool["description"])
                self.assertIn("structuredContent", run_until_blocked_tool["description"])
                get_batch_tool = next(
                    item for item in listed.json()["result"]["tools"]
                    if item["name"] == "get_wf01_batch"
                )
                self.assertIn("查看、下载、下载地址", get_batch_tool["description"])
                self.assertIn("裸 JSON 原样", get_batch_tool["description"])
                submit_interaction_tool = next(
                    item for item in listed.json()["result"]["tools"]
                    if item["name"] == "submit_wf01_interaction_response"
                )
                self.assertIn("完整 role=user agent_interaction_response", submit_interaction_tool["description"])
                self.assertIn("next_action", submit_interaction_tool["description"])
                self.assertIn("content[0].text", submit_interaction_tool["description"])
                self.assertIn("structuredContent", submit_interaction_tool["description"])

                total_description_chars = sum(
                    len(item["description"])
                    for item in listed.json()["result"]["tools"]
                )
                self.assertLessEqual(total_description_chars, 1400)

                prepared = client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 31,
                        "method": "tools/call",
                        "params": {
                            "name": "prepare_wf01_design_input",
                            "arguments": {
                                "file_names": [
                                    "CSFV_NC_002657.1_5UTR_1-373.fasta"
                                ],
                                "payload": {
                                    "designInput": {
                                        "targetOrganism": "猪瘟病毒（CSFV）",
                                        "host": "鸭",
                                        "coverageRequirement": "覆盖多个分型/毒株",
                                    },
                                    "sourceFile": {
                                        "fileName": "uploaded.fasta",
                                        "fileType": "FASTA",
                                    },
                                }
                            },
                        },
                    },
                )
                self.assertEqual(prepared.status_code, 200, prepared.text)
                prepared_result = prepared.json()["result"]
                self.assertFalse(prepared_result["isError"], prepared.text)
                prepared_content = prepared_result["structuredContent"]
                self.assertEqual(set(prepared_content), {"type", "data"})
                self.assertEqual(prepared_content["type"], "primer_design_input")
                self.assertEqual(len(prepared_result["content"]), 1)
                self.assertEqual(prepared_result["content"][0]["type"], "text")
                self.assertEqual(
                    json.loads(prepared_result["content"][0]["text"]),
                    prepared_content,
                )
                self.assertNotIn(
                    "ACGT",
                    prepared_result["content"][0]["text"],
                )
                self.assertEqual(
                    prepared_content["data"]["designInput"],
                    {
                        "targetOrganism": "猪瘟病毒（CSFV）",
                        "host": "鸭",
                        "coverageRequirement": "覆盖多个分型/毒株",
                    },
                )
                self.assertEqual(
                    prepared_content["data"]["sourceFile"]["fileName"],
                    "CSFV_NC_002657.1_5UTR_1-373.fasta",
                )

                with patch.object(
                    module.service,
                    "run_wf01_v02_until_blocked",
                    side_effect=lambda batch_id: (
                        module.service.get_wf01_v02_agent_summary(batch_id)
                    ),
                ) as form_auto_run:
                    created_from_form = client.post(
                        "/mcp",
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": 4,
                            "method": "tools/call",
                            "params": {
                                "name": "create_wf01_batch",
                                "arguments": {
                                    "payload": {
                                        "target_object": {"normalized_name": "旧值"},
                                        "host_objects": [{"normalized_name": "猪"}],
                                        "fasta_text": ">CSFV_01\n" + "ACGT" * 60,
                                    },
                                    "design_input_form": {
                                        "type": "primer_design_input",
                                        "data": {
                                            "status": "WAITING_USER",
                                            "confirmed": False,
                                            "canStart": True,
                                            "summaryText": "猪瘟病毒（CSFV）·宿主：鸭·覆盖多个分型/毒株",
                                            "sourceFile": {
                                                "fileName": "CSFV_5UTR.fasta",
                                                "fileType": "FASTA",
                                            },
                                            "designInput": {
                                                "targetOrganism": "猪瘟病毒（CSFV）",
                                                "host": "猪",
                                                "coverageRequirement": "覆盖多个分型/毒株",
                                            },
                                            "issues": [],
                                        },
                                    },
                                    "user_field_updates": {
                                        "type": "primer_design_input_update",
                                        "data": {"designInput": {"host": "鸭"}},
                                    },
                                },
                            },
                        },
                    )
                form_auto_run.assert_called_once()
                self.assertEqual(created_from_form.status_code, 200, created_from_form.text)
                created_from_form_result = created_from_form.json()["result"]
                self.assertFalse(created_from_form_result["isError"], created_from_form.text)
                content = created_from_form_result["structuredContent"]
                self.assertEqual(content["status"], "QUEUED")
                self.assertEqual(
                    content["target_manifest"]["targets"][0]["execution_path"],
                    "FULL_SPECIFICITY",
                )
                self.assertTrue(
                    content["mcp_input_mapping"]["do_not_retry_with_alternate_field_names"]
                )

                contract = client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 5,
                        "method": "resources/read",
                        "params": {"uri": "oligosmith://contracts/primer-design-input"},
                    },
                )
                self.assertEqual(contract.status_code, 200, contract.text)
                contract_text = contract.json()["result"]["contents"][0]["text"]
                self.assertIn('"WAITING_USER"', contract_text)
                self.assertIn('"targetOrganism"', contract_text)

                update_contract = client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 6,
                        "method": "resources/read",
                        "params": {
                            "uri": "oligosmith://contracts/primer-design-input-update"
                        },
                    },
                )
                self.assertEqual(update_contract.status_code, 200, update_contract.text)
                update_text = update_contract.json()["result"]["contents"][0]["text"]
                self.assertIn('"primer_design_input_update"', update_text)

if __name__ == "__main__":
    unittest.main()

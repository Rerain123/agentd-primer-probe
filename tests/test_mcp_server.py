from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import call, patch

import jwt
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.attachment_urls import build_wf02_attachment_url


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

    def _assert_direct_himat_card(
        self,
        result: dict,
        payload: dict,
    ) -> None:
        self.assertEqual(result["structuredContent"], payload)
        self.assertNotIn("_meta", result)
        self.assertEqual(len(result["content"]), 1)
        self.assertEqual(result["content"][0]["type"], "text")
        text = result["content"][0]["text"]
        assistant_payload = json.loads(text)
        self.assertEqual(assistant_payload, payload)
        self.assertEqual(list(assistant_payload), ["data", "type"])
        self.assertNotIn("content", assistant_payload)
        self.assertNotIn("structuredContent", assistant_payload)
        self.assertNotIn("isError", assistant_payload)
        self.assertTrue(text.startswith('{"data":'))
        self.assertTrue(text.endswith('"type":"primer_design_output"}'))
        self.assertNotIn("```", text)
        self.assertNotIn("https://", text)
        self.assertIn("\\u002f", text)
        self.assertNotIn("[card]", text)

    def _assert_direct_himat_wire_card(
        self,
        result: dict,
        payload: dict | None = None,
    ) -> None:
        if payload is not None:
            self.assertEqual(result, payload)
        self.assertEqual(list(result), ["data", "type"])
        self.assertEqual(result["type"], "primer_design_output")
        self.assertIsInstance(result["data"], dict)
        self.assertFalse(
            {"content", "structuredContent", "isError"} & set(result)
        )

    def test_terminal_card_rejects_fields_outside_the_contract(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="oligosmith-card-schema-"
        ) as state_dir:
            self._load_app(state_dir)
            module = sys.modules["agentd.mcp_server"]
            invalid_payload = {
                "data": {
                    "status": "COMPLETED",
                    "reportTitle": "schema test",
                    "summaryText": "completed",
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
                    "unexpected": True,
                },
                "type": "primer_design_output",
            }
            with self.assertRaisesRegex(RuntimeError, "不符合"):
                module._host_ui_result({"ui_message": invalid_payload})

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
                report_path = (
                    "/v1/wf02/tasks/00000000-0000-0000-0000-000000000001/report"
                )
                self.assertEqual(client.get(report_path).status_code, 401)
                authorised = client.get(
                    report_path,
                    headers={"Authorization": f"Bearer {self._token()}"},
                )
                self.assertEqual(authorised.status_code, 404)

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
                    client.post("/v1/wf02/tasks", json={}).status_code,
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
            self._assert_direct_himat_card(result_dump, output_payload)

    def test_terminal_card_is_returned_as_direct_mcp_json_for_every_workflow(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="oligosmith-terminal-links-"
        ) as state_dir:
            self._load_app(state_dir)
            module = sys.modules["agentd.mcp_server"]
            attachments = [
                {
                    "attachmentId": "attachment_html",
                    "fileName": "2026-08-25_19-27-57_主报告.html",
                    "fileType": "HTML",
                    "mimeType": "text/html; charset=utf-8",
                    "fileUrl": (
                        "https://rustfs.ne.scqcyz.cn/wens/data/"
                        "2026-08-25_19-27-57_主报告.html"
                    ),
                },
                {
                    "attachmentId": "attachment_process_html",
                    "fileName": "2026-08-25_19-27-57_过程报告.html",
                    "fileType": "HTML",
                    "mimeType": "text/html; charset=utf-8",
                    "fileUrl": (
                        "https://rustfs.ne.scqcyz.cn/wens/data/"
                        "2026-08-25_19-27-57_过程报告.html"
                    ),
                },
                {
                    "attachmentId": "attachment_scientific_data",
                    "fileName": "2026-08-25_19-27-57_数据包.zip",
                    "fileType": "ZIP",
                    "mimeType": "application/zip",
                    "fileUrl": (
                        "https://rustfs.ne.scqcyz.cn/wens/data/"
                        "2026-08-25_19-27-57_数据包.zip"
                    ),
                },
            ]
            payload = {
                "data": {
                    "status": "COMPLETED",
                    "reportTitle": "terminal [card](https://example.invalid)",
                    "summaryText": "completed <script> & escaped",
                    "primaryAttachmentId": "attachment_html",
                    "attachments": attachments,
                    "issues": [],
                },
                "type": "primer_design_output",
            }

            for workflow in ("WF-01", "WF-02", "WF-03", "WF-04", "WF-05"):
                with self.subTest(workflow=workflow):
                    source = {
                        "workflow": workflow,
                        "batch_id": f"batch-{workflow}",
                        "batch_code": f"{workflow.replace('-', '')}-TEST",
                        "ui_message": payload,
                    }
                    if workflow in {"WF-03", "WF-04", "WF-05"}:
                        source["job_id"] = f"job-{workflow}"
                    result = module._host_ui_result(source).model_dump(
                        by_alias=True,
                        exclude_none=True,
                    )

                    self._assert_direct_himat_card(result, payload)

    def test_wf03_and_wf04_report_tools_return_direct_himat_cards(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="oligosmith-terminal-workflows-"
        ) as state_dir:
            app = self._load_app(state_dir)
            module = sys.modules["agentd.mcp_server"]
            payload = {
                "data": {
                    "status": "COMPLETED",
                    "reportTitle": "terminal report",
                    "summaryText": "completed",
                    "primaryAttachmentId": "attachment_html",
                    "attachments": [
                        {
                            "attachmentId": "attachment_html",
                            "fileName": "terminal_report.html",
                            "fileType": "HTML",
                            "mimeType": "text/html; charset=utf-8",
                            "fileUrl": (
                                "https://rustfs.ne.scqcyz.cn/wens/data/"
                                "terminal_report.html"
                            ),
                        }
                    ],
                    "issues": [],
                },
                "type": "primer_design_output",
            }
            headers = self._mcp_headers(self._token("terminal-workflows"))
            cases = (
                ("WF-03", "get_wf03_report", "get_wf03_delivery"),
                ("WF-04", "get_wf04_report", "get_wf04_delivery"),
                ("WF-05", "get_wf05_report", "get_wf05_delivery"),
            )
            with TestClient(app) as client:
                for index, (workflow, tool_name, service_method) in enumerate(
                    cases,
                    start=1,
                ):
                    delivery = {
                        "workflow": workflow,
                        "job_id": f"job-{index}",
                        "next_action": "END",
                        "ui_message": payload,
                    }
                    with self.subTest(workflow=workflow), patch.object(
                        module.service,
                        service_method,
                        return_value=delivery,
                    ):
                        response = client.post(
                            "/mcp",
                            headers=headers,
                            json={
                                "jsonrpc": "2.0",
                                "id": 400 + index,
                                "method": "tools/call",
                                "params": {
                                    "name": tool_name,
                                    "arguments": {"job_id": f"job-{index}"},
                                },
                            },
                        )
                        self.assertEqual(response.status_code, 200, response.text)
                        result = response.json()["result"]
                        self._assert_direct_himat_wire_card(result, payload)

    def test_completed_batch_query_keeps_himat_card_json(self) -> None:
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
            self._assert_direct_himat_wire_card(result, output_payload)

    def test_completed_batch_query_accepts_user_facing_task_code(self) -> None:
        with tempfile.TemporaryDirectory(prefix="oligosmith-query-code-") as state_dir:
            app = self._load_app(state_dir)
            module = sys.modules["agentd.mcp_server"]
            batch_id = "5f5a24e9-3846-4883-ac7f-b9b28d01fa07"
            batch_code = "WF01-260824-0940-5F5A"
            module.service.wf01_v02.store.save(
                {
                    "batch_id": batch_id,
                    "batch_code": batch_code,
                    "workflow": "WF-01",
                    "created_at": "2026-08-24T01:40:00+00:00",
                    "status": "SUCCEEDED",
                    "m6_outcome": "COMPLETED",
                    "candidate_counts": {"deliverable": 3},
                    "targets": [],
                    "agent_interactions": [],
                    "m6_execution": {
                        "exports": {
                            "html": {
                                "filename": "report.html",
                                "media_type": "text/html; charset=utf-8",
                                "file_url": "/data/report.html",
                            }
                        },
                        "export_failures": {},
                    },
                }
            )
            module.service.wf01_v02.store.save_export(
                batch_id,
                "html",
                b"<html></html>",
                filename="report.html",
            )

            with TestClient(app) as client:
                response = client.post(
                    "/mcp",
                    headers=self._mcp_headers(self._token()),
                    json={
                        "jsonrpc": "2.0",
                        "id": 291,
                        "method": "tools/call",
                        "params": {
                            "name": "get_wf01_batch",
                            "arguments": {"batch_id": batch_code},
                        },
                    },
                )
                export_response = client.post(
                    "/mcp",
                    headers=self._mcp_headers(self._token()),
                    json={
                        "jsonrpc": "2.0",
                        "id": 292,
                        "method": "tools/call",
                        "params": {
                            "name": "get_wf01_export",
                            "arguments": {
                                "batch_id": batch_code,
                                "format_name": "html",
                            },
                        },
                    },
                )

            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()["result"]
            self._assert_direct_himat_wire_card(result)

            self.assertEqual(export_response.status_code, 200, export_response.text)
            export_result = export_response.json()["result"]
            self._assert_direct_himat_wire_card(export_result)

    def test_wf02_completed_card_and_exports_are_delivered_behind_jwt(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="oligosmith-wf02-delivery-"
        ) as state_dir:
            # WF-01 artifacts may be public in production, but WF-02 report
            # data and downloads deliberately remain behind the bearer token.
            app = self._load_app(state_dir, public_artifacts=True)
            module = sys.modules["agentd.mcp_server"]
            batch_id = "11111111-2222-4333-8444-555555555555"
            export_payloads = {
                "html": b"<!doctype html><title>WF-02 report</title>",
                "csv": b"\xef\xbb\xbfpanel_id,score\npanel-1,0.95\n",
                "pdf": b"%PDF-1.7\n% WF-02 report\n",
            }
            export_paths: dict[str, Path] = {}
            export_metadata: dict[str, dict[str, object]] = {}
            attachments: list[dict[str, object]] = []
            media_types = {
                "html": "text/html; charset=utf-8",
                "csv": "text/csv; charset=utf-8",
                "pdf": "application/pdf",
            }
            for format_name, content in export_payloads.items():
                path = Path(state_dir) / f"wf02-multiplex-report.{format_name}"
                path.write_bytes(content)
                export_paths[format_name] = path
                download_url = (
                    f"/v1/wf02/tasks/{batch_id}/exports/{format_name}"
                )
                export_metadata[format_name] = {
                    "format": format_name,
                    "filename": path.name,
                    "size_bytes": len(content),
                    "download_url": download_url,
                    "preview_url": (
                        f"/v1/wf02/tasks/{batch_id}/exports/html/preview"
                        if format_name == "html"
                        else None
                    ),
                }
                attachments.append(
                    {
                        "attachmentId": f"attachment_{format_name}",
                        "fileName": path.name,
                        "fileType": format_name.upper(),
                        "mimeType": media_types[format_name],
                        "fileUrl": download_url,
                    }
                )

            output_payload = {
                "data": {
                    "status": "COMPLETED",
                    "reportTitle": "WF-02 普通多重 qPCR 面板报告",
                    "summaryText": "已完成 N31/N32，可预览或下载报告附件。",
                    "primaryAttachmentId": "attachment_html",
                    "attachments": [
                        item
                        for item in attachments
                        if item["fileType"] == "HTML"
                    ],
                    "issues": [],
                },
                "type": "primer_design_output",
            }
            pdf_output_payload = {
                "data": {
                    **output_payload["data"],
                    "primaryAttachmentId": "attachment_pdf",
                    "attachments": [
                        item
                        for item in attachments
                        if item["fileType"] == "PDF"
                    ],
                },
                "type": "primer_design_output",
            }
            summary = {
                "response_schema_version": "wf02-v0.1-agent-summary-v1",
                "workflow": "WF-02",
                "batch_id": batch_id,
                "status": "COMPLETED",
                "stage": "N32",
                "next_action": "COMPLETE",
                "ui_message": output_payload,
            }
            report = {
                "schema_version": "wf02-v0.1-report-v1",
                "source": {"batch_id": batch_id},
                "top3_panels": [],
                "exports": export_metadata,
            }

            def get_export_path(requested_batch_id: str, format_name: str) -> Path:
                self.assertEqual(requested_batch_id, batch_id)
                return export_paths[format_name]

            def get_export_metadata(
                requested_batch_id: str,
                format_name: str,
            ) -> dict[str, object]:
                self.assertEqual(requested_batch_id, batch_id)
                return export_metadata[format_name]

            def get_agent_summary(
                requested_batch_id: str,
                *,
                attachment_formats: tuple[str, ...] = ("html",),
            ) -> dict[str, object]:
                self.assertEqual(requested_batch_id, batch_id)
                selected_message = (
                    pdf_output_payload
                    if attachment_formats == ("pdf",)
                    else output_payload
                )
                return {**summary, "ui_message": selected_message}

            with (
                patch.object(
                    module.service,
                    "get_wf02_v01_agent_summary",
                    side_effect=get_agent_summary,
                ),
                patch.object(
                    module.service,
                    "get_wf02_v01_report",
                    return_value=report,
                ),
                patch.object(
                    module.service,
                    "get_wf02_v01_export_path",
                    side_effect=get_export_path,
                ),
                patch.object(
                    module.service,
                    "get_wf02_v01_export_metadata",
                    side_effect=get_export_metadata,
                ),
            ):
                token = self._token("wf02-delivery-user")
                headers = self._mcp_headers(token)
                download_headers = {"Authorization": f"Bearer {token}"}
                with TestClient(app) as client:
                    completed = client.post(
                        "/mcp",
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": 301,
                            "method": "tools/call",
                            "params": {
                                "name": "get_wf02_batch",
                                "arguments": {"batch_id": batch_id},
                            },
                        },
                    )
                    self.assertEqual(completed.status_code, 200, completed.text)
                    completed_result = completed.json()["result"]
                    self._assert_direct_himat_wire_card(
                        completed_result,
                        output_payload,
                    )

                    export_card = client.post(
                        "/mcp",
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": 302,
                            "method": "tools/call",
                            "params": {
                                "name": "get_wf02_export",
                                "arguments": {
                                    "batch_id": batch_id,
                                    "format_name": "pdf",
                                },
                            },
                        },
                    )
                    self.assertEqual(export_card.status_code, 200, export_card.text)
                    export_result = export_card.json()["result"]
                    self._assert_direct_himat_wire_card(
                        export_result,
                        pdf_output_payload,
                    )

                    report_url = f"/v1/wf02/tasks/{batch_id}/report"
                    self.assertEqual(client.get(report_url).status_code, 401)
                    report_response = client.get(
                        report_url,
                        headers=download_headers,
                    )
                    self.assertEqual(report_response.status_code, 200)
                    self.assertEqual(report_response.json(), report)

                    for format_name, expected_content in export_payloads.items():
                        download_url = str(
                            export_metadata[format_name]["download_url"]
                        )
                        self.assertEqual(
                            client.get(download_url).status_code,
                            401,
                            download_url,
                        )
                        downloaded = client.get(
                            download_url,
                            headers=download_headers,
                        )
                        self.assertEqual(downloaded.status_code, 200)
                        self.assertEqual(downloaded.content, expected_content)
                        self.assertEqual(
                            downloaded.headers["cache-control"],
                            "private, no-store",
                        )
                        self.assertEqual(
                            downloaded.headers["x-content-type-options"],
                            "nosniff",
                        )
                        self.assertIn(
                            "attachment",
                            downloaded.headers["content-disposition"],
                        )

                    preview_url = (
                        f"/v1/wf02/tasks/{batch_id}/exports/html/preview"
                    )
                    self.assertEqual(client.get(preview_url).status_code, 401)
                    preview = client.get(preview_url, headers=download_headers)
                    self.assertEqual(preview.status_code, 200)
                    self.assertEqual(preview.content, export_payloads["html"])
                    self.assertEqual(
                        preview.headers["content-disposition"],
                        "inline",
                    )

    def test_wf02_signed_attachment_url_allows_only_exact_read(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="oligosmith-wf02-signed-delivery-"
        ) as state_dir:
            app = self._load_app(state_dir, public_artifacts=True)
            module = sys.modules["agentd.mcp_server"]
            batch_id = "11111111-2222-4333-8444-555555555555"
            export_path = Path(state_dir) / "wf02-multiplex-report.pdf"
            export_path.write_bytes(b"%PDF-1.7\n% signed fixture\n")
            with patch.dict(
                os.environ,
                {
                    "MCP_PUBLIC_BASE_URL": "http://testserver",
                    "MCP_AUTH_REQUIRED": "true",
                    "MCP_JWT_SECRET": self.secret,
                    "WF02_ATTACHMENT_URL_TTL_SECONDS": "600",
                },
                clear=False,
            ):
                signed_url = build_wf02_attachment_url(batch_id, "pdf")

            with patch.object(
                module.service,
                "get_wf02_v01_export_path",
                return_value=export_path,
            ):
                with TestClient(app) as client:
                    downloaded = client.get(signed_url)
                    self.assertEqual(downloaded.status_code, 200)
                    self.assertEqual(downloaded.content, export_path.read_bytes())
                    self.assertEqual(client.head(signed_url).status_code, 200)
                    self.assertEqual(
                        client.get(
                            signed_url.replace("/exports/pdf", "/exports/csv")
                        ).status_code,
                        401,
                    )
                    self.assertEqual(
                        client.get(f"{signed_url}&extra=1").status_code,
                        401,
                    )
                    self.assertEqual(client.post(signed_url).status_code, 401)

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
                "batch_code": "WF01-260824-0940-5F5A",
                "workflow": "WF-01",
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
                self._assert_direct_himat_card(direct_dump, final_payload)
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
            self._assert_direct_himat_wire_card(result, final_payload)
            self.assertNotIn("ui_message", result)
            self.assertNotIn("blockId", result)

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
                                    "sequence": "ACGT" * 30,
                                    "target_organism": "猪瘟病毒",
                                },
                            },
                        },
                    )

            normalize_input.assert_called_once()
            materialize_input.assert_called_once()
            auto_run.assert_called_once()
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()["result"]
            self._assert_direct_himat_wire_card(result, final_payload)

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
                self.assertLessEqual(len(instructions), 1400)
                for required_instruction in (
                    "候选与结论均采用工具结果",
                    "FULL_SPECIFICITY",
                    "完整路线",
                    "缺少 target_organism",
                    "download_himat_file",
                    "create_wf01_batch",
                    "run_wf01_until_blocked",
                    "只有用户后续明确要求",
                    "保留 batch_id",
                    "下载地址",
                    "get_wf01_report",
                    "create_wf01_version",
                    "create_wf02_batch",
                    "复用对话已有的靶标、文件映射和宿主",
                    "downstream.wf02.target",
                    "由 N04 返回 agent_interaction",
                    "省略 include_internal_control",
                    "省略 runtime_config",
                    "submit_wf02_input_response",
                    "get_wf02_batch",
                    "run_wf02_until_blocked",
                    "后端持久化任务执行",
                    "首次最多同步等待235秒",
                    "WAIT_FOR_BACKGROUND",
                    "只回复user_message",
                    "不轮询",
                    "不重复输出阶段文字",
                    "请求失败不得同轮重试",
                    "submit_wf02_child_response",
                    "run_wf03_internal_control",
                    "有附件/file_id时先 download_himat_file",
                    "取顶层 input_artifact_id",
                    "无附件且 host_species已知时直接调用",
                    "禁止先分析或用文本模拟结果",
                    "缺少 host_species 时只询问该字段并停止",
                    "P5 采用后端结果",
                    "retry_wf03_internal_control",
                    "get_wf03_report",
                    "run_wf04_validation",
                    "retryable_steps",
                    "retry_wf04_validation",
                    "get_wf04_report",
                    "run_wf05_variation",
                    "retry_wf05_variation",
                    "get_wf05_report",
                    "执行状态和最终判定是两个独立字段",
                ):
                    self.assertIn(required_instruction, instructions)
                self.assertIn("终态结果已由工具直接交付", instructions)
                self.assertIn("不得生成助手文本", instructions)
                self.assertIn("不得再次调用工具", instructions)
                self.assertNotIn("prepare_wf01_design_input", instructions)

                listed = client.post(
                    "/mcp",
                    headers=headers,
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                )
                self.assertEqual(listed.status_code, 200, listed.text)
                tool_names = {item["name"] for item in listed.json()["result"]["tools"]}
                expected_tools = {
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
                    "run_wf03_internal_control",
                    "retry_wf03_internal_control",
                    "get_wf03_report",
                    "run_wf04_validation",
                    "retry_wf04_validation",
                    "get_wf04_report",
                    "run_wf05_variation",
                    "retry_wf05_variation",
                    "get_wf05_report",
                }
                self.assertEqual(tool_names, expected_tools)
                for hidden_tool in (
                    "get_service_status",
                    "get_capabilities",
                    "design_primers_with_primer3",
                    "get_agent_audit_log",
                    "run_wf02_multiplex_panel",
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
                wf03_tool = next(
                    item
                    for item in listed.json()["result"]["tools"]
                    if item["name"] == "run_wf03_internal_control"
                )
                self.assertEqual(wf03_tool["inputSchema"]["required"], ["host_species"])
                self.assertNotIn("thresholds", wf03_tool["inputSchema"]["properties"])
                self.assertNotIn("gene_priority", wf03_tool["inputSchema"]["properties"])
                self.assertIn(
                    "input_artifact_id",
                    wf03_tool["inputSchema"]["properties"],
                )
                self.assertIn("input_gene", wf03_tool["inputSchema"]["properties"])
                self.assertIn("WF-03 新任务唯一入口", wf03_tool["description"])
                self.assertIn("必须立即调用", wf03_tool["description"])
                self.assertIn("禁止在调用前分析", wf03_tool["description"])
                self.assertIn("只能询问该字段并停止", wf03_tool["description"])
                self.assertIn("必须先调用 download_himat_file", wf03_tool["description"])
                self.assertIn(
                    "顶层input_artifact_id原样传入",
                    wf03_tool["description"],
                )
                self.assertIn(
                    "终态结构由outputSchema定义",
                    wf03_tool["description"],
                )
                wf04_tool = next(
                    item
                    for item in listed.json()["result"]["tools"]
                    if item["name"] == "run_wf04_validation"
                )
                self.assertEqual(
                    wf04_tool["inputSchema"].get("required", []),
                    [],
                )
                self.assertIn(
                    "input_artifact_id",
                    wf04_tool["inputSchema"]["properties"],
                )
                self.assertIn(
                    "顶层input_artifact_id原样传入",
                    wf04_tool["description"],
                )
                self.assertIn("禁止调用前分析", wf04_tool["description"])
                self.assertIn("primer_design_output", wf04_tool["description"])
                self.assertIn(
                    "终态结构由outputSchema定义",
                    wf04_tool["description"],
                )
                self.assertNotIn("allow_fallback", wf04_tool["inputSchema"]["properties"])
                for terminal_tool_name in (
                    "get_wf02_batch",
                    "run_wf02_until_blocked",
                    "get_wf02_export",
                    "retry_wf03_internal_control",
                    "get_wf03_report",
                    "retry_wf04_validation",
                    "get_wf04_report",
                    "retry_wf05_variation",
                    "get_wf05_report",
                ):
                    terminal_tool = next(
                        item
                        for item in listed.json()["result"]["tools"]
                        if item["name"] == terminal_tool_name
                    )
                    self.assertIn(
                        "终态结构由outputSchema定义",
                        terminal_tool["description"],
                    )
                    self.assertIn(
                        "由工具直接交付",
                        terminal_tool["description"],
                    )
                    self.assertIn(
                        "不得生成助手文本",
                        terminal_tool["description"],
                    )
                    output_schema = terminal_tool["outputSchema"]
                    self.assertEqual(len(output_schema["oneOf"]), 2)
                    card_schema = output_schema["oneOf"][0]
                    self.assertFalse(card_schema["additionalProperties"])
                    self.assertEqual(
                        card_schema["properties"]["type"]["const"],
                        "primer_design_output",
                    )
                    self.assertEqual(
                        set(card_schema["required"]),
                        {"data", "type"},
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
                    {"target_organism"},
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
                    "input_artifact_id",
                    create_batch_tool["inputSchema"]["properties"],
                )
                for removed_field in (
                    "payload",
                    "design_input_form",
                    "user_field_updates",
                ):
                    self.assertNotIn(
                        removed_field,
                        create_batch_tool["inputSchema"]["properties"],
                    )
                self.assertIn(
                    "host",
                    create_batch_tool["inputSchema"]["properties"],
                )
                create_wf02_tool = next(
                    item for item in listed.json()["result"]["tools"]
                    if item["name"] == "create_wf02_batch"
                )
                self.assertEqual(
                    set(create_wf02_tool["inputSchema"].get("required", [])),
                    set(),
                )
                self.assertEqual(
                    set(create_wf02_tool["inputSchema"]["properties"]),
                    {
                        "targets",
                        "host_species",
                        "include_internal_control",
                        "task_name",
                        "runtime_config",
                        "expected_target_count",
                        "failed_file_ids",
                    },
                )
                self.assertIn(
                    "每个文件绑定一个 target",
                    create_wf02_tool["inputSchema"]["properties"]["targets"][
                        "description"
                    ],
                )
                self.assertIn(
                    "input_artifact_id",
                    create_wf02_tool["inputSchema"]["properties"]["targets"][
                        "description"
                    ],
                )
                self.assertIn(
                    "downstream.wf02.target",
                    download_tool["description"],
                )
                self.assertIn(
                    "FASTA 正文由 AgentD 后端解析",
                    download_tool["description"],
                )
                self.assertIn(
                    "复用本轮及此前对话已提供的值",
                    create_wf02_tool["inputSchema"]["properties"]["host_species"][
                        "description"
                    ],
                )
                self.assertIn(
                    "由后端应用默认配置",
                    create_wf02_tool["inputSchema"]["properties"]["runtime_config"][
                        "description"
                    ],
                )
                self.assertNotIn(
                    "fasta_text",
                    create_wf02_tool["inputSchema"]["properties"]["targets"][
                        "description"
                    ],
                )
                bounded_tool = next(
                    item for item in listed.json()["result"]["tools"]
                    if item["name"] == "run_wf02_until_blocked"
                )
                self.assertIn("持久化后台任务", bounded_tool["description"])
                self.assertIn("同步等待235秒", bounded_tool["description"])
                self.assertIn("后台继续", bounded_tool["description"])
                wf02_export_tool = next(
                    item for item in listed.json()["result"]["tools"]
                    if item["name"] == "get_wf02_export"
                )
                self.assertEqual(
                    set(wf02_export_tool["inputSchema"]["required"]),
                    {"batch_id", "format_name"},
                )
                self.assertEqual(
                    set(
                        wf02_export_tool["inputSchema"]["properties"][
                            "format_name"
                        ]["enum"]
                    ),
                    {"html", "csv", "pdf"},
                )
                self.assertIn(
                    "CSV/PDF 不预生成",
                    wf02_export_tool["description"],
                )
                wf02_batch_tool = next(
                    item for item in listed.json()["result"]["tools"]
                    if item["name"] == "get_wf02_batch"
                )
                self.assertIn(
                    "用户明确查询时读取一次",
                    wf02_batch_tool["description"],
                )
                self.assertIn(
                    "运行中只返回当前状态并停止",
                    wf02_batch_tool["description"],
                )
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
                self.assertIn("完整 WF-01", create_tool["description"])
                self.assertIn("M2-M6", create_tool["description"])
                self.assertIn("P2/BLAST", create_tool["description"])
                self.assertIn(
                    "终态结构由outputSchema定义",
                    create_tool["description"],
                )
                self.assertNotIn("模型转发", create_tool["description"])
                self.assertIn("立即结束本轮", create_tool["description"])
                for follow_up_tool_name in (
                    "get_wf01_batch",
                    "get_wf01_report",
                    "get_wf01_candidate",
                    "get_wf01_export",
                    "create_wf01_version",
                    "run_wf01_until_blocked",
                ):
                    follow_up_tool = next(
                        item
                        for item in listed.json()["result"]["tools"]
                        if item["name"] == follow_up_tool_name
                    )
                    batch_description = follow_up_tool["inputSchema"]["properties"][
                        "batch_id"
                    ]["description"]
                    self.assertIn("内部 UUID", batch_description)
                    self.assertIn("WF01-YYMMDD-HHMM-XXXX", batch_description)

                module = sys.modules["agentd.mcp_server"]
                with patch.object(
                    module.service,
                    "run_wf01_v02_until_blocked",
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
                direct_auto_run.assert_not_called()
                self.assertEqual(direct_sequence.status_code, 200, direct_sequence.text)
                direct_result = direct_sequence.json()["result"]
                self.assertTrue(direct_result["isError"])
                self.assertIn("target_organism", direct_result["content"][0]["text"])

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
                                    "sequence": "ACGT" * 10,
                                    "target_organism": "猪瘟病毒",
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
                self.assertIn(
                    "终态结构由outputSchema定义",
                    run_until_blocked_tool["description"],
                )
                get_batch_tool = next(
                    item for item in listed.json()["result"]["tools"]
                    if item["name"] == "get_wf01_batch"
                )
                self.assertIn("查看、下载、下载地址", get_batch_tool["description"])
                self.assertIn(
                    "终态结构由outputSchema定义",
                    get_batch_tool["description"],
                )
                submit_interaction_tool = next(
                    item for item in listed.json()["result"]["tools"]
                    if item["name"] == "submit_wf01_interaction_response"
                )
                self.assertIn("完整 role=user agent_interaction_response", submit_interaction_tool["description"])
                self.assertIn("next_action", submit_interaction_tool["description"])
                self.assertIn(
                    "终态结构由outputSchema定义",
                    submit_interaction_tool["description"],
                )

                total_description_chars = sum(
                    len(item["description"])
                    for item in listed.json()["result"]["tools"]
                )
                self.assertLessEqual(
                    total_description_chars,
                    140 * len(listed.json()["result"]["tools"]),
                )

    def test_wf01_upload_uses_artifact_directly_and_requires_full_route(self) -> None:
        with tempfile.TemporaryDirectory(prefix="oligosmith-wf01-upload-") as state_dir:
            app = self._load_app(state_dir)
            module = sys.modules["agentd.mcp_server"]
            artifact = module.service.input_artifacts.store_inline_fasta(
                ">CSFV_test\n" + "ACGT" * 60 + "\n"
            )
            with patch.object(
                module.service,
                "run_wf01_v02_until_blocked",
                side_effect=lambda batch_id: (
                    module.service.get_wf01_v02_agent_summary(batch_id)
                ),
            ) as auto_run:
                with TestClient(app) as client:
                    response = client.post(
                        "/mcp",
                        headers=self._mcp_headers(self._token("wf01-upload-user")),
                        json={
                            "jsonrpc": "2.0",
                            "id": 40,
                            "method": "tools/call",
                            "params": {
                                "name": "create_wf01_batch",
                                "arguments": {
                                    "input_artifact_id": artifact["input_artifact_id"],
                                    "target_organism": "猪瘟病毒",
                                    "host": "猪",
                                },
                            },
                        },
                    )
            auto_run.assert_called_once()
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()["result"]
            self.assertFalse(result["isError"], response.text)
            content = result["structuredContent"]
            self.assertEqual(
                content["target_manifest"]["targets"][0]["execution_path"],
                "FULL_SPECIFICITY",
            )

    def test_wf02_upload_route_uses_artifact_reference_and_guides_inline_retry(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="oligosmith-wf02-upload-") as state_dir:
            app = self._load_app(state_dir)
            module = sys.modules["agentd.mcp_server"]
            headers = self._mcp_headers(self._token("wf02-upload-user"))
            artifact_id = "himat-" + "a" * 32
            with patch.object(
                module.service,
                "download_himat_file",
                return_value={
                    "input_artifact_id": artifact_id,
                    "source_file_id": 42,
                    "file_name": "CSFV-target.fasta",
                    "content_type": "text/x-fasta",
                    "size_bytes": 512,
                    "sha256": "b" * 64,
                    "status": "READY",
                },
            ) as download:
                with TestClient(app) as client:
                    prepared = client.post(
                        "/mcp",
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": 181,
                            "method": "tools/call",
                            "params": {
                                "name": "download_himat_file",
                                "arguments": {"file_id": 42},
                            },
                        },
                    )
                    self.assertEqual(prepared.status_code, 200, prepared.text)
                    prepared_result = prepared.json()["result"]
                    self.assertFalse(prepared_result["isError"], prepared.text)
                    content = prepared_result["structuredContent"]
                    self.assertEqual(
                        content["downstream"]["wf02"]["tool"],
                        "create_wf02_batch",
                    )
                    self.assertEqual(
                        content["downstream"]["wf02"]["target"],
                        {
                            "display_name": "CSFV-target",
                            "input_artifact_id": artifact_id,
                        },
                    )
                    self.assertNotIn("wf03", content["downstream"])
                    self.assertNotIn("wf04", content["downstream"])

                    rejected = client.post(
                        "/mcp",
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": 182,
                            "method": "tools/call",
                            "params": {
                                "name": "create_wf02_batch",
                                "arguments": {
                                    "targets": [
                                        {
                                            "display_name": "A",
                                            "fasta_text": ">A\n" + "ACGT" * 30,
                                        },
                                        {
                                            "display_name": "B",
                                            "fasta_text": ">B\n" + "TGCA" * 30,
                                        },
                                    ],
                                    "host_species": "猪",
                                },
                            },
                        },
                    )
                    self.assertEqual(rejected.status_code, 200, rejected.text)
                    rejected_result = rejected.json()["result"]
                    self.assertTrue(rejected_result["isError"], rejected.text)
                    error_text = rejected_result["content"][0]["text"]
                    self.assertIn("download_himat_file", error_text)
                    self.assertIn("input_artifact_id", error_text)

                    missing_count = client.post(
                        "/mcp",
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": 183,
                            "method": "tools/call",
                            "params": {
                                "name": "create_wf02_batch",
                                "arguments": {
                                    "targets": [
                                        {
                                            "display_name": "A",
                                            "input_artifact_id": artifact_id,
                                        },
                                        {
                                            "display_name": "B",
                                            "input_artifact_id": "himat-" + "c" * 32,
                                        },
                                    ],
                                    "host_species": "猪",
                                },
                            },
                        },
                    )
                    self.assertEqual(missing_count.status_code, 200)
                    missing_result = missing_count.json()["result"]
                    self.assertTrue(missing_result["isError"])
                    self.assertIn(
                        "WF02_UPLOAD_EXPECTED_TARGET_COUNT_REQUIRED",
                        missing_result["content"][0]["text"],
                    )

                    incomplete = client.post(
                        "/mcp",
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": 184,
                            "method": "tools/call",
                            "params": {
                                "name": "create_wf02_batch",
                                "arguments": {
                                    "targets": [
                                        {
                                            "display_name": "A",
                                            "input_artifact_id": artifact_id,
                                        },
                                        {
                                            "display_name": "B",
                                            "input_artifact_id": "himat-" + "c" * 32,
                                        },
                                    ],
                                    "host_species": "猪",
                                    "expected_target_count": 3,
                                    "failed_file_ids": [43],
                                },
                            },
                        },
                    )
                    self.assertEqual(incomplete.status_code, 200)
                    incomplete_result = incomplete.json()["result"]
                    self.assertTrue(incomplete_result["isError"])
                    self.assertIn(
                        "WF02_UPLOAD_INTAKE_INCOMPLETE",
                        incomplete_result["content"][0]["text"],
                    )
            download.assert_called_once_with(42)

    def test_wf02_mcp_run_starts_background_once_and_stops_current_turn(self) -> None:
        with tempfile.TemporaryDirectory(prefix="oligosmith-wf02-step-") as state_dir:
            app = self._load_app(state_dir)
            module = sys.modules["agentd.mcp_server"]
            headers = self._mcp_headers(self._token("wf02-step-user"))
            batch_id = "00000000-0000-0000-0000-000000000042"
            resume_token = "a" * 64
            progress = {
                "phase": "PANEL_SCREENING",
                "processed_units": 500,
                "total_units": 3125,
                "remaining_units": 2625,
            }
            waiting = {
                "response_schema_version": "wf02-v0.1-agent-summary-v1",
                "workflow": "WF-02",
                "batch_id": batch_id,
                "batch_code": "WF02-TEST",
                "status": "SCREENING_PANELS",
                "stage": "N18",
                "next_action": "CONTINUE_PANEL_SCREENING",
                "resume_token": resume_token,
                "execution_progress": progress,
                "execution_unit": {
                    "status": "COMMITTED",
                    "state_committed": True,
                },
                "background_execution": {"state": "RUNNING"},
                "user_message": "任务正在后台运行，请稍后查询。",
                "flowControl": {
                    "action": "WAIT_FOR_BACKGROUND",
                    "stopCurrentTurn": True,
                    "resumeTool": "get_wf02_batch",
                    "userInitiatedQueryOnly": True,
                },
                "orchestration": {
                    "mode": "PERSISTENT_BACKGROUND",
                    "synchronous_wait_seconds": 0.25,
                    "finished_within_window": False,
                    "background_continues": True,
                },
            }
            with patch.dict(
                os.environ, {"MCP_WF02_SYNC_WAIT_SECONDS": "0.25"}
            ):
                with patch.object(
                    module.service,
                    "run_wf02_v01_background",
                    return_value=waiting,
                ) as durable_run:
                    with TestClient(app) as client:
                        response = client.post(
                            "/mcp",
                            headers=headers,
                            json={
                                "jsonrpc": "2.0",
                                "id": 183,
                                "method": "tools/call",
                                "params": {
                                    "name": "run_wf02_until_blocked",
                                    "arguments": {"batch_id": batch_id},
                                },
                            },
                        )
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()["result"]
            self.assertFalse(result["isError"], response.text)
            content = result["structuredContent"]
            self.assertEqual(content["flowControl"]["action"], "WAIT_FOR_BACKGROUND")
            self.assertEqual(content["tool_control"]["mode"], "PERSISTENT_BACKGROUND")
            self.assertEqual(
                content["tool_control"]["directive"],
                "STOP_CURRENT_TURN",
            )
            self.assertFalse(content["tool_control"]["call_again"])
            self.assertIsNone(content["tool_control"]["next_tool"])
            self.assertIsNone(content["tool_control"]["next_arguments"])
            workflow_state = result["_meta"]["oligosmith"]["workflowState"]
            self.assertTrue(workflow_state["stopCurrentTurn"])
            self.assertFalse(workflow_state["continueCurrentTurn"])
            self.assertFalse(workflow_state["suppressAssistantText"])
            durable_run.assert_called_once_with(
                batch_id,
                wait_seconds=0.25,
                expected_resume_token=None,
            )

    def test_wf02_create_scopes_retry_replay_to_authenticated_caller(self) -> None:
        with tempfile.TemporaryDirectory(prefix="oligosmith-wf02-scope-") as state_dir:
            app = self._load_app(state_dir)
            module = sys.modules["agentd.mcp_server"]
            headers = self._mcp_headers(self._token("wf02-scope-user"))
            returned = {
                "workflow": "WF-02",
                "status": "WAITING_USER",
                "stage": "N04",
                "next_action": "WAIT_FOR_USER",
            }
            with patch.object(
                module.service,
                "create_wf02_v01_batch",
                return_value=returned,
            ) as create:
                with TestClient(app) as client:
                    response = client.post(
                        "/mcp",
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": 186,
                            "method": "tools/call",
                            "params": {
                                "name": "create_wf02_batch",
                                "arguments": {
                                    "targets": ["target-a", "target-b"],
                                    "host_species": "test-host",
                                },
                            },
                        },
                    )

            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()["result"]["isError"], response.text)
            create.assert_called_once_with(
                {
                    "targets": ["target-a", "target-b"],
                    "host_species": "test-host",
                },
                creation_scope="sub:wf02-scope-user",
            )

    def test_wf02_mcp_child_submission_hands_control_to_background(self) -> None:
        with tempfile.TemporaryDirectory(prefix="oligosmith-wf02-submit-") as state_dir:
            app = self._load_app(state_dir)
            module = sys.modules["agentd.mcp_server"]
            headers = self._mcp_headers(self._token("wf02-submit-user"))
            batch_id = "00000000-0000-0000-0000-000000000043"
            resume_token = "b" * 64
            progress = {
                "phase": "TARGET_DESIGN",
                "processed_units": 1,
                "total_units": 3,
                "remaining_units": 2,
            }
            child_response = {
                "type": "agent_interaction_response",
                "task": "MULTIPLEX_PANEL",
                "interactionId": "wf02-target-template-A",
                "answer": "PROVIDE_TEMPLATE",
                "fieldValues": {"input_artifact_id": "himat-" + "a" * 32},
            }
            submitted = {
                "response_schema_version": "wf02-v0.1-agent-summary-v1",
                "workflow": "WF-02",
                "batch_id": batch_id,
                "status": "DESIGNING_TARGETS",
                "stage": "N06",
                "next_action": "CONTINUE_TARGET_DESIGN",
                "resume_token": resume_token,
                "execution_progress": progress,
                "execution_unit": {
                    "status": "COMMITTED",
                    "state_committed": True,
                },
            }
            waiting = {
                **submitted,
                "background_execution": {"state": "RUNNING"},
                "user_message": "任务正在后台运行，请稍后查询。",
                "flowControl": {
                    "action": "WAIT_FOR_BACKGROUND",
                    "stopCurrentTurn": True,
                    "resumeTool": "get_wf02_batch",
                },
                "orchestration": {
                    "mode": "PERSISTENT_BACKGROUND",
                    "synchronous_wait_seconds": 0.0,
                    "finished_within_window": False,
                    "background_continues": True,
                },
            }
            with patch.dict(os.environ, {"MCP_WF02_SYNC_WAIT_SECONDS": "0"}):
                with patch.object(
                    module.service.wf02_v01,
                    "submit_child_response",
                    return_value=submitted,
                ) as bounded_submit:
                    with patch.object(
                        module.service,
                        "run_wf02_v01_background",
                        return_value=waiting,
                    ) as durable_run:
                        with TestClient(app) as client:
                            response = client.post(
                                "/mcp",
                                headers=headers,
                                json={
                                    "jsonrpc": "2.0",
                                    "id": 184,
                                    "method": "tools/call",
                                    "params": {
                                        "name": "submit_wf02_child_response",
                                        "arguments": {
                                            "batch_id": batch_id,
                                            "target_id": "A",
                                            "response": child_response,
                                        },
                                    },
                                },
                            )

            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()["result"]
            self.assertFalse(result["isError"], response.text)
            self.assertEqual(
                result["structuredContent"]["tool_control"]["directive"],
                "STOP_CURRENT_TURN",
            )
            self.assertFalse(
                result["structuredContent"]["tool_control"]["call_again"]
            )
            bounded_submit.assert_called_once_with(
                batch_id,
                "A",
                child_response,
                bounded=True,
            )
            durable_run.assert_called_once_with(
                batch_id,
                wait_seconds=0.0,
                expected_resume_token=resume_token,
            )

    def test_wf02_background_window_returns_terminal_in_one_tool_call(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="oligosmith-wf02-background-terminal-"
        ) as state_dir:
            app = self._load_app(state_dir)
            module = sys.modules["agentd.mcp_server"]
            headers = self._mcp_headers(self._token("wf02-relax-user"))
            batch_id = "00000000-0000-0000-0000-000000000044"
            terminal = {
                "response_schema_version": "wf02-v0.1-agent-summary-v1",
                "workflow": "WF-02",
                "batch_id": batch_id,
                "batch_code": "WF02-TEST",
                "status": "COMPLETED",
                "stage": "N32",
                "next_action": "COMPLETE",
                "resume_token": "h" * 64,
                "execution_progress": {
                    "phase": "COMPLETE",
                    "processed_units": 125,
                    "total_units": 125,
                    "remaining_units": 0,
                },
                "execution_unit": {
                    "status": "COMMITTED",
                    "state_committed": True,
                },
                "ui_message": {
                    "type": "primer_design_output",
                    "data": {
                        "status": "COMPLETED",
                        "reportTitle": "WF-02 report",
                        "summaryText": "Workflow completed.",
                        "primaryAttachmentId": "attachment_html",
                        "attachments": [
                            {
                                "attachmentId": "attachment_html",
                                "fileName": "report.html",
                                "fileType": "HTML",
                                "mimeType": "text/html",
                                "fileUrl": (
                                    "https://rustfs.ne.scqcyz.cn/wens/data/"
                                    "report.html"
                                ),
                            }
                        ],
                        "issues": [],
                    },
                },
                "background_execution": {"state": "COMPLETED"},
                "orchestration": {
                    "mode": "PERSISTENT_BACKGROUND",
                    "synchronous_wait_seconds": 235.0,
                    "finished_within_window": True,
                    "background_continues": False,
                },
            }
            with patch.object(
                module.service,
                "run_wf02_v01_background",
                return_value=terminal,
            ) as durable_run:
                with TestClient(app) as client:
                    response = client.post(
                        "/mcp",
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": 185,
                            "method": "tools/call",
                            "params": {
                                "name": "run_wf02_until_blocked",
                                "arguments": {
                                    "batch_id": batch_id,
                                    "resume_token": "a" * 64,
                                },
                            },
                        },
                    )

            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()["result"]
            self._assert_direct_himat_wire_card(
                result,
                terminal["ui_message"],
            )
            durable_run.assert_called_once_with(
                batch_id,
                wait_seconds=235.0,
                expected_resume_token="a" * 64,
            )

    def test_wf02_execution_window_delegates_stale_token_once(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="oligosmith-wf02-stale-window-"
        ) as state_dir:
            self._load_app(state_dir)
            module = sys.modules["agentd.mcp_server"]
            batch_id = "00000000-0000-0000-0000-000000000045"
            waiting = {
                "workflow": "WF-02",
                "batch_id": batch_id,
                "status": "SCREENING_PANELS",
                "stage": "N18",
                "next_action": "CONTINUE_PANEL_SCREENING",
                "resume_token": "c" * 64,
                "execution_progress": {
                    "phase": "PANEL_SCREENING",
                    "processed_units": 500,
                    "total_units": 3125,
                },
                "execution_unit": {
                    "status": "STALE_RESUME_TOKEN_RECOVERED",
                    "state_committed": False,
                },
                "background_execution": {"state": "RUNNING"},
                "flowControl": {
                    "action": "WAIT_FOR_BACKGROUND",
                    "stopCurrentTurn": True,
                    "resumeTool": "get_wf02_batch",
                },
                "orchestration": {
                    "mode": "PERSISTENT_BACKGROUND",
                    "stale_resume_token_recovered": True,
                },
            }
            with patch.object(
                module.service,
                "run_wf02_v01_background",
                return_value=waiting,
            ) as durable_run:
                result = module._run_wf02_execution_window(
                    batch_id,
                    "a" * 64,
                )

            self.assertTrue(
                result["orchestration"]["stale_resume_token_recovered"]
            )
            self.assertEqual(
                result["tool_control"]["directive"], "STOP_CURRENT_TURN"
            )
            self.assertFalse(result["tool_control"]["call_again"])
            durable_run.assert_called_once_with(
                batch_id,
                wait_seconds=235.0,
                expected_resume_token="a" * 64,
            )

    def test_wf02_intake_tools_pause_resume_and_keep_wf01_isolated(self) -> None:
        with tempfile.TemporaryDirectory(prefix="oligosmith-wf02-") as state_dir:
            app = self._load_app(state_dir)
            headers = self._mcp_headers(self._token("wf02-user"))
            with patch.dict(
                os.environ, {"MCP_WF02_SYNC_WAIT_SECONDS": "2"}
            ), TestClient(app) as client:
                blocked = client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 201,
                        "method": "tools/call",
                        "params": {
                            "name": "create_wf02_batch",
                            "arguments": {
                                "targets": ["猪瘟病毒", "非洲猪瘟病毒"],
                            },
                        },
                    },
                )
                self.assertEqual(blocked.status_code, 200, blocked.text)
                blocked_result = blocked.json()["result"]
                self.assertFalse(blocked_result["isError"], blocked.text)
                ui_message = blocked_result["structuredContent"]
                self.assertEqual(ui_message["type"], "agent_interaction")
                self.assertEqual(ui_message["task"], "MULTIPLEX_PANEL")
                self.assertEqual(ui_message["field"], "host_species")
                self.assertEqual(
                    json.loads(blocked_result["content"][0]["text"]),
                    ui_message,
                )

                resumed = client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 202,
                        "method": "tools/call",
                        "params": {
                            "name": "submit_wf02_input_response",
                            "arguments": {
                                "draft_id": ui_message["draftId"],
                                "response": {
                                    "type": "agent_interaction_response",
                                    "task": "MULTIPLEX_PANEL",
                                    "interactionId": ui_message["interactionId"],
                                    "answer": "EDIT",
                                    "fieldValues": {"host_species": "猪"},
                                },
                            },
                        },
                    },
                )
                self.assertEqual(resumed.status_code, 200, resumed.text)
                resumed_result = resumed.json()["result"]
                self.assertFalse(resumed_result["isError"], resumed.text)
                target_interaction = resumed_result["structuredContent"]
                self.assertEqual(target_interaction["type"], "agent_interaction")
                self.assertEqual(target_interaction["task"], "MULTIPLEX_PANEL")
                self.assertEqual(
                    json.loads(resumed_result["content"][0]["text"]),
                    target_interaction,
                )
                batch_id = target_interaction["taskId"]

                next_target = client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 205,
                        "method": "tools/call",
                        "params": {
                            "name": "submit_wf02_child_response",
                            "arguments": {
                                "batch_id": batch_id,
                                "target_id": target_interaction["targetId"],
                                "response": {
                                    "type": "agent_interaction_response",
                                    "task": "MULTIPLEX_PANEL",
                                    "interactionId": target_interaction[
                                        "interactionId"
                                    ],
                                    "answer": "PROVIDE_TEMPLATE",
                                    "fieldValues": {"sequence": "ACGT" * 30},
                                },
                            },
                        },
                    },
                )
                self.assertEqual(next_target.status_code, 200, next_target.text)
                next_result = next_target.json()["result"]
                self.assertFalse(next_result["isError"], next_target.text)
                next_interaction = next_result["structuredContent"]
                self.assertEqual(next_interaction["type"], "agent_interaction")
                self.assertNotEqual(
                    next_interaction["targetId"],
                    target_interaction["targetId"],
                )

            self.assertTrue((Path(state_dir) / "wf02-v01-batches").is_dir())
            self.assertFalse((Path(state_dir) / "wf01-v02-batches").exists())

    def test_wf02_overflow_keeps_control_state_and_paging_instruction(self) -> None:
        with tempfile.TemporaryDirectory(prefix="oligosmith-wf02-overflow-") as state_dir:
            self._load_app(state_dir)
            module = sys.modules["agentd.mcp_server"]
            payload = {
                "response_schema_version": "wf02-v0.1-agent-summary-v1",
                "workflow": "WF-02",
                "batch_id": "00000000-0000-4000-8000-000000000099",
                "status": "WAITING_TARGET_RESOURCES",
                "stage": "N08",
                "next_action": "WAIT_FOR_RESOURCE",
                "outcome": {"primary_failure_stage": "TARGET_DESIGN"},
                "resume_token": "a" * 64,
                "oversized_evidence": "x" * 100_000,
            }
            with patch.dict(
                os.environ,
                {"MCP_MAX_TOOL_RESULT_BYTES": "8192"},
            ):
                bounded = module._bounded_tool_result("wf02_get_batch", payload)

            self.assertTrue(bounded["response_truncated"])
            self.assertEqual(bounded["batch_id"], payload["batch_id"])
            self.assertEqual(bounded["status"], "WAITING_TARGET_RESOURCES")
            self.assertEqual(bounded["resume_token"], "a" * 64)
            self.assertEqual(
                bounded["detail_query"],
                {"tool": "get_wf02_batch", "use_pagination": True},
            )
            self.assertLessEqual(
                len(
                    json.dumps(
                        bounded,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ),
                8192,
            )

    def test_wf02_report_views_compact_large_evidence_before_transport(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="oligosmith-wf02-report-view-"
        ) as state_dir:
            self._load_app(state_dir)
            module = sys.modules["agentd.mcp_server"]
            huge = "x" * 100_000
            report = {
                "source": {
                    "batch_id": "00000000-0000-4000-8000-000000000098"
                },
                "report_id": "00000000-0000-4000-8000-000000000097",
                "result_summary": {"status": "FAIL"},
                "outcome": {"workflow_status": "FAILED"},
                "selection_mode": "INCOMPLETE_TARGET_COVERAGE",
                "multiplex": {
                    "status": "FAILED_INCOMPLETE_TARGET_COVERAGE",
                    "panel_size": 2,
                    "coverage": {"status": "INCOMPLETE"},
                    "coverage_gaps": [
                        {
                            "target_id": "B",
                            "reason_code": "TARGET_FAILED",
                            "failure": {
                                "code": "TARGET_FAILED",
                                "message": "failed",
                                "details": {"raw": huge},
                            },
                        }
                    ],
                },
                "top3_panels": [
                    {
                        "panel_id": "panel-1",
                        "tier": "STRICT",
                        "classification": "STRICT_COMPATIBLE",
                        "members": [
                            {
                                "target_id": "A",
                                "forward_primer": {
                                    "sequence_5to3": huge
                                },
                            }
                        ],
                        "p4_result": {
                            "status": "COMPLETED",
                            "verdict": "COMPATIBLE",
                            "heterodimer": {
                                "evaluated_pairs": 15,
                                "three_prime_risks": [huge],
                            },
                        },
                        "warnings": [],
                    }
                ],
                "traceability": {
                    "tool_executions": {
                        "wf01_targets": [
                            {
                                "tool": "WF-01",
                                "target_id": "B",
                                "status": "FAIL",
                                "failure": {
                                    "code": "TARGET_FAILED",
                                    "details": {"raw": huge},
                                },
                            }
                        ]
                    },
                    "target_lineage": [],
                },
            }

            summary = module._wf02_report_view(
                report, section="summary", cursor=0, limit=20
            )
            panels = module._wf02_report_view(
                report, section="panels", cursor=0, limit=20
            )
            failure = module._wf02_report_view(
                report, section="failure", cursor=0, limit=20
            )
            trace = module._wf02_report_view(
                report, section="trace", cursor=0, limit=20
            )
            panel_summary = module._wf02_panel_view(
                {
                    "batch_id": report["source"]["batch_id"],
                    "report_id": report["report_id"],
                    "panel": report["top3_panels"][0],
                },
                section="summary",
                cursor=0,
                limit=6,
            )
            panel_evidence = module._wf02_panel_view(
                {
                    "batch_id": report["source"]["batch_id"],
                    "report_id": report["report_id"],
                    "panel": report["top3_panels"][0],
                },
                section="evidence",
                cursor=0,
                limit=6,
            )

            for value in (
                summary,
                panels,
                failure,
                trace,
                panel_summary,
                panel_evidence,
            ):
                serialized = json.dumps(
                    value, ensure_ascii=False, separators=(",", ":")
                )
                self.assertNotIn(huge, serialized)
                self.assertLess(len(serialized.encode("utf-8")), 8192)
            panel_item = panels["panels"]["items"][0]
            self.assertNotIn("members", panel_item)
            self.assertEqual(
                panel_item["detail_query"]["tool"], "get_wf02_panel"
            )
            self.assertNotIn(
                "details",
                failure["coverage_gaps"]["items"][0]["failure"],
            )

if __name__ == "__main__":
    unittest.main()

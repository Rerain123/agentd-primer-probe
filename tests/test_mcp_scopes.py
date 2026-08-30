from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import jwt
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


SECRET = "test-only-secret-with-at-least-thirty-two-bytes"
EXPECTED_TOOLS = {
    "wf01": {
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
    },
    "wf02": {
        "download_himat_file",
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
    },
    "wf03": {
        "download_himat_file",
        "run_wf03_internal_control",
        "retry_wf03_internal_control",
        "get_wf03_report",
    },
    "wf04": {
        "download_himat_file",
        "run_wf04_validation",
        "retry_wf04_validation",
        "get_wf04_report",
    },
    "wf05": {
        "download_himat_file",
        "run_wf05_variation",
        "retry_wf05_variation",
        "get_wf05_report",
    },
}


def _load_module(state_dir: str):
    settings = {
        "AGENTD_PROJECT_ROOT": str(ROOT),
        "AGENTD_STATE_DIR": state_dir,
        "MCP_AUTH_REQUIRED": "true",
        "MCP_JWT_SECRET": SECRET,
        "MCP_JWT_ALGORITHM": "HS256",
        "MCP_JWT_REQUIRE_EXP": "true",
        "MCP_PUBLIC_WF01_ARTIFACTS": "false",
        "MCP_PUBLIC_BASE_URL": "http://testserver",
    }
    with patch.dict(os.environ, settings, clear=False):
        sys.modules.pop("agentd.mcp_server", None)
        sys.modules.pop("agentd.api", None)
        return importlib.import_module("agentd.mcp_server")


def _token() -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": "scoped-mcp-test", "iat": now, "exp": now + timedelta(minutes=5)},
        SECRET,
        algorithm="HS256",
    )


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-11-25",
    }


def _initialize(client: TestClient, path: str, request_id: int):
    return client.post(
        path,
        headers=_headers(),
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "scope-test", "version": "1.0"},
            },
        },
    )


def _list_tools(client: TestClient, path: str, request_id: int):
    return client.post(
        path,
        headers=_headers(),
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/list",
            "params": {},
        },
    )


def _contains_chinese(text: str) -> bool:
    return any("\u4e00" <= character <= "\u9fff" for character in text)


def _assert_chinese_tool_metadata(tools: list[dict]) -> None:
    def assert_no_display_title(value: object) -> None:
        if isinstance(value, dict):
            assert "title" not in value
            for child in value.values():
                assert_no_display_title(child)
        elif isinstance(value, list):
            for child in value:
                assert_no_display_title(child)

    for tool in tools:
        assert _contains_chinese(tool.get("description") or ""), tool["name"]
        schema = tool["inputSchema"]
        assert_no_display_title(schema)
        for parameter_name, parameter in schema.get("properties", {}).items():
            assert _contains_chinese(parameter.get("description") or ""), (
                f"{tool['name']}.{parameter_name}"
            )


def test_scoped_endpoints_are_authenticated_and_expose_exact_tool_catalogs():
    with tempfile.TemporaryDirectory(prefix="oligosmith-mcp-scopes-") as state_dir:
        module = _load_module(state_dir)
        with TestClient(module.app) as client:
            legacy = _list_tools(client, "/mcp", 900)
            assert legacy.status_code == 200, legacy.text
            _assert_chinese_tool_metadata(legacy.json()["result"]["tools"])

            for index, (scope, expected) in enumerate(EXPECTED_TOOLS.items(), 1):
                path = f"/mcp/{scope}/"
                unauthorized = client.post(path, json={})
                assert unauthorized.status_code == 401

                initialized = _initialize(client, path, index)
                assert initialized.status_code == 200, initialized.text
                result = initialized.json()["result"]
                assert result["serverInfo"]["name"] == (
                    f"OligoSmith {scope.upper().replace('WF', 'WF-')}"
                )
                assert len(result["instructions"]) < 260
                assert "终态primer_design_output已由工具直接交付" in result["instructions"]
                assert "不得生成助手文本" in result["instructions"]
                assert "立即结束当前轮次" in result["instructions"]

                listed = _list_tools(client, path, index + 100)
                assert listed.status_code == 200, listed.text
                tools = listed.json()["result"]["tools"]
                assert {tool["name"] for tool in tools} == expected
                _assert_chinese_tool_metadata(tools)
                for tool in tools:
                    assert "终态结构由outputSchema定义" not in tool["description"]
                    assert "模型转发" not in tool["description"]


def test_scoped_servers_reuse_the_legacy_tool_callables():
    with tempfile.TemporaryDirectory(prefix="oligosmith-mcp-callables-") as state_dir:
        module = _load_module(state_dir)
        for scope, expected in EXPECTED_TOOLS.items():
            scoped_server = module.scoped_mcp_servers[scope]
            for tool_name in expected:
                legacy_tool = module.mcp._tool_manager.get_tool(tool_name)
                scoped_tool = scoped_server._tool_manager.get_tool(tool_name)
                assert legacy_tool is not None
                assert scoped_tool is not None
                assert scoped_tool.fn is legacy_tool.fn
                assert scoped_tool.output_schema == legacy_tool.output_schema


def test_each_scoped_catalog_uses_less_than_half_the_legacy_context():
    with tempfile.TemporaryDirectory(prefix="oligosmith-mcp-context-") as state_dir:
        module = _load_module(state_dir)

        def context_chars(server) -> int:
            tools = list(server._tool_manager._tools.values())
            return (
                len(server.instructions or "")
                + sum(len(tool.description or "") for tool in tools)
                + sum(
                    len(
                        json.dumps(
                            tool.parameters,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    )
                    for tool in tools
                )
            )

        legacy_chars = context_chars(module.mcp)
        for scope, server in module.scoped_mcp_servers.items():
            assert context_chars(server) < legacy_chars / 2, scope


def test_scoped_and_legacy_terminal_results_are_identical():
    with tempfile.TemporaryDirectory(prefix="oligosmith-mcp-parity-") as state_dir:
        module = _load_module(state_dir)
        payload = {
            "data": {
                "status": "COMPLETED",
                "reportTitle": "scope parity report",
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
            },
            "type": "primer_design_output",
        }
        delivery = {
            "job_id": "job-scope-parity",
            "workflow": "WF-04",
            "status": "COMPLETED",
            "ui_message": payload,
        }
        request = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "get_wf04_report",
                "arguments": {"job_id": "job-scope-parity"},
            },
        }

        with patch.object(
            module.service,
            "get_wf04_delivery",
            return_value=delivery,
        ):
            with TestClient(module.app) as client:
                legacy = client.post(
                    "/mcp",
                    headers=_headers(),
                    json={**request, "id": 201},
                )
                scoped = client.post(
                    "/mcp/wf04/",
                    headers=_headers(),
                    json={**request, "id": 202},
                )

        assert legacy.status_code == 200, legacy.text
        assert scoped.status_code == 200, scoped.text
        legacy_result = legacy.json()["result"]
        scoped_result = scoped.json()["result"]
        assert scoped_result == legacy_result
        assert scoped_result == payload
        assert list(scoped_result) == ["data", "type"]
        assert not {"content", "structuredContent", "isError"} & scoped_result.keys()
        assert "_meta" not in scoped_result

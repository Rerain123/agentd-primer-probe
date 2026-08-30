from __future__ import annotations

import importlib
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

from agentd.mcp_himat_compat import rewrite_himat_jsonrpc_payload


SECRET = "test-only-secret-with-at-least-thirty-two-bytes"


def _card() -> dict:
    return {
        "data": {
            "status": "COMPLETED",
            "reportTitle": "WF-04 终态报告",
            "summaryText": "完整结果见 HTML 附件。",
            "primaryAttachmentId": "attachment_html",
            "attachments": [
                {
                    "attachmentId": "attachment_html",
                    "fileName": "WF04_report.html",
                    "fileType": "HTML",
                    "mimeType": "text/html; charset=utf-8",
                    "fileUrl": (
                        "https://rustfs.ne.scqcyz.cn/wens/data/"
                        "WF04_report.html"
                    ),
                }
            ],
            "issues": [],
        },
        "type": "primer_design_output",
    }


def _token() -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": "himat-compat-test", "iat": now, "exp": now + timedelta(minutes=5)},
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


def test_rewrite_uses_structured_content_and_strips_mcp_fields() -> None:
    card = _card()
    original = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "content": [{"type": "text", "text": "polluted markdown"}],
            "structuredContent": card,
            "isError": False,
        },
    }

    rewritten, changed = rewrite_himat_jsonrpc_payload(original)

    assert changed is True
    assert rewritten == {"jsonrpc": "2.0", "id": 1, "result": card}
    assert list(rewritten["result"]) == ["data", "type"]
    assert not {"content", "structuredContent", "isError"} & set(
        rewritten["result"]
    )


def test_rewrite_leaves_nonterminal_results_unchanged() -> None:
    original = {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "content": [{"type": "text", "text": "waiting"}],
            "structuredContent": {"type": "agent_interaction"},
            "isError": False,
        },
    }

    rewritten, changed = rewrite_himat_jsonrpc_payload(original)

    assert changed is False
    assert rewritten is original


def test_http_mcp_compat_returns_direct_card_on_legacy_and_scoped_routes() -> None:
    with tempfile.TemporaryDirectory(prefix="oligosmith-himat-compat-") as state_dir:
        module = _load_module(state_dir)
        card = _card()
        delivery = {
            "workflow": "WF-04",
            "job_id": "job-himat-compat",
            "next_action": "END",
            "ui_message": card,
        }
        request = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "get_wf04_report",
                "arguments": {"job_id": "job-himat-compat"},
            },
        }

        with patch.object(
            module.service,
            "get_wf04_delivery",
            return_value=delivery,
        ):
            with TestClient(module.app) as client:
                for request_id, path in ((11, "/mcp"), (12, "/mcp/wf04/")):
                    response = client.post(
                        path,
                        headers=_headers(),
                        json={**request, "id": request_id},
                    )
                    assert response.status_code == 200, response.text
                    assert response.json() == {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": card,
                    }

                initialized = client.post(
                    "/mcp",
                    headers=_headers(),
                    json={
                        "jsonrpc": "2.0",
                        "id": 13,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-11-25",
                            "capabilities": {},
                            "clientInfo": {
                                "name": "compat-test",
                                "version": "1.0",
                            },
                        },
                    },
                )
                assert initialized.status_code == 200, initialized.text
                assert "serverInfo" in initialized.json()["result"]

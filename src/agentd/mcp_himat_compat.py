"""HiMat compatibility for terminal MCP tool results.

HiMat's report-card parser expects the JSON-RPC ``result`` member to be the
business object itself.  Standard MCP transports wrap that object in a
``CallToolResult``.  This response-only adapter unwraps terminal
``primer_design_output`` results without changing any other MCP response.
"""

from __future__ import annotations

import json
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send


def _direct_terminal_result(message: Any) -> tuple[Any, bool]:
    if not isinstance(message, dict):
        return message, False
    result = message.get("result")
    if not isinstance(result, dict) or result.get("isError") is True:
        return message, False
    structured = result.get("structuredContent")
    if not isinstance(structured, dict):
        return message, False
    if structured.get("type") != "primer_design_output":
        return message, False
    data = structured.get("data")
    if not isinstance(data, dict):
        return message, False

    rewritten = dict(message)
    # Keep the business contract exact: result may contain data/type only.
    rewritten["result"] = {
        "data": data,
        "type": "primer_design_output",
    }
    return rewritten, True


def rewrite_himat_jsonrpc_payload(payload: Any) -> tuple[Any, bool]:
    """Unwrap terminal cards in one JSON-RPC response or response batch."""

    if isinstance(payload, list):
        rewritten_items: list[Any] = []
        changed = False
        for item in payload:
            rewritten, item_changed = _direct_terminal_result(item)
            rewritten_items.append(rewritten)
            changed = changed or item_changed
        return rewritten_items, changed
    return _direct_terminal_result(payload)


def _rewrite_json_bytes(body: bytes) -> tuple[bytes, bool]:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body, False
    rewritten, changed = rewrite_himat_jsonrpc_payload(payload)
    if not changed:
        return body, False
    return (
        json.dumps(
            rewritten,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8"),
        True,
    )


def _rewrite_sse_bytes(body: bytes) -> tuple[bytes, bool]:
    output: list[bytes] = []
    changed = False
    for line in body.splitlines(keepends=True):
        stripped = line.rstrip(b"\r\n")
        ending = line[len(stripped) :]
        if not stripped.startswith(b"data:"):
            output.append(line)
            continue
        prefix = b"data:"
        value = stripped[len(prefix) :].lstrip(b" ")
        rewritten, line_changed = _rewrite_json_bytes(value)
        if not line_changed:
            output.append(line)
            continue
        output.append(prefix + b" " + rewritten + ending)
        changed = True
    return b"".join(output), changed


def _rewrite_response_body(
    body: bytes,
    content_type: str,
) -> tuple[bytes, bool]:
    normalized = content_type.lower()
    if "text/event-stream" in normalized:
        return _rewrite_sse_bytes(body)
    if "json" in normalized or body.lstrip().startswith((b"{", b"[")):
        return _rewrite_json_bytes(body)
    return body, False


def _content_type(headers: list[tuple[bytes, bytes]]) -> str:
    for name, value in headers:
        if name.lower() == b"content-type":
            return value.decode("latin-1")
    return ""


def _with_content_length(
    headers: list[tuple[bytes, bytes]],
    length: int,
) -> list[tuple[bytes, bytes]]:
    retained = [
        (name, value)
        for name, value in headers
        if name.lower() != b"content-length"
    ]
    retained.append((b"content-length", str(length).encode("ascii")))
    return retained


class HiMatDirectResultCompatibilityMiddleware:
    """Rewrite only successful terminal MCP responses for HiMat."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    @staticmethod
    def _is_mcp_request(scope: Scope) -> bool:
        if scope.get("type") != "http":
            return False
        if str(scope.get("method") or "").upper() != "POST":
            return False
        path = str(scope.get("path") or "")
        return path == "/mcp" or path.startswith("/mcp/")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._is_mcp_request(scope):
            await self.app(scope, receive, send)
            return

        response_start: Message | None = None
        body_parts: list[bytes] = []

        async def capture(message: Message) -> None:
            nonlocal response_start
            if message["type"] == "http.response.start":
                response_start = message
                return
            if message["type"] != "http.response.body":
                await send(message)
                return

            body_parts.append(message.get("body", b""))
            if message.get("more_body", False):
                return

            if response_start is None:
                await send(message)
                return

            start = dict(response_start)
            headers = list(start.get("headers", []))
            body = b"".join(body_parts)
            status = int(start.get("status", 200))
            if 200 <= status < 300:
                body, changed = _rewrite_response_body(
                    body,
                    _content_type(headers),
                )
                if changed:
                    start["headers"] = _with_content_length(headers, len(body))

            await send(start)
            await send(
                {
                    "type": "http.response.body",
                    "body": body,
                    "more_body": False,
                }
            )

        await self.app(scope, receive, capture)


__all__ = [
    "HiMatDirectResultCompatibilityMiddleware",
    "rewrite_himat_jsonrpc_payload",
]

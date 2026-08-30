"""Short-lived capability URLs for locally persisted WF-02 exports."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from collections.abc import Mapping
from urllib.parse import parse_qs, urlencode, urlsplit


WF02_EXPORT_PATH_PATTERN = re.compile(
    r"/v1/wf02/tasks/"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}/"
    r"exports/(?:html|csv|pdf)"
)
WF02_ATTACHMENT_URL_TTL_SECONDS = 24 * 60 * 60
WF02_ATTACHMENT_SIGNATURE_VERSION = "wf02-export-v1"


def _as_bool(value: str | None, *, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _ttl_seconds(environ: Mapping[str, str]) -> int:
    raw = str(
        environ.get(
            "WF02_ATTACHMENT_URL_TTL_SECONDS",
            WF02_ATTACHMENT_URL_TTL_SECONDS,
        )
    ).strip()
    try:
        value = int(raw)
    except ValueError:
        return WF02_ATTACHMENT_URL_TTL_SECONDS
    return min(max(value, 300), 7 * 24 * 60 * 60)


def _signature(secret: str, path: str, expires: int) -> str:
    payload = (
        f"{WF02_ATTACHMENT_SIGNATURE_VERSION}\n{path}\n{expires}"
    ).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def wf02_export_path(batch_id: str, format_name: str) -> str:
    return f"/v1/wf02/tasks/{batch_id}/exports/{format_name}"


def build_wf02_attachment_url(
    batch_id: str,
    format_name: str,
    *,
    environ: Mapping[str, str] | None = None,
    now: int | None = None,
) -> str:
    """Build an absolute, expiring URL without moving the local file."""
    values = os.environ if environ is None else environ
    path = wf02_export_path(str(batch_id), str(format_name).lower())
    public_base_url = str(values.get("MCP_PUBLIC_BASE_URL") or "").strip().rstrip(
        "/"
    )
    public = urlsplit(public_base_url) if public_base_url else None
    absolute_base = (
        public_base_url
        if public
        and public.scheme in {"http", "https"}
        and bool(public.netloc)
        else ""
    )
    auth_required = _as_bool(
        values.get("MCP_AUTH_REQUIRED"),
        default=True,
    )
    if not auth_required:
        return f"{absolute_base}{path}" if absolute_base else path
    secret = str(
        values.get("MCP_JWT_SECRET") or values.get("JWT_SECRET") or ""
    )
    if not absolute_base or not secret:
        return path
    expires = int(time.time() if now is None else now) + _ttl_seconds(values)
    query = urlencode(
        {
            "expires": str(expires),
            "signature": _signature(secret, path, expires),
        }
    )
    return f"{absolute_base}{path}?{query}"


def verify_wf02_attachment_request(
    path: str,
    query_string: bytes | str,
    *,
    secret: str | None,
    method: str,
    now: int | None = None,
) -> bool:
    """Validate one GET/HEAD capability URL in constant time."""
    if str(method).upper() not in {"GET", "HEAD"}:
        return False
    if not WF02_EXPORT_PATH_PATTERN.fullmatch(str(path)):
        return False
    if not secret:
        return False
    raw_query = (
        query_string.decode("ascii", errors="ignore")
        if isinstance(query_string, bytes)
        else str(query_string)
    )
    values = parse_qs(raw_query, keep_blank_values=True)
    if set(values) != {"expires", "signature"}:
        return False
    if len(values["expires"]) != 1 or len(values["signature"]) != 1:
        return False
    try:
        expires = int(values["expires"][0])
    except (TypeError, ValueError):
        return False
    current = int(time.time() if now is None else now)
    if expires < current:
        return False
    supplied = values["signature"][0]
    expected = _signature(secret, str(path), expires)
    return hmac.compare_digest(supplied, expected)

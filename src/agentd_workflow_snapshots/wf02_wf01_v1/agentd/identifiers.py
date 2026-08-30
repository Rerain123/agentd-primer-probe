"""Stable internal identifiers and concise user-facing task codes."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID


BEIJING_TIMEZONE = timezone(timedelta(hours=8))


def wf01_batch_code(
    batch_id: str,
    created_at: str | None,
    *,
    workflow: str = "WF-01",
) -> str:
    """Return a readable code without replacing the UUID persistence key."""
    try:
        parsed = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        created = parsed.astimezone(BEIJING_TIMEZONE)
    except (TypeError, ValueError):
        created = datetime.now(BEIJING_TIMEZONE)

    try:
        suffix = UUID(str(batch_id)).hex[:4].upper()
    except (TypeError, ValueError, AttributeError):
        suffix = hashlib.sha256(str(batch_id).encode("utf-8")).hexdigest()[:4].upper()

    workflow_code = re.sub(r"[^A-Z0-9]", "", str(workflow).upper()) or "WF01"
    return f"{workflow_code}-{created:%y%m%d-%H%M}-{suffix}"


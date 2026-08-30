"""Stable identifiers owned by the WF-05 runtime."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID


BEIJING_TIMEZONE = timezone(timedelta(hours=8))


def wf05_task_code(job_id: str, created_at: str | None) -> str:
    try:
        parsed = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        created = parsed.astimezone(BEIJING_TIMEZONE)
    except (TypeError, ValueError):
        created = datetime.now(BEIJING_TIMEZONE)
    try:
        suffix = UUID(str(job_id)).hex[:4].upper()
    except (TypeError, ValueError, AttributeError):
        suffix = hashlib.sha256(str(job_id).encode("utf-8")).hexdigest()[:4].upper()
    return f"WF05-{created:%y%m%d-%H%M}-{suffix}"


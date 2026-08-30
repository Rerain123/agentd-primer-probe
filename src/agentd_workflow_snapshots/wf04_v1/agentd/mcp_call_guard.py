"""Small process-local guards for accidental duplicate MCP tool calls."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from typing import Any


class RecentToolCallGuard:
    """Claim a caller/payload key once within a short time window.

    The guard is deliberately scoped by an authenticated caller and, when the
    MCP client supplies one, a conversation-turn key. Anonymous calls are not
    deduplicated because sharing a process-wide anonymous key could suppress a
    different user's request.
    """

    def __init__(
        self,
        ttl_seconds: float,
        *,
        max_entries: int = 4096,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self.max_entries = max(32, int(max_entries))
        self._clock = clock
        self._entries: dict[tuple[str, str, str], float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _payload_digest(payload: Any) -> str:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()

    def claim(
        self,
        operation: str,
        *,
        caller_scope: str | None,
        payload: Any,
    ) -> bool:
        """Return ``True`` for the first call and ``False`` for a duplicate."""
        if self.ttl_seconds <= 0 or not caller_scope:
            return True

        now = self._clock()
        key = (operation, caller_scope, self._payload_digest(payload))
        with self._lock:
            expires_at = self._entries.get(key)
            if expires_at is not None and expires_at > now:
                return False

            self._entries[key] = now + self.ttl_seconds
            if len(self._entries) > self.max_entries:
                self._entries = {
                    entry_key: expiry
                    for entry_key, expiry in self._entries.items()
                    if expiry > now
                }
                if len(self._entries) > self.max_entries:
                    oldest = sorted(
                        self._entries,
                        key=self._entries.__getitem__,
                    )[: len(self._entries) - self.max_entries]
                    for entry_key in oldest:
                        self._entries.pop(entry_key, None)
        return True

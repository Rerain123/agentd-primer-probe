from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.mcp_call_guard import RecentToolCallGuard


class RecentToolCallGuardTests(unittest.TestCase):
    def test_duplicate_expires_and_scopes_do_not_collide(self) -> None:
        now = [100.0]
        guard = RecentToolCallGuard(10, clock=lambda: now[0])
        payload = {"designInput": {"host": "猪"}}

        self.assertTrue(
            guard.claim("prepare", caller_scope="user-1|turn-1", payload=payload)
        )
        self.assertFalse(
            guard.claim("prepare", caller_scope="user-1|turn-1", payload=payload)
        )
        self.assertTrue(
            guard.claim("prepare", caller_scope="user-1|turn-2", payload=payload)
        )
        self.assertTrue(
            guard.claim("prepare", caller_scope="user-2|turn-1", payload=payload)
        )
        self.assertTrue(
            guard.claim(
                "prepare",
                caller_scope="user-1|turn-1",
                payload={"designInput": {"host": "鸡"}},
            )
        )

        now[0] += 10
        self.assertTrue(
            guard.claim("prepare", caller_scope="user-1|turn-1", payload=payload)
        )

    def test_anonymous_and_disabled_guards_never_suppress(self) -> None:
        payload = {"value": 1}
        anonymous_guard = RecentToolCallGuard(10)
        self.assertTrue(
            anonymous_guard.claim("prepare", caller_scope=None, payload=payload)
        )
        self.assertTrue(
            anonymous_guard.claim("prepare", caller_scope=None, payload=payload)
        )

        disabled_guard = RecentToolCallGuard(0)
        self.assertTrue(
            disabled_guard.claim("prepare", caller_scope="user", payload=payload)
        )
        self.assertTrue(
            disabled_guard.claim("prepare", caller_scope="user", payload=payload)
        )


if __name__ == "__main__":
    unittest.main()

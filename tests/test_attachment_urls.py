from __future__ import annotations

import unittest
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.attachment_urls import (
    build_wf02_attachment_url,
    verify_wf02_attachment_request,
)


class WF02AttachmentURLTests(unittest.TestCase):
    secret = "test-only-secret-with-at-least-thirty-two-bytes"
    batch_id = "45558ea7-a3c0-4bfa-bc46-1a895374a6a6"

    def _environment(self) -> dict[str, str]:
        return {
            "MCP_PUBLIC_BASE_URL": "https://oligosmith.example.test",
            "MCP_AUTH_REQUIRED": "true",
            "MCP_JWT_SECRET": self.secret,
            "WF02_ATTACHMENT_URL_TTL_SECONDS": "600",
        }

    def test_builds_absolute_expiring_url_and_validates_get_or_head(self) -> None:
        url = build_wf02_attachment_url(
            self.batch_id,
            "html",
            environ=self._environment(),
            now=1_000,
        )
        parsed = urlsplit(url)

        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "oligosmith.example.test")
        self.assertEqual(
            parsed.path,
            f"/v1/wf02/tasks/{self.batch_id}/exports/html",
        )
        self.assertNotIn(self.secret, url)
        self.assertTrue(
            verify_wf02_attachment_request(
                parsed.path,
                parsed.query,
                secret=self.secret,
                method="GET",
                now=1_001,
            )
        )
        self.assertTrue(
            verify_wf02_attachment_request(
                parsed.path,
                parsed.query,
                secret=self.secret,
                method="HEAD",
                now=1_600,
            )
        )

    def test_rejects_expired_tampered_or_non_read_requests(self) -> None:
        url = build_wf02_attachment_url(
            self.batch_id,
            "pdf",
            environ=self._environment(),
            now=2_000,
        )
        parsed = urlsplit(url)

        self.assertFalse(
            verify_wf02_attachment_request(
                parsed.path,
                parsed.query,
                secret=self.secret,
                method="GET",
                now=2_601,
            )
        )
        self.assertFalse(
            verify_wf02_attachment_request(
                parsed.path.replace("/pdf", "/csv"),
                parsed.query,
                secret=self.secret,
                method="GET",
                now=2_001,
            )
        )
        self.assertFalse(
            verify_wf02_attachment_request(
                parsed.path,
                parsed.query,
                secret="wrong-secret",
                method="GET",
                now=2_001,
            )
        )
        self.assertFalse(
            verify_wf02_attachment_request(
                parsed.path,
                parsed.query,
                secret=self.secret,
                method="POST",
                now=2_001,
            )
        )

    def test_falls_back_without_signing_when_configuration_is_incomplete(
        self,
    ) -> None:
        relative = build_wf02_attachment_url(
            self.batch_id,
            "csv",
            environ={"MCP_AUTH_REQUIRED": "true"},
            now=3_000,
        )
        self.assertEqual(
            relative,
            f"/v1/wf02/tasks/{self.batch_id}/exports/csv",
        )

        public = build_wf02_attachment_url(
            self.batch_id,
            "csv",
            environ={
                "MCP_AUTH_REQUIRED": "false",
                "MCP_PUBLIC_BASE_URL": "https://oligosmith.example.test",
            },
            now=3_000,
        )
        self.assertEqual(
            public,
            "https://oligosmith.example.test"
            f"/v1/wf02/tasks/{self.batch_id}/exports/csv",
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

from agentd.errors import PersistenceError
from agentd.object_storage import RustFSArtifactPublisher, RustFSSettings
from agentd.service import AgentDService


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict] = {}
        self.head_buckets: list[str] = []
        self.deleted: list[tuple[str, str]] = []

    def put_object(self, **kwargs):
        body = kwargs["Body"]
        content = body if isinstance(body, bytes) else body.read()
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = {
            **kwargs,
            "Body": content,
        }
        return {"ETag": '"fixture-etag"', "VersionId": "fixture-version"}

    def head_object(self, *, Bucket: str, Key: str):
        value = self.objects[(Bucket, Key)]
        return {
            "ContentLength": len(value["Body"]),
            "Metadata": value["Metadata"],
        }

    def head_bucket(self, *, Bucket: str):
        self.head_buckets.append(Bucket)
        return {}

    def delete_object(self, *, Bucket: str, Key: str):
        self.deleted.append((Bucket, Key))
        self.objects.pop((Bucket, Key), None)
        return {}


class RustFSArtifactPublisherTests(unittest.TestCase):
    @staticmethod
    def settings() -> RustFSSettings:
        return RustFSSettings(
            enabled=True,
            endpoint_url="https://rustfs.ne.scqcyz.cn",
            access_key_id="fixture-access-key",
            secret_access_key="fixture-secret-key",
            bucket="wens",
            object_prefix="data",
            public_base_url="https://rustfs.ne.scqcyz.cn",
        )

    def test_disabled_by_default(self) -> None:
        publisher = RustFSArtifactPublisher.from_environment({})
        self.assertFalse(publisher.enabled)
        self.assertIsNone(publisher.publish(
            Path("not-needed.html"),
            batch_id="batch-id",
            media_type="text/html",
            sha256="abc",
        ))

    def test_enabled_configuration_requires_credentials(self) -> None:
        with self.assertRaises(PersistenceError) as raised:
            RustFSArtifactPublisher.from_environment(
                {
                    "RUSTFS_ENABLED": "true",
                    "RUSTFS_ENDPOINT_URL": "https://rustfs.ne.scqcyz.cn",
                    "RUSTFS_BUCKET": "wens",
                }
            )
        self.assertIn(
            "RUSTFS_ACCESS_KEY_ID",
            raised.exception.details["missing_variables"],
        )
        self.assertIn(
            "RUSTFS_SECRET_ACCESS_KEY",
            raised.exception.details["missing_variables"],
        )

    def test_public_base_url_must_be_http(self) -> None:
        with self.assertRaises(PersistenceError):
            RustFSArtifactPublisher(
                RustFSSettings(
                    **{
                        **self.settings().__dict__,
                        "public_base_url": "file:///other",
                    }
                )
            )

    def test_publish_uses_wens_data_key_and_verifies_metadata(self) -> None:
        fake = FakeS3Client()
        publisher = RustFSArtifactPublisher(self.settings(), client=fake)
        with tempfile.TemporaryDirectory(prefix="agentd-rustfs-") as temp_dir:
            report = Path(temp_dir) / "2026-08-08_10-30-00_引物探针设计报告.html"
            report.write_bytes(b"<!doctype html><title>fixture</title>")
            result = publisher.publish(
                report,
                batch_id="batch-fixture",
                media_type="text/html; charset=utf-8",
                sha256="fixture-sha256",
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["bucket"], "wens")
        self.assertEqual(
            result["object_key"],
            "data/2026-08-08_10-30-00_引物探针设计报告.html",
        )
        self.assertEqual(
            result["file_url"],
            (
                "https://rustfs.ne.scqcyz.cn/wens/data/"
                + quote(report.name, safe="")
            ),
        )
        self.assertNotIn("引物探针设计报告", result["file_url"])
        uploaded = fake.objects[("wens", result["object_key"])]
        self.assertEqual(uploaded["Metadata"]["sha256"], "fixture-sha256")
        self.assertEqual(uploaded["Metadata"]["batch-id"], "batch-fixture")
        self.assertEqual(uploaded["CacheControl"], "no-store")
        self.assertIn("filename*=UTF-8''", uploaded["ContentDisposition"])
        self.assertEqual(publisher.probe()["status"], "ready")
        self.assertEqual(fake.head_buckets, ["wens"])
        write_result = publisher.probe_write()
        self.assertTrue(write_result["write_verified"])
        self.assertTrue(write_result["test_object_deleted"])
        self.assertEqual(len(fake.deleted), 1)
        self.assertNotIn(fake.deleted[0], fake.objects)

    def test_publish_can_use_batch_scoped_object_name(self) -> None:
        fake = FakeS3Client()
        publisher = RustFSArtifactPublisher(self.settings(), client=fake)
        with tempfile.TemporaryDirectory(prefix="agentd-rustfs-object-name-") as temp_dir:
            report = Path(temp_dir) / "wf02-multiplex-report.pdf"
            report.write_bytes(b"%PDF-1.7\n")
            result = publisher.publish(
                report,
                batch_id="batch-fixture",
                media_type="application/pdf",
                sha256="fixture-sha256",
                object_name="wf02-batch-fixture-multiplex-report.pdf",
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            result["object_key"],
            "data/wf02-batch-fixture-multiplex-report.pdf",
        )
        self.assertEqual(
            result["file_url"],
            "https://rustfs.ne.scqcyz.cn/wens/data/"
            "wf02-batch-fixture-multiplex-report.pdf",
        )

    def test_capabilities_report_configured_attachment_location(self) -> None:
        configured = RustFSSettings(
            **{
                **self.settings().__dict__,
                "bucket": "oligosmith-v2",
                "object_prefix": "reports/custom",
                "public_base_url": "https://objects.example.test/storage",
            }
        )
        publisher = RustFSArtifactPublisher(configured, client=FakeS3Client())

        with tempfile.TemporaryDirectory(
            prefix="agentd-rustfs-capabilities-"
        ) as state_dir:
            capabilities = AgentDService(
                state_dir=state_dir,
                artifact_publisher=publisher,
            ).capabilities()

        publication = capabilities["wf01_prd_v0_2"][
            "attachment_publication"
        ]
        self.assertEqual(
            publication["public_base_url"],
            "https://objects.example.test/storage",
        )
        self.assertEqual(publication["bucket"], "oligosmith-v2")
        self.assertEqual(publication["object_prefix"], "reports/custom")


if __name__ == "__main__":
    unittest.main()

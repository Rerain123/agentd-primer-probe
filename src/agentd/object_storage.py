"""RustFS-backed publication for user-visible workflow artifacts.

The workflow keeps its canonical and rendered files on the local persistent
volume.  This module publishes a second copy to the shared RustFS bucket so a
HiMat backend running outside the Agent D container can resolve the attachment.
"""

from __future__ import annotations

import os
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlsplit
from uuid import uuid4

from .errors import PersistenceError


def _as_bool(value: str | None, *, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise PersistenceError(
        "RustFS 布尔配置无效",
        details={"value": value},
    )


def _positive_int(value: str | None, *, default: int, name: str) -> int:
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise PersistenceError(
            "RustFS 超时配置无效",
            details={"variable": name},
        ) from exc
    if parsed <= 0:
        raise PersistenceError(
            "RustFS 超时配置必须为正整数",
            details={"variable": name},
        )
    return parsed


@dataclass(frozen=True)
class RustFSSettings:
    enabled: bool
    endpoint_url: str = ""
    access_key_id: str = ""
    secret_access_key: str = ""
    bucket: str = ""
    region: str = "us-east-1"
    object_prefix: str = "data"
    public_base_url: str = ""
    verify_tls: bool = True
    connect_timeout_seconds: int = 5
    read_timeout_seconds: int = 30


class RustFSArtifactPublisher:
    """Publish generated files through RustFS' S3-compatible API."""

    def __init__(
        self,
        settings: RustFSSettings,
        *,
        client: Any | None = None,
    ) -> None:
        self.settings = settings
        self.enabled = settings.enabled
        self._client = client
        if self.enabled:
            self._validate_settings()

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "RustFSArtifactPublisher":
        values = os.environ if environ is None else environ
        enabled = _as_bool(values.get("RUSTFS_ENABLED"), default=False)
        endpoint_url = values.get("RUSTFS_ENDPOINT_URL", "").strip().rstrip("/")
        settings = RustFSSettings(
            enabled=enabled,
            endpoint_url=endpoint_url,
            access_key_id=values.get("RUSTFS_ACCESS_KEY_ID", "").strip(),
            secret_access_key=values.get("RUSTFS_SECRET_ACCESS_KEY", "").strip(),
            bucket=values.get("RUSTFS_BUCKET", "").strip(),
            region=values.get("RUSTFS_REGION", "us-east-1").strip()
            or "us-east-1",
            object_prefix=values.get("RUSTFS_OBJECT_PREFIX", "data").strip("/ "),
            public_base_url=(
                values.get("RUSTFS_PUBLIC_BASE_URL", "").strip().rstrip("/")
                or endpoint_url
            ),
            verify_tls=_as_bool(
                values.get("RUSTFS_VERIFY_TLS"), default=True
            ),
            connect_timeout_seconds=_positive_int(
                values.get("RUSTFS_CONNECT_TIMEOUT_SECONDS"),
                default=5,
                name="RUSTFS_CONNECT_TIMEOUT_SECONDS",
            ),
            read_timeout_seconds=_positive_int(
                values.get("RUSTFS_READ_TIMEOUT_SECONDS"),
                default=30,
                name="RUSTFS_READ_TIMEOUT_SECONDS",
            ),
        )
        return cls(settings)

    def _validate_settings(self) -> None:
        missing = [
            name
            for name, value in (
                ("RUSTFS_ENDPOINT_URL", self.settings.endpoint_url),
                ("RUSTFS_ACCESS_KEY_ID", self.settings.access_key_id),
                ("RUSTFS_SECRET_ACCESS_KEY", self.settings.secret_access_key),
                ("RUSTFS_BUCKET", self.settings.bucket),
                ("RUSTFS_OBJECT_PREFIX", self.settings.object_prefix),
                ("RUSTFS_PUBLIC_BASE_URL", self.settings.public_base_url),
            )
            if not value
        ]
        if missing:
            raise PersistenceError(
                "RustFS 已启用但配置不完整",
                details={"missing_variables": missing},
            )
        endpoint = urlsplit(self.settings.endpoint_url)
        if (
            endpoint.scheme not in {"http", "https"}
            or not endpoint.hostname
            or endpoint.path not in {"", "/"}
            or endpoint.query
            or endpoint.fragment
        ):
            raise PersistenceError(
                "RUSTFS_ENDPOINT_URL 必须是不带路径的 HTTP(S) 地址",
                details={"endpoint_url": self.settings.endpoint_url},
            )
        if "/" in self.settings.bucket:
            raise PersistenceError(
                "RUSTFS_BUCKET 不能包含路径分隔符",
                details={"bucket": self.settings.bucket},
            )
        public = urlsplit(self.settings.public_base_url)
        if (
            public.scheme not in {"http", "https"}
            or not public.hostname
            or public.query
            or public.fragment
        ):
            raise PersistenceError(
                "RUSTFS_PUBLIC_BASE_URL 必须是 HTTP(S) 地址",
                details={"public_base_url": self.settings.public_base_url},
            )

    def _get_client(self) -> Any:
        if not self.enabled:
            return None
        if self._client is not None:
            return self._client
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - deployment boundary
            raise PersistenceError(
                "RustFS 已启用但 boto3 未安装",
                details={"dependency": "boto3"},
            ) from exc
        self._client = boto3.client(
            "s3",
            endpoint_url=self.settings.endpoint_url,
            aws_access_key_id=self.settings.access_key_id,
            aws_secret_access_key=self.settings.secret_access_key,
            region_name=self.settings.region,
            verify=self.settings.verify_tls,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=self.settings.connect_timeout_seconds,
                read_timeout=self.settings.read_timeout_seconds,
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )
        return self._client

    def object_key(self, filename: str) -> str:
        if not filename or Path(filename).name != filename:
            raise PersistenceError(
                "RustFS 附件文件名无效",
                details={"filename": filename},
            )
        return f"{self.settings.object_prefix}/{filename}"

    def file_url(self, filename: str) -> str:
        if not filename or Path(filename).name != filename:
            raise PersistenceError(
                "RustFS 附件文件名无效",
                details={"filename": filename},
            )
        bucket = quote(self.settings.bucket, safe="")
        key = quote(self.object_key(filename), safe="/")
        return f"{self.settings.public_base_url}/{bucket}/{key}"

    def publish(
        self,
        path: Path,
        *,
        batch_id: str,
        media_type: str,
        sha256: str,
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        if not path.is_file():
            raise PersistenceError(
                "RustFS 待上传附件不存在",
                details={"filename": path.name},
            )
        key = self.object_key(path.name)
        size = path.stat().st_size
        disposition = "inline" if path.suffix.lower() in {".html", ".pdf"} else "attachment"
        disposition = (
            f"{disposition}; filename*=UTF-8''{quote(path.name, safe='')}"
        )
        client = self._get_client()
        try:
            with path.open("rb") as stream:
                response = client.put_object(
                    Bucket=self.settings.bucket,
                    Key=key,
                    Body=stream,
                    ContentLength=size,
                    ContentType=media_type,
                    ContentDisposition=disposition,
                    CacheControl="no-store",
                    Metadata={
                        "batch-id": batch_id,
                        "sha256": sha256,
                    },
                )
            uploaded = client.head_object(
                Bucket=self.settings.bucket,
                Key=key,
            )
        except Exception as exc:
            raise PersistenceError(
                "报告上传 RustFS 失败",
                details={
                    "endpoint_url": self.settings.endpoint_url,
                    "bucket": self.settings.bucket,
                    "object_key": key,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                },
            ) from exc
        uploaded_size = int(uploaded.get("ContentLength", -1))
        uploaded_sha256 = str((uploaded.get("Metadata") or {}).get("sha256") or "")
        if uploaded_size != size or uploaded_sha256 != sha256:
            raise PersistenceError(
                "RustFS 上传后完整性校验失败",
                details={
                    "bucket": self.settings.bucket,
                    "object_key": key,
                    "expected_size": size,
                    "actual_size": uploaded_size,
                    "sha256_metadata_matches": uploaded_sha256 == sha256,
                },
            )
        return {
            "provider": "rustfs",
            "endpoint_url": self.settings.endpoint_url,
            "bucket": self.settings.bucket,
            "object_key": key,
            "etag": str(response.get("ETag") or "").strip('"') or None,
            "version_id": response.get("VersionId"),
            "size_bytes": size,
            "sha256": sha256,
            "file_url": self.file_url(path.name),
            "published_at": datetime.now(timezone.utc).isoformat(),
        }

    def status(self) -> dict[str, Any]:
        return {
            "available": self.enabled,
            "enabled": self.enabled,
            "configured": self.enabled,
            "implementation": "rustfs-s3" if self.enabled else "disabled",
            "endpoint_url": self.settings.endpoint_url or None,
            "bucket": self.settings.bucket or None,
            "object_prefix": self.settings.object_prefix or None,
            "public_base_url": self.settings.public_base_url or None,
            "tls_verification": self.settings.verify_tls,
        }

    def probe(self) -> dict[str, Any]:
        """Verify credentials and bucket access without reading object data."""
        if not self.enabled:
            return {"status": "disabled", **self.status()}
        try:
            self._get_client().head_bucket(Bucket=self.settings.bucket)
        except Exception as exc:
            raise PersistenceError(
                "RustFS 存储桶连通性检查失败",
                details={
                    "endpoint_url": self.settings.endpoint_url,
                    "bucket": self.settings.bucket,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                },
            ) from exc
        return {"status": "ready", **self.status()}

    def probe_write(self) -> dict[str, Any]:
        """Create, verify, and remove one uniquely named connectivity object."""
        if not self.enabled:
            return {"status": "disabled", **self.status()}
        client = self._get_client()
        payload = b"agentd-rustfs-write-check\n"
        digest = hashlib.sha256(payload).hexdigest()
        filename = f"_agentd-write-check-{uuid4().hex}.txt"
        key = self.object_key(filename)
        created = False
        try:
            client.put_object(
                Bucket=self.settings.bucket,
                Key=key,
                Body=payload,
                ContentLength=len(payload),
                ContentType="text/plain; charset=utf-8",
                CacheControl="no-store",
                Metadata={"purpose": "agentd-write-check", "sha256": digest},
            )
            created = True
            uploaded = client.head_object(
                Bucket=self.settings.bucket,
                Key=key,
            )
            if (
                int(uploaded.get("ContentLength", -1)) != len(payload)
                or str((uploaded.get("Metadata") or {}).get("sha256") or "")
                != digest
            ):
                raise PersistenceError(
                    "RustFS 写入验收对象完整性校验失败",
                    details={"bucket": self.settings.bucket, "object_key": key},
                )
        except PersistenceError:
            raise
        except Exception as exc:
            raise PersistenceError(
                "RustFS 写入权限检查失败",
                details={
                    "endpoint_url": self.settings.endpoint_url,
                    "bucket": self.settings.bucket,
                    "object_key": key,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                },
            ) from exc
        finally:
            if created:
                try:
                    client.delete_object(Bucket=self.settings.bucket, Key=key)
                except Exception as exc:
                    raise PersistenceError(
                        "RustFS 写入验收对象清理失败",
                        details={
                            "bucket": self.settings.bucket,
                            "object_key": key,
                            "error_type": type(exc).__name__,
                        },
                    ) from exc
        return {
            "status": "ready",
            "write_verified": True,
            "test_object_deleted": True,
            **self.status(),
        }

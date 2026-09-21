"""
shared/services/storage_client.py — Async Object Storage Client (Sprint 16)

Wraps boto3 / aioboto3 to provide async uploads to MinIO (dev) or AWS S3
(production) with a single, unified interface.

Design goals:
  - Zero crashes if MinIO is unreachable — errors are caught and logged.
  - MinIO presigned URLs (7-day TTL) work without making the bucket public.
  - AWS S3 production path is identical — just remove `endpoint_url`.
  - `ensure_bucket_exists()` is idempotent and safe to call on every startup.
  - `upload_audio_bytes()` is the primary entry point for TTS audio.
  - Pure async via `aioboto3`; falls back to `boto3` + `asyncio.to_thread()`
    if `aioboto3` is not installed (so dev can pip-install incrementally).

Configuration (.env):
    MINIO_ENDPOINT=http://localhost:9020   ← MinIO API port
    MINIO_ACCESS_KEY=minioadmin
    MINIO_SECRET_KEY=minioadmin
    MINIO_BUCKET_NAME=omniflow-media

    For AWS S3 production, leave MINIO_ENDPOINT empty and set:
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, S3_ASSETS_BUCKET

References: Sprint 16 spec; AWS S3 presigned URL docs; MinIO Docker Compose
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog

from src.shared.core.config import get_settings

logger = structlog.get_logger(__name__)
_settings = get_settings()

# ── Presigned URL TTL ──────────────────────────────────────────────────────────
_PRESIGNED_EXPIRY_SECONDS: int = 7 * 24 * 3600   # 7 days


# ══════════════════════════════════════════════════════════════════════════════
# StorageClient
# ══════════════════════════════════════════════════════════════════════════════

class StorageClient:
    """
    Async S3-compatible object storage client.

    Supports:
        - MinIO (local Docker, dev/staging)   — set MINIO_ENDPOINT
        - AWS S3 (production)                 — unset MINIO_ENDPOINT

    Usage:
        # On worker startup:
        await storage_client.ensure_bucket_exists()

        # In TTS pipeline:
        url = await storage_client.upload_audio_bytes(audio_bytes, "tts_abc123.mp3")
    """

    def __init__(self) -> None:
        # Resolved once on first use to avoid import-time side effects
        self._bucket: str = _settings.minio_bucket_name
        self._endpoint: str | None = _settings.minio_endpoint or None
        self._access_key: str = _settings.minio_access_key
        self._secret_key: str = _settings.minio_secret_key
        self._region: str = _settings.aws_region
        self._use_aioboto3: bool = self._detect_aioboto3()

    @staticmethod
    def _detect_aioboto3() -> bool:
        """Return True if aioboto3 is available in the environment."""
        try:
            import aioboto3  # noqa: F401
            return True
        except ImportError:
            return False

    @property
    def is_configured(self) -> bool:
        """True if at least an access key is set (not a blank placeholder)."""
        return bool(self._access_key and self._access_key not in ("", "mock"))

    def _make_boto3_kwargs(self) -> dict[str, Any]:
        """Build the keyword arguments shared by both boto3 and aioboto3."""
        kwargs: dict[str, Any] = {
            "aws_access_key_id":     self._access_key,
            "aws_secret_access_key": self._secret_key,
            "region_name":           self._region,
        }
        if self._endpoint:
            kwargs["endpoint_url"] = self._endpoint
        return kwargs

    # ── Startup helper ─────────────────────────────────────────────────────────

    async def ensure_bucket_exists(self) -> None:
        """
        Create the target bucket if it does not already exist.

        Idempotent — safe to call on every worker startup.
        MinIO returns 200 for HeadBucket on existing buckets so no special
        handling is needed for the race condition.

        Swallows all errors so that a missing/unreachable MinIO does NOT
        prevent the rest of the application from starting.
        """
        if not self.is_configured:
            logger.warning(
                "storage_bucket_check_skipped",
                reason="MINIO_ACCESS_KEY not configured",
                bucket=self._bucket,
            )
            return

        try:
            if self._use_aioboto3:
                await self._ensure_bucket_aioboto3()
            else:
                await asyncio.to_thread(self._ensure_bucket_sync)
        except Exception as exc:
            # Non-fatal — log loudly but don't crash startup
            logger.error(
                "storage_ensure_bucket_failed",
                bucket=self._bucket,
                error=str(exc),
                exc_type=type(exc).__name__,
            )

    async def _ensure_bucket_aioboto3(self) -> None:
        import aioboto3  # type: ignore[import]

        session = aioboto3.Session()
        async with session.client("s3", **self._make_boto3_kwargs()) as s3:
            try:
                await s3.head_bucket(Bucket=self._bucket)
                logger.debug("storage_bucket_exists", bucket=self._bucket)
            except Exception:
                # BucketNotFound or similar — create it
                try:
                    if self._region and self._region != "us-east-1":
                        await s3.create_bucket(
                            Bucket=self._bucket,
                            CreateBucketConfiguration={"LocationConstraint": self._region},
                        )
                    else:
                        await s3.create_bucket(Bucket=self._bucket)
                    logger.info("storage_bucket_created", bucket=self._bucket)
                except Exception as create_exc:
                    # May already exist (race condition) — ignore
                    logger.debug(
                        "storage_bucket_create_skipped",
                        bucket=self._bucket,
                        reason=str(create_exc),
                    )

    def _ensure_bucket_sync(self) -> None:
        import boto3  # type: ignore[import]

        s3 = boto3.client("s3", **self._make_boto3_kwargs())
        try:
            s3.head_bucket(Bucket=self._bucket)
            logger.debug("storage_bucket_exists", bucket=self._bucket)
        except Exception:
            try:
                if self._region and self._region != "us-east-1":
                    s3.create_bucket(
                        Bucket=self._bucket,
                        CreateBucketConfiguration={"LocationConstraint": self._region},
                    )
                else:
                    s3.create_bucket(Bucket=self._bucket)
                logger.info("storage_bucket_created", bucket=self._bucket)
            except Exception as create_exc:
                logger.debug(
                    "storage_bucket_create_skipped",
                    bucket=self._bucket,
                    reason=str(create_exc),
                )

    # ── Upload ─────────────────────────────────────────────────────────────────

    async def upload_audio_bytes(
        self,
        file_bytes: bytes,
        filename: str,
        *,
        content_type: str = "audio/mpeg",
    ) -> str:
        """
        Upload raw audio bytes to the configured object storage.

        Args:
            file_bytes   — Raw audio content (MP3, OGG, etc.)
            filename     — Object key suffix (e.g. "tts_abc123.mp3").
                           Full key will be: "audio/{filename}"
            content_type — MIME type (default: "audio/mpeg")

        Returns:
            A presigned HTTPS URL valid for 7 days (MinIO / S3).
            Falls back to _MOCK_AUDIO_URL if storage is unreachable.

        Raises:
            Never — all exceptions are caught and logged. Falls back to
            the mock URL so the TTS pipeline never crashes.
        """
        if not self.is_configured:
            logger.warning(
                "storage_upload_skipped",
                reason="Storage not configured — returning mock URL",
                filename=filename,
            )
            return _MOCK_FALLBACK_URL

        object_key = f"audio/{filename}"
        try:
            if self._use_aioboto3:
                url = await self._upload_aioboto3(file_bytes, object_key, content_type)
            else:
                url = await asyncio.to_thread(
                    self._upload_sync, file_bytes, object_key, content_type
                )
            logger.info(
                "storage_upload_success",
                object_key=object_key,
                size_bytes=len(file_bytes),
                url=url,
            )
            return url
        except Exception as exc:
            logger.error(
                "storage_upload_failed",
                object_key=object_key,
                error=str(exc),
                exc_type=type(exc).__name__,
                fallback=_MOCK_FALLBACK_URL,
            )
            return _MOCK_FALLBACK_URL

    async def _upload_aioboto3(
        self, file_bytes: bytes, object_key: str, content_type: str
    ) -> str:
        import aioboto3  # type: ignore[import]

        session = aioboto3.Session()
        async with session.client("s3", **self._make_boto3_kwargs()) as s3:
            await s3.put_object(
                Bucket=self._bucket,
                Key=object_key,
                Body=file_bytes,
                ContentType=content_type,
            )
            # Generate a 7-day presigned URL
            url: str = await s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": object_key},
                ExpiresIn=_PRESIGNED_EXPIRY_SECONDS,
            )
        return url

    def _upload_sync(
        self, file_bytes: bytes, object_key: str, content_type: str
    ) -> str:
        import boto3  # type: ignore[import]

        s3 = boto3.client("s3", **self._make_boto3_kwargs())
        s3.put_object(
            Bucket=self._bucket,
            Key=object_key,
            Body=file_bytes,
            ContentType=content_type,
        )
        url: str = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": object_key},
            ExpiresIn=_PRESIGNED_EXPIRY_SECONDS,
        )
        return url

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def generate_audio_filename(
        synthesis_id: str,
        tenant_id: str | None = None,
    ) -> str:
        """
        Build a unique, tenant-scoped filename for a TTS audio object.

        Format: tts/{tenant_id}/{synthesis_id}.mp3
        Falls back to tts/unknown/{synthesis_id}.mp3 when tenant_id is absent.
        """
        scope = tenant_id.replace("-", "") if tenant_id else "unknown"
        return f"tts/{scope}/{synthesis_id}.mp3"


# ── Mock fallback URL ──────────────────────────────────────────────────────────
# Used when storage is not configured or upload fails.
# This is a publicly hosted royalty-free MP3 — browsers and WhatsApp can stream it.
_MOCK_FALLBACK_URL: str = (
    "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3"
)

# ── Module-level singleton ─────────────────────────────────────────────────────
storage_client = StorageClient()

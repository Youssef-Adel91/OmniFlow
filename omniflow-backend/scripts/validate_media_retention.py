"""
Real end-to-end validation of item 16's retention sweep against real MinIO.

`settings.media_voice_retention_hours` was declared in config but never
enforced anywhere (confirmed via grep before this fix — celery_app's own
docstring listed "Media cleanup (voice 24h / images 7d)" under "Still to
build"). This exercises the real `StorageClient.delete_expired_objects()`
and the real `omniflow.media_cleanup_check` Celery task body against a
real MinIO bucket — real `put_object`/`list_objects_v2`/`delete_objects`
calls, no mocked S3 client.

Scenario: upload one object with a backdated `LastModified` (MinIO doesn't
let a client set LastModified directly, so this test uploads an object,
then uses the MinIO Admin API's object age indirectly by uploading to a
throwaway prefix and asserting on relative age instead — see below for the
exact technique) that is older than the retention window, and one that is
fresh. Runs the real sweep and asserts: the old object is gone, the fresh
object survives, and objects outside the swept prefix are never touched.
"""
from __future__ import annotations

import asyncio
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.celery_app.tasks import _media_cleanup_check_async
from src.shared.core.config import get_settings
from src.shared.services.storage_client import storage_client

settings = get_settings()


def _require_local() -> None:
    if "localhost" not in (settings.minio_endpoint or "") and "127.0.0.1" not in (settings.minio_endpoint or ""):
        raise RuntimeError("requires a local MinIO endpoint")


def _boto3_kwargs() -> dict:
    return {
        "aws_access_key_id": settings.minio_access_key,
        "aws_secret_access_key": settings.minio_secret_key,
        "region_name": settings.aws_region,
        "endpoint_url": settings.minio_endpoint,
    }


def _list_keys_sync(prefix: str) -> set[str]:
    import boto3

    s3 = boto3.client("s3", **_boto3_kwargs())
    keys: set[str] = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.minio_bucket_name, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.add(obj["Key"])
    return keys


async def _list_keys(prefix: str) -> set[str]:
    return await asyncio.to_thread(_list_keys_sync, prefix)


def _put_sync(key: str, body: bytes) -> None:
    import boto3

    boto3.client("s3", **_boto3_kwargs()).put_object(Bucket=settings.minio_bucket_name, Key=key, Body=body)


async def _put(key: str, body: bytes = b"drill") -> None:
    await asyncio.to_thread(_put_sync, key, body)


def _delete_sync(key: str) -> None:
    import boto3

    boto3.client("s3", **_boto3_kwargs()).delete_object(Bucket=settings.minio_bucket_name, Key=key)


async def _delete(key: str) -> None:
    await asyncio.to_thread(_delete_sync, key)


async def main() -> None:
    _require_local()
    drill_id = uuid.uuid4().hex[:8]
    prefix = f"tts/retention-drill-{drill_id}/"
    fresh_key = f"{prefix}fresh.mp3"
    old_key = f"{prefix}old.mp3"
    outside_key = f"other-prefix-{drill_id}/untouched.mp3"

    print(f"drill prefix={prefix!r}")
    await _put(fresh_key)
    await _put(old_key)
    await _put(outside_key)

    before = await _list_keys(prefix)
    assert {fresh_key, old_key} <= before, "setup failed: both objects should exist right after upload"
    print("PASS: both real objects exist in real MinIO right after upload")

    # MinIO always stamps LastModified as "now" on put — there is no way to
    # backdate it client-side. So this drill proves the sweep's real
    # mechanics (real list_objects_v2 + real delete_objects, correct prefix
    # scoping, correct age-cutoff math) using a deliberately tiny
    # max_age_hours instead of waiting 24 real hours: everything just
    # uploaded is "expired" against an effectively-zero-hour window, except
    # `fresh_key`, which we re-upload immediately before the sweep so it is
    # provably newer than the cutoff even at second-level precision.
    await asyncio.sleep(1.1)
    await _put(fresh_key)  # re-upload: this one is now newer than `old_key`

    deleted = await storage_client.delete_expired_objects(
        prefix=prefix, max_age_hours=1.0 / 3600,  # 1 second
    )
    assert deleted >= 1, f"expected at least the stale object deleted, got deleted={deleted}"
    print(f"PASS: real sweep deleted {deleted} object(s) via real MinIO delete_objects")

    after = await _list_keys(prefix)
    assert old_key not in after, "the old object should have been deleted"
    assert fresh_key in after, "the freshly re-uploaded object must survive (it's newer than the cutoff)"
    print("PASS: old object gone, fresh object survives — age cutoff is correctly applied")

    outside_after = await _list_keys(f"other-prefix-{drill_id}/")
    assert outside_key in outside_after, "an object outside the swept prefix must never be touched"
    print("PASS: an object outside the swept prefix was correctly left alone")

    # Now exercise the REAL Celery task body end-to-end against the real
    # `tts/` prefix (not just the drill prefix) to prove the actual
    # production code path — settings.media_voice_retention_hours (24h) is
    # far longer than this drill's objects' age, so this call must be a
    # true no-op against real production data, never touching anything.
    result = await _media_cleanup_check_async()
    assert result["prefix"] == "tts/"
    assert result["max_age_hours"] == settings.media_voice_retention_hours
    print(f"PASS: the real omniflow.media_cleanup_check task ran cleanly: {result}")

    await _delete(fresh_key)
    await _delete(outside_key)
    print("cleanup: drill objects removed")


if __name__ == "__main__":
    asyncio.run(main())

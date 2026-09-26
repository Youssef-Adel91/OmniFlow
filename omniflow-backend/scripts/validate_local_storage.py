"""Validate a private MinIO upload and signed download using one temporary object."""
import asyncio
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from botocore.exceptions import ClientError
from src.shared.core.config import get_settings
from src.shared.services.report_download import report_download_url
from src.shared.storage.s3 import s3_mgr


async def main():
    settings = get_settings()
    if urlsplit(settings.minio_endpoint).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("Storage validation requires local MinIO")
    bucket = settings.s3_vault_bucket
    report = SimpleNamespace(tenant_id=uuid.uuid4(), customer_id=uuid.uuid4(), s3_url=None)
    key = f"tenant-{report.tenant_id}/reports/{report.customer_id}/validation.txt"
    content = b"Synthetic private storage validation"
    uploaded = False
    try:
        async with s3_mgr.get_client() as client:
            try:
                await client.head_bucket(Bucket=bucket)
            except ClientError as exc:
                if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
                    raise
                await client.create_bucket(Bucket=bucket)
        report.s3_url = await s3_mgr.upload_file(bucket, key, content, content_type="text/plain")
        uploaded = True
        signed = await report_download_url(report)
        async with httpx.AsyncClient() as client:
            response = await client.get(signed)
            response.raise_for_status()
            assert response.content == content
            unsigned = await client.get(report.s3_url)
            assert unsigned.status_code in (401, 403), "Vault must not allow public object access"
        print("PASS local storage: private upload, signed download, unsigned access rejected")
    finally:
        if uploaded:
            await s3_mgr.delete_file(bucket, key)
            print("Temporary object deleted")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"Storage validation failed: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)

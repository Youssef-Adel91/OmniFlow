"""
shared/storage/s3.py — S3 / MinIO async client
"""
from __future__ import annotations

import contextlib
from typing import Any, AsyncGenerator

import aiobotocore.session

from src.shared.core.config import get_settings

settings = get_settings()

class S3ClientManager:
    def __init__(self):
        self.session = aiobotocore.session.get_session()

    @contextlib.asynccontextmanager
    async def get_client(self) -> AsyncGenerator[Any, None]:
        async with self.session.create_client(
            "s3",
            region_name=settings.aws_region,
            aws_access_key_id=settings.minio_access_key or settings.aws_access_key_id,
            aws_secret_access_key=settings.minio_secret_key or settings.aws_secret_access_key,
            endpoint_url=settings.minio_endpoint or settings.s3_endpoint_url,
        ) as client:
            yield client

    async def upload_file(self, bucket: str, key: str, file_data: bytes, content_type: str = "application/pdf") -> str:
        async with self.get_client() as client:
            await client.put_object(
                Bucket=bucket,
                Key=key,
                Body=file_data,
                ContentType=content_type,
            )
            # Return S3 URL
            endpoint = settings.minio_endpoint or settings.s3_endpoint_url or f"https://{bucket}.s3.{settings.aws_region}.amazonaws.com"
            return f"{endpoint}/{bucket}/{key}"

    async def download_file(self, bucket: str, key: str) -> bytes:
        """
        Fetch an object's full body into memory.

        Used by the Knowledge Base ingestion task. Callers are responsible for
        keeping objects small enough to hold in RAM — the upload endpoint caps
        knowledge documents at 20 MB for exactly this reason.
        """
        async with self.get_client() as client:
            response = await client.get_object(Bucket=bucket, Key=key)
            async with response["Body"] as stream:
                return await stream.read()

    async def delete_file(self, bucket: str, key: str) -> None:
        """Delete a single object. Silently succeeds if the key is absent."""
        async with self.get_client() as client:
            await client.delete_object(Bucket=bucket, Key=key)

    async def generate_presigned_url(self, bucket: str, key: str, expires_in: int = 900) -> str:
        async with self.get_client() as client:
            response = await client.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=expires_in,
            )
            return response

s3_mgr = S3ClientManager()

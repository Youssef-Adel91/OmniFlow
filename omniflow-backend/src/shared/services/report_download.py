"""Short-lived report downloads restricted to the owning tenant/customer path."""
from urllib.parse import unquote, urlsplit

from src.shared.core.config import get_settings
from src.shared.storage.s3 import s3_mgr


def report_object_key(report) -> str:
    settings = get_settings()
    stored = urlsplit(report.s3_url or "")
    bucket = settings.s3_vault_bucket
    if stored.scheme == "s3" and stored.netloc == bucket:
        key = unquote(stored.path.lstrip("/"))
    else:
        endpoint = settings.minio_endpoint or settings.s3_endpoint_url
        if endpoint:
            trusted = urlsplit(endpoint)
            prefix = trusted.path.rstrip("/") + "/" + bucket + "/"
            if (stored.scheme, stored.netloc) != (trusted.scheme, trusted.netloc) or not stored.path.startswith(prefix):
                raise ValueError("Report object is outside configured storage")
            key = unquote(stored.path[len(prefix):])
        else:
            hostname = f"{bucket}.s3.{settings.aws_region}.amazonaws.com"
            if stored.scheme != "https" or stored.netloc != hostname:
                raise ValueError("Report object is outside configured storage")
            key = unquote(stored.path.lstrip("/"))
            # Older upload_file() versions included the bucket in virtual-host URLs.
            if key.startswith(bucket + "/"):
                key = key[len(bucket) + 1:]
    expected = f"tenant-{report.tenant_id}/reports/{report.customer_id}/"
    if not key.startswith(expected) or not key[len(expected):] or any(part in {".", ".."} for part in key.split("/")):
        raise ValueError("Report object does not belong to this customer")
    return key


async def report_download_url(report) -> str | None:
    if not report.s3_url:
        return None
    key = report_object_key(report)
    return await s3_mgr.generate_presigned_url(get_settings().s3_vault_bucket, key, expires_in=900)

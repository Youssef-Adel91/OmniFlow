"""Read-only API keys. A key looks like `ofk_<43 url-safe chars>`; only its SHA-256 is stored (the key has 256 bits of entropy, so a fast hash is
appropriate), plus a short prefix so the owner can tell keys apart. The full key is returned exactly once, at creation."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update

from src.shared.db.models import ApiKey

KEY_PREFIX = "ofk_"
MAX_ACTIVE_KEYS = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def generate_key() -> tuple[str, str, str]:
    """(full key, display prefix, sha256 hex)."""
    full = KEY_PREFIX + secrets.token_urlsafe(32)
    return full, full[:12], hash_key(full)


def looks_like_key(value: str) -> bool:
    return value.startswith(KEY_PREFIX) and 20 <= len(value) <= 80


class TooManyKeys(Exception):
    pass


async def create_key(session, *, tenant_id: uuid.UUID, name: str, created_by: uuid.UUID | None) -> tuple[ApiKey, str]:
    active = (await session.scalars(select(ApiKey.key_id).where(ApiKey.tenant_id == tenant_id, ApiKey.revoked_at.is_(None)))).all()
    if len(active) >= MAX_ACTIVE_KEYS:
        raise TooManyKeys(f"Maximum {MAX_ACTIVE_KEYS} active keys; revoke one first.")
    full, prefix, digest = generate_key()
    row = ApiKey(tenant_id=tenant_id, name=name.strip()[:80], prefix=prefix, key_hash=digest, created_by=created_by)
    session.add(row)
    await session.flush()
    return row, full


async def revoke_key(session, *, tenant_id: uuid.UUID, key_id: uuid.UUID) -> bool:
    res = await session.execute(update(ApiKey).where(ApiKey.key_id == key_id, ApiKey.tenant_id == tenant_id, ApiKey.revoked_at.is_(None))
                                .values(revoked_at=_now()))
    return bool(res.rowcount)


async def authenticate(system_session, presented: str) -> ApiKey | None:
    """Resolve a presented key to its (active) row. Runs in a SYSTEM session because the tenant is not known yet — the key IS the credential."""
    if not looks_like_key(presented):
        return None
    row = await system_session.scalar(select(ApiKey).where(ApiKey.key_hash == hash_key(presented)))
    if row is None or row.revoked_at is not None:
        return None
    return row

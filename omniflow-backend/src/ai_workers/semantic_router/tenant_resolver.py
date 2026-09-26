"""
ai_workers/semantic_router/tenant_resolver.py — Tenant Resolution Service

Resolves the tenant_id for an inbound event using a two-layer cache:

  Layer 1 — Redis (hot path, ~0.5ms):
    Key: "wa_phone_id:{phone_number_id}" → tenant_id UUID string
    TTL: 7 days (rega_verification_cache_ttl)

  Layer 2 — PostgreSQL via get_system_session() (~5-10ms):
    SELECT tenant_id FROM tenants WHERE whatsapp_phone_number_id = ?
    Result is written back to Redis to warm the cache.

  Layer 3 — Fallback None:
    If the phone_number_id is not registered to any tenant, returns None.
    The caller (worker) must reject the message and route to DLQ.

Why get_system_session() and not get_tenant_session()?
    At this point we don't yet KNOW the tenant_id — we're trying to find it.
    The RLS GUC requires a known tenant_id. We must bypass RLS to perform
    this cross-tenant lookup. The query is strictly SELECT-only and
    namespaced to the tenants table (which has no RLS policy itself).

References: SRS §2.3 Step 4 — Tenant Resolution
"""
from __future__ import annotations

import uuid
from typing import Final

import structlog
from sqlalchemy import select

from src.shared.core.config import get_settings
from src.shared.db.models import Tenant
from src.shared.db.session import get_system_session
from src.shared.events.canonical import CanonicalInboundEvent
from src.shared.redis_client.client import redis_mgr

logger = structlog.get_logger(__name__)
settings = get_settings()

# Cache TTL for "phone_number_id not found" — prevents DB hammering on
# invalid IDs. Shorter TTL so a newly-registered tenant picks up quickly.
_NEGATIVE_CACHE_TTL: Final[int] = 300  # 5 minutes
_NEGATIVE_SENTINEL: Final[str] = "NOTFOUND"


class TenantResolver:
    """
    Stateless service that resolves tenant_id from a WhatsApp phone_number_id.

    Instantiate once and reuse across message processing iterations.
    All state is held in Redis — this class has no instance state.

    Usage:
        resolver = TenantResolver()
        tenant_id = await resolver.resolve_from_event(event)
        if tenant_id is None:
            # Unknown phone_number_id — route to DLQ
            raise ValueError(f"No tenant for phone_number_id={event...}")
    """

    async def resolve_from_event(
        self, event: CanonicalInboundEvent
    ) -> uuid.UUID | None:
        """
        Main entry point — resolve tenant_id for a CanonicalInboundEvent.

        For WhatsApp events, the phone_number_id comes from the webhook
        metadata (stored in CanonicalInboundEvent.platform_conversation_id
        or derived from tenant config).

        For Sprint 5, we use the configured default phone_number_id and
        perform the lookup. Sprint 6 will pass the actual phone_number_id
        from the adapter via an event header.
        """
        # Channel adapters resolve the destination account before publishing.
        # Replacing it with the global phone number would cross tenant boundaries.
        if event.tenant_id.int == 0:
            return None
        async with get_system_session() as session:
            return await session.scalar(
                select(Tenant.tenant_id).where(
                    Tenant.tenant_id == event.tenant_id,
                    Tenant.status.in_(["active", "trial"]),
                )
            )

    async def resolve_by_phone_number_id(
        self, phone_number_id: str
    ) -> uuid.UUID | None:
        """
        Resolve tenant_id for a WhatsApp Business phone_number_id.

        Resolution order:
          1. Redis cache (hot path)
          2. PostgreSQL query (cold path, then warm cache)
          3. Return None (unknown phone_number_id)
        """
        if not phone_number_id:
            logger.warning("tenant_resolver_empty_phone_id")
            return None

        # ── Layer 1: Redis hot path ────────────────────────────────────────────
        cached = await redis_mgr.get_tenant_by_wa_phone_id(phone_number_id)
        if cached is not None:
            logger.debug(
                "tenant_resolver_cache_hit",
                phone_number_id=phone_number_id,
                tenant_id=str(cached),
            )
            return cached

        # Check for negative cache (previously confirmed "not found")
        raw = await redis_mgr._cache.get(f"wa_phone_id:{phone_number_id}")
        if raw == _NEGATIVE_SENTINEL:
            logger.debug(
                "tenant_resolver_negative_cache_hit",
                phone_number_id=phone_number_id,
            )
            return None

        # ── Layer 2: PostgreSQL cold path ─────────────────────────────────────
        logger.info(
            "tenant_resolver_cache_miss",
            phone_number_id=phone_number_id,
        )

        tenant_id = await self._query_db(phone_number_id)

        if tenant_id is not None:
            # Warm the Redis cache
            await redis_mgr.set_tenant_wa_mapping(phone_number_id, tenant_id)
            logger.info(
                "tenant_resolver_db_hit",
                phone_number_id=phone_number_id,
                tenant_id=str(tenant_id),
            )
        else:
            # Write negative cache entry to prevent repeated DB queries
            await redis_mgr._cache.set(
                f"wa_phone_id:{phone_number_id}",
                _NEGATIVE_SENTINEL,
                ex=_NEGATIVE_CACHE_TTL,
            )
            logger.warning(
                "tenant_resolver_db_miss",
                phone_number_id=phone_number_id,
            )

        return tenant_id

    async def _query_db(self, phone_number_id: str) -> uuid.UUID | None:
        """
        Query the tenants table for a matching whatsapp_phone_number_id.

        Uses get_system_session() which bypasses RLS — this is intentional
        and safe because:
          1. We are querying the `tenants` table which has NO RLS policy
          2. The query is strictly SELECT-only
          3. This code path is only reachable via authenticated Kafka messages
        """
        try:
            async with get_system_session() as session:
                result = await session.scalars(
                    select(Tenant.tenant_id).where(
                        Tenant.whatsapp_phone_number_id == phone_number_id,
                        Tenant.status.in_(["active", "trial"]),  # type: ignore[attr-defined]
                    )
                )
                tenant_id: uuid.UUID | None = result.first()
                return tenant_id
        except Exception as exc:
            logger.error(
                "tenant_resolver_db_error",
                phone_number_id=phone_number_id,
                error=str(exc),
                exc_type=type(exc).__name__,
            )
            return None

    async def invalidate(self, phone_number_id: str) -> None:
        """
        Invalidate the Redis cache for a phone_number_id.

        Call this when a tenant updates their WhatsApp phone number assignment
        so the next request picks up the new mapping from DB.
        """
        await redis_mgr.invalidate_tenant_wa_mapping(phone_number_id)
        # Also clear negative cache entry
        await redis_mgr._cache.delete(f"wa_phone_id:{phone_number_id}")
        logger.info(
            "tenant_resolver_cache_invalidated",
            phone_number_id=phone_number_id,
        )

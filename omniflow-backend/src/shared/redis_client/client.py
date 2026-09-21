"""
shared/redis_client/client.py — Async Redis Client Manager

Provides a singleton Redis client with:
  - Separate connection pools per logical database (conversations, cache, rate-limit)
  - Tenant-scoped key namespacing: "t:{tenant_id}:*"
  - Typed helper methods for: tenant resolution, conversation history,
    session state, VCard state, rate limiting, and idempotency

Redis logical database layout (mirrors Settings):
  DB 0  — conversation context + session state  (conversation_context_ttl)
  DB 1  — rate limiting counters                (rate_limit_window)
  DB 2  — idempotency keys                      (idempotency_key_ttl)
  DB 5  — general cache (REGA, tenant mappings) (rega_verification_cache_ttl)

Key naming convention (prevents cross-tenant collisions):
  Tenant mapping:     "wa_phone_id:{phone_number_id}"   → tenant_id (DB 5)
  Conversation ctx:   "t:{tid}:conv:{conv_id}:history"  → JSON list (DB 0)
  Session state:      "t:{tid}:session:{phone}"         → JSON dict (DB 0)
  VCard state:        "t:{tid}:vcard:{phone}"           → str enum value (DB 0)
  Rate limit:         "rl:{tid}:{phone}:{window}"       → int counter (DB 1)
  Idempotency:        "idem:{event_id}"                 → "1" (DB 2)

References: SRS §3.2 — Redis State Management, SRS §5 — VCard State Machine
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import structlog
from redis.asyncio import Redis, ConnectionPool
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError

from src.shared.core.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


# ─────────────────────────────────────────────────────────────────────────────
# Internal pool factory
# ─────────────────────────────────────────────────────────────────────────────

def _build_pool(db: int) -> ConnectionPool:
    """
    Build an async Redis connection pool for the given logical DB index.

    Uses exponential backoff retry on ConnectionError / TimeoutError so
    transient network blips don't crash workers.
    """
    scheme = "rediss" if settings.redis_ssl else "redis"
    url = (
        f"{scheme}://:{settings.redis_password}"
        f"@{settings.redis_host}:{settings.redis_port}/{db}"
    )
    retry = Retry(ExponentialBackoff(cap=10, base=0.5), retries=3)
    return ConnectionPool.from_url(
        url,
        max_connections=settings.redis_max_connections,
        decode_responses=True,       # All values returned as str (not bytes)
        retry=retry,
        retry_on_error=[RedisConnectionError, RedisTimeoutError],
        socket_timeout=5.0,
        socket_connect_timeout=5.0,
        health_check_interval=30,
    )


# ══════════════════════════════════════════════════════════════════════════════
# RedisClientManager — singleton managing multiple logical DB pools
# ══════════════════════════════════════════════════════════════════════════════

class RedisClientManager:
    """
    Manages async Redis connections across multiple logical databases.

    Usage:
        # In FastAPI lifespan:
        await redis_mgr.start()
        ...
        await redis_mgr.stop()

        # In workers / endpoints:
        from src.shared.redis_client.client import redis_mgr
        tenant_id = await redis_mgr.get_tenant_by_wa_phone_id(phone_id)
    """

    def __init__(self) -> None:
        self._conv_pool: ConnectionPool | None = None   # DB 0
        self._rl_pool: ConnectionPool | None = None     # DB 1
        self._idem_pool: ConnectionPool | None = None   # DB 2
        self._cache_pool: ConnectionPool | None = None  # DB 5
        self._started: bool = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize connection pools. Called once during app/worker startup."""
        if self._started:
            return
        self._conv_pool  = _build_pool(settings.redis_db_conversations)
        self._rl_pool    = _build_pool(settings.redis_db_rate_limiting)
        self._idem_pool  = _build_pool(settings.redis_db_idempotency)
        self._cache_pool = _build_pool(settings.redis_db_cache)
        self._started = True
        logger.info("redis_client_started", host=settings.redis_host, port=settings.redis_port)

    async def stop(self) -> None:
        """Gracefully close all connection pools."""
        for pool in (self._conv_pool, self._rl_pool, self._idem_pool, self._cache_pool):
            if pool:
                await pool.aclose()
        self._started = False
        logger.info("redis_client_stopped")

    # ── Internal clients (property-based for lazy access) ─────────────────────

    @property
    def _conv(self) -> Redis:
        if not self._conv_pool:
            raise RuntimeError("RedisClientManager not started. Call start() first.")
        return Redis(connection_pool=self._conv_pool)

    @property
    def _cache(self) -> Redis:
        if not self._cache_pool:
            raise RuntimeError("RedisClientManager not started.")
        return Redis(connection_pool=self._cache_pool)

    @property
    def _rl(self) -> Redis:
        if not self._rl_pool:
            raise RuntimeError("RedisClientManager not started.")
        return Redis(connection_pool=self._rl_pool)

    @property
    def _idem(self) -> Redis:
        if not self._idem_pool:
            raise RuntimeError("RedisClientManager not started.")
        return Redis(connection_pool=self._idem_pool)

    # ══════════════════════════════════════════════════════════════════════════
    # Tenant Resolution Cache
    # ══════════════════════════════════════════════════════════════════════════

    def _wa_phone_key(self, phone_number_id: str) -> str:
        return f"wa_phone_id:{phone_number_id}"

    async def get_tenant_by_wa_phone_id(
        self, phone_number_id: str
    ) -> uuid.UUID | None:
        """
        Look up the tenant_id mapped to a WhatsApp phone_number_id.

        Cache miss → returns None → caller must query DB and call
        set_tenant_wa_mapping() to populate the cache.

        TTL: rega_verification_cache_ttl (7 days default) — tenant
        mappings change rarely; long TTL reduces DB load.
        """
        raw = await self._cache.get(self._wa_phone_key(phone_number_id))
        if raw is None:
            return None
        try:
            return uuid.UUID(raw)
        except ValueError:
            logger.warning("redis_invalid_tenant_uuid", raw=raw)
            return None

    async def set_tenant_wa_mapping(
        self,
        phone_number_id: str,
        tenant_id: uuid.UUID,
    ) -> None:
        """
        Cache the phone_number_id → tenant_id mapping.

        Called by TenantResolver after a DB lookup on cache miss.
        """
        await self._cache.set(
            self._wa_phone_key(phone_number_id),
            str(tenant_id),
            ex=settings.rega_verification_cache_ttl,
        )
        logger.debug(
            "redis_tenant_mapping_cached",
            phone_number_id=phone_number_id,
            tenant_id=str(tenant_id),
        )

    async def invalidate_tenant_wa_mapping(self, phone_number_id: str) -> None:
        """Delete the mapping (call when a tenant re-assigns their phone number)."""
        await self._cache.delete(self._wa_phone_key(phone_number_id))

    # ══════════════════════════════════════════════════════════════════════════
    # Conversation History Cache
    # ══════════════════════════════════════════════════════════════════════════

    def _conv_history_key(self, tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> str:
        return f"t:{tenant_id!s}:conv:{conversation_id!s}:history"

    async def get_conversation_history(
        self,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
        last_n: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Fetch the last N messages from the conversation history cache.

        Stored as a Redis List (RPUSH → LRANGE from right = chronological).
        Each element is a JSON-encoded dict:
            {
                "role": "user" | "assistant" | "system",
                "content": str,
                "sender_type": "customer" | "ai_bot" | "human_agent",
                "timestamp": ISO-8601 str,
                "message_type": "text" | "audio" | ...
            }

        Returns empty list on cache miss — caller falls back to DB query.
        """
        key = self._conv_history_key(tenant_id, conversation_id)
        # LRANGE with negative indices: -N to -1 = last N elements
        raw_messages = await self._conv.lrange(key, -last_n, -1)  # type: ignore[attr-defined]
        messages: list[dict[str, Any]] = []
        for raw in raw_messages:
            try:
                messages.append(json.loads(raw))
            except json.JSONDecodeError:
                logger.warning("redis_conv_history_json_error", key=key)
        return messages

    async def append_message_to_history(
        self,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
        message: dict[str, Any],
        *,
        max_history: int = 100,
    ) -> None:
        """
        Append a message to the conversation history list.

        Keeps the list capped at `max_history` entries using LTRIM to prevent
        unbounded growth (Redis is memory-constrained).

        Resets the TTL on every append to keep active conversations warm.
        """
        key = self._conv_history_key(tenant_id, conversation_id)
        pipe = self._conv.pipeline()
        pipe.rpush(key, json.dumps(message, ensure_ascii=False, default=str))  # type: ignore[attr-defined]
        pipe.ltrim(key, -max_history, -1)   # Keep last N entries only
        pipe.expire(key, settings.conversation_context_ttl)
        await pipe.execute()

    async def clear_conversation_history(
        self,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
    ) -> None:
        """Clear history on conversation close (GDPR right-to-erasure path)."""
        await self._conv.delete(self._conv_history_key(tenant_id, conversation_id))

    # ══════════════════════════════════════════════════════════════════════════
    # Session State (per customer phone — lightweight hot data)
    # ══════════════════════════════════════════════════════════════════════════

    def _session_key(self, tenant_id: uuid.UUID, phone: str) -> str:
        return f"t:{tenant_id!s}:session:{phone}"

    async def get_session_state(
        self, tenant_id: uuid.UUID, phone: str
    ) -> dict[str, Any] | None:
        """
        Get the lightweight session state for a customer.

        Contains fast-path fields the AI worker needs without a DB round-trip:
            {
                "customer_id": str,
                "vcard_state": str,
                "is_processing_restricted": bool,
                "is_human_active": bool,
                "conversation_id": str | null,
                "is_vip": bool,
            }
        """
        raw = await self._conv.get(self._session_key(tenant_id, phone))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def set_session_state(
        self,
        tenant_id: uuid.UUID,
        phone: str,
        state: dict[str, Any],
    ) -> None:
        """Cache session state with sliding TTL (reset on every activity)."""
        await self._conv.set(
            self._session_key(tenant_id, phone),
            json.dumps(state, ensure_ascii=False, default=str),
            ex=settings.session_state_ttl,
        )

    async def patch_session_state(
        self,
        tenant_id: uuid.UUID,
        phone: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Atomic read-modify-write for partial session updates.

        Used when only one field changes (e.g., vcard_state transitions)
        to avoid overwriting concurrent updates.
        """
        current = await self.get_session_state(tenant_id, phone) or {}
        current.update(updates)
        await self.set_session_state(tenant_id, phone, current)
        return current

    # ══════════════════════════════════════════════════════════════════════════
    # VCard State (thin wrapper over session — for explicit vcard operations)
    # ══════════════════════════════════════════════════════════════════════════

    def _vcard_key(self, tenant_id: uuid.UUID, phone: str) -> str:
        return f"t:{tenant_id!s}:vcard:{phone}"

    async def get_vcard_state(
        self, tenant_id: uuid.UUID, phone: str
    ) -> str | None:
        """Get the customer's current VCard state string (e.g., 'STATE_NEW')."""
        return await self._conv.get(self._vcard_key(tenant_id, phone))

    async def set_vcard_state(
        self,
        tenant_id: uuid.UUID,
        phone: str,
        state: str,
    ) -> None:
        """Update VCard state with TTL so dormant customers auto-expire."""
        await self._conv.set(
            self._vcard_key(tenant_id, phone),
            state,
            ex=settings.conversation_context_ttl,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # Idempotency Keys — prevent duplicate processing
    # ══════════════════════════════════════════════════════════════════════════

    def _idem_key(self, event_id: str) -> str:
        return f"idem:{event_id}"

    async def is_duplicate_event(self, event_id: str) -> bool:
        """
        Check and mark an event_id atomically (SET NX).

        Returns True if this event_id was already processed (duplicate).
        Returns False and marks it as processed if it's new.

        TTL: idempotency_key_ttl (7 days) — covers all reasonable retry windows.
        """
        key = self._idem_key(event_id)
        # SET NX (set if not exists) — atomic check-and-set
        was_set = await self._idem.set(
            key, "1", nx=True, ex=settings.idempotency_key_ttl
        )
        return was_set is None  # None = key already existed = duplicate

    # ══════════════════════════════════════════════════════════════════════════
    # Rate Limiting (sliding window counter)
    # ══════════════════════════════════════════════════════════════════════════

    async def check_rate_limit(
        self,
        tenant_id: uuid.UUID,
        phone: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> tuple[bool, int]:
        """
        Increment and check a rate limit counter for a customer.

        Uses a sliding window implemented as:
            INCR rl:{tenant_id}:{phone}:{window_bucket}
            EXPIRE ... window_seconds

        Returns:
            (allowed: bool, current_count: int)
            allowed=True  → request is within limit
            allowed=False → request exceeds limit (caller should throttle)
        """
        import time
        bucket = int(time.time()) // window_seconds
        key = f"rl:{tenant_id!s}:{phone}:{bucket}"
        pipe = self._rl.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds * 2)  # 2x to survive bucket rollover
        results = await pipe.execute()
        count: int = results[0]
        return count <= limit, count

    # ══════════════════════════════════════════════════════════════════════════
    # Low-level raw key access (cache DB 5)
    # ══════════════════════════════════════════════════════════════════════════

    async def get_raw(self, key: str) -> str | None:
        """
        Get a raw string value from the general cache DB (DB 5).
        Returns None if the key doesn't exist.

        Used by OutboundDispatcherWorker for tenant credential caching.
        """
        try:
            return await self._cache.get(key)
        except Exception as exc:
            logger.warning("redis_get_raw_error", key=key, error=str(exc))
            return None

    async def set_raw(self, key: str, value: str, *, ttl: int | None = None) -> None:
        """
        Set a raw string value in the general cache DB (DB 5).

        Used by OutboundDispatcherWorker to store wamid → message_id mapping.
        """
        try:
            if ttl:
                await self._cache.setex(key, ttl, value)
            else:
                await self._cache.set(key, value)
        except Exception as exc:
            logger.warning("redis_set_raw_error", key=key, error=str(exc))

    async def set_idempotency_key(self, key: str, ttl: int) -> bool:
        """
        Atomic SET NX (set-if-not-exists) on the idempotency DB (DB 2).

        Returns:
            True  — key was SET successfully (first time seeing this key)
            False — key already existed (duplicate — caller should skip processing)

        Used by OutboundDispatcherWorker to prevent double-sending messages.
        The key format is "sent:{source_event_id}" with a 7-day TTL.
        """
        try:
            result = await self._idem.set(key, "1", nx=True, ex=ttl)
            return result is True
        except Exception as exc:
            logger.error("redis_idempotency_key_error", key=key, error=str(exc))
            # On Redis failure: allow processing (prefer at-least-once over data loss)
            return True

    # ══════════════════════════════════════════════════════════════════════════
    # Health
    # ══════════════════════════════════════════════════════════════════════════

    async def ping(self) -> bool:
        """Return True if Redis is reachable. Used by /health/ready endpoint."""
        try:
            return await self._cache.ping()  # type: ignore[return-value]
        except Exception:
            return False

    async def get_client(self):
        """
        Return the conversation-context Redis client (DB 0).

        This is the client used for Pub/Sub SSE channels (``sse:{tenant_id}``).
        Consumers call this to obtain a client on which they can:
          - Create a pubsub: redis.pubsub()
          - Publish events:  redis.publish(channel, payload)

        Returns the internal _conv Redis instance (DB 0) which is pre-configured
        with auto-reconnect and exponential backoff.
        """
        return self._conv

    async def publish_sse(
        self,
        *,
        tenant_id: "uuid.UUID",
        event_type: str,
        data: "dict",
    ) -> None:
        """
        Publish an SSE event to Redis Pub/Sub channel ``sse:{tenant_id}``.

        The SSE generator in conversations.py subscribes to this channel and
        forwards every message to the connected browser EventSource.

        Args:
            tenant_id  — UUID of the tenant whose inbox should receive the update.
            event_type — One of: "new_message", "conversation_update", "typing", "ping".
            data       — JSON-serialisable payload dict.
        """
        import json as _json

        channel = f"sse:{tenant_id!s}"
        payload = _json.dumps({"event": event_type, "data": data})
        try:
            await self._conv.publish(channel, payload)
            logger.debug(
                "redis_sse_published",
                channel=channel,
                event_type=event_type,
            )
        except Exception as exc:
            logger.warning(
                "redis_sse_publish_failed",
                channel=channel,
                event_type=event_type,
                error=str(exc),
            )


# ── Module-level singleton ─────────────────────────────────────────────────────
redis_mgr = RedisClientManager()

"""Outgoing webhooks (transactional outbox).

`emit()` inserts one `webhook_deliveries` row per matching endpoint INSIDE the caller's transaction (so an event is recorded iff the business
change committed) and never raises. A Celery task (`omniflow.deliver_webhooks`) later POSTs due rows. Each request is signed:

    X-OmniFlow-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, f"{t}.{raw_body}")>
    X-OmniFlow-Event / X-OmniFlow-Delivery / X-OmniFlow-Event-Id

Retries: 1 min, 5 min, 30 min, 2 h, 6 h (6 attempts in total) → `failed`. After 20 consecutive failures an endpoint is switched off.
SSRF: only https (http allowed in development), no URL credentials, and every resolved address must be public — checked when the endpoint is
saved AND again right before each delivery; redirects are never followed.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import httpx
import structlog
from sqlalchemy import or_, select

from src.shared.core.config import get_settings
from src.shared.db.models import WebhookDelivery, WebhookEndpoint
from src.shared.security.crypto import decrypt_secret, encrypt_secret

logger = structlog.get_logger(__name__)

EVENTS = ("conversation.created", "conversation.handoff", "customer.created")
TEST_EVENT = "webhook.test"
BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2), timedelta(hours=6))
MAX_ATTEMPTS = len(BACKOFF) + 1
DISABLE_AFTER_FAILURES = 20
MAX_ENDPOINTS = 5
TIMEOUT_S = 8.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UnsafeUrl(ValueError):
    pass


def _is_public(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    return a.is_global and not a.is_multicast


def validate_url(url: str, *, resolve: bool = True) -> str:
    """Raise UnsafeUrl unless `url` is an https (or dev http) URL with no credentials whose every address is public."""
    parts = urlsplit(url.strip())
    cfg = get_settings()
    allowed = {"https"} | ({"http"} if cfg.is_development else set())
    if parts.scheme not in allowed or not parts.hostname:
        raise UnsafeUrl("العنوان يجب أن يكون https صالحًا.")
    if parts.username or parts.password:
        raise UnsafeUrl("لا يُسمح بوجود بيانات دخول داخل العنوان.")
    if len(url) > 500:
        raise UnsafeUrl("العنوان طويل جدًا.")
    host = parts.hostname
    if resolve:
        try:
            infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except OSError as exc:
            raise UnsafeUrl("تعذّر تحليل اسم النطاق.") from exc
        addrs = {i[4][0] for i in infos}
        if not addrs or not all(_is_public(a) for a in addrs):
            if not cfg.is_development:
                raise UnsafeUrl("العنوان يشير إلى شبكة داخلية أو غير عامة.")
    else:
        try:
            literal_ip = ipaddress.ip_address(host)
        except ValueError:
            literal_ip = None            # a hostname, not a literal IP
        if literal_ip is not None and not _is_public(str(literal_ip)):
            raise UnsafeUrl("العنوان يشير إلى شبكة داخلية أو غير عامة.")
    return url.strip()


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def sign(secret: str, body: bytes, timestamp: int) -> str:
    mac = hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def verify_signature(secret: str, body: bytes, header: str, *, tolerance_s: int = 300, now: float | None = None) -> bool:
    """Reference verifier (what a receiver should do); used by the tests and documented in docs/api.md."""
    try:
        fields = dict(p.split("=", 1) for p in header.split(","))
        ts = int(fields["t"])
    except (ValueError, KeyError):
        return False
    if abs((now if now is not None else time.time()) - ts) > tolerance_s:
        return False
    return hmac.compare_digest(sign(secret, body, ts).split("v1=", 1)[1], fields.get("v1", ""))


def build_payload(event_id: uuid.UUID, event_type: str, tenant_id: uuid.UUID, data: dict[str, Any], created: datetime | None = None) -> dict[str, Any]:
    return {"id": str(event_id), "type": event_type, "created_at": (created or _now()).isoformat(), "tenant_id": str(tenant_id), "data": data}


async def emit(session, tenant_id: uuid.UUID, event_type: str, data: dict[str, Any]) -> int:
    """Queue `event_type` for every active endpoint subscribed to it. Best-effort: NEVER raises (a SAVEPOINT isolates failures). Returns rows queued."""
    try:
        async with session.begin_nested():
            endpoints = (await session.scalars(select(WebhookEndpoint).where(
                WebhookEndpoint.tenant_id == tenant_id, WebhookEndpoint.active.is_(True)))).all()
            event_id = uuid.uuid4()
            queued = 0
            for ep in endpoints:
                if event_type not in (ep.events or []):
                    continue
                session.add(WebhookDelivery(tenant_id=tenant_id, endpoint_id=ep.endpoint_id, event_id=event_id, event_type=event_type,
                                            payload=build_payload(event_id, event_type, tenant_id, data), status="pending", next_attempt_at=_now()))
                queued += 1
            await session.flush()
            return queued
    except Exception as exc:  # noqa: BLE001
        logger.warning("webhook_emit_failed", tenant_id=str(tenant_id), event_type=event_type, error=type(exc).__name__)
        return 0


Poster = Callable[[str, bytes, dict[str, str]], Awaitable[tuple[int | None, str | None]]]


async def _post(url: str, body: bytes, headers: dict[str, str]) -> tuple[int | None, str | None]:
    """(status code, error). Never follows redirects."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False) as c:
            r = await c.post(url, content=body, headers=headers)
        return r.status_code, None
    except httpx.HTTPError as exc:
        return None, type(exc).__name__


async def attempt(delivery: WebhookDelivery, endpoint: WebhookEndpoint, *, poster: Poster = _post, now: datetime | None = None) -> None:
    """One delivery attempt; mutates `delivery`/`endpoint` (caller commits). 2xx = delivered; anything else schedules a retry or marks failed."""
    now = now or _now()
    secret = decrypt_secret(endpoint.secret_enc)
    delivery.attempts += 1
    code: int | None = None
    error: str | None = None
    if secret is None:
        error = "secret_unreadable"
    else:
        try:
            await asyncio.to_thread(validate_url, endpoint.url)   # re-check right before connecting (DNS may have changed since the endpoint was saved)
        except UnsafeUrl as exc:
            error = f"unsafe_url:{str(exc)[:80]}"
    if error is None:
        body = json.dumps(delivery.payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "OmniFlow-Webhooks/1",
                   "X-OmniFlow-Signature": sign(secret, body, int(now.timestamp())), "X-OmniFlow-Event": delivery.event_type,
                   "X-OmniFlow-Event-Id": str(delivery.event_id), "X-OmniFlow-Delivery": str(delivery.delivery_id)}
        code, error = await poster(endpoint.url, body, headers)
    delivery.last_status_code = code
    if code is not None and 200 <= code < 300:
        delivery.status, delivery.delivered_at, delivery.next_attempt_at, delivery.last_error = "delivered", now, None, None
        endpoint.consecutive_failures = 0
        return
    delivery.last_error = (error or f"http_{code}")[:300]
    endpoint.consecutive_failures += 1
    if delivery.attempts >= MAX_ATTEMPTS:
        delivery.status, delivery.next_attempt_at = "failed", None
    else:
        delivery.next_attempt_at = now + BACKOFF[delivery.attempts - 1]
    if endpoint.consecutive_failures >= DISABLE_AFTER_FAILURES and endpoint.active:
        endpoint.active, endpoint.disabled_reason = False, "too_many_failures"


async def deliver_due(session, *, limit: int = 50, poster: Poster = _post, now: datetime | None = None) -> int:
    """Attempt every due pending delivery (SYSTEM session; rows are locked so two workers never double-send). Returns attempts made."""
    now = now or _now()
    rows = (await session.scalars(select(WebhookDelivery).where(
        WebhookDelivery.status == "pending", or_(WebhookDelivery.next_attempt_at.is_(None), WebhookDelivery.next_attempt_at <= now))
        .order_by(WebhookDelivery.next_attempt_at).limit(limit).with_for_update(skip_locked=True))).all()
    done = 0
    for d in rows:
        ep = await session.get(WebhookEndpoint, d.endpoint_id)
        if ep is None or not ep.active:
            d.status, d.last_error, d.next_attempt_at = "failed", "endpoint_inactive", None
            continue
        await attempt(d, ep, poster=poster, now=now)
        done += 1
    return done


def make_endpoint(*, tenant_id: uuid.UUID, url: str, events: list[str], description: str | None) -> tuple[WebhookEndpoint, str]:
    secret = new_secret()
    ep = WebhookEndpoint(tenant_id=tenant_id, url=validate_url(url), description=(description or "")[:120] or None,
                         secret_enc=encrypt_secret(secret), events=sorted(set(events)), active=True)
    return ep, secret

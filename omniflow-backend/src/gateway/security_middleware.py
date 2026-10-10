"""Pure-ASGI security middleware (no BaseHTTPMiddleware: it breaks SSE streaming / client-disconnect handling).

* SecurityHeadersMiddleware — nosniff, frame denial, referrer policy, permissions policy, `Cache-Control: no-store` on API
  responses, HSTS in production. (Nginx adds the same at the edge; this keeps the API safe when reached any other way.)
* RateLimitMiddleware — Redis fixed-window counters, defence in depth behind nginx `limit_req`:
    - authenticated API: per bearer-token hash (fallback: client IP)   → RATE_LIMIT_API_PER_MIN
    - unauthenticated provider hooks (payments, Clerk): per client IP  → RATE_LIMIT_WEBHOOK_PER_MIN
    - NEVER limited: health probes, SSE streams, and Meta channel webhooks (customers' messages must not be dropped; they are
      protected by their HMAC signature, and Meta calls from a small shared IP pool).
  Fails OPEN when Redis is unavailable.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Awaitable, Callable

from src.shared.core.config import get_settings
from src.shared.redis_client import redis_mgr

Scope = dict
Receive = Callable[[], Awaitable[dict]]
Send = Callable[[dict], Awaitable[None]]

_STATIC = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
]
_HSTS = (b"strict-transport-security", b"max-age=63072000; includeSubDomains")


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        prod = get_settings().is_production
        path = scope.get("path", "")

        async def wrapped(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", [])]
                have = {k.lower() for k, _ in headers}
                add = [h for h in _STATIC if h[0] not in have]
                if prod and _HSTS[0] not in have:
                    add.append(_HSTS)
                if path.startswith("/api/") and b"cache-control" not in have and "/stream" not in path:
                    add.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers + add}
            await send(message)

        await self.app(scope, receive, wrapped)


def _client_ip(scope: Scope) -> str:
    headers = {k.lower(): v for k, v in scope.get("headers", [])}
    real = headers.get(b"x-real-ip")          # set by nginx (proxy-params.conf) — never trusted from the open internet in dev
    if real and get_settings().app_env != "development":
        return real.decode()[:64]
    client = scope.get("client")
    return client[0] if client else "unknown"


def classify(path: str, method: str) -> str | None:
    """'api' | 'hook' | None (exempt)."""
    if method == "OPTIONS" or not path.startswith("/api/"):
        return None
    if "/stream" in path or path.endswith("/events"):
        return None
    if path.startswith(("/api/v1/webhooks/payments", "/api/v1/webhooks/clerk")):
        return "hook"
    if path.startswith("/api/v1/webhooks") or path.startswith(("/api/v1/whatsapp", "/api/v1/instagram", "/api/v1/meta")):
        return None                            # Meta channels: HMAC-protected, must not be throttled
    return "api"


class RateLimitMiddleware:
    def __init__(self, app, *, window_seconds: int = 60):
        self.app, self.window = app, window_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        cfg = get_settings()
        if scope["type"] != "http" or not cfg.rate_limit_enabled:
            return await self.app(scope, receive, send)
        kind = classify(scope.get("path", ""), scope.get("method", "GET"))
        if kind is None:
            return await self.app(scope, receive, send)
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        auth = headers.get(b"authorization", b"")
        if kind == "api" and auth.lower().startswith(b"bearer "):
            ident = "t:" + hashlib.sha256(auth[7:]).hexdigest()[:20]
        else:
            ident = "ip:" + _client_ip(scope)
        limit = cfg.rate_limit_webhook_per_min if kind == "hook" else cfg.rate_limit_api_per_min
        bucket = int(time.time()) // self.window
        count = await redis_mgr.incr_counter(f"rl:{kind}:{ident}:{bucket}", ttl=self.window * 2)
        if count is not None and count > limit:
            retry = self.window - (int(time.time()) % self.window)
            body = json.dumps({"detail": {"code": "RATE_LIMITED", "message": "طلبات كثيرة جدًا. حاول بعد قليل."}}).encode()
            await send({"type": "http.response.start", "status": 429, "headers": [
                (b"content-type", b"application/json"), (b"retry-after", str(retry).encode()), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)

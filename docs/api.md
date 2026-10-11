# OmniFlow API v1 (read-only) + outgoing webhooks

Manage keys and webhooks in **الإعدادات ← المطورون** (admin only). Everything here is per-workspace: a key only ever sees its own workspace's data.

## 1) Read-only API

- Base URL: `https://<your-api-host>/api/public/v1` (the host is whatever you deployed the API on).
- Auth: `Authorization: Bearer ofk_…`. A key is shown **once** when created; only its SHA-256 is stored, so we cannot show it again — create a new one and revoke the old.
- Read-only: there are no write endpoints (POST/PUT/PATCH/DELETE answer 405/401).
- Rate limit: **120 requests/minute per key** → `429` with `Retry-After`. (If our rate-limit store is down, requests are allowed rather than blocked.)
- Usage log: every request (method, path, status, time) is recorded and visible per key.
- Errors: `{"detail": {"code": "...", "message": "..."}}` with `401` (missing/invalid/revoked key), `404`, `422` (bad parameter), `429`.
- Pagination: `limit` (1–100, default 25) and `offset`; responses carry `has_more` and `next_offset`.

| Endpoint | Filters |
|---|---|
| `GET /customers` · `GET /customers/{id}` | `phone` (E.164), `updated_since` (ISO-8601). Archived and erased customers are never returned. |
| `GET /conversations` · `GET /conversations/{id}` | `status`, `channel`, `customer_id` |
| `GET /properties` · `GET /properties/{id}` | `city`, `status` |

```bash
curl -s -H "Authorization: Bearer $OMNIFLOW_KEY" "https://<api-host>/api/public/v1/customers?limit=10"
```

Fields are deliberately a subset (no internal notes, no extracted profiles, no tokens). Money/area are plain numbers; timestamps are ISO-8601 UTC.

## 2) Outgoing webhooks

Events: `customer.created`, `conversation.created`, `conversation.handoff` (plus `webhook.test`, sent by the **Send test** button).

Envelope:

```json
{ "id": "<event id>", "type": "customer.created", "created_at": "2026-01-01T12:00:00+00:00",
  "tenant_id": "<workspace id>", "data": { "customer_id": "…", "phone": "+9665…", "source": "whatsapp" } }
```

`data` per event: `customer.created` → `customer_id, phone, source` · `conversation.created` → `conversation_id, customer_id, channel` · `conversation.handoff` → `conversation_id, reason, assigned_agent_id`.

### Headers
`X-OmniFlow-Signature: t=<unix seconds>,v1=<hex>` · `X-OmniFlow-Event` · `X-OmniFlow-Event-Id` · `X-OmniFlow-Delivery` (stable across retries — use it to de-duplicate).

### Verify the signature
`v1 = HMAC-SHA256(secret, "<t>." + raw_request_body)` as lowercase hex. Compare in constant time and reject timestamps older than ~5 minutes.

```python
import hmac, hashlib, time

def verify(secret: str, raw_body: bytes, header: str, tolerance=300) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    t = int(parts["t"])
    if abs(time.time() - t) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{t}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts["v1"])
```

### Delivery & retries
- Respond with any `2xx` within 8 seconds. Redirects are not followed.
- Otherwise we retry after 1 min, 5 min, 30 min, 2 h, 6 h (6 attempts in total) and then mark the delivery `failed`. Every attempt is visible in the delivery log.
- After 20 consecutive failures an endpoint is switched off (re-enable it from the settings page).
- Events are queued in the same database transaction as the change that caused them (transactional outbox), so an event exists if and only if the change committed. Delivery is **at-least-once**: de-duplicate on `X-OmniFlow-Delivery` / `id`.
- Order is not guaranteed.

### Security rules for endpoints
Only `https://` URLs (plain `http://` is accepted only in development); no credentials in the URL; the host must resolve to **public** addresses only — checked when you save the endpoint and again before every delivery. The signing secret is shown once; **Rotate secret** invalidates the old one immediately. Up to 5 endpoints and 10 active API keys per workspace.

## 3) Not included in v1
Write endpoints, per-key scopes (every key is full read), message bodies, API versioning beyond `/v1`, and a sandbox. Delivery-log and API-usage-log retention is handled by the retention job (see the retention docs).

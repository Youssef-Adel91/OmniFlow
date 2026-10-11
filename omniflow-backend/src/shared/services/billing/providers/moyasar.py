"""Moyasar adapter (https://docs.moyasar.com). Written from the public API documentation.

NOT yet exercised against the live Moyasar sandbox (no test keys were available while this was built): request/response
shapes follow the docs and are unit-tested with recorded-shape fixtures only. First action when sandbox keys exist:
run the sandbox checklist in IMPLEMENTATION_STATUS.md.

Security model
  * Hosted invoice page → no card data ever reaches us.
  * Webhook authenticity: Moyasar puts the shared `secret_token` in the body (not an HMAC). We compare it in constant
    time AND the caller re-fetches the payment from Moyasar before trusting its status/amount (defence in depth).
"""
from __future__ import annotations

import hmac
import json
import re
from typing import Any, Mapping

import httpx

from src.shared.core.config import get_settings
from src.shared.services.billing.providers.base import (
    Checkout, PaymentInfo, ProviderError, RefundInfo, WebhookEvent, WebhookRejected,
)

_STATUS = {"paid": "paid", "captured": "paid", "failed": "failed", "initiated": "initiated", "refunded": "refunded"}
_SECRET_FIELDS = {"secret_token"}


def _payment(p: Mapping[str, Any]) -> PaymentInfo:
    src = p.get("source") or {}
    meta = p.get("metadata") or {}
    number = str(src.get("number") or "")
    last4 = re.sub(r"\D", "", number)[-4:] or None
    return PaymentInfo(
        payment_id=str(p.get("id") or ""),
        status=_STATUS.get(str(p.get("status")), "other"),
        amount_halalas=int(p.get("amount") or 0),
        currency=str(p.get("currency") or "SAR").upper(),
        transaction_id=(str(meta.get("transaction_id")) if meta.get("transaction_id") else None),
        invoice_id=(str(p["invoice_id"]) if p.get("invoice_id") else None),
        token=(str(src["token"]) if src.get("token") else None),
        card_brand=(str(src.get("company") or "") or None),
        card_last4=last4,
        failure_reason=(str(src.get("message") or "") or None) if str(p.get("status")) == "failed" else None,
    )


class MoyasarProvider:
    name = "moyasar"

    def __init__(self, *, client: httpx.AsyncClient | None = None):
        self._client = client

    # ── plumbing ──────────────────────────────────────────────────────────────
    def _cfg(self):
        cfg = get_settings()
        key = (cfg.moyasar_secret_key or "").strip()
        if not key:
            raise ProviderError("Moyasar is not configured (MOYASAR_SECRET_KEY is empty).", code="not_configured")
        if key.startswith("sk_live_") and cfg.app_env != "production":
            # A live key outside production is almost certainly a mistake that would charge real cards.
            raise ProviderError("A live Moyasar key is configured outside production; refusing to use it.", code="live_key_outside_production")
        return cfg, key

    async def _request(self, method: str, path: str, **kw) -> dict[str, Any]:
        cfg, key = self._cfg()
        url = cfg.moyasar_base_url.rstrip("/") + path
        try:
            if self._client is not None:
                r = await self._client.request(method, url, auth=(key, ""), timeout=20, **kw)
            else:
                async with httpx.AsyncClient() as c:
                    r = await c.request(method, url, auth=(key, ""), timeout=20, **kw)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Moyasar unreachable: {type(exc).__name__}", retryable=True, code="network") from exc
        if r.status_code >= 500 or r.status_code == 429:
            raise ProviderError(f"Moyasar error {r.status_code}", retryable=True, code="provider_5xx")
        if r.status_code >= 400:
            try:
                msg = (r.json() or {}).get("message") or r.text[:200]
            except ValueError:
                msg = r.text[:200]
            raise ProviderError(f"Moyasar rejected the request: {msg}", code=f"http_{r.status_code}")
        try:
            return r.json()
        except ValueError as exc:
            raise ProviderError("Moyasar returned a non-JSON body", retryable=True, code="bad_body") from exc

    # ── PaymentProvider ──────────────────────────────────────────────────────
    async def create_checkout(self, *, transaction_id, amount_halalas, currency, description, success_url, back_url, metadata) -> Checkout:
        data = await self._request("POST", "/invoices", json={
            "amount": amount_halalas, "currency": currency, "description": description,
            "success_url": success_url, "back_url": back_url,
            "metadata": {**metadata, "transaction_id": transaction_id},
        })
        if not data.get("id") or not data.get("url"):
            raise ProviderError("Moyasar invoice response is missing id/url", code="bad_body")
        return Checkout(provider_invoice_id=str(data["id"]), url=str(data["url"]))

    async def fetch_payment(self, payment_id: str) -> PaymentInfo:
        return _payment(await self._request("GET", f"/payments/{payment_id}"))

    async def fetch_invoice_payments(self, provider_invoice_id: str) -> list[PaymentInfo]:
        data = await self._request("GET", f"/invoices/{provider_invoice_id}")
        return [_payment(p) for p in (data.get("payments") or [])]

    async def charge_token(self, *, token, transaction_id, amount_halalas, currency, description, metadata) -> PaymentInfo:
        data = await self._request("POST", "/payments", json={
            "given_id": transaction_id,            # Moyasar's idempotency key: our transaction id (a UUID)
            "amount": amount_halalas, "currency": currency, "description": description,
            "source": {"type": "token", "token": token},
            "metadata": {**metadata, "transaction_id": transaction_id},
        })
        return _payment(data)

    async def refund_payment(self, *, payment_id: str, amount_halalas: int, reason: str) -> RefundInfo:
        """Moyasar: POST /payments/{id}/refund {amount}. Refunding returns the payment object (status 'refunded'). Not exercised against
        the live API from this repository — the contract here follows Moyasar's public documentation only."""
        data = await self._request("POST", f"/payments/{payment_id}/refund", json={"amount": amount_halalas})
        status = str(data.get("status") or "").lower()
        refunded = int(data.get("refunded") or 0)
        ok = status in ("refunded", "partially_refunded") or refunded >= amount_halalas
        return RefundInfo(refund_id=str(data.get("id") or payment_id), status="refunded" if ok else "pending", amount_halalas=refunded or amount_halalas)

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> WebhookEvent:
        expected = (get_settings().moyasar_webhook_secret or "").strip()
        if not expected:
            raise WebhookRejected("webhook secret is not configured")
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise WebhookRejected("body is not JSON") from exc
        if not isinstance(payload, dict):
            raise WebhookRejected("body is not an object")
        supplied = str(payload.get("secret_token") or "")
        if not supplied or not hmac.compare_digest(supplied.encode(), expected.encode()):
            raise WebhookRejected("bad secret_token")
        event_id = str(payload.get("id") or "")
        etype = str(payload.get("type") or "")
        if not event_id or not etype:
            raise WebhookRejected("missing id/type")
        data = payload.get("data")
        pay = _payment(data) if isinstance(data, dict) and data.get("id") else None
        # Never persist the secret (or card-ish source details beyond what _payment extracted).
        raw = {k: v for k, v in payload.items() if k not in _SECRET_FIELDS and k != "data"}
        if pay:
            raw["data"] = {"id": pay.payment_id, "status": pay.status, "amount": pay.amount_halalas, "currency": pay.currency}
        return WebhookEvent(event_key=event_id, event_type=etype, payment=pay, raw=raw)

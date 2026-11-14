"""
Sandbox payment provider — for demos, local development and the end-to-end simulation. NO money, NO network, NO card data.

`PAYMENT_PROVIDER=sandbox` makes the hosted-checkout step succeed immediately: the "checkout" URL is simply the success URL, and the provider answers every lookup with a
PAID payment that is derived from the ids themselves (stateless: it works across processes and restarts). It issues a fake reusable token (Visa ****4242) so saved-card
features (auto top-up, renewals) can be exercised. It refuses to run when APP_ENV=production.
"""
from __future__ import annotations

from typing import Mapping

from src.shared.core.config import get_settings
from src.shared.services.billing.providers.base import Checkout, PaymentInfo, ProviderError, RefundInfo, WebhookEvent, WebhookRejected

_INV, _PAY = "sbxinv", "sbxpay"


def _guard() -> None:
    if get_settings().app_env == "production":
        raise ProviderError("The sandbox payment provider is disabled in production.", code="sandbox_in_production")


class SandboxProvider:
    name = "sandbox"

    async def create_checkout(self, *, transaction_id: str, amount_halalas: int, currency: str, description: str, success_url: str, back_url: str,
                              metadata: Mapping[str, str]) -> Checkout:
        _guard()
        return Checkout(provider_invoice_id=f"{_INV}:{transaction_id}:{amount_halalas}:{currency}", url=success_url)

    def _paid(self, transaction_id: str, amount: int, currency: str) -> PaymentInfo:
        return PaymentInfo(payment_id=f"{_PAY}:{transaction_id}:{amount}:{currency}", status="paid", amount_halalas=amount, currency=currency, transaction_id=transaction_id,
                           token="sbx_tok_4242", card_brand="visa", card_last4="4242")        # pragma: allowlist secret

    async def fetch_invoice_payments(self, provider_invoice_id: str) -> list[PaymentInfo]:
        _guard()
        try:
            prefix, txn, amount, currency = provider_invoice_id.split(":")
            return [self._paid(txn, int(amount), currency)] if prefix == _INV else []
        except ValueError:
            return []

    async def fetch_payment(self, payment_id: str) -> PaymentInfo:
        _guard()
        try:
            prefix, txn, amount, currency = payment_id.split(":")
            if prefix != _PAY:
                raise ValueError
            return self._paid(txn, int(amount), currency)
        except ValueError as exc:
            raise ProviderError("unknown sandbox payment", code="not_found") from exc

    async def charge_token(self, *, token: str, transaction_id: str, amount_halalas: int, currency: str, description: str, metadata: Mapping[str, str]) -> PaymentInfo:
        _guard()
        return self._paid(transaction_id, amount_halalas, currency)

    async def refund_payment(self, *, payment_id: str, amount_halalas: int, reason: str) -> RefundInfo:
        _guard()
        return RefundInfo(refund_id=f"sbxref:{payment_id}:{amount_halalas}", status="refunded", amount_halalas=amount_halalas)

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> WebhookEvent:
        raise WebhookRejected("the sandbox provider sends no webhooks")

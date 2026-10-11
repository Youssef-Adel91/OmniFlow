"""Payment provider contract. Cards are never handled by us: hosted checkout + opaque provider tokens only."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


class ProviderError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, code: str | None = None):
        super().__init__(message)
        self.retryable, self.code = retryable, code


class WebhookRejected(Exception):
    """Bad signature / malformed body. The HTTP layer answers 401/400 and nothing is stored or applied."""


@dataclass(frozen=True)
class Checkout:
    provider_invoice_id: str
    url: str


@dataclass(frozen=True)
class PaymentInfo:
    payment_id: str
    status: str                     # paid | failed | initiated | refunded | other
    amount_halalas: int
    currency: str
    transaction_id: str | None = None     # our transactions.transaction_id echoed back from metadata
    invoice_id: str | None = None
    token: str | None = None              # reusable payment token, when the provider issued one
    card_brand: str | None = None
    card_last4: str | None = None
    failure_reason: str | None = None


@dataclass(frozen=True)
class RefundInfo:
    refund_id: str
    status: str                     # refunded | pending | failed
    amount_halalas: int


@dataclass(frozen=True)
class WebhookEvent:
    event_key: str                  # unique per delivery-able event → idempotency
    event_type: str
    payment: PaymentInfo | None
    raw: Mapping[str, Any] = field(default_factory=dict)      # secrets already stripped


class PaymentProvider(Protocol):
    name: str

    async def create_checkout(self, *, transaction_id: str, amount_halalas: int, currency: str, description: str,
                              success_url: str, back_url: str, metadata: Mapping[str, str]) -> Checkout: ...

    async def fetch_payment(self, payment_id: str) -> PaymentInfo: ...

    async def fetch_invoice_payments(self, provider_invoice_id: str) -> list[PaymentInfo]: ...

    async def charge_token(self, *, token: str, transaction_id: str, amount_halalas: int, currency: str,
                           description: str, metadata: Mapping[str, str]) -> PaymentInfo: ...

    async def refund_payment(self, *, payment_id: str, amount_halalas: int, reason: str) -> RefundInfo: ...

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> WebhookEvent: ...

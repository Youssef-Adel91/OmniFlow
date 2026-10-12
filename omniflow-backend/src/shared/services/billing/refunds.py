"""Refund policy (draft, awaiting legal review): a 14-day money-back guarantee on the tenant's FIRST payment only.

What is refunded = what was paid (VAT excluded) minus the real AI cost already consumed since the payment minus the gateway fee, with
VAT recomputed on what remains. Only OmniFlow staff can issue a refund (the endpoint is staff-gated); each one is audited, goes to the
payment provider's refund API, and produces a credit note (CN-YYYY-NNNNNN) — the document a ZATCA credit-note (381) is later built from.
A full refund of the first payment ends the subscription immediately.
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import func, select, text

from src.shared.core.config import get_settings
from src.shared.db.models import AuditLog, BillingInvoice, CreditNote, LlmUsage, PaymentTransaction, Refund, Subscription, Tenant
from src.shared.services.billing import pricing
from src.shared.services.billing.providers import get_provider
from src.shared.services.billing.providers.base import ProviderError
from src.shared.services.billing.service import BillingError, _mirror_tenant

logger = structlog.get_logger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def quote(session, txn: PaymentTransaction, now: datetime | None = None) -> dict:
    """What a refund of this transaction would be, and why not if it cannot be refunded. Pure read."""
    now = now or _now()
    cfg = get_settings()
    reasons: list[str] = []
    if txn.status != "paid":
        reasons.append("العملية غير مدفوعة.")
    if txn.kind not in ("initial", "manual"):
        reasons.append("الضمان يشمل أول دفعة فقط (ليس التجديدات أو الترقيات أو الإضافات).")
    first = await session.scalar(select(PaymentTransaction.transaction_id).where(
        PaymentTransaction.tenant_id == txn.tenant_id, PaymentTransaction.status == "paid", PaymentTransaction.kind.in_(("initial", "manual")))
        .order_by(PaymentTransaction.paid_at).limit(1))
    if first != txn.transaction_id:
        reasons.append("هذه ليست أول دفعة للمنشأة.")
    if txn.paid_at is None or now > txn.paid_at + timedelta(days=cfg.billing_refund_window_days):
        reasons.append(f"انتهت مهلة الضمان ({cfg.billing_refund_window_days} يومًا من تاريخ الدفع).")
    done = await session.scalar(select(Refund.refund_id).where(Refund.transaction_id == txn.transaction_id, Refund.status == "done"))
    if done is not None:
        reasons.append("تم استرداد هذه العملية سابقًا.")

    consumed_sar = await session.scalar(select(func.coalesce(func.sum(LlmUsage.cost_sar), 0)).where(
        LlmUsage.tenant_id == txn.tenant_id, LlmUsage.created_at >= (txn.paid_at or now)))
    consumed = int(math.ceil(float(consumed_sar or 0) * 100))
    fee = (txn.amount_halalas * cfg.billing_gateway_fee_bp + 5000) // 10000 + cfg.billing_gateway_fee_fixed_halalas
    remaining = max(0, txn.subtotal_halalas - consumed - fee)
    a = pricing.with_vat(remaining, cfg.billing_vat_rate_bp)
    return {"eligible": not reasons, "reasons": reasons, "gross_halalas": txn.amount_halalas, "consumed_halalas": consumed, "fee_halalas": fee,
            "subtotal_halalas": a.subtotal, "vat_halalas": a.vat, "amount_halalas": a.total}


async def issue(session, txn_id: uuid.UUID, *, reason: str, staff_email: str, amount_halalas: int | None = None,
                now: datetime | None = None) -> dict:
    """System session. Refund through the provider, then record the credit note. Idempotent: a done refund is never repeated."""
    now = now or _now()
    cfg = get_settings()
    txn = await session.scalar(select(PaymentTransaction).where(PaymentTransaction.transaction_id == txn_id).with_for_update())
    if txn is None:
        raise BillingError("NOT_FOUND", "العملية غير موجودة.", 404)
    q = await quote(session, txn, now)
    if not q["eligible"]:
        raise BillingError("NOT_REFUNDABLE", " ".join(q["reasons"]), 409)
    amount = q["amount_halalas"]
    if amount_halalas is not None:
        if amount_halalas > amount:
            raise BillingError("AMOUNT_TOO_HIGH", f"أقصى مبلغ قابل للاسترداد {amount / 100:.2f} ر.س.", 422)
        amount = amount_halalas
    if amount <= 0:
        raise BillingError("NOTHING_TO_REFUND", "لا يوجد مبلغ قابل للاسترداد بعد خصم الاستهلاك والرسوم.", 409)
    split = pricing.with_vat((amount * 10000) // (10000 + cfg.billing_vat_rate_bp), cfg.billing_vat_rate_bp) if amount_halalas is not None else None
    subtotal, vat = (split.subtotal, amount - split.subtotal) if split else (q["subtotal_halalas"], q["vat_halalas"])
    invoice = await session.scalar(select(BillingInvoice).where(BillingInvoice.transaction_id == txn.transaction_id))
    refund = Refund(refund_id=uuid.uuid4(), tenant_id=txn.tenant_id, transaction_id=txn.transaction_id, invoice_id=invoice.invoice_id if invoice else None,
                    status="pending", reason=reason[:1000], gross_halalas=txn.amount_halalas, consumed_halalas=q["consumed_halalas"],
                    fee_halalas=q["fee_halalas"], subtotal_halalas=subtotal, vat_halalas=vat, amount_halalas=amount, provider=txn.provider,
                    requested_by=staff_email[:254], created_at=now)
    session.add(refund)
    await session.flush()

    if txn.provider == "manual":                      # paid by bank transfer: nothing to call; staff return the money by transfer
        refund.provider_refund_id = "manual"
    else:
        try:
            info = await get_provider().refund_payment(payment_id=txn.provider_payment_id or "", amount_halalas=amount, reason=reason)
        except ProviderError as exc:
            refund.status, refund.failure_reason = "failed", str(exc)[:500]
            await session.flush()
            session.add(AuditLog(tenant_id=txn.tenant_id, action="billing.refund_failed", entity_type="refund", entity_id=str(refund.refund_id),
                                 details={"error": str(exc)[:200], "staff": staff_email}))
            await session.flush()
            return {"failed": True, "code": "PROVIDER_REFUND_FAILED", "message": f"تعذّر الاسترداد عبر بوابة الدفع: {exc}", "refund_id": str(refund.refund_id)}
        if info.status != "refunded":
            refund.status, refund.failure_reason = "failed", f"provider status {info.status}"
            await session.flush()
            return {"failed": True, "code": "PROVIDER_REFUND_PENDING", "refund_id": str(refund.refund_id),
                    "message": "لم تؤكد البوابة الاسترداد بعد؛ راجع لوحة البوابة ولا تُعد المحاولة قبل التأكد."}
        refund.provider_refund_id = info.refund_id
    refund.status, refund.completed_at = "done", now

    tenant = await session.scalar(select(Tenant).where(Tenant.tenant_id == txn.tenant_id))
    seq = (await session.execute(text("SELECT nextval('credit_note_number_seq')"))).scalar_one()
    note = CreditNote(credit_note_id=uuid.uuid4(), tenant_id=txn.tenant_id, refund_id=refund.refund_id,
                      original_invoice_id=invoice.invoice_id if invoice else None, credit_note_number=f"CN-{now.year}-{seq:06d}", issued_at=now,
                      subtotal_halalas=subtotal, vat_halalas=vat, total_halalas=amount, vat_rate_bp=cfg.billing_vat_rate_bp, currency=txn.currency,
                      reason=reason[:1000], seller=(invoice.seller if invoice else {}),
                      buyer=(invoice.buyer if invoice else {"name": getattr(tenant, "business_name", "")}))
    session.add(note)
    await session.flush()
    if invoice is not None:
        invoice.status = "credited"
    from src.einvoice import service as einvoice

    await einvoice.issue_safely(session, note=note)

    if amount >= q["amount_halalas"]:                 # a full refund of the first payment ends the subscription at once
        sub = await session.scalar(select(Subscription).where(Subscription.subscription_id == txn.subscription_id).with_for_update())
        if sub is not None:
            sub.status, sub.cancelled_at, sub.cancel_at_period_end = "cancelled", now, False
            sub.payment_token = None
            sub.auto_topup_enabled = False
            await session.flush()
            await _mirror_tenant(session, sub)
    session.add(AuditLog(tenant_id=txn.tenant_id, action="billing.refund", entity_type="refund", entity_id=str(refund.refund_id),
                         details={"transaction_id": str(txn.transaction_id), "amount_halalas": amount, "consumed_halalas": q["consumed_halalas"],
                                  "fee_halalas": q["fee_halalas"], "credit_note": note.credit_note_number, "staff": staff_email, "reason": reason[:300]}))
    await session.flush()
    logger.info("billing_refund_done", tenant_id=str(txn.tenant_id), refund_id=str(refund.refund_id), amount_halalas=amount)
    return {"refund_id": str(refund.refund_id), "credit_note_number": note.credit_note_number, "amount_halalas": amount, "subtotal_halalas": subtotal,
            "vat_halalas": vat, "consumed_halalas": q["consumed_halalas"], "fee_halalas": q["fee_halalas"], "provider": txn.provider,
            "manual_transfer_required": txn.provider == "manual"}

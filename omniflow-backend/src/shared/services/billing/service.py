"""Billing orchestration: subscriptions, checkout, webhook application, renewals/dunning, manual activation, invoices.

Every function takes an explicit AsyncSession. Request handlers pass a tenant (RLS) session; webhooks, the scheduled sweep
and staff actions pass a system session and filter by tenant explicitly. State only changes through:
  verified provider payment (webhook / return-page sync / token charge)  |  scheduled sweep  |  staff manual activation.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.shared.core.config import get_settings
from src.shared.db.models import (
    AuditLog, BillingInvoice, PaymentEvent, PaymentTransaction, Plan, Subscription, Tenant,
)
from src.shared.services.billing import pricing
from src.shared.services.billing.access import Access, evaluate
from src.shared.services.billing.providers import get_provider
from src.shared.services.billing.providers.base import PaymentInfo, ProviderError, WebhookEvent
from src.shared.security.crypto import decrypt_secret, encrypt_secret
from src.shared.services.notifications import notify_safe

logger = structlog.get_logger(__name__)

PENDING_REUSE_MINUTES = 60
PENDING_EXPIRE_HOURS = 48


def default_urls() -> dict[str, str]:
    base = str(get_settings().frontend_url).rstrip("/")
    return {"success": f"{base}/ar/billing/return?tx={{tx}}", "back": f"{base}/ar/settings#billing"}


class BillingError(Exception):
    def __init__(self, code: str, message: str, http: int = 409):
        super().__init__(message)
        self.code, self.message, self.http = code, message, http


class AmountMismatch(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ══════════════════════════════════════════════════════════════════════════════
# Catalogue + subscription row
# ══════════════════════════════════════════════════════════════════════════════

async def list_plans(session, *, only_active: bool = True) -> list[Plan]:
    stmt = select(Plan).order_by(Plan.sort_order)
    if only_active:
        stmt = stmt.where(Plan.is_active.is_(True))
    return list((await session.execute(stmt)).scalars())


async def get_plan(session, code: str) -> Plan:
    plan = await session.scalar(select(Plan).where(Plan.code == code))
    if plan is None or not plan.is_active:
        raise BillingError("PLAN_NOT_FOUND", "الخطة غير موجودة أو غير متاحة.", 404)
    return plan


async def ensure_subscription(session, tenant_id: uuid.UUID, now: datetime | None = None) -> Subscription:
    """The tenant's subscription row; created lazily as a real (non-exempt) trial for tenants born after billing shipped."""
    sub = await session.scalar(select(Subscription).where(Subscription.tenant_id == tenant_id))
    if sub is not None:
        return sub
    now = now or _now()
    cfg = get_settings()
    tenant = await session.scalar(select(Tenant).where(Tenant.tenant_id == tenant_id))
    if tenant is None:
        raise BillingError("TENANT_NOT_FOUND", "المنشأة غير موجودة.", 404)
    started = tenant.created_at or now
    tier = str(getattr(tenant.subscription_tier, "value", tenant.subscription_tier) or "economic")
    await session.execute(pg_insert(Subscription).values(
        subscription_id=uuid.uuid4(), tenant_id=tenant_id, plan_code=tier, billing_interval="monthly", status="trial",
        is_exempt=False, trial_ends_at=started + timedelta(days=cfg.billing_trial_days),
    ).on_conflict_do_nothing(index_elements=[Subscription.tenant_id]))
    await session.flush()
    return await session.scalar(select(Subscription).where(Subscription.tenant_id == tenant_id))


def access_of(sub: Subscription | None, now: datetime | None = None) -> Access:
    cfg = get_settings()
    return evaluate(sub, now or _now(), grace_days=cfg.billing_grace_days, gate_enabled=cfg.billing_gate_enabled)


async def _mirror_tenant(session, sub: Subscription) -> None:
    """tenants.status/tier stay a readable mirror of the subscription (other code and reports still read them)."""
    await session.execute(update(Tenant).where(Tenant.tenant_id == sub.tenant_id)
                          .values(status=sub.status, subscription_tier=sub.plan_code))


async def _audit(session, tenant_id, action: str, entity_id: str | None, details: dict, actor: uuid.UUID | None = None) -> None:
    session.add(AuditLog(tenant_id=tenant_id, actor_user_id=actor, action=action, entity_type="subscription",
                         entity_id=entity_id, details=details))
    await session.flush()


# ══════════════════════════════════════════════════════════════════════════════
# Checkout
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ChangeResult:
    action: str                           # checkout | scheduled
    transaction: PaymentTransaction | None = None
    effective_at: datetime | None = None


def _check_sellable(plan: Plan) -> None:
    if plan.is_placeholder and get_settings().app_env == "production":
        raise BillingError("PLACEHOLDER_PRICING", "أسعار هذه الخطة لم تُعتمد بعد؛ لا يمكن الدفع حاليًا.", 409)


async def _new_transaction(session, sub: Subscription, plan: Plan, interval: str, kind: str, subtotal: int, *, provider: str,
                           period_start=None, period_end=None, attempt: int = 1, now: datetime | None = None) -> PaymentTransaction:
    amounts = pricing.with_vat(subtotal, get_settings().billing_vat_rate_bp)
    txn = PaymentTransaction(
        transaction_id=uuid.uuid4(), tenant_id=sub.tenant_id, subscription_id=sub.subscription_id, kind=kind,
        plan_code=plan.code, billing_interval=interval, subtotal_halalas=amounts.subtotal, vat_halalas=amounts.vat,
        amount_halalas=amounts.total, currency=plan.currency, status="pending", provider=provider,
        period_start=period_start, period_end=period_end, attempt=attempt, created_at=now or _now(),
    )
    session.add(txn)
    await session.flush()
    return txn


async def _attach_checkout(session, txn: PaymentTransaction, plan: Plan, urls: dict[str, str], description: str) -> PaymentTransaction:
    try:
        co = await get_provider().create_checkout(
            transaction_id=str(txn.transaction_id), amount_halalas=txn.amount_halalas, currency=txn.currency,
            description=description, success_url=urls["success"].format(tx=txn.transaction_id), back_url=urls["back"],
            metadata={"tenant_id": str(txn.tenant_id), "plan": txn.plan_code, "interval": txn.billing_interval, "kind": txn.kind})
    except ProviderError as exc:
        txn.status, txn.failure_reason = "failed", str(exc)[:500]
        await session.flush()
        logger.warning("billing_checkout_failed", tenant_id=str(txn.tenant_id), code=exc.code)
        return txn
    txn.provider_invoice_id, txn.checkout_url = co.provider_invoice_id, co.url
    await session.flush()
    return txn


async def _reusable_pending(session, sub, plan_code, interval, kind, now: datetime | None = None) -> PaymentTransaction | None:
    cutoff = (now or _now()) - timedelta(minutes=PENDING_REUSE_MINUTES)
    return await session.scalar(
        select(PaymentTransaction).where(
            PaymentTransaction.subscription_id == sub.subscription_id, PaymentTransaction.status == "pending",
            PaymentTransaction.plan_code == plan_code, PaymentTransaction.billing_interval == interval,
            PaymentTransaction.kind == kind, PaymentTransaction.checkout_url.is_not(None),
            PaymentTransaction.created_at > cutoff).order_by(PaymentTransaction.created_at.desc()).limit(1))


def _describe(plan: Plan, interval: str, kind: str) -> str:
    unit = "سنوي" if interval == "yearly" else "شهري"
    label = {"upgrade": "ترقية الخطة", "renewal": "تجديد الاشتراك"}.get(kind, "اشتراك")
    return f"OmniFlow — {label} — {plan.name_ar} ({unit})"


async def change_plan(session, tenant_id: uuid.UUID, plan_code: str, interval: str, urls: dict[str, str],
                      now: datetime | None = None) -> ChangeResult:
    """Subscribe / renew early / upgrade (pro-rated, immediate) / schedule a downgrade or interval change for the next renewal."""
    now = now or _now()
    if interval not in pricing.INTERVALS:
        raise BillingError("INVALID_INTERVAL", "دورة الفوترة غير صالحة.", 422)
    plan = await get_plan(session, plan_code)
    _check_sellable(plan)
    sub = await ensure_subscription(session, tenant_id, now)
    if sub.is_exempt:
        raise BillingError("EXEMPT", "هذا الحساب معفى من الفوترة (حساب داخلي).", 409)
    price = pricing.plan_price(plan, interval)
    provider = get_settings().payment_provider

    running = sub.status == "active" and sub.current_period_end is not None and sub.current_period_end > now
    if running:
        cur = await get_plan(session, sub.plan_code)
        same = (plan.code, interval) == (sub.plan_code, sub.billing_interval)
        if same:
            kind, subtotal, p_start, p_end = "renewal", price, sub.current_period_end, pricing.add_interval(sub.current_period_end, interval)
        elif interval == sub.billing_interval and price > pricing.plan_price(cur, interval):
            subtotal = pricing.prorated_upgrade(pricing.plan_price(cur, interval), price, sub.current_period_start or now,
                                                sub.current_period_end, now)
            if subtotal <= 0:
                raise BillingError("NOTHING_TO_CHARGE", "لا يوجد فرق يُدفع الآن.", 409)
            kind, p_start, p_end = "upgrade", sub.current_period_start, sub.current_period_end
        else:                                                    # downgrade / interval change → next renewal, no charge now
            sub.pending_plan_code, sub.pending_interval = plan.code, interval
            await session.flush()
            await _audit(session, tenant_id, "billing.plan_change_scheduled", str(sub.subscription_id),
                         {"plan": plan.code, "interval": interval, "effective_at": sub.current_period_end.isoformat()})
            return ChangeResult("scheduled", effective_at=sub.current_period_end)
    else:                                                        # trial / past_due / suspended / cancelled / expired → new purchase
        kind, subtotal, p_start, p_end = "initial", price, None, None

    existing = await _reusable_pending(session, sub, plan.code, interval, kind, now)
    if existing is not None and existing.amount_halalas == pricing.with_vat(subtotal, get_settings().billing_vat_rate_bp).total:
        return ChangeResult("checkout", existing)
    txn = await _new_transaction(session, sub, plan, interval, kind, subtotal, provider=provider, period_start=p_start, period_end=p_end, now=now)
    txn = await _attach_checkout(session, txn, plan, urls, _describe(plan, interval, kind))
    return ChangeResult("checkout", txn)


async def sync_transaction(session, txn: PaymentTransaction, now: datetime | None = None) -> PaymentTransaction:
    """Return-page fallback: ask the provider what happened, so a slow/lost webhook never leaves the customer stuck."""
    if txn.status != "pending" or not (txn.provider_payment_id or txn.provider_invoice_id):
        return txn
    prov = get_provider()
    try:
        if txn.provider_payment_id:
            candidates = [await prov.fetch_payment(txn.provider_payment_id)]
        else:
            candidates = await prov.fetch_invoice_payments(txn.provider_invoice_id)
    except ProviderError as exc:
        logger.warning("billing_sync_failed", transaction_id=str(txn.transaction_id), code=exc.code)
        return txn
    paid = next((c for c in candidates if c.status == "paid"), None)
    if paid is not None:
        await apply_payment_result(session, txn, paid, now or _now())
    return txn


# ══════════════════════════════════════════════════════════════════════════════
# Applying a provider result (the only place money turns into access)
# ══════════════════════════════════════════════════════════════════════════════

def _renewal_start(sub: Subscription, now: datetime, grace_days: int) -> datetime:
    old_end = sub.current_period_end
    if old_end is not None and sub.status in ("active", "past_due") and now <= old_end + timedelta(days=grace_days):
        return max(old_end, now - timedelta(days=grace_days))      # paid time is continuous: no gap, no double billing
    return now


async def _activate(session, sub: Subscription, txn: PaymentTransaction, info: PaymentInfo | None, now: datetime) -> None:
    cfg = get_settings()
    if txn.kind == "upgrade":
        sub.plan_code = txn.plan_code
    else:
        start = _renewal_start(sub, now, cfg.billing_grace_days) if txn.kind in ("renewal", "manual") else now
        sub.plan_code, sub.billing_interval = txn.plan_code, txn.billing_interval
        sub.current_period_start, sub.current_period_end = start, pricing.add_interval(start, txn.billing_interval)
        if sub.pending_plan_code == txn.plan_code:
            sub.pending_plan_code = sub.pending_interval = None
    sub.status = "active"
    sub.cancel_at_period_end, sub.cancelled_at = False, None
    sub.retry_count, sub.next_retry_at, sub.grace_ends_at, sub.last_payment_error = 0, None, None, None
    sub.provider = txn.provider
    if info is not None and info.token:
        sub.payment_token = encrypt_secret(info.token)
        sub.card_brand, sub.card_last4 = info.card_brand, info.card_last4
    await session.flush()
    await _mirror_tenant(session, sub)


async def issue_invoice(session, txn: PaymentTransaction, now: datetime) -> BillingInvoice:
    existing = await session.scalar(select(BillingInvoice).where(BillingInvoice.transaction_id == txn.transaction_id))
    if existing is not None:
        return existing
    cfg = get_settings()
    tenant = await session.scalar(select(Tenant).where(Tenant.tenant_id == txn.tenant_id))
    plan = await session.scalar(select(Plan).where(Plan.code == txn.plan_code))
    seq = (await session.execute(text("SELECT nextval('invoice_number_seq')"))).scalar_one()
    inv = BillingInvoice(
        invoice_id=uuid.uuid4(), tenant_id=txn.tenant_id, transaction_id=txn.transaction_id,
        invoice_number=pricing.invoice_number(seq, now.year), issued_at=now,
        subtotal_halalas=txn.subtotal_halalas, vat_halalas=txn.vat_halalas, total_halalas=txn.amount_halalas,
        vat_rate_bp=cfg.billing_vat_rate_bp, currency=txn.currency,
        description=_describe(plan, txn.billing_interval, txn.kind) if plan else f"اشتراك OmniFlow ({txn.plan_code})",
        period_start=txn.period_start, period_end=txn.period_end,
        seller={"name": cfg.billing_seller_name, "tax_number": cfg.billing_seller_tax_number,
                "address": cfg.billing_seller_address, "cr_number": cfg.billing_seller_cr_number},
        buyer={"name": getattr(tenant, "business_name", ""), "fal_license": getattr(tenant, "fal_license_number", "")},
    )
    session.add(inv)
    await session.flush()
    return inv


async def apply_payment_result(session, txn: PaymentTransaction, info: PaymentInfo, now: datetime | None = None) -> str:
    """Idempotent. Returns 'paid' | 'failed' | 'noop'. Raises AmountMismatch if the provider's numbers differ from ours."""
    now = now or _now()
    if info.amount_halalas != txn.amount_halalas or info.currency.upper() != txn.currency.upper():
        raise AmountMismatch(f"provider {info.amount_halalas} {info.currency} != expected {txn.amount_halalas} {txn.currency}")
    if info.status == "paid":
        if txn.status == "paid":
            return "noop"
        txn.status, txn.paid_at, txn.failure_reason = "paid", now, None
        txn.provider_payment_id = info.payment_id or txn.provider_payment_id
        sub = await session.scalar(select(Subscription).where(Subscription.subscription_id == txn.subscription_id).with_for_update())
        await _activate(session, sub, txn, info, now)
        await session.flush()
        await issue_invoice(session, txn, now)
        await _audit(session, txn.tenant_id, "billing.payment_applied", str(txn.transaction_id),
                     {"kind": txn.kind, "plan": txn.plan_code, "amount_halalas": txn.amount_halalas})
        return "paid"
    if info.status == "failed":
        if txn.status == "paid":
            return "noop"                               # a late failure webhook must never undo a settled payment
        txn.status, txn.failure_reason = "failed", (info.failure_reason or "payment_failed")[:500]
        txn.provider_payment_id = info.payment_id or txn.provider_payment_id
        await session.flush()
        return "failed"
    return "noop"


# ══════════════════════════════════════════════════════════════════════════════
# Webhooks
# ══════════════════════════════════════════════════════════════════════════════

async def handle_webhook(session, event: WebhookEvent, now: datetime | None = None) -> dict[str, Any]:
    """`session` is a SYSTEM session (no tenant known yet). Raises ProviderError(retryable) so the HTTP layer can ask for a retry."""
    now = now or _now()
    provider = get_provider()
    inserted = (await session.execute(
        pg_insert(PaymentEvent).values(provider=provider.name, event_key=event.event_key, event_type=event.event_type,
                                       outcome="received", payload=dict(event.raw))
        .on_conflict_do_nothing(constraint="uq_payment_events_provider_key").returning(PaymentEvent.event_pk))).scalar_one_or_none()
    if inserted is None:
        return {"outcome": "duplicate"}
    outcome, tenant_id = "ignored", None
    if event.payment is not None and event.event_type.startswith("payment_"):
        info = await provider.fetch_payment(event.payment.payment_id)        # authoritative; the webhook body is a hint only
        txn = None
        if info.transaction_id:
            try:
                txn = await session.scalar(select(PaymentTransaction).where(
                    PaymentTransaction.transaction_id == uuid.UUID(info.transaction_id)).with_for_update())
            except ValueError:
                txn = None
        if txn is None:
            txn = await session.scalar(select(PaymentTransaction).where(
                PaymentTransaction.provider == provider.name, PaymentTransaction.provider_payment_id == info.payment_id).with_for_update())
        if txn is not None:
            tenant_id = txn.tenant_id
            try:
                outcome = "applied" if await apply_payment_result(session, txn, info, now) != "noop" else "ignored"
            except AmountMismatch as exc:
                outcome = "rejected"
                logger.error("billing_webhook_amount_mismatch", transaction_id=str(txn.transaction_id), detail=str(exc))
    await session.execute(update(PaymentEvent).where(PaymentEvent.event_pk == inserted).values(outcome=outcome, tenant_id=tenant_id))
    return {"outcome": outcome, "tenant_id": tenant_id}


# ══════════════════════════════════════════════════════════════════════════════
# Cancel / resume / manual activation
# ══════════════════════════════════════════════════════════════════════════════

async def cancel_at_period_end(session, tenant_id: uuid.UUID, actor: uuid.UUID | None) -> Subscription:
    sub = await ensure_subscription(session, tenant_id)
    if sub.is_exempt or sub.status != "active":
        raise BillingError("NOT_CANCELLABLE", "لا يوجد اشتراك فعّال لإلغائه.", 409)
    sub.cancel_at_period_end = True
    sub.pending_plan_code = sub.pending_interval = None
    await session.flush()
    await _audit(session, tenant_id, "billing.cancel_requested", str(sub.subscription_id),
                 {"effective_at": sub.current_period_end.isoformat() if sub.current_period_end else None}, actor)
    return sub


async def resume(session, tenant_id: uuid.UUID, actor: uuid.UUID | None) -> Subscription:
    sub = await ensure_subscription(session, tenant_id)
    if sub.status != "active" or not sub.cancel_at_period_end:
        raise BillingError("NOT_RESUMABLE", "لا يوجد إلغاء مجدول للتراجع عنه.", 409)
    sub.cancel_at_period_end = False
    await session.flush()
    await _audit(session, tenant_id, "billing.cancel_reverted", str(sub.subscription_id), {}, actor)
    return sub


async def manual_activate(session, tenant_id: uuid.UUID, plan_code: str, interval: str, *, periods: int = 1, note: str = "",
                          amount_halalas: int | None = None, reference: str = "", now: datetime | None = None) -> PaymentTransaction:
    """Staff activation for customers who paid outside the gateway (bank transfer). Always audited and invoiced."""
    now = now or _now()
    if not 1 <= periods <= 24:
        raise BillingError("INVALID_PERIODS", "عدد الدورات بين 1 و24.", 422)
    plan = await get_plan(session, plan_code)
    sub = await ensure_subscription(session, tenant_id, now)
    if sub.is_exempt:
        raise BillingError("EXEMPT", "هذا الحساب معفى من الفوترة.", 409)
    sub_total = amount_halalas if amount_halalas is not None else pricing.plan_price(plan, interval) * periods
    txn = await _new_transaction(session, sub, plan, interval, "manual", sub_total, provider="manual", now=now)
    # `amount_halalas` (if given) is the amount actually received, VAT-inclusive: split it back out.
    if amount_halalas is not None:
        rate = get_settings().billing_vat_rate_bp
        net = (amount_halalas * 10000 + (10000 + rate) // 2) // (10000 + rate)
        txn.subtotal_halalas, txn.vat_halalas, txn.amount_halalas = net, amount_halalas - net, amount_halalas
    txn.status, txn.paid_at, txn.provider_payment_id = "paid", now, f"manual:{reference or txn.transaction_id}"
    start = _renewal_start(sub, now, get_settings().billing_grace_days)
    end = start
    for _ in range(periods):
        end = pricing.add_interval(end, interval)
    txn.period_start, txn.period_end = start, end
    await session.flush()
    await _activate(session, sub, txn, None, now)
    sub.current_period_start, sub.current_period_end = start, end
    sub.provider = "manual"
    await session.flush()
    await issue_invoice(session, txn, now)
    await _audit(session, tenant_id, "billing.manual_activation", str(txn.transaction_id),
                 {"plan": plan_code, "interval": interval, "periods": periods, "amount_halalas": txn.amount_halalas,
                  "reference": reference[:120], "note": note[:300]})
    return txn


# ══════════════════════════════════════════════════════════════════════════════
# Scheduled sweep: trial expiry, renewals + retries, cancellations, grace → suspended
# ══════════════════════════════════════════════════════════════════════════════

async def _fail_attempt(session, sub: Subscription, txn: PaymentTransaction | None, reason: str, now: datetime, urls: dict[str, str]) -> str:
    cfg = get_settings()
    sub.retry_count += 1
    sub.last_payment_error = reason[:500]
    if txn is not None:
        txn.status, txn.failure_reason = "failed", reason[:500]
    if sub.retry_count >= cfg.billing_retry_max:
        sub.status = "past_due"
        sub.grace_ends_at = now + timedelta(days=cfg.billing_grace_days)
        sub.next_retry_at = None
        await session.flush()
        await _mirror_tenant(session, sub)
        await _audit(session, sub.tenant_id, "billing.past_due", str(sub.subscription_id), {"reason": reason[:200]})
        await _renewal_link(session, sub, now, urls)
        await notify_safe(session, sub.tenant_id, "payment_failed", "تعذّر تحصيل اشتراكك",
                          f"سيتوقف المساعد الذكي في {sub.grace_ends_at:%Y-%m-%d} إن لم يتم السداد. حدّث الدفع من صفحة الفوترة.", "/settings#billing",
                          dedupe_key=f"past_due:{sub.current_period_end:%Y%m%d}" if sub.current_period_end else None)
        return "past_due"
    sub.next_retry_at = now + timedelta(hours=cfg.billing_retry_interval_hours)
    await session.flush()
    return "retry_scheduled"


async def _renewal_link(session, sub: Subscription, now: datetime, urls: dict[str, str]) -> None:
    """Give a past_due customer a link they can pay from the billing page (the dashboard shows it)."""
    code, interval = sub.pending_plan_code or sub.plan_code, sub.pending_interval or sub.billing_interval
    plan = await get_plan(session, code)
    if await _reusable_pending(session, sub, code, interval, "renewal", now) is not None:
        return
    txn = await _new_transaction(session, sub, plan, interval, "renewal", pricing.plan_price(plan, interval),
                                 provider=get_settings().payment_provider, attempt=sub.retry_count + 1, now=now)
    await _attach_checkout(session, txn, plan, urls, _describe(plan, interval, "renewal"))


async def _renew(session, sub: Subscription, now: datetime, urls: dict[str, str]) -> str:
    code, interval = sub.pending_plan_code or sub.plan_code, sub.pending_interval or sub.billing_interval
    plan = await get_plan(session, code)
    if not sub.payment_token:                                   # nothing to charge automatically
        sub.retry_count = max(sub.retry_count, get_settings().billing_retry_max - 1)
        return await _fail_attempt(session, sub, None, "no_saved_payment_method", now, urls)
    txn = await _new_transaction(session, sub, plan, interval, "renewal", pricing.plan_price(plan, interval),
                                 provider=sub.provider or get_settings().payment_provider, attempt=sub.retry_count + 1,
                                 period_start=sub.current_period_end, period_end=pricing.add_interval(sub.current_period_end, interval), now=now)
    try:
        token = decrypt_secret(sub.payment_token)
        if not token:
            raise ProviderError("saved token cannot be decrypted", code="bad_token")
        info = await get_provider().charge_token(token=token, transaction_id=str(txn.transaction_id), amount_halalas=txn.amount_halalas,
                                                 currency=txn.currency, description=_describe(plan, interval, "renewal"),
                                                 metadata={"tenant_id": str(sub.tenant_id), "kind": "renewal"})
    except ProviderError as exc:
        return await _fail_attempt(session, sub, txn, f"{exc.code or 'provider_error'}: {exc}", now, urls)
    txn.provider_payment_id = info.payment_id or None
    try:
        res = await apply_payment_result(session, txn, info, now)
    except AmountMismatch as exc:
        return await _fail_attempt(session, sub, txn, f"amount_mismatch: {exc}", now, urls)
    if res == "paid":
        return "renewed"
    return await _fail_attempt(session, sub, txn, info.failure_reason or f"charge_{info.status}", now, urls)


async def run_sweep(session, urls: dict[str, str], now: datetime | None = None) -> dict[str, int]:
    """System session. Safe to run concurrently (row locks + SKIP LOCKED) and repeatedly (every step is state-guarded)."""
    now = now or _now()
    out = {"trial_expired": 0, "renewed": 0, "retry_scheduled": 0, "past_due": 0, "suspended": 0, "cancelled": 0, "pending_expired": 0}

    warn_until = now + timedelta(days=3)
    for sub in (await session.execute(select(Subscription).where(
            Subscription.status == "trial", Subscription.is_exempt.is_(False), Subscription.trial_ends_at >= now, Subscription.trial_ends_at <= warn_until))).scalars():
        if await notify_safe(session, sub.tenant_id, "trial_ending", "تنتهي تجربتك قريبًا",
                             f"تنتهي الفترة التجريبية في {sub.trial_ends_at:%Y-%m-%d}. اختر باقة لتستمر الخدمة.", "/settings#billing",
                             dedupe_key=f"trial_ending:{sub.trial_ends_at:%Y%m%d}"):
            out["trial_warned"] = out.get("trial_warned", 0) + 1

    for sub in (await session.execute(select(Subscription).where(
            Subscription.status == "trial", Subscription.is_exempt.is_(False), Subscription.trial_ends_at < now)
            .with_for_update(skip_locked=True))).scalars():
        sub.status = "suspended"
        await session.flush(); await _mirror_tenant(session, sub)
        await notify_safe(session, sub.tenant_id, "subscription_suspended", "انتهت الفترة التجريبية",
                          "توقف المساعد الذكي. اختر باقة لاستعادة الخدمة.", "/settings#billing", dedupe_key=f"trial_expired:{now:%Y%m%d}")
        out["trial_expired"] += 1

    for sub in (await session.execute(select(Subscription).where(
            Subscription.status == "active", Subscription.is_exempt.is_(False), Subscription.cancel_at_period_end.is_(True),
            Subscription.current_period_end <= now).with_for_update(skip_locked=True))).scalars():
        sub.status, sub.cancelled_at = "cancelled", now
        await session.flush(); await _mirror_tenant(session, sub)
        await _audit(session, sub.tenant_id, "billing.cancelled", str(sub.subscription_id), {})
        out["cancelled"] += 1

    due = (await session.execute(select(Subscription).where(
        Subscription.status == "active", Subscription.is_exempt.is_(False), Subscription.cancel_at_period_end.is_(False),
        Subscription.current_period_end <= now,
        (Subscription.next_retry_at.is_(None)) | (Subscription.next_retry_at <= now)).with_for_update(skip_locked=True))).scalars().all()
    for sub in due:
        res = await _renew(session, sub, now, urls)
        out[res] = out.get(res, 0) + 1

    for sub in (await session.execute(select(Subscription).where(
            Subscription.status == "past_due", Subscription.is_exempt.is_(False), Subscription.grace_ends_at < now)
            .with_for_update(skip_locked=True))).scalars():
        sub.status = "suspended"
        await session.flush(); await _mirror_tenant(session, sub)
        await _audit(session, sub.tenant_id, "billing.suspended", str(sub.subscription_id), {})
        await notify_safe(session, sub.tenant_id, "subscription_suspended", "تم إيقاف اشتراكك",
                          "توقف المساعد الذكي والحساب في وضع القراءة فقط. جدّد الاشتراك لاستعادة الخدمة.", "/settings#billing", dedupe_key=f"suspended:{now:%Y%m%d}")
        out["suspended"] += 1

    cutoff = now - timedelta(hours=PENDING_EXPIRE_HOURS)
    res = await session.execute(update(PaymentTransaction).where(
        PaymentTransaction.status == "pending", PaymentTransaction.created_at < cutoff).values(status="cancelled", failure_reason="expired"))
    out["pending_expired"] = res.rowcount or 0
    return out

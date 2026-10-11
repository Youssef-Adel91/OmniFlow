"""
gateway/routers/billing.py — subscriptions, checkout, invoices, usage (prefix /api/v1/billing)

    GET  /status                      every role: gate state for the dashboard banner
    GET  /plans                       catalogue with VAT preview (admin/auditor)
    GET  /subscription                plan, status, usage vs limits, card on file, pending change (admin/auditor)
    POST /checkout                    admin: subscribe / renew early / upgrade (hosted payment page) or schedule a downgrade
    GET  /transactions                admin/auditor
    GET  /transactions/{id}           admin/auditor: also asks the provider if still pending (return-page fallback)
    POST /cancel · /resume            admin: cancel at period end / undo
    GET  /invoices · /invoices/{id}/pdf   admin/auditor
    POST /admin/tenants/{tid}/activate    staff (X-Billing-Key): manual activation for bank-transfer customers
Webhook: POST /api/v1/webhooks/payments (see bottom) — unauthenticated by design, verified by the provider adapter.

None of these routes are behind the subscription write gate: a suspended tenant must still be able to pay.
"""
from __future__ import annotations

import hmac
import uuid
from typing import Annotated, Optional

import structlog
from fastapi import APIRouter, Header, HTTPException, Path, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from src.gateway.dependencies import AuthTenantSession, CurrentUser
from src.gateway.routers.support import StaffUser
from src.shared.core.config import get_settings
from src.shared.core.enums import TenantUserRole
from src.shared.db.models import BillingInvoice, PaymentTransaction, Plan
from src.shared.db.session import get_system_session
from src.shared.services.billing import catalog, credits, entitlements, llm_cost, pricing, quotas, refunds, service, usage
from src.shared.services.billing.invoice_pdf import build_invoice_pdf
from src.shared.services.billing.providers import get_provider
from src.shared.services.billing.providers.base import ProviderError, WebhookRejected

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/billing", tags=["Billing"])
webhook_router = APIRouter(prefix="/api/v1/webhooks", tags=["Billing Webhooks"])

MAX_WEBHOOK_BYTES = 64 * 1024


def _http(exc: service.BillingError) -> HTTPException:
    return HTTPException(status_code=exc.http, detail={"code": exc.code, "message": exc.message})


def _require(user, *roles: TenantUserRole) -> None:
    if user.role not in roles:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "هذا الإجراء متاح لمدير الحساب فقط."})


_urls = service.default_urls


def _payments_enabled() -> bool:
    cfg = get_settings()
    return cfg.payment_provider == "moyasar" and bool((cfg.moyasar_secret_key or "").strip())


def _txn_json(t: PaymentTransaction) -> dict:
    return {"transaction_id": str(t.transaction_id), "kind": t.kind, "plan_code": t.plan_code, "billing_interval": t.billing_interval,
            "subtotal_halalas": t.subtotal_halalas, "vat_halalas": t.vat_halalas, "amount_halalas": t.amount_halalas, "currency": t.currency,
            "status": t.status, "provider": t.provider, "failure_reason": t.failure_reason, "created_at": t.created_at.isoformat() if t.created_at else None,
            "paid_at": t.paid_at.isoformat() if t.paid_at else None,
            "checkout_url": t.checkout_url if t.status == "pending" else None}


def _plan_json(p: Plan) -> dict:
    vat = get_settings().billing_vat_rate_bp
    return {"code": p.code, "name_ar": p.name_ar, "description_ar": p.description_ar, "limits": p.limits, "currency": p.currency,
            "is_placeholder": p.is_placeholder, "contact_sales": p.contact_sales,
            "monthly": {"subtotal_halalas": p.price_monthly_halalas, "total_halalas": pricing.with_vat(p.price_monthly_halalas, vat).total},
            "yearly": {"subtotal_halalas": p.price_yearly_halalas, "total_halalas": pricing.with_vat(p.price_yearly_halalas, vat).total,
                       "monthly_equivalent_halalas": p.price_yearly_halalas // 12}}


# ── read ─────────────────────────────────────────────────────────────────────

@router.get("/status", summary="Gate state for the dashboard banner (all roles)")
async def billing_status(user: CurrentUser, session: AuthTenantSession) -> dict:
    sub = await service.ensure_subscription(session, user.tenant_id)
    a = service.access_of(sub)
    return {"mode": a.mode, "status": a.status, "reason": a.reason, "banner": a.banner, "days_left": a.days_left,
            "plan_code": sub.plan_code, "is_exempt": sub.is_exempt}


@router.get("/plans", summary="Plan catalogue")
async def plans(user: CurrentUser, session: AuthTenantSession) -> dict:
    _require(user, TenantUserRole.ADMIN, TenantUserRole.AUDITOR)
    return {"items": [_plan_json(p) for p in await service.list_plans(session)], "vat_rate_bp": get_settings().billing_vat_rate_bp}


@router.get("/subscription", summary="Current subscription, usage and card on file")
async def subscription(user: CurrentUser, session: AuthTenantSession) -> dict:
    _require(user, TenantUserRole.ADMIN, TenantUserRole.AUDITOR)
    sub = await service.ensure_subscription(session, user.tenant_id)
    a = service.access_of(sub)
    plan = await session.scalar(select(Plan).where(Plan.code == sub.plan_code))
    limits = catalog.effective_limits(sub, plan) or {}      # trial caps / add-ons included; {} = unlimited (exempt)
    used = await usage.current_usage(session, user.tenant_id)
    msg_limit = limits.get("messages_per_month")
    pending = await session.scalar(
        select(PaymentTransaction).where(PaymentTransaction.subscription_id == sub.subscription_id, PaymentTransaction.status == "pending",
                                         PaymentTransaction.checkout_url.is_not(None)).order_by(PaymentTransaction.created_at.desc()).limit(1))
    last_failed = await session.scalar(
        select(PaymentTransaction).where(PaymentTransaction.subscription_id == sub.subscription_id, PaymentTransaction.status == "failed")
        .order_by(PaymentTransaction.created_at.desc()).limit(1))
    iso = lambda d: d.isoformat() if d else None  # noqa: E731
    return {
        "payments_enabled": _payments_enabled(), "is_exempt": sub.is_exempt, "viewer_role": getattr(user.role, "value", str(user.role)),
        "plan": _plan_json(plan) if plan else {"code": sub.plan_code}, "billing_interval": sub.billing_interval, "status": sub.status,
        "access": {"mode": a.mode, "reason": a.reason, "banner": a.banner, "days_left": a.days_left},
        "trial_ends_at": iso(sub.trial_ends_at), "current_period_start": iso(sub.current_period_start), "current_period_end": iso(sub.current_period_end),
        "cancel_at_period_end": sub.cancel_at_period_end, "grace_ends_at": iso(sub.grace_ends_at),
        "pending_plan_code": sub.pending_plan_code, "pending_interval": sub.pending_interval,
        "card": {"brand": sub.card_brand, "last4": sub.card_last4} if sub.card_last4 else None, "auto_renew": bool(sub.payment_token),
        "last_payment_error": sub.last_payment_error, "retry_count": sub.retry_count,
        "usage": {"ai_messages": used, "limit": msg_limit, "level": usage.level(used, msg_limit)},
        "credits": await credits.balance(session, user.tenant_id),
        "quota_settings": {"quota_exhausted_action": sub.quota_exhausted_action, "auto_topup_enabled": sub.auto_topup_enabled,
                           "auto_topup_pack": sub.auto_topup_pack},
        "limits": limits, "quotas": await quotas.snapshot(session, user.tenant_id),
        "addons": {"seats": sub.addon_seats, "whatsapp_numbers": sub.addon_whatsapp_numbers},
        "pending_checkout": _txn_json(pending) if pending else None,
        "last_failed": _txn_json(last_failed) if last_failed else None,
    }


@router.get("/transactions", summary="Payment attempts, newest first")
async def transactions(user: CurrentUser, session: AuthTenantSession, limit: int = 50) -> dict:
    _require(user, TenantUserRole.ADMIN, TenantUserRole.AUDITOR)
    rows = (await session.execute(select(PaymentTransaction).order_by(PaymentTransaction.created_at.desc()).limit(min(max(limit, 1), 200)))).scalars()
    return {"items": [_txn_json(t) for t in rows]}


@router.get("/transactions/{transaction_id}", summary="One payment attempt (syncs with the provider while pending)")
async def transaction_detail(user: CurrentUser, session: AuthTenantSession, transaction_id: Annotated[uuid.UUID, Path()]) -> dict:
    _require(user, TenantUserRole.ADMIN, TenantUserRole.AUDITOR)
    t = await session.scalar(select(PaymentTransaction).where(PaymentTransaction.transaction_id == transaction_id))
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "العملية غير موجودة."})
    if t.status == "pending" and user.role == TenantUserRole.ADMIN:
        before = t.status
        await service.sync_transaction(session, t)
        if t.status != before:
            await entitlements.invalidate(user.tenant_id)
    return _txn_json(t)


@router.get("/invoices", summary="Issued invoices")
async def invoices(user: CurrentUser, session: AuthTenantSession) -> dict:
    _require(user, TenantUserRole.ADMIN, TenantUserRole.AUDITOR)
    rows = (await session.execute(select(BillingInvoice).order_by(BillingInvoice.issued_at.desc()).limit(200))).scalars()
    return {"items": [{"invoice_id": str(i.invoice_id), "invoice_number": i.invoice_number, "issued_at": i.issued_at.isoformat(),
                       "description": i.description, "subtotal_halalas": i.subtotal_halalas, "vat_halalas": i.vat_halalas,
                       "total_halalas": i.total_halalas, "currency": i.currency} for i in rows]}


@router.get("/invoices/{invoice_id}/pdf", summary="Invoice as an Arabic PDF")
async def invoice_pdf(user: CurrentUser, session: AuthTenantSession, invoice_id: Annotated[uuid.UUID, Path()]) -> Response:
    _require(user, TenantUserRole.ADMIN, TenantUserRole.AUDITOR)
    inv = await session.scalar(select(BillingInvoice).where(BillingInvoice.invoice_id == invoice_id))
    if inv is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "الفاتورة غير موجودة."})
    return Response(content=build_invoice_pdf(inv), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{inv.invoice_number}.pdf"', "Cache-Control": "private, no-store"})


# ── write ────────────────────────────────────────────────────────────────────

class CheckoutBody(BaseModel):
    plan_code: str = Field(..., min_length=2, max_length=30)
    interval: str = Field("monthly", pattern="^(monthly|yearly)$")


@router.post("/checkout", summary="Subscribe, renew early or change plan")
async def checkout(user: CurrentUser, session: AuthTenantSession, body: CheckoutBody) -> dict:
    _require(user, TenantUserRole.ADMIN)
    if not _payments_enabled():
        raise HTTPException(status_code=503, detail={"code": "PAYMENTS_UNAVAILABLE", "message": "الدفع الإلكتروني غير مفعّل حاليًا. تواصل مع الدعم."})
    try:
        res = await service.change_plan(session, user.tenant_id, body.plan_code, body.interval, _urls())
    except service.BillingError as exc:
        raise _http(exc) from exc
    if res.action == "scheduled":
        return {"action": "scheduled", "effective_at": res.effective_at.isoformat()}
    t = res.transaction
    if t.status == "failed" or not t.checkout_url:
        raise HTTPException(status_code=502, detail={"code": "PROVIDER_ERROR", "message": "تعذّر فتح صفحة الدفع الآن. حاول بعد قليل."})
    return {"action": "checkout", "transaction": _txn_json(t), "checkout_url": t.checkout_url}


# ── top-up packs, add-ons, quota behaviour ───────────────────────────────────

def _vat_json(subtotal: int) -> dict:
    a = pricing.with_vat(subtotal, get_settings().billing_vat_rate_bp)
    return {"subtotal_halalas": a.subtotal, "vat_halalas": a.vat, "total_halalas": a.total}


@router.get("/topup-packs", summary="Extra AI-reply packs, add-ons and what is in the tenant's balance")
async def topup_packs(user: CurrentUser, session: AuthTenantSession) -> dict:
    _require(user, TenantUserRole.ADMIN, TenantUserRole.AUDITOR)
    from src.shared.db.models import PlanAddon

    packs = await service.list_topup_packs(session)
    addons = (await session.execute(select(PlanAddon).where(PlanAddon.is_active.is_(True)))).scalars().all()
    return {
        "packs": [{"code": p.code, "replies": p.replies, "validity_months": p.validity_months, **_vat_json(p.price_halalas)} for p in packs],
        "addons": [{"code": a.code, "name_ar": a.name_ar, "available": a.is_available, "unavailable_reason": a.unavailable_reason,
                    "monthly": _vat_json(a.price_monthly_halalas), "yearly": _vat_json(a.price_monthly_halalas * 10)} for a in addons],
        "credits": await credits.balance(session, user.tenant_id),
    }


class TopupBody(BaseModel):
    pack_code: str = Field(..., min_length=2, max_length=30)


@router.post("/topups", summary="Buy an extra AI-reply pack (hosted payment page)")
async def buy_topup(user: CurrentUser, session: AuthTenantSession, body: TopupBody) -> dict:
    _require(user, TenantUserRole.ADMIN)
    if not _payments_enabled():
        raise HTTPException(status_code=503, detail={"code": "PAYMENTS_UNAVAILABLE", "message": "الدفع الإلكتروني غير مفعّل حاليًا. تواصل مع الدعم."})
    try:
        t = await service.purchase_topup(session, user.tenant_id, body.pack_code, _urls())
    except service.BillingError as exc:
        raise _http(exc) from exc
    if t.status == "failed" or not t.checkout_url:
        raise HTTPException(status_code=502, detail={"code": "PROVIDER_ERROR", "message": "تعذّر فتح صفحة الدفع الآن. حاول بعد قليل."})
    return {"transaction": _txn_json(t), "checkout_url": t.checkout_url}


class AddonBody(BaseModel):
    code: str = Field(..., pattern="^(seat|whatsapp_number)$")
    quantity: int = Field(1, ge=1, le=50)


@router.post("/addons", summary="Add seats / numbers to the current period (pro-rated); recurring from the next renewal")
async def buy_addon(user: CurrentUser, session: AuthTenantSession, body: AddonBody) -> dict:
    _require(user, TenantUserRole.ADMIN)
    if not _payments_enabled():
        raise HTTPException(status_code=503, detail={"code": "PAYMENTS_UNAVAILABLE", "message": "الدفع الإلكتروني غير مفعّل حاليًا. تواصل مع الدعم."})
    try:
        t = await service.purchase_addon(session, user.tenant_id, body.code, body.quantity, _urls())
    except service.BillingError as exc:
        raise _http(exc) from exc
    if t.status == "failed" or not t.checkout_url:
        raise HTTPException(status_code=502, detail={"code": "PROVIDER_ERROR", "message": "تعذّر فتح صفحة الدفع الآن. حاول بعد قليل."})
    return {"transaction": _txn_json(t), "checkout_url": t.checkout_url}


@router.delete("/addons/{code}", summary="Remove add-on units from the next renewal on (no refund of the current period)")
async def remove_addon(user: CurrentUser, session: AuthTenantSession, code: Annotated[str, Path(pattern="^(seat|whatsapp_number)$")],
                       quantity: int = 1) -> dict:
    _require(user, TenantUserRole.ADMIN)
    try:
        sub = await service.reduce_addon(session, user.tenant_id, code, quantity, user.user_id)
    except service.BillingError as exc:
        raise _http(exc) from exc
    await entitlements.invalidate(user.tenant_id)
    return {"addons": {"seats": sub.addon_seats, "whatsapp_numbers": sub.addon_whatsapp_numbers}}


class QuotaSettingsBody(BaseModel):
    quota_exhausted_action: Optional[str] = Field(None, pattern="^(handoff|stop)$")
    auto_topup_enabled: Optional[bool] = None
    auto_topup_pack: Optional[str] = Field(None, max_length=30)


@router.put("/quota-settings", summary="What happens when the AI quota is spent + opt-in automatic recharge")
async def quota_settings(user: CurrentUser, session: AuthTenantSession, body: QuotaSettingsBody) -> dict:
    _require(user, TenantUserRole.ADMIN)
    try:
        sub = await service.update_quota_settings(session, user.tenant_id, quota_exhausted_action=body.quota_exhausted_action,
                                                  auto_topup_enabled=body.auto_topup_enabled, auto_topup_pack=body.auto_topup_pack, actor=user.user_id)
    except service.BillingError as exc:
        raise _http(exc) from exc
    await entitlements.invalidate(user.tenant_id)
    return {"quota_exhausted_action": sub.quota_exhausted_action, "auto_topup_enabled": sub.auto_topup_enabled, "auto_topup_pack": sub.auto_topup_pack}


@router.post("/cancel", summary="Cancel at the end of the paid period")
async def cancel(user: CurrentUser, session: AuthTenantSession) -> dict:
    _require(user, TenantUserRole.ADMIN)
    try:
        sub = await service.cancel_at_period_end(session, user.tenant_id, user.user_id)
    except service.BillingError as exc:
        raise _http(exc) from exc
    return {"cancel_at_period_end": True, "effective_at": sub.current_period_end.isoformat() if sub.current_period_end else None}


@router.post("/resume", summary="Undo a scheduled cancellation")
async def resume(user: CurrentUser, session: AuthTenantSession) -> dict:
    _require(user, TenantUserRole.ADMIN)
    try:
        await service.resume(session, user.tenant_id, user.user_id)
    except service.BillingError as exc:
        raise _http(exc) from exc
    return {"cancel_at_period_end": False}


# ── staff ────────────────────────────────────────────────────────────────────

class ManualActivation(BaseModel):
    plan_code: str
    interval: str = Field("monthly", pattern="^(monthly|yearly)$")
    periods: int = Field(1, ge=1, le=24)
    amount_halalas: Optional[int] = Field(None, ge=0, description="Amount actually received, VAT-inclusive. Default: list price × periods + VAT.")
    reference: str = Field("", max_length=120, description="Bank transfer reference")
    note: str = Field("", max_length=300)


@router.post("/admin/tenants/{tenant_id}/activate", status_code=201, summary="Staff: activate a tenant that paid outside the gateway")
async def manual_activate(tenant_id: Annotated[uuid.UUID, Path()], body: ManualActivation,
                          x_billing_key: Annotated[str | None, Header()] = None) -> dict:
    expected = get_settings().billing_admin_api_key
    if not expected:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Not found."})        # endpoint is off
    if not x_billing_key or not hmac.compare_digest(x_billing_key.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "Invalid billing key."})
    try:
        async with get_system_session() as session:
            t = await service.manual_activate(session, tenant_id, body.plan_code, body.interval, periods=body.periods, note=body.note,
                                              amount_halalas=body.amount_halalas, reference=body.reference)
            out = _txn_json(t)
    except service.BillingError as exc:
        raise _http(exc) from exc
    await entitlements.invalidate(tenant_id)
    return out


@router.get("/admin/unit-economics", summary="Platform staff: real AI cost vs revenue and margin per tenant for a month")
async def unit_economics(staff: StaffUser, period: Annotated[Optional[str], Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")] = None) -> dict:
    return await llm_cost.margin_report(period or usage.period_of())


class RefundBody(BaseModel):
    transaction_id: uuid.UUID
    reason: str = Field(..., min_length=5, max_length=1000)
    amount_halalas: Optional[int] = Field(None, ge=1, description="Lower than the computed maximum only; default = the full computed amount")


@router.get("/admin/transactions/{transaction_id}/refund-quote", summary="Staff: what a refund of this payment would be (14-day guarantee, first payment only)")
async def refund_quote(staff: StaffUser, transaction_id: Annotated[uuid.UUID, Path()]) -> dict:
    async with get_system_session() as session:
        txn = await session.scalar(select(PaymentTransaction).where(PaymentTransaction.transaction_id == transaction_id))
        if txn is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "العملية غير موجودة."})
        return await refunds.quote(session, txn)


@router.post("/admin/refunds", status_code=201, summary="Staff: refund a first payment (provider refund + credit note + audit)")
async def issue_refund(staff: StaffUser, body: RefundBody) -> dict:
    try:
        async with get_system_session() as session:
            out = await refunds.issue(session, body.transaction_id, reason=body.reason, staff_email=str(getattr(staff, "email", "")), amount_halalas=body.amount_halalas)
            tid = (await session.scalar(select(PaymentTransaction.tenant_id).where(PaymentTransaction.transaction_id == body.transaction_id)))
    except service.BillingError as exc:
        raise _http(exc) from exc
    if out.get("failed"):                       # the failed attempt + audit row were committed above; now tell the caller
        raise HTTPException(status_code=502, detail={"code": out["code"], "message": out["message"], "refund_id": out["refund_id"]})
    await entitlements.invalidate(tid)
    return out


@router.get("/credit-notes", summary="Credit notes issued to this workspace")
async def credit_notes(user: CurrentUser, session: AuthTenantSession) -> dict:
    _require(user, TenantUserRole.ADMIN, TenantUserRole.AUDITOR)
    from src.shared.db.models import CreditNote

    rows = (await session.execute(select(CreditNote).order_by(CreditNote.issued_at.desc()).limit(100))).scalars()
    return {"items": [{"credit_note_id": str(n.credit_note_id), "credit_note_number": n.credit_note_number, "issued_at": n.issued_at.isoformat(),
                       "subtotal_halalas": n.subtotal_halalas, "vat_halalas": n.vat_halalas, "total_halalas": n.total_halalas, "reason": n.reason} for n in rows]}


@router.get("/credit-notes/{credit_note_id}/pdf", summary="Credit note as an Arabic PDF")
async def credit_note_pdf(user: CurrentUser, session: AuthTenantSession, credit_note_id: Annotated[uuid.UUID, Path()]) -> Response:
    _require(user, TenantUserRole.ADMIN, TenantUserRole.AUDITOR)
    from types import SimpleNamespace

    from src.shared.db.models import CreditNote

    n = await session.scalar(select(CreditNote).where(CreditNote.credit_note_id == credit_note_id))
    if n is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "الإشعار غير موجود."})
    doc = SimpleNamespace(invoice_number=n.credit_note_number, issued_at=n.issued_at, currency=n.currency, seller=n.seller, buyer=n.buyer,
                          description="استرداد — " + n.reason, period_start=None, period_end=None, subtotal_halalas=n.subtotal_halalas,
                          vat_halalas=n.vat_halalas, total_halalas=n.total_halalas, vat_rate_bp=n.vat_rate_bp, reason=n.reason)
    return Response(content=build_invoice_pdf(doc, credit_note=True), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{n.credit_note_number}.pdf"', "Cache-Control": "private, no-store"})


# ── provider webhook ─────────────────────────────────────────────────────────

@webhook_router.post("/payments", summary="Payment provider webhook (signature/secret verified)", include_in_schema=False)
async def payment_webhook(request: Request) -> dict:
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BYTES:
        raise HTTPException(status_code=413, detail={"code": "TOO_LARGE", "message": "Body too large."})
    try:
        event = get_provider().parse_webhook(request.headers, body)
    except WebhookRejected as exc:
        logger.warning("payment_webhook_rejected", reason=str(exc))
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "Invalid webhook."}) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail={"code": "UNAVAILABLE", "message": "Payments are not configured."}) from exc
    try:
        async with get_system_session() as session:
            result = await service.handle_webhook(session, event)
    except ProviderError as exc:           # cannot verify with the provider right now → 503 so it retries later
        logger.warning("payment_webhook_verify_failed", code=exc.code)
        raise HTTPException(status_code=503, detail={"code": "UNAVAILABLE", "message": "Try again later."}) from exc
    if result.get("tenant_id"):
        await entitlements.invalidate(result["tenant_id"])
    return {"ok": True, "outcome": result["outcome"]}

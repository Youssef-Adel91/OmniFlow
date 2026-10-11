"""Customer domain logic shared by the REST router and the bulk importer.

Identity: `unified_phone` is the messaging contact key (kept for the pipeline), `phone_e164` the real
phone, `channel_user_ids` the per-channel platform ids. Consent (PDPL): `marketing_opt_in` is False
until a person with authority records consent; `opt_out_at` always wins and is never reverted by an
import. Every consent change and merge is written to `audit_logs`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError

from src.shared.db.models import Appointment, AuditLog, Conversation, Customer, CustomerReport
from src.shared.services.bulk_import.phone import normalize_phone


class DuplicateCustomerError(ValueError):
    def __init__(self, existing_id: uuid.UUID, message: str = "يوجد عميل بنفس رقم الجوال.") -> None:
        super().__init__(message)
        self.existing_id = existing_id


EDITABLE = ("display_name", "email", "city", "tags", "notes", "is_vip", "is_processing_restricted")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def audit(session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID | None, action: str, customer_id: uuid.UUID | str | None,
                details: dict[str, Any] | None = None) -> None:
    session.add(AuditLog(tenant_id=tenant_id, actor_user_id=actor_id, action=action, entity_type="customer",
                         entity_id=str(customer_id) if customer_id else None, details=details))


def apply_marketing_consent(customer: Customer, opt_in: bool, source: str, *, now: datetime | None = None) -> bool:
    """
    Record a consent decision. Returns True when something changed.
    Withdrawal (opt_in=False) always applies and stamps `opt_out_at`; granting consent to someone who
    has opted out is refused (they must opt in again themselves) and returns False.
    """
    now = now or _now()
    if opt_in:
        if customer.opt_out_at is not None or customer.marketing_opt_in:
            return False
        customer.marketing_opt_in, customer.opt_in_source, customer.opt_in_at = True, source[:120], now
        return True
    if not customer.marketing_opt_in and customer.opt_out_at is not None:
        return False
    customer.marketing_opt_in, customer.opt_out_at = False, now
    return True


_OPT_OUT_WORDS = {
    "stop", "unsubscribe", "stop all", "cancel subscription", "opt out", "optout",
    "الغاء الاشتراك", "الغاء اشتراكي", "ايقاف", "ايقاف الاشتراك", "توقف", "لا ارغب بالتواصل", "لا اريد استلام الرسائل",
}


def is_opt_out_keyword(text: str) -> bool:
    """True only when the WHOLE message is an unsubscribe keyword ("STOP", "إلغاء الاشتراك"…) -- never inside a sentence."""
    from src.shared.services.bulk_import.text import norm_text

    return norm_text(text) in {norm_text(w) for w in _OPT_OUT_WORDS}


async def find_by_phone(session, tenant_id: uuid.UUID, phone: str) -> Customer | None:
    """The customer owning this real phone number, whichever identity column holds it."""
    return (await session.scalars(
        select(Customer).where(Customer.tenant_id == tenant_id,
                               (Customer.phone_e164 == phone) | (Customer.unified_phone == phone))
        .order_by(Customer.created_at).limit(1))).first()


async def create_customer(session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID | None, phone: str, source: str = "manual",
                          marketing_opt_in: bool = False, opt_in_source: str | None = None, **fields: Any) -> Customer:
    phone = normalize_phone(phone)
    existing = await find_by_phone(session, tenant_id, phone)
    if existing is not None:
        raise DuplicateCustomerError(existing.customer_id)
    customer = Customer(tenant_id=tenant_id, unified_phone=phone, phone_e164=phone, source=source,
                        channel_user_ids={}, **{k: v for k, v in fields.items() if k in EDITABLE and v is not None})
    session.add(customer)
    try:
        await session.flush()
    except IntegrityError as exc:                                   # lost a race with another writer
        raise DuplicateCustomerError(uuid.UUID(int=0)) from exc
    if marketing_opt_in:
        apply_marketing_consent(customer, True, opt_in_source or source)
    await audit(session, tenant_id=tenant_id, actor_id=actor_id, action="customer.created", customer_id=customer.customer_id,
                details={"source": source, "marketing_opt_in": customer.marketing_opt_in})
    if customer.marketing_opt_in:
        await audit(session, tenant_id=tenant_id, actor_id=actor_id, action="customer.opt_in_granted",
                    customer_id=customer.customer_id, details={"source": customer.opt_in_source})
    await session.flush()
    from src.shared.services.developers.webhooks_out import emit

    await emit(session, tenant_id, "customer.created", {"customer_id": str(customer.customer_id), "phone": customer.phone_e164, "source": source})
    return customer


async def set_phone(session, customer: Customer, phone: str) -> None:
    """Attach/replace the real phone. A phone owned by another customer is a merge candidate, not an overwrite."""
    phone = normalize_phone(phone)
    other = await find_by_phone(session, customer.tenant_id, phone)
    if other is not None and other.customer_id != customer.customer_id:
        raise DuplicateCustomerError(other.customer_id)
    customer.phone_e164 = phone


async def merge_customers(session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID | None, keep_id: uuid.UUID,
                          drop_id: uuid.UUID) -> Customer:
    """
    Fold `drop` into `keep` (same person seen on two identities). Conversations, reports and appointments
    move over; identities, tags, notes and missing profile fields are merged; consent is conservative
    (an opt-out on either side wins). `drop` is then deleted.
    """
    if keep_id == drop_id:
        raise ValueError("لا يمكن دمج العميل مع نفسه.")
    keep = await session.scalar(select(Customer).where(Customer.customer_id == keep_id, Customer.tenant_id == tenant_id))
    drop = await session.scalar(select(Customer).where(Customer.customer_id == drop_id, Customer.tenant_id == tenant_id))
    if keep is None or drop is None:
        raise LookupError("العميل غير موجود")

    ids = {**(drop.channel_user_ids or {}), **(keep.channel_user_ids or {})}
    # PSID-style contact keys of the dropped customer must stay resolvable after the merge.
    if drop.phone_e164 is None and drop.unified_phone not in ids.values():
        ids.setdefault(drop.source if drop.source not in ("manual", "import", "whatsapp") else "other", drop.unified_phone)
    dropped_phone, dropped_key = drop.phone_e164, drop.unified_phone

    for model in (Conversation, CustomerReport, Appointment):
        await session.execute(update(model).where(model.customer_id == drop_id).values(customer_id=keep_id))

    drop_opted_out = drop.opt_out_at is not None
    drop_consent = (drop.marketing_opt_in, drop.opt_in_source, drop.opt_in_at)
    drop_notes, drop_tags = drop.notes, list(drop.tags or [])
    drop_name, drop_email, drop_city = drop.display_name or drop.whatsapp_profile_name, drop.email, drop.city
    drop_vip, drop_restricted = drop.is_vip, drop.is_processing_restricted

    await session.delete(drop)
    await session.flush()                                          # frees phone_e164 / unique keys

    if keep.phone_e164 is None and dropped_phone:
        keep.phone_e164 = dropped_phone
    keep.channel_user_ids = ids
    keep.tags = list(dict.fromkeys([*(keep.tags or []), *drop_tags]))[:20]
    keep.display_name = keep.display_name or drop_name
    keep.email = keep.email or drop_email
    keep.city = keep.city or drop_city
    if drop_notes and drop_notes not in (keep.notes or ""):
        keep.notes = f"{keep.notes}\n{drop_notes}".strip() if keep.notes else drop_notes
    keep.is_vip = keep.is_vip or drop_vip
    keep.is_processing_restricted = keep.is_processing_restricted or drop_restricted
    if drop_opted_out and keep.opt_out_at is None:
        keep.marketing_opt_in, keep.opt_out_at = False, _now()
    elif drop_consent[0] and not keep.marketing_opt_in and keep.opt_out_at is None:
        keep.marketing_opt_in, keep.opt_in_source, keep.opt_in_at = True, drop_consent[1], drop_consent[2]

    await audit(session, tenant_id=tenant_id, actor_id=actor_id, action="customer.merged", customer_id=keep_id,
                details={"merged_from": str(drop_id), "dropped_phone": dropped_phone, "dropped_contact_key": dropped_key})
    await session.flush()
    return keep

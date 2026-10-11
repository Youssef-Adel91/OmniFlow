"""PDPL data-subject tools: export a customer's data, irreversibly anonymise a customer, read the consent history.

Anonymisation keeps the row and the conversation shells (so counts, funnels and billing/accounting aggregates stay
truthful) but removes everything that identifies the person: names, phones, email, notes, tags, extracted profile, channel
ids, message text and media pointers. The customer is also opted out and processing-restricted so no one can contact them.
Audit-log rows are kept (they hold only ids and action names, never PII) — that is the accountability trail the law expects.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select, update

from src.shared.db.models import AuditLog, BroadcastDelivery, Conversation, Customer, Message

logger = structlog.get_logger(__name__)
CONSENT_ACTIONS = ("customer.opt_in_granted", "customer.opt_out", "customer.created", "customer.merged", "customer.import_consent")
ERASED_NAME = "عميل محذوف"


class PrivacyError(Exception):
    def __init__(self, code: str, message: str, http: int = 409):
        super().__init__(message)
        self.code, self.message, self.http = code, message, http


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


async def _customer(session, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> Customer:
    c = await session.scalar(select(Customer).where(Customer.customer_id == customer_id, Customer.tenant_id == tenant_id))
    if c is None:
        raise PrivacyError("NOT_FOUND", "العميل غير موجود.", 404)
    return c


async def consent_history(session, tenant_id: uuid.UUID, customer_id: uuid.UUID, limit: int = 200) -> list[dict[str, Any]]:
    rows = (await session.execute(select(AuditLog).where(
        AuditLog.tenant_id == tenant_id, AuditLog.entity_type == "customer", AuditLog.entity_id == str(customer_id),
        AuditLog.action.in_(CONSENT_ACTIONS)).order_by(AuditLog.created_at.desc()).limit(limit))).scalars().all()
    return [{"action": r.action, "at": _iso(r.created_at), "actor_user_id": str(r.actor_user_id) if r.actor_user_id else None,
             "source": (r.details or {}).get("source")} for r in rows]


async def export_customer(session, tenant_id: uuid.UUID, customer_id: uuid.UUID, actor_id: uuid.UUID | None) -> dict[str, Any]:
    c = await _customer(session, tenant_id, customer_id)
    convs = (await session.execute(select(Conversation).where(Conversation.customer_id == customer_id, Conversation.tenant_id == tenant_id)
                                   .order_by(Conversation.created_at))).scalars().all()
    conversations = []
    for conv in convs:
        msgs = (await session.execute(select(Message).where(Message.conversation_id == conv.conversation_id).order_by(Message.created_at))).scalars().all()
        conversations.append({"conversation_id": str(conv.conversation_id), "channel": str(getattr(conv.channel, "value", conv.channel)),
                              "status": str(getattr(conv.status, "value", conv.status)), "created_at": _iso(conv.created_at),
                              "messages": [{"at": _iso(m.created_at), "from": m.sender_type, "type": str(getattr(m.message_type, "value", m.message_type)),
                                            "text": m.text_content, "has_media": bool(m.s3_media_url)} for m in msgs]})
    deliveries = (await session.execute(select(BroadcastDelivery).where(BroadcastDelivery.customer_id == customer_id, BroadcastDelivery.tenant_id == tenant_id)
                                        .order_by(BroadcastDelivery.created_at))).scalars().all()
    session.add(AuditLog(tenant_id=tenant_id, actor_user_id=actor_id, action="privacy.export", entity_type="customer", entity_id=str(customer_id),
                         details={"conversations": len(conversations)}))
    await session.flush()
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "customer": {"customer_id": str(c.customer_id), "display_name": c.display_name, "whatsapp_profile_name": c.whatsapp_profile_name,
                     "phone_e164": c.phone_e164, "contact_key": c.unified_phone, "channel_user_ids": c.channel_user_ids, "email": c.email, "city": c.city,
                     "tags": c.tags, "notes": c.notes, "source": c.source, "created_at": _iso(c.created_at), "archived_at": _iso(c.archived_at),
                     "extracted_profile": c.extracted_profile, "engagement_score": c.engagement_score},
        "consent": {"marketing_opt_in": c.marketing_opt_in, "opt_in_source": c.opt_in_source, "opt_in_at": _iso(c.opt_in_at),
                    "opt_out_at": _iso(c.opt_out_at), "processing_restricted": c.is_processing_restricted,
                    "history": await consent_history(session, tenant_id, customer_id)},
        "conversations": conversations,
        "broadcast_deliveries": [{"campaign_id": str(d.campaign_id), "status": d.delivery_status, "sent_at": _iso(d.sent_at),
                                  "delivered_at": _iso(d.delivered_at), "read_at": _iso(d.read_at)} for d in deliveries],
    }


async def anonymize_customer(session, tenant_id: uuid.UUID, customer_id: uuid.UUID, actor_id: uuid.UUID | None, reason: str = "",
                             file_store=None) -> dict[str, int]:
    from src.shared.services import erasure_files

    c = await _customer(session, tenant_id, customer_id)
    if c.erased_at is not None:
        raise PrivacyError("ALREADY_ERASED", "تم محو بيانات هذا العميل مسبقًا.", 409)
    now = datetime.now(timezone.utc)
    # Files first: if they cannot be deleted we stop BEFORE wiping the pointers, so nothing is left orphaned and untraceable.
    targets = await erasure_files.collect(session, tenant_id, c)
    try:
        files_removed = await erasure_files.delete(targets, tenant_id, file_store)
    except erasure_files.FilesNotDeleted as exc:
        raise PrivacyError("FILES_NOT_DELETED", f"تعذّر حذف {len(exc.failed)} ملف من التخزين؛ لم يُمحَ العميل. أعد المحاولة لاحقًا.", 502) from exc
    await erasure_files.forget_reports(session, tenant_id, targets.report_ids)
    conv_ids = list((await session.execute(select(Conversation.conversation_id).where(Conversation.customer_id == customer_id, Conversation.tenant_id == tenant_id))).scalars())
    wiped = 0
    if conv_ids:
        res = await session.execute(update(Message).where(Message.conversation_id.in_(conv_ids)).values(text_content=None, s3_media_url=None))
        wiped = res.rowcount or 0
        await session.execute(update(Conversation).where(Conversation.conversation_id.in_(conv_ids)).values(status="closed"))
    # Unique identity columns get an opaque, per-row placeholder so the row stays valid but identifies no one.
    token = f"e:{customer_id.hex[:18]}"            # unified_phone is VARCHAR(20): 2 + 18 hex chars = 72 bits, unique per customer
    c.display_name, c.whatsapp_profile_name = ERASED_NAME, None
    c.unified_phone, c.phone_e164 = token, None
    c.channel_user_ids, c.email, c.city, c.notes, c.tags = {}, None, None, None, []
    c.extracted_profile, c.extracted_profile_updated_at, c.vault_s3_prefix = None, None, None
    c.marketing_opt_in, c.opt_out_at, c.is_processing_restricted = False, now, True
    c.archived_at, c.erased_at = now, now
    session.add(AuditLog(tenant_id=tenant_id, actor_user_id=actor_id, action="privacy.anonymize", entity_type="customer", entity_id=str(customer_id),
                         details={"messages_wiped": wiped, "conversations": len(conv_ids), "files_deleted": files_removed, "reason": reason[:200]}))
    await session.flush()
    try:                                                            # cached chat context must go too
        from src.shared.redis_client import redis_mgr
        for cid in conv_ids:
            await redis_mgr.clear_conversation_history(tenant_id, cid)
    except Exception:  # noqa: BLE001 - Redis is a cache; the DB wipe above is the source of truth
        logger.warning("privacy_redis_clear_failed", customer_id=str(customer_id))
    return {"messages_wiped": wiped, "conversations": len(conv_ids), "files_deleted": files_removed}


async def summary(session, tenant_id: uuid.UUID) -> dict[str, int]:
    from sqlalchemy import func

    total = await session.scalar(select(func.count()).select_from(Customer).where(Customer.tenant_id == tenant_id)) or 0
    erased = await session.scalar(select(func.count()).select_from(Customer).where(Customer.tenant_id == tenant_id, Customer.erased_at.is_not(None))) or 0
    opted = await session.scalar(select(func.count()).select_from(Customer).where(Customer.tenant_id == tenant_id, Customer.marketing_opt_in.is_(True))) or 0
    restricted = await session.scalar(select(func.count()).select_from(Customer).where(Customer.tenant_id == tenant_id, Customer.is_processing_restricted.is_(True))) or 0
    return {"customers": total, "erased": erased, "marketing_opt_in": opted, "processing_restricted": restricted}

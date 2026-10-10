"""Human handoff: what happens when the AI route decides "a person must take this conversation".

Before this, a HUMAN_ESCALATION route (explicit request for an agent, low classifier confidence) ended in the invoker
with a bare `return`: the conversation stayed AI_ACTIVE, nobody was assigned and the customer got NO reply. Now (per
tenant `handoff` + `business_hours` preferences):
  * the conversation becomes ESCALATED (the inbox shows it; the AI stops answering it),
  * with round-robin on, it is assigned to the active agent with the fewest open escalated/human conversations,
  * the customer gets the handoff message — or, outside business hours, the out-of-hours message with the next opening time.
PDPL-restricted customers are escalated silently (no automated message to someone who objected to processing).
`auto_escalate=False` keeps the previous behaviour (no state change, no message).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import structlog
from sqlalchemy import func, select, update

from src.shared.core.enums import ConversationStatus, TenantUserRole
from src.shared.db.models import Conversation, TenantUser
from src.shared.db.session import get_tenant_session
from src.shared.services import tenant_prefs as prefs_svc

logger = structlog.get_logger(__name__)
SILENT_REASONS = ("pdpl_processing_restricted", "human_agent_active")


@dataclass
class HandoffResult:
    applied: bool = False
    text: str | None = None
    agent_id: uuid.UUID | None = None
    within_hours: bool = True


async def pick_agent(session, tenant_id: uuid.UUID) -> uuid.UUID | None:
    """Least-loaded active agent (fewest open escalated/human conversations; ties -> longest idle). Admins only if no agents."""
    busy = (
        select(Conversation.assigned_agent_id.label("aid"), func.count().label("n"), func.max(Conversation.updated_at).label("last"))
        .where(Conversation.tenant_id == tenant_id, Conversation.assigned_agent_id.is_not(None),
               Conversation.status.in_([ConversationStatus.ESCALATED.value, ConversationStatus.HUMAN_ACTIVE.value]))
        .group_by(Conversation.assigned_agent_id).subquery()
    )
    for role in (TenantUserRole.AGENT, TenantUserRole.ADMIN):
        row = (await session.execute(
            select(TenantUser.user_id)
            .outerjoin(busy, busy.c.aid == TenantUser.user_id)
            .where(TenantUser.tenant_id == tenant_id, TenantUser.is_active.is_(True), TenantUser.role == role)
            .order_by(func.coalesce(busy.c.n, 0), busy.c.last.asc().nulls_first(), TenantUser.user_id)
            .limit(1))).first()
        if row:
            return row[0]
    return None


async def perform_handoff(tenant_id: uuid.UUID, conversation_id: uuid.UUID, *, reason: str, now: datetime | None = None) -> HandoffResult:
    now = now or datetime.now(timezone.utc)
    prefs = await prefs_svc.get_prefs(tenant_id)
    handoff, hours = prefs["handoff"], prefs["business_hours"]
    if not handoff["auto_escalate"] or reason == "human_agent_active":
        return HandoffResult()

    async with get_tenant_session(tenant_id) as session:
        agent_id = await pick_agent(session, tenant_id) if handoff["round_robin"] else None
        values: dict = {"status": ConversationStatus.ESCALATED}
        if agent_id:
            values["assigned_agent_id"] = agent_id
        res = await session.execute(
            update(Conversation).where(Conversation.conversation_id == conversation_id,
                                       Conversation.tenant_id == tenant_id,
                                       Conversation.status == ConversationStatus.AI_ACTIVE).values(**values))
        if not res.rowcount:                     # a human took it / it was closed meanwhile: leave it alone
            return HandoffResult()
        from src.shared.services.notifications import notify_safe

        # Tell the assigned agent (or every admin when nobody could be picked) — in-app by default, email only if they opted in.
        await notify_safe(session, tenant_id, "handoff", "محادثة جديدة محوّلة إليك" if agent_id else "محادثة تنتظر موظفًا",
                          "يحتاج عميل إلى موظف بشري." + (f" السبب: {reason[:80]}" if reason and reason not in SILENT_REASONS else ""),
                          f"/inbox?conversation={conversation_id}", user_ids=[agent_id] if agent_id else None,
                          dedupe_key=f"{conversation_id}:{int(now.timestamp()) // 600}")

    open_now = prefs_svc.is_open(hours, now)
    text: str | None
    if reason in SILENT_REASONS:
        text = None
    elif open_now:
        text = handoff["handoff_message"]
    else:
        text = hours["out_of_hours_message"].replace("{next_open}", prefs_svc.describe_next_open(hours, now))
    logger.info("handoff_performed", tenant_id=str(tenant_id), conversation_id=str(conversation_id), reason=reason[:60],
                assigned=str(agent_id) if agent_id else None, within_hours=open_now)
    return HandoffResult(applied=True, text=text, agent_id=agent_id, within_hours=open_now)

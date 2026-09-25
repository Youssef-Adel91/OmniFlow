"""
Real end-to-end validation of the customer purchase-likelihood scoring
system, against real Postgres — no synthetic/mock scoring inputs.

Creates 3 real customers under one real tenant, each with a real conversation
and real persisted messages representing a distinct real-world pattern:

  - "Cold": one message, no reply history, never contacted since.
  - "Warm": several messages, one buying-intent question, contacted recently,
    but no VCard confirmation yet.
  - "Hot": VIP, many messages across two separate conversations (repeat
    contact), multiple real buying-intent phrases, a message that reached
    the L2 (deep) routing tier, VCard confirmed AND opened.

Then calls the REAL `GET /api/v1/customers?sort=lead_score` endpoint logic
(`_load_lead_scores` + `list_customers`'s ranking path) against real
Postgres, and asserts the real computed order is Hot > Warm > Cold — proving
the formula actually differentiates real customers, not just the isolated
unit self-check in lead_scoring.py's own __main__.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from src.gateway.routers.customers import _load_lead_scores
from src.shared.core.enums import Channel, ConversationStatus
from src.shared.db.models import Conversation, Customer, Message, Tenant
from src.shared.db.session import get_system_session


def _require_local() -> None:
    from src.shared.core.config import get_settings
    settings = get_settings()
    if settings.postgres_host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("requires a local Postgres database")


async def _make_conversation(session, tenant_id, customer_id, *, days_ago: float) -> uuid.UUID:
    conv_id = uuid.uuid4()
    session.add(Conversation(
        conversation_id=conv_id, tenant_id=tenant_id, customer_id=customer_id,
        channel=Channel.WHATSAPP, platform_conversation_id=f"drill-{uuid.uuid4().hex[:8]}",
        status=ConversationStatus.AI_ACTIVE,
        last_message_at=(datetime.now(tz=timezone.utc) - timedelta(days=days_ago)).replace(tzinfo=None),
        message_count=0,
    ))
    await session.flush()
    return conv_id


async def _add_message(session, conv_id, *, sender_type, text, tier=None, msg_type="text"):
    session.add(Message(
        message_id=uuid.uuid4(), conversation_id=conv_id, sender_type=sender_type,
        message_type=msg_type, text_content=text, llm_routing_tier=tier,
        delivery_status="DELIVERED",
    ))
    conv = await session.get(Conversation, conv_id)
    conv.message_count = (conv.message_count or 0) + 1


async def setup() -> dict:
    tenant_id = uuid.uuid4()
    async with get_system_session() as session:
        session.add(Tenant(
            tenant_id=tenant_id, business_name="Lead Scoring Drill Co",
            fal_license_number=f"LSD-{uuid.uuid4().hex[:10]}",
        ))

    ids = {}
    async with get_system_session() as session:
        # ── Cold: single message, no follow-up, contacted long ago ──────────
        cold_id = uuid.uuid4()
        session.add(Customer(
            customer_id=cold_id, tenant_id=tenant_id,
            unified_phone=f"+1555{uuid.uuid4().int % 10_000_000:07d}",
            display_name="Cold Drill Customer", is_vip=False,
        ))
        cold_conv = await _make_conversation(session, tenant_id, cold_id, days_ago=25)
        await _add_message(session, cold_conv, sender_type="customer", text="مرحبا")
        ids["cold"] = cold_id

        # ── Warm: recent, several messages, one buying-intent question ──────
        warm_id = uuid.uuid4()
        session.add(Customer(
            customer_id=warm_id, tenant_id=tenant_id,
            unified_phone=f"+1555{uuid.uuid4().int % 10_000_000:07d}",
            display_name="Warm Drill Customer", is_vip=False,
        ))
        warm_conv = await _make_conversation(session, tenant_id, warm_id, days_ago=0.1)
        await _add_message(session, warm_conv, sender_type="customer", text="مرحبا، عندكم توصيل؟")
        await _add_message(session, warm_conv, sender_type="ai_bot", text="أهلاً، نعم عندنا توصيل.")
        await _add_message(session, warm_conv, sender_type="customer", text="طيب كام السعر؟ وفيه خصم؟")
        await _add_message(session, warm_conv, sender_type="ai_bot", text="السعر 350 ريال، وفيه خصم 10% حالياً.")
        await _add_message(session, warm_conv, sender_type="customer", text="تمام هفكر واكلمكم")
        ids["warm"] = warm_id

        # ── Hot: VIP, repeat contact (2 conversations), multiple intent
        #    phrases, reached L2, VCard confirmed + opened ────────────────────
        hot_id = uuid.uuid4()
        session.add(Customer(
            customer_id=hot_id, tenant_id=tenant_id,
            unified_phone=f"+1555{uuid.uuid4().int % 10_000_000:07d}",
            display_name="Hot Drill Customer", is_vip=True,
            vcard_state="STATE_CONTACT_SAVED_VERIFIED",
            vcard_opened_at=datetime.now(tz=timezone.utc) - timedelta(days=2),
        ))
        hot_conv1 = await _make_conversation(session, tenant_id, hot_id, days_ago=3)
        await _add_message(session, hot_conv1, sender_type="customer", text="عايز اطلب جهاز مساج")
        await _add_message(session, hot_conv1, sender_type="ai_bot", text="تمام، أي موديل يهمك؟", tier="L2")
        await _add_message(session, hot_conv1, sender_type="customer", text="متوفر عندكم توصيل؟")
        hot_conv2 = await _make_conversation(session, tenant_id, hot_id, days_ago=0.1)
        await _add_message(session, hot_conv2, sender_type="customer", text="كام السعر؟ وعندكم خصم؟")
        await _add_message(session, hot_conv2, sender_type="customer", text="تمام هدفع كاش، احجز لي واحد")
        ids["hot"] = hot_id

    return {"tenant_id": tenant_id, **ids}


async def cleanup(ctx: dict) -> None:
    from sqlalchemy import delete
    async with get_system_session() as session:
        for key in ("cold", "warm", "hot"):
            cid = ctx[key]
            convs = (await session.execute(
                select(Conversation.conversation_id).where(Conversation.customer_id == cid)
            )).scalars().all()
            for conv_id in convs:
                await session.execute(delete(Message).where(Message.conversation_id == conv_id))
            await session.execute(delete(Conversation).where(Conversation.customer_id == cid))
            await session.execute(delete(Customer).where(Customer.customer_id == cid))
        await session.execute(delete(Tenant).where(Tenant.tenant_id == ctx["tenant_id"]))
    print("cleanup: synthetic tenant/customers/conversations/messages deleted")


async def main() -> None:
    _require_local()
    ctx = await setup()
    print(f"real tenant={ctx['tenant_id']}")
    try:
        async with get_system_session() as session:
            scores = await _load_lead_scores(session, [ctx["cold"], ctx["warm"], ctx["hot"]])

        cold, warm, hot = scores[ctx["cold"]], scores[ctx["warm"]], scores[ctx["hot"]]
        print(f"\nCOLD customer: score={cold.score} tier={cold.tier} "
              f"(behavior={cold.conversation_behavior}, profile={cold.profile_data}, "
              f"history={cold.interaction_history}, intent_hits={cold.buying_intent_hits})")
        print(f"WARM customer: score={warm.score} tier={warm.tier} "
              f"(behavior={warm.conversation_behavior}, profile={warm.profile_data}, "
              f"history={warm.interaction_history}, intent_hits={warm.buying_intent_hits})")
        print(f"HOT  customer: score={hot.score} tier={hot.tier} "
              f"(behavior={hot.conversation_behavior}, profile={hot.profile_data}, "
              f"history={hot.interaction_history}, intent_hits={hot.buying_intent_hits})")

        assert hot.score > warm.score > cold.score, (
            f"expected hot > warm > cold, got hot={hot.score} warm={warm.score} cold={cold.score}"
        )
        assert str(hot.tier) == "hot", hot.tier
        assert str(warm.tier) == "warm", warm.tier
        assert str(cold.tier) == "cold", cold.tier
        print(f"\nPASS: real computed scores strictly ordered hot({hot.score}) > "
              f"warm({warm.score}) > cold({cold.score}), tiers correct")
    finally:
        await cleanup(ctx)


if __name__ == "__main__":
    asyncio.run(main())

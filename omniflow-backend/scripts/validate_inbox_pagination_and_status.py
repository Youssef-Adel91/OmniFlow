"""
Real end-to-end validation of item 12: message pagination for long
conversations, and delivery-status propagation over SSE.

Real Postgres (105 real messages in one conversation) and real Redis pub/sub
(the exact channel/payload format the SSE endpoint actually subscribes to) —
no mocks. Calls the actual `ConversationRepository.get_recent_messages` and
`update_message_delivery_status` functions.

Scenario A: before this fix, GET .../messages defaulted to offset=0 with no
cursor, so a conversation with more than `limit` messages silently hid its
NEWEST messages behind its oldest page. Proves the new default returns the
tail (most recent messages, oldest-first for rendering), and that the
`before` cursor correctly pages further into the past with no gaps or
duplicates against the exact same 105-message conversation.
Scenario B: before this fix, `update_message_delivery_status` never
published anything — a message's PENDING -> SENT transition was invisible
to an already-open inbox until a full reload. Proves a real subscriber on
the real `sse:{tenant_id}` Redis channel receives the real event.
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import redis.asyncio as aioredis

from src.shared.core.config import get_settings
from src.shared.core.enums import Channel
from src.shared.db.models import Conversation, Customer, Message, Tenant
from src.shared.db.persistence import update_message_delivery_status
from src.shared.db.repository import ConversationRepository
from src.shared.db.session import get_system_session, get_tenant_session
from src.shared.redis_client.client import redis_mgr

settings = get_settings()


def _require_local() -> None:
    if settings.postgres_host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("requires a local Postgres database")
    if settings.redis_host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("requires local Redis")


async def _setup(n_messages: int) -> dict:
    tenant_id, customer_id, conversation_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with get_system_session() as session:
        session.add(Tenant(
            tenant_id=tenant_id, business_name="Pagination Drill Realty",
            fal_license_number=f"PAG-{uuid.uuid4().hex[:10]}",
        ))
        session.add(Customer(
            customer_id=customer_id, tenant_id=tenant_id,
            unified_phone=f"+1555{uuid.uuid4().int % 10_000_000:07d}",
            display_name="Pagination Drill Customer",
        ))
        session.add(Conversation(
            conversation_id=conversation_id, tenant_id=tenant_id, customer_id=customer_id,
            channel=Channel.WHATSAPP, platform_conversation_id="pagination-drill",
        ))

    # Distinct, strictly increasing created_at values so ordering/cursor math
    # is unambiguous — real DB rows, not mocked timestamps.
    base = datetime.now(timezone.utc) - timedelta(hours=1)
    message_ids: list[uuid.UUID] = []
    async with get_tenant_session(tenant_id) as session:
        for i in range(n_messages):
            mid = uuid.uuid4()
            message_ids.append(mid)
            session.add(Message(
                message_id=mid,
                conversation_id=conversation_id,
                sender_type="customer",
                text_content=f"message #{i}",
                created_at=base + timedelta(seconds=i),
                delivery_status="PENDING",
            ))
    return {
        "tenant_id": tenant_id, "customer_id": customer_id,
        "conversation_id": conversation_id, "message_ids": message_ids,
    }


async def _cleanup(ctx: dict) -> None:
    from sqlalchemy import delete

    async with get_system_session() as session:
        await session.execute(delete(Message).where(Message.conversation_id == ctx["conversation_id"]))
        await session.execute(delete(Conversation).where(Conversation.conversation_id == ctx["conversation_id"]))
        await session.execute(delete(Customer).where(Customer.customer_id == ctx["customer_id"]))
        await session.execute(delete(Tenant).where(Tenant.tenant_id == ctx["tenant_id"]))
    print("cleanup: synthetic tenant/customer/conversation/messages deleted")


async def scenario_a_pagination(ctx: dict, n_messages: int, page_size: int) -> None:
    print("\n=== Scenario A: tail-first default + cursor-based 'load older' ===")
    async with get_tenant_session(ctx["tenant_id"]) as session:
        repo = ConversationRepository(session)

        # No cursor -> must be the newest page, not the oldest.
        page1 = await repo.get_recent_messages(ctx["conversation_id"], limit=page_size)
        assert len(page1) == page_size, f"expected {page_size} messages, got {len(page1)}"
        assert page1[-1].text_content == f"message #{n_messages - 1}", (
            f"expected the LAST message in the page to be the newest, got {page1[-1].text_content!r} — "
            "the old offset=0 default would have returned the OLDEST page instead"
        )
        assert page1[0].text_content == f"message #{n_messages - page_size}"
        print(f"PASS: default (no cursor) call returned the {page_size} newest messages, oldest-first")

        # Page backward using the oldest loaded message's created_at as the cursor.
        oldest_loaded = page1[0].created_at
        page2 = await repo.get_recent_messages(ctx["conversation_id"], limit=page_size, before=oldest_loaded)
        expected_count = n_messages - page_size
        assert len(page2) == expected_count, f"expected {expected_count} older messages, got {len(page2)}"
        assert page2[-1].text_content == f"message #{n_messages - page_size - 1}", (
            "the 'before' page should end exactly where the first page began, no overlap"
        )
        assert page2[0].text_content == "message #0"
        print(f"PASS: 'before' cursor returned the remaining {expected_count} older messages with no gap or overlap")

        combined_ids = {m.message_id for m in page2} | {m.message_id for m in page1}
        assert combined_ids == set(ctx["message_ids"]), "combined pages must cover every real message exactly once"
        print("PASS: the two pages together cover all real messages exactly once (no duplicates, no gaps)")


async def scenario_b_sse_delivery_status(ctx: dict) -> None:
    print("\n=== Scenario B: real delivery-status change published over real Redis SSE channel ===")
    channel = f"sse:{ctx['tenant_id']}"
    target_message_id = ctx["message_ids"][0]

    sub_client = aioredis.Redis(
        host=settings.redis_host, port=settings.redis_port,
        password=settings.redis_password, db=settings.redis_db_conversations,
    )
    pubsub = sub_client.pubsub()
    await pubsub.subscribe(channel)
    try:
        # Drain the subscribe-confirmation message before publishing.
        await pubsub.get_message(timeout=2.0)

        await update_message_delivery_status(
            tenant_id=ctx["tenant_id"], message_id=target_message_id, delivery_status="SENT",
        )

        received = None
        deadline = asyncio.get_event_loop().time() + 5.0
        while asyncio.get_event_loop().time() < deadline:
            msg = await pubsub.get_message(timeout=1.0)
            if msg and msg.get("type") == "message":
                received = msg
                break
        assert received is not None, f"no message received on real Redis channel {channel!r} within timeout"

        payload = json.loads(received["data"])
        assert payload["event"] == "message_status_update", f"unexpected event type: {payload['event']!r}"
        assert payload["data"]["id"] == str(target_message_id)
        assert payload["data"]["conversation_id"] == str(ctx["conversation_id"])
        assert payload["data"]["delivery_status"] == "SENT"
        print(f"PASS: real SSE event received on {channel!r} — {payload['event']} for the correct message/conversation")
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        await sub_client.aclose()

    async with get_tenant_session(ctx["tenant_id"]) as session:
        from sqlalchemy import select
        msg = await session.scalar(select(Message).where(Message.message_id == target_message_id))
        assert msg.delivery_status == "SENT"
    print("PASS: the DB row itself was also correctly updated (not just the SSE side-channel)")


async def main() -> None:
    _require_local()
    await redis_mgr.start()
    n_messages, page_size = 105, 100
    ctx = await _setup(n_messages)
    print(f"synthetic tenant={ctx['tenant_id']} conversation={ctx['conversation_id']} ({n_messages} real messages)")
    try:
        await scenario_a_pagination(ctx, n_messages, page_size)
        await scenario_b_sse_delivery_status(ctx)
        print("\nALL SCENARIOS PASSED")
    finally:
        await _cleanup(ctx)
        await redis_mgr.stop()


if __name__ == "__main__":
    asyncio.run(main())

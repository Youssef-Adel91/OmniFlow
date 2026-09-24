"""
Real end-to-end validation of item 13 (concurrency audit) fixes.

Real Postgres (actual asyncpg connections racing each other, not asyncio
tasks sharing one connection/session) and real Redis pub/sub — no mocks.
Exercises the actual `ConversationRepository.assign_agent`,
`ConversationRepository.add_message`, and the real
`gateway.routers.conversations.takeover_conversation` endpoint function.

Scenario A: before the fix, two agents clicking "take over" on the same
unassigned conversation within milliseconds both read assigned_agent_id=NULL
then both unconditionally wrote their own id — the loser got a false 200 OK.
Proves that with N genuinely concurrent `assign_agent` calls on separate
connections, exactly one succeeds and every other one raises
ConversationConflictError, and the DB is left owned by the winner.

Scenario B: before the fix, `message_count` was a Python read-modify-write
(`conv.message_count = (conv.message_count or 0) + 1`), so concurrent
`add_message` calls lost updates. Proves N genuinely concurrent
`add_message` calls on separate connections leave `message_count` exactly
equal to N (not less).

Scenario C: before the fix, `conversations.py` published the SSE event
inline in the endpoint body, before the FastAPI dependency-injected
session's surrounding transaction committed — a subscriber could see an
event for a row not yet visible to a fresh read. Proves the real
`takeover_conversation` endpoint's SSE publish (now deferred via
BackgroundTasks) never fires before the row is actually committed: a
subscriber sees nothing while the transaction is still open, and only
receives the event, with the row already durably committed, after the
background task actually runs (the equivalent of "after the response was
sent" in a real request).

NOT covered by this script (documented, not silently skipped): the
AI-reply-after-takeover TOCTOU narrowing in outbound_dispatcher/worker.py
requires a real Kafka + WhatsApp-transport-boundary harness to exercise
end-to-end; it was verified by code inspection and the existing unit tests
only, not by a dedicated real-dependency race script here.
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import redis.asyncio as aioredis
from fastapi import BackgroundTasks

from src.gateway.routers import conversations as conversations_router
from src.shared.core.config import get_settings
from src.shared.core.enums import Channel, ConversationStatus, TenantUserRole
from src.shared.db.models import Conversation, Customer, Tenant, TenantUser
from src.shared.db.repository import ConversationConflictError, ConversationRepository
from src.shared.db.session import get_system_session, get_tenant_session
from src.shared.redis_client.client import redis_mgr

settings = get_settings()


def _require_local() -> None:
    if settings.postgres_host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("requires a local Postgres database")
    if settings.redis_host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("requires local Redis")


async def _setup() -> dict:
    tenant_id, customer_id, conversation_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with get_system_session() as session:
        session.add(Tenant(
            tenant_id=tenant_id, business_name="Concurrency Drill Realty",
            fal_license_number=f"CNC-{uuid.uuid4().hex[:10]}",
        ))
        session.add(Customer(
            customer_id=customer_id, tenant_id=tenant_id,
            unified_phone=f"+1555{uuid.uuid4().int % 10_000_000:07d}",
            display_name="Concurrency Drill Customer",
        ))
        session.add(Conversation(
            conversation_id=conversation_id, tenant_id=tenant_id, customer_id=customer_id,
            channel=Channel.WHATSAPP, platform_conversation_id="concurrency-drill",
        ))
    return {"tenant_id": tenant_id, "customer_id": customer_id, "conversation_id": conversation_id}


async def _make_agents(tenant_id: uuid.UUID, n: int) -> list[uuid.UUID]:
    """Real TenantUser rows — assigned_agent_id has a real FK to tenant_users."""
    agent_ids = [uuid.uuid4() for _ in range(n)]
    async with get_system_session() as session:
        for i, agent_id in enumerate(agent_ids):
            session.add(TenantUser(
                user_id=agent_id, tenant_id=tenant_id,
                full_name=f"Drill Agent {i}", email=f"drill-agent-{agent_id.hex[:8]}@example.com",
                hashed_password="x", role=TenantUserRole.AGENT,
            ))
    return agent_ids


async def _cleanup(ctx: dict) -> None:
    from sqlalchemy import delete

    from src.shared.db.models import Message

    async with get_system_session() as session:
        await session.execute(delete(Message).where(Message.conversation_id == ctx["conversation_id"]))
        await session.execute(delete(Conversation).where(Conversation.conversation_id == ctx["conversation_id"]))
        await session.execute(delete(Customer).where(Customer.customer_id == ctx["customer_id"]))
        await session.execute(delete(TenantUser).where(TenantUser.tenant_id == ctx["tenant_id"]))
        await session.execute(delete(Tenant).where(Tenant.tenant_id == ctx["tenant_id"]))
    print("cleanup: synthetic tenant/customer/conversation/messages/agents deleted")


async def _reset_assignment(ctx: dict) -> None:
    from sqlalchemy import update

    async with get_tenant_session(ctx["tenant_id"]) as session:
        await session.execute(
            update(Conversation)
            .where(Conversation.conversation_id == ctx["conversation_id"])
            .values(assigned_agent_id=None, status=ConversationStatus.AI_ACTIVE)
        )


async def _attempt_takeover(tenant_id: uuid.UUID, conversation_id: uuid.UUID, agent_id: uuid.UUID) -> str:
    """Each attempt gets its OWN connection/session — a real race, not tasks sharing one."""
    try:
        async with get_tenant_session(tenant_id) as session:
            repo = ConversationRepository(session)
            await repo.assign_agent(conversation_id=conversation_id, agent_id=agent_id)
        return "won"
    except ConversationConflictError:
        return "conflict"


async def scenario_a_takeover_race(ctx: dict, agent_ids: list[uuid.UUID]) -> None:
    n_agents = len(agent_ids)
    print(f"\n=== Scenario A: {n_agents} concurrent takeover attempts on the same unassigned conversation ===")
    results = await asyncio.gather(*[
        _attempt_takeover(ctx["tenant_id"], ctx["conversation_id"], agent_id) for agent_id in agent_ids
    ])
    wins = results.count("won")
    conflicts = results.count("conflict")
    assert wins == 1, f"expected exactly 1 winner, got {wins} (results={results})"
    assert conflicts == n_agents - 1, f"expected {n_agents - 1} conflicts, got {conflicts}"
    print(f"PASS: exactly 1 of {n_agents} concurrent takeovers won, the other {conflicts} got a real ConversationConflictError")

    async with get_tenant_session(ctx["tenant_id"]) as session:
        conv = await session.get(Conversation, ctx["conversation_id"])
        assert conv.assigned_agent_id in agent_ids
        assert str(conv.status).lower() == ConversationStatus.HUMAN_ACTIVE
    print("PASS: the DB row is owned by exactly one of the agents, status is HUMAN_ACTIVE")


async def _attempt_add_message(tenant_id: uuid.UUID, conversation_id: uuid.UUID, i: int) -> None:
    async with get_tenant_session(tenant_id) as session:
        repo = ConversationRepository(session)
        await repo.add_message(
            conversation_id=conversation_id,
            sender_type="customer",
            text_content=f"race message #{i}",
        )


async def scenario_b_message_count_race(ctx: dict, n_messages: int) -> None:
    print(f"\n=== Scenario B: {n_messages} concurrent add_message calls on the same conversation ===")
    await asyncio.gather(*[
        _attempt_add_message(ctx["tenant_id"], ctx["conversation_id"], i) for i in range(n_messages)
    ])
    async with get_tenant_session(ctx["tenant_id"]) as session:
        from sqlalchemy import func, select

        from src.shared.db.models import Message

        conv = await session.get(Conversation, ctx["conversation_id"])
        actual_rows = await session.scalar(
            select(func.count()).select_from(Message).where(Message.conversation_id == ctx["conversation_id"])
        )
    assert actual_rows == n_messages, f"expected {n_messages} message rows, found {actual_rows}"
    assert conv.message_count == n_messages, (
        f"message_count={conv.message_count} but {actual_rows} rows actually exist — "
        "a lost update would show message_count < actual_rows"
    )
    print(f"PASS: message_count == {n_messages} == actual row count, no lost updates under real concurrency")


async def scenario_c_sse_after_commit(ctx: dict, agent_id: uuid.UUID) -> None:
    print("\n=== Scenario C: real takeover endpoint never publishes SSE before the transaction commits ===")
    await _reset_assignment(ctx)
    user = SimpleNamespace(role=TenantUserRole.AGENT, user_id=agent_id, tenant_id=ctx["tenant_id"])
    channel = f"sse:{ctx['tenant_id']}"

    sub_client = aioredis.Redis(
        host=settings.redis_host, port=settings.redis_port,
        password=settings.redis_password, db=settings.redis_db_conversations,
    )
    pubsub = sub_client.pubsub()
    await pubsub.subscribe(channel)
    try:
        await pubsub.get_message(timeout=2.0)  # drain subscribe confirmation

        background_tasks = BackgroundTasks()
        async with get_tenant_session(ctx["tenant_id"]) as session:
            repo = ConversationRepository(session)
            await conversations_router.takeover_conversation(
                ctx["conversation_id"], user, repo, background_tasks,
            )
            # Transaction is still OPEN here (commits only when this `async
            # with` block exits) — this is exactly the FastAPI dependency
            # lifecycle: the endpoint has "returned" its result but the
            # session dependency hasn't torn down/committed yet.
            no_event_yet = await pubsub.get_message(timeout=0.5)
            assert no_event_yet is None, (
                "SSE event was published before the transaction committed — "
                "the BackgroundTasks fix is not actually deferring it"
            )
            print("PASS: no SSE event on the real Redis channel while the transaction is still open")

        # `async with` block above has now exited -> real commit happened.
        # Simulate "after the response was sent": actually run the deferred task.
        await background_tasks()

        received = None
        deadline = asyncio.get_event_loop().time() + 5.0
        while asyncio.get_event_loop().time() < deadline:
            msg = await pubsub.get_message(timeout=1.0)
            if msg and msg.get("type") == "message":
                received = msg
                break
        assert received is not None, f"no SSE event received on {channel!r} after running the background task"
        payload = json.loads(received["data"])
        assert payload["event"] == "conversation_update"
        assert str(payload["data"]["status"]).lower() == ConversationStatus.HUMAN_ACTIVE
        print("PASS: SSE event received only after the background task ran, i.e. only after commit")

        async with get_tenant_session(ctx["tenant_id"]) as session:
            conv = await session.get(Conversation, ctx["conversation_id"])
            assert conv.assigned_agent_id == agent_id
        print("PASS: by the time the event fired, the row was already durably committed and visible on a fresh read")
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        await sub_client.aclose()


async def main() -> None:
    _require_local()
    await redis_mgr.start()
    ctx = await _setup()
    agent_ids = await _make_agents(ctx["tenant_id"], 8)
    print(f"synthetic tenant={ctx['tenant_id']} conversation={ctx['conversation_id']}")
    try:
        await scenario_a_takeover_race(ctx, agent_ids)
        await scenario_b_message_count_race(ctx, n_messages=20)
        await scenario_c_sse_after_commit(ctx, agent_ids[0])
        print("\nALL SCENARIOS PASSED")
    finally:
        await _cleanup(ctx)
        await redis_mgr.stop()


if __name__ == "__main__":
    asyncio.run(main())

"""
Real end-to-end validation of the LLM-failure safety net added to
LLMInvokerWorker.process_message() after a real live-test finding: on a
Gemini/Groq call failure (timeout or any exception), the worker used to
either silently escalate to human with zero customer-facing message, or
re-raise for Kafka's retry/DLQ machinery — also customer-silent. From the
customer's side that is indistinguishable from the bot being broken.

This exercises the REAL LLMInvokerWorker.process_message() (not a
reimplementation) against real Postgres (the load_conversation_state()
check) and a real Kafka producer/consumer pair (Redpanda) for the actual
OutboundMessage. Only the Gemini/Groq API call itself is monkeypatched to
always raise, so the script proves the fallback path fires, not that a
real provider is down.

Scenario A: generate_response always raises -> exactly one retry is made
(2 total attempts), then a real graceful Arabic fallback OutboundMessage
is published on messages.outgoing.v1, and the conversation is flipped to
ESCALATED in real Postgres.
Scenario B: generate_response fails once then succeeds -> the retry
absorbs it and the real LLM-generated text is published, not the
fallback (proves the retry doesn't over-fire on a transient blip).
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiokafka import AIOKafkaConsumer
from sqlalchemy import select

from src.ai_workers.llm_invoker import worker as invoker_module
from src.ai_workers.llm_invoker.worker import LLMInvokerWorker
from src.ai_workers.semantic_router.worker import RoutingDecision
from src.shared.core.config import get_settings
from src.shared.core.enums import Channel, ConversationStatus, MessageType
from src.shared.db.models import Conversation, Customer, Tenant
from src.shared.db.session import get_system_session
from src.shared.events.canonical import CanonicalInboundEvent
from src.shared.redis_client.client import redis_mgr

settings = get_settings()


def _require_local() -> None:
    if settings.postgres_host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("requires a local Postgres database")


def _record(value: bytes):
    return SimpleNamespace(value=value, key=b"test-key", topic="llm.routing.v1", partition=0, offset=1)


async def _setup() -> dict:
    tenant_id, customer_id, conversation_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with get_system_session() as session:
        session.add(Tenant(
            tenant_id=tenant_id, business_name="Fallback Drill Co",
            fal_license_number=f"FBD-{uuid.uuid4().hex[:10]}",
        ))
        session.add(Customer(
            customer_id=customer_id, tenant_id=tenant_id,
            unified_phone=f"+1555{uuid.uuid4().int % 10_000_000:07d}",
            display_name="Fallback Drill Customer",
        ))
        session.add(Conversation(
            conversation_id=conversation_id, tenant_id=tenant_id, customer_id=customer_id,
            channel=Channel.WHATSAPP, platform_conversation_id="fallback-drill",
            status=ConversationStatus.AI_ACTIVE,
        ))
    return {"tenant_id": tenant_id, "customer_id": customer_id, "conversation_id": conversation_id}


async def _cleanup(ctx: dict) -> None:
    from sqlalchemy import delete

    async with get_system_session() as session:
        await session.execute(delete(Conversation).where(Conversation.conversation_id == ctx["conversation_id"]))
        await session.execute(delete(Customer).where(Customer.customer_id == ctx["customer_id"]))
        await session.execute(delete(Tenant).where(Tenant.tenant_id == ctx["tenant_id"]))
    print("cleanup: synthetic tenant/customer/conversation deleted")


def _decision(ctx: dict, *, platform_message_id: str) -> RoutingDecision:
    event = CanonicalInboundEvent(
        tenant_id=ctx["tenant_id"], channel=Channel.WHATSAPP,
        platform_message_id=platform_message_id,
        platform_conversation_id="fallback-drill",
        platform_user_id="966500000000", customer_phone="966500000000",
        message_type=MessageType.TEXT, text_content="هل عندكم توصيل؟",
    )
    return RoutingDecision(
        event=event, tenant_id=ctx["tenant_id"], conversation_id=ctx["conversation_id"],
        target_tier="L1", route_reason="triage", skip_llm=False,
    )


async def _consume_one_outbound(group_suffix: str, timeout_s: float = 10.0) -> dict:
    consumer = AIOKafkaConsumer(
        settings.kafka_topic_messages_outgoing,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=f"fallback-drill-{group_suffix}-{uuid.uuid4().hex[:8]}",
        auto_offset_reset="latest",
        enable_auto_commit=True,
    )
    await consumer.start()
    try:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            batch = await consumer.getmany(timeout_ms=500, max_records=10)
            for _, records in batch.items():
                for rec in records:
                    payload = json.loads(rec.value.decode("utf-8"))
                    return payload
        raise AssertionError(f"no message observed on {settings.kafka_topic_messages_outgoing!r} within {timeout_s}s")
    finally:
        await consumer.stop()


async def scenario_a_always_fails(ctx: dict, worker: LLMInvokerWorker) -> None:
    print("\n=== Scenario A: LLM call fails every attempt ===")
    call_count = {"n": 0}

    async def _always_fail(*args, **kwargs):
        call_count["n"] += 1
        raise RuntimeError("simulated provider outage")

    original = invoker_module.gemini_client.generate_response
    invoker_module.gemini_client.generate_response = _always_fail
    try:
        consume_task = asyncio.create_task(_consume_one_outbound("always-fail"))
        await asyncio.sleep(1.0)
        decision = _decision(ctx, platform_message_id=f"wamid.{uuid.uuid4().hex[:10]}")
        await worker.process_message(_record(decision.to_kafka_bytes()))
        payload = await consume_task

        assert call_count["n"] == 2, f"expected exactly 2 attempts (1 retry), got {call_count['n']}"
        assert payload["message_type"] == "text"
        assert "عذرًا" in payload["text"], f"expected the graceful fallback text, got {payload['text']!r}"
        print(f"PASS: {call_count['n']} real attempts made, real fallback OutboundMessage published")

        async with get_system_session() as session:
            conv = (await session.execute(
                select(Conversation).where(Conversation.conversation_id == ctx["conversation_id"])
            )).scalar_one()
            assert conv.status == ConversationStatus.ESCALATED, (
                f"expected conversation escalated to a human, got {conv.status}"
            )
        print("PASS: real DB row confirms conversation.status == ESCALATED after the fallback")
    finally:
        invoker_module.gemini_client.generate_response = original


async def scenario_b_transient_then_succeeds(ctx: dict, worker: LLMInvokerWorker) -> None:
    print("\n=== Scenario B: LLM call fails once, then succeeds ===")
    call_count = {"n": 0}
    real_generate = invoker_module.gemini_client.generate_response

    async def _fail_once_then_succeed(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated transient blip")
        return SimpleNamespace(
            text="نعم، عندنا توصيل لكل المناطق.", was_blocked=False,
            model_used="test-model", output_tokens=8, input_tokens=10,
            was_truncated=False,
        )

    invoker_module.gemini_client.generate_response = _fail_once_then_succeed
    try:
        consume_task = asyncio.create_task(_consume_one_outbound("transient"))
        await asyncio.sleep(1.0)
        decision = _decision(ctx, platform_message_id=f"wamid.{uuid.uuid4().hex[:10]}")
        await worker.process_message(_record(decision.to_kafka_bytes()))
        payload = await consume_task

        assert call_count["n"] == 2, f"expected exactly 2 attempts, got {call_count['n']}"
        assert payload["text"] == "نعم، عندنا توصيل لكل المناطق.", (
            f"expected the real (second-attempt) LLM text, got {payload['text']!r}"
        )
        assert "عذرًا" not in payload["text"], "must not send the fallback when the retry succeeded"
        print("PASS: single transient failure absorbed by the retry; real LLM text delivered, not the fallback")
    finally:
        invoker_module.gemini_client.generate_response = real_generate


async def main() -> None:
    _require_local()
    ctx = await _setup()
    print(f"synthetic tenant={ctx['tenant_id']} conversation={ctx['conversation_id']}")
    worker = LLMInvokerWorker()
    await worker._outbound_producer.start()
    await redis_mgr.start()
    try:
        await scenario_a_always_fails(ctx, worker)
        # Reset conversation back to AI_ACTIVE for scenario B (A escalated it)
        async with get_system_session() as session:
            conv = (await session.execute(
                select(Conversation).where(Conversation.conversation_id == ctx["conversation_id"])
            )).scalar_one()
            conv.status = ConversationStatus.AI_ACTIVE
        await scenario_b_transient_then_succeeds(ctx, worker)
        print("\nALL SCENARIOS PASSED")
    finally:
        await worker._outbound_producer.stop()
        await redis_mgr.stop()
        await _cleanup(ctx)


if __name__ == "__main__":
    asyncio.run(main())

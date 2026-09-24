"""
Real end-to-end validation of item 14 (voice/image v1 decision).

Before this change, a customer's WhatsApp voice note or image (with no
caption) fell through to LLMInvokerWorker._build_current_message()'s
placeholder branch — the LLM received a string like
"[audio message — media: waid:123]" as "the customer's message" and
generated a confident-sounding reply to content it never actually saw.
No Whisper/vision worker exists anywhere in this codebase (see
ai_workers/multimodal/__init__.py, a docstring-only stub) — this was a
silent gap, not a documented v1 limitation.

This script exercises the REAL LLMInvokerWorker.process_message() (not a
reimplementation of the guard) against real Postgres (for the
load_conversation_state() check the worker performs) and a real Kafka
producer/consumer pair (Redpanda) for the actual OutboundMessage — the
only thing NOT real is the Gemini API call itself, which is made
impossible to reach on purpose: gemini_client.generate_response is
monkeypatched to raise, so if the new guard in worker.py:234-259 were ever
removed or bypassed, this script would fail loudly instead of silently
passing.

Scenario A: audio message, no caption, feature_multimodal_voice=False
(the shipped v1 default) -> the real worker must publish the canned
Arabic fallback as a real "text" OutboundMessage on the real
messages.outgoing.v1 topic, and must never call Gemini.
Scenario B: same for an image message and feature_multimodal_vision.
Scenario C: an audio message that DOES have a caption (text_content set)
must NOT be treated as unsupported - it's a normal text turn and takes
the ordinary LLM path.
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

from src.ai_workers.llm_invoker import worker as invoker_module
from src.ai_workers.llm_invoker.worker import LLMInvokerWorker
from src.ai_workers.semantic_router.worker import RoutingDecision
from src.shared.core.config import get_settings
from src.shared.core.enums import Channel, MessageType
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
            tenant_id=tenant_id, business_name="Multimodal Drill Realty",
            fal_license_number=f"MMD-{uuid.uuid4().hex[:10]}",
        ))
        session.add(Customer(
            customer_id=customer_id, tenant_id=tenant_id,
            unified_phone=f"+1555{uuid.uuid4().int % 10_000_000:07d}",
            display_name="Multimodal Drill Customer",
        ))
        session.add(Conversation(
            conversation_id=conversation_id, tenant_id=tenant_id, customer_id=customer_id,
            channel=Channel.WHATSAPP, platform_conversation_id="multimodal-drill",
        ))
    return {"tenant_id": tenant_id, "customer_id": customer_id, "conversation_id": conversation_id}


async def _cleanup(ctx: dict) -> None:
    from sqlalchemy import delete

    async with get_system_session() as session:
        await session.execute(delete(Conversation).where(Conversation.conversation_id == ctx["conversation_id"]))
        await session.execute(delete(Customer).where(Customer.customer_id == ctx["customer_id"]))
        await session.execute(delete(Tenant).where(Tenant.tenant_id == ctx["tenant_id"]))
    print("cleanup: synthetic tenant/customer/conversation deleted")


def _decision(ctx: dict, *, message_type: MessageType, text_content: str | None, media_url: str | None) -> RoutingDecision:
    event = CanonicalInboundEvent(
        tenant_id=ctx["tenant_id"], channel=Channel.WHATSAPP,
        platform_message_id=f"wamid.{uuid.uuid4().hex[:10]}",
        platform_conversation_id="multimodal-drill",
        platform_user_id="966500000000", customer_phone="966500000000",
        message_type=message_type, text_content=text_content, media_url=media_url,
    )
    return RoutingDecision(
        event=event, tenant_id=ctx["tenant_id"], conversation_id=ctx["conversation_id"],
        target_tier="L1", route_reason="triage", skip_llm=False,
    )


async def _consume_one_outbound(group_suffix: str, timeout_s: float = 10.0) -> dict:
    consumer = AIOKafkaConsumer(
        settings.kafka_topic_messages_outgoing,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=f"multimodal-drill-{group_suffix}-{uuid.uuid4().hex[:8]}",
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


async def _run_scenario(ctx: dict, worker: LLMInvokerWorker, *, message_type: MessageType,
                         text_content: str | None, media_url: str | None, group_suffix: str) -> dict:
    consume_task = asyncio.create_task(_consume_one_outbound(group_suffix))
    await asyncio.sleep(1.0)  # let the consumer group actually join before we publish
    decision = _decision(ctx, message_type=message_type, text_content=text_content, media_url=media_url)
    await worker.process_message(_record(decision.to_kafka_bytes()))
    return await consume_task


async def scenario_a_audio_no_caption(ctx: dict, worker: LLMInvokerWorker) -> None:
    print("\n=== Scenario A: audio message, no caption, feature_multimodal_voice=False ===")
    assert settings.feature_multimodal_voice is False, "expected the shipped v1 default (disabled)"
    payload = await _run_scenario(
        ctx, worker, message_type=MessageType.AUDIO, text_content=None,
        media_url="waid:audio-drill-1", group_suffix="audio",
    )
    assert payload["message_type"] == "text", f"expected a plain text fallback, got {payload['message_type']!r}"
    assert payload["model_used"] == "L0_deterministic", (
        f"expected NO LLM to have been invoked, model_used={payload['model_used']!r}"
    )
    assert "عذرًا" in payload["text"], f"expected the canned Arabic fallback, got {payload['text']!r}"
    print(f"PASS: real OutboundMessage on {settings.kafka_topic_messages_outgoing!r} is the fallback text, not an LLM hallucination")


async def scenario_b_image_no_caption(ctx: dict, worker: LLMInvokerWorker) -> None:
    print("\n=== Scenario B: image message, no caption, feature_multimodal_vision=False ===")
    assert settings.feature_multimodal_vision is False, "expected the shipped v1 default (disabled)"
    payload = await _run_scenario(
        ctx, worker, message_type=MessageType.IMAGE, text_content=None,
        media_url="waid:image-drill-1", group_suffix="image",
    )
    assert payload["message_type"] == "text"
    assert payload["model_used"] == "L0_deterministic"
    assert "عذرًا" in payload["text"]
    print("PASS: real OutboundMessage for an uncaptioned image is also the fallback, not a blind LLM answer")


async def scenario_c_audio_with_caption_uses_llm_path(ctx: dict, worker: LLMInvokerWorker) -> None:
    print("\n=== Scenario C: audio message WITH a caption is not treated as unsupported ===")
    called = {"hit": False}

    async def _tripwire(*args, **kwargs):
        called["hit"] = True
        raise RuntimeError("expected: real LLM call attempted (no real API key in this drill)")

    original = invoker_module.gemini_client.generate_response
    invoker_module.gemini_client.generate_response = _tripwire
    try:
        decision = _decision(
            ctx, message_type=MessageType.AUDIO, text_content="عندي استفسار عن شقة",
            media_url="waid:audio-drill-2",
        )
        try:
            await worker.process_message(_record(decision.to_kafka_bytes()))
        except Exception as exc:
            if not called["hit"]:
                raise AssertionError(f"failed before reaching the LLM call: {exc!r}") from exc
        assert called["hit"], "a message WITH a real caption must still reach the normal LLM path, not the fallback"
        print("PASS: a captioned audio message correctly falls through to the real LLM path (guard is caption-aware)")
    finally:
        invoker_module.gemini_client.generate_response = original


async def main() -> None:
    _require_local()
    ctx = await _setup()
    print(f"synthetic tenant={ctx['tenant_id']} conversation={ctx['conversation_id']}")
    worker = LLMInvokerWorker()
    await worker._outbound_producer.start()
    await redis_mgr.start()
    try:
        await scenario_a_audio_no_caption(ctx, worker)
        await scenario_b_image_no_caption(ctx, worker)
        await scenario_c_audio_with_caption_uses_llm_path(ctx, worker)
        print("\nALL SCENARIOS PASSED")
    finally:
        await worker._outbound_producer.stop()
        await redis_mgr.stop()
        await _cleanup(ctx)


if __name__ == "__main__":
    asyncio.run(main())

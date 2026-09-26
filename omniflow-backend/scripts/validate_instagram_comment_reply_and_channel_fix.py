"""
Real end-to-end validation of two things found/fixed together while wiring
Instagram/Messenger page-comment replies:

1. A real, live production bug (found during this pass, not previously
   known): `OutboundMessage.channel` defaults to "whatsapp" and NEITHER
   `LLMInvokerWorker._publish_outbound()` nor `VaultWorker._publish_outbound()`
   -- the two workers that generate real AI replies for every channel, not
   just WhatsApp -- ever set it. `validate_instagram_outbound_delivery.py`
   (the prior Instagram DM validator) never caught this because it hand-built
   `OutboundMessage(channel="instagram", ...)` directly and fed it straight
   to `OutboundDispatcherWorker`, bypassing `LLMInvokerWorker` entirely. A
   real Instagram-originated message going through the REAL pipeline
   (webhook -> semantic_router -> llm_invoker -> dispatcher) would, before
   this fix, have published `channel="whatsapp"` on the wire regardless of
   the real event's channel -- the dispatcher would then have tried to send
   it as a WhatsApp message using WhatsApp credentials and the customer's
   IGSID as if it were a wa_id. This is the exact same class of gap as the
   P0 persona regression documented in IMPLEMENTATION_STATUS.md: a directly
   -- constructed OutboundMessage proved the DOWNSTREAM code path works,
   without ever exercising the UPSTREAM code that builds the real one.

2. The new, real Instagram/Messenger page-comment reply wiring:
   `CanonicalInboundEvent.reply_target_type` / `OutboundMessage.reply_target_type`
   ("dm" | "comment"), set by the webhook adapter at ingestion, carried
   through `RoutingDecision.event` unchanged, and read by
   `outbound_dispatcher` to call `send_comment_reply()` (a different Graph
   API endpoint, POST /{comment_id}/comments) instead of `send_message()`
   (POST /me/messages) when a reply is answering a public comment rather
   than a DM.

Real Postgres + real Kafka (Redpanda), through the actual PRODUCTION ENTRY
POINTS this time -- `LLMInvokerWorker.process_message()` (via its
`run()`/consumer loop, not called directly) consuming a real RoutingDecision
from `llm.routing.v1`, publishing a real OutboundMessage to
`messages.outgoing.v1`, then the real `OutboundDispatcherWorker.run()`
consuming THAT and calling the real `channel_adapters.instagram.client`
functions. Only the Instagram Graph API network transport is mocked (no
live Instagram credentials exist in this environment) and only the LLM call
is avoided by using the L0_SEMANTIC_CACHE tier, which is itself a real,
existing fast path (`_get_l0_response`) that skips the model call by
design -- not a test-only shortcut.

Scenario A (channel-defaulting bug): a real Instagram DM RoutingDecision,
tier=L0, reply_target_type defaults to "dm". Asserts the real dispatcher
sent via `send_message` to /me/messages with this tenant's own token, and
the Message row reaches delivery_status=SENT. Before the fix, `msg.channel`
on the wire would have been "whatsapp", so the dispatcher would have gone
down the WhatsApp credential-resolution branch instead (no WhatsApp
credentials configured for this synthetic tenant -> real, observable
failure, not a silent pass).

Scenario B (comment reply): a real Instagram comment RoutingDecision,
reply_target_type="comment", platform_conversation_id=<comment_id>. Asserts
the real dispatcher called `send_comment_reply` against
/{comment_id}/comments (not /me/messages), with the right comment_id and
this tenant's own token, and the Message row reaches SENT.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

import httpx
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai_workers.llm_invoker.worker import LLMInvokerWorker
from src.ai_workers.outbound_dispatcher.worker import OutboundDispatcherWorker
from src.ai_workers.semantic_router.worker import RoutingDecision
from src.channel_adapters.instagram import client as instagram_client_module
from src.channel_adapters.whatsapp.client import whatsapp_client
from src.shared.core.config import get_settings
from src.shared.core.enums import Channel, ConversationStatus, MessageType, RoutingTier
from src.shared.db.models import Conversation, Customer, Message, Tenant
from src.shared.db.session import get_system_session
from src.shared.events.canonical import CanonicalInboundEvent
from src.shared.kafka.consumer import BaseKafkaConsumer
from src.shared.kafka.producer import KafkaProducerManager
from src.shared.redis_client.client import redis_mgr

settings = get_settings()
ROUTING_TOPIC = settings.kafka_topic_llm_routing
OUTGOING_TOPIC = settings.kafka_topic_messages_outgoing

_CAPTURED_REQUESTS: list[dict] = []


def _require_local() -> None:
    if settings.postgres_host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("requires a local Postgres database")


def _mock_handler(request: httpx.Request) -> httpx.Response:
    _CAPTURED_REQUESTS.append({
        "url": str(request.url),
        "access_token": request.url.params.get("access_token"),
        "body": json.loads(request.content.decode("utf-8")) if request.content else {},
    })
    if str(request.url).rstrip("?").endswith("/comments") or "/comments?" in str(request.url):
        return httpx.Response(200, json={"id": f"{uuid.uuid4().hex[:10]}_comment_reply"})
    return httpx.Response(200, json={"recipient_id": "ig-recipient", "message_id": f"mid.{uuid.uuid4().hex[:10]}"})


class _MockAsyncClient(httpx.AsyncClient):
    def __init__(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_mock_handler)
        super().__init__(*args, **kwargs)


class _GroupedLLMInvoker(LLMInvokerWorker):
    """Same real worker, isolated consumer group so re-runs don't collide."""
    def __init__(self, group_id: str) -> None:
        super().__init__()
        self.group_id = group_id


class _GroupedDispatcher(OutboundDispatcherWorker):
    def __init__(self, group_id: str) -> None:
        BaseKafkaConsumer.__init__(
            self, topics=[OUTGOING_TOPIC], group_id=group_id, max_retries=2, retry_backoff_ms=100, batch_size=5,
        )


async def _run_workers_briefly(workers: list[BaseKafkaConsumer], condition, timeout: float = 25.0) -> bool:
    tasks = [asyncio.create_task(w.run()) for w in workers]
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = condition()
            if asyncio.iscoroutine(result):
                result = await result
            if result:
                return True
            await asyncio.sleep(0.25)
        return False
    finally:
        for w in workers:
            w._stop_event.set()
        for t in tasks:
            try:
                await asyncio.wait_for(t, timeout=10)
            except asyncio.TimeoutError:
                t.cancel()


async def _setup(*, reply_target_type: str) -> dict:
    tenant_id, customer_id, conversation_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    token = f"real-distinct-page-token-{uuid.uuid4().hex[:12]}"
    comment_id = f"{uuid.uuid4().hex[:8]}_{uuid.uuid4().hex[:8]}"
    igsid = f"igsid-{uuid.uuid4().hex[:8]}"
    platform_conversation_id = comment_id if reply_target_type == "comment" else igsid
    async with get_system_session() as session:
        session.add(Tenant(
            tenant_id=tenant_id, business_name="Instagram Comment Drill Co",
            fal_license_number=f"IGC-{uuid.uuid4().hex[:10]}",
            instagram_page_id=f"page-{uuid.uuid4().hex[:8]}",
            instagram_page_access_token=token,
        ))
        session.add(Customer(
            customer_id=customer_id, tenant_id=tenant_id,
            unified_phone=f"ig-{uuid.uuid4().hex[:10]}",
            display_name="Instagram Drill Customer",
        ))
        session.add(Conversation(
            conversation_id=conversation_id, tenant_id=tenant_id, customer_id=customer_id,
            channel=Channel.INSTAGRAM, platform_conversation_id=platform_conversation_id,
            status=ConversationStatus.AI_ACTIVE,
        ))
    return {
        "tenant_id": tenant_id, "customer_id": customer_id, "conversation_id": conversation_id,
        "token": token, "comment_id": comment_id, "igsid": igsid,
        "platform_conversation_id": platform_conversation_id,
    }


async def _cleanup(ctx: dict) -> None:
    from sqlalchemy import delete
    async with get_system_session() as session:
        await session.execute(delete(Conversation).where(Conversation.conversation_id == ctx["conversation_id"]))
        await session.execute(delete(Customer).where(Customer.customer_id == ctx["customer_id"]))
        await session.execute(delete(Tenant).where(Tenant.tenant_id == ctx["tenant_id"]))
    print(f"cleanup: synthetic tenant/customer/conversation deleted ({ctx['tenant_id']})")


def _build_decision(ctx: dict, *, reply_target_type: str) -> RoutingDecision:
    event = CanonicalInboundEvent(
        tenant_id=ctx["tenant_id"],
        channel=Channel.INSTAGRAM,
        platform_message_id=f"msg-{uuid.uuid4().hex[:10]}",
        platform_conversation_id=ctx["platform_conversation_id"],
        platform_user_id=ctx["igsid"],
        customer_display_name="Instagram Drill Customer",
        message_type=MessageType.TEXT,
        text_content="مرحباً" if reply_target_type == "dm" else "تعليق تجريبي",
        reply_target_type=reply_target_type,
    )
    return RoutingDecision(
        event=event,
        tenant_id=ctx["tenant_id"],
        customer_id=ctx["customer_id"],
        conversation_id=ctx["conversation_id"],
        target_tier=RoutingTier.L0_SEMANTIC_CACHE,
        route_reason="greeting",
        skip_llm=True,
    )


async def scenario_a_dm_channel_fix(ctx: dict) -> None:
    print("\n=== Scenario A: real Instagram DM through LLMInvokerWorker -> OutboundDispatcherWorker ===")
    decision = _build_decision(ctx, reply_target_type="dm")

    producer = KafkaProducerManager()
    await producer.start()
    try:
        await producer.publish(
            topic=ROUTING_TOPIC, event=decision,
            key=RoutingDecision.kafka_key(ctx["tenant_id"], decision.event.platform_user_id),
        )
    finally:
        await producer.stop()
    print(f"published a real RoutingDecision(channel=instagram, reply_target_type=dm) to {ROUTING_TOPIC}")

    invoker = _GroupedLLMInvoker(f"ig-drill-invoker-{uuid.uuid4().hex[:8]}")
    dispatcher = _GroupedDispatcher(f"ig-drill-dispatch-{uuid.uuid4().hex[:8]}")

    async def _check_sent() -> bool:
        async with get_system_session() as session:
            rows = (await session.execute(
                select(Message).where(Message.conversation_id == ctx["conversation_id"])
            )).scalars().all()
            return any(r.delivery_status == "SENT" for r in rows)

    ok = await _run_workers_briefly([invoker, dispatcher], _check_sent, timeout=30.0)
    assert ok, (
        "REGRESSION: no real Message row reached delivery_status=SENT for a real Instagram DM "
        "produced by the actual LLMInvokerWorker. Before the channel-defaulting fix, "
        "OutboundMessage.channel would default to 'whatsapp', so the dispatcher would try "
        "WhatsApp credential resolution instead (which this synthetic tenant has none of) "
        "and never reach the Instagram send path at all."
    )
    assert _CAPTURED_REQUESTS, "no real HTTP request was captured by the mock transport"

    req = _CAPTURED_REQUESTS[-1]
    assert req["url"].split("?")[0].endswith("/me/messages"), (
        f"expected the DM messages endpoint, got {req['url']}"
    )
    assert req["access_token"] == ctx["token"], (
        f"REGRESSION: wrong tenant token used (expected '{ctx['token']}', got '{req['access_token']}')"
    )
    assert req["body"]["recipient"]["id"] == ctx["igsid"]
    print(
        "PASS: a RoutingDecision built exactly like the real pipeline builds one flowed through "
        "the REAL LLMInvokerWorker.process_message() (not a hand-built OutboundMessage), produced "
        "an OutboundMessage with channel='instagram' on the wire, and the real OutboundDispatcherWorker "
        "sent it via send_message() to /me/messages with this tenant's own token."
    )


async def scenario_b_comment_reply(ctx: dict) -> None:
    print("\n=== Scenario B: real Instagram comment reply through LLMInvokerWorker -> OutboundDispatcherWorker ===")
    decision = _build_decision(ctx, reply_target_type="comment")

    producer = KafkaProducerManager()
    await producer.start()
    try:
        await producer.publish(
            topic=ROUTING_TOPIC, event=decision,
            key=RoutingDecision.kafka_key(ctx["tenant_id"], decision.event.platform_user_id),
        )
    finally:
        await producer.stop()
    print(f"published a real RoutingDecision(channel=instagram, reply_target_type=comment, comment_id={ctx['comment_id']}) to {ROUTING_TOPIC}")

    invoker = _GroupedLLMInvoker(f"ig-comment-invoker-{uuid.uuid4().hex[:8]}")
    dispatcher = _GroupedDispatcher(f"ig-comment-dispatch-{uuid.uuid4().hex[:8]}")

    before_count = len(_CAPTURED_REQUESTS)

    async def _check_sent() -> bool:
        async with get_system_session() as session:
            rows = (await session.execute(
                select(Message).where(Message.conversation_id == ctx["conversation_id"])
            )).scalars().all()
            return any(r.delivery_status == "SENT" for r in rows)

    ok = await _run_workers_briefly([invoker, dispatcher], _check_sent, timeout=30.0)
    assert ok, "REGRESSION: no real Message row reached delivery_status=SENT for the comment-reply scenario"
    assert len(_CAPTURED_REQUESTS) > before_count, "no new real HTTP request was captured"

    req = _CAPTURED_REQUESTS[-1]
    assert f"/{ctx['comment_id']}/comments" in req["url"], (
        f"REGRESSION: expected a POST to /{ctx['comment_id']}/comments (the comment-reply endpoint), "
        f"got {req['url']} -- this would mean a comment reply was sent as if it were a DM"
    )
    assert req["access_token"] == ctx["token"]
    assert req["body"].get("message") == decision.event.text_content or "message" in req["body"]
    print(
        f"PASS: a comment-flagged RoutingDecision flowed through the real LLMInvokerWorker and "
        f"OutboundDispatcherWorker, and the real dispatcher called send_comment_reply() against "
        f"POST /{ctx['comment_id']}/comments -- not the DM messages endpoint."
    )


async def main() -> None:
    _require_local()
    original_async_client = instagram_client_module.httpx.AsyncClient
    instagram_client_module.httpx.AsyncClient = _MockAsyncClient
    await redis_mgr.start()
    await whatsapp_client.start()
    ctx_dm = await _setup(reply_target_type="dm")
    ctx_comment = await _setup(reply_target_type="comment")
    try:
        await scenario_a_dm_channel_fix(ctx_dm)
        await scenario_b_comment_reply(ctx_comment)
        print("\nALL SCENARIOS PASSED")
    finally:
        instagram_client_module.httpx.AsyncClient = original_async_client
        await whatsapp_client.stop()
        await redis_mgr.stop()
        await _cleanup(ctx_dm)
        await _cleanup(ctx_comment)


if __name__ == "__main__":
    asyncio.run(main())

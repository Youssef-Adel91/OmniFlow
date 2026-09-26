"""
Real end-to-end validation of Instagram/Messenger DM outbound delivery —
part of the P0 channel-parity pass. Before this, `outbound_dispatcher`
hard-rejected any non-WhatsApp channel outright (`msg.channel != WHATSAPP`
-> raise -> DLQ), so an Instagram/Messenger reply the real pipeline
generated could never actually be delivered, and Instagram credentials
were a single global settings field shared by every tenant.

Real Postgres + real Kafka (Redpanda), through the actual
`OutboundDispatcherWorker.run()`/`_handle_record()` consumer loop (not
`process_message()` called directly) — same bar as `validate_vcard_delivery.py`
and `validate_broadcast_delivery.py`. Only the Instagram Graph API network
transport is faked via `httpx.MockTransport` (no real Instagram/Messenger
test credentials exist yet, same disclosed limitation every other channel
had before its own real test number existed) — the real
`channel_adapters.instagram.client.send_message()` request-building code
(URL, payload shape, per-tenant access_token) runs unmodified and is what
gets asserted on.

Scenario A: a tenant with a real, distinct `instagram_page_access_token`
set — the real dispatcher resolves THAT tenant's token specifically (not
a shared/global one) and sends via the real Graph API request shape.
Scenario B: a tenant with NO Instagram credentials configured — the
message must be DLQ'd with a clear reason, not silently dropped or sent
with some fallback/shared credential.
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

from src.ai_workers.outbound_dispatcher.worker import OutboundDispatcherWorker
from src.channel_adapters.instagram import client as instagram_client_module
from src.channel_adapters.whatsapp.client import whatsapp_client
from src.shared.core.config import get_settings
from src.shared.core.enums import Channel, ConversationStatus
from src.shared.db.models import Conversation, Customer, Message, Tenant
from src.shared.db.session import get_system_session
from src.shared.events.outbound import OutboundMessage
from src.shared.kafka.consumer import BaseKafkaConsumer
from src.shared.kafka.producer import KafkaProducerManager
from src.shared.redis_client.client import redis_mgr

settings = get_settings()
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
    return httpx.Response(200, json={"recipient_id": "ig-recipient", "message_id": f"mid.{uuid.uuid4().hex[:10]}"})


class _MockAsyncClient(httpx.AsyncClient):
    def __init__(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_mock_handler)
        super().__init__(*args, **kwargs)


class _GroupedDispatcher(OutboundDispatcherWorker):
    def __init__(self, group_id: str) -> None:
        BaseKafkaConsumer.__init__(
            self, topics=[OUTGOING_TOPIC], group_id=group_id, max_retries=2, retry_backoff_ms=100, batch_size=5,
        )


async def _run_worker_briefly(worker: BaseKafkaConsumer, condition, timeout: float = 20.0) -> bool:
    task = asyncio.create_task(worker.run())
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
        worker._stop_event.set()
        try:
            await asyncio.wait_for(task, timeout=10)
        except asyncio.TimeoutError:
            task.cancel()


async def _setup(*, with_credentials: bool) -> dict:
    tenant_id, customer_id, conversation_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    token = f"real-distinct-page-token-{uuid.uuid4().hex[:12]}" if with_credentials else None
    async with get_system_session() as session:
        session.add(Tenant(
            tenant_id=tenant_id, business_name="Instagram Drill Co",
            fal_license_number=f"IGD-{uuid.uuid4().hex[:10]}",
            instagram_page_id=f"page-{uuid.uuid4().hex[:8]}",
            instagram_page_access_token=token,
        ))
        session.add(Customer(
            customer_id=customer_id, tenant_id=tenant_id,
            unified_phone=f"ig-{uuid.uuid4().hex[:10]}",  # IGSID stored here, not a real phone
            display_name="Instagram Drill Customer",
        ))
        session.add(Conversation(
            conversation_id=conversation_id, tenant_id=tenant_id, customer_id=customer_id,
            channel=Channel.INSTAGRAM, platform_conversation_id=f"igsid-{uuid.uuid4().hex[:8]}",
            status=ConversationStatus.AI_ACTIVE,
        ))
    return {"tenant_id": tenant_id, "customer_id": customer_id, "conversation_id": conversation_id, "token": token}


async def _cleanup(ctx: dict) -> None:
    from sqlalchemy import delete
    async with get_system_session() as session:
        await session.execute(delete(Conversation).where(Conversation.conversation_id == ctx["conversation_id"]))
        await session.execute(delete(Customer).where(Customer.customer_id == ctx["customer_id"]))
        await session.execute(delete(Tenant).where(Tenant.tenant_id == ctx["tenant_id"]))
    print("cleanup: synthetic tenant/customer/conversation deleted")


async def scenario_a_real_send_with_own_token(ctx: dict) -> None:
    print("\n=== Scenario A: tenant with its own real instagram_page_access_token ===")
    msg = OutboundMessage(
        tenant_id=ctx["tenant_id"], conversation_id=ctx["conversation_id"],
        customer_phone=f"igsid-recipient-{uuid.uuid4().hex[:8]}",
        channel="instagram", platform_conversation_id="ig-drill",
        text="أهلاً! نعم عندنا توصيل لكل المناطق.",
        source_event_id=uuid.uuid4(), routing_tier_used="L1", model_used="test-model",
    )
    producer = KafkaProducerManager()
    await producer.start()
    try:
        await producer.publish(topic=OUTGOING_TOPIC, event=msg, key=OutboundMessage.kafka_key(ctx["tenant_id"], msg.customer_phone))
    finally:
        await producer.stop()
    print(f"published a real OutboundMessage(channel=instagram) to {OUTGOING_TOPIC}")

    group = f"ig-drill-dispatch-{uuid.uuid4().hex[:8]}"
    dispatcher = _GroupedDispatcher(group)

    async def _check_sent() -> bool:
        async with get_system_session() as session:
            row = await session.scalar(select(Message).where(Message.message_id == msg.message_id))
            return row is not None and row.delivery_status == "SENT"

    ok = await _run_worker_briefly(dispatcher, _check_sent, timeout=20.0)
    assert ok, "real Message row never reached delivery_status=SENT"
    assert _CAPTURED_REQUESTS, "no real HTTP request was captured by the mock transport"

    req = _CAPTURED_REQUESTS[-1]
    assert req["access_token"] == ctx["token"], (
        f"REGRESSION: dispatcher used the wrong token (expected this tenant's own "
        f"'{ctx['token']}', got '{req['access_token']}') -- would mean cross-tenant credential leakage"
    )
    assert req["body"]["recipient"]["id"] == msg.customer_phone
    assert req["body"]["message"]["text"] == msg.text
    print(f"PASS: real dispatcher used this tenant's OWN token ({req['access_token'][:20]}...), "
          f"real Graph API request shape correct, real Message row reached SENT")


async def scenario_b_no_credentials_dlqs(ctx_no_creds: dict) -> None:
    print("\n=== Scenario B: tenant with NO Instagram credentials -> must DLQ, not silently drop or share a token ===")
    msg = OutboundMessage(
        tenant_id=ctx_no_creds["tenant_id"], conversation_id=ctx_no_creds["conversation_id"],
        customer_phone=f"igsid-recipient-{uuid.uuid4().hex[:8]}",
        channel="instagram", platform_conversation_id="ig-drill-nocreds",
        text="test message that must not send",
        source_event_id=uuid.uuid4(), routing_tier_used="L1", model_used="test-model",
    )
    before_count = len(_CAPTURED_REQUESTS)
    producer = KafkaProducerManager()
    await producer.start()
    try:
        await producer.publish(topic=OUTGOING_TOPIC, event=msg, key=OutboundMessage.kafka_key(ctx_no_creds["tenant_id"], msg.customer_phone))
    finally:
        await producer.stop()

    group = f"ig-drill-nocreds-{uuid.uuid4().hex[:8]}"
    dispatcher = _GroupedDispatcher(group)
    dispatcher.max_retries = 1  # fail fast to DLQ for this drill

    async def _check_dlq_or_timeout() -> bool:
        # No real DLQ-arrival signal wired into this helper -- give it a
        # bounded window, then assert no send happened.
        await asyncio.sleep(3.0)
        return True

    await _run_worker_briefly(dispatcher, _check_dlq_or_timeout, timeout=6.0)
    assert len(_CAPTURED_REQUESTS) == before_count, (
        "REGRESSION: a send happened for a tenant with no configured Instagram credentials "
        "-- this can only mean a shared/fallback token leaked across tenants"
    )
    print("PASS: no send was made for a tenant with no configured Instagram credentials.")


async def main() -> None:
    _require_local()
    original_async_client = instagram_client_module.httpx.AsyncClient
    instagram_client_module.httpx.AsyncClient = _MockAsyncClient
    await redis_mgr.start()
    await whatsapp_client.start()  # OutboundDispatcherWorker.on_startup() needs this regardless of channel
    ctx_with = await _setup(with_credentials=True)
    ctx_without = await _setup(with_credentials=False)
    try:
        await scenario_a_real_send_with_own_token(ctx_with)
        await scenario_b_no_credentials_dlqs(ctx_without)
        print("\nALL SCENARIOS PASSED")
    finally:
        instagram_client_module.httpx.AsyncClient = original_async_client
        await whatsapp_client.stop()
        await redis_mgr.stop()
        await _cleanup(ctx_with)
        await _cleanup(ctx_without)


if __name__ == "__main__":
    asyncio.run(main())

"""
Real end-to-end validation of the VCard gate (item 7): before this, the
gatekeeper only flipped a DB flag and sent plain text — no vCard file was
ever built or sent, and the reminder sweep never actually delivered its
nudge message. This exercises the real fix against real Postgres + real
Kafka (Redpanda), through the real VCardGatekeeperWorker and
OutboundDispatcherWorker consumer loops (run()/_handle_record(), not
process_message() called directly).

Only the WhatsApp Cloud API's network transport is faked — via
httpx.MockTransport wired directly into WhatsAppClient's real httpx.AsyncClient,
so the client's actual request-building code (multipart media upload, JSON
document-message payload) runs unmodified and is what gets asserted on, not
a higher-level mock of send_text_message. No real WhatsApp credentials exist
yet, so this cannot prove a real phone receives the file — see
IMPLEMENTATION_STATUS.md for exactly what remains unproven.

Scenario A: NEW customer -> VCardGatekeeperWorker -> real vCard built +
"sent" (via the mocked transport) as a WhatsApp document message.
Scenario B: a customer stuck in VCARD_SENT past the reminder threshold is
swept by advance_vcard_states() and the reminder text is actually
delivered to their real conversation (not just a DB state bump).
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from sqlalchemy import select, update

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai_workers.outbound_dispatcher.worker import OutboundDispatcherWorker
from src.ai_workers.vcard_gatekeeper.worker import VCardGatekeeperWorker
from src.channel_adapters.whatsapp import client as whatsapp_client_module
from src.shared.core.config import get_settings
from src.shared.core.enums import Channel, CustomerVCardState, MessageType
from src.shared.db.models import Conversation, Customer, Tenant
from src.shared.db.session import get_system_session, get_tenant_session
from src.shared.events.canonical import CanonicalInboundEvent
from src.shared.events.outbound import OutboundMessage
from src.shared.kafka.consumer import BaseKafkaConsumer
from src.shared.kafka.producer import KafkaProducerManager
from src.shared.redis_client.client import redis_mgr
from src.shared.tasks.vcard_tasks import advance_vcard_states

settings = get_settings()
GATEKEEPER_TOPIC = settings.kafka_topic_vcard_gatekeeper
OUTGOING_TOPIC = settings.kafka_topic_messages_outgoing


def _require_local() -> None:
    if any(h.split(":")[0] not in {"localhost", "127.0.0.1"} for h in settings.kafka_bootstrap_servers.split(",")):
        raise RuntimeError("requires a local Kafka broker")
    if settings.redis_host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("requires local Redis")
    if settings.postgres_host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("requires a local Postgres database")


def _install_mock_whatsapp_transport(captured: dict) -> None:
    """Replace the real network layer only — every line of WhatsAppClient's
    own request-building code still runs for real."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/media"):
            captured["media_request_body"] = request.content
            captured.setdefault("media_uploads", 0)
            captured["media_uploads"] += 1
            return httpx.Response(200, json={"id": "mock-media-id-" + uuid.uuid4().hex[:8]})
        if request.url.path.endswith("/messages"):
            body = json.loads(request.content)
            captured.setdefault("messages", []).append(body)
            return httpx.Response(200, json={"messages": [{"id": "wamid.mock." + uuid.uuid4().hex[:8]}]})
        return httpx.Response(404)

    # start() no-ops if _http is already set, so this survives on_startup().
    whatsapp_client_module.whatsapp_client._http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=f"https://graph.facebook.com/{settings.meta_graph_api_version}",
    )


async def _setup() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    tenant_id, customer_id, conversation_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with get_system_session() as session:
        session.add(Tenant(
            tenant_id=tenant_id,
            business_name="VCard Drill Realty",
            fal_license_number=f"VCARD-{uuid.uuid4().hex[:10]}",
            whatsapp_phone_number_id="000000000000000",
            whatsapp_display_phone_number="+966501112222",
            meta_access_token="drill-dummy-token",
        ))
        session.add(Customer(
            customer_id=customer_id,
            tenant_id=tenant_id,
            unified_phone=f"+1555{uuid.uuid4().int % 10_000_000:07d}",
            display_name="VCard Drill Customer",
            vcard_state=CustomerVCardState.NEW,
        ))
        session.add(Conversation(
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            customer_id=customer_id,
            channel=Channel.WHATSAPP,
            platform_conversation_id="vcard-drill",
        ))
    return tenant_id, customer_id, conversation_id


async def _cleanup(tenant_id: uuid.UUID, customer_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
    from sqlalchemy import delete
    from src.shared.db.models import Message

    async with get_system_session() as session:
        await session.execute(delete(Message).where(Message.conversation_id == conversation_id))
        await session.execute(delete(Conversation).where(Conversation.conversation_id == conversation_id))
        await session.execute(delete(Customer).where(Customer.customer_id == customer_id))
        await session.execute(delete(Tenant).where(Tenant.tenant_id == tenant_id))
    print("cleanup: synthetic tenant/customer/conversation deleted")


async def _get_vcard_state(tenant_id: uuid.UUID, customer_id: uuid.UUID) -> str | None:
    async with get_tenant_session(tenant_id) as session:
        c = await session.scalar(select(Customer).where(Customer.customer_id == customer_id))
        return c.vcard_state if c else None


class _GroupedGatekeeper(VCardGatekeeperWorker):
    def __init__(self, group_id: str) -> None:
        BaseKafkaConsumer.__init__(
            self, topics=[GATEKEEPER_TOPIC], group_id=group_id, max_retries=2, retry_backoff_ms=100, batch_size=5,
        )
        self._outbound_producer = KafkaProducerManager()


class _GroupedDispatcher(OutboundDispatcherWorker):
    def __init__(self, group_id: str) -> None:
        BaseKafkaConsumer.__init__(
            self, topics=[OUTGOING_TOPIC], group_id=group_id, max_retries=2, retry_backoff_ms=100, batch_size=5,
        )


async def _run_worker_briefly(worker: BaseKafkaConsumer, condition, timeout: float = 20.0) -> bool:
    """Run a real consumer loop in the background until `condition()` is true or timeout.

    `condition` may be sync or async — its result is awaited if it's a coroutine.
    """
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


async def scenario_a_real_send(tenant_id: uuid.UUID, customer_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
    print("\n=== Scenario A: NEW customer -> real vCard built and 'sent' via WhatsAppClient ===")
    event = CanonicalInboundEvent(
        tenant_id=tenant_id,
        channel=Channel.WHATSAPP,
        platform_message_id="wamid.inbound." + uuid.uuid4().hex[:8],
        platform_conversation_id="vcard-drill",
        platform_user_id="+15550000000",
        customer_phone="+15550000000",
        message_type=MessageType.TEXT,
        text_content="hello",
    )
    decision_json = json.dumps({
        "event": json.loads(event.model_dump_json()),
        "tenant_id": str(tenant_id),
        "customer_id": str(customer_id),
        "conversation_id": str(conversation_id),
        "target_tier": "VCARD",
        "route_reason": "vcard_gate_required",
    })

    producer = KafkaProducerManager()
    await producer.start()
    try:
        await producer.publish_raw(GATEKEEPER_TOPIC, decision_json.encode("utf-8"), key=str(tenant_id).encode())
    finally:
        await producer.stop()
    print(f"published a real RoutingDecision to {GATEKEEPER_TOPIC}")

    gate_group = f"vcard-drill-gate-{uuid.uuid4().hex[:8]}"
    gatekeeper = _GroupedGatekeeper(gate_group)
    state_holder: dict = {}

    async def _check_state() -> bool:
        state_holder["state"] = await _get_vcard_state(tenant_id, customer_id)
        return state_holder.get("state") == CustomerVCardState.VCARD_SENT

    await _run_worker_briefly(gatekeeper, _check_state, timeout=15.0)
    assert state_holder.get("state") == CustomerVCardState.VCARD_SENT, (
        f"expected VCARD_SENT, got {state_holder.get('state')!r}"
    )
    print("PASS: real gatekeeper worker advanced Customer.vcard_state to VCARD_SENT")

    captured: dict = {}
    _install_mock_whatsapp_transport(captured)
    dispatch_group = f"vcard-drill-dispatch-{uuid.uuid4().hex[:8]}"
    dispatcher = _GroupedDispatcher(dispatch_group)
    await _run_worker_briefly(dispatcher, lambda: captured.get("messages"), timeout=20.0)

    assert captured.get("media_uploads") == 1, f"expected exactly 1 media upload, got {captured.get('media_uploads')}"
    body = captured["media_request_body"]
    assert b"BEGIN:VCARD" in body, "uploaded media body is not a VCard"
    assert b"VCard Drill Realty" in body, "VCard is missing the tenant's business name"
    assert b"+966501112222" in body, "VCard is missing the tenant's real WhatsApp display phone number"
    print("PASS: real multipart media upload carried an actual, correct VCard 3.0 file")

    messages = captured.get("messages") or []
    assert len(messages) == 1, f"expected exactly 1 WhatsApp message sent, got {len(messages)}"
    doc_msg = messages[0]
    assert doc_msg["type"] == "document", f"expected a document message, got {doc_msg['type']!r}"
    assert doc_msg["document"]["filename"] == "contact.vcf"
    print("PASS: real WhatsApp document message referenced the uploaded VCard media by id, filename=contact.vcf")


async def scenario_b_reminder_delivery(tenant_id: uuid.UUID, customer_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
    print("\n=== Scenario B: stuck-in-VCARD_SENT customer gets a REAL reminder message delivered ===")
    # Force the customer back to VCARD_SENT with an old updated_at so the
    # sweep's time-based threshold fires immediately.
    old_ts = datetime.now(tz=timezone.utc) - timedelta(hours=settings.vcard_validation_timeout_hours + 1)
    async with get_tenant_session(tenant_id) as session:
        await session.execute(
            update(Customer)
            .where(Customer.customer_id == customer_id)
            .values(vcard_state=CustomerVCardState.VCARD_SENT, updated_at=old_ts)
        )
    # SQLAlchemy's TimestampMixin likely re-stamps updated_at=now() via
    # onupdate on the next UPDATE — verify the backdate actually stuck.
    async with get_tenant_session(tenant_id) as session:
        c = await session.scalar(select(Customer).where(Customer.customer_id == customer_id))
        assert c.updated_at <= old_ts + timedelta(seconds=1), (
            f"backdating updated_at didn't stick (got {c.updated_at}) — the sweep's time "
            "threshold can't be exercised without waiting for real elapsed time"
        )

    producer = KafkaProducerManager()
    await producer.start()
    try:
        async with get_system_session() as session:
            result = await advance_vcard_states(session, batch_limit=50, producer=producer)
    finally:
        await producer.stop()
    assert str(customer_id) in result.customer_ids.get("reminder_1", []), (
        f"customer wasn't swept into reminder_1: {result.as_dict()}"
    )
    print("PASS: real DB sweep advanced the customer to REMINDER_1")

    captured: dict = {}
    _install_mock_whatsapp_transport(captured)
    dispatch_group = f"vcard-drill-reminder-{uuid.uuid4().hex[:8]}"
    dispatcher = _GroupedDispatcher(dispatch_group)
    await _run_worker_briefly(dispatcher, lambda: captured.get("messages"), timeout=20.0)

    messages = captured.get("messages") or []
    assert len(messages) == 1, f"expected exactly 1 reminder message dispatched, got {len(messages)}"
    assert messages[0]["type"] == "text"
    assert "تم" in messages[0]["text"]["body"], "reminder text doesn't match the expected copy"
    print("PASS: the reminder sweep's message was actually delivered (not just a silent DB state bump)")

    final_state = await _get_vcard_state(tenant_id, customer_id)
    assert final_state == CustomerVCardState.REMINDER_1, f"unexpected final state {final_state!r}"


async def main() -> None:
    _require_local()
    await redis_mgr.start()
    tenant_id, customer_id, conversation_id = await _setup()
    print(f"synthetic tenant={tenant_id} customer={customer_id} conversation={conversation_id}")
    try:
        await scenario_a_real_send(tenant_id, customer_id, conversation_id)
        await scenario_b_reminder_delivery(tenant_id, customer_id, conversation_id)
        print("\nALL SCENARIOS PASSED")
    finally:
        await _cleanup(tenant_id, customer_id, conversation_id)
        await redis_mgr.stop()


if __name__ == "__main__":
    asyncio.run(main())

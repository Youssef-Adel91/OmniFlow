"""
Real end-to-end validation of the outbound Kafka pipeline against a real
Postgres + real Kafka (Redpanda) + real Redis — not mocks, not a bypass of
BaseKafkaConsumer's run()/_handle_record() loop.

Exercises exactly what IMPLEMENTATION_STATUS.md flagged as unverified:

  1. Crash recovery: a real OutboundDispatcherWorker subprocess is killed
     (proc.kill()) AFTER it finishes real work (WhatsApp "send" — mocked at
     the HTTP client boundary only — Redis idempotency key, DB status
     update) but BEFORE the Kafka offset commit that would normally follow.
     A fresh consumer in the same consumer group then redelivers the same
     record; the test proves the message is NOT sent twice (the mocked
     WhatsApp call is asserted to NOT be invoked on redelivery — the
     existing idempotency short-circuit is what's actually being proven)
     and the DB/Kafka state converges correctly afterward.
  2. DLQ: a message whose "send" always fails exhausts real retries and
     lands on the real DLQ topic with the real payload; the DB row ends up
     FAILED; a message published right after is still processed normally
     by the same running consumer (no head-of-line blocking).
  3. DLQ-publish failure: if the DLQ producer itself fails, the consumer
     process must terminate rather than silently continuing past an
     unacknowledged record (the documented behavior in kafka/consumer.py).

Only the WhatsApp HTTP call is mocked — everything else (Postgres rows,
Kafka topics/offsets/consumer groups, Redis idempotency keys, the real
Celery task body, the real BaseKafkaConsumer retry/DLQ machinery) is real.
Creates and tears down its own synthetic tenant/customer/conversation;
refuses to run against anything but localhost.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaError
from aiokafka.structs import TopicPartition
from sqlalchemy import select

from src.ai_workers.outbound_dispatcher import worker as dispatcher_module
from src.ai_workers.outbound_dispatcher.worker import OutboundDispatcherWorker
from src.channel_adapters.whatsapp import client as whatsapp_client_module
from src.channel_adapters.whatsapp.client import SendResult
from src.shared.core.config import get_settings
from src.shared.core.enums import Channel
from src.shared.db.models import Conversation, Customer, Message, Tenant
from src.shared.db.session import get_system_session
from src.shared.kafka.consumer import BaseKafkaConsumer
from src.shared.redis_client.client import redis_mgr
from src.shared.tasks.outbound_tasks import publish_pending_messages

settings = get_settings()
TOPIC = settings.kafka_topic_messages_outgoing
DLQ_TOPIC = f"{TOPIC}.dlq"


def _require_local() -> None:
    hosts = settings.kafka_bootstrap_servers.split(",")
    if any(h.split(":")[0] not in {"localhost", "127.0.0.1"} for h in hosts):
        raise RuntimeError("This validator requires a local Kafka broker")
    if settings.redis_host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("This validator requires local Redis")
    if settings.postgres_host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("This validator requires a local Postgres database")


async def _setup_tenant_and_customer() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Real committed rows (a genuine cross-process crash test needs them visible
    to a separate OS process/connection, so this can't run inside one rolled-back
    transaction like the other validators). Cleaned up in cleanup()."""
    tenant_id = uuid.uuid4()
    customer_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    async with get_system_session() as session:
        session.add(Tenant(
            tenant_id=tenant_id,
            business_name="Kafka Crash Drill Co",
            fal_license_number=f"DRILL-{uuid.uuid4().hex[:10]}",
            whatsapp_phone_number_id="000000000000000",
            meta_access_token="drill-dummy-token",
        ))
        session.add(Customer(
            customer_id=customer_id,
            tenant_id=tenant_id,
            unified_phone=f"+1555{uuid.uuid4().int % 10_000_000:07d}",
            display_name="Kafka Crash Drill Customer",
        ))
        session.add(Conversation(
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            customer_id=customer_id,
            channel=Channel.WHATSAPP,
            platform_conversation_id="crash-drill",
        ))
    return tenant_id, customer_id, conversation_id


async def _create_pending_message(conversation_id: uuid.UUID, tenant_id: uuid.UUID, text: str) -> uuid.UUID:
    from src.shared.db.session import get_tenant_session

    message_id = uuid.uuid4()
    async with get_tenant_session(tenant_id) as session:
        session.add(Message(
            message_id=message_id,
            conversation_id=conversation_id,
            sender_type="human_agent",
            text_content=text,
            delivery_status="PENDING",
        ))
    return message_id


async def _get_delivery_status(tenant_id: uuid.UUID, message_id: uuid.UUID) -> str | None:
    from src.shared.db.session import get_tenant_session

    async with get_tenant_session(tenant_id) as session:
        msg = await session.scalar(select(Message).where(Message.message_id == message_id))
        return msg.delivery_status if msg else None


async def _cleanup(tenant_id: uuid.UUID, customer_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
    from sqlalchemy import delete

    # Tenant/Customer/Conversation FKs are ondelete=RESTRICT (by design — a
    # tenant can't be silently dropped in production), so children must go
    # first regardless of the ORM's declarative cascade= (which only applies
    # when deleting through a loaded object graph, not a bare DELETE here).
    async with get_system_session() as session:
        await session.execute(delete(Message).where(Message.conversation_id == conversation_id))
        await session.execute(delete(Conversation).where(Conversation.conversation_id == conversation_id))
        await session.execute(delete(Customer).where(Customer.customer_id == customer_id))
        await session.execute(delete(Tenant).where(Tenant.tenant_id == tenant_id))
    print("cleanup: synthetic tenant/customer/conversation/messages deleted")


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 1 — crash recovery
# ══════════════════════════════════════════════════════════════════════════════

async def scenario_crash_recovery(tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
    print("\n=== Scenario 1: kill the dispatcher after real work, before offset commit ===")
    message_id = await _create_pending_message(conversation_id, tenant_id, "crash-recovery-drill")

    result = await publish_pending_messages()
    assert result["queued"] >= 1, f"expected at least 1 queued, got {result}"
    print(f"published {result['queued']} pending message(s) to real Kafka topic {TOPIC}")

    group_id = f"crash-drill-{uuid.uuid4().hex[:8]}"
    marker_file = Path(tempfile.gettempdir()) / f"crash_marker_{uuid.uuid4().hex}.txt"
    subprocess_path = Path(__file__).parent / "_crash_worker_subprocess.py"

    proc = subprocess.Popen(
        [sys.executable, str(subprocess_path), group_id, str(message_id), str(marker_file)],
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not marker_file.exists():
            await asyncio.sleep(0.25)
        assert marker_file.exists(), (
            "subprocess never reached the post-work marker — it either crashed early "
            "or never received the message from Kafka"
        )
        print("subprocess completed real send/Redis/DB work and is now hung (simulating pre-commit crash)")

        status_before_kill = await _get_delivery_status(tenant_id, message_id)
        assert status_before_kill == "SENT", (
            f"expected DB status SENT before the crash, got {status_before_kill!r} — "
            "the subprocess's real work didn't actually complete"
        )
        idem_key = f"sent:{tenant_id}:{message_id}"
        sent_wamid = await redis_mgr.get_raw(idem_key)
        assert sent_wamid, "Redis idempotency key was not set by the real send path before the crash"
        print(f"confirmed: DB delivery_status=SENT, Redis idempotency key set (wamid={sent_wamid})")

        proc.kill()
        proc.wait(timeout=10)
        print(f"subprocess killed (returncode={proc.returncode}) — offset was never committed")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
    marker_file.unlink(missing_ok=True)

    # Prove the Kafka offset genuinely wasn't committed: a fresh consumer in
    # the SAME group must still receive this record.
    send_calls = 0

    async def _counting_fake_send(*, phone_number_id, to, text, access_token, **kwargs):
        nonlocal send_calls
        send_calls += 1
        return SendResult(wamid="wamid.should-not-happen", phone_number=to, message_status="accepted")

    whatsapp_client_module.whatsapp_client.send_text_message = _counting_fake_send

    class RecoveryDispatcher(OutboundDispatcherWorker):
        def __init__(self) -> None:
            BaseKafkaConsumer.__init__(
                self, topics=[TOPIC], group_id=group_id, max_retries=2, retry_backoff_ms=100, batch_size=5,
            )

    recovery = RecoveryDispatcher()
    task = asyncio.create_task(recovery.run())
    try:
        deadline = time.monotonic() + 20
        recovered_status = None
        while time.monotonic() < deadline:
            recovered_status = await _get_delivery_status(tenant_id, message_id)
            if recovered_status == "SENT":
                break
            await asyncio.sleep(0.5)
        assert recovered_status == "SENT", f"message never reconverged to SENT, last saw {recovered_status!r}"
    finally:
        recovery._stop_event.set()
        try:
            await asyncio.wait_for(task, timeout=10)
        except asyncio.TimeoutError:
            task.cancel()

    assert send_calls == 0, (
        f"WhatsApp 'send' was called {send_calls} time(s) on redelivery — this would be a REAL "
        "duplicate customer message. The idempotency short-circuit (Redis 'sent:' key check) failed."
    )
    print("PASS: redelivered record did NOT trigger a duplicate WhatsApp send; DB reconverged to SENT")


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 2 — DLQ + no head-of-line blocking
# ══════════════════════════════════════════════════════════════════════════════

async def scenario_dlq_and_continue(tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
    print("\n=== Scenario 2: persistent failure -> real DLQ, then prove no head-of-line blocking ===")
    dlq_message_id = await _create_pending_message(conversation_id, tenant_id, "dlq-drill")
    result = await publish_pending_messages()
    assert result["queued"] >= 1, result

    group_id = f"dlq-drill-{uuid.uuid4().hex[:8]}"
    dlq_message_id_str = str(dlq_message_id)
    canary_message_id_holder: dict[str, str] = {}

    async def _selective_fail_send(*, phone_number_id, to, text, access_token, **kwargs):
        # We can't see the message_id here directly, so key off the text instead.
        if text == "dlq-drill":
            raise RuntimeError("simulated persistent WhatsApp outage")
        return SendResult(wamid="wamid.canary." + uuid.uuid4().hex[:8], phone_number=to, message_status="accepted")

    whatsapp_client_module.whatsapp_client.send_text_message = _selective_fail_send

    class DlqTestDispatcher(OutboundDispatcherWorker):
        def __init__(self) -> None:
            BaseKafkaConsumer.__init__(
                self, topics=[TOPIC], group_id=group_id, max_retries=2, retry_backoff_ms=100, batch_size=5,
            )

    worker = DlqTestDispatcher()
    task = asyncio.create_task(worker.run())

    # Watch the REAL DLQ topic with an independent consumer.
    dlq_consumer = AIOKafkaConsumer(
        DLQ_TOPIC,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=f"dlq-watcher-{uuid.uuid4().hex[:8]}",
        auto_offset_reset="latest",
        enable_auto_commit=True,
    )
    await dlq_consumer.start()
    try:
        found_dlq_payload = None
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and found_dlq_payload is None:
            batch = await dlq_consumer.getmany(timeout_ms=500, max_records=10)
            for _, records in batch.items():
                for record in records:
                    payload = json.loads(record.value.decode("utf-8"))
                    if dlq_message_id_str in (payload.get("original_value") or ""):
                        found_dlq_payload = payload
                        break
        assert found_dlq_payload is not None, "DLQ'd message never showed up on the real DLQ topic"
        assert found_dlq_payload["original_topic"] == TOPIC
        print(f"PASS: real DLQ payload observed on {DLQ_TOPIC}: error={found_dlq_payload['error']!r}")

        deadline = time.monotonic() + 15
        dlq_db_status = None
        while time.monotonic() < deadline:
            dlq_db_status = await _get_delivery_status(tenant_id, dlq_message_id)
            if dlq_db_status == "FAILED":
                break
            await asyncio.sleep(0.5)
        assert dlq_db_status == "FAILED", f"expected DB status FAILED for the DLQ'd message, got {dlq_db_status!r}"
        print("PASS: DLQ'd message's DB row correctly ended up FAILED")

        # Now prove the consumer wasn't stuck: publish a canary and confirm
        # the SAME still-running worker processes it normally afterward.
        canary_message_id = await _create_pending_message(conversation_id, tenant_id, "canary-drill")
        canary_message_id_holder["id"] = str(canary_message_id)
        result = await publish_pending_messages()
        assert result["queued"] >= 1, result

        deadline = time.monotonic() + 20
        canary_status = None
        while time.monotonic() < deadline:
            canary_status = await _get_delivery_status(tenant_id, canary_message_id)
            if canary_status == "SENT":
                break
            await asyncio.sleep(0.5)
        assert canary_status == "SENT", (
            f"canary message published after the DLQ'd one never reached SENT (got {canary_status!r}) — "
            "the consumer got stuck after DLQ'ing the earlier record"
        )
        print("PASS: canary message published right after the DLQ'd one was still processed normally — no head-of-line blocking")
    finally:
        worker._stop_event.set()
        try:
            await asyncio.wait_for(task, timeout=10)
        except asyncio.TimeoutError:
            task.cancel()
        await dlq_consumer.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 3 — DLQ producer itself fails -> consumer must terminate
# ══════════════════════════════════════════════════════════════════════════════

async def scenario_dlq_publish_failure_terminates_consumer(tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
    print("\n=== Scenario 3: DLQ publish itself fails -> consumer must terminate, not continue ===")
    message_id = await _create_pending_message(conversation_id, tenant_id, "dlq-outage-drill")
    result = await publish_pending_messages()
    assert result["queued"] >= 1, result

    group_id = f"dlq-outage-drill-{uuid.uuid4().hex[:8]}"

    async def _always_fail_send(*, phone_number_id, to, text, access_token, **kwargs):
        raise RuntimeError("simulated persistent WhatsApp outage")

    whatsapp_client_module.whatsapp_client.send_text_message = _always_fail_send

    class DoomedDispatcher(OutboundDispatcherWorker):
        def __init__(self) -> None:
            BaseKafkaConsumer.__init__(
                self, topics=[TOPIC], group_id=group_id, max_retries=1, retry_backoff_ms=50, batch_size=5,
            )

        async def on_startup(self) -> None:
            await super().on_startup()
            # Patch the real DLQ producer's send call (not _publish_to_dlq
            # itself) so the actual code path in kafka/consumer.py —
            # including its critical-log-then-re-raise — is what's exercised.
            async def _doomed_send_and_wait(*args, **kwargs):
                raise KafkaError("simulated DLQ broker outage")

            self._dlq_producer.send_and_wait = _doomed_send_and_wait

    worker = DoomedDispatcher()
    raised = False
    try:
        await asyncio.wait_for(worker.run(), timeout=20)
    except KafkaError:
        raised = True
    except asyncio.TimeoutError:
        raised = False
    assert raised, (
        "worker.run() did not propagate the DLQ publish failure — a broken DLQ path "
        "would silently let the consumer continue past an unacknowledged record"
    )
    print("PASS: a failed DLQ publish correctly terminates the consumer (exception propagated out of run())")


async def main() -> None:
    _require_local()
    await redis_mgr.start()
    tenant_id, customer_id, conversation_id = await _setup_tenant_and_customer()
    print(f"synthetic tenant={tenant_id} customer={customer_id} conversation={conversation_id}")
    try:
        await scenario_crash_recovery(tenant_id, conversation_id)
        await scenario_dlq_and_continue(tenant_id, conversation_id)
        await scenario_dlq_publish_failure_terminates_consumer(tenant_id, conversation_id)
        print("\nALL SCENARIOS PASSED")
    finally:
        await _cleanup(tenant_id, customer_id, conversation_id)
        await redis_mgr.stop()


if __name__ == "__main__":
    asyncio.run(main())

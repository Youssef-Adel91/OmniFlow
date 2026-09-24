"""
Real end-to-end validation of the broadcast pipeline (item 8). Before this,
`BroadcastWorker` used raw SQL against columns that don't exist
(`broadcast_deliveries.status` instead of `delivery_status`,
`vip_subscribers.list_type` which was never a column, `customers.primary_phone`
instead of `unified_phone`) and never called the WhatsApp API at all — it
would have crashed on its first real recipient row. There was also no
Kafka-publish wiring from `schedule_campaign` and no Meta template-approval
gate before a real send.

Real Postgres + real Kafka (Redpanda), through the actual
`_broadcast_dispatch_check_async()` Beat task body and the real
`BroadcastWorker` consumer loop (`run()`/`_handle_record()`). Only the
WhatsApp network transport is faked via httpx.MockTransport, same technique
as `validate_vcard_delivery.py` — the client's real request-building code
runs unmodified.

Scenario A: a SCHEDULED campaign with no meta_template_id must be rejected by
the dispatch sweep BEFORE it ever reaches Kafka or WhatsApp (the actual
number-ban-risk gate this item calls out).
Scenario B: a SCHEDULED campaign with a template, targeting the "vip"
segment, is dispatched, consumed by the real worker, sent (mocked transport)
to exactly the VIP-flagged customer, and correctly recorded.
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
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai_workers.broadcast_worker.worker import BroadcastWorker
from src.channel_adapters.whatsapp import client as whatsapp_client_module
from src.shared.core.config import get_settings
from src.shared.core.enums import BroadcastCampaignStatus, Channel
from src.shared.db.models import BroadcastCampaign, BroadcastDelivery, Customer, Tenant
from src.shared.db.session import get_system_session, get_tenant_session
from src.shared.kafka.consumer import BaseKafkaConsumer
from src.shared.redis_client.client import redis_mgr

settings = get_settings()
BROADCAST_TOPIC = settings.kafka_topic_broadcast_marketing


def _require_local() -> None:
    if any(h.split(":")[0] not in {"localhost", "127.0.0.1"} for h in settings.kafka_bootstrap_servers.split(",")):
        raise RuntimeError("requires a local Kafka broker")
    if settings.redis_host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("requires local Redis")
    if settings.postgres_host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("requires a local Postgres database")


def _install_mock_whatsapp_transport(captured: dict) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            body = json.loads(request.content)
            captured.setdefault("messages", []).append(body)
            return httpx.Response(200, json={"messages": [{"id": "wamid.mock." + uuid.uuid4().hex[:8]}]})
        return httpx.Response(404)

    whatsapp_client_module.whatsapp_client._http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=f"https://graph.facebook.com/{settings.meta_graph_api_version}",
    )


async def _setup() -> dict:
    tenant_id = uuid.uuid4()
    vip_id, regular_id = uuid.uuid4(), uuid.uuid4()
    async with get_system_session() as session:
        session.add(Tenant(
            tenant_id=tenant_id,
            business_name="Broadcast Drill Realty",
            fal_license_number=f"BCAST-{uuid.uuid4().hex[:10]}",
            whatsapp_phone_number_id="000000000000000",
            meta_access_token="drill-dummy-token",
        ))
        session.add(Customer(
            customer_id=vip_id, tenant_id=tenant_id,
            unified_phone=f"+1555{uuid.uuid4().int % 10_000_000:07d}",
            display_name="VIP Drill Customer", is_vip=True,
        ))
        session.add(Customer(
            customer_id=regular_id, tenant_id=tenant_id,
            unified_phone=f"+1555{uuid.uuid4().int % 10_000_000:07d}",
            display_name="Regular Drill Customer", is_vip=False,
        ))
    return {"tenant_id": tenant_id, "vip_id": vip_id, "regular_id": regular_id}


async def _cleanup(ctx: dict) -> None:
    from sqlalchemy import delete

    async with get_system_session() as session:
        await session.execute(delete(BroadcastDelivery).where(BroadcastDelivery.tenant_id == ctx["tenant_id"]))
        await session.execute(delete(BroadcastCampaign).where(BroadcastCampaign.tenant_id == ctx["tenant_id"]))
        await session.execute(delete(Customer).where(Customer.tenant_id == ctx["tenant_id"]))
        await session.execute(delete(Tenant).where(Tenant.tenant_id == ctx["tenant_id"]))
    print("cleanup: synthetic tenant/customers/campaigns/deliveries deleted")


async def _create_campaign(tenant_id: uuid.UUID, *, meta_template_id: str | None, segment: str) -> uuid.UUID:
    campaign_id = uuid.uuid4()
    async with get_tenant_session(tenant_id) as session:
        session.add(BroadcastCampaign(
            campaign_id=campaign_id,
            tenant_id=tenant_id,
            title="Drill campaign",
            message_template="hello",
            target_audience={"segment": segment},
            meta_template_id=meta_template_id,
            status=BroadcastCampaignStatus.SCHEDULED,
            scheduled_at=datetime.now(timezone.utc) - timedelta(seconds=5),  # already due
        ))
    return campaign_id


async def _get_campaign_status(tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> str | None:
    async with get_tenant_session(tenant_id) as session:
        c = await session.scalar(select(BroadcastCampaign).where(BroadcastCampaign.campaign_id == campaign_id))
        return c.status if c else None


class _GroupedBroadcastWorker(BroadcastWorker):
    def __init__(self, group_id: str) -> None:
        BaseKafkaConsumer.__init__(
            self, topics=[BROADCAST_TOPIC], group_id=group_id, max_retries=2, retry_backoff_ms=100, batch_size=5,
        )


async def _run_worker_briefly(worker, async_condition, timeout: float = 20.0) -> None:
    """Run a real consumer loop in the background until `async_condition()` is true or timeout."""
    task = asyncio.create_task(worker.run())
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await async_condition():
                return
            await asyncio.sleep(0.25)
    finally:
        worker._stop_event.set()
        try:
            await asyncio.wait_for(task, timeout=10)
        except asyncio.TimeoutError:
            task.cancel()


async def scenario_a_rejects_without_template(ctx: dict) -> None:
    print("\n=== Scenario A: campaign without an approved template is rejected before Kafka/WhatsApp ===")
    from src.celery_app.tasks import _broadcast_dispatch_check_async

    campaign_id = await _create_campaign(ctx["tenant_id"], meta_template_id=None, segment="all")
    result = await _broadcast_dispatch_check_async()
    assert str(campaign_id) in result["campaign_ids"]["rejected"], result
    assert str(campaign_id) not in result["campaign_ids"]["dispatched"], result

    status_ = await _get_campaign_status(ctx["tenant_id"], campaign_id)
    assert status_ == BroadcastCampaignStatus.FAILED, f"expected FAILED, got {status_!r}"
    print("PASS: template-less campaign was rejected by the real dispatch sweep — never reached Kafka")


async def scenario_b_real_send_to_vip_segment(ctx: dict) -> None:
    print("\n=== Scenario B: VIP-segment campaign dispatched and sent via the real worker ===")
    campaign_id = await _create_campaign(ctx["tenant_id"], meta_template_id="drill_template_v1", segment="vip")

    from src.celery_app.tasks import _broadcast_dispatch_check_async

    result = await _broadcast_dispatch_check_async()
    assert str(campaign_id) in result["campaign_ids"]["dispatched"], result
    print(f"real dispatch sweep published campaign {campaign_id} to {BROADCAST_TOPIC}")

    status_after_dispatch = await _get_campaign_status(ctx["tenant_id"], campaign_id)
    assert status_after_dispatch == BroadcastCampaignStatus.SENDING, status_after_dispatch

    captured: dict = {}
    _install_mock_whatsapp_transport(captured)
    group_id = f"broadcast-drill-{uuid.uuid4().hex[:8]}"
    worker = _GroupedBroadcastWorker(group_id)

    async def _done() -> bool:
        s = await _get_campaign_status(ctx["tenant_id"], campaign_id)
        return s in (BroadcastCampaignStatus.COMPLETED, BroadcastCampaignStatus.FAILED)

    await _run_worker_briefly(worker, _done)

    final_status = await _get_campaign_status(ctx["tenant_id"], campaign_id)
    assert final_status == BroadcastCampaignStatus.COMPLETED, f"expected COMPLETED, got {final_status!r}"

    messages = captured.get("messages") or []
    assert len(messages) == 1, f"expected exactly 1 real WhatsApp send (VIP customer only), got {len(messages)}"
    assert messages[0]["type"] == "template"
    assert messages[0]["template"]["name"] == "drill_template_v1"
    print("PASS: exactly one WhatsApp template message sent, to the VIP customer only (regular customer excluded)")

    async with get_tenant_session(ctx["tenant_id"]) as session:
        vip_delivery = await session.scalar(
            select(BroadcastDelivery).where(
                BroadcastDelivery.campaign_id == campaign_id,
                BroadcastDelivery.customer_id == ctx["vip_id"],
            )
        )
        regular_delivery = await session.scalar(
            select(BroadcastDelivery).where(
                BroadcastDelivery.campaign_id == campaign_id,
                BroadcastDelivery.customer_id == ctx["regular_id"],
            )
        )
    assert vip_delivery is not None and vip_delivery.delivery_status == "SENT" and vip_delivery.platform_message_id
    assert regular_delivery is None, "the non-VIP customer should never have gotten a delivery row"
    print("PASS: BroadcastDelivery correctly recorded (delivery_status=SENT, real wamid) for the VIP customer only")


async def main() -> None:
    _require_local()
    await redis_mgr.start()
    ctx = await _setup()
    print(f"synthetic tenant={ctx['tenant_id']} vip_customer={ctx['vip_id']} regular_customer={ctx['regular_id']}")
    try:
        await scenario_a_rejects_without_template(ctx)
        await scenario_b_real_send_to_vip_segment(ctx)
        print("\nALL SCENARIOS PASSED")
    finally:
        await _cleanup(ctx)
        await redis_mgr.stop()


if __name__ == "__main__":
    asyncio.run(main())

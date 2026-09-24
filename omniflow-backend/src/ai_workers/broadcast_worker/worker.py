"""
ai_workers/broadcast_worker/worker.py — VIP Broadcast Worker

Consumes from `broadcast.marketing.v1` (published by the
`omniflow.broadcast_dispatch_check` Celery Beat sweep) and sends a scheduled
campaign to its resolved audience via a real Meta WhatsApp template message.

Rewritten from scratch — the previous version used raw SQL against columns
that don't exist (`broadcast_deliveries.status` instead of `delivery_status`,
`vip_subscribers.list_type` which was never a column) and never actually
called the WhatsApp API (`asyncio.sleep(0.02)` with a comment claiming to
"simulate" the send). It would have crashed on its first real customer row.

Audience resolution mirrors `gateway/routers/broadcasts.py`'s segment logic
(all/vip/engaged/new/inactive) rather than the old `vip_subscribers.list_type`
scheme, since that's what the API's audience-preview endpoint (and therefore
whatever a tenant saw before scheduling) actually promises.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from aiokafka.structs import ConsumerRecord
from sqlalchemy import func, or_, select

from src.ai_workers.outbound_dispatcher.worker import _resolve_tenant_credentials
from src.channel_adapters.whatsapp.client import WhatsAppAPIError, whatsapp_client
from src.shared.core.config import get_settings
from src.shared.core.enums import BroadcastCampaignStatus
from src.shared.db.models import BroadcastCampaign, BroadcastDelivery, Customer
from src.shared.db.session import get_tenant_session
from src.shared.events.broadcast import BroadcastEvent
from src.shared.kafka.consumer import BaseKafkaConsumer

logger = structlog.get_logger(__name__)
settings = get_settings()

# Matches gateway/routers/broadcasts.py::VALID_SEGMENTS — kept in sync by hand
# since importing a FastAPI router module into a Kafka worker would pull in
# the whole gateway app for one constant.
_SEGMENT_ALL = "all"
_SEGMENT_VIP = "vip"
_SEGMENT_ENGAGED = "engaged"
_SEGMENT_NEW = "new"
_SEGMENT_INACTIVE = "inactive"
_NEW_CUSTOMER_WINDOW_DAYS = 30

# 50 msg/sec/tenant cap (SRS §5.6) — one send every 20ms.
_RATE_LIMIT_DELAY_SECONDS = 0.02


def _resolve_segment(target_audience: dict | None) -> str:
    value = None
    if isinstance(target_audience, dict):
        value = target_audience.get("segment") or target_audience.get("list_type")
    if not isinstance(value, str):
        return _SEGMENT_ALL
    candidate = value.strip().lower()
    return candidate if candidate in {
        _SEGMENT_ALL, _SEGMENT_VIP, _SEGMENT_ENGAGED, _SEGMENT_NEW, _SEGMENT_INACTIVE,
    } else _SEGMENT_ALL


async def _select_audience(session, segment: str) -> list[Customer]:
    """Real recipient rows for a segment — the send-time counterpart of
    broadcasts.py::_count_audience, which only counts."""
    threshold = settings.vip_engagement_score_threshold
    stmt = select(Customer).where(Customer.is_processing_restricted.is_(False))

    if segment == _SEGMENT_VIP:
        stmt = stmt.where(Customer.is_vip.is_(True))
    elif segment == _SEGMENT_ENGAGED:
        stmt = stmt.where(or_(Customer.is_vip.is_(True), Customer.engagement_score >= threshold))
    elif segment == _SEGMENT_NEW:
        cutoff = datetime.now(timezone.utc) - timedelta(days=_NEW_CUSTOMER_WINDOW_DAYS)
        stmt = stmt.where(Customer.created_at >= cutoff)
    elif segment == _SEGMENT_INACTIVE:
        stmt = stmt.where(
            or_(Customer.engagement_score.is_(None), Customer.engagement_score < threshold)
        ).where(Customer.is_vip.is_(False))

    return list((await session.execute(stmt)).scalars().all())


class BroadcastWorker(BaseKafkaConsumer):
    def __init__(self) -> None:
        super().__init__(
            topics=[settings.kafka_topic_broadcast_marketing],
            group_id=settings.kafka_consumer_group_broadcast_workers,
            max_retries=3,
            retry_backoff_ms=2000,
            batch_size=1,  # one campaign at a time — a campaign send can take a while
        )

    async def on_startup(self) -> None:
        await whatsapp_client.start()
        logger.info("broadcast_worker_ready", topic=settings.kafka_topic_broadcast_marketing)

    async def on_shutdown(self) -> None:
        await whatsapp_client.stop()
        logger.info("broadcast_worker_shutdown")

    async def process_message(self, record: ConsumerRecord) -> None:
        raw = record.value
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")

        try:
            event = BroadcastEvent.model_validate_json(raw)
        except Exception as exc:
            raise ValueError(f"Cannot deserialize BroadcastEvent: {exc}") from exc

        log = logger.bind(campaign_id=str(event.campaign_id), tenant_id=str(event.tenant_id))
        log.info("broadcast_worker_received_campaign")

        async with get_tenant_session(event.tenant_id) as session:
            campaign = await session.scalar(
                select(BroadcastCampaign).where(BroadcastCampaign.campaign_id == event.campaign_id)
            )
            if not campaign:
                log.error("broadcast_worker_campaign_not_found")
                return

            # Re-checked here too (not just in the Beat sweep and the API):
            # this is the last line of defense before a real WhatsApp call,
            # and defense-in-depth against a future caller that publishes to
            # this topic directly, bypassing the sweep's own check.
            if not campaign.meta_template_id:
                campaign.status = BroadcastCampaignStatus.FAILED
                campaign.completed_at = datetime.now(timezone.utc)
                log.error("broadcast_worker_rejected_no_template")
                return

            segment = _resolve_segment(campaign.target_audience)
            recipients = await _select_audience(session, segment)
            if not recipients:
                campaign.status = BroadcastCampaignStatus.FAILED
                campaign.completed_at = datetime.now(timezone.utc)
                log.info("broadcast_worker_no_recipients", segment=segment)
                return

            log.info("broadcast_worker_found_recipients", count=len(recipients), segment=segment)

        creds = await _resolve_tenant_credentials(event.tenant_id)
        if not creds:
            async with get_tenant_session(event.tenant_id) as session:
                campaign = await session.scalar(
                    select(BroadcastCampaign).where(BroadcastCampaign.campaign_id == event.campaign_id)
                )
                campaign.status = BroadcastCampaignStatus.FAILED
                campaign.completed_at = datetime.now(timezone.utc)
            log.error("broadcast_worker_no_credentials")
            return

        sent_count = 0
        failed_count = 0
        for customer in recipients:
            async with get_tenant_session(event.tenant_id) as session:
                # One row per (campaign, customer) — the unique constraint
                # makes a redelivered Kafka record (at-least-once) a no-op
                # rather than a double-send, same idempotency shape as the
                # regular outbound dispatcher's Redis-based guard.
                existing = await session.scalar(
                    select(BroadcastDelivery).where(
                        BroadcastDelivery.campaign_id == event.campaign_id,
                        BroadcastDelivery.customer_id == customer.customer_id,
                    )
                )
                if existing:
                    if existing.delivery_status in ("SENT", "DELIVERED", "READ"):
                        sent_count += 1
                        continue

                try:
                    result = await whatsapp_client.send_template_message(
                        phone_number_id=creds.phone_number_id,
                        to=customer.unified_phone,
                        template_name=campaign.meta_template_id,
                        access_token=creds.access_token,
                    )
                except WhatsAppAPIError as exc:
                    failed_count += 1
                    if existing:
                        existing.delivery_status = "FAILED"
                        existing.error_detail = str(exc)[:2000]
                        existing.retry_count += 1
                    else:
                        session.add(BroadcastDelivery(
                            delivery_id=uuid.uuid4(),
                            tenant_id=event.tenant_id,
                            campaign_id=event.campaign_id,
                            customer_id=customer.customer_id,
                            delivery_status="FAILED",
                            error_detail=str(exc)[:2000],
                        ))
                    log.warning("broadcast_send_failed", customer_id=str(customer.customer_id), error=str(exc))
                else:
                    sent_count += 1
                    if existing:
                        existing.delivery_status = "SENT"
                        existing.platform_message_id = result.wamid
                        existing.sent_at = datetime.now(timezone.utc)
                    else:
                        session.add(BroadcastDelivery(
                            delivery_id=uuid.uuid4(),
                            tenant_id=event.tenant_id,
                            campaign_id=event.campaign_id,
                            customer_id=customer.customer_id,
                            delivery_status="SENT",
                            platform_message_id=result.wamid,
                            sent_at=datetime.now(timezone.utc),
                        ))

            await asyncio.sleep(_RATE_LIMIT_DELAY_SECONDS)

        async with get_tenant_session(event.tenant_id) as session:
            campaign = await session.scalar(
                select(BroadcastCampaign).where(BroadcastCampaign.campaign_id == event.campaign_id)
            )
            campaign.status = (
                BroadcastCampaignStatus.COMPLETED if sent_count else BroadcastCampaignStatus.FAILED
            )
            campaign.completed_at = datetime.now(timezone.utc)
            campaign.recipients_count = len(recipients)

        log.info("broadcast_worker_campaign_completed", sent_count=sent_count, failed_count=failed_count)


async def _main() -> None:
    worker = BroadcastWorker()
    await worker.run()


def run() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    run()

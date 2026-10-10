"""
ai_workers/broadcast_worker/worker.py — Broadcast Worker

Consumes from `broadcast.marketing.v1` (published by the `omniflow.broadcast_dispatch_check` Celery Beat sweep)
and sends a scheduled campaign to its audience as an APPROVED Meta WhatsApp template message.

Audience = `shared/services/broadcast_audience.py` -- the same definition the API used for the confirmed recipient
count: marketing opt-in, never opted out, real phone number (`phone_e164`, never the Instagram/Messenger platform id),
not archived, no PDPL processing objection. Consent is re-read for every recipient immediately before the send, so a
customer who unsubscribes while the campaign is running is skipped. Template body variables ({{1}}, {{2}}…) are filled
per recipient ({name}, {city}). The send rate honours the campaign's `send_rate_per_second`.
Failures are recorded on the campaign (`last_error`) and per delivery (`error_detail`).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import structlog
from aiokafka.structs import ConsumerRecord
from sqlalchemy import select

from src.ai_workers.outbound_dispatcher.worker import _resolve_tenant_credentials
from src.channel_adapters.whatsapp.client import WhatsAppAPIError, whatsapp_client
from src.shared.core.config import get_settings
from src.shared.core.enums import BroadcastCampaignStatus
from src.shared.db.models import BroadcastCampaign, BroadcastDelivery, Customer
from src.shared.db.session import get_tenant_session
from src.shared.events.broadcast import BroadcastEvent
from src.shared.kafka.consumer import BaseKafkaConsumer
from src.shared.services import broadcast_audience as aud

logger = structlog.get_logger(__name__)
settings = get_settings()

_MAX_RATE = 80


def _components(params: list[str]) -> list[dict] | None:
    if not params:
        return None
    return [{"type": "body", "parameters": [{"type": "text", "text": p[:1024]} for p in params]}]


async def _fail(tenant_id: uuid.UUID, campaign_id: uuid.UUID, reason: str) -> None:
    async with get_tenant_session(tenant_id) as session:
        campaign = await session.scalar(select(BroadcastCampaign).where(BroadcastCampaign.campaign_id == campaign_id))
        if campaign is not None:
            campaign.status = BroadcastCampaignStatus.FAILED
            campaign.completed_at = datetime.now(timezone.utc)
            campaign.last_error = reason[:1000]


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
            if str(getattr(campaign.status, "value", campaign.status)) not in ("sending", "scheduled"):
                log.warning("broadcast_worker_skipped_state", status=str(campaign.status))
                return

            # Last line of defense before a real WhatsApp call (also checked by the API and the Beat sweep).
            if not campaign.meta_template_id:
                await _fail_in(session, campaign, "لا يوجد قالب واتساب معتمد للحملة.")
                log.error("broadcast_worker_rejected_no_template")
                return
            try:
                audience = aud.parse_audience(campaign.target_audience)
            except aud.InvalidAudience as exc:
                await _fail_in(session, campaign, f"جمهور غير صالح: {exc}")
                log.error("broadcast_worker_invalid_audience", error=str(exc))
                return

            campaign.started_at = campaign.started_at or datetime.now(timezone.utc)
            template, language = campaign.meta_template_id, campaign.template_language or "ar"
            params_tpl = list(campaign.template_params or [])
            delay = 1.0 / max(1, min(int(campaign.send_rate_per_second or 20), _MAX_RATE))
            recipient_ids = [
                row for row in (await session.execute(
                    aud.select_customers(audience, engagement_threshold=settings.vip_engagement_score_threshold)
                    .with_only_columns(Customer.customer_id).order_by(Customer.created_at))).scalars().all()
            ]
            if not recipient_ids:
                await _fail_in(session, campaign, "لا يوجد عملاء مؤهلون (موافقة تسويقية + رقم جوال) وقت الإرسال.")
                log.info("broadcast_worker_no_recipients", segment=audience.segment)
                return
            log.info("broadcast_worker_found_recipients", count=len(recipient_ids), segment=audience.segment)

        creds = await _resolve_tenant_credentials(event.tenant_id)
        if not creds:
            await _fail(event.tenant_id, event.campaign_id, "واتساب غير مربوط: لا توجد بيانات اعتماد صالحة للمستأجر.")
            log.error("broadcast_worker_no_credentials")
            return

        sent_count = failed_count = skipped_count = 0
        last_error: str | None = None
        for customer_id in recipient_ids:
            async with get_tenant_session(event.tenant_id) as session:
                # Re-read consent NOW: skip anyone who unsubscribed (or was archived/restricted) since the audience was built.
                customer = await session.scalar(aud.select_customers(audience, engagement_threshold=settings.vip_engagement_score_threshold)
                                                .where(Customer.customer_id == customer_id))
                if customer is None:
                    skipped_count += 1
                    continue
                existing = await session.scalar(
                    select(BroadcastDelivery).where(
                        BroadcastDelivery.campaign_id == event.campaign_id,
                        BroadcastDelivery.customer_id == customer_id,
                    )
                )
                if existing and existing.delivery_status in ("SENT", "DELIVERED", "READ"):
                    sent_count += 1          # at-least-once Kafka redelivery: never double-send
                    continue

                try:
                    result = await whatsapp_client.send_template_message(
                        phone_number_id=creds.phone_number_id,
                        to=customer.phone_e164,
                        template_name=template,
                        language_code=language,
                        components=_components(aud.render_params(params_tpl, customer)),
                        access_token=creds.access_token,
                    )
                except WhatsAppAPIError as exc:
                    failed_count += 1
                    last_error = str(exc)[:500]
                    if existing:
                        existing.delivery_status = "FAILED"
                        existing.error_detail = str(exc)[:2000]
                        existing.retry_count += 1
                    else:
                        session.add(BroadcastDelivery(
                            delivery_id=uuid.uuid4(), tenant_id=event.tenant_id, campaign_id=event.campaign_id,
                            customer_id=customer_id, delivery_status="FAILED", error_detail=str(exc)[:2000]))
                    log.warning("broadcast_send_failed", customer_id=str(customer_id), error=str(exc))
                else:
                    sent_count += 1
                    now = datetime.now(timezone.utc)
                    if existing:
                        existing.delivery_status, existing.platform_message_id, existing.sent_at = "SENT", result.wamid, now
                    else:
                        session.add(BroadcastDelivery(
                            delivery_id=uuid.uuid4(), tenant_id=event.tenant_id, campaign_id=event.campaign_id,
                            customer_id=customer_id, delivery_status="SENT", platform_message_id=result.wamid, sent_at=now))

            await asyncio.sleep(delay)

        async with get_tenant_session(event.tenant_id) as session:
            campaign = await session.scalar(
                select(BroadcastCampaign).where(BroadcastCampaign.campaign_id == event.campaign_id)
            )
            campaign.status = BroadcastCampaignStatus.COMPLETED if sent_count else BroadcastCampaignStatus.FAILED
            campaign.completed_at = datetime.now(timezone.utc)
            campaign.recipients_count = len(recipient_ids) - skipped_count
            if failed_count and not sent_count:
                campaign.last_error = last_error
            elif failed_count:
                campaign.last_error = f"فشل {failed_count} من {sent_count + failed_count}: {last_error}"

        log.info("broadcast_worker_campaign_completed", sent_count=sent_count, failed_count=failed_count, skipped_count=skipped_count)


async def _fail_in(session, campaign: BroadcastCampaign, reason: str) -> None:
    campaign.status = BroadcastCampaignStatus.FAILED
    campaign.completed_at = datetime.now(timezone.utc)
    campaign.last_error = reason[:1000]


async def _main() -> None:
    worker = BroadcastWorker()
    await worker.run()


def run() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    run()

"""
ai_workers/vcard_gatekeeper/worker.py — VCard Gatekeeper Worker

Consumes from `vcard.gatekeeper.v1`. Enforces the conversational gate
requiring users to save the VCard contact before allowing access to AI features.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import structlog
from aiokafka.structs import ConsumerRecord

from src.ai_workers.semantic_router.worker import RoutingDecision
from src.ai_workers.llm_invoker.worker import OutboundMessage
from src.shared.core.config import get_settings
from src.shared.core.enums import CustomerVCardState
from src.shared.kafka.consumer import BaseKafkaConsumer
from src.shared.kafka.producer import KafkaProducerManager
from src.shared.redis_client.client import redis_mgr
from src.shared.tasks.vcard_tasks import schedule_vcard_reminder

logger = structlog.get_logger(__name__)
settings = get_settings()


class VCardGatekeeperWorker(BaseKafkaConsumer):
    def __init__(self) -> None:
        super().__init__(
            topics=[settings.kafka_topic_vcard_gatekeeper],
            group_id=settings.kafka_consumer_group_vcard_gatekeeper,
            max_retries=3,
            retry_backoff_ms=500,
            batch_size=10,
        )
        self._outbound_producer = KafkaProducerManager()

    async def on_startup(self) -> None:
        await redis_mgr.start()
        await self._outbound_producer.start()
        logger.info(
            "vcard_gatekeeper_worker_ready",
            input_topic=settings.kafka_topic_vcard_gatekeeper,
            output_topic=settings.kafka_topic_messages_outgoing,
        )

    async def on_shutdown(self) -> None:
        await self._outbound_producer.stop()
        await redis_mgr.stop()
        logger.info("vcard_gatekeeper_worker_shutdown")

    async def process_message(self, record: ConsumerRecord) -> None:
        start_ns = asyncio.get_event_loop().time()
        raw = record.value
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")

        try:
            decision = RoutingDecision.model_validate_json(raw)
        except Exception as exc:
            raise ValueError(f"Cannot deserialize RoutingDecision: {exc}") from exc

        event = decision.event
        tenant_id = decision.tenant_id
        customer_phone = event.customer_phone or event.platform_user_id
        
        session = decision.session_state or {}
        current_state = event.vcard_state or session.get("vcard_state", CustomerVCardState.NEW)

        log = logger.bind(
            event_id=str(event.event_id),
            tenant_id=str(tenant_id),
            phone=customer_phone,
            current_state=current_state,
        )

        next_state = current_state
        response_text = ""

        if decision.route_reason == "vcard_confirmation_received":
            next_state = CustomerVCardState.CONTACT_SAVED_VERIFIED
            response_text = "ممتاز! شكراً لحفظ الرقم 🎉 يسعدنا خدمتك في أي وقت، يمكنك الآن استخدام جميع ميزات المساعد الذكي."
            log.info("vcard_verified_unlocked")
        else:
            if current_state in (CustomerVCardState.NEW, CustomerVCardState.DORMANT):
                next_state = CustomerVCardState.VCARD_SENT
                response_text = (
                    "أهلاً وسهلاً بك 🌹\n"
                    "لضمان وصول جميع تقاريرك العقارية والإشعارات الهامة بشكل صحيح، يرجى حفظ بطاقة الاتصال الخاصة بنا المرفقة.\n"
                    "بعد الحفظ، أرسل كلمة 'تم' أو 'حفظت' لتفعيل كامل مميزات المساعد الذكي."
                )
                log.info("vcard_sent_initial")
                
                # Schedule 1-hour reminder
                schedule_vcard_reminder(
                    tenant_id=str(tenant_id),
                    customer_phone=customer_phone,
                    current_state=next_state,
                    delay_seconds=3600  # 1 hour
                )
            else:
                next_state = CustomerVCardState.AWAITING_VALIDATION
                response_text = "لإكمال طلبك، يرجى حفظ رقمنا في جهات الاتصال وإرسال كلمة 'تم'. 🤝"
                log.info("vcard_awaiting_validation_reminder")

        # Update Redis state
        await redis_mgr.patch_session_state(
            tenant_id,
            customer_phone,
            {"vcard_state": next_state}
        )
        
        # Note: Database sync for vcard_state is assumed to be handled asynchronously or by a separate sync worker.

        latency_ms = int((asyncio.get_event_loop().time() - start_ns) * 1000)
        
        # Publish Outbound Message
        outbound = OutboundMessage(
            tenant_id=tenant_id,
            conversation_id=decision.conversation_id,
            customer_phone=customer_phone,
            platform_conversation_id=event.platform_conversation_id,
            text=response_text,
            message_type="vcard" if next_state == CustomerVCardState.VCARD_SENT else "text",
            source_event_id=event.event_id,
            routing_tier_used="VCARD",
            model_used="deterministic",
            latency_ms=latency_ms,
        )

        await self._outbound_producer.publish(
            topic=settings.kafka_topic_messages_outgoing,
            event=outbound,
            key=OutboundMessage.kafka_key(tenant_id, customer_phone),
            headers={
                "tenant_id": str(tenant_id),
                "channel": str(event.channel),
                "tier": "VCARD",
                "message_type": outbound.message_type,
            },
        )

async def _main() -> None:
    worker = VCardGatekeeperWorker()
    await worker.run()

def run() -> None:
    asyncio.run(_main())

if __name__ == "__main__":
    run()

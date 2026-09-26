"""
ai_workers/vault_worker/worker.py — Vault Retrieval Worker

Consumes from `vault.retrieval.v1` to fetch reports for customers.
Generates pre-signed S3 URLs and dispatches them via messages.outgoing.v1.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import structlog
from aiokafka.structs import ConsumerRecord
from sqlalchemy import select

from src.ai_workers.semantic_router.worker import RoutingDecision
from src.shared.events.outbound import OutboundMessage
from src.shared.core.config import get_settings
from src.shared.db.models import CustomerReport
from src.shared.db.session import get_tenant_session
from src.shared.kafka.consumer import BaseKafkaConsumer
from src.shared.kafka.producer import KafkaProducerManager
from src.shared.services.report_download import report_download_url

logger = structlog.get_logger(__name__)
settings = get_settings()


class VaultWorker(BaseKafkaConsumer):
    def __init__(self) -> None:
        super().__init__(
            topics=[settings.kafka_topic_vault_retrieval],
            group_id=settings.kafka_consumer_group_vault_workers,
            max_retries=3,
            retry_backoff_ms=500,
            batch_size=10,
        )
        self._outbound_producer = KafkaProducerManager()

    async def on_startup(self) -> None:
        await self._outbound_producer.start()
        logger.info(
            "vault_worker_ready",
            input_topic=settings.kafka_topic_vault_retrieval,
            output_topic=settings.kafka_topic_messages_outgoing,
        )

    async def on_shutdown(self) -> None:
        await self._outbound_producer.stop()
        logger.info("vault_worker_shutdown")

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
        customer_id = decision.customer_id
        
        log = logger.bind(
            event_id=str(event.event_id),
            tenant_id=str(tenant_id),
            customer_id=str(customer_id) if customer_id else None,
        )

        if not customer_id:
            log.warning("vault_worker_missing_customer_id")
            await self._send_fallback_message(decision, start_ns, "عذراً، لم أتمكن من التعرف على حسابك. يرجى التحدث مع موظف خدمة العملاء.")
            return

        # Query Database
        async with get_tenant_session(tenant_id) as session:
            stmt = select(CustomerReport).where(CustomerReport.customer_id == customer_id)
            result = await session.execute(stmt)
            reports = result.scalars().all()

        if not reports:
            await self._send_fallback_message(decision, start_ns, "لم أجد أي تقارير أو ملفات مسجلة في خزانتك الرقمية.")
            return

        # Generate pre-signed URLs
        lines = ["هذه هي التقارير والملفات الموجودة في خزانتك الرقمية:"]
        for report in reports:
            download_url = await report_download_url(report)
            if not download_url:
                continue
            lines.append(f"\n📄 {report.report_type.upper()}:")
            lines.append(f"تنزيل الملف (الرابط صالح لمدة 15 دقيقة): {download_url}")

        if len(lines) == 1:
            await self._send_fallback_message(decision, start_ns, "ملفات تقاريرك غير جاهزة للتنزيل حاليًا. يرجى التواصل مع فريق الدعم.")
            return

        response_text = "\n".join(lines)

        latency_ms = int((asyncio.get_event_loop().time() - start_ns) * 1000)
        await self._publish_outbound(decision, response_text, latency_ms)

        log.info("vault_worker_processed", reports_count=len(reports), latency_ms=latency_ms)

    async def _send_fallback_message(self, decision: RoutingDecision, start_ns: float, message: str) -> None:
        latency_ms = int((asyncio.get_event_loop().time() - start_ns) * 1000)
        await self._publish_outbound(decision, message, latency_ms)

    async def _publish_outbound(self, decision: RoutingDecision, text: str, latency_ms: int) -> None:
        outbound = OutboundMessage(
            tenant_id=decision.tenant_id,
            conversation_id=decision.conversation_id,
            customer_phone=decision.event.customer_phone or decision.event.platform_user_id,
            channel=str(decision.event.channel),
            platform_conversation_id=decision.event.platform_conversation_id,
            reply_target_type=decision.event.reply_target_type,
            text=text,
            message_type="text",
            source_event_id=decision.event.event_id,
            routing_tier_used="VAULT",
            model_used="deterministic",
            latency_ms=latency_ms,
        )

        await self._outbound_producer.publish(
            topic=settings.kafka_topic_messages_outgoing,
            event=outbound,
            key=OutboundMessage.kafka_key(decision.tenant_id, outbound.customer_phone),
        )

async def _main() -> None:
    worker = VaultWorker()
    await worker.run()

def run() -> None:
    asyncio.run(_main())

if __name__ == "__main__":
    run()

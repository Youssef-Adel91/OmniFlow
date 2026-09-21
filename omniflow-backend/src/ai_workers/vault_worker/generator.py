"""
ai_workers/vault_worker/generator.py — Vault Report Generator Worker

Consumes from `payment.events.v1` to generate PDF reports upon successful payment,
uploads them to MinIO/S3, and stores the record in PostgreSQL.
"""
from __future__ import annotations

import asyncio
import uuid
import hashlib
from datetime import datetime, timezone

import structlog
from aiokafka.structs import ConsumerRecord
from pydantic import BaseModel
from sqlalchemy import insert, text

from src.shared.core.config import get_settings
from src.shared.db.models import CustomerReport
from src.shared.db.session import get_tenant_session
from src.shared.kafka.consumer import BaseKafkaConsumer
from src.shared.storage.s3 import s3_mgr

logger = structlog.get_logger(__name__)
settings = get_settings()


class PaymentEvent(BaseModel):
    """Event published when a payment is successful."""
    transaction_id: uuid.UUID
    tenant_id: uuid.UUID
    customer_id: uuid.UUID
    transaction_type: str
    amount: float
    status: str
    metadata: dict = {}


class VaultGeneratorWorker(BaseKafkaConsumer):
    def __init__(self) -> None:
        super().__init__(
            topics=[settings.kafka_topic_payment_events],
            group_id=settings.kafka_consumer_group_vault_workers + "_generator",
            max_retries=3,
            retry_backoff_ms=1000,
            batch_size=5,
        )

    async def on_startup(self) -> None:
        logger.info(
            "vault_generator_worker_ready",
            input_topic=settings.kafka_topic_payment_events,
        )

    async def on_shutdown(self) -> None:
        logger.info("vault_generator_worker_shutdown")

    async def process_message(self, record: ConsumerRecord) -> None:
        raw = record.value
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")

        try:
            event = PaymentEvent.model_validate_json(raw)
        except Exception as exc:
            raise ValueError(f"Cannot deserialize PaymentEvent: {exc}") from exc

        if event.status != "completed" or event.transaction_type != "report_fee":
            # Skip non-report or incomplete payments
            return

        log = logger.bind(
            transaction_id=str(event.transaction_id),
            tenant_id=str(event.tenant_id),
            customer_id=str(event.customer_id),
        )
        log.info("vault_generator_processing_payment")

        report_type = event.metadata.get("report_type", "deed_check_29")
        report_title = event.metadata.get("report_title", f"تقرير {report_type}")

        # 1. Generate PDF (Mock)
        pdf_content = f"OmniFlow AI Report: {report_title}\nGenerated on {datetime.now(timezone.utc)}".encode("utf-8")
        file_hash = hashlib.sha256(pdf_content).hexdigest()
        file_size = len(pdf_content)

        # 2. Upload to MinIO/S3
        bucket = settings.s3_vault_bucket
        key = f"tenant-{event.tenant_id}/reports/{event.customer_id}/{uuid.uuid4()}.pdf"
        
        try:
            s3_url = await s3_mgr.upload_file(
                bucket=bucket,
                key=key,
                file_data=pdf_content,
                content_type="application/pdf"
            )
        except Exception as e:
            log.error("s3_upload_failed", error=str(e))
            raise e

        # 3. Store in PostgreSQL
        async with get_tenant_session(event.tenant_id) as session:
            stmt = insert(CustomerReport).values(
                tenant_id=event.tenant_id,
                customer_id=event.customer_id,
                report_type=report_type,
                s3_url=s3_url,
                price_sar=event.amount,
                payment_reference=str(event.transaction_id),
                is_delivered=True,
                # Additional fields from the SQL schema that aren't mapped strictly in the stub model but we'll try to map:
            )
            # The CustomerReport ORM model handles report_id automatically
            await session.execute(stmt)
            
            # Since the ORM model CustomerReport doesn't have all columns from SQL defined in models.py (from what we saw),
            # let's fallback to raw SQL if needed, but SQLAlchemy insert will work with mapped columns.
            # We'll also update the native SQL table customer_reports via text if needed to fill the rest of the columns.
            # For now, we commit the ORM insert.
            await session.commit()

        log.info("vault_generator_report_created", s3_url=s3_url)


async def _main() -> None:
    worker = VaultGeneratorWorker()
    await worker.run()

def run() -> None:
    asyncio.run(_main())

if __name__ == "__main__":
    run()

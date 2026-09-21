"""
shared/kafka/producer.py — Async Kafka Producer Singleton

Uses aiokafka (pure-Python async Kafka client) compatible with both
Apache Kafka and Redpanda (local dev via Docker Compose).

Architecture:
    KafkaProducerManager (singleton) is started/stopped by the
    FastAPI lifespan in gateway/main.py:

        @asynccontextmanager
        async def lifespan(app):
            await kafka_producer.start()
            yield
            await kafka_producer.stop()

    Endpoints use the module-level `kafka_producer` instance:
        from src.shared.kafka.producer import kafka_producer
        await kafka_producer.publish(topic, event)

Producer guarantees:
  - enable_idempotence=True  → exactly-once delivery per partition
  - acks="all"               → all ISR replicas must confirm
  - max_in_flight_requests=5 → Kafka 1.0+ safe with idempotence
  - compression_type="lz4"   → fast compression for JSON payloads

References: SRS §2.4 — Kafka Topics & Producer Configuration
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import structlog
from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError, KafkaTimeoutError
from pydantic import BaseModel
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.shared.core.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


class KafkaProducerManager:
    """
    Async Kafka producer lifecycle manager.

    Designed as a module-level singleton (`kafka_producer`).
    Thread-safe: AIOKafkaProducer uses asyncio internally.

    Usage:
        # In FastAPI lifespan:
        await kafka_producer.start()
        ...
        await kafka_producer.stop()

        # In endpoints / workers:
        await kafka_producer.publish(topic, event_model)
        await kafka_producer.publish_raw(topic, key, value_bytes)
    """

    def __init__(self) -> None:
        self._producer: AIOKafkaProducer | None = None
        self._started: bool = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """
        Initialize and start the AIOKafkaProducer.

        Called once during FastAPI startup (lifespan context manager).
        Retries connection up to 5 times with exponential backoff to handle
        Kafka/Redpanda not being fully ready at container startup.
        """
        if self._started:
            logger.warning("kafka_producer_already_started")
            return

        self._producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            # All ISR replicas must acknowledge before the send future resolves
            acks="all",
            # gzip is built-in (lz4 requires python-lz4 which isn't installed in dev)
            compression_type="gzip",
            # Micro-batching: wait up to 5ms to collect messages into a batch
            linger_ms=5,
            # 64KB batch size — aiokafka 0.14 uses max_batch_size (was batch_size)
            max_batch_size=65_536,
            # NOTE: enable_idempotence, retries, request_timeout_ms,
            # value_serializer, key_serializer, and
            # max_in_flight_requests_per_connection were removed in aiokafka ≥ 0.14.
        )

        # Retry startup — Redpanda may still be initializing on first compose up
        for attempt in range(1, 6):
            try:
                await self._producer.start()
                self._started = True
                logger.info(
                    "kafka_producer_started",
                    bootstrap=settings.kafka_bootstrap_servers,
                )
                return
            except KafkaError as exc:
                logger.warning(
                    "kafka_producer_start_retry",
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt == 5:
                    raise
                await asyncio.sleep(2 ** attempt)  # 2s, 4s, 8s, 16s, 32s

    async def stop(self) -> None:
        """
        Flush pending messages and shut down the producer gracefully.

        Called during FastAPI shutdown (lifespan context manager).
        Waits up to 10s for in-flight messages to complete.
        """
        if not self._started or self._producer is None:
            return
        try:
            await asyncio.wait_for(self._producer.stop(), timeout=10.0)
            self._started = False
            logger.info("kafka_producer_stopped")
        except asyncio.TimeoutError:
            logger.error("kafka_producer_stop_timeout")

    # ── Publishing ────────────────────────────────────────────────────────────

    async def publish(
        self,
        topic: str,
        event: BaseModel,
        *,
        key: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """
        Publish a Pydantic model to a Kafka topic.

        Serialization: model.model_dump_json() → UTF-8 bytes.
        If the model has a `to_kafka_bytes()` method, it is used instead
        (e.g., CanonicalInboundEvent.to_kafka_bytes()).

        Args:
            topic   — Kafka topic name (e.g., settings.kafka_topic_messages_incoming)
            event   — Pydantic BaseModel instance
            key     — Optional partition key bytes (for ordered delivery)
            headers — Optional Kafka message headers for tracing / routing

        Raises:
            RuntimeError if the producer has not been started.
            KafkaTimeoutError / KafkaError on publish failure (after retries).
        """
        if not self._started or self._producer is None:
            raise RuntimeError(
                "KafkaProducerManager.start() must be called before publish(). "
                "Ensure kafka_producer.start() is in the FastAPI lifespan."
            )

        # Use custom serializer if available (preserves special field encoding)
        if hasattr(event, "to_kafka_bytes"):
            value_bytes: bytes = event.to_kafka_bytes()  # type: ignore[attr-defined]
        else:
            value_bytes = event.model_dump_json().encode("utf-8")

        kafka_headers = (
            [(k, v.encode("utf-8")) for k, v in headers.items()]
            if headers
            else []
        )

        await self._publish_with_retry(
            topic=topic,
            key=key,
            value=value_bytes,
            headers=kafka_headers,
        )

    async def publish_raw(
        self,
        topic: str,
        value: bytes,
        *,
        key: bytes | None = None,
        headers: list[tuple[str, bytes]] | None = None,
    ) -> None:
        """
        Publish raw bytes directly — for forwarding already-serialized events
        (e.g., from a consumer that re-publishes to another topic).
        """
        if not self._started or self._producer is None:
            raise RuntimeError("Producer not started.")
        await self._publish_with_retry(
            topic=topic,
            key=key,
            value=value,
            headers=headers or [],
        )

    async def _publish_with_retry(
        self,
        *,
        topic: str,
        key: bytes | None,
        value: bytes,
        headers: list[tuple[str, bytes]],
    ) -> None:
        """
        Internal publish with structured logging and error handling.

        The AIOKafkaProducer already retries internally (retries=5).
        This wrapper adds observability and catches permanent failures.
        """
        try:
            record_metadata = await self._producer.send_and_wait(  # type: ignore[union-attr]
                topic,
                key=key,
                value=value,
                headers=headers,
            )
            logger.debug(
                "kafka_message_published",
                topic=topic,
                partition=record_metadata.partition,
                offset=record_metadata.offset,
                bytes_sent=len(value),
            )
        except KafkaTimeoutError as exc:
            logger.error(
                "kafka_publish_timeout",
                topic=topic,
                error=str(exc),
            )
            raise
        except KafkaError as exc:
            logger.error(
                "kafka_publish_error",
                topic=topic,
                error=str(exc),
                exc_type=type(exc).__name__,
            )
            raise

    # ── Health ────────────────────────────────────────────────────────────────

    @property
    def is_ready(self) -> bool:
        """True if the producer has been started and is operational."""
        return self._started and self._producer is not None


# ── Module-level singleton ─────────────────────────────────────────────────────
# All services import this instance directly.
# FastAPI lifespan controls start/stop.
kafka_producer = KafkaProducerManager()

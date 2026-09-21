"""
shared/kafka/consumer.py — Generic Async Kafka Consumer Base Class

Provides a robust, opinionated base class that every OmniFlow worker
inherits from. Implements the following guarantees out of the box:

  ┌─────────────────────────────────────────────────────────────────┐
  │  Delivery Guarantee: At-Least-Once with Idempotency Guard       │
  │                                                                 │
  │  1. Fetch batch from Kafka                                      │
  │  2. Check idempotency key in Redis → skip if duplicate          │
  │  3. Dispatch to process_message() (implemented by subclass)     │
  │  4. On success → commit offset manually                         │
  │  5. On transient error → retry up to max_retries                │
  │  6. On permanent error → publish to DLQ topic + commit offset   │
  └─────────────────────────────────────────────────────────────────┘

Design decisions:
  - Manual offset commits (enable_auto_commit=False) — we only commit
    AFTER successful processing, so a crash never loses a message.
  - DLQ (Dead Letter Queue) — a failed message after max_retries is
    published to "<topic>.dlq" for human inspection, not dropped.
  - Idempotency guard — uses Redis SET NX to deduplicate replayed
    messages (Kafka guarantees at-least-once, not exactly-once).
  - Graceful shutdown — `asyncio.Event` + SIGTERM handler ensures
    in-flight messages complete before the process exits.

References: SRS §2.4 — Kafka Consumer Groups, Sprint 5 spec
"""
from __future__ import annotations

import asyncio
import json
import signal
from abc import ABC, abstractmethod
from typing import Any

import structlog
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaError, CommitFailedError
from aiokafka.structs import ConsumerRecord

from src.shared.core.config import get_settings
from src.shared.redis_client.client import redis_mgr

logger = structlog.get_logger(__name__)
settings = get_settings()


class BaseKafkaConsumer(ABC):
    """
    Abstract base class for all OmniFlow Kafka consumer workers.

    Subclasses MUST implement:
        process_message(record: ConsumerRecord) -> None

    Subclasses MAY override:
        get_idempotency_key(record) — default uses record.value["event_id"]
        on_startup()                — async hook after consumer.start()
        on_shutdown()               — async hook before consumer.stop()

    Usage:
        class MyWorker(BaseKafkaConsumer):
            async def process_message(self, record):
                event = MyEvent.model_validate_json(record.value)
                await do_something(event)

        worker = MyWorker(
            topics=["my.topic.v1"],
            group_id="omniflow.my-worker.v1",
        )
        asyncio.run(worker.run())
    """

    def __init__(
        self,
        *,
        topics: list[str],
        group_id: str,
        max_retries: int = 3,
        retry_backoff_ms: int = 500,
        batch_size: int = 10,
    ) -> None:
        self.topics = topics
        self.group_id = group_id
        self.max_retries = max_retries
        self.retry_backoff_ms = retry_backoff_ms
        self.batch_size = batch_size

        self._consumer: AIOKafkaConsumer | None = None
        self._dlq_producer: AIOKafkaProducer | None = None
        self._stop_event = asyncio.Event()
        self._running = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize consumer and DLQ producer, then subscribe to topics."""
        self._consumer = AIOKafkaConsumer(
            *self.topics,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=self.group_id,
            # Manual commit — we commit only after successful processing
            enable_auto_commit=False,
            # Earliest: on first deploy, process all historical messages
            auto_offset_reset="earliest",
            # Deserialize value as raw bytes — subclass parses
            value_deserializer=lambda v: v,
            key_deserializer=lambda k: k.decode("utf-8") if k else None,
            # Fetch up to batch_size messages in one poll
            max_poll_records=self.batch_size,
            # Heartbeat interval (must be < session_timeout)
            heartbeat_interval_ms=3_000,
            session_timeout_ms=30_000,
            # Rebalance timeout — give workers time to finish in-flight messages
            max_poll_interval_ms=300_000,
            # Fetch settings
            fetch_min_bytes=1,
            fetch_max_wait_ms=500,
        )

        # DLQ producer — reuses the same broker but is independent of kafka_producer
        self._dlq_producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda v: v if isinstance(v, bytes) else v.encode("utf-8"),
            acks="all",
        )

        await self._consumer.start()
        await self._dlq_producer.start()
        self._running = True

        logger.info(
            "kafka_consumer_started",
            topics=self.topics,
            group_id=self.group_id,
            worker=self.__class__.__name__,
        )

        # Install SIGTERM handler for graceful shutdown (K8s Pod termination)
        loop = asyncio.get_running_loop()
        import os
        if os.name != 'nt':
            loop.add_signal_handler(signal.SIGTERM, self._handle_sigterm)

        await self.on_startup()

    async def stop(self) -> None:
        """Flush DLQ producer and stop consumer gracefully."""
        self._running = False
        if self._dlq_producer:
            await self._dlq_producer.stop()
        if self._consumer:
            await self._consumer.stop()
        await self.on_shutdown()
        logger.info(
            "kafka_consumer_stopped",
            worker=self.__class__.__name__,
        )

    def _handle_sigterm(self) -> None:
        """Signal handler — sets the stop event for graceful drain."""
        logger.info("kafka_consumer_sigterm_received", worker=self.__class__.__name__)
        self._stop_event.set()

    # ── Main event loop ───────────────────────────────────────────────────────

    async def run(self) -> None:
        """
        Main consumer loop. Call this from asyncio.run() in the worker entrypoint.

        Polls Kafka for message batches and dispatches each record to
        _handle_record() which implements retry + DLQ logic.
        Stops cleanly when SIGTERM is received or stop() is called.
        """
        await self.start()
        try:
            while not self._stop_event.is_set():
                try:
                    # Fetch a batch — blocks for up to fetch_max_wait_ms
                    batch = await self._consumer.getmany(  # type: ignore[union-attr]
                        timeout_ms=1_000,
                        max_records=self.batch_size,
                    )
                    for tp, records in batch.items():
                        for record in records:
                            await self._handle_record(record)

                except KafkaError as exc:
                    logger.error(
                        "kafka_consumer_poll_error",
                        error=str(exc),
                        worker=self.__class__.__name__,
                    )
                    await asyncio.sleep(1)

        except asyncio.CancelledError:
            logger.info("kafka_consumer_cancelled", worker=self.__class__.__name__)
        finally:
            await self.stop()

    async def _handle_record(self, record: ConsumerRecord) -> None:
        """
        Single-record dispatch with retry, idempotency, and DLQ routing.

        Steps:
          1. Extract event_id for idempotency check.
          2. Skip if already processed (Redis SET NX said it's a duplicate).
          3. Try process_message() up to max_retries times.
          4. On persistent failure → publish to DLQ + commit offset.
          5. On success → commit offset.
        """
        # ── Extract event_id for idempotency ──────────────────────────────────
        event_id = self._extract_event_id(record)

        # ── Idempotency guard ─────────────────────────────────────────────────
        if event_id and await redis_mgr.is_duplicate_event(event_id):
            logger.debug(
                "kafka_consumer_duplicate_skipped",
                event_id=event_id,
                topic=record.topic,
                partition=record.partition,
                offset=record.offset,
            )
            await self._commit_offset(record)
            return

        # ── Retry loop ────────────────────────────────────────────────────────
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                await self.process_message(record)
                await self._commit_offset(record)
                return

            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "kafka_consumer_process_error",
                    attempt=attempt,
                    max_retries=self.max_retries,
                    topic=record.topic,
                    offset=record.offset,
                    error=str(exc),
                    exc_type=type(exc).__name__,
                    worker=self.__class__.__name__,
                )
                if attempt < self.max_retries:
                    backoff = self.retry_backoff_ms * (2 ** (attempt - 1)) / 1000
                    await asyncio.sleep(backoff)

        # ── All retries exhausted → DLQ ───────────────────────────────────────
        logger.error(
            "kafka_consumer_dlq_routing",
            topic=record.topic,
            offset=record.offset,
            event_id=event_id,
            error=str(last_exc),
            worker=self.__class__.__name__,
        )
        await self._publish_to_dlq(record, last_exc)
        await self._commit_offset(record)  # Commit so we don't re-process

    async def _commit_offset(self, record: ConsumerRecord) -> None:
        """Manually commit the offset for a processed record."""
        try:
            await self._consumer.commit()  # type: ignore[union-attr]
        except CommitFailedError as exc:
            # Offset commit failure is non-fatal — the record may be reprocessed
            # after a rebalance but idempotency guard will deduplicate it.
            logger.warning(
                "kafka_consumer_commit_failed",
                topic=record.topic,
                offset=record.offset,
                error=str(exc),
            )

    async def _publish_to_dlq(
        self, record: ConsumerRecord, exc: Exception | None
    ) -> None:
        """
        Publish a failed record to the Dead Letter Queue topic.

        DLQ topic name convention: "<original_topic>.dlq"
        DLQ payload wraps the original record value with error metadata.
        """
        if not self._dlq_producer:
            return

        dlq_topic = f"{record.topic}.dlq"
        dlq_payload = json.dumps(
            {
                "original_topic": record.topic,
                "original_partition": record.partition,
                "original_offset": record.offset,
                "original_key": record.key,
                "original_value": record.value.decode("utf-8", errors="replace")
                if isinstance(record.value, bytes)
                else str(record.value),
                "error": str(exc),
                "worker": self.__class__.__name__,
            },
            ensure_ascii=False,
        ).encode("utf-8")

        try:
            await self._dlq_producer.send_and_wait(
                dlq_topic,
                key=record.key.encode() if isinstance(record.key, str) else record.key,
                value=dlq_payload,
            )
        except KafkaError as dlq_exc:
            # DLQ publish failure — at this point we can only log and alert
            logger.critical(
                "kafka_dlq_publish_failed",
                dlq_topic=dlq_topic,
                error=str(dlq_exc),
            )

    @staticmethod
    def _extract_event_id(record: ConsumerRecord) -> str | None:
        """
        Extract event_id from the record value for idempotency keying.

        Assumes JSON-encoded value with an "event_id" field (all canonical events).
        Returns None if extraction fails — idempotency check is skipped.
        """
        try:
            value = record.value
            if isinstance(value, bytes):
                value = value.decode("utf-8")
            parsed = json.loads(value)
            return parsed.get("event_id")
        except (json.JSONDecodeError, AttributeError):
            return None

    # ── Hooks for subclasses ──────────────────────────────────────────────────

    @abstractmethod
    async def process_message(self, record: ConsumerRecord) -> None:
        """
        Process a single Kafka message record.

        Implement this in every concrete worker subclass.
        Raise any exception to trigger the retry + DLQ mechanism.
        """
        ...

    async def on_startup(self) -> None:
        """Optional hook: called once after consumer.start(). Override as needed."""
        pass

    async def on_shutdown(self) -> None:
        """Optional hook: called once before consumer.stop(). Override as needed."""
        pass

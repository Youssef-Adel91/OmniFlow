"""Consumer-group lag and dead-letter counts, for the /health/lag warning endpoint (informational; never part of readiness).

Pure maths (`lag_of`, `level_of`) is separated from the broker call (`collect`) so it can be unit-tested without Kafka.
"""
from __future__ import annotations

import asyncio
from typing import Iterable, Mapping

from src.shared.core.config import Settings

WARN_LAG = 100
CRITICAL_LAG = 1000


def watched(cfg: Settings) -> dict[str, list[str]]:
    """consumer group → topics it reads (mirrors the workers' own subscriptions)."""
    return {
        cfg.kafka_consumer_group_ai_workers: [cfg.kafka_topic_messages_incoming, cfg.kafka_topic_llm_routing],
        cfg.kafka_consumer_group_notification_svc: [cfg.kafka_topic_messages_outgoing],
        cfg.kafka_consumer_group_broadcast_workers: [cfg.kafka_topic_broadcast_marketing],
        cfg.kafka_consumer_group_vault_workers: [cfg.kafka_topic_vault_retrieval],
        cfg.kafka_consumer_group_vcard_gatekeeper: [cfg.kafka_topic_vcard_gatekeeper],
    }


def lag_of(end: Mapping[int, int], committed: Mapping[int, int | None]) -> int:
    """Total messages written but not yet committed. A partition with no commit yet counts everything up to its end."""
    return sum(max(0, e - (committed.get(p) or 0)) for p, e in end.items())


def level_of(lag: int) -> str:
    return "critical" if lag >= CRITICAL_LAG else "warn" if lag >= WARN_LAG else "ok"


def worst(levels: Iterable[str]) -> str:
    order = {"ok": 0, "warn": 1, "critical": 2}
    return max(levels, key=lambda lv: order[lv], default="ok")


async def collect(bootstrap: str, groups: Mapping[str, list[str]], *, timeout: float = 4.0) -> dict:
    """Ask the broker. Raises on any broker problem (the caller reports 'unavailable' without details)."""
    from aiokafka import AIOKafkaConsumer, TopicPartition

    async def _run() -> dict:
        out: dict = {"groups": {}, "dead_letters": {}}
        probe = AIOKafkaConsumer(bootstrap_servers=bootstrap, enable_auto_commit=False)
        await probe.start()
        try:
            for group, topics in groups.items():
                consumer = AIOKafkaConsumer(bootstrap_servers=bootstrap, group_id=group, enable_auto_commit=False)
                await consumer.start()
                try:
                    per_topic = {}
                    for topic in topics:
                        parts = consumer.partitions_for_topic(topic) or set()
                        tps = [TopicPartition(topic, p) for p in parts]
                        end = await consumer.end_offsets(tps) if tps else {}
                        committed = {tp.partition: await consumer.committed(tp) for tp in tps}
                        lag = lag_of({tp.partition: off for tp, off in end.items()}, committed)
                        per_topic[topic] = {"lag": lag, "level": level_of(lag)}
                    out["groups"][group] = per_topic
                finally:
                    await consumer.stop()
            for topics in groups.values():
                for topic in topics:
                    dlq = f"{topic}.dlq"
                    parts = probe.partitions_for_topic(dlq) or set()
                    tps = [TopicPartition(dlq, p) for p in parts]
                    if tps:
                        ends, begins = await probe.end_offsets(tps), await probe.beginning_offsets(tps)
                        out["dead_letters"][dlq] = sum(ends[tp] - begins[tp] for tp in tps)
        finally:
            await probe.stop()
        return out

    return await asyncio.wait_for(_run(), timeout=timeout)

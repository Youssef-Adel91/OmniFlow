"""Exercise real Kafka/Redis with private test topics; never consume product topics."""
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiokafka import AIOKafkaConsumer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.structs import TopicPartition
from src.shared.core.config import get_settings
from src.shared.kafka.consumer import BaseKafkaConsumer
from src.shared.kafka.producer import KafkaProducerManager
from src.shared.redis_client.client import redis_mgr


async def main():
    settings = get_settings()
    servers = settings.kafka_bootstrap_servers
    hosts = servers.split(",") if isinstance(servers, str) else servers
    if any(address.split(":")[0] not in {"localhost", "127.0.0.1"} for address in hosts):
        raise RuntimeError("Transport validation requires local Kafka")
    if settings.redis_host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("Transport validation requires local Redis")
    topic = "validation.inbox." + uuid.uuid4().hex
    group = topic + ".consumer"
    topics = [topic, topic + ".dlq"]
    admin = AIOKafkaAdminClient(bootstrap_servers=servers, request_timeout_ms=10000)
    producer = KafkaProducerManager()
    created = False
    records = []

    class Worker(BaseKafkaConsumer):
        def __init__(self):
            super().__init__(topics=[topic], group_id=group, max_retries=2, retry_backoff_ms=0)
            self.attempts = {}

        async def process_message(self, record):
            payload = json.loads(record.value)
            identity = payload["event_id"]
            self.attempts[identity] = self.attempts.get(identity, 0) + 1
            if identity == "retry" and self.attempts[identity] == 1:
                raise RuntimeError("Synthetic transient failure")
            if identity == "dlq":
                raise ValueError("Synthetic permanent failure")

    worker = Worker()
    dlq = AIOKafkaConsumer(topics[1], bootstrap_servers=servers, auto_offset_reset="earliest",
                           enable_auto_commit=False, request_timeout_ms=10000)
    try:
        await admin.start()
        await admin.create_topics([NewTopic(name, num_partitions=1, replication_factor=1) for name in topics])
        created = True
        await redis_mgr.start()
        await producer.start()
        await worker.start()
        await dlq.start()
        for identity in ("retry", "dlq"):
            await producer.publish_raw(topic, json.dumps({"event_id": identity}).encode(), key=b"synthetic")
        for _ in range(2):
            record = await asyncio.wait_for(worker._consumer.getone(), timeout=20)
            records.append(record)
            await worker._handle_record(record)
        assert worker.attempts == {"retry": 2, "dlq": 2}
        replay = records[0]
        await worker._handle_record(replay)
        assert worker.attempts["retry"] == 2, "Redis completion marker failed to suppress replay"
        dead = json.loads((await asyncio.wait_for(dlq.getone(), timeout=20)).value)
        assert json.loads(dead["original_value"])["event_id"] == "dlq"
        assert dead["original_key"] == "synthetic"
        # Replay above explicitly acknowledges its own offset; complete the later record again.
        await worker._commit_offset(records[-1])
        committed = await worker._consumer.committed(TopicPartition(topic, 0))
        assert committed == records[-1].offset + 1
        print("PASS real Kafka/Redis: publish, transient retry, completion deduplication, DLQ, explicit offset commit")
    finally:
        await dlq.stop()
        await worker.stop()
        await producer.stop()
        if records:
            redis = redis_mgr._cache
            await redis.delete(*[f"processed:{group}:{r.topic}:{r.partition}:{r.offset}" for r in records])
        await redis_mgr.stop()
        if created:
            await admin.delete_topics(topics)
        await admin.close()


if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(main(), timeout=90))
    except Exception as exc:
        print(f"Transport validation failed: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)

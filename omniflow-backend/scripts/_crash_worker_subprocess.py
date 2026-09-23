"""
Subprocess entrypoint spawned by validate_kafka_outbound_recovery.py to
simulate a hard process crash mid-message-processing.

Consumes exactly like the real OutboundDispatcherWorker (its actual
process_message runs unmodified), except: after successfully finishing all
real side effects (WhatsApp "send" via a mocked client, Redis idempotency
key, DB delivery_status update) for one specific target message, it touches
a marker file and then blocks forever instead of returning control to
BaseKafkaConsumer. The parent process watches for the marker file, then
kills this process (proc.kill() — SIGKILL on POSIX, TerminateProcess on
Windows) — no graceful shutdown, no chance for the offset-commit line in
_handle_record to run. This is the real "crashed after doing the work but
before committing the Kafka offset" scenario the recovery logic is supposed
to survive.

Usage: python _crash_worker_subprocess.py <group_id> <crash_message_id> <marker_file>
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai_workers.outbound_dispatcher import worker as dispatcher_module
from src.channel_adapters.whatsapp import client as whatsapp_client_module
from src.channel_adapters.whatsapp.client import SendResult
from src.shared.core.config import get_settings
from src.shared.kafka.consumer import BaseKafkaConsumer

settings = get_settings()


async def _fake_send_text_message(*, phone_number_id, to, text, access_token, **kwargs):
    return SendResult(wamid="wamid.crashtest." + uuid.uuid4().hex[:8], phone_number=to, message_status="accepted")


class CrashDispatcher(dispatcher_module.OutboundDispatcherWorker):
    def __init__(self, group_id: str, crash_message_id: str, marker_file: Path) -> None:
        BaseKafkaConsumer.__init__(
            self,
            topics=[settings.kafka_topic_messages_outgoing],
            group_id=group_id,
            max_retries=2,
            retry_backoff_ms=200,
            batch_size=5,
        )
        self._crash_message_id = crash_message_id
        self._marker_file = marker_file

    async def process_message(self, record):
        raw = record.value.decode("utf-8") if isinstance(record.value, bytes) else record.value
        payload = json.loads(raw)

        # Run the REAL, unmodified business logic — WhatsApp send (mocked at
        # the client layer, not here), Redis idempotency key, DB status
        # update all happen for real.
        await super().process_message(record)

        if payload.get("message_id") == self._crash_message_id:
            self._marker_file.write_text("done", encoding="utf-8")
            # Never return — the process gets kill()ed from here. This
            # blocks BEFORE _handle_record's offset-commit line runs.
            await asyncio.Event().wait()


async def _main() -> None:
    group_id, crash_message_id, marker_file = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    whatsapp_client_module.whatsapp_client.send_text_message = _fake_send_text_message
    worker = CrashDispatcher(group_id, crash_message_id, marker_file)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(_main())

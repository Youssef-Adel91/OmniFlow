"""WhatsApp 24-hour customer-service window: pure rule + the dispatcher fails fast (readable reason, no Meta call, no retry) outside it."""
import unittest
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.ai_workers.outbound_dispatcher import worker as dispatcher
from src.channel_adapters.whatsapp import service_window as sw
from src.shared.events.outbound import OutboundMessage

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


class WindowRuleTests(unittest.TestCase):
    def test_boundaries(self):
        self.assertTrue(sw.is_open(NOW - timedelta(hours=23, minutes=59), NOW))
        self.assertFalse(sw.is_open(NOW - timedelta(hours=24), NOW))
        self.assertFalse(sw.is_open(NOW - timedelta(days=3), NOW))
        self.assertTrue(sw.is_open(NOW, NOW))

    def test_unknown_last_inbound_is_allowed_and_naive_dates_are_utc(self):
        self.assertTrue(sw.is_open(None, NOW))
        self.assertTrue(sw.is_open(datetime(2026, 10, 13, 1, 0), NOW))
        self.assertFalse(sw.is_open(datetime(2026, 10, 11, 1, 0), NOW))


class DispatcherWindowTests(unittest.IsolatedAsyncioTestCase):
    async def run_dispatch(self, last_inbound):
        msg = OutboundMessage(
            message_id=uuid.uuid4(), tenant_id=uuid.uuid4(), conversation_id=uuid.uuid4(), customer_phone="966500000000",
            platform_conversation_id="966500000000", text="hello", sender_type="human_agent", source_event_id=uuid.uuid4(),
            routing_tier_used="HUMAN", model_used="human_agent",
        )
        store = {}

        @asynccontextmanager
        async def session(_):
            yield SimpleNamespace(scalar=AsyncMock(return_value=None))

        async def get_raw(key):
            return store.get(key)

        async def set_raw(key, value, **_):
            store[key] = value

        send = AsyncMock(return_value=SimpleNamespace(wamid="wamid.ok"))
        with patch.object(dispatcher, "get_tenant_session", session), \
                patch.object(dispatcher, "_resolve_tenant_credentials", AsyncMock(return_value=SimpleNamespace(phone_number_id="n", access_token="t"))), \
                patch.object(dispatcher, "persist_outbound_message", AsyncMock(return_value=msg.message_id)), \
                patch.object(dispatcher, "update_message_delivery_status", AsyncMock()) as update, \
                patch.object(dispatcher.redis_mgr, "get_raw", get_raw), patch.object(dispatcher.redis_mgr, "set_raw", set_raw), \
                patch.object(sw, "last_inbound_at", AsyncMock(return_value=last_inbound)), \
                patch.object(dispatcher.whatsapp_client, "send_text_message", send):
            rec = SimpleNamespace(value=msg.to_kafka_bytes(), key=b"k", topic="t", partition=0, offset=1)
            await dispatcher.OutboundDispatcherWorker().process_message(rec)
        return send, update

    async def test_closed_window_is_not_sent_and_says_why(self):
        send, update = await self.run_dispatch(datetime.now(timezone.utc) - timedelta(hours=30))
        send.assert_not_awaited()
        self.assertEqual(update.call_args.kwargs["delivery_status"], "FAILED")
        self.assertEqual(update.call_args.kwargs["failure_reason"], sw.FAILURE_REASON)

    async def test_open_window_sends_normally(self):
        send, update = await self.run_dispatch(datetime.now(timezone.utc) - timedelta(hours=2))
        send.assert_awaited_once()
        self.assertEqual(update.call_args.kwargs["delivery_status"], "SENT")


if __name__ == "__main__":
    unittest.main()

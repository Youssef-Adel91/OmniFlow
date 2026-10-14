"""Graceful degradation when a Meta token dies (Graph error 190): the tenant is told (existing), comment automation stops calling Meta with a dead token, and the alarm
clears by itself as soon as a send works again (instead of nagging until someone presses "reconnect")."""
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.channel_adapters.instagram import comment_actions
from src.shared.services import channel_health
from tests.test_comment_replies import IngestionTestCase
from tests.test_comments_v2 import FakeRedis
from tests.test_instagram_webhook import feed_comment_payload, ig_comment_payload


class NoteSuccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis = FakeRedis()
        self.tenant = uuid.uuid4()
        p = patch.object(channel_health, "redis_mgr", self.redis)
        p.start()
        self.addCleanup(p.stop)

    async def test_a_successful_send_clears_a_stale_alarm_and_a_new_failure_notifies_again(self):
        with patch("src.shared.services.notifications.notify_safe", AsyncMock(return_value=1)) as notify, patch("src.shared.db.session.get_tenant_session"):
            await channel_health.mark_auth_failed(self.tenant, "facebook", "expired")
            self.assertIsNotNone(await channel_health.get_failure(self.tenant, "facebook"))
            await channel_health.note_success(self.tenant, "facebook")
            self.assertIsNone(await channel_health.get_failure(self.tenant, "facebook"))
            await channel_health.mark_auth_failed(self.tenant, "facebook", "expired again")
            self.assertEqual(notify.await_count, 2)

    async def test_it_only_touches_the_channel_that_worked_and_never_raises(self):
        await channel_health.mark_auth_failed(self.tenant, "whatsapp", "x")
        await channel_health.note_success(self.tenant, "instagram")
        self.assertIsNotNone(await channel_health.get_failure(self.tenant, "whatsapp"))
        with patch.object(channel_health, "redis_mgr", SimpleNamespace(get_raw=AsyncMock(side_effect=RuntimeError("redis down")))):
            await channel_health.note_success(self.tenant, "whatsapp")                         # must not raise

    def test_channel_values_map_to_the_right_alarm(self):
        self.assertEqual([channel_health.health_channel(c) for c in ("whatsapp", "instagram", "messenger", "telegram", "web")],
                         ["whatsapp", "instagram", "facebook", "telegram", "facebook"])


class CommentAutomationStopsOnADeadTokenTests(IngestionTestCase):
    async def test_comments_on_a_flagged_channel_are_not_answered_but_the_other_channel_still_is(self):
        self.start()
        alarm = AsyncMock(side_effect=lambda tenant, channel: {"at": "x", "reason": "190"} if channel == "facebook" else None)
        with patch.object(channel_health, "get_failure", alarm):
            await self.post(feed_comment_payload(comment_id="123_1"))                          # Facebook: token dead -> skipped
            await self.post(ig_comment_payload(comment_id="1785_1"))                           # Instagram: fine -> answered
        self.assertEqual([e.platform_message_id for e in self.events()], ["1785_1"])

    async def test_the_skip_is_recorded_so_the_tenant_can_see_why(self):
        self.start()
        record = AsyncMock()
        with patch.object(channel_health, "get_failure", AsyncMock(return_value={"at": "x", "reason": "190"})), patch.object(comment_actions, "record", record):
            await self.post(feed_comment_payload(comment_id="123_2"))
        self.assertEqual(self.events(), [])
        self.assertEqual((record.await_args.kwargs["action"], record.await_args.kwargs["reason"]), ("ignored", "channel_needs_reconnect"))

    async def test_a_successful_hide_or_public_reply_clears_the_alarm(self):
        with patch.object(comment_actions, "page_access_token", AsyncMock(return_value="tok")), \
                patch.object(comment_actions.meta_client, "hide_comment", AsyncMock(return_value={})), \
                patch.object(comment_actions.channel_health, "note_success", AsyncMock()) as ok:
            self.assertTrue(await comment_actions.hide_comment_for_tenant(uuid.uuid4(), platform="facebook", comment_id="c1"))
        ok.assert_awaited_once()
        self.assertEqual(ok.await_args.args[1], "facebook")


class DispatcherClearsTheAlarmTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_delivered_whatsapp_message_tells_channel_health_it_worked(self):
        from contextlib import asynccontextmanager

        from src.ai_workers.outbound_dispatcher import worker as dispatcher
        from src.shared.events.outbound import OutboundMessage

        msg = OutboundMessage(message_id=uuid.uuid4(), tenant_id=uuid.uuid4(), conversation_id=uuid.uuid4(), customer_phone="966500000000", platform_conversation_id="966500000000",
                              text="hello", sender_type="human_agent", source_event_id=uuid.uuid4(), routing_tier_used="HUMAN", model_used="human_agent")
        store = {}

        @asynccontextmanager
        async def session(_):
            yield SimpleNamespace(scalar=AsyncMock(return_value=None))

        async def get_raw(k):
            return store.get(k)

        async def set_raw(k, v, **_):
            store[k] = v

        with patch.object(dispatcher, "get_tenant_session", session), \
                patch.object(dispatcher, "_resolve_tenant_credentials", AsyncMock(return_value=SimpleNamespace(phone_number_id="n", access_token="t"))), \
                patch.object(dispatcher, "persist_outbound_message", AsyncMock(return_value=msg.message_id)), \
                patch.object(dispatcher, "update_message_delivery_status", AsyncMock()), \
                patch.object(dispatcher.redis_mgr, "get_raw", get_raw), patch.object(dispatcher.redis_mgr, "set_raw", set_raw), \
                patch("src.channel_adapters.whatsapp.service_window.last_inbound_at", AsyncMock(return_value=None)), \
                patch.object(dispatcher.whatsapp_client, "send_text_message", AsyncMock(return_value=SimpleNamespace(wamid="wamid.ok"))), \
                patch.object(channel_health, "note_success", AsyncMock()) as ok:
            await dispatcher.OutboundDispatcherWorker().process_message(SimpleNamespace(value=msg.to_kafka_bytes(), key=b"k", topic="t", partition=0, offset=1))
        ok.assert_awaited_once()
        self.assertEqual(ok.await_args.args[1], "whatsapp")


if __name__ == "__main__":
    unittest.main()

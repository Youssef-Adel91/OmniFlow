"""Facebook Messenger is its own channel ("messenger"), no longer stored as "instagram".

* the conversation of a returning Messenger customer that was stored as "instagram" is ADOPTED (continued and relabelled), never duplicated;
* an Instagram thread is never relabelled and never matched by a Messenger event (different id spaces, different products);
* the outbound dispatcher sends "messenger" messages through the same Page-token path as Instagram DMs;
* a failed Messenger send raises the "facebook" reconnect alarm, an Instagram one the "instagram" alarm.
"""
import os
import unittest
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.ai_workers.outbound_dispatcher import worker as dispatcher
from src.shared.core.enums import Channel
from src.shared.events.outbound import OutboundMessage

DB_URL = os.environ.get("OMNIFLOW_TEST_DATABASE_URL")
RLS_URL = os.environ.get("OMNIFLOW_TEST_RLS_URL")


@unittest.skipUnless(DB_URL and RLS_URL, "needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL")
class ConversationAdoptionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        self.text = text
        self.admin, self.app_engine = create_async_engine(DB_URL), create_async_engine(RLS_URL)
        self.Admin = async_sessionmaker(self.admin, expire_on_commit=False)
        self.App = async_sessionmaker(self.app_engine, expire_on_commit=False)
        self.t, self.c = uuid.uuid4(), uuid.uuid4()
        async with self.Admin() as s:
            await s.execute(text("INSERT INTO tenants (tenant_id,business_name,fal_license_number,subscription_tier,status,max_ai_conversations,onboarding_status,created_at,updated_at) "
                                 "VALUES (:t,'مكتب',:f,'starter','active',100,'completed',now(),now())"), {"t": self.t, "f": f"FAL-{self.t.hex[:8]}"})
            await s.execute(text("INSERT INTO customers (customer_id,tenant_id,unified_phone,is_processing_restricted,is_vip,vcard_state,created_at,updated_at) VALUES (:c,:t,'psid-1',false,false,'new',now(),now())"),
                            {"c": self.c, "t": self.t})
            await s.commit()
        self.addAsyncCleanup(self._cleanup)

    async def _cleanup(self):
        async with self.Admin() as s:
            for table in ("conversations", "customers", "tenants"):
                await s.execute(self.text(f"DELETE FROM {table} WHERE tenant_id=:t"), {"t": self.t})
            await s.commit()
        await self.admin.dispose()
        await self.app_engine.dispose()

    async def seed(self, channel, psid="psid-1"):
        v = uuid.uuid4()
        async with self.Admin() as s:
            await s.execute(self.text("INSERT INTO conversations (conversation_id,tenant_id,customer_id,channel,platform_conversation_id,status,message_count,created_at,updated_at) "
                                      "VALUES (:v,:t,:c,:ch,:p,'ai_active',0,now(),now())"), {"v": v, "t": self.t, "c": self.c, "ch": channel, "p": psid})
            await s.commit()
        return v

    async def get_or_create(self, channel, psid="psid-1"):
        from src.shared.db.repository import ConversationRepository
        from src.shared.db.session import get_tenant_session

        with patch("src.shared.db.session.AsyncSessionFactory", self.App):
            async with get_tenant_session(self.t) as session:
                conv, created = await ConversationRepository(session).get_or_create_for_platform(
                    tenant_id=self.t, customer_id=self.c, channel=channel, platform_conversation_id=psid)
                return conv.conversation_id, created

    async def channels(self):
        async with self.Admin() as s:
            return sorted((await s.execute(self.text("SELECT channel FROM conversations WHERE tenant_id=:t"), {"t": self.t})).scalars().all())

    async def test_a_legacy_instagram_labelled_messenger_conversation_is_adopted_not_duplicated(self):
        legacy = await self.seed("instagram")
        cid, created = await self.get_or_create(Channel.MESSENGER)
        self.assertEqual((cid, created), (legacy, False))
        self.assertEqual(await self.channels(), ["messenger"])

    async def test_a_new_messenger_customer_gets_a_messenger_conversation(self):
        _, created = await self.get_or_create(Channel.MESSENGER, "psid-new")
        self.assertTrue(created)
        self.assertEqual(await self.channels(), ["messenger"])

    async def test_an_instagram_event_never_adopts_or_relabels_anything(self):
        messenger = await self.seed("messenger")
        cid, created = await self.get_or_create(Channel.INSTAGRAM)
        self.assertTrue(created)
        self.assertNotEqual(cid, messenger)
        self.assertEqual(await self.channels(), ["instagram", "messenger"])

    async def test_a_second_messenger_message_reuses_the_conversation(self):
        first, _ = await self.get_or_create(Channel.MESSENGER)
        second, created = await self.get_or_create(Channel.MESSENGER)
        self.assertEqual((first, created), (second, False))


class DispatcherMessengerTests(unittest.IsolatedAsyncioTestCase):
    async def dispatch(self, channel, send_error=None):
        msg = OutboundMessage(message_id=uuid.uuid4(), tenant_id=uuid.uuid4(), conversation_id=uuid.uuid4(), customer_phone="psid-1", platform_conversation_id="psid-1",
                              text="hello", sender_type="human_agent", source_event_id=uuid.uuid4(), routing_tier_used="HUMAN", model_used="human_agent", channel=channel)
        store = {}

        @asynccontextmanager
        async def session(_):
            yield SimpleNamespace(scalar=AsyncMock(return_value=None))

        async def get_raw(k):
            return store.get(k)

        async def set_raw(k, v, **_):
            store[k] = v

        send = AsyncMock(side_effect=send_error) if send_error else AsyncMock(return_value=SimpleNamespace(message_id="m.1"))
        health = AsyncMock()
        with patch.object(dispatcher, "get_tenant_session", session), \
                patch.object(dispatcher, "_resolve_instagram_credentials", AsyncMock(return_value="page-token")), \
                patch.object(dispatcher, "persist_outbound_message", AsyncMock(return_value=msg.message_id)), \
                patch.object(dispatcher, "update_message_delivery_status", AsyncMock()) as update, \
                patch.object(dispatcher.redis_mgr, "get_raw", get_raw), patch.object(dispatcher.redis_mgr, "set_raw", set_raw), \
                patch("src.channel_adapters.instagram.client.send_message", send), \
                patch("src.shared.services.channel_health.mark_auth_failed", health):
            rec = SimpleNamespace(value=msg.to_kafka_bytes(), key=b"k", topic="t", partition=0, offset=1)
            try:
                await dispatcher.OutboundDispatcherWorker().process_message(rec)
            except Exception:  # noqa: BLE001 -- the failure path re-raises for the consumer's retry/DLQ logic
                pass
        return send, update, health

    async def test_a_messenger_message_is_sent_with_the_page_token_and_marked_sent(self):
        send, update, _ = await self.dispatch(Channel.MESSENGER)
        send.assert_awaited_once()
        self.assertEqual(send.await_args.kwargs["access_token"], "page-token")
        self.assertEqual(update.call_args.kwargs["delivery_status"], "SENT")

    async def test_auth_failures_name_the_right_channel(self):
        from src.channel_adapters.instagram.client import MetaAuthError

        for channel, label in ((Channel.MESSENGER, "facebook"), (Channel.INSTAGRAM, "instagram")):
            _, _, health = await self.dispatch(channel, send_error=MetaAuthError("expired"))
            self.assertEqual(health.await_args.args[1], label, channel)


if __name__ == "__main__":
    unittest.main()

"""Facebook/Instagram comment replies (OFF by default): platform flag, tenant switch, dedupe, rate limit, own-comment guard, thread replies,
public vs private mode, the post link in the inbox, and the outbound private-reply call.

Meta is simulated: signed webhook payloads are produced locally and Graph calls are mocked — nothing here talks to real Meta.
"""
import json
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from src.ai_workers.outbound_dispatcher import worker as dispatcher
from src.channel_adapters.instagram import client as ig_client
from src.channel_adapters.instagram import comment_policy
from src.channel_adapters.instagram import router as meta
from src.shared.core.config import get_settings
from src.shared.events.outbound import OutboundMessage
from src.shared.services import tenant_prefs
from tests.test_instagram_webhook import (CUSTOMER_IGSID, IG_ACCOUNT_ID, PAGE_ID, PATH, comment_patches, feed_comment_payload, ig_comment_payload,
                                          sign)


class PolicyUnitTests(unittest.TestCase):
    def test_post_url_only_for_facebook_style_ids(self):
        self.assertEqual(comment_policy.facebook_post_url("112233_445566"), "https://www.facebook.com/112233_445566")
        self.assertIsNone(comment_policy.facebook_post_url(None))
        self.assertIsNone(comment_policy.facebook_post_url("javascript:alert(1)"))
        self.assertIsNone(comment_policy.facebook_post_url("a/b"))

    def test_thread_reply_detection(self):
        self.assertFalse(comment_policy.is_thread_reply(parent_id=None))
        self.assertFalse(comment_policy.is_thread_reply(parent_id="9_9", post_id="9_9"))          # Facebook top-level comment: parent == post
        self.assertTrue(comment_policy.is_thread_reply(parent_id="9_5", post_id="9_9"))
        self.assertTrue(comment_policy.is_thread_reply(parent_id="1785"))                           # Instagram reply

    def test_preference_model_defaults_to_off_and_rejects_unknown_modes(self):
        defaults = tenant_prefs.validate_section("social_comments", {})
        self.assertEqual((defaults["enabled"], defaults["mode"], defaults["only_business_hours"], defaults["allow_price_in_private"]), (False, "public_reply", False, False))
        self.assertTrue(defaults["auto_hide_abusive"])
        self.assertEqual(tenant_prefs.validate_section("social_comments", {"enabled": True, "mode": "private_dm"})["mode"], "private_dm")
        with self.assertRaises(ValueError):
            tenant_prefs.validate_section("social_comments", {"mode": "shout"})
        with self.assertRaises(ValueError):
            tenant_prefs.validate_section("social_comments", {"bogus": 1})

    def test_the_platform_flag_defaults_to_off(self):
        self.assertFalse(type(get_settings()).model_fields["feature_social_comment_replies"].default)


class IngestionTestCase(unittest.IsolatedAsyncioTestCase):
    def start(self, **kw):
        self.counters = {}
        self.tenant_id = uuid.uuid4()
        self.app = FastAPI()
        self.app.include_router(meta.router)
        self.publish, self.persist = AsyncMock(), AsyncMock(return_value=(uuid.uuid4(), uuid.uuid4(), True))
        patchers = [patch.object(meta, "_resolve_tenant_id", AsyncMock(return_value=self.tenant_id)),
                    patch.object(meta, "persist_inbound_message", self.persist), patch.object(meta.kafka_producer, "publish", self.publish),
                    *comment_patches(counters=self.counters, **kw)]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    async def post(self, payload):
        body = json.dumps(payload).encode()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test") as c:
            return await c.post(PATH, content=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": sign(body)})

    def events(self):
        return [c.kwargs["event"] for c in self.publish.await_args_list]


class GatingTests(IngestionTestCase):
    async def test_nothing_is_answered_while_the_platform_flag_is_off(self):
        self.start(flag=False)
        for payload in (feed_comment_payload(), ig_comment_payload()):
            self.assertEqual((await self.post(payload)).status_code, 200)
        self.assertEqual((self.events(), self.persist.await_count, self.counters), ([], 0, {}))          # no tenant lookup effects, no storage, no counters

    async def test_nothing_is_answered_while_the_tenant_has_not_switched_it_on(self):
        self.start(enabled=False)
        await self.post(feed_comment_payload())
        await self.post(ig_comment_payload())
        self.assertEqual((self.events(), self.persist.await_count), ([], 0))

    async def test_a_duplicate_delivery_of_the_same_comment_is_answered_once(self):
        self.start()
        for _ in range(3):
            await self.post(feed_comment_payload(comment_id="123_456"))
        await self.post(ig_comment_payload(comment_id="17858893269000001"))
        await self.post(ig_comment_payload(comment_id="17858893269000001"))
        self.assertEqual(sorted(e.platform_message_id for e in self.events()), ["123_456", "17858893269000001"])

    async def test_without_redis_we_cannot_prove_it_is_not_a_duplicate_so_we_stay_silent(self):
        self.start()
        with patch.object(comment_policy.redis_mgr, "incr_counter", AsyncMock(return_value=None)):
            await self.post(feed_comment_payload())
        self.assertEqual(self.events(), [])

    async def test_the_per_tenant_rate_limit_caps_replies_per_minute(self):
        self.start()
        with patch.object(get_settings(), "social_comment_rate_per_minute", 2):
            for i in range(5):
                await self.post(feed_comment_payload(comment_id=f"123_{i}"))
        self.assertEqual(len(self.events()), 2)

    async def test_own_comments_and_thread_replies_are_never_answered(self):
        self.start()
        for payload in (feed_comment_payload(sender=PAGE_ID, comment_id="1_1"), feed_comment_payload(sender=IG_ACCOUNT_ID, comment_id="1_2"),
                        ig_comment_payload(sender=IG_ACCOUNT_ID, comment_id="2"), ig_comment_payload(sender=PAGE_ID, comment_id="3"),
                        ig_comment_payload(parent_id="17858893269000000", comment_id="4"), ig_comment_payload(text="  ", comment_id="5")):
            await self.post(payload)
        fb_reply = feed_comment_payload(comment_id="1_3")
        fb_reply["entry"][0]["changes"][0]["value"].update(post_id="1_9", parent_id="1_2")            # reply to another comment
        await self.post(fb_reply)
        self.assertEqual(self.events(), [])

    async def test_a_top_level_facebook_comment_whose_parent_is_the_post_is_answered(self):
        self.start()
        p = feed_comment_payload(comment_id="1_4")
        p["entry"][0]["changes"][0]["value"].update(post_id="1_9", parent_id="1_9")
        await self.post(p)
        self.assertEqual(len(self.events()), 1)


class ModeAndLinkTests(IngestionTestCase):
    async def test_public_mode_uses_the_public_reply_targets(self):
        self.start(mode="public_reply")
        await self.post(feed_comment_payload(comment_id="1_1"))
        await self.post(ig_comment_payload(comment_id="22"))
        self.assertEqual(sorted(e.reply_target_type for e in self.events()), ["comment", "instagram_comment"])

    async def test_private_mode_answers_both_platforms_with_a_private_reply(self):
        self.start(mode="private_dm")
        await self.post(feed_comment_payload(comment_id="1_1"))
        await self.post(ig_comment_payload(comment_id="22"))
        self.assertEqual([e.reply_target_type for e in self.events()], ["comment_private", "comment_private"])
        self.assertTrue(all(e.platform_conversation_id == e.platform_message_id for e in self.events()))          # the comment id is the thread

    async def test_the_agent_sees_a_link_to_the_post_but_the_ai_text_stays_clean(self):
        self.start()
        p = feed_comment_payload(comment_id="1_5", text="كم السعر؟")
        p["entry"][0]["changes"][0]["value"].update(post_id="112233_445566")
        await self.post(p)
        await self.post(ig_comment_payload(comment_id="26", text="متاح؟"))
        stored = [c.kwargs["text_content"] for c in self.persist.await_args_list]
        self.assertEqual(stored[0], "💬 تعليق على منشور: https://www.facebook.com/112233_445566\nكم السعر؟")
        self.assertEqual(stored[1], "💬 تعليق على منشور إنستجرام (المعرّف 17900000000000001)\nمتاح؟")        # Instagram: id only, no permalink lookup
        fb_event, ig_event = self.events()
        self.assertEqual((fb_event.text_content, fb_event.source_post_url), ("كم السعر؟", "https://www.facebook.com/112233_445566"))
        self.assertEqual((ig_event.text_content, ig_event.source_post_url, ig_event.source_post_id), ("متاح؟", None, "17900000000000001"))


class PrivateReplyDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_posts_recipient_comment_id_to_the_page_messages_endpoint(self):
        post = AsyncMock(return_value=httpx.Response(200, json={"recipient_id": "r", "message_id": "mid.1"}))
        with patch.object(httpx.AsyncClient, "post", post):
            out = await ig_client.send_private_reply("PAGE1", "c_9", "hello", access_token="tok")
        self.assertEqual(out["message_id"], "mid.1")
        call = post.await_args
        self.assertTrue(call.args[0].endswith("/PAGE1/messages"))
        self.assertEqual(call.kwargs["json"], {"recipient": {"comment_id": "c_9"}, "message": {"text": "hello"}})
        self.assertEqual(call.kwargs["params"], {"access_token": "tok"})

    async def test_client_maps_meta_errors(self):
        with patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=httpx.Response(400, json={"error": {"code": 100, "message": "already replied"}}))):
            with self.assertRaises(ig_client.MetaInvalidRecipientError):
                await ig_client.send_private_reply("PAGE1", "c_9", "hello", access_token="tok")

    async def test_dispatcher_routes_comment_private_to_the_private_reply_function(self):
        from tests.test_instagram_webhook import OutboundInstagramTests

        t = OutboundInstagramTests()
        t.setUp()
        msg = t.message("comment_private", platform_conversation_id="c_9")
        sent = AsyncMock(return_value={"message_id": "mid.priv"})
        with patch.object(dispatcher, "_resolve_instagram_page_id", AsyncMock(return_value="PAGE1")):
            graph = await t.dispatch(msg, send_private_reply=sent)
        sent.assert_awaited_once_with("PAGE1", "c_9", "our reply", access_token=t.page_token)
        graph["send_comment_reply"].assert_not_awaited()
        graph["send_message"].assert_not_awaited()
        self.assertEqual(t.update.await_args.kwargs["delivery_status"], "SENT")

    async def test_the_outbound_schema_accepts_the_new_target(self):
        m = OutboundMessage(tenant_id=uuid.uuid4(), customer_phone=CUSTOMER_IGSID, channel="instagram", platform_conversation_id="c", reply_target_type="comment_private",
                            text="x", source_event_id=uuid.uuid4(), routing_tier_used="L1", model_used="m")
        self.assertEqual(OutboundMessage.model_validate_json(m.to_kafka_bytes()).reply_target_type, "comment_private")


if __name__ == "__main__":
    unittest.main()

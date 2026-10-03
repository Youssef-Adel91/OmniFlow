"""Instagram / Messenger webhook, tenant routing and outbound delivery (mocked Graph API, no network or database)."""
import hashlib
import hmac
import json
import unittest
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from sqlalchemy.dialects import postgresql

from src.ai_workers.outbound_dispatcher import worker as dispatcher
from src.channel_adapters.instagram import client as ig_client
from src.channel_adapters.instagram import router as meta
from src.shared.core.config import get_settings
from src.shared.events.outbound import OutboundMessage
from src.shared.security.crypto import encrypt_secret

PAGE_ID = "112233445566778"
IG_ACCOUNT_ID = "17841400000000000"
CUSTOMER_IGSID = "9988776655"
PATH = "/webhooks/meta"


def sign(body: bytes) -> str:
    secret = get_settings().meta_webhook_hmac_secret.encode()
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


def dm_payload(obj, entry_id, sender=CUSTOMER_IGSID, text="hello", echo=False):
    message = {"mid": "mid.1", "text": text}
    if echo:
        message["is_echo"] = True
    return {
        "object": obj,
        "entry": [{"id": entry_id, "time": 1, "messaging": [{
            "sender": {"id": sender}, "recipient": {"id": entry_id},
            "timestamp": 1716377600000, "message": message,
        }]}],
    }


def ig_comment_payload(entry_id=IG_ACCOUNT_ID, comment_id="17858893269000001", sender=CUSTOMER_IGSID,
                       text="how much?", parent_id=None, username="john"):
    value = {
        "id": comment_id, "text": text,
        "from": {"id": sender, "username": username},
        "media": {"id": "17900000000000001", "media_product_type": "FEED"},
    }
    if parent_id:
        value["parent_id"] = parent_id
    return {"object": "instagram", "entry": [{"id": entry_id, "time": 1,
                                              "changes": [{"field": "comments", "value": value}]}]}


def feed_comment_payload(page_id=PAGE_ID, comment_id="123_456", sender=CUSTOMER_IGSID, text="price?",
                         verb="add", item="comment"):
    return {"object": "page", "entry": [{"id": page_id, "time": 1, "changes": [{
        "field": "feed",
        "value": {"item": item, "verb": verb, "comment_id": comment_id, "message": text,
                  "from": {"id": sender, "name": "John"}},
    }]}]}


class WebhookIngestionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tenant_id = uuid.uuid4()
        self.app = FastAPI()
        self.app.include_router(meta.router)
        self.resolve = AsyncMock(return_value=self.tenant_id)
        self.publish = AsyncMock()
        for p in (
            patch.object(meta, "_resolve_tenant_id", self.resolve),
            patch.object(meta, "persist_inbound_message", AsyncMock(return_value=(uuid.uuid4(), uuid.uuid4(), True))),
            patch.object(meta.kafka_producer, "publish", self.publish),
        ):
            p.start()
            self.addCleanup(p.stop)

    async def post(self, payload, signed=True, signature=None):
        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if signature is not None:
            headers["X-Hub-Signature-256"] = signature
        elif signed:
            headers["X-Hub-Signature-256"] = sign(body)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test") as c:
            return await c.post(PATH, content=body, headers=headers)

    def published(self):
        return [call.kwargs["event"] for call in self.publish.await_args_list]

    # ── signature (must keep working for the new object type) ────────────────
    async def test_unsigned_or_wrongly_signed_requests_are_rejected_for_both_objects(self):
        for payload in (dm_payload("page", PAGE_ID), dm_payload("instagram", IG_ACCOUNT_ID)):
            self.assertEqual((await self.post(payload, signed=False)).status_code, 403)
            self.assertEqual((await self.post(payload, signature="sha256=" + "0" * 64)).status_code, 403)
        self.publish.assert_not_awaited()
        self.resolve.assert_not_awaited()

    # ── object routing ───────────────────────────────────────────────────────
    async def test_page_object_dm_is_published_and_routed_by_page_id(self):
        self.assertEqual((await self.post(dm_payload("page", PAGE_ID))).status_code, 200)
        (event,) = self.published()
        self.resolve.assert_awaited_once_with(PAGE_ID)
        self.assertEqual(event.tenant_id, self.tenant_id)
        self.assertEqual(str(event.channel), "instagram")
        self.assertEqual(event.platform_user_id, CUSTOMER_IGSID)
        self.assertEqual(event.reply_target_type, "dm")

    async def test_instagram_object_dm_is_accepted_and_routed_by_instagram_account_id(self):
        self.assertEqual((await self.post(dm_payload("instagram", IG_ACCOUNT_ID))).status_code, 200)
        (event,) = self.published()
        self.resolve.assert_awaited_once_with(IG_ACCOUNT_ID)
        self.assertEqual(event.tenant_id, self.tenant_id)
        self.assertEqual(event.platform_conversation_id, CUSTOMER_IGSID)
        self.assertEqual(event.text_content, "hello")
        self.assertEqual(self.publish.await_args.kwargs["headers"]["tenant_id"], str(self.tenant_id))

    async def test_unknown_object_is_acknowledged_but_ignored(self):
        response = await self.post(dm_payload("whatsapp_business_account", "1"))
        self.assertEqual(response.status_code, 200)
        self.publish.assert_not_awaited()

    async def test_echoes_self_authored_messages_and_receipts_are_not_processed(self):
        receipt = dm_payload("instagram", IG_ACCOUNT_ID)
        receipt["entry"][0]["messaging"][0] = {
            "sender": {"id": CUSTOMER_IGSID}, "recipient": {"id": IG_ACCOUNT_ID}, "read": {"mid": "mid.1"},
        }
        for payload in (
            dm_payload("instagram", IG_ACCOUNT_ID, echo=True),
            dm_payload("instagram", IG_ACCOUNT_ID, sender=IG_ACCOUNT_ID),
            receipt,
        ):
            self.assertEqual((await self.post(payload)).status_code, 200)
        self.publish.assert_not_awaited()

    # ── Instagram comments (field "comments") ────────────────────────────────
    async def test_instagram_comment_is_published_as_instagram_comment(self):
        self.assertEqual((await self.post(ig_comment_payload())).status_code, 200)
        (event,) = self.published()
        self.resolve.assert_awaited_once_with(IG_ACCOUNT_ID)
        self.assertEqual(event.reply_target_type, "instagram_comment")
        self.assertEqual(event.platform_conversation_id, "17858893269000001")
        self.assertEqual(event.platform_message_id, "17858893269000001")
        self.assertEqual(event.platform_user_id, CUSTOMER_IGSID)
        self.assertEqual(event.customer_display_name, "john")
        self.assertEqual(event.text_content, "how much?")
        self.assertEqual(str(event.channel), "instagram")

    async def test_instagram_comments_that_must_not_get_a_reply_are_skipped(self):
        for payload in (
            ig_comment_payload(sender=IG_ACCOUNT_ID),          # our own reply coming back: would loop forever
            ig_comment_payload(parent_id="17858893269000000"),  # reply inside a thread: IG only replies to top-level
            ig_comment_payload(text="   "),                    # nothing to answer
        ):
            self.assertEqual((await self.post(payload)).status_code, 200)
        self.publish.assert_not_awaited()

    async def test_other_instagram_change_fields_are_ignored(self):
        payload = ig_comment_payload()
        payload["entry"][0]["changes"][0]["field"] = "mentions"
        self.assertEqual((await self.post(payload)).status_code, 200)
        self.publish.assert_not_awaited()

    # ── Facebook Page feed comments (field "feed") ───────────────────────────
    async def test_facebook_feed_comment_is_published_as_comment(self):
        self.assertEqual((await self.post(feed_comment_payload())).status_code, 200)
        (event,) = self.published()
        self.resolve.assert_awaited_once_with(PAGE_ID)
        self.assertEqual(event.reply_target_type, "comment")
        self.assertEqual(event.platform_conversation_id, "123_456")
        self.assertEqual(event.customer_display_name, "John")

    async def test_facebook_feed_changes_that_must_not_get_a_reply_are_skipped(self):
        for payload in (
            feed_comment_payload(sender=PAGE_ID),   # the Page's own reply
            feed_comment_payload(verb="remove"),
            feed_comment_payload(item="post"),
        ):
            self.assertEqual((await self.post(payload)).status_code, 200)
        self.publish.assert_not_awaited()


class TenantResolutionTests(unittest.IsolatedAsyncioTestCase):
    def session_returning(self, row, captured):
        @asynccontextmanager
        async def session():
            async def execute(stmt):
                captured.append(stmt)
                return SimpleNamespace(first=lambda: row)
            yield SimpleNamespace(execute=execute)
        return session

    async def test_lookup_matches_page_id_or_instagram_account_id(self):
        captured = []
        tid = uuid.uuid4()
        row = SimpleNamespace(tenant_id=tid, instagram_page_id=PAGE_ID)
        with patch.object(meta, "get_system_session", self.session_returning(row, captured)):
            self.assertEqual(await meta._resolve_tenant_id(IG_ACCOUNT_ID), tid)
            self.assertEqual(await meta._resolve_tenant_id(PAGE_ID), tid)
        sql = str(captured[0].compile(dialect=postgresql.dialect()))
        self.assertIn("instagram_page_id", sql)
        self.assertIn("instagram_account_id", sql)
        self.assertIn(" OR ", sql)
        self.assertIn("status IN", sql)

    async def test_unknown_id_uses_dev_placeholder_only_in_development(self):
        with patch.object(meta, "get_system_session", self.session_returning(None, [])):
            self.assertEqual(await meta._resolve_tenant_id("unknown"), uuid.UUID(int=0))
            with patch.object(meta, "settings", SimpleNamespace(is_development=False)):
                with self.assertRaises(ValueError):
                    await meta._resolve_tenant_id("unknown")

    async def test_database_error_never_resolves_to_a_wrong_tenant(self):
        @asynccontextmanager
        async def broken():
            raise RuntimeError("db down")
            yield  # pragma: no cover
        with patch.object(meta, "get_system_session", broken), \
                patch.object(meta, "settings", SimpleNamespace(is_development=False)):
            with self.assertRaises(ValueError):
                await meta._resolve_tenant_id(IG_ACCOUNT_ID)


class GraphClientTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, fn, response):
        post = AsyncMock(return_value=response)
        with patch.object(httpx.AsyncClient, "post", post):
            result = await fn("777", "thanks!", access_token="tok")
        return result, post.await_args

    async def test_instagram_comments_reply_on_replies_and_facebook_comments_on_comments(self):
        _, ig = await self.call(ig_client.send_instagram_comment_reply, httpx.Response(200, json={"id": "r1"}))
        _, fb = await self.call(ig_client.send_comment_reply, httpx.Response(200, json={"id": "r2"}))
        self.assertTrue(ig.args[0].endswith("/777/replies"))
        self.assertTrue(fb.args[0].endswith("/777/comments"))
        self.assertEqual(ig.kwargs["params"], {"access_token": "tok"})
        self.assertEqual(ig.kwargs["json"], {"message": "thanks!"})

    async def test_instagram_comment_reply_returns_graph_id_and_maps_auth_errors(self):
        result, _ = await self.call(ig_client.send_instagram_comment_reply, httpx.Response(200, json={"id": "r1"}))
        self.assertEqual(result, {"id": "r1"})
        with self.assertRaises(ig_client.MetaAuthError):
            await self.call(ig_client.send_instagram_comment_reply,
                            httpx.Response(401, json={"error": {"code": 190, "message": "expired"}}))


class OutboundInstagramTests(unittest.IsolatedAsyncioTestCase):
    """outbound_dispatcher -> Graph API, using the tenant's encrypted Page token."""

    def setUp(self):
        self.page_token = "EAAB-plain-page-token"
        self.tenant = SimpleNamespace(instagram_page_access_token=encrypt_secret(self.page_token))
        self.sent = {}

    def message(self, reply_target_type="dm", **overrides):
        fields = dict(
            tenant_id=uuid.uuid4(), channel="instagram", customer_phone=CUSTOMER_IGSID,
            platform_conversation_id=CUSTOMER_IGSID, reply_target_type=reply_target_type,
            text="our reply", source_event_id=uuid.uuid4(), routing_tier_used="L1", model_used="m",
        )
        fields.update(overrides)
        return OutboundMessage(**fields)

    def patches(self, tenant):
        @asynccontextmanager
        async def session(_):
            async def scalar(stmt):
                return tenant if "tenants" in str(stmt) else None
            yield SimpleNamespace(scalar=scalar)

        async def get_raw(key):
            return self.sent.get(key)

        async def set_raw(key, value, **_):
            self.sent[key] = value

        self.update = AsyncMock()
        return (
            patch.object(dispatcher, "get_tenant_session", session),
            patch.object(dispatcher, "persist_outbound_message", AsyncMock(return_value=uuid.uuid4())),
            patch.object(dispatcher, "update_message_delivery_status", self.update),
            patch.object(dispatcher.redis_mgr, "get_raw", get_raw),
            patch.object(dispatcher.redis_mgr, "set_raw", set_raw),
        )

    async def dispatch(self, msg, tenant=None, **graph):
        record = SimpleNamespace(value=msg.to_kafka_bytes(), key=b"k", topic="out", partition=0, offset=1)
        base = tenant if tenant is not None else self.tenant
        graph_patches = {
            "send_message": AsyncMock(return_value=SimpleNamespace(message_id="mid.out", recipient_id="r")),
            "send_comment_reply": AsyncMock(return_value={"id": "fb-reply"}),
            "send_instagram_comment_reply": AsyncMock(return_value={"id": "ig-reply"}),
        }
        graph_patches.update(graph)
        ctx = list(self.patches(base)) + [
            patch.object(ig_client, name, mock) for name, mock in graph_patches.items()
        ]
        for p in ctx:
            p.start()
        try:
            await dispatcher.OutboundDispatcherWorker().process_message(record)
        finally:
            for p in ctx:
                p.stop()
        return graph_patches

    async def test_dm_is_sent_with_the_decrypted_tenant_page_token(self):
        graph = await self.dispatch(self.message("dm"))
        graph["send_message"].assert_awaited_once_with(
            recipient_id=CUSTOMER_IGSID, text="our reply", access_token=self.page_token,
        )
        graph["send_instagram_comment_reply"].assert_not_awaited()
        self.assertEqual(self.update.await_args.kwargs["delivery_status"], "SENT")
        self.assertEqual(self.update.await_args.kwargs["platform_message_id"], "mid.out")

    async def test_instagram_comment_uses_the_replies_endpoint_function(self):
        graph = await self.dispatch(self.message("instagram_comment", platform_conversation_id="17858893269000001"))
        graph["send_instagram_comment_reply"].assert_awaited_once_with(
            comment_id="17858893269000001", text="our reply", access_token=self.page_token,
        )
        graph["send_comment_reply"].assert_not_awaited()
        graph["send_message"].assert_not_awaited()
        self.assertEqual(self.update.await_args.kwargs["platform_message_id"], "ig-reply")

    async def test_facebook_comment_still_uses_the_comments_endpoint_function(self):
        graph = await self.dispatch(self.message("comment", platform_conversation_id="123_456"))
        graph["send_comment_reply"].assert_awaited_once_with(
            comment_id="123_456", text="our reply", access_token=self.page_token,
        )
        graph["send_instagram_comment_reply"].assert_not_awaited()

    async def test_tenant_without_a_page_token_cannot_send(self):
        with self.assertRaises(RuntimeError):
            await self.dispatch(self.message("dm"), tenant=SimpleNamespace(instagram_page_access_token=None))

    async def test_legacy_plaintext_token_is_still_used(self):
        graph = await self.dispatch(
            self.message("dm"), tenant=SimpleNamespace(instagram_page_access_token="EAAB-legacy-plain"),
        )
        self.assertEqual(graph["send_message"].await_args.kwargs["access_token"], "EAAB-legacy-plain")


class WebhookToGraphChainTests(unittest.IsolatedAsyncioTestCase):
    """Signed webhook -> Kafka event -> (llm_invoker's field mapping) -> dispatcher -> real Graph client URL."""

    async def run_chain(self, payload):
        tenant_id = uuid.uuid4()
        app = FastAPI()
        app.include_router(meta.router)
        publish = AsyncMock()
        token = "EAAB-chain-token"
        tenant = SimpleNamespace(instagram_page_access_token=encrypt_secret(token))

        @asynccontextmanager
        async def session(_):
            async def scalar(stmt):
                return tenant if "tenants" in str(stmt) else None
            yield SimpleNamespace(scalar=scalar)

        graph_post = AsyncMock(return_value=httpx.Response(200, json={"id": "x", "message_id": "mid.out", "recipient_id": "r"}))
        store = {}

        async def get_raw(key):
            return store.get(key)

        async def set_raw(key, value, **_):
            store[key] = value

        body = json.dumps(payload).encode()
        with patch.object(meta, "_resolve_tenant_id", AsyncMock(return_value=tenant_id)), \
                patch.object(meta, "persist_inbound_message", AsyncMock(return_value=(uuid.uuid4(), uuid.uuid4(), True))), \
                patch.object(meta.kafka_producer, "publish", publish):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
                response = await c.post(PATH, content=body, headers={"X-Hub-Signature-256": sign(body)})
        self.assertEqual(response.status_code, 200)
        event = publish.await_args.kwargs["event"]

        # The same field mapping llm_invoker uses when it builds the reply.
        outbound = OutboundMessage(
            tenant_id=event.tenant_id, customer_phone=event.customer_phone or event.platform_user_id,
            channel=str(event.channel), platform_conversation_id=event.platform_conversation_id,
            reply_target_type=event.reply_target_type, text="ai reply", source_event_id=event.event_id,
            routing_tier_used="L1", model_used="m",
        )
        record = SimpleNamespace(value=outbound.to_kafka_bytes(), key=b"k", topic="out", partition=0, offset=1)
        with patch.object(dispatcher, "get_tenant_session", session), \
                patch.object(dispatcher, "persist_outbound_message", AsyncMock(return_value=uuid.uuid4())), \
                patch.object(dispatcher, "update_message_delivery_status", AsyncMock()), \
                patch.object(dispatcher.redis_mgr, "get_raw", get_raw), \
                patch.object(dispatcher.redis_mgr, "set_raw", set_raw), \
                patch.object(httpx.AsyncClient, "post", graph_post):
            await dispatcher.OutboundDispatcherWorker().process_message(record)
        return graph_post.await_args, token

    async def test_instagram_dm_ends_as_a_graph_message_to_the_igsid(self):
        call, token = await self.run_chain(dm_payload("instagram", IG_ACCOUNT_ID))
        self.assertTrue(call.args[0].endswith("/me/messages"))
        self.assertEqual(call.kwargs["params"], {"access_token": token})
        self.assertEqual(call.kwargs["json"]["recipient"], {"id": CUSTOMER_IGSID})
        self.assertEqual(call.kwargs["json"]["message"], {"text": "ai reply"})

    async def test_instagram_comment_ends_as_a_graph_reply_on_the_comment(self):
        call, token = await self.run_chain(ig_comment_payload(comment_id="17858893269000001"))
        self.assertTrue(call.args[0].endswith("/17858893269000001/replies"))
        self.assertEqual(call.kwargs["params"], {"access_token": token})
        self.assertEqual(call.kwargs["json"], {"message": "ai reply"})

    async def test_facebook_feed_comment_ends_as_a_graph_comment_on_the_comment(self):
        call, _ = await self.run_chain(feed_comment_payload(comment_id="123_456"))
        self.assertTrue(call.args[0].endswith("/123_456/comments"))


if __name__ == "__main__":
    unittest.main()

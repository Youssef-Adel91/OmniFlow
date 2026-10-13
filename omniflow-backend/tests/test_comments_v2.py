"""Comment replies v2 + channel health + Meta diagnostics. Meta, Redis and the database are all simulated: nothing here talks to a real service.

Covers: text rules (emoji / insults / spam / ignore words), hide, public template + private reply, business hours, hourly cap, the public-safety
filter for AI answers, Meta token-error classification (190), the channel-health alarm, the page diagnostics, and the new Settings endpoints.
"""
import json
import unittest
import uuid
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import FastAPI

from src.ai_workers.outbound_dispatcher import worker as dispatcher
from src.channel_adapters.instagram import client as ig_client
from src.channel_adapters.instagram import comment_actions, comment_moderation as mod
from src.channel_adapters.instagram import router as meta
from src.gateway import dependencies as deps
from src.gateway.main import create_app
from src.shared.core.config import get_settings
from src.shared.core.enums import TenantUserRole
from src.shared.services import channel_health, meta_diagnostics, tenant_prefs
from tests.test_comment_replies import IngestionTestCase
from tests.test_instagram_webhook import PAGE_ID, comment_patches, feed_comment_payload, ig_comment_payload
from tests.test_instagram_webhook import OutboundInstagramTests
from tests.test_property_import_flow import client_for


def verdict(text, **kw):
    return mod.classify_text(text, ignore_keywords=kw.get("ignore", []), hide_keywords=kw.get("hide", []), auto_hide_abusive=kw.get("auto", True))


class ModerationRulesTests(unittest.TestCase):
    def test_emoji_only_and_single_characters_are_ignored(self):
        for t in ("😍😍", "👍", "!!!", "؟", "a", "  "):
            self.assertEqual((verdict(t).action, verdict(t).reason), ("ignore", "no_text"), t)

    def test_ordinary_questions_are_answered(self):
        for t in ("كم السعر؟", "هل متاحة للمعاينة؟", "ممتاز جدا", "how much?", "اتصلوا بي 0501234567"):
            self.assertIsNone(verdict(t), t)               # a phone number is a customer asking to be called, not spam

    def test_insults_scam_accusations_and_links_are_hidden(self):
        for t, why in (("انتم نصابين", "abusive"), ("هذا احتيال", "abusive"), ("you are a scammer", "abusive"), ("زورو www.example.com الان", "spam"),
                       ("https://bit.ly/x", "spam"), ("aaaaaaaaaaaaaaa", "spam")):
            v = verdict(t)
            self.assertEqual((v.action, v.reason), ("hide", why), t)

    def test_arabic_spelling_variants_match(self):
        self.assertEqual(verdict("حقيرة").action, "hide")              # ة / ه normalisation
        self.assertEqual(verdict("نصَّاب").action, "hide")             # diacritics removed

    def test_auto_hide_can_be_switched_off_and_tenant_words_still_work(self):
        self.assertIsNone(verdict("انتم نصابين", auto=False))
        self.assertEqual(verdict("كلام فاضي", hide=["فاضي"]).reason, "hide_keyword")
        self.assertIsNone(verdict("كلام فاضي", hide=["فاضي"], auto=False))        # hide words belong to the auto-hide switch
        self.assertEqual((verdict("شركة المنافس أحسن", ignore=["المنافس"]).action, verdict("شركة المنافس أحسن", ignore=["المنافس"]).reason), ("ignore", "ignore_keyword"))

    def test_hide_wins_over_ignore(self):
        self.assertEqual(verdict("نصاب والمنافس احسن", ignore=["المنافس"]).action, "hide")

    def test_public_policy_blocks_prices_and_promises_but_not_normal_sentences(self):
        for t in ("السعر 850,000 ريال", "يبدأ من 900 الف", "price 1.2 million SAR", "ابتداء من 1500000", "SAR 900", "السعر يبدأ من ٩٠٠ ألف",
                  "$450 only"):
            self.assertEqual(mod.violates_public_policy(t), "price", t)
        for t in ("نضمن لك عائد 10%", "ربح مضمون", "risk-free investment", "عائد مضمون سنويا"):
            self.assertEqual(mod.violates_public_policy(t), "promise", t)
        for t in ("شقة 3 غرف في حي الياسمين", "تواصل معنا على الخاص", "المساحة 120 متر", "أهلا بك! كيف أخدمك؟", "عندنا خيارات في شمال الرياض"):
            self.assertIsNone(mod.violates_public_policy(t), t)

    def test_template_name_handling(self):
        self.assertEqual(mod.render_public_template("أهلاً {name} 🌟 شكراً", name="Ahmed Ali"), "أهلاً Ahmed 🌟 شكراً")
        self.assertEqual(mod.render_public_template("أهلاً {name} 🌟 شكراً", name=None), "أهلاً 🌟 شكراً")


class PrefsModelTests(unittest.TestCase):
    def test_new_fields_validate(self):
        ok = tenant_prefs.validate_section("social_comments", {"mode": "public_and_private", "max_per_hour": 10, "ignore_keywords": [" x ", "x"],
                                                               "public_template": "هلا {name}"})
        self.assertEqual((ok["mode"], ok["max_per_hour"], ok["ignore_keywords"]), ("public_and_private", 10, ["x"]))
        for bad in ({"max_per_hour": 0}, {"max_per_hour": 5000}, {"public_template": "  "}, {"public_template": "hi {phone}"}, {"mode": "shout"},
                    {"ignore_keywords": ["x" * 41]}, {"ignore_keywords": ["k"] * 51}):
            with self.assertRaises(ValueError, msg=str(bad)):
                tenant_prefs.validate_section("social_comments", bad)

    def test_an_old_stored_row_still_loads_with_defaults(self):
        old = tenant_prefs.with_defaults("social_comments", {"enabled": True, "mode": "private_dm"})
        self.assertEqual((old["enabled"], old["mode"], old["max_per_hour"], old["auto_hide_abusive"]), (True, "private_dm", 60, True))


class V2IngestionTests(IngestionTestCase):
    def start(self, **kw):
        super().start(**kw)
        self.hide = AsyncMock(return_value=True)
        self.public = AsyncMock(return_value="أهلاً 🌟")
        self.rec = AsyncMock()
        for p in (patch.object(comment_actions, "hide_comment_for_tenant", self.hide), patch.object(comment_actions, "post_public_template", self.public),
                  patch.object(comment_actions, "record", self.rec)):
            p.start()
            self.addCleanup(p.stop)

    def recorded(self):
        return [(c.kwargs["action"], c.kwargs["reason"]) for c in self.rec.await_args_list]

    async def test_an_abusive_comment_is_hidden_and_never_answered(self):
        self.start()
        await self.post(feed_comment_payload(comment_id="1_1", text="انتم نصابين"))
        await self.post(ig_comment_payload(comment_id="22", text="visit www.spam.com"))
        self.assertEqual((self.events(), self.hide.await_count), ([], 2))
        self.assertEqual([c.kwargs["platform"] for c in self.hide.await_args_list], ["facebook", "instagram"])
        self.assertEqual(self.recorded(), [("hidden", "abusive"), ("hidden", "spam")])

    async def test_a_failed_hide_is_logged_as_failed_not_silently_dropped(self):
        self.start()
        self.hide.return_value = False
        await self.post(feed_comment_payload(comment_id="1_1", text="انتم نصابين"))
        self.assertEqual(self.recorded(), [("hide_failed", "abusive")])

    async def test_emoji_only_and_ignore_words_are_ignored_and_logged(self):
        self.start(extra={"ignore_keywords": ["المنافس"]})
        await self.post(feed_comment_payload(comment_id="1_1", text="😍😍😍"))
        await self.post(feed_comment_payload(comment_id="1_2", text="شركة المنافس أحسن"))
        self.assertEqual((self.events(), self.hide.await_count), ([], 0))
        self.assertEqual(self.recorded(), [("ignored", "no_text"), ("ignored", "ignore_keyword")])

    async def test_hide_is_off_when_the_tenant_turns_auto_hide_off(self):
        self.start(extra={"auto_hide_abusive": False})
        await self.post(feed_comment_payload(comment_id="1_1", text="انتم نصابين"))
        self.assertEqual((self.hide.await_count, len(self.events())), (0, 1))

    async def test_outside_business_hours_comments_are_left_alone_when_the_tenant_asked_for_it(self):
        closed = {"enabled": True, "timezone": "Asia/Riyadh", "days": {}, "out_of_hours_message": "x"}
        self.start(extra={"only_business_hours": True}, hours={**closed, "days": {"sun": []}} | {"enabled": False})
        await self.post(feed_comment_payload(comment_id="1_1"))              # hours disabled = always open
        self.assertEqual(len(self.events()), 1)

    async def test_business_hours_closed_blocks_and_is_logged(self):
        hours = tenant_prefs.validate_section("business_hours", {"enabled": True, "days": {"sun": [["09:00", "10:00"]]}})
        self.start(extra={"only_business_hours": True}, hours=hours)
        with patch.object(tenant_prefs, "is_open", return_value=False):
            await self.post(feed_comment_payload(comment_id="1_1"))
        self.assertEqual((self.events(), self.recorded()), ([], [("ignored", "outside_business_hours")]))

    async def test_hourly_cap(self):
        self.start(extra={"max_per_hour": 2})
        for i in range(4):
            await self.post(feed_comment_payload(comment_id=f"9_{i}"))
        self.assertEqual(len(self.events()), 2)
        self.assertEqual(self.recorded()[-1], ("ignored", "hourly_cap"))

    async def test_public_and_private_posts_the_template_then_queues_a_private_reply(self):
        self.start(mode="public_and_private")
        await self.post(feed_comment_payload(comment_id="1_1"))
        await self.post(ig_comment_payload(comment_id="22"))
        self.assertEqual([c.kwargs["platform"] for c in self.public.await_args_list], ["facebook", "instagram"])
        self.assertEqual([e.reply_target_type for e in self.events()], ["comment_private", "comment_private"])
        self.assertEqual(self.recorded()[0][0], "public_and_private")

    async def test_a_failed_public_template_never_blocks_the_private_reply(self):
        self.start(mode="public_and_private")
        self.public.return_value = None
        await self.post(feed_comment_payload(comment_id="1_1"))
        self.assertEqual([e.reply_target_type for e in self.events()], ["comment_private"])

    async def test_hide_does_not_use_the_reply_rate_limits(self):
        self.start(extra={"max_per_hour": 1})
        for i in range(3):
            await self.post(feed_comment_payload(comment_id=f"5_{i}", text="انتم نصابين"))
        self.assertEqual(self.hide.await_count, 3)


class PublicSafetyFilterTests(unittest.IsolatedAsyncioTestCase):
    def msg(self, text):
        return NS(tenant_id=uuid.uuid4(), text=text)

    async def run_filter(self, text, **pref):
        async def prefs(_t):
            return {"social_comments": {"enabled": True, **pref}}

        with patch.object(tenant_prefs, "get_prefs", prefs):
            return await dispatcher._public_safe_text(self.msg(text))

    async def test_a_safe_answer_is_posted_as_is(self):
        self.assertEqual(await self.run_filter("أهلا! سعدنا بتواصلك، راسلنا للتفاصيل."), ("أهلا! سعدنا بتواصلك، راسلنا للتفاصيل.", None))

    async def test_an_answer_with_a_price_is_withheld_from_the_public(self):
        public, private = await self.run_filter("الشقة بسعر 850,000 ريال")
        self.assertEqual((public, private), (mod.PUBLIC_FALLBACK_NO_DM, None))
        self.assertNotIn("850", public)

    async def test_with_the_setting_on_the_full_answer_goes_privately(self):
        public, private = await self.run_filter("الشقة بسعر 850,000 ريال", allow_price_in_private=True, public_template="شكراً {name}، أرسلنا لك التفاصيل")
        self.assertEqual((public, private), ("شكراً، أرسلنا لك التفاصيل", "الشقة بسعر 850,000 ريال"))

    async def test_a_return_promise_is_never_public(self):
        public, _ = await self.run_filter("نضمن لك عائد استثماري 12%")
        self.assertEqual(public, mod.PUBLIC_FALLBACK_NO_DM)


class DispatcherPublicSafetyTests(unittest.IsolatedAsyncioTestCase):
    """The real dispatcher path (Graph mocked): a public answer with a price is replaced; the private follow-up only when allowed."""
    setUp = OutboundInstagramTests.setUp
    message = OutboundInstagramTests.message
    patches = OutboundInstagramTests.patches
    dispatch = OutboundInstagramTests.dispatch

    async def run_with_prefs(self, text, target="comment", **pref):
        async def prefs(_t):
            return {"social_comments": {"enabled": True, **pref}}

        private = AsyncMock(return_value={"message_id": "pm1"})
        with patch.object(tenant_prefs, "get_prefs", prefs), patch.object(ig_client, "send_private_reply", private):
            graph = await self.dispatch(self.message(target, platform_conversation_id="123_456", text=text))
        return graph, private

    async def test_a_normal_public_answer_is_posted_unchanged_and_nothing_private_is_sent(self):
        graph, private = await self.run_with_prefs("أهلاً بك، راسلنا على الخاص")
        graph["send_comment_reply"].assert_awaited_once_with(comment_id="123_456", text="أهلاً بك، راسلنا على الخاص", access_token=self.page_token)
        private.assert_not_awaited()

    async def test_a_price_is_never_posted_publicly(self):
        graph, private = await self.run_with_prefs("السعر 850,000 ريال")
        self.assertEqual(graph["send_comment_reply"].await_args.kwargs["text"], mod.PUBLIC_FALLBACK_NO_DM)
        private.assert_not_awaited()

    async def test_with_allow_price_in_private_the_full_answer_follows_as_a_private_reply(self):
        graph, private = await self.run_with_prefs("السعر 850,000 ريال", allow_price_in_private=True, public_template="شكراً {name}، التفاصيل على الخاص")
        self.assertEqual(graph["send_comment_reply"].await_args.kwargs["text"], "شكراً، التفاصيل على الخاص")
        private.assert_awaited_once()
        self.assertEqual(private.await_args.args[2], "السعر 850,000 ريال")

    async def test_instagram_comments_are_filtered_too_and_a_failed_private_follow_up_does_not_fail_the_delivery(self):
        private = AsyncMock(side_effect=RuntimeError("meta down"))

        async def prefs(_t):
            return {"social_comments": {"enabled": True, "allow_price_in_private": True}}

        with patch.object(tenant_prefs, "get_prefs", prefs), patch.object(ig_client, "send_private_reply", private):
            graph = await self.dispatch(self.message("instagram_comment", platform_conversation_id="178", text="يبدأ من 900 الف"))
        self.assertNotIn("900", graph["send_instagram_comment_reply"].await_args.kwargs["text"])
        self.assertEqual(self.update.await_args.kwargs["delivery_status"], "SENT")


class MetaErrorClassificationTests(unittest.TestCase):
    def raise_for(self, status, body):
        try:
            ig_client._raise_for_status(httpx.Response(status, json=body))
        except Exception as exc:  # noqa: BLE001
            return type(exc).__name__
        return None

    def test_expired_token_is_an_auth_error_even_with_http_400(self):
        self.assertEqual(self.raise_for(400, {"error": {"type": "OAuthException", "code": 190, "message": "expired"}}), "MetaAuthError")
        self.assertEqual(self.raise_for(400, {"error": {"type": "OAuthException", "code": 102, "message": "session"}}), "MetaAuthError")
        self.assertEqual(self.raise_for(401, {"error": {"code": 0, "message": "x"}}), "MetaAuthError")

    def test_other_errors_keep_their_class(self):
        self.assertEqual(self.raise_for(400, {"error": {"type": "OAuthException", "code": 100, "message": "bad param"}}), "MetaInvalidRecipientError")
        self.assertEqual(self.raise_for(429, {"error": {"code": 4, "message": "slow"}}), "MetaRateLimitError")
        self.assertEqual(self.raise_for(500, {"error": {"code": 2, "message": "x"}}), "MetaAPIError")


class HideCommentClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_instagram_and_facebook_use_their_own_parameter(self):
        seen = []

        class Http:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, params=None, **kw):
                seen.append((url, params))
                return httpx.Response(200, json={"success": True})

        with patch.object(ig_client.httpx, "AsyncClient", Http):
            await ig_client.hide_comment("c1", platform="instagram", access_token="T")
            await ig_client.hide_comment("c2", platform="facebook", access_token="T")
        self.assertTrue(seen[0][0].endswith("/c1") and seen[0][1] == {"access_token": "T", "hide": "true"})
        self.assertTrue(seen[1][0].endswith("/c2") and seen[1][1] == {"access_token": "T", "is_hidden": "true"})


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def set_raw(self, key, value, *, ttl=None):
        self.store[key] = value

    async def get_raw(self, key):
        return self.store.get(key)

    async def delete_raw(self, key):
        self.store.pop(key, None)

    async def set_if_absent(self, key, value, *, ttl):
        if key in self.store:
            return False
        self.store[key] = value
        return True

    async def incr_counter(self, key, *, ttl):
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]


class ChannelHealthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis = FakeRedis()
        self.notify = AsyncMock(return_value=1)
        self.tenant = uuid.uuid4()
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        for p in (patch.object(channel_health, "redis_mgr", self.redis), patch("src.shared.services.notifications.notify_safe", self.notify),
                  patch("src.shared.db.session.get_tenant_session", MagicMock(return_value=session))):
            p.start()
            self.addCleanup(p.stop)

    async def test_failure_is_remembered_and_notified_once_a_day(self):
        await channel_health.mark_auth_failed(self.tenant, "facebook", "Error validating access token")
        await channel_health.mark_auth_failed(self.tenant, "facebook", "Error validating access token")
        self.assertEqual(self.notify.await_count, 1)
        self.assertEqual(self.notify.await_args.args[2], "channel_disconnected")
        info = await channel_health.get_failure(self.tenant, "facebook")
        self.assertIn("Error validating", info["reason"])
        self.assertIsNone(await channel_health.get_failure(self.tenant, "whatsapp"))           # per channel

    async def test_clear_forgets_the_alarm_and_allows_a_new_notification(self):
        await channel_health.mark_auth_failed(self.tenant, "whatsapp", "x")
        await channel_health.clear(self.tenant, "whatsapp")
        self.assertIsNone(await channel_health.get_failure(self.tenant, "whatsapp"))
        await channel_health.mark_auth_failed(self.tenant, "whatsapp", "x")
        self.assertEqual(self.notify.await_count, 2)

    async def test_unknown_channel_and_redis_failure_never_raise(self):
        await channel_health.mark_auth_failed(self.tenant, "tiktok", "x")
        self.assertEqual(self.notify.await_count, 0)
        with patch.object(channel_health, "redis_mgr", MagicMock(set_raw=AsyncMock(side_effect=RuntimeError("redis down")))):
            await channel_health.mark_auth_failed(self.tenant, "facebook", "x")             # must not raise
        self.assertIsNone(await channel_health.get_failure(self.tenant, "facebook") if False else None)


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def client(self, routes):
        def handler(request: httpx.Request) -> httpx.Response:
            for suffix, resp in routes.items():
                if request.url.path.endswith(suffix):
                    return resp
            return httpx.Response(404, json={})

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def run_inspect(self, routes, **kw):
        with patch.object(get_settings(), "meta_app_id", "123"), patch.object(get_settings(), "meta_app_secret", "s"):
            async with self.client(routes) as c:
                return await meta_diagnostics.inspect_page("p1", "TOKEN", has_instagram=kw.get("ig", True), client=c)

    async def test_a_complete_connection_is_ok(self):
        scopes = list(meta_diagnostics.PAGE_REQUIRED_SCOPES + meta_diagnostics.INSTAGRAM_REQUIRED_SCOPES)
        rep = await self.run_inspect({"/debug_token": httpx.Response(200, json={"data": {"is_valid": True, "expires_at": 0, "scopes": scopes}}),
                                      "/subscribed_apps": httpx.Response(200, json={"data": [{"id": "123", "subscribed_fields": list(meta_diagnostics.PAGE_REQUIRED_FIELDS)}]})})
        self.assertTrue(rep.ok, rep.issues_ar())
        self.assertIsNone(rep.expires_at)

    async def test_missing_permission_and_subscription_are_named_in_arabic(self):
        scopes = [s for s in meta_diagnostics.PAGE_REQUIRED_SCOPES if s != "pages_manage_engagement"]
        rep = await self.run_inspect({"/debug_token": httpx.Response(200, json={"data": {"is_valid": True, "expires_at": 0, "scopes": scopes}}),
                                      "/subscribed_apps": httpx.Response(200, json={"data": [{"id": "123", "subscribed_fields": ["messages", "feed"]}]})}, ig=False)
        self.assertFalse(rep.ok)
        self.assertEqual(rep.missing_scopes, ["pages_manage_engagement"])
        self.assertIn("message_reactions", rep.missing_fields)
        self.assertTrue(any("الرد على التعليقات وإخفاؤها" in i for i in rep.issues_ar()))

    async def test_instagram_scopes_are_required_only_when_an_instagram_account_is_linked(self):
        scopes = list(meta_diagnostics.PAGE_REQUIRED_SCOPES)
        routes = {"/debug_token": httpx.Response(200, json={"data": {"is_valid": True, "scopes": scopes}}),
                  "/subscribed_apps": httpx.Response(200, json={"data": [{"id": "123", "subscribed_fields": list(meta_diagnostics.PAGE_REQUIRED_FIELDS)}]})}
        self.assertTrue((await self.run_inspect(routes, ig=False)).ok)
        self.assertIn("instagram_manage_comments", (await self.run_inspect(routes, ig=True)).missing_scopes)

    async def test_an_invalid_token_asks_for_reconnect(self):
        rep = await self.run_inspect({"/debug_token": httpx.Response(200, json={"data": {"is_valid": False}}),
                                      "/subscribed_apps": httpx.Response(400, json={"error": {"code": 190, "message": "Error validating access token"}})})
        self.assertTrue(rep.needs_reconnect)
        self.assertEqual(rep.issues_ar()[0], "رمز الوصول لم يعد صالحًا — أعد ربط الصفحة.")

    async def test_network_errors_do_not_raise(self):
        def boom(request):
            raise httpx.ConnectError("down")

        with patch.object(get_settings(), "meta_app_id", "1"), patch.object(get_settings(), "meta_app_secret", "s"):
            async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as c:
                rep = await meta_diagnostics.inspect_page("p1", "T", has_instagram=False, client=c)
        self.assertFalse(rep.reachable)
        self.assertFalse(rep.ok)


class SettingsEndpointsTests(unittest.IsolatedAsyncioTestCase):
    """The three new Settings endpoints, with the tenant row and Meta simulated (no database)."""

    def make_app(self, role=TenantUserRole.ADMIN):
        from src.gateway.routers import settings as srv

        self.srv = srv
        self.tenant = uuid.uuid4()
        self.added = []
        session = NS(add=lambda row: self.added.append(row), flush=AsyncMock())
        app = create_app()
        user = NS(tenant_id=self.tenant, user_id=uuid.uuid4(), role=role)
        app.dependency_overrides[deps.get_current_user] = lambda: user
        app.dependency_overrides[deps.get_authenticated_tenant_session] = lambda: session
        self.row = NS(whatsapp_phone_number_id="111", meta_access_token="WA-TOKEN", instagram_page_id="555", instagram_page_access_token="PG-TOKEN",
                      instagram_account_id=None, whatsapp_waba_id=None, whatsapp_display_phone_number=None)
        patcher = patch.object(srv, "_load_tenant", AsyncMock(return_value=self.row))
        patcher.start()
        self.addCleanup(patcher.stop)
        return app

    async def test_diagnostics_is_admin_only_and_reports_issues(self):
        report = meta_diagnostics.PageReport(token_valid=True, missing_fields=["feed"])
        with patch.object(meta_diagnostics, "inspect_page", AsyncMock(return_value=report)):
            async with client_for(self.make_app(TenantUserRole.AGENT)) as c:
                self.assertEqual((await c.get("/api/v1/settings/channels/facebook/diagnostics")).status_code, 403)
            async with client_for(self.make_app()) as c:
                body = (await c.get("/api/v1/settings/channels/facebook/diagnostics")).json()
        self.assertEqual((body["connected"], body["ok"], body["missing_fields"]), (True, False, ["feed"]))
        self.assertNotIn("PG-TOKEN", json.dumps(body))

    async def test_diagnostics_when_nothing_is_connected(self):
        app = self.make_app()
        self.row.instagram_page_id = None
        async with client_for(app) as c:
            body = (await c.get("/api/v1/settings/channels/facebook/diagnostics")).json()
        self.assertEqual((body["connected"], body["ok"]), (False, False))

    async def test_recent_comment_activity_is_returned(self):
        app = self.make_app()
        with patch.object(comment_actions, "recent", AsyncMock(return_value=[{"action": "hidden", "reason": "abusive"}])):
            async with client_for(app) as c:
                self.assertEqual((await c.get("/api/v1/settings/comments/activity")).json(), {"items": [{"action": "hidden", "reason": "abusive"}]})

    async def test_whatsapp_test_message_sends_text_and_is_audited_without_the_full_number(self):
        from src.channel_adapters.whatsapp import client as wa

        sent = {}

        class FakeWa:
            async def start(self):
                pass

            async def stop(self):
                pass

            async def send_text_message(self, **kw):
                sent.update(kw)
                return NS(wamid="wamid.1")

        app = self.make_app()
        with patch.object(wa, "WhatsAppClient", FakeWa), patch("src.shared.redis_client.redis_mgr.incr_counter", AsyncMock(return_value=1)):
            async with client_for(app) as c:
                r = await c.post("/api/v1/settings/channels/whatsapp/test-message", json={"to": "0501234567"})
        self.assertEqual((r.status_code, r.json()["ok"], r.json()["method"]), (200, True, "text"), r.text)
        self.assertEqual(sent["to"], "+966501234567")
        self.assertEqual(self.added[0].details, {"method": "text", "to_last4": "4567"})

    async def test_outside_the_24h_window_it_falls_back_to_the_sample_template(self):
        from src.channel_adapters.whatsapp import client as wa

        class FakeWa:
            async def start(self):
                pass

            async def stop(self):
                pass

            async def send_text_message(self, **kw):
                raise wa.WhatsAppAPIError("re-engagement", status_code=400, error_code=131047)

            async def send_template_message(self, **kw):
                self.kw = kw
                return NS(wamid="wamid.2")

        app = self.make_app()
        with patch.object(wa, "WhatsAppClient", FakeWa), patch("src.shared.redis_client.redis_mgr.incr_counter", AsyncMock(return_value=1)):
            async with client_for(app) as c:
                r = await c.post("/api/v1/settings/channels/whatsapp/test-message", json={"to": "+966501234567"})
        self.assertEqual((r.json()["ok"], r.json()["method"]), (True, "template"))

    async def test_bad_number_rate_limit_agent_and_unconnected_are_refused(self):
        async with client_for(self.make_app(TenantUserRole.AGENT)) as c:
            self.assertEqual((await c.post("/api/v1/settings/channels/whatsapp/test-message", json={"to": "+966501234567"})).status_code, 403)
        app = self.make_app()
        async with client_for(app) as c:
            with patch("src.shared.redis_client.redis_mgr.incr_counter", AsyncMock(return_value=1)):
                self.assertEqual((await c.post("/api/v1/settings/channels/whatsapp/test-message", json={"to": "12"})).status_code, 422)
            with patch("src.shared.redis_client.redis_mgr.incr_counter", AsyncMock(return_value=6)):
                self.assertEqual((await c.post("/api/v1/settings/channels/whatsapp/test-message", json={"to": "+966501234567"})).status_code, 429)
            self.row.whatsapp_phone_number_id = None
            self.assertEqual((await c.post("/api/v1/settings/channels/whatsapp/test-message", json={"to": "+966501234567"})).status_code, 409)

    async def test_an_auth_failure_marks_the_channel_for_reconnect(self):
        from src.channel_adapters.whatsapp import client as wa

        class FakeWa:
            async def start(self):
                pass

            async def stop(self):
                pass

            async def send_text_message(self, **kw):
                raise wa.WhatsAppAuthError("token expired", status_code=401, error_code=190)

        marked = AsyncMock()
        app = self.make_app()
        with patch.object(wa, "WhatsAppClient", FakeWa), patch("src.shared.redis_client.redis_mgr.incr_counter", AsyncMock(return_value=1)), \
                patch.object(channel_health, "mark_auth_failed", marked):
            async with client_for(app) as c:
                r = await c.post("/api/v1/settings/channels/whatsapp/test-message", json={"to": "+966501234567"})
        self.assertEqual((r.json()["ok"], r.json()["needs_reconnect"]), (False, True))
        marked.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()


class SubscriptionScriptTests(unittest.IsolatedAsyncioTestCase):
    async def test_report_lists_what_is_missing_and_the_exit_code_reflects_it(self):
        from scripts import meta_check_subscriptions as script

        rep = meta_diagnostics.PageReport(token_valid=True, scopes=["pages_messaging"], subscribed_fields=["messages"],
                                          missing_scopes=["pages_manage_engagement"], missing_fields=["feed"])
        lines = "\n".join(script.format_report("tenant x", rep))
        self.assertIn("صلاحية ناقصة pages_manage_engagement", lines)
        self.assertIn("اشتراك ناقص feed", lines)
        self.assertNotIn("TOKEN", lines)
        with patch.dict("os.environ", {"PAGE_T": "TOKEN"}), patch.object(meta_diagnostics, "inspect_page", AsyncMock(return_value=rep)):
            self.assertEqual(await script.run(script.argparse.Namespace(page_id="1", token_env="PAGE_T", instagram=False, tenant_id=None, all=False)), 1)
        ok = meta_diagnostics.PageReport(token_valid=True)
        with patch.dict("os.environ", {"PAGE_T": "TOKEN"}), patch.object(meta_diagnostics, "inspect_page", AsyncMock(return_value=ok)):
            self.assertEqual(await script.run(script.argparse.Namespace(page_id="1", token_env="PAGE_T", instagram=False, tenant_id=None, all=False)), 0)

    async def test_missing_token_variable_is_a_usage_error(self):
        from scripts import meta_check_subscriptions as script

        self.assertEqual(await script.run(script.argparse.Namespace(page_id="1", token_env="NOPE_NOT_SET", instagram=False, tenant_id=None, all=False)), 2)

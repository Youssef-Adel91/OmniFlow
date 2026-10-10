"""Settings redesign: business hours, human handoff, AI behaviour, identity, channels, bot test.

Pure tests run everywhere; the DB class needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL (see test_customers.py).
"""
import os
import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

from src.gateway.main import create_app
from src.shared.core.enums import TenantUserRole
from src.shared.services import tenant_prefs as tp
from tests.test_property_import_flow import auth_overrides, client_for

DB_URL = os.environ.get("OMNIFLOW_TEST_DATABASE_URL")
RLS_URL = os.environ.get("OMNIFLOW_TEST_RLS_URL")
BASE = "/api/v1/settings"
UTC = timezone.utc


def at_riyadh(y, mo, d, h, mi):
    """Build a UTC datetime from a Riyadh wall-clock time (UTC+3, no DST)."""
    return datetime(y, mo, d, h - 3, mi, tzinfo=UTC) if h >= 3 else datetime(y, mo, d - 1, h + 21, mi, tzinfo=UTC)


class PureTests(unittest.TestCase):
    def test_defaults_are_conservative_and_validate(self):
        d = tp.all_defaults()
        self.assertEqual((d["business_hours"]["enabled"], d["handoff"]["auto_escalate"], d["handoff"]["sla_minutes"]), (False, True, 15))
        for s in tp.SECTIONS:
            tp.validate_section(s, d[s])                       # a default must always round-trip

    def test_validation_rejects_bad_input_with_clear_errors(self):
        bad = [
            ("business_hours", {"timezone": "Mars/Base"}), ("business_hours", {"days": {"mon": [["17:00", "09:00"]]}}),
            ("business_hours", {"days": {"mon": [["09:00", "13:00"], ["12:00", "15:00"]]}}), ("business_hours", {"days": {"xxx": []}}),
            ("business_hours", {"enabled": True, "days": {}}), ("business_hours", {"days": {"mon": [["9:00", "17:00"]]}}),
            ("handoff", {"sla_minutes": 0}), ("handoff", {"keywords": ["x" * 41]}), ("handoff", {"nonsense": 1}),
            ("ai_behavior", {"tone": "angry"}), ("ai_behavior", {"quick_replies": ["x"] * 9}), ("identity", {"currency": "BTC"}),
        ]
        for section, payload in bad:
            with self.assertRaises(ValueError, msg=f"{section} {payload}"):
                tp.validate_section(section, payload)

    def test_open_hours_use_the_tenant_timezone_and_sunday_to_thursday_by_default(self):
        cfg = tp.validate_section("business_hours", {"enabled": True})
        self.assertTrue(tp.is_open(cfg, at_riyadh(2026, 10, 15, 11, 0)))            # Thursday 11:00
        self.assertFalse(tp.is_open(cfg, at_riyadh(2026, 10, 15, 17, 0)))           # closes at 17:00 sharp
        self.assertFalse(tp.is_open(cfg, at_riyadh(2026, 10, 16, 11, 0)))           # Friday
        self.assertTrue(tp.is_open(tp.validate_section("business_hours", {}), at_riyadh(2026, 10, 16, 3, 0)))   # hours disabled = always open
        self.assertEqual(tp.describe_next_open(cfg, at_riyadh(2026, 10, 15, 20, 0)), "يوم الأحد الساعة 09:00")
        self.assertEqual(tp.describe_next_open(cfg, at_riyadh(2026, 10, 14, 21, 0)), "غدًا الساعة 09:00")
        self.assertEqual(tp.describe_next_open(cfg, at_riyadh(2026, 10, 14, 7, 0)), "اليوم الساعة 09:00")
        ny = tp.validate_section("business_hours", {"enabled": True, "timezone": "America/New_York", "days": {"mon": [["09:00", "17:00"]]}})
        self.assertTrue(tp.is_open(ny, datetime(2026, 10, 12, 15, 0, tzinfo=UTC)))     # Monday 11:00 New York (EDT)

    def test_split_shifts(self):
        cfg = tp.validate_section("business_hours", {"enabled": True, "days": {"sun": [["09:00", "13:00"], ["16:00", "20:00"]]}})
        self.assertEqual([tp.is_open(cfg, at_riyadh(2026, 10, 11, h, 0)) for h in (10, 14, 17, 21)], [True, False, True, False])
        self.assertEqual(tp.describe_next_open(cfg, at_riyadh(2026, 10, 11, 14, 0)), "اليوم الساعة 16:00")

    def test_handoff_keywords_match_whole_words_only(self):
        kws = tp.DEFAULT_KEYWORDS
        self.assertEqual(tp.match_handoff_keyword("ابغى اكلم موظف لو سمحت", kws), "اكلم موظف")
        self.assertEqual(tp.match_handoff_keyword("Please connect me to Customer Service", kws), "customer service")
        for no in ("هل عندكم مدير مبيعات؟", "الموظفين كثر", "", None, "السعر كم؟"):
            self.assertIsNone(tp.match_handoff_keyword(no, kws), no)
        self.assertEqual(tp.match_handoff_keyword("كلمني على الواتس", ["كلمني"]), "كلمني")
        self.assertIsNone(tp.match_handoff_keyword("anything", []))

    def test_behavior_block_reflects_the_choices(self):
        b = tp.behavior_block({"tone": "formal", "dialect": "msa", "language": "en", "welcome_message": "أهلاً بك في عقاري",
                               "constraints": {"no_competitor_mentions": True, "ask_budget_early": True, "no_price_without_inventory": False}},
                              {"currency": "AED"})
        for needle in ("رسمي", "الفصحى", "English", "الدرهم الإماراتي", "منافسة", "ميزانيته", "أهلاً بك في عقاري"):
            self.assertIn(needle, b)
        self.assertNotIn("لا تذكر سعرًا", b)
        self.assertIn("لا تقدّم نصيحة", b)                                            # default-on constraint stays on

    def test_corrupt_stored_rows_fall_back_to_defaults(self):
        self.assertEqual(tp.with_defaults("handoff", {"sla_minutes": "garbage"}), tp.Handoff().model_dump())
        self.assertEqual(tp.with_defaults("identity", None)["currency"], "SAR")


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._b = status, body

    def json(self):
        return self._b


class FakeHttp:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        self.calls.append((url, kw))
        return self.resp


@unittest.skipUnless(DB_URL and RLS_URL, "needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL (non-superuser: RLS is the tenant guard)")
class SettingsDbTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        self.text = text
        self.admin = create_async_engine(DB_URL)
        self.app_engine = create_async_engine(RLS_URL)
        self.Admin = async_sessionmaker(self.admin, expire_on_commit=False)
        self.App = async_sessionmaker(self.app_engine, expire_on_commit=False)
        self.tenants = [uuid.uuid4(), uuid.uuid4()]
        async with self.Admin() as s:
            for i, t in enumerate(self.tenants):
                await s.execute(text(
                    "INSERT INTO tenants (tenant_id,business_name,fal_license_number,subscription_tier,status,max_ai_conversations,onboarding_status,created_at,updated_at) "
                    "VALUES (:t,:n,:f,'economic','active',100,'completed',now(),now())"), {"t": t, "n": f"T{i}", "f": f"FAL-{t.hex[:8]}"})
            await s.commit()
        self.patches = [patch("src.shared.db.session.AsyncSessionFactory", self.App)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        tp._CACHE.clear()
        self.app = create_app()
        auth_overrides(self.app, self.tenants[0], self.App)

    async def asyncTearDown(self):
        async with self.Admin() as s:
            for t in self.tenants:
                for stmt in ("DELETE FROM audit_logs WHERE tenant_id=:t", "DELETE FROM tenant_preferences WHERE tenant_id=:t",
                             "DELETE FROM messages WHERE conversation_id IN (SELECT conversation_id FROM conversations WHERE tenant_id=:t)",
                             "DELETE FROM conversations WHERE tenant_id=:t", "DELETE FROM customers WHERE tenant_id=:t",
                             "DELETE FROM tenant_users WHERE tenant_id=:t", "DELETE FROM tenants WHERE tenant_id=:t"):
                    await s.execute(self.text(stmt), {"t": t})
            await s.commit()
        await self.admin.dispose()
        await self.app_engine.dispose()

    def app_as(self, role, tenant=None):
        app = create_app()
        auth_overrides(app, tenant or self.tenants[0], self.App, role=role)
        return app

    # preferences ---------------------------------------------------------------------------------------
    async def test_preferences_roundtrip_defaults_validation_roles_and_audit(self):
        async with client_for(self.app) as c:
            r = (await c.get(f"{BASE}/preferences")).json()
            self.assertEqual((r["preferences"]["handoff"]["sla_minutes"], r["configured"]["handoff"]), (15, False))
            hours = {"enabled": True, "timezone": "Asia/Riyadh", "days": {"sun": [["08:00", "16:00"]]}, "out_of_hours_message": "نردّ {next_open}"}
            ok = await c.put(f"{BASE}/preferences/business_hours", json=hours)
            self.assertEqual(ok.status_code, 200, ok.text)
            self.assertEqual(ok.json()["value"]["days"], {"sun": [["08:00", "16:00"]]})
            bad = await c.put(f"{BASE}/preferences/business_hours", json={"timezone": "Nope/Nope"})
            self.assertEqual((bad.status_code, bad.json()["detail"]["code"]), (422, "INVALID_PREFERENCES"))
            self.assertIn("منطقة زمنية", bad.json()["detail"]["message"])
            self.assertEqual((await c.put(f"{BASE}/preferences/unknown", json={})).status_code, 404)
            self.assertEqual((await c.put(f"{BASE}/preferences/handoff", json={"sla_minutes": 30, "keywords": ["اكلم موظف"]})).status_code, 200)
            again = (await c.get(f"{BASE}/preferences")).json()
            self.assertEqual((again["preferences"]["business_hours"]["enabled"], again["preferences"]["handoff"]["sla_minutes"], again["configured"]["identity"]), (True, 30, False))
        async with client_for(self.app_as(TenantUserRole.AGENT)) as c:
            self.assertEqual((await c.put(f"{BASE}/preferences/identity", json={"currency": "USD"})).status_code, 403)
            self.assertEqual((await c.get(f"{BASE}/preferences")).status_code, 200)
        async with client_for(self.app_as(TenantUserRole.ADMIN, self.tenants[1])) as c2:
            self.assertEqual((await c2.get(f"{BASE}/preferences")).json()["configured"]["business_hours"], False)     # tenant isolation
        async with self.Admin() as s:
            acts = [r[0] for r in await s.execute(self.text("SELECT action FROM audit_logs WHERE tenant_id=:t ORDER BY created_at, audit_id"), {"t": self.tenants[0]})]
        self.assertEqual(sorted(acts), ["settings.business_hours_updated", "settings.handoff_updated"])

    async def test_dashboard_sla_default_comes_from_the_handoff_preference(self):
        from src.gateway.routers import dashboard as dash

        captured = {}

        async def fake_build(session, tenant_id, window, channel, sla):
            captured["sla"] = sla
            return {"ok": True}

        async with client_for(self.app) as c:
            await c.put(f"{BASE}/preferences/handoff", json={"sla_minutes": 7})
            with patch.object(dash, "build_analytics", fake_build), patch.object(dash.redis_mgr, "get_raw", AsyncMock(return_value=None)), \
                 patch.object(dash.redis_mgr, "set_raw", AsyncMock()):
                tp._CACHE.clear()
                await c.get("/api/v1/dashboard/analytics")
                self.assertEqual(captured["sla"], 7)
                await c.get("/api/v1/dashboard/analytics", params={"sla_minutes": 30})
                self.assertEqual(captured["sla"], 30)                                                                 # explicit wins

    async def test_persona_gets_the_behaviour_block_only_when_the_tenant_saved_it(self):
        from src.ai_engine import company_context as cc

        async with self.Admin() as s:
            await s.execute(self.text("UPDATE tenants SET ai_system_prompt='أنت مساعد عقاري' WHERE tenant_id=:t"), {"t": self.tenants[0]})
            await s.commit()
        cc.invalidate_company_context(self.tenants[0])
        self.assertEqual(await cc.get_tenant_persona(self.tenants[0]), "أنت مساعد عقاري")                          # untouched tenant: unchanged
        async with client_for(self.app) as c:
            await c.put(f"{BASE}/preferences/ai_behavior", json={"tone": "formal", "dialect": "msa"})
            await c.put(f"{BASE}/preferences/identity", json={"currency": "AED"})
        persona = await cc.get_tenant_persona(self.tenants[0])                                                       # cache dropped by the PUT
        self.assertTrue(persona.startswith("أنت مساعد عقاري"))
        for needle in ("رسمي", "الفصحى", "الدرهم الإماراتي"):
            self.assertIn(needle, persona)

    # channels ---------------------------------------------------------------------------------------------
    async def test_channels_status_issues_and_last_inbound(self):
        async with self.Admin() as s:
            await s.execute(self.text("UPDATE tenants SET whatsapp_phone_number_id='111', meta_access_token='EAAxxxxsecret1234', instagram_page_id='555', instagram_page_access_token='tok' WHERE tenant_id=:t"), {"t": self.tenants[0]})
            cu, conv = uuid.uuid4(), uuid.uuid4()
            await s.execute(self.text("INSERT INTO customers (customer_id,tenant_id,unified_phone,source,channel_user_ids,tags,vcard_state,marketing_opt_in,is_vip,is_processing_restricted,created_at,updated_at) VALUES (:c,:t,'+966500000001','whatsapp','{}','[]','STATE_NEW',false,false,false,now(),now())"), {"c": cu, "t": self.tenants[0]})
            await s.execute(self.text("INSERT INTO conversations (conversation_id,tenant_id,customer_id,channel,status,message_count,ai_reply_epoch,created_at,updated_at) VALUES (:v,:t,:c,'instagram','ai_active',0,0,now(),now())"), {"v": conv, "t": self.tenants[0], "c": cu})
            await s.execute(self.text("INSERT INTO messages (message_id,conversation_id,sender_type,message_type,text_content,delivery_status,created_at,updated_at) VALUES (:m,:v,'customer','text','hi','SENT',now(),now())"), {"m": uuid.uuid4(), "v": conv})
            await s.commit()
        async with client_for(self.app) as c:
            r = (await c.get(f"{BASE}/channels")).json()
        wa, fb = r["whatsapp"], r["facebook"]
        self.assertEqual((wa["connected"], wa["token"], wa["templates_ready"], wa["last_inbound_at"]), (True, "***1234", False, None))
        self.assertTrue(any("WABA" in i for i in wa["issues"]))
        self.assertEqual((fb["connected"], fb["messenger"], fb["instagram"]), (True, True, False))
        self.assertIsNotNone(fb["last_inbound_at"])
        self.assertTrue(any("إنستجرام" in i for i in fb["issues"]))
        self.assertNotIn("EAAxxxxsecret1234", str(r))                                                              # never leak the token

    async def test_channel_health_check_maps_meta_answers(self):
        from src.gateway.routers import settings as srv

        async with self.Admin() as s:
            await s.execute(self.text("UPDATE tenants SET whatsapp_phone_number_id='111', meta_access_token='tok' WHERE tenant_id=:t"), {"t": self.tenants[0]})
            await s.commit()
        async with client_for(self.app) as c:
            self.assertEqual((await c.post(f"{BASE}/channels/facebook/check")).json()["ok"], False)                  # not connected
            self.assertEqual((await c.post(f"{BASE}/channels/tiktok/check")).status_code, 404)
            http = FakeHttp(FakeResp(200, {"display_phone_number": "+966 50 123 4567", "verified_name": "عقاري"}))
            with patch.object(srv.httpx, "AsyncClient", MagicMock(return_value=http)):
                ok = (await c.post(f"{BASE}/channels/whatsapp/check")).json()
            self.assertEqual((ok["ok"], ok["info"]["verified_name"]), (True, "عقاري"))
            self.assertEqual(http.calls[0][1]["headers"], {"Authorization": "Bearer tok"})                           # token in header, never in the URL
            self.assertNotIn("tok", http.calls[0][0])
            http = FakeHttp(FakeResp(400, {"error": {"code": 190, "message": "expired"}}))
            with patch.object(srv.httpx, "AsyncClient", MagicMock(return_value=http)):
                bad = (await c.post(f"{BASE}/channels/whatsapp/check")).json()
            self.assertEqual((bad["ok"], bad["needs_reconnect"]), (False, True))

    async def test_facebook_disconnect_is_admin_only_and_audited(self):
        async with self.Admin() as s:
            await s.execute(self.text("UPDATE tenants SET instagram_page_id='555', instagram_page_access_token='tok', instagram_account_id='9' WHERE tenant_id=:t"), {"t": self.tenants[0]})
            await s.commit()
        async with client_for(self.app_as(TenantUserRole.AGENT)) as c:
            self.assertEqual((await c.post(f"{BASE}/channels/facebook/disconnect")).status_code, 403)
        async with client_for(self.app) as c:
            self.assertEqual((await c.post(f"{BASE}/channels/facebook/disconnect")).json()["connected"], False)
            self.assertEqual((await c.get(f"{BASE}/channels")).json()["facebook"]["connected"], False)
        async with self.Admin() as s:
            row = (await s.execute(self.text("SELECT instagram_page_id, instagram_page_access_token, instagram_account_id FROM tenants WHERE tenant_id=:t"), {"t": self.tenants[0]})).one()
            self.assertEqual(tuple(row), (None, None, None))
            self.assertEqual((await s.scalar(self.text("SELECT count(*) FROM audit_logs WHERE tenant_id=:t AND action='channel.facebook_disconnected'"), {"t": self.tenants[0]})), 1)

    # bot test ----------------------------------------------------------------------------------------------------
    async def test_ai_test_uses_the_tenant_persona_and_never_for_auditors(self):
        from src.ai_workers.llm_invoker import client as llm

        seen = {}

        async def fake_generate(**kw):
            seen.update(kw)
            return NS(text="أهلاً! كيف أخدمك؟", model_used="fake-model")

        async with self.Admin() as s:
            await s.execute(self.text("UPDATE tenants SET ai_system_prompt='أنت مساعد شركة نجد العقارية' WHERE tenant_id=:t"), {"t": self.tenants[0]})
            await s.commit()
        async with client_for(self.app) as c:
            with patch.object(llm.gemini_client, "generate_response", fake_generate), patch.object(llm.gemini_client, "_configured", True, create=True):
                r = await c.post(f"{BASE}/ai/test", json={"message": "عندكم فلل؟"})
            self.assertEqual((r.status_code, r.json()["reply"], r.json()["model"]), (200, "أهلاً! كيف أخدمك؟", "fake-model"))
            self.assertIn("شركة نجد العقارية", seen["system_prompt"])
            self.assertEqual(seen["messages"][0]["parts"], ["عندكم فلل؟"])
            with patch.object(llm.gemini_client, "generate_response", AsyncMock(side_effect=RuntimeError("no key"))), patch.object(llm.gemini_client, "_configured", True, create=True):
                bad = await c.post(f"{BASE}/ai/test", json={"message": "x"})
            self.assertEqual((bad.status_code, bad.json()["detail"]["code"]), (502, "AI_UNAVAILABLE"))
            self.assertEqual((await c.post(f"{BASE}/ai/test", json={"message": ""})).status_code, 422)
        async with client_for(self.app_as(TenantUserRole.AUDITOR)) as c:
            self.assertEqual((await c.post(f"{BASE}/ai/test", json={"message": "x"})).status_code, 403)

    # handoff ---------------------------------------------------------------------------------------------------------
    async def seed_agents(self):
        ids = {}
        async with self.Admin() as s:
            for name, role in (("a1", "agent"), ("a2", "agent"), ("boss", "admin")):
                uid = uuid.uuid4()
                ids[name] = uid
                await s.execute(self.text("INSERT INTO tenant_users (user_id,tenant_id,full_name,email,hashed_password,role,is_active,created_at,updated_at) VALUES (:u,:t,:n,:e,'x',:r,true,now(),now())"),
                                {"u": uid, "t": self.tenants[0], "n": name, "e": f"{name}-{uid.hex[:6]}@x.sa", "r": role})
            await s.commit()
        return ids

    async def new_conversation(self, status="ai_active", restricted=False):
        cu, conv = uuid.uuid4(), uuid.uuid4()
        async with self.Admin() as s:
            await s.execute(self.text("INSERT INTO customers (customer_id,tenant_id,unified_phone,source,channel_user_ids,tags,vcard_state,marketing_opt_in,is_vip,is_processing_restricted,created_at,updated_at) VALUES (:c,:t,:p,'whatsapp','{}','[]','STATE_NEW',false,false,:r,now(),now())"),
                            {"c": cu, "t": self.tenants[0], "p": f"+9665{uuid.uuid4().int % 10**8:08d}", "r": restricted})
            await s.execute(self.text("INSERT INTO conversations (conversation_id,tenant_id,customer_id,channel,status,message_count,ai_reply_epoch,created_at,updated_at) VALUES (:v,:t,:c,'whatsapp',:s,0,0,now(),now())"),
                            {"v": conv, "t": self.tenants[0], "c": cu, "s": status})
            await s.commit()
        return conv

    async def conv_row(self, conv):
        async with self.Admin() as s:
            return (await s.execute(self.text("SELECT status, assigned_agent_id FROM conversations WHERE conversation_id=:c"), {"c": conv})).one()

    async def test_handoff_escalates_assigns_round_robin_and_picks_the_right_message(self):
        from src.shared.services.handoff import perform_handoff

        agents = await self.seed_agents()
        async with client_for(self.app) as c:
            await c.put(f"{BASE}/preferences/business_hours", json={"enabled": True, "days": {"sun": [["09:00", "17:00"]]}, "out_of_hours_message": "خارج الدوام، نردّ {next_open}"})
            await c.put(f"{BASE}/preferences/handoff", json={"handoff_message": "حولناك لموظف", "round_robin": True})
        tp._CACHE.clear()
        open_time, closed_time = at_riyadh(2026, 10, 11, 10, 0), at_riyadh(2026, 10, 11, 20, 0)        # Sunday 10:00 / 20:00 Riyadh
        c1, c2, c3 = await self.new_conversation(), await self.new_conversation(), await self.new_conversation()
        r1 = await perform_handoff(self.tenants[0], c1, reason="tenant_handoff_keyword:اكلم موظف", now=open_time)
        r2 = await perform_handoff(self.tenants[0], c2, reason="low_confidence_escalation (0.31)", now=closed_time)
        r3 = await perform_handoff(self.tenants[0], c3, reason="x", now=open_time)
        self.assertEqual((r1.applied, r1.text, r1.within_hours), (True, "حولناك لموظف", True))
        self.assertEqual((r2.applied, r2.text, r2.within_hours), (True, "خارج الدوام، نردّ يوم الأحد الساعة 09:00", False))   # only Sunday is open: next opening is next Sunday
        assigned = [r1.agent_id, r2.agent_id, r3.agent_id]
        self.assertTrue(all(a in (agents["a1"], agents["a2"]) for a in assigned))                           # agents first, admins only as a fallback
        self.assertEqual({assigned[0], assigned[1]}, {agents["a1"], agents["a2"]})                         # spread, not piled on one agent
        self.assertEqual(tuple((await self.conv_row(c1))), ("escalated", r1.agent_id))
        again = await perform_handoff(self.tenants[0], c1, reason="x", now=open_time)                       # already escalated: idempotent
        self.assertFalse(again.applied)

    async def test_handoff_variants_pdpl_silent_disabled_and_admin_fallback(self):
        from src.shared.services.handoff import perform_handoff

        conv = await self.new_conversation()
        silent = await perform_handoff(self.tenants[0], conv, reason="pdpl_processing_restricted")          # no agents at all in this tenant
        self.assertEqual((silent.applied, silent.text, silent.agent_id), (True, None, None))
        self.assertEqual((await self.conv_row(conv))[0], "escalated")
        human = await self.new_conversation(status="human_active")
        self.assertFalse((await perform_handoff(self.tenants[0], human, reason="x")).applied)               # a person already owns it
        async with client_for(self.app) as c:
            await c.put(f"{BASE}/preferences/handoff", json={"auto_escalate": False})
        tp._CACHE.clear()
        off = await self.new_conversation()
        self.assertFalse((await perform_handoff(self.tenants[0], off, reason="x")).applied)
        self.assertEqual((await self.conv_row(off))[0], "ai_active")                                        # legacy behaviour preserved when disabled
        ids = await self.seed_agents()
        async with self.Admin() as s:
            await s.execute(self.text("UPDATE tenant_users SET is_active=false WHERE user_id IN (:a,:b)"), {"a": ids["a1"], "b": ids["a2"]})
            await s.commit()
        async with client_for(self.app) as c:
            await c.put(f"{BASE}/preferences/handoff", json={"auto_escalate": True})
        tp._CACHE.clear()
        fb = await self.new_conversation()
        self.assertEqual((await perform_handoff(self.tenants[0], fb, reason="x")).agent_id, ids["boss"])     # inactive agents skipped -> admin fallback


if __name__ == "__main__":
    unittest.main()

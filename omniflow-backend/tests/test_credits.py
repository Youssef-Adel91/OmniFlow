"""P10-B/C: the hard AI-reply cap (atomic, before the LLM call), top-up packs, add-ons, auto-recharge, real AI cost tracking.

Needs PostgreSQL (OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL) AND a reachable Redis with the configured password — the
atomicity claim is only meaningful against a real Redis running the Lua script, so these tests never mock the counters.
"""
import asyncio
import json
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from src.gateway.main import create_app
from src.shared.core.config import get_settings
from src.shared.core.enums import TenantUserRole
from src.shared.redis_client import redis_mgr
from src.shared.services.billing import credits, entitlements, llm_cost, providers, service, usage
from tests.test_billing import FakeProvider
from tests.test_property_import_flow import auth_overrides, client_for

DB_URL = os.environ.get("OMNIFLOW_TEST_DATABASE_URL")
RLS_URL = os.environ.get("OMNIFLOW_TEST_RLS_URL")
BASE = "/api/v1/billing"
UTC = timezone.utc


class PureCostTests(unittest.TestCase):
    def test_default_cost_when_no_price_or_no_tokens(self):
        with patch.object(get_settings(), "llm_prices_json", ""):
            self.assertEqual(llm_cost.compute("gpt-x", 1000, 500), (Decimal("0.020000"), "default"))
        with patch.object(get_settings(), "llm_prices_json", json.dumps({"m": {"in_per_1k": 0.01, "out_per_1k": 0.04}})):
            self.assertEqual(llm_cost.compute("m", 0, 0)[1], "default")                  # nothing measured → assumed
            self.assertEqual(llm_cost.compute("other", 100, 100)[1], "default")           # unknown model → assumed

    def test_measured_cost_uses_configured_prices(self):
        with patch.object(get_settings(), "llm_prices_json", json.dumps({"m": {"in_per_1k": 0.01, "out_per_1k": 0.04}})):
            cost, src = llm_cost.compute("m", 2000, 500)
            self.assertEqual((cost, src), (Decimal("0.040000"), "measured"))              # 2*0.01 + 0.5*0.04

    def test_broken_price_config_degrades_to_default_instead_of_raising(self):
        with patch.object(get_settings(), "llm_prices_json", "{not json"):
            self.assertEqual(llm_cost.compute("m", 10, 10)[1], "default")
        with patch.object(get_settings(), "llm_prices_json", json.dumps({"m": {"in_per_1k": "x"}})):
            self.assertEqual(llm_cost.compute("m", 10, 10)[1], "default")

    def test_month_bounds_are_riyadh_calendar_months(self):
        s, e = llm_cost.month_bounds("2026-12")
        self.assertEqual((s, e), (datetime(2026, 11, 30, 21, 0, tzinfo=UTC), datetime(2026, 12, 31, 21, 0, tzinfo=UTC)))


@unittest.skipUnless(DB_URL and RLS_URL, "needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL (non-superuser: RLS is the tenant guard)")
class CreditsBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        await redis_mgr.start()
        if not await redis_mgr.ping():
            await redis_mgr.stop()
            self.skipTest("Redis not reachable")
        self.text = text
        self.admin = create_async_engine(DB_URL)
        self.app_engine = create_async_engine(RLS_URL)
        self.Admin = async_sessionmaker(self.admin, expire_on_commit=False)
        self.App = async_sessionmaker(self.app_engine, expire_on_commit=False)
        self.tenants = [uuid.uuid4(), uuid.uuid4()]
        async with self.Admin() as s:
            await s.execute(text("DELETE FROM plans WHERE code='ctest'"))
            await s.execute(text("INSERT INTO plans (code,name_ar,price_monthly_halalas,price_yearly_halalas,limits,is_placeholder) "
                                 "VALUES ('ctest','x',50000,500000,CAST(:l AS jsonb),false)"),
                            {"l": json.dumps({"messages_per_month": 50, "seats": 2, "properties": 5})})
            for i, t in enumerate(self.tenants):
                await s.execute(text(
                    "INSERT INTO tenants (tenant_id,business_name,fal_license_number,subscription_tier,status,max_ai_conversations,onboarding_status,created_at,updated_at) "
                    "VALUES (:t,:n,:f,'starter','active',100,'completed',now(),now())"), {"t": t, "n": f"شركة {i}", "f": f"FAL-{t.hex[:8]}"})
                await s.execute(text("INSERT INTO subscriptions (tenant_id,plan_code,status,is_exempt,billing_interval,current_period_start,current_period_end,provider) "
                                     "VALUES (:t,'ctest','active',false,'monthly',now()-interval '10 days',now()+interval '20 days','moyasar')"), {"t": t})
            await s.commit()
        self.fake = FakeProvider()
        providers.set_provider_for_tests(self.fake)
        self.addCleanup(providers.set_provider_for_tests, None)
        cfg = get_settings()
        for name, value in (("billing_gate_enabled", True), ("billing_enforce_limits", True), ("billing_enforce_message_limit", True),
                            ("moyasar_secret_key", "sk_test_x"), ("app_env", "development"), ("billing_seller_name", "OmniFlow للتقنية"),   # pragma: allowlist secret
                            ("llm_prices_json", "")):
            p = patch.object(cfg, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch("src.shared.db.session.AsyncSessionFactory", self.App)
        p.start()
        self.addCleanup(p.stop)
        await self._clear_redis()

    async def _clear_redis(self):
        for t in self.tenants:
            for k in (f"usage:{t}:{usage.period_of()}", f"credits:{t}", f"credits_used:{t}", f"l3:{t}:{usage.period_of()}",
                      f"quota_exhausted_alert:{t}:{usage.period_of()}", f"auto_topup_lock:{t}", f"billing:ent:{t}"):
                await redis_mgr.delete_raw(k)

    async def asyncTearDown(self):
        await self._clear_redis()
        async with self.Admin() as s:
            for t in self.tenants:
                for stmt in ("DELETE FROM llm_usage_daily WHERE tenant_id=:t", "DELETE FROM llm_usage WHERE tenant_id=:t",
                             "DELETE FROM reply_credits WHERE tenant_id=:t", "DELETE FROM notifications WHERE tenant_id=:t",
                             "DELETE FROM usage_counters WHERE tenant_id=:t", "DELETE FROM payment_events WHERE tenant_id=:t",
                             "DELETE FROM invoices WHERE tenant_id=:t", "DELETE FROM transactions WHERE tenant_id=:t",
                             "DELETE FROM audit_logs WHERE tenant_id=:t", "DELETE FROM tenant_users WHERE tenant_id=:t",
                             "DELETE FROM subscriptions WHERE tenant_id=:t", "DELETE FROM tenants WHERE tenant_id=:t"):
                    await s.execute(self.text(stmt), {"t": t})
            await s.execute(self.text("DELETE FROM plans WHERE code='ctest'"))
            await s.commit()
        await self.admin.dispose()
        await self.app_engine.dispose()
        await redis_mgr.stop()

    # ── helpers ─────────────────────────────────────────────────────────────────────
    def app_as(self, role=TenantUserRole.ADMIN, tenant=None):
        app = create_app()
        auth_overrides(app, tenant or self.tenants[0], self.App, role=role)
        return app

    async def rows(self, sql, **p):
        async with self.Admin() as s:
            return (await s.execute(self.text(sql), p)).mappings().all()

    async def grant(self, tenant, replies, days=365):
        async with self.Admin() as s:
            await s.execute(self.text("INSERT INTO reply_credits (tenant_id,pack_code,replies_total,replies_remaining,source,expires_at) "
                                      "VALUES (:t,'replies_5k',:n,:n,'manual', now()+ (:d || ' days')::interval)"), {"t": tenant, "n": replies, "d": str(days)})
            await s.commit()

    async def snap(self, tenant=None):
        await entitlements.invalidate(tenant or self.tenants[0])
        return await entitlements.snapshot(tenant or self.tenants[0])

    async def hook(self, c, event_id, pid, **kw):
        return await c.post("/api/v1/webhooks/payments", json=self.fake.hook(event_id, pid, **kw))



@unittest.skipUnless(DB_URL and RLS_URL, "needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL (non-superuser: RLS is the tenant guard)")
class CreditsDbTests(CreditsBase):
    # ── the hard cap ────────────────────────────────────────────────────────────────
    async def test_two_hundred_concurrent_replies_never_exceed_the_plan_quota(self):
        t = self.tenants[0]
        snap = await self.snap()
        self.assertEqual(snap["limits"]["messages_per_month"], 50)
        results = await asyncio.gather(*[credits.reserve(t, snap) for _ in range(200)])
        self.assertEqual(sum(r.allowed for r in results), 50)                          # exactly the quota, not 51
        self.assertEqual({r.source for r in results if r.allowed}, {"plan"})
        self.assertEqual({r.source for r in results if not r.allowed}, {"exhausted"})
        self.assertEqual(await credits.total_used(t), 50)                              # denied attempts leave no trace in the counter

    async def test_topup_credits_are_spent_only_after_the_plan_quota_and_never_overspent(self):
        t = self.tenants[0]
        await self.grant(t, 30)
        snap = await self.snap()
        res = await asyncio.gather(*[credits.reserve(t, snap) for _ in range(200)])
        self.assertEqual(sum(r.allowed for r in res), 80)                              # 50 plan + 30 top-up
        self.assertEqual((sum(r.source == "plan" for r in res), sum(r.source == "topup" for r in res)), (50, 30))
        self.assertEqual(int(await redis_mgr.get_raw(f"credits_used:{t}")), 30)
        self.assertEqual(await credits.flush_consumption(), 30)
        self.assertEqual((await self.rows("SELECT replies_remaining FROM reply_credits WHERE tenant_id=:t", t=t))[0]["replies_remaining"], 0)
        self.assertFalse((await credits.reserve(t, snap)).allowed)                     # nothing left anywhere

    async def test_release_returns_the_reservation_when_the_llm_call_failed(self):
        t = self.tenants[0]
        await self.grant(t, 2)
        snap = await self.snap()
        for _ in range(50):
            await credits.reserve(t, snap)
        r = await credits.reserve(t, snap)
        self.assertEqual(r.source, "topup")
        await credits.release(t, r)
        self.assertEqual((await credits.reserve(t, snap)).source, "topup")             # the credit is available again
        self.assertEqual((await credits.reserve(t, snap)).source, "topup")
        self.assertFalse((await credits.reserve(t, snap)).allowed)
        # releasing a plan reservation frees one slot of the month
        await redis_mgr.delete_raw(f"credits:{t}")
        await redis_mgr.delete_raw(f"credits_used:{t}")
        plan_res = credits.Reservation(True, "plan")
        await credits.release(t, plan_res)
        self.assertEqual(await credits.total_used(t), 49)

    async def test_expired_credits_do_not_count_and_the_balance_is_rebuilt_from_the_ledger(self):
        t = self.tenants[0]
        await self.grant(t, 10, days=-1)                                               # expired yesterday
        await self.grant(t, 4, days=30)
        snap = await self.snap()
        for _ in range(50):
            await credits.reserve(t, snap)
        res = await asyncio.gather(*[credits.reserve(t, snap) for _ in range(10)])
        self.assertEqual(sum(r.allowed for r in res), 4)
        # Redis flushed: balance is re-hydrated from the ledger MINUS consumption not yet drained (no free replies, no double charge)
        await redis_mgr.delete_raw(f"credits:{t}")
        self.assertFalse((await credits.reserve(t, snap)).allowed)

    async def test_tenants_have_independent_counters_and_exempt_tenants_are_never_capped(self):
        a, b = self.tenants
        snap_a = await self.snap(a)
        await asyncio.gather(*[credits.reserve(a, snap_a) for _ in range(60)])
        self.assertTrue((await credits.reserve(b, await self.snap(b))).allowed)
        async with self.Admin() as s:
            await s.execute(self.text("UPDATE subscriptions SET is_exempt=true WHERE tenant_id=:t"), {"t": a})
            await s.commit()
        snap_a = await self.snap(a)
        self.assertTrue(snap_a["exempt"])
        res = await asyncio.gather(*[credits.reserve(a, snap_a) for _ in range(120)])
        self.assertTrue(all(r.allowed and r.source == "unmetered" for r in res))

    async def test_switch_off_and_redis_outage_fail_open_but_loudly(self):
        t = self.tenants[0]
        snap = await self.snap()
        with patch.object(get_settings(), "billing_enforce_message_limit", False):
            res = await asyncio.gather(*[credits.reserve(t, snap) for _ in range(60)])
            self.assertTrue(all(r.allowed for r in res))
        with patch.object(redis_mgr, "eval_script", AsyncMock(side_effect=ConnectionError("down"))):
            r = await credits.reserve(t, snap)
        self.assertEqual((r.allowed, r.source), (True, "unmetered"))

    # ── L3 share cap ────────────────────────────────────────────────────────────────
    async def test_l3_is_capped_at_its_share_of_the_monthly_replies(self):
        t = self.tenants[0]
        snap = await self.snap()
        allowed = 0
        for i in range(1, 101):
            await credits.reserve(t, snap if i <= 50 else {**snap, "limits": {"messages_per_month": 1000}})
            if await credits.l3_allowed(t):
                allowed += 1
        self.assertLessEqual(allowed, 15)                                              # 15 % of 100
        self.assertGreaterEqual(allowed, 14)
        with patch.object(get_settings(), "llm_l3_max_share_pct", 100):
            self.assertTrue(await credits.l3_allowed(t))

    # ── purchase flow ───────────────────────────────────────────────────────────────
    async def test_buying_a_topup_pack_grants_credits_invoices_vat_and_is_idempotent(self):
        t = self.tenants[0]
        async with client_for(self.app_as()) as c:
            cat = (await c.get(f"{BASE}/topup-packs")).json()
            self.assertEqual([(p["code"], p["replies"], p["subtotal_halalas"], p["total_halalas"]) for p in cat["packs"]],
                             [("replies_5k", 5000, 18900, 21735), ("replies_20k", 20000, 69900, 80385), ("replies_50k", 50000, 149900, 172385)])
            r = await c.post(f"{BASE}/topups", json={"pack_code": "replies_5k"})
            self.assertEqual(r.status_code, 200, r.text)
            txn = r.json()["transaction"]
            self.assertEqual((txn["kind"], txn["amount_halalas"], txn["subtotal_halalas"]), ("topup", 21735, 18900))
            self.assertEqual((await c.post(f"{BASE}/topups", json={"pack_code": "nope"})).status_code, 404)
            pid = self.fake.pay(txn["transaction_id"], 21735)
            self.assertEqual((await self.hook(c, "evt-t1", pid)).json()["outcome"], "applied")
            self.assertEqual((await self.hook(c, "evt-t2", pid)).json()["outcome"], "ignored")           # replay: no second grant
            sub = (await c.get(f"{BASE}/subscription")).json()
            self.assertEqual((sub["status"], sub["plan"]["code"]), ("active", "ctest"))                  # a top-up never changes the plan
            self.assertEqual(sub["credits"]["remaining"], 5000)
            invs = (await c.get(f"{BASE}/invoices")).json()["items"]
            self.assertEqual(len(invs), 1)
            self.assertIn("5,000", invs[0]["description"])
            self.assertEqual((invs[0]["subtotal_halalas"], invs[0]["vat_halalas"], invs[0]["total_halalas"]), (18900, 2835, 21735))
        credit = (await self.rows("SELECT replies_total, replies_remaining, expires_at - purchased_at AS span FROM reply_credits WHERE tenant_id=:t", t=t))
        self.assertEqual(len(credit), 1)
        self.assertEqual((credit[0]["replies_total"], credit[0]["replies_remaining"]), (5000, 5000))
        self.assertGreaterEqual(credit[0]["span"], timedelta(days=364))                                  # 12 months
        # the Redis mirror reflects the purchase: the first reply after the quota is spent can use it
        snap = await self.snap()
        for _ in range(50):
            await credits.reserve(t, snap)
        self.assertEqual((await credits.reserve(t, snap)).source, "topup")

    async def test_topups_need_an_active_paid_plan_and_a_working_payment_provider(self):
        async with self.Admin() as s:
            await s.execute(self.text("UPDATE subscriptions SET status='trial', trial_ends_at=now()+interval '5 days' WHERE tenant_id=:t"), {"t": self.tenants[0]})
            await s.commit()
        async with client_for(self.app_as()) as c:
            r = await c.post(f"{BASE}/topups", json={"pack_code": "replies_5k"})
            self.assertEqual((r.status_code, r.json()["detail"]["code"]), (409, "PLAN_REQUIRED"))
        async with client_for(self.app_as(TenantUserRole.AGENT)) as c:
            self.assertEqual((await c.post(f"{BASE}/topups", json={"pack_code": "replies_5k"})).status_code, 403)

    async def test_addon_seat_is_prorated_extends_the_seat_limit_and_joins_the_renewal_price(self):
        from src.shared.services import team

        t = self.tenants[0]
        async with client_for(self.app_as()) as c:
            cat = (await c.get(f"{BASE}/topup-packs")).json()["addons"]
            self.assertEqual([(a["code"], a["available"], a["monthly"]["subtotal_halalas"]) for a in cat], [("seat", True, 4900), ("whatsapp_number", False, 9900)])
            r = await c.post(f"{BASE}/addons", json={"code": "whatsapp_number", "quantity": 1})
            self.assertEqual((r.status_code, r.json()["detail"]["code"]), (409, "ADDON_UNAVAILABLE"))     # single-number data model today
            r = await c.post(f"{BASE}/addons", json={"code": "seat", "quantity": 2})
            self.assertEqual(r.status_code, 200, r.text)
            txn = r.json()["transaction"]
            self.assertEqual(txn["kind"], "addon")
            self.assertTrue(0 < txn["subtotal_halalas"] < 2 * 4900)                                       # only the remaining ~2/3 of the period
            await self.hook(c, "evt-a1", self.fake.pay(txn["transaction_id"], txn["amount_halalas"], token=False))
            sub = (await c.get(f"{BASE}/subscription")).json()
            self.assertEqual((sub["addons"]["seats"], sub["limits"]["seats"]), (2, 4))
        async with self.App() as s, s.begin():
            await s.execute(self.text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(t)})
            self.assertEqual(await team.seat_limit(s, t), 4)
        async with await self._sys() as db:
            sub = await service.ensure_subscription(db, t)
            plan = await service.get_plan(db, "ctest")
            self.assertEqual(await service.recurring_subtotal(db, sub, plan, "monthly"), 50000 + 2 * 4900)
            self.assertEqual(await service.recurring_subtotal(db, sub, plan, "yearly"), 500000 + 2 * 4900 * 10)
        async with client_for(self.app_as()) as c:
            ok = await c.delete(f"{BASE}/addons/seat?quantity=1")
            self.assertEqual((ok.status_code, ok.json()["addons"]["seats"]), (200, 1))
            self.assertEqual((await c.delete(f"{BASE}/addons/seat?quantity=5")).status_code, 422)

    async def _sys(self):
        from src.shared.db.session import get_system_session
        return get_system_session()

    async def test_removing_a_seat_addon_is_refused_while_the_capacity_is_in_use(self):
        t = self.tenants[0]
        async with self.Admin() as s:
            await s.execute(self.text("UPDATE subscriptions SET addon_seats=1 WHERE tenant_id=:t"), {"t": t})
            for i in range(3):
                await s.execute(self.text("INSERT INTO tenant_users (user_id,tenant_id,email,full_name,hashed_password,role,is_active,created_at,updated_at) "
                                          "VALUES (gen_random_uuid(),:t,:e,'x','x','agent',true,now(),now())"), {"t": t, "e": f"u{i}@example.sa"})
            await s.commit()
        async with client_for(self.app_as()) as c:
            r = await c.delete(f"{BASE}/addons/seat?quantity=1")
            self.assertEqual((r.status_code, r.json()["detail"]["code"]), (409, "CAPACITY_IN_USE"))

    # ── what happens at the wall ────────────────────────────────────────────────────
    async def test_quota_settings_are_validated_and_auto_topup_needs_a_saved_card(self):
        async with client_for(self.app_as()) as c:
            r = await c.put(f"{BASE}/quota-settings", json={"quota_exhausted_action": "stop"})
            self.assertEqual((r.status_code, r.json()["quota_exhausted_action"]), (200, "stop"))
            self.assertEqual((await c.put(f"{BASE}/quota-settings", json={"quota_exhausted_action": "explode"})).status_code, 422)
            r = await c.put(f"{BASE}/quota-settings", json={"auto_topup_enabled": True})
            self.assertEqual(r.json()["detail"]["code"], "PACK_REQUIRED")
            r = await c.put(f"{BASE}/quota-settings", json={"auto_topup_enabled": True, "auto_topup_pack": "replies_5k"})
            self.assertEqual(r.json()["detail"]["code"], "NO_CARD")
            sub = (await c.get(f"{BASE}/subscription")).json()
            self.assertFalse(sub["quota_settings"]["auto_topup_enabled"])                                 # OFF unless explicitly enabled
        self.assertEqual((await self.snap())["quota_action"], "stop")

    async def test_exhaustion_hands_over_by_default_or_stays_silent_and_alerts_once_a_month(self):
        from src.ai_workers.llm_invoker.worker import LLMInvokerWorker

        t = self.tenants[0]
        async with self.Admin() as s:
            await s.execute(self.text("INSERT INTO tenant_users (user_id,tenant_id,email,full_name,hashed_password,role,is_active,created_at,updated_at) "
                                      "VALUES (gen_random_uuid(),:t,'owner@example.sa','x','x','admin',true,now(),now())"), {"t": t})
            await s.commit()
        worker = LLMInvokerWorker.__new__(LLMInvokerWorker)
        worker._handoff_to_human = AsyncMock()
        dec = type("D", (), {"tenant_id": t, "conversation_id": uuid.uuid4()})()
        log = type("L", (), {"warning": lambda *a, **k: None, "info": lambda *a, **k: None})()
        await worker._on_quota_exhausted(dec, {"quota_action": "handoff", "auto_topup": False}, log)
        worker._handoff_to_human.assert_awaited_once()
        self.assertEqual(worker._handoff_to_human.await_args.args[1], "ai_quota_exhausted")
        worker._handoff_to_human.reset_mock()
        await worker._on_quota_exhausted(dec, {"quota_action": "stop", "auto_topup": False}, log)
        worker._handoff_to_human.assert_not_awaited()                                                      # "stop" = the bot stays silent
        notes = await self.rows("SELECT kind, title FROM notifications WHERE tenant_id=:t", t=t)
        self.assertEqual([n["kind"] for n in notes], ["usage_threshold"])                                  # one alert, not one per message

    async def test_auto_topup_charges_the_saved_card_within_its_safety_limits(self):
        from src.shared.security.crypto import encrypt_secret

        t = self.tenants[0]
        async with self.Admin() as s:
            await s.execute(self.text("UPDATE subscriptions SET payment_token=:k, auto_topup_enabled=true, auto_topup_pack='replies_5k' WHERE tenant_id=:t"),
                            {"k": encrypt_secret("tok_abc123"), "t": t})                                    # pragma: allowlist secret
            await s.commit()
        self.assertEqual(await service.auto_topup(t), "paid")
        self.assertEqual((self.fake.charges[0]["amount_halalas"], self.fake.charges[0]["metadata"]["kind"]), (21735, "topup"))
        self.assertEqual((await self.rows("SELECT replies_remaining FROM reply_credits WHERE tenant_id=:t", t=t))[0]["replies_remaining"], 5000)
        self.assertEqual(await service.auto_topup(t), "skipped")                                            # cool-down lock: one attempt at a time
        for _ in range(2):
            await redis_mgr.delete_raw(f"auto_topup_lock:{t}")
            self.assertEqual(await service.auto_topup(t), "paid")
        await redis_mgr.delete_raw(f"auto_topup_lock:{t}")
        self.assertEqual(await service.auto_topup(t), "skipped")                                            # 3 per month cap
        self.assertEqual(len(self.fake.charges), 3)
        self.assertEqual(len(await self.rows("SELECT 1 FROM invoices WHERE tenant_id=:t", t=t)), 3)

    async def test_failed_auto_topup_notifies_and_does_not_retry_until_the_cooldown(self):
        from src.shared.security.crypto import encrypt_secret

        t = self.tenants[0]
        async with self.Admin() as s:
            await s.execute(self.text("INSERT INTO tenant_users (user_id,tenant_id,email,full_name,hashed_password,role,is_active,created_at,updated_at) "
                                      "VALUES (gen_random_uuid(),:t,'owner@example.sa','x','x','admin',true,now(),now())"), {"t": t})
            await s.execute(self.text("UPDATE subscriptions SET payment_token=:k, auto_topup_enabled=true, auto_topup_pack='replies_5k' WHERE tenant_id=:t"),
                            {"k": encrypt_secret("tok_abc123"), "t": t})                                    # pragma: allowlist secret
            await s.commit()
        self.fake.charge_result = "failed"
        self.assertEqual(await service.auto_topup(t), "failed")
        self.assertEqual(await service.auto_topup(t), "skipped")
        self.assertEqual(len(self.fake.charges), 1)
        self.assertEqual([n["kind"] for n in await self.rows("SELECT kind FROM notifications WHERE tenant_id=:t", t=t)], ["payment_failed"])
        self.assertEqual(await self.rows("SELECT 1 FROM reply_credits WHERE tenant_id=:t", t=t), [])

    # ── cost tracking ───────────────────────────────────────────────────────────────
    async def test_every_reply_is_costed_idempotently_rolled_up_and_compared_with_revenue(self):
        a, b = self.tenants
        prices = json.dumps({"gpt-4o-mini": {"in_per_1k": 0.01, "out_per_1k": 0.04}})
        ev = uuid.uuid4()
        with patch.object(get_settings(), "llm_prices_json", prices):
            await llm_cost.record(tenant_id=a, event_id=ev, model="gpt-4o-mini", tier="L1", input_tokens=2000, output_tokens=500, quota_source="plan")
            await llm_cost.record(tenant_id=a, event_id=ev, model="gpt-4o-mini", tier="L1", input_tokens=2000, output_tokens=500, quota_source="plan")   # redelivery
            await llm_cost.record(tenant_id=a, event_id=uuid.uuid4(), model="mystery", tier="L3", input_tokens=0, output_tokens=0, quota_source="topup")
            await llm_cost.record(tenant_id=b, event_id=uuid.uuid4(), model="gpt-4o-mini", tier="L2", input_tokens=1000, output_tokens=1000, quota_source="plan")
        rows = await self.rows("SELECT model, cost_sar, cost_source FROM llm_usage WHERE tenant_id=:t ORDER BY model", t=a)
        self.assertEqual([(r["model"], r["cost_source"], float(r["cost_sar"])) for r in rows], [("gpt-4o-mini", "measured", 0.04), ("mystery", "default", 0.02)])
        self.assertGreaterEqual(await llm_cost.rollup_daily(), 3)
        await llm_cost.rollup_daily()                                                                         # idempotent upsert, no doubling
        daily = await self.rows("SELECT tier, replies, cost_sar FROM llm_usage_daily WHERE tenant_id=:t ORDER BY tier", t=a)
        self.assertEqual([(r["tier"], r["replies"], float(r["cost_sar"])) for r in daily], [("L1", 1, 0.04), ("L3", 1, 0.02)])
        async with self.Admin() as s:                                                                         # tenant a paid 100 SAR (VAT-excl.), b paid 0.10 SAR
            for t, sub_total in ((a, 10000), (b, 10)):
                sid = (await s.execute(self.text("SELECT subscription_id FROM subscriptions WHERE tenant_id=:t"), {"t": t})).scalar()
                await s.execute(self.text("INSERT INTO transactions (tenant_id,subscription_id,kind,plan_code,billing_interval,subtotal_halalas,vat_halalas,amount_halalas,"
                                          "status,provider,paid_at) VALUES (:t,:s,'initial','ctest','monthly',:st,0,:st,'paid','manual',now())"), {"t": t, "s": sid, "st": sub_total})
            await s.commit()
        report = await llm_cost.margin_report(usage.period_of())
        mine = {x["tenant_id"]: x for x in report["tenants"]}
        self.assertEqual((mine[str(a)]["ai_cost_sar"], mine[str(a)]["revenue_sar"], mine[str(a)]["alert"]), (0.06, 100.0, False))
        self.assertEqual(mine[str(a)]["l3_replies"], 1)
        self.assertEqual(mine[str(a)]["assumed_cost_sar"], 0.02)
        self.assertTrue(mine[str(b)]["alert"])                                                                # cost 0.08 > 40 % of 0.10 revenue
        self.assertEqual(report["tenants"][0]["tenant_id"], str(b))                                           # alerts sort first

    async def test_staff_only_see_the_platform_margin_view(self):
        with patch.object(get_settings(), "support_staff_emails", "staff@omniflow.sa, other@omniflow.sa"):
            from src.gateway import dependencies as deps
            from src.shared.core.enums import TenantUserRole as R
            from types import SimpleNamespace as NS

            app = create_app()
            auth_overrides(app, self.tenants[0], self.App, role=R.ADMIN)
            async with client_for(app) as c:
                self.assertEqual((await c.get(f"{BASE}/admin/unit-economics")).status_code, 404)             # normal tenant admin: hidden
            app.dependency_overrides[deps.get_current_user] = lambda: NS(tenant_id=self.tenants[0], user_id=uuid.uuid4(), role=R.ADMIN,
                                                                         email="Other@OmniFlow.sa", is_active=True)       # comma list, any case
            async with client_for(app) as c:
                r = await c.get(f"{BASE}/admin/unit-economics?period={usage.period_of()}")
                self.assertEqual(r.status_code, 200, r.text)
                self.assertIn("tenants", r.json())
                self.assertEqual((await c.get(f"{BASE}/admin/unit-economics?period=2026-13")).status_code, 422)


@unittest.skipUnless(DB_URL and RLS_URL, "needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL (non-superuser: RLS is the tenant guard)")
class InvokerQuotaTests(CreditsBase):
    """The real LLMInvokerWorker.process_message: when the quota is spent the model is NEVER called."""

    def make(self, tier="L1", conv=True):
        from src.ai_workers.llm_invoker import worker as invoker
        from src.ai_workers.llm_invoker.client import LLMResponse
        from src.ai_workers.semantic_router.worker import RoutingDecision
        from src.shared.core.enums import Channel, MessageType
        from src.shared.events.canonical import CanonicalInboundEvent
        from types import SimpleNamespace

        t = self.tenants[0]
        event = CanonicalInboundEvent(tenant_id=t, channel=Channel.WHATSAPP, platform_message_id=f"wamid.{uuid.uuid4().hex[:8]}",
                                      platform_conversation_id="966500000000", platform_user_id="966500000000",
                                      message_type=MessageType.TEXT, text_content="أبغى شقة")
        decision = RoutingDecision(event=event, tenant_id=t, conversation_id=uuid.uuid4() if conv else None, target_tier=tier, route_reason="general")
        worker = invoker.LLMInvokerWorker()
        worker._publish_outbound = AsyncMock()
        worker._handoff_to_human = AsyncMock()
        worker._rag_enabled = False
        worker._get_tenant_context = AsyncMock(return_value={})
        self.llm = AsyncMock(return_value=LLMResponse(text="أهلًا", model_used="gpt-4o-mini", tier=tier, input_tokens=2000, output_tokens=500,
                                                      total_tokens=2500, latency_ms=5, finish_reason="STOP"))
        patches = [patch.object(invoker.gemini_client, "generate_response", self.llm),
                   patch.object(invoker, "get_tenant_persona", AsyncMock(return_value="persona")),
                   patch.object(invoker, "get_company_context", AsyncMock(return_value="")),
                   patch.object(invoker.redis_mgr, "get_conversation_history", AsyncMock(return_value=[])),
                   patch.object(invoker.redis_mgr, "append_message_to_history", AsyncMock()),
                   patch.object(invoker, "load_conversation_state", AsyncMock(return_value={"is_human_active": False, "is_processing_restricted": False, "ai_reply_epoch": 0})),
                   patch.object(worker, "_publish_analytics", AsyncMock()),
                   patch.object(get_settings(), "llm_prices_json", json.dumps({"gpt-4o-mini": {"in_per_1k": 0.01, "out_per_1k": 0.04}}))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return worker, decision

    async def run_one(self, worker, decision):
        from tests.test_delivery import record
        await worker.process_message(record(decision.to_kafka_bytes()))
        await asyncio.sleep(0.2)            # fire-and-forget cost row

    async def test_a_normal_reply_counts_against_the_quota_and_records_its_real_cost(self):
        worker, decision = self.make()
        await self.run_one(worker, decision)
        self.llm.assert_awaited_once()
        worker._publish_outbound.assert_awaited_once()
        self.assertEqual(await credits.total_used(self.tenants[0]), 1)
        row = (await self.rows("SELECT model, tier, input_tokens, output_tokens, cost_sar, cost_source, quota_source FROM llm_usage WHERE tenant_id=:t", t=self.tenants[0]))[0]
        self.assertEqual((row["model"], row["tier"], row["input_tokens"], float(row["cost_sar"]), row["cost_source"], row["quota_source"]),
                         ("gpt-4o-mini", "L1", 2000, 0.04, "measured", "plan"))

    async def test_when_the_quota_and_credits_are_spent_the_model_is_not_called_at_all(self):
        t = self.tenants[0]
        worker, decision = self.make()
        snap = await self.snap()
        for _ in range(50):
            await credits.reserve(t, snap)
        await self.run_one(worker, decision)
        self.llm.assert_not_awaited()                                                  # no LLM spend after the cap
        worker._publish_outbound.assert_not_awaited()
        worker._handoff_to_human.assert_awaited_once()                                 # default behaviour: a person takes over
        self.assertEqual(await credits.total_used(t), 50)
        self.assertEqual(await self.rows("SELECT 1 FROM llm_usage WHERE tenant_id=:t", t=t), [])

    async def test_stop_mode_stays_silent(self):
        t = self.tenants[0]
        async with self.Admin() as s:
            await s.execute(self.text("UPDATE subscriptions SET quota_exhausted_action='stop' WHERE tenant_id=:t"), {"t": t})
            await s.commit()
        worker, decision = self.make()
        snap = await self.snap()
        for _ in range(50):
            await credits.reserve(t, snap)
        await self.run_one(worker, decision)
        self.llm.assert_not_awaited()
        worker._handoff_to_human.assert_not_awaited()
        worker._publish_outbound.assert_not_awaited()

    async def test_topup_credits_keep_the_bot_answering_and_are_marked_as_such(self):
        t = self.tenants[0]
        await self.grant(t, 3)
        worker, decision = self.make()
        snap = await self.snap()
        for _ in range(50):
            await credits.reserve(t, snap)
        await self.run_one(worker, decision)
        self.llm.assert_awaited_once()
        self.assertEqual((await self.rows("SELECT quota_source FROM llm_usage WHERE tenant_id=:t", t=t))[0]["quota_source"], "topup")
        self.assertEqual(int(await redis_mgr.get_raw(f"credits_used:{t}")), 1)

    async def test_a_failed_llm_call_is_not_charged_to_the_quota(self):
        t = self.tenants[0]
        worker, decision = self.make()
        self.llm.side_effect = RuntimeError("provider down")
        worker._publish_escalation_event = AsyncMock()
        await self.run_one(worker, decision)
        self.assertEqual(self.llm.await_count, 2)                                      # the existing one-retry behaviour
        self.assertEqual(await credits.total_used(t), 0)                               # reservation handed back
        self.assertEqual(await self.rows("SELECT 1 FROM llm_usage WHERE tenant_id=:t", t=t), [])

    async def test_l3_is_downgraded_to_l2_once_its_share_is_used_up(self):
        from src.shared.core.enums import RoutingTier

        t = self.tenants[0]
        worker, decision = self.make(tier="L3")
        await self.run_one(worker, decision)
        self.assertEqual(self.llm.await_args.kwargs["tier"], RoutingTier.L3_MASTER)    # the first L3 reply of the month is allowed
        worker2, decision2 = self.make(tier="L3")
        await self.run_one(worker2, decision2)
        self.assertEqual(self.llm.await_args.kwargs["tier"], RoutingTier.L2_RAG)       # 1 of 2 replies would be 50 % > 15 %

    async def test_output_and_history_are_bounded(self):
        from src.ai_workers.llm_invoker import worker as invoker
        worker, decision = self.make()
        with patch.object(get_settings(), "llm_history_max_messages", 4), patch.object(invoker.settings, "llm_history_max_messages", 4):
            await self.run_one(worker, decision)
        self.assertEqual(invoker.redis_mgr.get_conversation_history.await_args.kwargs["last_n"], 4)
        from src.ai_workers.llm_invoker.client import GeminiLLMClient
        self.assertLessEqual(get_settings().llm_max_output_tokens, 1024)


if __name__ == "__main__":
    unittest.main()

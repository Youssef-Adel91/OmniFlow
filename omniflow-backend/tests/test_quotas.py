"""P10-A: every plan limit is enforced for real (properties, contacts, seats, campaigns, channels) — pure maths + PostgreSQL + RLS.

Pure tests run everywhere; the DB class needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL (see test_customers.py).
A scratch plan `qtest` with tiny limits stands in for the real plans so a handful of rows prove the cap.
"""
import json
import os
import unittest
import uuid
from types import SimpleNamespace as NS
from unittest.mock import patch

from src.gateway.main import create_app
from src.shared.core.config import get_settings
from src.shared.core.enums import TenantUserRole
from src.shared.services.billing import catalog, quotas
from tests.test_property_import_flow import auth_overrides, client_for

DB_URL = os.environ.get("OMNIFLOW_TEST_DATABASE_URL")
RLS_URL = os.environ.get("OMNIFLOW_TEST_RLS_URL")
LIMITS = {"messages_per_month": 100, "seats": 2, "whatsapp_numbers": 1, "properties": 2, "contacts": 2, "campaigns": False}


class CatalogTests(unittest.TestCase):
    def test_exempt_and_missing_subscription_are_unlimited(self):
        self.assertIsNone(catalog.effective_limits(None, None))
        self.assertIsNone(catalog.effective_limits(NS(is_exempt=True, status="active"), NS(limits={"seats": 1})))

    def test_trial_uses_the_trial_caps_whatever_the_plan(self):
        lim = catalog.effective_limits(NS(is_exempt=False, status="trial", addon_seats=5, addon_whatsapp_numbers=0), NS(limits={"seats": 99}))
        self.assertEqual((lim["messages_per_month"], lim["channels"], lim["seats"], lim["properties"], lim["campaigns"]), (300, 1, 2, 50, False))

    def test_paid_plan_limits_plus_purchased_addons(self):
        sub = NS(is_exempt=False, status="active", addon_seats=3, addon_whatsapp_numbers=1)
        lim = catalog.effective_limits(sub, NS(limits={"seats": 5, "whatsapp_numbers": 1, "properties": 1000}))
        self.assertEqual((lim["seats"], lim["whatsapp_numbers"], lim["properties"]), (8, 2, 1000))

    def test_enterprise_with_no_limits_is_unlimited_per_key(self):
        lim = catalog.effective_limits(NS(is_exempt=False, status="active", addon_seats=0, addon_whatsapp_numbers=0), NS(limits={}))
        self.assertEqual(lim, {})


@unittest.skipUnless(DB_URL and RLS_URL, "needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL (non-superuser: RLS is the tenant guard)")
class QuotaDbTests(unittest.IsolatedAsyncioTestCase):
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
            await s.execute(text("DELETE FROM plans WHERE code='qtest'"))
            await s.execute(text("INSERT INTO plans (code,name_ar,price_monthly_halalas,price_yearly_halalas,limits,is_placeholder) "
                                 "VALUES ('qtest','x',100,1000,CAST(:l AS jsonb),false)"), {"l": json.dumps(LIMITS)})
            for i, t in enumerate(self.tenants):
                await s.execute(text(
                    "INSERT INTO tenants (tenant_id,business_name,fal_license_number,subscription_tier,status,max_ai_conversations,onboarding_status,created_at,updated_at) "
                    "VALUES (:t,:n,:f,'starter','active',100,'completed',now(),now())"), {"t": t, "n": f"شركة {i}", "f": f"FAL-{t.hex[:8]}"})
                await s.execute(text("INSERT INTO subscriptions (tenant_id,plan_code,status,is_exempt,current_period_end) "
                                     "VALUES (:t,'qtest','active',false,now()+interval '10 days')"), {"t": t})
            await s.commit()
        for name, value in (("billing_gate_enabled", True), ("billing_enforce_limits", True), ("app_env", "development")):
            p = patch.object(get_settings(), name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch("src.shared.db.session.AsyncSessionFactory", self.App)
        p.start()
        self.addCleanup(p.stop)

    async def asyncTearDown(self):
        async with self.Admin() as s:
            for t in self.tenants:
                for stmt in ("DELETE FROM broadcast_campaigns WHERE tenant_id=:t", "DELETE FROM customers WHERE tenant_id=:t",
                             "DELETE FROM property_listings WHERE tenant_id=:t", "DELETE FROM audit_logs WHERE tenant_id=:t",
                             "DELETE FROM team_invitations WHERE tenant_id=:t", "DELETE FROM tenant_users WHERE tenant_id=:t",
                             "DELETE FROM subscriptions WHERE tenant_id=:t", "DELETE FROM tenants WHERE tenant_id=:t"):
                    await s.execute(self.text(stmt), {"t": t})
            await s.execute(self.text("DELETE FROM plans WHERE code='qtest'"))
            await s.commit()
        await self.admin.dispose()
        await self.app_engine.dispose()

    def app_as(self, tenant=None, role=TenantUserRole.ADMIN):
        app = create_app()
        auth_overrides(app, tenant or self.tenants[0], self.App, role=role)
        return app

    async def scalar(self, sql, **p):
        async with self.Admin() as s:
            return (await s.execute(self.text(sql), p)).scalar()

    async def set_plan(self, tenant, plan="qtest", status="active", exempt=False):
        async with self.Admin() as s:
            await s.execute(self.text("UPDATE subscriptions SET plan_code=:p, status=:st, is_exempt=:e WHERE tenant_id=:t"),
                            {"p": plan, "st": status, "e": exempt, "t": tenant})
            await s.commit()

    # ── properties ──────────────────────────────────────────────────────────────────
    async def test_property_creation_stops_at_the_plan_limit_and_other_tenants_are_unaffected(self):
        body = {"property_type": "apartment", "city": "الرياض"}
        with patch("src.gateway.routers.properties.sync_listing_to_qdrant", create=True):
            async with client_for(self.app_as()) as c:
                codes = [(await c.post("/api/v1/properties", json=body)).status_code for _ in range(3)]
            self.assertEqual(codes, [201, 201, 402])
            async with client_for(self.app_as()) as c:
                r = await c.post("/api/v1/properties", json=body)
            d = r.json()["detail"]
            self.assertEqual((d["code"], d["resource"], d["limit"], d["used"]), ("PLAN_LIMIT_REACHED", "properties", 2, 2))
            self.assertIn("2", d["message"])
            async with client_for(self.app_as(self.tenants[1])) as c:        # another tenant has its own count
                self.assertEqual((await c.post("/api/v1/properties", json=body)).status_code, 201)
        self.assertEqual(await self.scalar("SELECT count(*) FROM property_listings WHERE tenant_id=:t", t=self.tenants[0]), 2)

    # ── contacts ────────────────────────────────────────────────────────────────────
    async def test_manual_contact_creation_stops_at_the_limit_but_inbound_contacts_are_never_dropped(self):
        async with client_for(self.app_as()) as c:
            codes = [(await c.post("/api/v1/customers", json={"phone": f"05000000{i:02d}"})).status_code for i in range(3)]
        self.assertEqual(codes, [201, 201, 402])
        # an inbound customer message goes through the repository, not the manual endpoint: it must still create the contact
        async with self.Admin() as s:
            await s.execute(self.text("INSERT INTO customers (customer_id,tenant_id,unified_phone,source,channel_user_ids,marketing_opt_in,is_vip,is_processing_restricted,vcard_state,engagement_score,created_at,updated_at) "
                                      "VALUES (gen_random_uuid(),:t,'+966511111111','whatsapp','{}',false,false,false,'STATE_NEW',0,now(),now())"), {"t": self.tenants[0]})
            await s.commit()
        snap = await self.snapshot()
        self.assertEqual((snap["resources"]["contacts"]["used"], snap["resources"]["contacts"]["over"]), (3, True))
        async with client_for(self.app_as()) as c:                            # …but adding by hand stays blocked
            self.assertEqual((await c.post("/api/v1/customers", json={"phone": "0509999999"})).status_code, 402)

    async def test_erased_contacts_do_not_count(self):
        async with self.Admin() as s:
            for i in range(2):
                await s.execute(self.text("INSERT INTO customers (customer_id,tenant_id,unified_phone,source,channel_user_ids,marketing_opt_in,is_vip,is_processing_restricted,vcard_state,engagement_score,erased_at,created_at,updated_at) "
                                          "VALUES (gen_random_uuid(),:t,:p,'whatsapp','{}',false,false,false,'STATE_NEW',0,now(),now(),now())"), {"t": self.tenants[0], "p": f"+96650000009{i}"})
            await s.commit()
        async with client_for(self.app_as()) as c:
            self.assertEqual((await c.post("/api/v1/customers", json={"phone": "0501234567"})).status_code, 201)

    async def snapshot(self, tenant=None):
        async with self.App() as s, s.begin():
            await s.execute(self.text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(tenant or self.tenants[0])})
            return await quotas.snapshot(s, tenant or self.tenants[0])

    # ── campaigns ───────────────────────────────────────────────────────────────────
    async def test_campaigns_are_blocked_on_plans_without_them_and_allowed_on_plans_with_them(self):
        body = {"title": "عرض الصيف"}
        async with client_for(self.app_as()) as c:
            r = await c.post("/api/v1/broadcasts", json=body)
            self.assertEqual((r.status_code, r.json()["detail"]["code"], r.json()["detail"]["resource"]), (402, "PLAN_LIMIT_REACHED", "campaigns"))
            self.assertEqual((await c.post(f"/api/v1/broadcasts/{uuid.uuid4()}/schedule", json={})).status_code, 402)    # cannot be scheduled either
        async with self.Admin() as s:
            await s.execute(self.text("UPDATE plans SET limits = limits || '{\"campaigns\": true}'::jsonb WHERE code='qtest'"))
            await s.commit()
        async with client_for(self.app_as()) as c:
            self.assertEqual((await c.post("/api/v1/broadcasts", json=body)).status_code, 201)

    # ── seats ───────────────────────────────────────────────────────────────────────
    async def test_team_seats_follow_the_plan_trial_caps_and_purchased_addons(self):
        from src.shared.services import team

        async with self.App() as s, s.begin():
            await s.execute(self.text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(self.tenants[0])})
            self.assertEqual(await team.seat_limit(s, self.tenants[0]), 2)
        async with self.Admin() as s:
            await s.execute(self.text("UPDATE subscriptions SET addon_seats=3 WHERE tenant_id=:t"), {"t": self.tenants[0]})
            await s.commit()
        async with self.App() as s, s.begin():
            await s.execute(self.text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(self.tenants[0])})
            self.assertEqual(await team.seat_limit(s, self.tenants[0]), 5)
        await self.set_plan(self.tenants[0], status="trial")
        async with self.App() as s, s.begin():
            await s.execute(self.text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(self.tenants[0])})
            self.assertEqual(await team.seat_limit(s, self.tenants[0]), 2)                      # trial cap, add-ons ignored

    # ── channels (trial: 1) ─────────────────────────────────────────────────────────
    async def test_trial_allows_one_channel_group_only(self):
        await self.set_plan(self.tenants[0], status="trial")
        async with self.App() as s, s.begin():
            await s.execute(self.text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(self.tenants[0])})
            await quotas.ensure_channel_slot(s, self.tenants[0], "whatsapp")                    # first channel fits
        async with self.Admin() as s:
            await s.execute(self.text("UPDATE tenants SET whatsapp_phone_number_id='123456' WHERE tenant_id=:t"), {"t": self.tenants[0]})
            await s.commit()
        async with self.App() as s, s.begin():
            await s.execute(self.text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(self.tenants[0])})
            await quotas.ensure_channel_slot(s, self.tenants[0], "whatsapp")                    # re-saving the connected one is fine
            with self.assertRaises(quotas.QuotaExceeded) as cm:
                await quotas.ensure_channel_slot(s, self.tenants[0], "meta_page")
            self.assertEqual((cm.exception.resource, cm.exception.limit, cm.exception.used), ("channels", 1, 1))
            self.assertEqual(quotas.to_http(cm.exception).status_code, 402)

    # ── exempt / kill switch ────────────────────────────────────────────────────────
    async def test_exempt_tenants_and_the_kill_switch_are_never_limited(self):
        await self.set_plan(self.tenants[0], exempt=True)
        async with self.App() as s, s.begin():
            await s.execute(self.text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(self.tenants[0])})
            self.assertIsNone(await quotas.remaining(s, self.tenants[0], "properties"))
            await quotas.ensure_feature(s, self.tenants[0], "campaigns")
        await self.set_plan(self.tenants[0], exempt=False)
        with patch.object(get_settings(), "billing_enforce_limits", False):
            async with self.App() as s, s.begin():
                await s.execute(self.text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(self.tenants[0])})
                self.assertIsNone(await quotas.remaining(s, self.tenants[0], "contacts"))

    async def test_a_tenant_with_no_subscription_row_gets_the_trial_caps(self):
        async with self.Admin() as s:
            await s.execute(self.text("DELETE FROM subscriptions WHERE tenant_id=:t"), {"t": self.tenants[0]})
            await s.commit()
        async with self.App() as s, s.begin():                              # bare session, no tenant GUC (like onboarding)
            lim = await quotas.limits_of(s, self.tenants[0])
        self.assertEqual((lim["properties"], lim["channels"], lim["campaigns"]), (50, 1, False))
        self.assertEqual(await self.scalar("SELECT status FROM subscriptions WHERE tenant_id=:t", t=self.tenants[0]), "trial")

    # ── bulk import ─────────────────────────────────────────────────────────────────
    async def test_bulk_import_keeps_what_fits_and_reports_the_rest_per_row(self):
        from src.shared.services import customer_import, property_import

        def row(n, key):
            return NS(ok=True, errors=[], row_number=n, key=key, values={"property_type": "APARTMENT", "display_name": f"c{n}"})

        t = self.tenants[0]
        async with self.App() as s, s.begin():
            await s.execute(self.text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(t)})
            out = await property_import._apply_batch(s, t, uuid.uuid4(), [row(i, None) for i in range(1, 6)], "skip")
        self.assertEqual((len(out["created"]), len(out["failed"])), (2, 3))
        self.assertIn("حد العقارات", out["failed"][0][1])
        async with self.App() as s, s.begin():
            await s.execute(self.text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(t)})
            out = await customer_import._apply_batch(s, t, uuid.uuid4(), [row(i, f"+96650000100{i}") for i in range(1, 5)], {}, "f.csv",
                                                     {"opted_in": 0, "opted_out": 0})
        self.assertEqual((out["created"], len(out["failed"])), (2, 2))
        self.assertIn("حد جهات الاتصال", out["failed"][0][1])
        self.assertEqual(await self.scalar("SELECT count(*) FROM customers WHERE tenant_id=:t", t=t), 2)

    # ── billing page ────────────────────────────────────────────────────────────────
    async def test_billing_page_reports_usage_against_every_limit(self):
        async with client_for(self.app_as()) as c:
            await c.post("/api/v1/properties", json={"property_type": "apartment"})
            q = (await c.get("/api/v1/billing/subscription")).json()["quotas"]
        self.assertFalse(q["unlimited"])
        self.assertEqual((q["resources"]["properties"]["used"], q["resources"]["properties"]["limit"]), (1, 2))
        self.assertEqual((q["resources"]["seats"]["limit"], q["campaigns"]), (2, False))


if __name__ == "__main__":
    unittest.main()

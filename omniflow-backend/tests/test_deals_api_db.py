"""The /api/v1/deals API end to end on PostgreSQL/RLS: 404 while the platform flag is off, board, move/close with the lost-reason rule, the hand-off summary, the conversion
report, role limits (auditor read-only) and tenant isolation (another tenant's deal is a 404 and cannot be moved)."""
import json
import os
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from src.gateway.main import create_app
from src.shared.core.config import get_settings
from src.shared.core.enums import TenantUserRole
from src.shared.services import tenant_prefs
from tests.test_property_import_flow import auth_overrides, client_for

DB_URL = os.environ.get("OMNIFLOW_TEST_DATABASE_URL")
RLS_URL = os.environ.get("OMNIFLOW_TEST_RLS_URL")


@unittest.skipUnless(DB_URL and RLS_URL, "needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL")
class DealsApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        self.text = text
        self.admin, self.app_engine = create_async_engine(DB_URL), create_async_engine(RLS_URL)
        self.Admin = async_sessionmaker(self.admin, expire_on_commit=False)
        self.App = async_sessionmaker(self.app_engine, expire_on_commit=False)
        p = patch("src.shared.db.session.AsyncSessionFactory", self.App)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(get_settings(), "feature_sales_closing", True)
        p.start()
        self.addCleanup(p.stop)
        self.a, self.b = uuid.uuid4(), uuid.uuid4()
        self.deal: dict[uuid.UUID, uuid.UUID] = {}
        self.conv: dict[uuid.UUID, uuid.UUID] = {}
        async with self.Admin() as s:
            for t in (self.a, self.b):
                c, v, d = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
                await s.execute(text("INSERT INTO tenants (tenant_id,business_name,fal_license_number,subscription_tier,status,max_ai_conversations,onboarding_status,created_at,updated_at) "
                                     "VALUES (:t,'مكتب',:f,'starter','active',100,'completed',now(),now())"), {"t": t, "f": f"FAL-{t.hex[:8]}"})
                await s.execute(text("INSERT INTO customers (customer_id,tenant_id,unified_phone,display_name,qualification,is_processing_restricted,is_vip,vcard_state,created_at,updated_at) "
                                     "VALUES (:c,:t,:p,'عميل',CAST(:q AS jsonb),false,false,'new',now(),now())"),
                                {"c": c, "t": t, "p": f"+9665{uuid.uuid4().int % 10**8:08d}", "q": json.dumps({"label": "hot", "score": 70, "district": "النرجس", "property_type": "apartment", "budget_max": 1000000})})
                await s.execute(text("INSERT INTO conversations (conversation_id,tenant_id,customer_id,channel,status,message_count,created_at,updated_at) VALUES (:v,:t,:c,'whatsapp','ai_active',0,now(),now())"), {"v": v, "t": t, "c": c})
                await s.execute(text("INSERT INTO deals (deal_id,tenant_id,customer_id,conversation_id,stage,reached_stage,lead_label,score,source_channel,created_at,updated_at,stage_changed_at) "
                                     "VALUES (:d,:t,:c,:v,'qualified','qualified','hot',70,'whatsapp',now(),now(),now())"), {"d": d, "t": t, "c": c, "v": v})
                self.deal[t], self.conv[t] = d, v
            await s.commit()
        self.addAsyncCleanup(self._cleanup)

    async def _cleanup(self):
        async with self.Admin() as s:
            for t in (self.a, self.b):
                for table in ("deals", "appointments", "property_listings", "conversations", "customers", "audit_logs", "tenants"):
                    await s.execute(self.text(f"DELETE FROM {table} WHERE tenant_id=:t"), {"t": t})
            await s.commit()
        await self.admin.dispose()
        await self.app_engine.dispose()

    def client(self, tenant, role=TenantUserRole.ADMIN):
        app = create_app()
        auth_overrides(app, tenant, self.App, role)
        return client_for(app)

    async def test_everything_answers_404_while_the_platform_flag_is_off(self):
        with patch.object(get_settings(), "feature_sales_closing", False):
            async with self.client(self.a) as c:
                for method, url in (("get", "/api/v1/deals/board"), ("get", "/api/v1/deals/report"), ("get", f"/api/v1/deals/by-conversation/{self.conv[self.a]}")):
                    self.assertEqual((await getattr(c, method)(url)).status_code, 404, url)
                self.assertEqual((await c.patch(f"/api/v1/deals/{self.deal[self.a]}", json={"stage": "won"})).status_code, 404)

    async def test_board_lists_only_my_deals_in_the_right_column(self):
        async with self.client(self.a) as c:
            r = await c.get("/api/v1/deals/board")
        self.assertEqual(r.status_code, 200)
        cols = r.json()["columns"]
        self.assertEqual([x["deal_id"] for x in cols["qualified"]["cards"]], [str(self.deal[self.a])])
        self.assertEqual(sum(v["total"] for v in cols.values()), 1)                                              # tenant B's deal is invisible

    async def test_moving_and_closing_a_deal_follows_the_rules_and_is_audited(self):
        async with self.client(self.a) as c:
            url = f"/api/v1/deals/{self.deal[self.a]}"
            self.assertEqual((await c.patch(url, json={"stage": "viewing"})).json()["stage"], "viewing")
            r = await c.patch(url, json={"stage": "lost"})
            self.assertEqual(r.status_code, 422)                                                                    # a reason is mandatory
            r = await c.patch(url, json={"stage": "lost", "lost_reason": "price"})
            self.assertEqual((r.status_code, r.json()["stage"], r.json()["lost_reason"]), (200, "lost", "price"))
            self.assertEqual((await c.patch(url, json={"stage": "bogus"})).status_code, 422)
        rows = await self.text_rows("SELECT details->>'to' FROM audit_logs WHERE tenant_id=:t AND action='deal.updated' ORDER BY created_at", self.a)
        self.assertEqual([r[0] for r in rows], ["viewing", "lost"])

    async def text_rows(self, sql, tenant):
        async with self.Admin() as s:
            return (await s.execute(self.text(sql), {"t": tenant})).all()

    async def test_the_auditor_can_read_but_not_change(self):
        async with self.client(self.a, TenantUserRole.AUDITOR) as c:
            self.assertEqual((await c.get("/api/v1/deals/board")).status_code, 200)
            self.assertIn((await c.patch(f"/api/v1/deals/{self.deal[self.a]}", json={"stage": "won"})).status_code, (401, 403))

    async def test_another_tenants_deal_is_a_404_and_stays_untouched(self):
        async with self.client(self.b) as c:
            self.assertEqual((await c.patch(f"/api/v1/deals/{self.deal[self.a]}", json={"stage": "won"})).status_code, 404)
            self.assertEqual((await c.get(f"/api/v1/deals/by-conversation/{self.conv[self.a]}")).status_code, 404)
        self.assertEqual((await self.text_rows("SELECT stage FROM deals WHERE tenant_id=:t", self.a))[0][0], "qualified")

    async def test_the_handoff_summary_has_the_profile_the_deal_and_matching_listings(self):
        async with self.Admin() as s:
            await s.execute(self.text("INSERT INTO property_listings (listing_id,tenant_id,rega_ad_number,property_type,status,is_verified,district,city,price,bedrooms,created_at,updated_at) "
                                      "VALUES (:i,:t,'R9','apartment','VERIFIED_ACTIVE',true,'النرجس','الرياض',850000,3,now(),now())"), {"i": uuid.uuid4(), "t": self.a})
            await s.execute(self.text("INSERT INTO appointments (appointment_id,tenant_id,conversation_id,customer_id,scheduled_at,status,source) "
                                      "SELECT :i,:t,conversation_id,customer_id,now() + interval '1 day','scheduled','bot' FROM conversations WHERE conversation_id=:v"),
                            {"i": uuid.uuid4(), "t": self.a, "v": self.conv[self.a]})
            await s.commit()
        async with self.client(self.a) as c:
            r = await c.get(f"/api/v1/deals/by-conversation/{self.conv[self.a]}")
        j = r.json()
        self.assertEqual((r.status_code, j["label"], j["deal"]["stage"], j["qualification"]["district"]), (200, "hot", "qualified", "النرجس"))
        self.assertEqual([x["price"] for x in j["suggested_listings"]], [850000.0])
        self.assertEqual(j["appointment"]["source"], "bot")

    async def test_the_conversion_report_groups_by_channel_and_validates_the_window(self):
        async with self.client(self.a) as c:
            await c.patch(f"/api/v1/deals/{self.deal[self.a]}", json={"stage": "won"})
            r = await c.get("/api/v1/deals/report?days=30")
            bad = await c.get("/api/v1/deals/report?days=0")
        wa = r.json()["by_channel"]["whatsapp"]
        self.assertEqual((wa["deals"], wa["won"], wa["win_rate"]), (1, 1, 1.0))
        self.assertEqual(bad.status_code, 422)


if __name__ == "__main__":
    unittest.main()

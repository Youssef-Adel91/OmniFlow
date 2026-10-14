"""Roles: salesperson (receives the hand-off, reads the summary, closes / loses a deal with a reason, reads the report), lower-privilege staff (403, not 200), tenant B (sees nothing of tenant A)."""
import uuid

from src.gateway.main import create_app
from src.shared.core.enums import TenantUserRole
from tests.e2e.simulation.harness import SimCase
from tests.test_property_import_flow import auth_overrides, client_for


class StaffTests(SimCase):
    def client(self, role=TenantUserRole.ADMIN, tenant=None):
        app = create_app()
        auth_overrides(app, tenant or self.tenant, self.App, role)
        return client_for(app)

    async def hot_negotiation(self, channel="whatsapp"):
        c = await self.customer("سالم", channel=channel)
        await c.say("ابغى شقة ٣ غرف في النرجس تحت مليون كاش للسكن")
        await c.say("ابغى اشتريها ب 700 الف")
        return c

    async def test_the_salesperson_sees_the_summary_closes_the_deal_and_the_report_counts_it_per_channel(self):
        wa, ig = await self.hot_negotiation("whatsapp"), await self.hot_negotiation("instagram")
        self.assertEqual(len(await self.q("SELECT 1 FROM notifications WHERE tenant_id=:t AND kind='handoff'")), 2)
        async with self.client() as api:
            r = await api.get(f"/api/v1/deals/by-conversation/{wa.conv_id}")
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertEqual(body["deal"]["stage"], "negotiation")
            self.assertIn("النرجس", str(body))
            deal_id = body["deal"]["deal_id"]
            self.assertEqual((await api.patch(f"/api/v1/deals/{deal_id}", json={"stage": "lost"})).status_code, 422)          # a reason is mandatory
            self.assertEqual((await api.patch(f"/api/v1/deals/{deal_id}", json={"stage": "lost", "lost_reason": "price"})).status_code, 200)
            rep = (await api.get("/api/v1/deals/report")).json()
        text = str(rep)
        self.assertIn("whatsapp", text)
        self.assertIn("instagram", text)

    async def test_lower_privilege_staff_get_403_not_200(self):
        c = await self.hot_negotiation()
        deal = (await self.q("SELECT deal_id FROM deals WHERE tenant_id=:t"))[0][0]
        async with self.client(TenantUserRole.AUDITOR) as api:
            self.assertEqual((await api.get("/api/v1/deals/board")).status_code, 200)                                          # read-only is fine
            self.assertIn((await api.patch(f"/api/v1/deals/{deal}", json={"stage": "won"})).status_code, (401, 403))
        self.assertEqual((await self.q("SELECT stage FROM deals WHERE tenant_id=:t"))[0][0], "negotiation")
        async with self.client(TenantUserRole.AGENT) as api:
            r = await api.post("/api/v1/team/invitations", json={"email": "x@example.com", "role": "agent"})
            self.assertIn(r.status_code, (401, 403), r.text)                                                                   # an agent cannot invite
            self.assertIn((await api.post("/api/v1/billing/checkout", json={"plan": "pro"})).status_code, (401, 403, 422))

    async def test_tenant_b_sees_nothing_of_tenant_a_through_the_api_and_through_the_database(self):
        await self.hot_negotiation()
        other = uuid.uuid4()
        async with self.Admin() as s:
            await s.execute(self.text("INSERT INTO tenants (tenant_id,business_name,fal_license_number,subscription_tier,status,max_ai_conversations,onboarding_status,created_at,updated_at) "
                                      "VALUES (:t,'منافس',:f,'starter','active',100,'completed',now(),now())"), {"t": other, "f": f"FAL-{other.hex[:8]}"})
            await s.commit()
        self.addAsyncCleanup(self._drop_tenant, other)
        async with self.client(tenant=other) as api:
            self.assertEqual(sum(v["total"] for v in (await api.get("/api/v1/deals/board")).json()["columns"].values()), 0)
        async with self.App() as s, s.begin():                                                                                  # raw SQL as tenant B under RLS
            await s.execute(self.text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(other)})
            for table in ("customers", "conversations", "messages", "deals", "followup_tasks", "notifications", "property_listings"):
                n = (await s.execute(self.text(f"SELECT count(*) FROM {table}"))).scalar()
                self.assertEqual(n, 0, f"tenant B can read {table}")

    async def _drop_tenant(self, t):
        async with self.Admin() as s:
            await s.execute(self.text("DELETE FROM tenants WHERE tenant_id=:t"), {"t": t})
            await s.commit()

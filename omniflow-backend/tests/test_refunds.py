"""P10-D/E: 14-day first-payment refund policy (provider refund + credit note + audit) and one-trial-per-company.

DB tests need OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL. The provider is a fake: the real Moyasar refund API is NOT exercised.
"""
import uuid
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import patch

from src.gateway import dependencies as deps
from src.gateway.main import create_app
from src.shared.core.config import get_settings
from src.shared.core.enums import TenantUserRole
from src.shared.services.billing import refunds, trial_identity
from src.shared.services.billing.providers.base import ProviderError
from tests.test_credits import CreditsBase, DB_URL, RLS_URL, BASE
from tests.test_property_import_flow import auth_overrides, client_for

UTC = timezone.utc


class TrialIdentityPureTests(unittest.TestCase):
    def test_normalisation_makes_equivalent_spellings_collide(self):
        self.assertEqual(trial_identity.digest("cr", "1010 123456"), trial_identity.digest("cr", "1010123456"))
        self.assertEqual(trial_identity.digest("phone", "0501234567"), trial_identity.digest("phone", "+966 50 123 4567"))
        self.assertEqual(trial_identity.digest("phone", "00966501234567"), trial_identity.digest("phone", "966501234567"))
        self.assertNotEqual(trial_identity.digest("cr", "1010123456"), trial_identity.digest("phone", "1010123456"))
        self.assertIsNone(trial_identity.digest("cr", "12"))
        self.assertIsNone(trial_identity.digest("phone", "abc"))


@unittest.skipUnless(DB_URL and RLS_URL, "needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL (non-superuser: RLS is the tenant guard)")
class RefundDbTests(CreditsBase):
    staff = "Staff@OmniFlow.sa"

    async def asyncSetUp(self):
        await super().asyncSetUp()
        p = patch.object(get_settings(), "support_staff_emails", "staff@omniflow.sa")
        p.start()
        self.addCleanup(p.stop)
        async with self.Admin() as s:
            await s.execute(self.text("DELETE FROM subscriptions WHERE tenant_id = ANY(:t)"), {"t": self.tenants})
            await s.commit()

    async def pay_first(self, tenant=None, days_ago=2, kind="initial", provider="moyasar"):
        t = tenant or self.tenants[0]
        async with self.Admin() as s:
            await s.execute(self.text("INSERT INTO subscriptions (tenant_id,plan_code,status,is_exempt,billing_interval,current_period_start,current_period_end,provider,payment_token) "
                                      "VALUES (:t,'ctest','active',false,'monthly',now(),now()+interval '30 days',:p,'enc') ON CONFLICT (tenant_id) DO NOTHING"), {"t": t, "p": provider})
            sid = (await s.execute(self.text("SELECT subscription_id FROM subscriptions WHERE tenant_id=:t"), {"t": t})).scalar()
            tid = uuid.uuid4()
            await s.execute(self.text("INSERT INTO transactions (transaction_id,tenant_id,subscription_id,kind,plan_code,billing_interval,subtotal_halalas,vat_halalas,amount_halalas,status,provider,"
                                      "provider_payment_id,paid_at) VALUES (:x,:t,:s,:k,'ctest','monthly',50000,7500,57500,'paid',:p,'pay_'||:xs, now() - (:d || ' days')::interval)"),
                            {"x": tid, "xs": tid.hex, "t": t, "s": sid, "k": kind, "p": provider, "d": str(days_ago)})
            await s.execute(self.text("INSERT INTO invoices (tenant_id,transaction_id,invoice_number,subtotal_halalas,vat_halalas,total_halalas,vat_rate_bp,description) "
                                      "VALUES (:t,:x,:n,50000,7500,57500,1500,'اشتراك')"), {"t": t, "x": tid, "n": f"INV-T-{uuid.uuid4().hex[:6]}"})
            await s.commit()
        return tid

    def staff_app(self):
        app = create_app()
        auth_overrides(app, self.tenants[0], self.App, role=TenantUserRole.ADMIN)
        app.dependency_overrides[deps.get_current_user] = lambda: NS(tenant_id=self.tenants[0], user_id=uuid.uuid4(), role=TenantUserRole.ADMIN,
                                                                     email=self.staff, is_active=True)
        return app

    async def test_full_refund_within_the_window_with_consumption_and_fee_deducted(self):
        t = self.tenants[0]
        txn = await self.pay_first()
        async with self.Admin() as s:                                         # 3.00 SAR of measured AI cost since the payment
            await s.execute(self.text("INSERT INTO llm_usage (tenant_id,event_id,model,tier,cost_sar,cost_source) VALUES (:t,gen_random_uuid(),'m','L1',3.00,'measured')"), {"t": t})
            await s.commit()
        with patch.object(get_settings(), "billing_gateway_fee_bp", 275), patch.object(get_settings(), "billing_gateway_fee_fixed_halalas", 100):
            async with client_for(self.staff_app()) as c:
                q = (await c.get(f"{BASE}/admin/transactions/{txn}/refund-quote")).json()
                fee = (57500 * 275 + 5000) // 10000 + 100
                self.assertEqual((q["eligible"], q["consumed_halalas"], q["fee_halalas"]), (True, 300, fee))
                remaining = 50000 - 300 - fee
                self.assertEqual((q["subtotal_halalas"], q["vat_halalas"], q["amount_halalas"]), (remaining, (remaining * 1500 + 5000) // 10000, remaining + (remaining * 1500 + 5000) // 10000))
                r = await c.post(f"{BASE}/admin/refunds", json={"transaction_id": str(txn), "reason": "طلب العميل خلال فترة الضمان"})
        self.assertEqual(r.status_code, 201, r.text)
        out = r.json()
        self.assertEqual(out["amount_halalas"], q["amount_halalas"])
        self.assertRegex(out["credit_note_number"], r"^CN-\d{4}-\d{6}$")
        self.assertEqual(self.fake.refunds[0]["amount_halalas"], q["amount_halalas"])
        sub = (await self.rows("SELECT status, payment_token FROM subscriptions WHERE tenant_id=:t", t=t))[0]
        self.assertEqual((sub["status"], sub["payment_token"]), ("cancelled", None))                    # a full first-payment refund ends the plan
        self.assertEqual((await self.rows("SELECT status FROM invoices WHERE tenant_id=:t", t=t))[0]["status"], "credited")
        note = (await self.rows("SELECT subtotal_halalas, vat_halalas, total_halalas FROM credit_notes WHERE tenant_id=:t", t=t))[0]
        self.assertEqual(note["subtotal_halalas"] + note["vat_halalas"], note["total_halalas"])
        audit = await self.rows("SELECT details FROM audit_logs WHERE tenant_id=:t AND action='billing.refund'", t=t)
        self.assertEqual((len(audit), audit[0]["details"]["staff"]), (1, self.staff))
        # the customer can fetch the credit note PDF; another tenant cannot
        app = self.app_as()
        async with client_for(app) as c:
            items = (await c.get(f"{BASE}/credit-notes")).json()["items"]
            self.assertEqual(len(items), 1)
            pdf = await c.get(f"{BASE}/credit-notes/{items[0]['credit_note_id']}/pdf")
            self.assertEqual((pdf.status_code, pdf.content[:5]), (200, b"%PDF-"))
        async with client_for(self.app_as(tenant=self.tenants[1])) as c:
            self.assertEqual((await c.get(f"{BASE}/credit-notes")).json()["items"], [])
            self.assertEqual((await c.get(f"{BASE}/credit-notes/{items[0]['credit_note_id']}/pdf")).status_code, 404)

    async def test_a_second_refund_is_refused_and_the_provider_is_not_called_twice(self):
        txn = await self.pay_first()
        async with client_for(self.staff_app()) as c:
            self.assertEqual((await c.post(f"{BASE}/admin/refunds", json={"transaction_id": str(txn), "reason": "الضمان 14 يوم"})).status_code, 201)
            again = await c.post(f"{BASE}/admin/refunds", json={"transaction_id": str(txn), "reason": "مرة ثانية"})
        self.assertEqual((again.status_code, again.json()["detail"]["code"]), (409, "NOT_REFUNDABLE"))
        self.assertEqual(len(self.fake.refunds), 1)

    async def test_policy_limits_window_first_payment_only_and_paid_status(self):
        late = await self.pay_first(days_ago=20)
        async with client_for(self.staff_app()) as c:
            r = await c.post(f"{BASE}/admin/refunds", json={"transaction_id": str(late), "reason": "متأخر"})
            self.assertEqual((r.status_code, r.json()["detail"]["code"]), (409, "NOT_REFUNDABLE"))
            self.assertIn("انتهت مهلة الضمان", r.json()["detail"]["message"])
        t2 = self.tenants[1]
        first = await self.pay_first(t2)
        renewal = await self.pay_first(t2, kind="renewal")
        async with client_for(self.staff_app()) as c:
            r = await c.post(f"{BASE}/admin/refunds", json={"transaction_id": str(renewal), "reason": "تجديد"})
            self.assertIn("أول دفعة", r.json()["detail"]["message"])
            q = (await c.get(f"{BASE}/admin/transactions/{first}/refund-quote")).json()
            self.assertTrue(q["eligible"])
        self.assertEqual(self.fake.refunds if hasattr(self.fake, "refunds") else [], [])

    async def test_only_staff_can_refund_and_the_amount_can_only_go_down(self):
        txn = await self.pay_first()
        async with client_for(self.app_as()) as c:                                              # a tenant admin is not staff
            self.assertEqual((await c.post(f"{BASE}/admin/refunds", json={"transaction_id": str(txn), "reason": "طلب"})).status_code, 404)
        async with client_for(self.staff_app()) as c:
            high = await c.post(f"{BASE}/admin/refunds", json={"transaction_id": str(txn), "reason": "أكثر من المسموح", "amount_halalas": 99999})
            self.assertEqual((high.status_code, high.json()["detail"]["code"]), (422, "AMOUNT_TOO_HIGH"))
            part = await c.post(f"{BASE}/admin/refunds", json={"transaction_id": str(txn), "reason": "استرداد جزئي", "amount_halalas": 11500})
            self.assertEqual(part.status_code, 201, part.text)
            self.assertEqual((part.json()["amount_halalas"], part.json()["subtotal_halalas"], part.json()["vat_halalas"]), (11500, 10000, 1500))
        sub = (await self.rows("SELECT status FROM subscriptions WHERE tenant_id=:t", t=self.tenants[0]))[0]
        self.assertEqual(sub["status"], "active")                                               # a partial refund keeps the plan running

    async def test_provider_failure_is_recorded_and_changes_nothing_else(self):
        t = self.tenants[0]
        txn = await self.pay_first()
        self.fake.refund_error = ProviderError("gateway down", retryable=True, code="network")
        async with client_for(self.staff_app()) as c:
            r = await c.post(f"{BASE}/admin/refunds", json={"transaction_id": str(txn), "reason": "طلب العميل"})
        self.assertEqual((r.status_code, r.json()["detail"]["code"]), (502, "PROVIDER_REFUND_FAILED"))
        row = (await self.rows("SELECT status, failure_reason FROM refunds WHERE tenant_id=:t", t=t))[0]
        self.assertEqual(row["status"], "failed")
        self.assertEqual(await self.rows("SELECT 1 FROM credit_notes WHERE tenant_id=:t", t=t), [])
        self.assertEqual((await self.rows("SELECT status FROM subscriptions WHERE tenant_id=:t", t=t))[0]["status"], "active")
        self.fake.refund_error = None                                                           # a retry after the outage works
        async with client_for(self.staff_app()) as c:
            self.assertEqual((await c.post(f"{BASE}/admin/refunds", json={"transaction_id": str(txn), "reason": "إعادة المحاولة"})).status_code, 201)

    async def test_bank_transfer_payments_are_refunded_manually_without_a_provider_call(self):
        txn = await self.pay_first(provider="manual")
        async with client_for(self.staff_app()) as c:
            r = await c.post(f"{BASE}/admin/refunds", json={"transaction_id": str(txn), "reason": "تحويل بنكي"})
        self.assertEqual((r.status_code, r.json()["manual_transfer_required"]), (201, True))
        self.assertFalse(getattr(self.fake, "refunds", []))

    # ── trial identity ──────────────────────────────────────────────────────────────
    async def test_the_same_company_cannot_start_a_second_trial(self):
        a, b = self.tenants
        async with await self._sys() as db:
            await trial_identity.claim(db, a, cr="1010 123 456", phone="0501234567")
            await trial_identity.claim(db, a, cr="1010123456")                                  # same tenant again: fine
        async with await self._sys() as db:
            with self.assertRaises(trial_identity.TrialAlreadyUsed) as cm:
                await trial_identity.claim(db, b, cr="1010-123-456")
            self.assertEqual(cm.exception.kind, "cr")
        async with await self._sys() as db:
            with self.assertRaises(trial_identity.TrialAlreadyUsed) as cm:
                await trial_identity.claim(db, b, phone="+966501234567")
            self.assertEqual(cm.exception.kind, "phone")
        stored = await self.rows("SELECT value_hash FROM trial_identities WHERE tenant_id=:t", t=a)
        self.assertTrue(all(len(r["value_hash"]) == 64 and "1010" not in r["value_hash"] for r in stored))   # hashes only

    async def test_onboarding_endpoint_blocks_a_repeat_company_with_409(self):
        async with await self._sys() as db:
            await trial_identity.claim(db, self.tenants[1], cr="7001234567")
        p = patch("src.gateway.routers.tenants.AsyncSessionFactory", self.App)
        p.start()
        self.addCleanup(p.stop)
        app = create_app()
        row = NS(tenant_id=self.tenants[0], user_id=uuid.uuid4(), role=TenantUserRole.ADMIN, email="o@example.sa", is_active=True)
        app.dependency_overrides[deps.get_current_user] = lambda: row
        async with client_for(app) as c:
            r = await c.patch("/api/v1/tenants/onboarding", json={"option": "self_service", "commercial_registration": "700-123-4567"})
        self.assertEqual((r.status_code, r.json()["detail"]["code"], r.json()["detail"]["kind"]), (409, "TRIAL_ALREADY_USED", "cr"))
        self.assertEqual((await self.rows("SELECT onboarding_status FROM tenants WHERE tenant_id=:t", t=self.tenants[0]))[0]["onboarding_status"], "completed")


if __name__ == "__main__":
    unittest.main()

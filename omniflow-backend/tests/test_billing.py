"""Billing: pricing maths, access states, Moyasar adapter shapes, and the full subscription lifecycle on PostgreSQL + RLS.

Pure tests run everywhere; the DB class needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL (see test_customers.py).
NOT covered here (needs real Moyasar sandbox keys): the live HTTP contract with Moyasar.
"""
import asyncio
import json
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import httpx

from src.gateway.main import create_app
from src.shared.core.config import get_settings
from src.shared.core.enums import TenantUserRole
from src.shared.services.billing import access, pricing
from src.shared.services.billing import invoice_pdf, providers
from src.shared.services.billing.providers import moyasar
from src.shared.services.billing.providers.base import Checkout, PaymentInfo, ProviderError, WebhookEvent, WebhookRejected
from tests.test_property_import_flow import auth_overrides, client_for

DB_URL = os.environ.get("OMNIFLOW_TEST_DATABASE_URL")
RLS_URL = os.environ.get("OMNIFLOW_TEST_RLS_URL")
BASE = "/api/v1/billing"
UTC = timezone.utc
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def sub(**kw):
    d = dict(status="active", is_exempt=False, trial_ends_at=None, current_period_end=None, grace_ends_at=None, cancel_at_period_end=False)
    d.update(kw)
    return NS(**d)


def ev(s, now=T0, **kw):
    return access.evaluate(s, now, grace_days=7, **kw)


class PricingTests(unittest.TestCase):
    def test_vat_is_added_on_top_and_rounds_half_up_without_losing_a_halala(self):
        a = pricing.with_vat(29900, 1500)
        self.assertEqual((a.subtotal, a.vat, a.total), (29900, 4485, 34385))
        self.assertEqual(pricing.with_vat(10, 1500).vat, 2)                       # 1.5 → 2 (half-up)
        self.assertEqual(pricing.with_vat(0, 1500).total, 0)
        for sub_total in (1, 99, 12345, 99999):
            x = pricing.with_vat(sub_total, 1500)
            self.assertEqual(x.total, x.subtotal + x.vat)
        with self.assertRaises(ValueError):
            pricing.with_vat(-1, 1500)

    def test_calendar_aware_periods(self):
        self.assertEqual(pricing.add_interval(datetime(2026, 1, 31, tzinfo=UTC), "monthly"), datetime(2026, 2, 28, tzinfo=UTC))
        self.assertEqual(pricing.add_interval(datetime(2028, 1, 31, tzinfo=UTC), "monthly"), datetime(2028, 2, 29, tzinfo=UTC))
        self.assertEqual(pricing.add_interval(datetime(2028, 2, 29, tzinfo=UTC), "yearly"), datetime(2029, 2, 28, tzinfo=UTC))
        with self.assertRaises(ValueError):
            pricing.add_interval(T0, "weekly")

    def test_proration_charges_only_the_difference_for_the_time_left(self):
        start, end = datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 31, tzinfo=UTC)
        half = start + (end - start) / 2
        self.assertEqual(pricing.prorated_upgrade(10000, 30000, start, end, half), 10000)
        self.assertEqual(pricing.prorated_upgrade(10000, 30000, start, end, start), 20000)
        self.assertEqual(pricing.prorated_upgrade(10000, 30000, start, end, end), 0)
        self.assertEqual(pricing.prorated_upgrade(30000, 10000, start, end, half), 0)       # downgrade never refunds/charges here
        self.assertEqual(pricing.invoice_number(42, 2026), "INV-2026-000042")


class AccessTests(unittest.TestCase):
    def test_trial(self):
        self.assertEqual(ev(sub(status="trial", trial_ends_at=T0 + timedelta(days=10))).mode, "full")
        a = ev(sub(status="trial", trial_ends_at=T0 + timedelta(days=2)))
        self.assertEqual((a.mode, a.banner, a.days_left), ("full", "trial_ending", 2))
        a = ev(sub(status="trial", trial_ends_at=T0 - timedelta(seconds=1)))
        self.assertEqual((a.mode, a.reason, a.bot_enabled, a.writable), ("blocked", "trial_expired", False, False))

    def test_active_overdue_has_a_short_window_then_stops_even_if_the_sweep_is_down(self):
        end = T0 - timedelta(days=1)
        self.assertEqual(ev(sub(current_period_end=T0 + timedelta(days=5))).mode, "full")
        a = ev(sub(current_period_end=end))
        self.assertEqual((a.mode, a.banner), ("grace", "past_due"))
        self.assertTrue(a.bot_enabled and a.writable)
        a = ev(sub(current_period_end=T0 - timedelta(days=8)))
        self.assertEqual((a.mode, a.reason), ("blocked", "payment_overdue"))

    def test_past_due_suspended_cancelled(self):
        self.assertEqual(ev(sub(status="past_due", grace_ends_at=T0 + timedelta(days=3))).mode, "grace")
        self.assertEqual(ev(sub(status="past_due", grace_ends_at=T0 - timedelta(minutes=1))).reason, "payment_overdue")
        self.assertEqual(ev(sub(status="past_due", grace_ends_at=None)).mode, "blocked")
        self.assertEqual(ev(sub(status="suspended")).reason, "suspended")
        self.assertEqual(ev(sub(status="cancelled")).reason, "cancelled")
        self.assertEqual(ev(sub(status="weird")).mode, "blocked")                      # unknown state fails closed
        a = ev(sub(cancel_at_period_end=True, current_period_end=T0 - timedelta(hours=1)))
        self.assertEqual((a.mode, a.reason), ("blocked", "cancelled"))
        self.assertEqual(ev(sub(cancel_at_period_end=True, current_period_end=T0 + timedelta(days=2))).mode, "full")

    def test_exempt_missing_row_and_kill_switch_are_never_gated(self):
        self.assertEqual(ev(sub(status="suspended", is_exempt=True)).mode, "full")
        self.assertEqual(ev(None).mode, "full")
        self.assertEqual(ev(sub(status="suspended"), gate_enabled=False).mode, "full")


class FakeProvider:
    """Stands in for Moyasar: same contract, in-memory payments, our own shared-secret webhook check."""
    name = "moyasar"
    SECRET = "whsec-test"      # pragma: allowlist secret

    def __init__(self):
        self.payments: dict[str, PaymentInfo] = {}
        self.invoice_payments: dict[str, list[PaymentInfo]] = {}
        self.checkouts: list[dict] = []
        self.charges: list[dict] = []
        self.charge_result = "paid"            # paid | failed | raise
        self.fetch_error: ProviderError | None = None
        self.checkout_error: ProviderError | None = None
        self.token = "tok_abc123"              # pragma: allowlist secret

    async def create_checkout(self, **kw):
        if self.checkout_error:
            raise self.checkout_error
        self.checkouts.append(kw)
        return Checkout(provider_invoice_id=f"inv_{len(self.checkouts)}", url=f"https://pay.example/{len(self.checkouts)}")

    async def fetch_payment(self, payment_id):
        if self.fetch_error:
            raise self.fetch_error
        return self.payments[payment_id]

    async def fetch_invoice_payments(self, provider_invoice_id):
        return self.invoice_payments.get(provider_invoice_id, [])

    async def charge_token(self, **kw):
        self.charges.append(kw)
        if self.charge_result == "raise":
            raise ProviderError("down", retryable=True, code="network")
        pid = f"pay_{uuid.uuid4().hex[:8]}"
        status = self.charge_result
        info = PaymentInfo(pid, status, kw["amount_halalas"], kw["currency"], transaction_id=kw["transaction_id"],
                           failure_reason="insufficient_funds" if status == "failed" else None)
        self.payments[pid] = info
        return info

    def parse_webhook(self, headers, body):
        data = json.loads(body)
        if data.get("secret_token") != self.SECRET:
            raise WebhookRejected("bad secret")
        return WebhookEvent(event_key=data["id"], event_type=data["type"],
                            payment=PaymentInfo(data["payment_id"], "other", 0, "SAR") if data.get("payment_id") else None, raw={"id": data["id"]})

    def pay(self, txn_id, amount, status="paid", token=True, pid=None):
        pid = pid or f"pay_{uuid.uuid4().hex[:8]}"
        self.payments[pid] = PaymentInfo(pid, status, amount, "SAR", transaction_id=str(txn_id), token=self.token if token else None,
                                         card_brand="mada", card_last4="4242", failure_reason="declined" if status == "failed" else None)
        return pid

    @staticmethod
    def hook(event_id, pid, etype="payment_paid", secret=SECRET):
        return {"id": event_id, "type": etype, "payment_id": pid, "secret_token": secret}


class MoyasarAdapterTests(unittest.IsolatedAsyncioTestCase):
    def cfg(self, **kw):
        base = dict(moyasar_secret_key="sk_test_abc", moyasar_webhook_secret="hook-secret", moyasar_base_url="https://m.test/v1", app_env="development")  # pragma: allowlist secret
        base.update(kw)
        return patch.object(moyasar, "get_settings", return_value=NS(**base))

    def prov(self, handler):
        return moyasar.MoyasarProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    async def test_checkout_request_shape_and_response_parsing(self):
        seen = {}

        def handler(req):
            seen.update(url=str(req.url), auth=req.headers["authorization"], body=json.loads(req.content))
            return httpx.Response(201, json={"id": "inv_9", "url": "https://checkout.moyasar.test/inv_9"})
        with self.cfg():
            co = await self.prov(handler).create_checkout(transaction_id="t-1", amount_halalas=34385, currency="SAR", description="d",
                                                          success_url="https://app/ok", back_url="https://app/back", metadata={"tenant_id": "x"})
        self.assertEqual(co.url, "https://checkout.moyasar.test/inv_9")
        self.assertEqual(seen["url"], "https://m.test/v1/invoices")
        self.assertTrue(seen["auth"].startswith("Basic "))
        self.assertEqual((seen["body"]["amount"], seen["body"]["currency"], seen["body"]["metadata"]["transaction_id"]), (34385, "SAR", "t-1"))

    async def test_errors_are_classified_and_unconfigured_or_live_keys_refused(self):
        with self.cfg():
            with self.assertRaises(ProviderError) as c:
                await self.prov(lambda r: httpx.Response(503)).fetch_payment("p")
            self.assertTrue(c.exception.retryable)
            with self.assertRaises(ProviderError) as c:
                await self.prov(lambda r: httpx.Response(400, json={"message": "bad"})).fetch_payment("p")
            self.assertFalse(c.exception.retryable)
        with self.cfg(moyasar_secret_key=""):
            with self.assertRaises(ProviderError) as c:
                await self.prov(lambda r: httpx.Response(200, json={})).fetch_payment("p")
            self.assertEqual(c.exception.code, "not_configured")
        with self.cfg(moyasar_secret_key="sk_live_x", app_env="development"):  # pragma: allowlist secret
            with self.assertRaises(ProviderError) as c:
                await self.prov(lambda r: httpx.Response(200, json={})).fetch_payment("p")
            self.assertEqual(c.exception.code, "live_key_outside_production")

    async def test_payment_parsing_and_token_charge_uses_the_transaction_as_idempotency_key(self):
        pay = {"id": "pay_1", "status": "paid", "amount": 34385, "currency": "sar", "invoice_id": "inv_1", "metadata": {"transaction_id": "t-9"},
               "source": {"type": "creditcard", "company": "mada", "number": "4111-11XX-XXXX-1111", "token": "tok_1"}}
        seen = {}

        def handler(req):
            seen["body"] = json.loads(req.content)
            return httpx.Response(201, json=pay)
        with self.cfg():
            info = await self.prov(handler).charge_token(token="tok_1", transaction_id="t-9", amount_halalas=34385, currency="SAR", description="r", metadata={})
        self.assertEqual((info.status, info.amount_halalas, info.currency, info.transaction_id, info.card_last4, info.token),
                         ("paid", 34385, "SAR", "t-9", "1111", "tok_1"))
        self.assertEqual(seen["body"]["given_id"], "t-9")
        self.assertEqual(seen["body"]["source"], {"type": "token", "token": "tok_1"})

    def test_webhook_secret_is_checked_in_constant_time_and_never_stored(self):
        body = json.dumps({"id": "evt_1", "type": "payment_paid", "secret_token": "hook-secret", "live": False,  # pragma: allowlist secret
                           "data": {"id": "pay_1", "status": "paid", "amount": 100, "currency": "SAR", "source": {"number": "4111111111111111"}}}).encode()
        with self.cfg():
            e = moyasar.MoyasarProvider().parse_webhook({}, body)
            self.assertEqual((e.event_key, e.event_type, e.payment.payment_id), ("evt_1", "payment_paid", "pay_1"))
            self.assertNotIn("secret_token", json.dumps(e.raw))
            self.assertNotIn("4111", json.dumps(e.raw))
            for bad in (b"not json", b"[]", json.dumps({"id": "e", "type": "t", "secret_token": "nope"}).encode(),  # pragma: allowlist secret
                        json.dumps({"id": "e", "type": "t"}).encode(), json.dumps({"type": "t", "secret_token": "hook-secret"}).encode()):  # pragma: allowlist secret
                with self.assertRaises(WebhookRejected, msg=bad):
                    moyasar.MoyasarProvider().parse_webhook({}, bad)
        with self.cfg(moyasar_webhook_secret=""):
            with self.assertRaises(WebhookRejected):
                moyasar.MoyasarProvider().parse_webhook({}, body)             # unconfigured secret ⇒ nothing is accepted


class InvoiceTests(unittest.TestCase):
    def test_invoice_splits_vat_and_is_honest_about_missing_tax_number_and_zatca(self):
        inv = NS(invoice_number="INV-2026-000001", issued_at=T0, currency="SAR", seller={"name": "OmniFlow", "tax_number": "", "cr_number": "", "address": ""},
                 buyer={"name": "شركة <b>الاختبار</b>", "fal_license": "FAL-1"}, description="اشتراك", period_start=T0, period_end=T0 + timedelta(days=30),
                 subtotal_halalas=29900, vat_halalas=4485, total_halalas=34385, vat_rate_bp=1500)
        html = invoice_pdf.invoice_html(inv)
        for needle in ("299.00", "44.85", "343.85", "15%", "غير مضبوط بعد", "لا تتضمن رمز الاستجابة السريعة"):
            self.assertIn(needle, html)
        self.assertNotIn("<b>الاختبار", html)                                    # buyer name is escaped
        pdf = invoice_pdf.build_invoice_pdf(inv)
        self.assertTrue(pdf.startswith(b"%PDF-") and len(pdf) > 3000)


@unittest.skipUnless(DB_URL and RLS_URL, "needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL (non-superuser: RLS is the tenant guard)")
class BillingDbTests(unittest.IsolatedAsyncioTestCase):
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
                    "VALUES (:t,:n,:f,'economic','trial',100,'completed',now(),now())"), {"t": t, "n": f"شركة {i}", "f": f"FAL-{t.hex[:8]}"})
            await s.commit()
        self.fake = FakeProvider()
        providers.set_provider_for_tests(self.fake)
        self.addCleanup(providers.set_provider_for_tests, None)
        cfg = get_settings()
        for name, value in (("billing_gate_enabled", True), ("moyasar_secret_key", "sk_test_x"), ("app_env", "development"),   # pragma: allowlist secret
                            ("billing_seller_name", "OmniFlow للتقنية"), ("billing_admin_api_key", ""), ("billing_trial_days", 14)):
            p = patch.object(cfg, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch("src.shared.db.session.AsyncSessionFactory", self.App)
        p.start()
        self.addCleanup(p.stop)
        self.app = self.app_as(TenantUserRole.ADMIN)

    async def asyncTearDown(self):
        async with self.Admin() as s:
            for t in self.tenants:
                for stmt in ("DELETE FROM usage_counters WHERE tenant_id=:t", "DELETE FROM payment_events WHERE tenant_id=:t",
                             "DELETE FROM invoices WHERE tenant_id=:t", "DELETE FROM transactions WHERE tenant_id=:t",
                             "DELETE FROM audit_logs WHERE tenant_id=:t", "DELETE FROM subscriptions WHERE tenant_id=:t",
                             "DELETE FROM customers WHERE tenant_id=:t", "DELETE FROM tenants WHERE tenant_id=:t"):
                    await s.execute(self.text(stmt), {"t": t})
            await s.execute(self.text("DELETE FROM payment_events WHERE tenant_id IS NULL AND event_key LIKE 'evt-%'"))
            await s.commit()
        await self.admin.dispose()
        await self.app_engine.dispose()

    def app_as(self, role, tenant=None):
        app = create_app()
        auth_overrides(app, tenant or self.tenants[0], self.App, role=role)
        return app

    async def sysdb(self):
        from src.shared.db.session import get_system_session
        return get_system_session()

    async def row(self, sql, **p):
        async with self.Admin() as s:
            return (await s.execute(self.text(sql), p)).mappings().all()

    async def hook(self, c, event_id, pid, **kw):
        return await c.post("/api/v1/webhooks/payments", json=self.fake.hook(event_id, pid, **kw))

    async def subscribe(self, c, plan="pro", interval="monthly"):
        r = await c.post(f"{BASE}/checkout", json={"plan_code": plan, "interval": interval})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    # ── the main journey ────────────────────────────────────────────────────────────
    async def test_full_lifecycle_trial_active_failed_past_due_suspended_reactivated(self):
        from src.shared.services.billing import service
        t = self.tenants[0]
        async with client_for(self.app) as c:
            st = (await c.get(f"{BASE}/status")).json()
            self.assertEqual((st["mode"], st["status"], st["is_exempt"]), ("full", "trial", False))       # lazy trial for a new tenant
            plans = (await c.get(f"{BASE}/plans")).json()["items"]
            self.assertEqual([p["code"] for p in plans], ["starter", "pro", "business", "enterprise"])
            self.assertFalse(any(p["is_placeholder"] for p in plans))
            self.assertEqual([p["contact_sales"] for p in plans], [False, False, False, True])
            pro = next(p for p in plans if p["code"] == "pro")
            self.assertEqual((pro["monthly"]["subtotal_halalas"], pro["monthly"]["total_halalas"]), (79900, 91885))

            first = await self.subscribe(c)
            txn = first["transaction"]
            self.assertEqual((txn["status"], txn["amount_halalas"], first["checkout_url"].startswith("https://pay.example/")), ("pending", 91885, True))
            again = await self.subscribe(c)                                                                 # double click → same page
            self.assertEqual(again["transaction"]["transaction_id"], txn["transaction_id"])
            self.assertEqual(len(self.fake.checkouts), 1)
            self.assertEqual(self.fake.checkouts[0]["metadata"]["tenant_id"], str(t))

            # forged / tampered deliveries first
            self.assertEqual((await c.post("/api/v1/webhooks/payments", json=self.fake.hook("evt-x", "p", secret="wrong"))).status_code, 401)
            bad_pid = self.fake.pay(txn["transaction_id"], 100)                                             # provider says a different amount
            r = await self.hook(c, "evt-tamper", bad_pid)
            self.assertEqual(r.json()["outcome"], "rejected")
            self.assertEqual((await c.get(f"{BASE}/subscription")).json()["status"], "trial")

            pid = self.fake.pay(txn["transaction_id"], 91885)
            r = await self.hook(c, "evt-paid-1", pid)
            self.assertEqual((r.status_code, r.json()["outcome"]), (200, "applied"))
            self.assertEqual((await self.hook(c, "evt-paid-1", pid)).json()["outcome"], "duplicate")        # same event again
            self.assertEqual((await self.hook(c, "evt-paid-2", pid)).json()["outcome"], "ignored")          # new event, already settled

            s = (await c.get(f"{BASE}/subscription")).json()
            self.assertEqual((s["status"], s["plan"]["code"], s["auto_renew"], s["card"]), ("active", "pro", True, {"brand": "mada", "last4": "4242"}))
            invs = (await c.get(f"{BASE}/invoices")).json()["items"]
            self.assertEqual(len(invs), 1)
            self.assertEqual((invs[0]["subtotal_halalas"], invs[0]["vat_halalas"], invs[0]["total_halalas"]), (79900, 11985, 91885))
            pdf = await c.get(f"{BASE}/invoices/{invs[0]['invoice_id']}/pdf")
            self.assertEqual((pdf.status_code, pdf.content[:5], pdf.headers["content-type"]), (200, b"%PDF-", "application/pdf"))
        tenant = (await self.row("SELECT status, subscription_tier FROM tenants WHERE tenant_id=:t", t=t))[0]
        self.assertEqual((tenant["status"], tenant["subscription_tier"]), ("active", "pro"))
        tok = (await self.row("SELECT payment_token FROM subscriptions WHERE tenant_id=:t", t=t))[0]["payment_token"]
        self.assertNotIn("tok_abc123", tok)                                                                 # stored encrypted

        # ── renewal fails 3× (24 h apart) → past_due with a pay link, still usable during grace ──
        async with await self.sysdb() as db:
            end = (await service.ensure_subscription(db, t)).current_period_end
        self.fake.charge_result = "failed"
        outs = []
        for i in range(3):
            async with await self.sysdb() as db:
                outs.append(await service.run_sweep(db, service.default_urls(), now=end + timedelta(hours=1 + 24 * i)))
        self.assertEqual([o["retry_scheduled"] for o in outs], [1, 1, 0])
        self.assertEqual(outs[2]["past_due"], 1)
        async with client_for(self.app) as c:
            s = (await c.get(f"{BASE}/subscription")).json()
            self.assertEqual((s["status"], s["retry_count"]), ("past_due", 3))
            self.assertEqual(s["last_payment_error"], "insufficient_funds")
            self.assertIsNotNone(s["pending_checkout"])                                                     # the dashboard shows this link
            self.assertEqual(len(self.fake.charges), 3)
        async with await self.sysdb() as db:
            now = end + timedelta(days=3)
            acc = service.access_of(await service.ensure_subscription(db, t), now)
        self.assertEqual((acc.mode, acc.banner), ("grace", "past_due"))

        # ── grace over → suspended: bot silent, dashboard read-only, billing still reachable ──
        async with await self.sysdb() as db:
            out = await service.run_sweep(db, service.default_urls(), now=end + timedelta(days=3 + 8))
        self.assertEqual(out["suspended"], 1)
        from src.shared.services.billing import entitlements
        self.assertEqual(await entitlements.bot_allowed(t), (False, "suspended"))
        async with client_for(self.app) as c:
            blocked = await c.delete(f"/api/v1/customers/{uuid.uuid4()}")
            self.assertEqual((blocked.status_code, blocked.json()["detail"]["code"]), (402, "SUBSCRIPTION_INACTIVE"))
            self.assertEqual((await c.get("/api/v1/customers")).status_code, 200)                           # reads still work
            self.assertEqual((await c.get(f"{BASE}/status")).json()["mode"], "blocked")
            self.assertEqual((await c.patch(f"/api/v1/support/tickets/{uuid.uuid4()}", json={"status": "closed"})).status_code, 404)   # support is never gated (404 = reached the handler)
            self.assertEqual((await c.get(f"{BASE}/subscription")).json()["status"], "suspended")
            # the old pay link has expired by now; a suspended tenant simply subscribes again from the billing page
            link = (await self.subscribe(c))["transaction"]
            self.assertEqual(link["kind"], "initial")
            pid = self.fake.pay(link["transaction_id"], link["amount_halalas"])
            self.assertEqual((await self.hook(c, "evt-reactivate", pid)).json()["outcome"], "applied")
            s = (await c.get(f"{BASE}/subscription")).json()
            self.assertEqual((s["status"], s["access"]["mode"], s["retry_count"]), ("active", "full", 0))
            self.assertEqual((await c.delete(f"/api/v1/customers/{uuid.uuid4()}")).status_code, 404)       # gate open again (404 = reached the handler)
            self.assertEqual(len((await c.get(f"{BASE}/invoices")).json()["items"]), 2)
        self.assertEqual(await entitlements.bot_allowed(t), (True, None))
        nums = [r["invoice_number"] for r in await self.row("SELECT invoice_number FROM invoices WHERE tenant_id=:t ORDER BY issued_at", t=t)]
        self.assertEqual(len(set(nums)), 2)

    async def test_successful_token_renewal_extends_the_period_without_a_gap(self):
        from src.shared.services import billing  # noqa: F401
        from src.shared.services.billing import service
        t = self.tenants[0]
        async with client_for(self.app) as c:
            txn = (await self.subscribe(c))["transaction"]
            await self.hook(c, "evt-1", self.fake.pay(txn["transaction_id"], txn["amount_halalas"]))
        async with await self.sysdb() as db:
            s0 = await service.ensure_subscription(db, t)
            end0, start0 = s0.current_period_end, s0.current_period_start
        self.fake.charge_result = "paid"
        async with await self.sysdb() as db:
            out = await service.run_sweep(db, service.default_urls(), now=end0 + timedelta(minutes=30))
            again = await service.run_sweep(db, service.default_urls(), now=end0 + timedelta(minutes=31))
        self.assertEqual((out["renewed"], again["renewed"]), (1, 0))                                         # second run finds nothing due
        self.assertEqual(len(self.fake.charges), 1)
        async with await self.sysdb() as db:
            s1 = await service.ensure_subscription(db, t)
        self.assertEqual(s1.current_period_start, end0)
        self.assertEqual(s1.current_period_end, pricing.add_interval(end0, "monthly"))
        self.assertGreater(s1.current_period_end, end0)
        self.assertEqual(self.fake.charges[0]["amount_halalas"], 91885)

    async def test_provider_outage_during_renewal_counts_as_a_failed_attempt_but_never_suspends_immediately(self):
        from src.shared.services.billing import service
        t = self.tenants[0]
        async with client_for(self.app) as c:
            txn = (await self.subscribe(c))["transaction"]
            await self.hook(c, "evt-1", self.fake.pay(txn["transaction_id"], txn["amount_halalas"]))
        async with await self.sysdb() as db:
            end = (await service.ensure_subscription(db, t)).current_period_end
        self.fake.charge_result = "raise"
        async with await self.sysdb() as db:
            out = await service.run_sweep(db, service.default_urls(), now=end + timedelta(hours=1))
        self.assertEqual(out["retry_scheduled"], 1)
        async with await self.sysdb() as db:
            self.assertEqual((await service.ensure_subscription(db, t)).status, "active")

    async def test_no_saved_card_goes_straight_to_past_due_with_a_pay_link(self):
        from src.shared.services.billing import service
        t = self.tenants[0]
        async with client_for(self.app) as c:
            txn = (await self.subscribe(c))["transaction"]
            await self.hook(c, "evt-1", self.fake.pay(txn["transaction_id"], txn["amount_halalas"], token=False))
        async with await self.sysdb() as db:
            end = (await service.ensure_subscription(db, t)).current_period_end
            out = await service.run_sweep(db, service.default_urls(), now=end + timedelta(hours=1))
        self.assertEqual(out["past_due"], 1)
        self.assertEqual(self.fake.charges, [])
        async with client_for(self.app) as c:
            self.assertIsNotNone((await c.get(f"{BASE}/subscription")).json()["pending_checkout"])

    async def test_concurrent_duplicate_webhooks_apply_exactly_once(self):
        t = self.tenants[0]
        async with client_for(self.app) as c:
            txn = (await self.subscribe(c))["transaction"]
            pid = self.fake.pay(txn["transaction_id"], txn["amount_halalas"])
            rs = await asyncio.gather(*[self.hook(c, "evt-race", pid) for _ in range(4)])
        outcomes = sorted(r.json()["outcome"] for r in rs)
        self.assertEqual(outcomes.count("applied"), 1, outcomes)
        self.assertEqual(len(await self.row("SELECT 1 FROM invoices WHERE tenant_id=:t", t=t)), 1)

    async def test_unverifiable_webhook_asks_for_a_retry_and_leaves_no_trace(self):
        async with client_for(self.app) as c:
            txn = (await self.subscribe(c))["transaction"]
            pid = self.fake.pay(txn["transaction_id"], txn["amount_halalas"])
            self.fake.fetch_error = ProviderError("down", retryable=True, code="network")
            self.assertEqual((await self.hook(c, "evt-retry", pid)).status_code, 503)
            self.fake.fetch_error = None
            self.assertEqual((await self.hook(c, "evt-retry", pid)).json()["outcome"], "applied")             # the retry works: first try left no event row
            unknown = await self.hook(c, "evt-unknown", self.fake.pay(uuid.uuid4(), 100))
            self.assertEqual(unknown.json()["outcome"], "ignored")

    async def test_late_failure_event_cannot_undo_a_settled_payment(self):
        async with client_for(self.app) as c:
            txn = (await self.subscribe(c))["transaction"]
            ok = self.fake.pay(txn["transaction_id"], txn["amount_halalas"])
            await self.hook(c, "evt-a", ok)
            self.fake.payments[ok] = PaymentInfo(ok, "failed", txn["amount_halalas"], "SAR", transaction_id=txn["transaction_id"])
            await self.hook(c, "evt-b", ok, etype="payment_failed")
            self.assertEqual((await c.get(f"{BASE}/subscription")).json()["status"], "active")

    async def test_failed_hosted_payment_can_be_retried_with_a_fresh_page(self):
        async with client_for(self.app) as c:
            txn = (await self.subscribe(c))["transaction"]
            pid = self.fake.pay(txn["transaction_id"], txn["amount_halalas"], status="failed")
            self.assertEqual((await self.hook(c, "evt-f", pid, etype="payment_failed")).json()["outcome"], "applied")
            s = (await c.get(f"{BASE}/subscription")).json()
            self.assertEqual((s["status"], s["last_failed"]["failure_reason"]), ("trial", "declined"))
            second = await self.subscribe(c)
            self.assertNotEqual(second["transaction"]["transaction_id"], txn["transaction_id"])

    async def test_return_page_sync_applies_a_payment_whose_webhook_never_arrived(self):
        async with client_for(self.app) as c:
            data = await self.subscribe(c)
            txn = data["transaction"]
            pid = self.fake.pay(txn["transaction_id"], txn["amount_halalas"])
            self.fake.invoice_payments["inv_1"] = [self.fake.payments[pid]]
            r = await c.get(f"{BASE}/transactions/{txn['transaction_id']}")
            self.assertEqual(r.json()["status"], "paid")
            self.assertEqual((await c.get(f"{BASE}/subscription")).json()["status"], "active")

    async def test_checkout_failures_are_reported_not_hidden(self):
        self.fake.checkout_error = ProviderError("provider down", retryable=True, code="network")
        async with client_for(self.app) as c:
            r = await c.post(f"{BASE}/checkout", json={"plan_code": "starter", "interval": "yearly"})
            self.assertEqual((r.status_code, r.json()["detail"]["code"]), (502, "PROVIDER_ERROR"))
            self.assertEqual((await c.post(f"{BASE}/checkout", json={"plan_code": "nope", "interval": "monthly"})).status_code, 404)
            self.assertEqual((await c.post(f"{BASE}/checkout", json={"plan_code": "starter", "interval": "weekly"})).status_code, 422)
        with patch.object(get_settings(), "moyasar_secret_key", ""):
            async with client_for(self.app) as c:
                r = await c.post(f"{BASE}/checkout", json={"plan_code": "starter", "interval": "monthly"})
                self.assertEqual((r.status_code, r.json()["detail"]["code"]), (503, "PAYMENTS_UNAVAILABLE"))

    async def test_placeholder_prices_cannot_be_charged_in_production(self):
        async with self.Admin() as s:
            await s.execute(self.text("UPDATE plans SET is_placeholder = true WHERE code = 'starter'"))
            await s.commit()
        try:
            with patch.object(get_settings(), "app_env", "production"):
                async with client_for(self.app) as c:
                    r = await c.post(f"{BASE}/checkout", json={"plan_code": "starter", "interval": "monthly"})
                    real = await c.post(f"{BASE}/checkout", json={"plan_code": "pro", "interval": "monthly"})   # real price: sellable
        finally:
            async with self.Admin() as s:
                await s.execute(self.text("UPDATE plans SET is_placeholder = false WHERE code = 'starter'"))
                await s.commit()
        self.assertEqual((r.status_code, r.json()["detail"]["code"]), (409, "PLACEHOLDER_PRICING"))
        self.assertEqual(real.status_code, 200, real.text)
        self.assertEqual(len(self.fake.checkouts), 1)

    async def test_enterprise_is_contact_us_never_purchasable_online(self):
        async with client_for(self.app) as c:
            r = await c.post(f"{BASE}/checkout", json={"plan_code": "enterprise", "interval": "monthly"})
        self.assertEqual((r.status_code, r.json()["detail"]["code"]), (409, "CONTACT_SALES"))
        self.assertEqual(self.fake.checkouts, [])

    async def test_legacy_placeholder_plans_are_not_sellable(self):
        async with client_for(self.app) as c:
            for code in ("economic", "professional"):
                r = await c.post(f"{BASE}/checkout", json={"plan_code": code, "interval": "monthly"})
                self.assertEqual((r.status_code, r.json()["detail"]["code"]), (404, "PLAN_NOT_FOUND"), code)

    # ── plan changes ────────────────────────────────────────────────────────────────
    async def test_upgrade_is_prorated_and_immediate_downgrade_waits_for_renewal(self):
        from src.shared.services.billing import service
        t = self.tenants[0]
        async with client_for(self.app) as c:
            txn = (await self.subscribe(c, "pro"))["transaction"]
            await self.hook(c, "evt-1", self.fake.pay(txn["transaction_id"], txn["amount_halalas"]))
            async with await self.sysdb() as db:
                s = await service.ensure_subscription(db, t)
                start, end = s.current_period_start, s.current_period_end
            up = (await self.subscribe(c, "business"))["transaction"]
            self.assertEqual(up["kind"], "upgrade")
            full_diff = 179900 - 79900
            self.assertTrue(0 < up["subtotal_halalas"] <= full_diff, up)                                     # only the remaining fraction
            self.assertEqual(up["amount_halalas"], pricing.with_vat(up["subtotal_halalas"], 1500).total)
            await self.hook(c, "evt-up", self.fake.pay(up["transaction_id"], up["amount_halalas"], token=False))
            s = (await c.get(f"{BASE}/subscription")).json()
            self.assertEqual(s["plan"]["code"], "business")
            self.assertEqual((s["current_period_start"], s["current_period_end"]), (start.isoformat(), end.isoformat()))   # period unchanged
            down = await c.post(f"{BASE}/checkout", json={"plan_code": "starter", "interval": "monthly"})
            self.assertEqual((down.json()["action"], down.json()["effective_at"]), ("scheduled", end.isoformat()))
            s = (await c.get(f"{BASE}/subscription")).json()
            self.assertEqual((s["plan"]["code"], s["pending_plan_code"]), ("business", "starter"))
        # at renewal the scheduled plan (and its price) applies
        self.fake.charge_result = "paid"
        async with await self.sysdb() as db:
            await service.run_sweep(db, service.default_urls(), now=end + timedelta(minutes=5))
            s = await service.ensure_subscription(db, t)
        self.assertEqual((s.plan_code, s.pending_plan_code), ("starter", None))
        self.assertEqual(self.fake.charges[-1]["amount_halalas"], pricing.with_vat(29900, 1500).total)

    async def test_early_renewal_of_the_same_plan_extends_from_the_current_end(self):
        from src.shared.services.billing import service
        t = self.tenants[0]
        async with client_for(self.app) as c:
            txn = (await self.subscribe(c, "starter"))["transaction"]
            await self.hook(c, "evt-1", self.fake.pay(txn["transaction_id"], txn["amount_halalas"]))
            async with await self.sysdb() as db:
                end = (await service.ensure_subscription(db, t)).current_period_end
            r = (await self.subscribe(c, "starter"))["transaction"]
            self.assertEqual(r["kind"], "renewal")
            await self.hook(c, "evt-2", self.fake.pay(r["transaction_id"], r["amount_halalas"]))
            async with await self.sysdb() as db:
                self.assertEqual((await service.ensure_subscription(db, t)).current_period_end, pricing.add_interval(end, "monthly"))

    # ── cancel / resume / trial expiry / exempt ────────────────────────────────────
    async def test_cancel_at_period_end_blocks_only_after_the_paid_time_and_can_be_undone(self):
        from src.shared.services.billing import service
        t = self.tenants[0]
        async with client_for(self.app) as c:
            self.assertEqual((await c.post(f"{BASE}/cancel")).status_code, 409)                              # nothing to cancel on a trial
            txn = (await self.subscribe(c))["transaction"]
            await self.hook(c, "evt-1", self.fake.pay(txn["transaction_id"], txn["amount_halalas"]))
            self.assertTrue((await c.post(f"{BASE}/cancel")).json()["cancel_at_period_end"])
            self.assertEqual((await c.get(f"{BASE}/status")).json()["mode"], "full")                         # paid time is still usable
            self.assertEqual((await c.post(f"{BASE}/resume")).json()["cancel_at_period_end"], False)
            self.assertEqual((await c.post(f"{BASE}/resume")).status_code, 409)
            await c.post(f"{BASE}/cancel")
        async with await self.sysdb() as db:
            end = (await service.ensure_subscription(db, t)).current_period_end
            out = await service.run_sweep(db, service.default_urls(), now=end + timedelta(minutes=1))
        self.assertEqual(out["cancelled"], 1)
        self.assertEqual(self.fake.charges, [])                                                              # a cancelled plan is never charged
        async with client_for(self.app) as c:
            self.assertEqual((await c.get(f"{BASE}/subscription")).json()["status"], "cancelled")

    async def test_trial_expiry_sweep_suspends_but_a_grandfathered_tenant_never_expires(self):
        from src.shared.services.billing import service
        t0, t1 = self.tenants
        async with await self.sysdb() as db:
            a = await service.ensure_subscription(db, t0)
            a.trial_ends_at = datetime.now(UTC) - timedelta(days=1)
            self.assertEqual(service.access_of(a).reason, "trial_expired")          # blocked by the clock even before the sweep runs
        async with self.Admin() as s:                                                                        # t1 = pre-billing tenant (migration flags it exempt)
            await s.execute(self.text("INSERT INTO subscriptions (tenant_id, plan_code, status, is_exempt, trial_ends_at) VALUES (:t,'economic','trial',true,now() - interval '90 days')"), {"t": t1})
            await s.commit()
        async with await self.sysdb() as db:
            out = await service.run_sweep(db, service.default_urls())
        self.assertEqual(out["trial_expired"], 1)
        self.assertEqual((await self.row("SELECT status FROM tenants WHERE tenant_id=:t", t=t0))[0]["status"], "suspended")
        self.assertEqual((await self.row("SELECT status FROM tenants WHERE tenant_id=:t", t=t1))[0]["status"], "trial")
        async with client_for(self.app) as c:
            st0 = (await c.get(f"{BASE}/status")).json()
            self.assertEqual((st0["mode"], st0["status"]), ("blocked", "suspended"))
        async with client_for(self.app_as(TenantUserRole.ADMIN, t1)) as c:
            st = (await c.get(f"{BASE}/status")).json()
            self.assertEqual((st["mode"], st["is_exempt"]), ("full", True))
            self.assertEqual((await c.post(f"{BASE}/checkout", json={"plan_code": "starter", "interval": "monthly"})).json()["detail"]["code"], "EXEMPT")

    # ── roles + isolation ────────────────────────────────────────────────────────────
    async def test_roles_only_admins_pay_and_agents_see_nothing_financial(self):
        for role, expected in ((TenantUserRole.AGENT, 403), (TenantUserRole.AUDITOR, 403)):
            async with client_for(self.app_as(role)) as c:
                self.assertEqual((await c.post(f"{BASE}/checkout", json={"plan_code": "starter", "interval": "monthly"})).status_code, expected, role)
                self.assertEqual((await c.post(f"{BASE}/cancel")).status_code, 403)
                self.assertEqual((await c.get(f"{BASE}/status")).status_code, 200)                           # banner is for everyone
        async with client_for(self.app_as(TenantUserRole.AGENT)) as c:
            for path in ("plans", "subscription", "transactions", "invoices"):
                self.assertEqual((await c.get(f"{BASE}/{path}")).status_code, 403, path)
        async with client_for(self.app_as(TenantUserRole.AUDITOR)) as c:
            for path in ("plans", "subscription", "transactions", "invoices"):
                self.assertEqual((await c.get(f"{BASE}/{path}")).status_code, 200, path)

    async def test_tenants_never_see_each_others_money(self):
        async with client_for(self.app) as c:
            txn = (await self.subscribe(c))["transaction"]
            await self.hook(c, "evt-1", self.fake.pay(txn["transaction_id"], txn["amount_halalas"]))
            inv = (await c.get(f"{BASE}/invoices")).json()["items"][0]
        async with client_for(self.app_as(TenantUserRole.ADMIN, self.tenants[1])) as c:
            self.assertEqual((await c.get(f"{BASE}/invoices")).json()["items"], [])
            self.assertEqual((await c.get(f"{BASE}/transactions")).json()["items"], [])
            self.assertEqual((await c.get(f"{BASE}/transactions/{txn['transaction_id']}")).status_code, 404)
            self.assertEqual((await c.get(f"{BASE}/invoices/{inv['invoice_id']}/pdf")).status_code, 404)
            self.assertEqual((await c.get(f"{BASE}/subscription")).json()["status"], "trial")

    # ── manual activation ────────────────────────────────────────────────────────────
    async def test_manual_activation_is_key_guarded_audited_and_invoiced(self):
        t = self.tenants[0]
        body = {"plan_code": "pro", "interval": "monthly", "periods": 3, "reference": "TRF-1001", "note": "bank transfer"}
        async with client_for(self.app) as c:
            self.assertEqual((await c.post(f"{BASE}/admin/tenants/{t}/activate", json=body, headers={"X-Billing-Key": "k"})).status_code, 404)    # off by default
        with patch.object(get_settings(), "billing_admin_api_key", "billing-key-123"):                                                    # pragma: allowlist secret
            async with client_for(self.app) as c:
                url = f"{BASE}/admin/tenants/{t}/activate"
                self.assertEqual((await c.post(url, json=body)).status_code, 403)
                self.assertEqual((await c.post(url, json=body, headers={"X-Billing-Key": "nope"})).status_code, 403)
                r = await c.post(url, json=body, headers={"X-Billing-Key": "billing-key-123"})
                self.assertEqual(r.status_code, 201, r.text)
                self.assertEqual((r.json()["status"], r.json()["provider"], r.json()["subtotal_halalas"]), ("paid", "manual", 79900 * 3))
                self.assertEqual((await c.post(f"{BASE}/admin/tenants/{uuid.uuid4()}/activate", json={**body, "plan_code": "x"},
                                               headers={"X-Billing-Key": "billing-key-123"})).status_code, 404)
        async with client_for(self.app) as c:
            s = (await c.get(f"{BASE}/subscription")).json()
            self.assertEqual((s["status"], s["plan"]["code"], s["auto_renew"]), ("active", "pro", False))
            start, end = datetime.fromisoformat(s["current_period_start"]), datetime.fromisoformat(s["current_period_end"])
            self.assertEqual(end, pricing.add_interval(pricing.add_interval(pricing.add_interval(start, "monthly"), "monthly"), "monthly"))
            self.assertEqual(len((await c.get(f"{BASE}/invoices")).json()["items"]), 1)
        audit = await self.row("SELECT details FROM audit_logs WHERE tenant_id=:t AND action='billing.manual_activation'", t=t)
        self.assertEqual((len(audit), audit[0]["details"]["reference"], audit[0]["details"]["periods"]), (1, "TRF-1001", 3))

    async def test_manual_activation_with_a_received_amount_splits_the_vat_back_out(self):
        from src.shared.services.billing import service
        async with await self.sysdb() as db:
            txn = await service.manual_activate(db, self.tenants[0], "starter", "monthly", amount_halalas=34385, reference="R")
        self.assertEqual((txn.subtotal_halalas, txn.vat_halalas, txn.amount_halalas), (29900, 4485, 34385))

    # ── usage ────────────────────────────────────────────────────────────────────────
    async def test_usage_alerts_fire_once_at_80_and_100_percent(self):
        from src.shared.services.billing import usage
        t = self.tenants[0]
        key = f"usage:{t}:{usage.period_of()}"
        async with client_for(self.app) as c:
            await c.get(f"{BASE}/status")                                                                    # creates the subscription (economic: 1000/month)

        async def flush(n):
            with patch.object(usage.redis_mgr, "scan_counters", AsyncMock(return_value={key: n})):
                async with await self.sysdb() as db:
                    return await usage.flush(db, {t: 1000})
        self.assertEqual(await flush(500), [])
        a = await flush(800)
        self.assertEqual([(x["level"], x["used"]) for x in a], [(80, 800)])
        self.assertEqual(await flush(850), [])                                                               # not again
        a = await flush(1000)
        self.assertEqual([x["level"] for x in a], [100])
        self.assertEqual(await flush(1200), [])
        self.assertEqual(await flush(900), [])                                                               # a smaller Redis value never lowers the stored count
        self.assertEqual((await self.row("SELECT ai_messages FROM usage_counters WHERE tenant_id=:t", t=t))[0]["ai_messages"], 1200)
        self.assertEqual((usage.level(799, 1000), usage.level(800, 1000), usage.level(1000, 1000), usage.level(5, None)), ("ok", "warning", "limit_reached", "unlimited"))

    async def test_kill_switch_reopens_everything(self):
        from src.shared.services.billing import entitlements, service
        t = self.tenants[0]
        async with await self.sysdb() as db:
            s = await service.ensure_subscription(db, t)
            s.status = "suspended"
        self.assertFalse((await entitlements.bot_allowed(t))[0])
        with patch.object(get_settings(), "billing_gate_enabled", False):
            self.assertTrue((await entitlements.bot_allowed(t))[0])
            async with client_for(self.app) as c:
                self.assertEqual((await c.delete(f"/api/v1/customers/{uuid.uuid4()}")).status_code, 404)


if __name__ == "__main__":
    unittest.main()

"""Customers: identity (phone vs channel ids), manual CRUD, merge, PDPL consent, bulk import.

Pure tests run everywhere (CI). The DB class needs the same opt-in env as the property import tests
(OMNIFLOW_TEST_DATABASE_URL superuser + OMNIFLOW_TEST_RLS_URL non-superuser: RLS is the tenant guard).
"""
import os
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from src.gateway.main import create_app
from src.shared.core.config import get_settings
from src.shared.core.enums import TenantUserRole
from src.shared.db.models import Customer
from src.shared.services import customer_import as cimp
from src.shared.services import customers as csvc
from src.shared.services import property_import as base
from src.shared.services.bulk_import.customer_schema import CUSTOMER_SCHEMA, parse_bool, parse_tags
from src.shared.services.bulk_import.phone import normalize_phone
from src.shared.services.bulk_import.text import ParseError
from tests.test_property_import_flow import FakeStorage, auth_overrides, client_for

DB_URL = os.environ.get("OMNIFLOW_TEST_DATABASE_URL")
RLS_URL = os.environ.get("OMNIFLOW_TEST_RLS_URL")
BASE = "/api/v1/customers"
IMP = f"{BASE}/import"


class PhoneAndSchemaTests(unittest.TestCase):
    def test_saudi_numbers_normalise_to_e164(self):
        for raw in ("0501234567", "501234567", "966501234567", "00966 50 123 4567", "+966-50-123-4567", "٠٥٠١٢٣٤٥٦٧",
                    "966501234567.0", " +966 (50) 123 4567 ", "+9660501234567"):
            self.assertEqual(normalize_phone(raw), "+966501234567", raw)
        self.assertEqual(normalize_phone("+971501234567"), "+971501234567")
        self.assertEqual(normalize_phone("+966 11 234 5678"), "+966112345678")

    def test_bad_numbers_are_rejected_with_an_arabic_message(self):
        for raw in ("", "abc", "12345", "055", "0401234567", "+1", "+966 5 12"):
            with self.assertRaises(ParseError, msg=raw):
                normalize_phone(raw)

    def test_cell_parsers(self):
        self.assertEqual(parse_tags("مستثمر، فيلا; vip|مستثمر"), ["مستثمر", "فيلا", "vip"])
        with self.assertRaises(ParseError):
            parse_tags(",".join(f"t{i}" for i in range(21)))
        self.assertEqual([parse_bool(x) for x in ("نعم", "Yes", "1", "لا", "no", "0")], [True, True, True, False, False, False])
        with self.assertRaises(ParseError):
            parse_bool("ربما")
        self.assertEqual(CUSTOMER_SCHEMA.by_name()["email"].parse("  A@B.sa "), "a@b.sa")
        with self.assertRaises(ParseError):
            CUSTOMER_SCHEMA.by_name()["email"].parse("not-an-email")

    def test_consent_rules(self):
        c = Customer(marketing_opt_in=False, opt_out_at=None)
        now = datetime.now(timezone.utc)
        self.assertTrue(csvc.apply_marketing_consent(c, True, "manual", now=now))
        self.assertEqual((c.marketing_opt_in, c.opt_in_source, c.opt_in_at), (True, "manual", now))
        self.assertFalse(csvc.apply_marketing_consent(c, True, "import:x", now=now))           # already opted in: no change
        self.assertTrue(csvc.apply_marketing_consent(c, False, "x", now=now))                  # withdrawal always applies
        self.assertEqual((c.marketing_opt_in, c.opt_out_at), (False, now))
        self.assertFalse(csvc.apply_marketing_consent(c, True, "import:x", now=now))           # opted out: never re-opted in
        self.assertFalse(c.marketing_opt_in)

    def test_contact_key_for_channel(self):
        c = Customer(unified_phone="+966501234567", channel_user_ids={"instagram": "1784", "messenger": "99"})
        self.assertEqual((c.contact_key_for("whatsapp"), c.contact_key_for("instagram"), c.contact_key_for("messenger"),
                          c.contact_key_for("tiktok"), c.contact_key_for(None)),
                         ("+966501234567", "1784", "99", "+966501234567", "+966501234567"))

    def test_options_validation(self):
        o = cimp.normalize_options(None)
        self.assertEqual((o["on_duplicate"], o["consent_confirmed"], o["opt_in_all"], o["tags"]), ("skip", False, False, []))
        with self.assertRaises(ValueError):
            cimp.normalize_options({"on_duplicate": "create_new"})
        with self.assertRaises(ValueError):
            cimp.normalize_options({"defaults": {"status": "x"}})
        with self.assertRaises(ValueError):
            cimp.normalize_options({"tags": [f"t{i}" for i in range(25)]})


@unittest.skipUnless(DB_URL and RLS_URL, "needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL (non-superuser: RLS is the tenant guard)")
class CustomersDbTests(unittest.IsolatedAsyncioTestCase):
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
        self.storage = FakeStorage()
        for p in (patch.object(base, "storage", self.storage), patch("src.shared.db.session.AsyncSessionFactory", self.App),
                  patch.object(get_settings(), "import_batch_size", 500)):
            p.start()
            self.addCleanup(p.stop)
        self.app = create_app()
        auth_overrides(self.app, self.tenants[0], self.App)

    async def asyncTearDown(self):
        async with self.Admin() as s:
            for t in self.tenants:
                for stmt in ("DELETE FROM audit_logs WHERE tenant_id=:t", "DELETE FROM import_jobs WHERE tenant_id=:t",
                             "DELETE FROM import_mapping_templates WHERE tenant_id=:t", "DELETE FROM conversations WHERE tenant_id=:t",
                             "DELETE FROM customers WHERE tenant_id=:t", "DELETE FROM tenants WHERE tenant_id=:t"):
                    await s.execute(self.text(stmt), {"t": t})
            await s.commit()
        await self.admin.dispose()
        await self.app_engine.dispose()

    # helpers ---------------------------------------------------------------------------------
    def app_as(self, role, tenant=None):
        app = create_app()
        auth_overrides(app, tenant or self.tenants[0], self.App, role=role)
        return app

    async def rows(self, tenant=None, where="true"):
        async with self.Admin() as s:
            r = await s.execute(self.text(f"SELECT * FROM customers WHERE tenant_id=:t AND {where} ORDER BY created_at"),
                                {"t": tenant or self.tenants[0]})
            return [dict(x._mapping) for x in r]

    async def audit(self, tenant=None):
        async with self.Admin() as s:
            r = await s.execute(self.text("SELECT action, entity_id, details, actor_user_id FROM audit_logs WHERE tenant_id=:t ORDER BY created_at, audit_id"),
                                {"t": tenant or self.tenants[0]})
            return [dict(x._mapping) for x in r]

    async def seed(self, phone, tenant=None, **kw):
        cols = {"customer_id": uuid.uuid4(), "tenant_id": tenant or self.tenants[0], "unified_phone": phone, "source": "whatsapp",
                "channel_user_ids": "{}", "tags": "[]", "vcard_state": "STATE_NEW", "marketing_opt_in": False, "is_vip": False,
                "is_processing_restricted": False}
        cols.update(kw)
        names = ", ".join(cols)
        marks = ", ".join(f"CAST(:{k} AS jsonb)" if k in ("channel_user_ids", "tags") else f":{k}" for k in cols)
        async with self.Admin() as s:
            await s.execute(self.text(f"INSERT INTO customers ({names}, created_at, updated_at) VALUES ({marks}, now(), now())"), cols)
            await s.commit()
        return cols["customer_id"]

    # manual CRUD -------------------------------------------------------------------------------
    async def test_manual_create_normalises_phone_defaults_consent_off_and_audits(self):
        async with client_for(self.app) as c:
            r = await c.post(f"{BASE}", json={"phone": "0501234567", "display_name": "محمد", "email": "m@x.sa", "city": "الرياض",
                                              "tags": ["مستثمر", "مستثمر", " vip "], "notes": "ملاحظة"})
            self.assertEqual(r.status_code, 201, r.text)
            body = r.json()
            self.assertEqual((body["phone_e164"], body["unified_phone"], body["source"], body["marketing_opt_in"], body["tags"]),
                             ("+966501234567", "+966501234567", "manual", False, ["مستثمر", "vip"]))
            self.assertIsNone(body["opt_in_at"])
            dup = await c.post(f"{BASE}", json={"phone": "+966 50 123 4567"})
            self.assertEqual((dup.status_code, dup.json()["detail"]["code"], dup.json()["detail"]["existing_customer_id"]),
                             (409, "DUPLICATE_PHONE", body["customer_id"]))
            bad = await c.post(f"{BASE}", json={"phone": "123"})
            self.assertEqual((bad.status_code, bad.json()["detail"]["code"]), (422, "INVALID_PHONE"))
            self.assertEqual((await c.post(f"{BASE}", json={"phone": "0555555555", "email": "nope"})).status_code, 422)
            ok = await c.post(f"{BASE}", json={"phone": "0555555555", "marketing_opt_in": True})
            self.assertEqual((ok.json()["marketing_opt_in"], ok.json()["opt_in_source"]), (True, "manual"))
            self.assertIsNotNone(ok.json()["opt_in_at"])
        actions = [a["action"] for a in await self.audit()]
        self.assertEqual(actions, ["customer.created", "customer.created", "customer.opt_in_granted"])

    async def test_roles_agent_writes_auditor_reads_only_delete_is_admin_only(self):
        cid = await self.seed("+966500000001")
        async with client_for(self.app_as(TenantUserRole.AUDITOR)) as c:
            self.assertEqual((await c.post(f"{BASE}", json={"phone": "0501111111"})).status_code, 403)
            self.assertEqual((await c.patch(f"{BASE}/{cid}", json={"city": "x"})).status_code, 403)
            self.assertEqual((await c.get(f"{BASE}/{cid}")).status_code, 200)
        async with client_for(self.app_as(TenantUserRole.AGENT)) as c:
            self.assertEqual((await c.patch(f"{BASE}/{cid}", json={"city": "جدة"})).status_code, 200)
            self.assertEqual((await c.delete(f"{BASE}/{cid}")).status_code, 403)
        async with client_for(self.app) as c:                                  # admin
            self.assertEqual((await c.delete(f"{BASE}/{cid}")).status_code, 204)
            self.assertEqual((await c.get(f"{BASE}/{cid}")).status_code, 404)
        self.assertIn("customer.deleted", [a["action"] for a in await self.audit()])

    async def test_list_filters_search_archive_and_tenant_isolation(self):
        a = await self.seed("+966500000001", display_name="أحمد", city="الرياض", tags='["vip","مستثمر"]', source="manual", email="ahmad@x.sa",
                            phone_e164="+966500000001")
        b = await self.seed("1784000000001", display_name="سارة", source="instagram", channel_user_ids='{"instagram":"1784000000001"}')
        c_ = await self.seed("+966500000003", display_name="خالد", marketing_opt_in=True, city="جدة", phone_e164="+966500000003")
        await self.seed("+966500000009", tenant=self.tenants[1], display_name="غريب")
        async with client_for(self.app) as c:
            def ids(r):
                return {i["customer_id"] for i in r.json()["items"]}
            self.assertEqual(ids(await c.get(f"{BASE}")), {str(a), str(b), str(c_)})
            self.assertEqual(ids(await c.get(f"{BASE}", params={"source": "instagram"})), {str(b)})
            self.assertEqual(ids(await c.get(f"{BASE}", params={"tag": "مستثمر"})), {str(a)})
            self.assertEqual(ids(await c.get(f"{BASE}", params={"opt_in": "true"})), {str(c_)})
            self.assertEqual(ids(await c.get(f"{BASE}", params={"city": "جد"})), {str(c_)})
            self.assertEqual(ids(await c.get(f"{BASE}", params={"search": "ahmad@"})), {str(a)})
            self.assertEqual(ids(await c.get(f"{BASE}", params={"search": "%"})), set())            # wildcard-safe
            self.assertEqual(ids(await c.get(f"{BASE}", params={"search": "غريب"})), set())         # other tenant
            r = await c.patch(f"{BASE}/{b}", json={"archived": True})
            self.assertEqual(r.status_code, 200)
            self.assertIsNotNone(r.json()["archived_at"])
            self.assertNotIn(str(b), ids(await c.get(f"{BASE}")))
            self.assertEqual(ids(await c.get(f"{BASE}", params={"archived": "true"})), {str(b)})
            r = await c.patch(f"{BASE}/{b}", json={"archived": False})
            self.assertIsNone(r.json()["archived_at"])

    async def test_phone_attach_conflict_and_consent_rules_via_patch(self):
        ig = await self.seed("1784000000001", source="instagram", channel_user_ids='{"instagram":"1784000000001"}')
        wa = await self.seed("+966500000001", phone_e164="+966500000001")
        async with client_for(self.app) as c:
            r = await c.patch(f"{BASE}/{ig}", json={"phone": "0500000001"})
            self.assertEqual((r.status_code, r.json()["detail"]["existing_customer_id"]), (409, str(wa)))
            r = await c.patch(f"{BASE}/{ig}", json={"phone": "0599999999"})
            self.assertEqual((r.status_code, r.json()["phone_e164"], r.json()["unified_phone"]), (200, "+966599999999", "1784000000001"))
            r = await c.patch(f"{BASE}/{wa}", json={"marketing_opt_in": True})
            self.assertTrue(r.json()["marketing_opt_in"])
            r = await c.patch(f"{BASE}/{wa}", json={"marketing_opt_in": False})
            self.assertEqual((r.json()["marketing_opt_in"], r.json()["opt_out_at"] is not None), (False, True))
            r = await c.patch(f"{BASE}/{wa}", json={"marketing_opt_in": True})
            self.assertEqual((r.status_code, r.json()["detail"]["code"]), (409, "OPTED_OUT"))
        self.assertEqual([a["action"] for a in await self.audit()], ["customer.opt_in_granted", "customer.opt_out"])

    # identity resolution (the inbound pipeline) ----------------------------------------------------
    async def test_inbound_identity_phone_vs_platform_id(self):
        from src.shared.db.repository import CustomerRepository

        manual = await self.seed("+966501234567", phone_e164="+966501234567", source="manual", display_name="يدوي")
        t = self.tenants[0]

        async def resolve(key, channel):
            async with self.App() as s, s.begin():
                await s.execute(self.text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(t)})
                cust, created = await CustomerRepository(s).get_or_create_by_phone(key, t, channel)
                return cust.customer_id, created

        self.assertEqual(await resolve("+966501234567", "whatsapp"), (manual, False))             # same person, found by phone
        ig_id, created = await resolve("1784000000777", "instagram")
        self.assertTrue(created)
        self.assertEqual(await resolve("1784000000777", "instagram"), (ig_id, False))
        wa_id, created = await resolve("+966507654321", "whatsapp")
        self.assertTrue(created)
        by_id = {r["customer_id"]: r for r in await self.rows()}
        self.assertEqual((by_id[ig_id]["source"], by_id[ig_id]["phone_e164"], by_id[ig_id]["channel_user_ids"]),
                         ("instagram", None, {"instagram": "1784000000777"}))
        self.assertEqual((by_id[wa_id]["source"], by_id[wa_id]["phone_e164"], by_id[wa_id]["channel_user_ids"]),
                         ("whatsapp", "+966507654321", {}))
        self.assertFalse(by_id[ig_id]["marketing_opt_in"])                                        # inbound never implies consent

    async def test_merge_moves_conversations_merges_identity_and_opt_out_wins(self):
        wa = await self.seed("+966500000001", phone_e164="+966500000001", display_name="أحمد", tags='["a"]', marketing_opt_in=True,
                             opt_in_source="manual")
        ig = await self.seed("1784000000001", source="instagram", channel_user_ids='{"instagram":"1784000000001"}', tags='["b"]',
                             city="جدة", opt_out_at=datetime.now(timezone.utc))
        async with self.Admin() as s:
            await s.execute(self.text(
                "INSERT INTO conversations (conversation_id,tenant_id,customer_id,channel,status,message_count,ai_reply_epoch,created_at,updated_at) "
                "VALUES (:c,:t,:cu,'instagram','ai_active',0,0,now(),now())"), {"c": uuid.uuid4(), "t": self.tenants[0], "cu": ig})
            await s.commit()
        async with client_for(self.app) as c:
            self.assertEqual((await c.post(f"{BASE}/{wa}/merge", json={"source_customer_id": str(wa)})).status_code, 422)
            r = await c.post(f"{BASE}/{wa}/merge", json={"source_customer_id": str(ig)})
            self.assertEqual(r.status_code, 200, r.text)
            m = r.json()
            self.assertEqual((m["channel_user_ids"], m["tags"], m["city"], m["marketing_opt_in"], m["phone_e164"]),
                             ({"instagram": "1784000000001"}, ["a", "b"], "جدة", False, "+966500000001"))
            self.assertIsNotNone(m["opt_out_at"])                                                # an opt-out on either side wins
            detail = (await c.get(f"{BASE}/{wa}/detail")).json()
            self.assertEqual([x["channel"] for x in detail["conversations"]], ["instagram"])
            self.assertIn("customer.merged", [a["action"] for a in detail["activity"]])
            self.assertEqual((await c.get(f"{BASE}/{ig}")).status_code, 404)
        self.assertEqual(len(await self.rows()), 1)

    async def test_export_csv_respects_filters_and_defuses_formulas(self):
        await self.seed("+966500000001", phone_e164="+966500000001", display_name="=cmd|' /C calc'!A0", tags='["vip"]')
        await self.seed("+966500000002", phone_e164="+966500000002", display_name="عادي")
        async with client_for(self.app) as c:
            r = await c.get(f"{BASE}/export.csv", params={"tag": "vip"})
        text = r.content.decode("utf-8-sig")
        self.assertEqual(len(text.strip().splitlines()), 2, text)
        self.assertIn("'=cmd", text)
        self.assertNotIn("عادي", text)

    # bulk import ------------------------------------------------------------------------------------
    HEADER = "الاسم,الجوال,البريد,المدينة,الوسوم,موافقة\n"

    def csv(self, *rows):
        return (self.HEADER + "\n".join(rows) + "\n").encode("utf-8-sig")

    async def upload(self, c, data, name="customers.csv"):
        r = await c.post(f"{IMP}/upload", files={"file": (name, data)})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    @staticmethod
    def mapping_of(up):
        return {s["field"]: s["header"] for s in up["suggested_mapping"] if s["header"]}

    async def wait(self, c, import_id):
        import asyncio

        for _ in range(600):
            job = (await c.get(f"{IMP}/{import_id}")).json()
            if job["status"] in ("completed", "failed", "cancelled"):
                return job
            await asyncio.sleep(0.1)
        self.fail(job)

    async def test_import_normalises_dedupes_and_never_opts_in_without_the_declaration(self):
        old = await self.seed("+966500000001", phone_e164="+966500000001", display_name="قديم")
        data = self.csv("أحمد,0501234567,a@x.sa,الرياض,\"مستثمر، vip\",نعم", "سارة,٠٥٥٥٥٥٥٥٥٥,,جدة,,لا",
                        "قديم جديد,966500000001,old@x.sa,,,", "خطأ,12345,,,,", "مكرر,+966501234567,,,,", "بدون موافقة,0560000000,,,,")
        async with client_for(self.app) as c:
            up = await self.upload(c, data)
            self.assertEqual((up["total_rows"], self.mapping_of(up)["phone"], self.mapping_of(up)["marketing_opt_in"]), (6, "الجوال", "موافقة"))
            v = (await c.post(f"{IMP}/{up['import_id']}/validate", json={"mapping": self.mapping_of(up), "options": {"on_duplicate": "update"}})).json()
            self.assertEqual((v["counts"]["total"], v["counts"]["valid"], v["counts"]["errors"], v["counts"]["duplicate_in_file"],
                              v["counts"]["will_create"], v["counts"]["will_update"], v["counts"]["will_opt_in"]), (6, 4, 2, 1, 3, 1, 0))
            self.assertTrue(v["consent"]["blocked"])                                    # a consent column without the declaration
            self.assertIn("إقرار الموافقة على التواصل (PDPL)", v["required_missing"])
            blocked = await c.post(f"{IMP}/{up['import_id']}/commit")
            self.assertEqual(blocked.status_code, 422)                                  # cannot commit until the declaration
            self.assertEqual(await self.rows(where="source='import'"), [])

            v = (await c.post(f"{IMP}/{up['import_id']}/validate", json={"mapping": self.mapping_of(up), "options": {"on_duplicate": "update"}})).json()
            # Declaration NOT ticked but consent column dropped from the mapping -> import without any consent.
            m = {k: h for k, h in self.mapping_of(up).items() if k != "marketing_opt_in"}
            v = (await c.post(f"{IMP}/{up['import_id']}/validate", json={"mapping": m, "options": {"on_duplicate": "update"}})).json()
            self.assertFalse(v["consent"]["blocked"])
            job = None
            r = await c.post(f"{IMP}/{up['import_id']}/commit")
            self.assertEqual(r.status_code, 202, r.text)
            job = await self.wait(c, up["import_id"])
        self.assertEqual((job["status"], job["created"], job["updated"], job["failed"]), ("completed", 3, 1, 2))
        rows = {r["phone_e164"]: r for r in await self.rows()}
        self.assertEqual(set(rows), {"+966500000001", "+966501234567", "+966555555555", "+966560000000"})
        self.assertFalse(any(r["marketing_opt_in"] for r in rows.values()))             # no declaration -> nobody opted in
        self.assertEqual((rows["+966501234567"]["source"], rows["+966501234567"]["tags"], rows["+966501234567"]["email"]),
                         ("import", ["مستثمر", "vip"], "a@x.sa"))
        self.assertEqual((rows["+966500000001"]["customer_id"], rows["+966500000001"]["email"], rows["+966500000001"]["display_name"]),
                         (old, "old@x.sa", "قديم جديد"))                                # merged by phone, not duplicated
        self.assertEqual(len(rows), 4)

    async def test_import_with_declaration_opts_in_only_the_rows_that_said_yes_and_never_reopts_opted_out(self):
        gone = await self.seed("+966500000001", phone_e164="+966500000001", opt_out_at=datetime.now(timezone.utc))
        data = self.csv("أ,0501234567,,,,نعم", "ب,0555555555,,,,لا", "ج,0560000000,,,,", "د,0500000001,,,,نعم")
        async with client_for(self.app) as c:
            up = await self.upload(c, data)
            opts = {"on_duplicate": "update", "consent_confirmed": True, "tags": ["حملة-أكتوبر"]}
            v = (await c.post(f"{IMP}/{up['import_id']}/validate", json={"mapping": self.mapping_of(up), "options": opts})).json()
            self.assertEqual((v["counts"]["will_opt_in"], v["counts"]["opted_out_ignored"], v["consent"]["blocked"]), (1, 1, False))
            self.assertEqual((await c.post(f"{IMP}/{up['import_id']}/commit")).status_code, 202)
            job = await self.wait(c, up["import_id"])
        self.assertEqual((job["status"], job["created"], job["updated"]), ("completed", 3, 1))
        rows = {r["phone_e164"]: r for r in await self.rows()}
        self.assertTrue(rows["+966501234567"]["marketing_opt_in"])
        self.assertEqual(rows["+966501234567"]["opt_in_source"], "import:customers.csv")
        self.assertIsNotNone(rows["+966501234567"]["opt_in_at"])
        self.assertFalse(rows["+966555555555"]["marketing_opt_in"])                      # explicit "no"
        self.assertFalse(rows["+966560000000"]["marketing_opt_in"])                      # blank -> no consent
        self.assertEqual((rows["+966500000001"]["customer_id"], rows["+966500000001"]["marketing_opt_in"]), (gone, False))   # stays opted out
        self.assertEqual(rows["+966501234567"]["tags"], ["حملة-أكتوبر"])
        audit = {a["action"]: a for a in await self.audit()}
        self.assertEqual(audit["customer_import.consent_confirmed"]["details"]["expected_opt_ins"], 1)
        self.assertEqual((audit["customer_import.completed"]["details"]["opted_in"], audit["customer_import.completed"]["details"]["consent_confirmed"]), (1, True))
        self.assertIsNotNone(audit["customer_import.completed"]["actor_user_id"])

    async def test_import_is_tenant_isolated_and_cleans_up_source(self):
        async with client_for(self.app) as c:
            up = await self.upload(c, self.csv("أ,0501234567,,,,"))
            m = {k: h for k, h in self.mapping_of(up).items() if k != "marketing_opt_in"}
            await c.post(f"{IMP}/{up['import_id']}/validate", json={"mapping": m})
            await c.post(f"{IMP}/{up['import_id']}/commit")
            await self.wait(c, up["import_id"])
        self.assertEqual(await self.rows(self.tenants[1]), [])
        async with client_for(self.app_as(TenantUserRole.ADMIN, self.tenants[1])) as c2:
            self.assertEqual((await c2.get(f"{IMP}/{up['import_id']}")).status_code, 404)
            self.assertEqual((await c2.post(f"{IMP}/{up['import_id']}/cancel")).status_code, 404)
        self.assertFalse([k for k in self.storage.objects if k.endswith("/source") or k.endswith("/rows.json")])

    async def test_import_template_and_property_endpoints_do_not_see_customer_jobs(self):
        async with client_for(self.app) as c:
            t = await c.get(f"{IMP}/template")
            self.assertEqual(t.status_code, 200)
            self.assertTrue(t.content.startswith(b"PK"))
            up = await self.upload(c, self.csv("أ,0501234567,,,,"))
            self.assertEqual((await c.get(f"/api/v1/properties/import/{up['import_id']}")).status_code, 404)


if __name__ == "__main__":
    unittest.main()

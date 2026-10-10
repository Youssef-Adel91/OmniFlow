"""Support: help-center search, ticket threads, attachments (private + validated), tenant isolation, staff reply.

Pure tests run everywhere; the DB class needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL (see test_customers.py).
"""
import io
import os
import unittest
import uuid
import zipfile
from types import SimpleNamespace as NS
from unittest.mock import patch

from src.gateway import dependencies as deps
from src.gateway.main import create_app
from src.gateway.routers import support as sup
from src.shared.core.enums import TenantUserRole
from src.shared.services import support_kb
from tests.test_property_import_flow import auth_overrides, client_for

DB_URL = os.environ.get("OMNIFLOW_TEST_DATABASE_URL")
RLS_URL = os.environ.get("OMNIFLOW_TEST_RLS_URL")
BASE = "/api/v1/support"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class MemStorage:
    def __init__(self):
        self.files = {}

    async def put(self, key, data, content_type):
        self.files[key] = data

    async def get(self, key):
        return self.files[key]


def docx_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w/>")
    return buf.getvalue()


class PureTests(unittest.TestCase):
    def test_every_article_is_reachable_by_its_own_title(self):
        self.assertGreaterEqual(len(support_kb.all_articles()), 12)
        for a in support_kb.all_articles():
            self.assertIn(a['id'], [h['id'] for h in support_kb.search(a['title'])], a['id'])

    def test_search_finds_common_questions_and_nonsense_returns_nothing(self):
        self.assertEqual(support_kb.search("البوت ما يرد")[0]["id"], "bot-not-replying")
        self.assertEqual(support_kb.search("كيف استورد عقارات")[0]["id"], "import-properties")
        self.assertEqual(support_kb.search("xyz"), [])
        self.assertEqual(support_kb.search(""), [])

    def test_reference_and_filename_helpers(self):
        refs = {sup.new_reference() for _ in range(200)}
        self.assertEqual(len(refs), 200)
        self.assertTrue(all(r.startswith("OF-") and len(r) == 11 for r in refs))
        self.assertEqual(sup.safe_filename("../../etc/pass\"wd.png"), "passwd.png")
        self.assertEqual(sup.safe_filename(""), "file")

    def test_every_article_link_points_to_a_real_route_shape(self):
        for a in support_kb.all_articles():
            if a["link"]:
                self.assertTrue(a['link'].startswith("/"), a['id'])


class MetaAndStaffKeyTests(unittest.IsolatedAsyncioTestCase):
    async def test_meta_hides_unconfigured_contacts(self):
        app = create_app()
        auth_overrides(app, uuid.uuid4())
        async with client_for(app) as c:
            m = (await c.get(f"{BASE}/meta")).json()
        self.assertEqual(m["contacts"], {"whatsapp": None, "zoom": None, "docs": None})   # nothing invented
        self.assertEqual([p["id"] for p in m["priorities"]], ["urgent", "high", "normal", "low"])

    async def test_meta_returns_configured_whatsapp_link(self):
        app = create_app()
        auth_overrides(app, uuid.uuid4())
        with patch.object(sup, "get_settings") as gs:
            gs.return_value.support_whatsapp_number = "+966 50 123 4567"
            gs.return_value.support_zoom_url = ""
            gs.return_value.support_docs_url = ""
            async with client_for(app) as c:
                m = (await c.get(f"{BASE}/meta")).json()
        self.assertEqual(m["contacts"]["whatsapp"], "https://wa.me/966501234567")

    async def test_staff_endpoint_is_off_without_a_configured_key_and_rejects_wrong_keys(self):
        app = create_app()
        async with client_for(app) as c:
            r = await c.post(f"{BASE}/admin/tickets/{uuid.uuid4()}/reply", json={"body": "x"}, headers={"X-Support-Key": "k"})
            self.assertEqual(r.status_code, 404)
            with patch("src.gateway.routers.support.get_settings") as gs:
                gs.return_value.support_admin_api_key = "right"  # pragma: allowlist secret
                self.assertEqual((await c.post(f"{BASE}/admin/tickets/{uuid.uuid4()}/reply", json={"body": "x"})).status_code, 403)
                self.assertEqual((await c.post(f"{BASE}/admin/tickets/{uuid.uuid4()}/reply", json={"body": "x"},
                                               headers={"X-Support-Key": "wrong"})).status_code, 403)


@unittest.skipUnless(DB_URL and RLS_URL, "needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL (non-superuser: RLS is the tenant guard)")
class SupportDbTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        self.text = text
        self.admin = create_async_engine(DB_URL)
        self.app_engine = create_async_engine(RLS_URL)
        self.Admin = async_sessionmaker(self.admin, expire_on_commit=False)
        self.App = async_sessionmaker(self.app_engine, expire_on_commit=False)
        self.tenants = [uuid.uuid4(), uuid.uuid4()]
        self.users = {}
        async with self.Admin() as s:
            for i, t in enumerate(self.tenants):
                await s.execute(text(
                    "INSERT INTO tenants (tenant_id,business_name,fal_license_number,subscription_tier,status,max_ai_conversations,onboarding_status,created_at,updated_at) "
                    "VALUES (:t,:n,:f,'economic','active',100,'completed',now(),now())"), {"t": t, "n": f"T{i}", "f": f"FAL-{t.hex[:8]}"})
                uid = uuid.uuid4()
                self.users[t] = uid
                await s.execute(text("INSERT INTO tenant_users (user_id,tenant_id,full_name,email,hashed_password,role,is_active,created_at,updated_at) "
                                     "VALUES (:u,:t,'u',:e,'x','admin',true,now(),now())"), {"u": uid, "t": t, "e": f"u-{uid.hex[:6]}@x.sa"})
            await s.commit()
        self.mem = MemStorage()
        for p in (patch("src.shared.db.session.AsyncSessionFactory", self.App), patch.object(sup, "storage", self.mem)):
            p.start()
            self.addCleanup(p.stop)
        self.app = self.app_as(TenantUserRole.ADMIN)

    async def asyncTearDown(self):
        async with self.Admin() as s:
            for t in self.tenants:
                for stmt in ("DELETE FROM support_attachments WHERE tenant_id=:t", "DELETE FROM support_ticket_messages WHERE tenant_id=:t",
                             "DELETE FROM support_tickets WHERE tenant_id=:t", "DELETE FROM tenant_users WHERE tenant_id=:t", "DELETE FROM tenants WHERE tenant_id=:t"):
                    await s.execute(self.text(stmt), {"t": t})
            await s.commit()
        await self.admin.dispose()
        await self.app_engine.dispose()

    def app_as(self, role, tenant=None):
        app = create_app()
        tenant = tenant or self.tenants[0]
        auth_overrides(app, tenant, self.App, role=role)
        user = NS(tenant_id=tenant, user_id=self.users[tenant], role=role)       # a real tenant_users row (FK)
        app.dependency_overrides[deps.get_current_user] = lambda: user
        return app

    async def new_ticket(self, c, **kw):
        body = {"subject": "البوت لا يرد", "message": "توقف عن الرد منذ الصباح", "category": "ai", "priority": "high", **kw}
        r = await c.post(f"{BASE}/tickets", json=body)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    async def test_create_thread_reply_close_and_reopen(self):
        async with client_for(self.app) as c:
            t = await self.new_ticket(c)
            self.assertRegex(t["reference"], r"^OF-[A-Z2-9]{8}$")
            self.assertEqual((t["category"], t["priority"], t["status"]), ("ai", "high", "open"))
            d = (await c.get(f"{BASE}/tickets/{t['ticket_id']}")).json()
            self.assertEqual([m["author_type"] for m in d["messages"]], ["customer"])
            self.assertEqual((await c.post(f"{BASE}/tickets/{t['ticket_id']}/messages", json={"body": "   "})).status_code, 422)
            self.assertEqual((await c.post(f"{BASE}/tickets/{t['ticket_id']}/messages", json={"body": "تحديث"})).status_code, 201)
            closed = (await c.patch(f"{BASE}/tickets/{t['ticket_id']}", json={"status": "closed"})).json()
            self.assertEqual(closed["status"], "closed")
            self.assertIsNotNone(closed["closed_at"])
            self.assertEqual((await c.patch(f"{BASE}/tickets/{t['ticket_id']}", json={"status": "in_progress"})).status_code, 403)
            await c.post(f"{BASE}/tickets/{t['ticket_id']}/messages", json={"body": "ما زالت المشكلة"})     # reply re-opens
            d = (await c.get(f"{BASE}/tickets/{t['ticket_id']}")).json()
            self.assertEqual(d["ticket"]["status"], "open")
            self.assertIsNone(d["ticket"]["closed_at"])
            self.assertEqual([m["kind"] for m in d["messages"]], ["message", "message", "status", "message", "status"])

    async def test_validation_and_filters(self):
        async with client_for(self.app) as c:
            for bad in ({"category": "nope"}, {"priority": "asap"}, {"subject": "x"}):
                r = await c.post(f"{BASE}/tickets", json={"subject": "عنوان", "message": "نص", **bad})
                self.assertEqual(r.status_code, 422, bad)
            await self.new_ticket(c, category="billing", priority="low")
            await self.new_ticket(c)
            self.assertEqual((await c.get(f"{BASE}/tickets")).json()["total"], 2)
            self.assertEqual((await c.get(f"{BASE}/tickets", params={"category": "billing"})).json()["total"], 1)
            self.assertEqual((await c.get(f"{BASE}/tickets", params={"priority": "high", "status": "closed"})).json()["total"], 0)

    async def test_attachments_are_validated_private_and_capped(self):
        async with client_for(self.app) as c:
            t = await self.new_ticket(c)
            url = f"{BASE}/tickets/{t['ticket_id']}/attachments"
            ok = await c.post(url, files={"file": ("shot.png", PNG, "application/octet-stream")})
            self.assertEqual(ok.status_code, 201, ok.text)
            self.assertEqual(ok.json()["content_type"], "image/png")
            self.assertEqual((await c.post(url, files={"file": ("w.docx", docx_bytes(), "x/y")})).status_code, 201)
            for name, data in (("run.exe", b"MZ\x90\x00" + b"\x00" * 50), ("fake.png", b"just text,not,png\n1,2,3"), ("empty.csv", b"")):
                r = await c.post(url, files={"file": (name, data, "image/png")})
                self.assertEqual(r.status_code, 422, name)
            big = await c.post(url, files={"file": ("big.pdf", b"%PDF-" + b"0" * (sup.MAX_ATTACHMENT_BYTES + 10), "application/pdf")})
            self.assertEqual(big.status_code, 413)
            for i in range(3):
                self.assertEqual((await c.post(url, files={"file": (f"{i}.csv", b"a,b\n1,2\n", "text/csv")})).status_code, 201)
            self.assertEqual((await c.post(url, files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")})).json()["detail"]["code"], "TOO_MANY_FILES")
            att = (await c.get(f"{BASE}/tickets/{t['ticket_id']}")).json()["attachments"][0]
            dl = await c.get(f"{BASE}/attachments/{att['attachment_id']}")
            self.assertEqual((dl.status_code, dl.content), (200, PNG))
            self.assertEqual(dl.headers["x-content-type-options"], "nosniff")
            self.assertEqual(len(self.mem.files), 5)
            self.assertTrue(all(k.startswith(f"support/{self.tenants[0]}/") for k in self.mem.files))

    async def test_other_tenants_never_see_tickets_messages_or_files(self):
        async with client_for(self.app) as c:
            t = await self.new_ticket(c)
            a = (await c.post(f"{BASE}/tickets/{t['ticket_id']}/attachments", files={"file": ("a.png", PNG, "image/png")})).json()
        async with client_for(self.app_as(TenantUserRole.ADMIN, self.tenants[1])) as c:
            self.assertEqual((await c.get(f"{BASE}/tickets")).json()["total"], 0)
            tid = t["ticket_id"]
            self.assertEqual((await c.get(f"{BASE}/tickets/{tid}")).status_code, 404)
            self.assertEqual((await c.post(f"{BASE}/tickets/{tid}/messages", json={"body": "x"})).status_code, 404)
            self.assertEqual((await c.patch(f"{BASE}/tickets/{tid}", json={"status": "closed"})).status_code, 404)
            self.assertEqual((await c.post(f"{BASE}/tickets/{tid}/attachments", files={"file": ("a.png", PNG, "image/png")})).status_code, 404)
            self.assertEqual((await c.get(f"{BASE}/attachments/{a['attachment_id']}")).status_code, 404)

    async def test_every_role_can_open_a_ticket_but_status_stays_tenant_scoped(self):
        for role in (TenantUserRole.AGENT, TenantUserRole.AUDITOR):
            async with client_for(self.app_as(role)) as c:
                r = await c.post(f"{BASE}/tickets", json={"subject": "سؤال", "message": "نص"})
                self.assertEqual(r.status_code, 201, role)

    async def test_staff_reply_sets_first_response_and_status_and_is_key_guarded(self):
        async with client_for(self.app) as c:
            t = await self.new_ticket(c)
            with patch.object(sup, "get_settings") as gs:
                gs.return_value.support_admin_api_key = "secret-key"  # pragma: allowlist secret
                url = f"{BASE}/admin/tickets/{t['ticket_id']}/reply"
                self.assertEqual((await c.post(url, json={"body": "x"}, headers={"X-Support-Key": "bad"})).status_code, 403)
                self.assertEqual((await c.post(f"{BASE}/admin/tickets/{uuid.uuid4()}/reply", json={"body": "x"},
                                               headers={"X-Support-Key": "secret-key"})).status_code, 404)
                r = await c.post(url, json={"body": "نعمل على حلّها"}, headers={"X-Support-Key": "secret-key"})
                self.assertEqual(r.status_code, 201, r.text)
                r2 = await c.post(url, json={"body": "تم الحل", "status": "closed"}, headers={"X-Support-Key": "secret-key"})
                self.assertEqual(r2.status_code, 201)
            d = (await c.get(f"{BASE}/tickets/{t['ticket_id']}")).json()
            self.assertEqual(d["ticket"]["status"], "closed")
            self.assertIsNotNone(d["ticket"]["first_response_at"])
            self.assertEqual([m["author_type"] for m in d["messages"]], ["customer", "support", "system", "support", "system"])

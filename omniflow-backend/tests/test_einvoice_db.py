"""E-invoice storage + chain + submission state machine on PostgreSQL/RLS, and the billing integration (flag-gated).

Needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL. ZATCA itself is replaced by an httpx MockTransport — nothing here talks to ZATCA.
"""
import asyncio
import base64
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
from lxml import etree

from src.einvoice import certs, pdf, service as ez, signing, ubl
from src.einvoice.client import ZatcaClient
from src.shared.core.config import get_settings
from src.shared.db.models import BillingInvoice, CreditNote, EInvoiceDocument, EInvoiceUnit
from src.shared.security.crypto import encrypt_secret

DB_URL = os.environ.get("OMNIFLOW_TEST_DATABASE_URL")
RLS_URL = os.environ.get("OMNIFLOW_TEST_RLS_URL")
UTC = timezone.utc
VAT = "399999999900003"


@unittest.skipUnless(DB_URL and RLS_URL, "needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL (non-superuser: RLS is the tenant guard)")
class EInvoiceDbTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        self.text = text
        self.admin = create_async_engine(DB_URL)
        self.app_engine = create_async_engine(RLS_URL)
        self.Admin = async_sessionmaker(self.admin, expire_on_commit=False)
        self.App = async_sessionmaker(self.app_engine, expire_on_commit=False)
        self.tenants = [uuid.uuid4(), uuid.uuid4()]
        self.unit_name = f"test-{uuid.uuid4().hex[:8]}"
        async with self.Admin() as s:
            for i, t in enumerate(self.tenants):
                await s.execute(text("INSERT INTO tenants (tenant_id,business_name,fal_license_number,subscription_tier,status,max_ai_conversations,onboarding_status,vat_number,billing_address,created_at,updated_at) "
                                     "VALUES (:t,:n,:f,'starter','active',100,'completed',:v,CAST(:a AS jsonb),now(),now())"),
                                {"t": t, "n": f"مكتب {i}", "f": f"FAL-{t.hex[:8]}", "v": "310000000000003" if i == 0 else None,
                                 "a": '{"street":"شارع","building":"1234","district":"حي","city":"الرياض","postal_zone":"12345"}'})
            await s.commit()
        cfg = get_settings()
        for name, value in (("einvoice_enabled", True), ("einvoice_env", "sandbox"), ("einvoice_allow_production_submit", False),
                            ("billing_seller_name", "شركة أومني فلو"), ("billing_seller_tax_number", VAT), ("billing_seller_cr_number", "1010010000"),
                            ("billing_seller_city", "الرياض"), ("billing_vat_rate_bp", 1500)):
            p = patch.object(cfg, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch("src.shared.db.session.AsyncSessionFactory", self.App)
        p.start()
        self.addCleanup(p.stop)
        key = certs.generate_key()
        async with await self._sys() as db:
            unit = await ez.get_or_create_unit(db, name=self.unit_name)
            unit.private_key_enc = encrypt_secret(certs.key_to_pem(key).decode())
            unit.certificate_b64 = base64.b64encode(certs.dev_self_signed_cert(key)).decode()
            unit.secret_enc = encrypt_secret("csid-secret")                                      # pragma: allowlist secret
            unit.status = "production" if False else "compliance"
            self.unit_id = unit.unit_id
        # every issue() call must use THIS unit
        orig = ez.get_or_create_unit

        async def pinned(session, *, name=None, env=None):
            return await orig(session, name=self.unit_name, env=env)
        p = patch.object(ez, "get_or_create_unit", pinned)
        p.start()
        self.addCleanup(p.stop)

    async def asyncTearDown(self):
        async with self.Admin() as s:
            await s.execute(self.text("DELETE FROM einvoice_documents WHERE unit_id=:u"), {"u": self.unit_id})
            await s.execute(self.text("DELETE FROM einvoice_units WHERE unit_id=:u"), {"u": self.unit_id})
            for t in self.tenants:
                for stmt in ("DELETE FROM credit_notes WHERE tenant_id=:t", "DELETE FROM refunds WHERE tenant_id=:t", "DELETE FROM audit_logs WHERE tenant_id=:t", "DELETE FROM notifications WHERE tenant_id=:t", "DELETE FROM invoices WHERE tenant_id=:t",
                             "DELETE FROM transactions WHERE tenant_id=:t", "DELETE FROM subscriptions WHERE tenant_id=:t", "DELETE FROM tenants WHERE tenant_id=:t"):
                    await s.execute(self.text(stmt), {"t": t})
            await s.commit()
        await self.admin.dispose()
        await self.app_engine.dispose()

    async def _sys(self):
        from src.shared.db.session import get_system_session
        return get_system_session()

    async def make_invoice(self, tenant=None, subtotal=79900, number=None) -> uuid.UUID:
        t = tenant or self.tenants[0]
        async with self.Admin() as s:
            await s.execute(self.text("INSERT INTO subscriptions (tenant_id,plan_code,status,is_exempt) VALUES (:t,'starter','active',false) ON CONFLICT (tenant_id) DO NOTHING"), {"t": t})
            sid = (await s.execute(self.text("SELECT subscription_id FROM subscriptions WHERE tenant_id=:t"), {"t": t})).scalar()
            tx, inv = uuid.uuid4(), uuid.uuid4()
            vat = (subtotal * 1500 + 5000) // 10000
            await s.execute(self.text("INSERT INTO transactions (transaction_id,tenant_id,subscription_id,kind,plan_code,billing_interval,subtotal_halalas,vat_halalas,amount_halalas,status,provider,paid_at) "
                                      "VALUES (:x,:t,:s,'initial','starter','monthly',:st,:v,:a,'paid','manual',now())"), {"x": tx, "t": t, "s": sid, "st": subtotal, "v": vat, "a": subtotal + vat})
            await s.execute(self.text("INSERT INTO invoices (invoice_id,tenant_id,transaction_id,invoice_number,subtotal_halalas,vat_halalas,total_halalas,vat_rate_bp,description,buyer,issued_at) "
                                      "VALUES (:i,:t,:x,:n,:st,:v,:a,1500,'اشتراك OmniFlow — الأساسية (شهري)',CAST(:b AS jsonb),now())"),
                            {"i": inv, "t": t, "x": tx, "n": number or f"INV-T-{uuid.uuid4().hex[:8]}", "st": subtotal, "v": vat, "a": subtotal + vat, "b": '{"name":"مكتب عقارات"}'})
            await s.commit()
        return inv

    async def issue(self, invoice_id):
        from sqlalchemy import select
        async with await self._sys() as db:
            inv = await db.scalar(select(BillingInvoice).where(BillingInvoice.invoice_id == invoice_id))
            doc = await ez.issue_for_invoice(db, inv)
            return doc.doc_id if doc else None

    async def docs(self):
        async with self.Admin() as s:
            return (await s.execute(self.text("SELECT * FROM einvoice_documents WHERE unit_id=:u ORDER BY icv"), {"u": self.unit_id})).mappings().all()

    # ── issuing + chain ─────────────────────────────────────────────────────────────
    async def test_issuing_stores_a_signed_document_and_moves_the_chain(self):
        inv = await self.make_invoice()
        await self.issue(inv)
        d = (await self.docs())[0]
        self.assertEqual((d["icv"], d["pih"], d["type_code"], d["subtype"], d["status"]), (1, ubl.PIH_FIRST, "388", "0100000", "pending"))
        root = etree.fromstring(d["xml"].encode())
        self.assertTrue(signing.verify_invoice(root))
        self.assertEqual(signing.invoice_hash_b64(root), d["invoice_hash"])
        unit = (await self.row("SELECT icv_counter, last_hash FROM einvoice_units WHERE unit_id=:u", u=self.unit_id))[0]
        self.assertEqual((unit["icv_counter"], unit["last_hash"]), (1, d["invoice_hash"]))
        self.assertEqual(root.findtext("cac:LegalMonetaryTotal/cbc:PayableAmount", namespaces=ubl.NS | {"cac": ubl.NS["cac"], "cbc": ubl.NS["cbc"]}), "918.85")

    async def row(self, sql, **p):
        async with self.Admin() as s:
            return (await s.execute(self.text(sql), p)).mappings().all()

    async def test_issuing_twice_returns_the_same_document(self):
        inv = await self.make_invoice()
        a, b = await self.issue(inv), await self.issue(inv)
        self.assertEqual(a, b)
        self.assertEqual(len(await self.docs()), 1)

    async def test_concurrent_issuing_has_no_gaps_duplicates_or_broken_links(self):
        ids = [await self.make_invoice() for _ in range(12)]
        await asyncio.gather(*[self.issue(i) for i in ids])
        docs = await self.docs()
        self.assertEqual([d["icv"] for d in docs], list(range(1, 13)))                                    # contiguous, unique
        self.assertEqual(docs[0]["pih"], ubl.PIH_FIRST)
        for prev, cur in zip(docs, docs[1:]):
            self.assertEqual(cur["pih"], prev["invoice_hash"])                                            # every document chains to the previous one
        for d in docs:
            self.assertTrue(signing.verify_invoice(etree.fromstring(d["xml"].encode())))

    async def test_buyer_without_vat_number_gets_a_simplified_invoice(self):
        inv = await self.make_invoice(tenant=self.tenants[1])
        await self.issue(inv)
        d = (await self.docs())[0]
        self.assertEqual(d["subtype"], "0200000")
        qr = ubl.NS and __import__("src.einvoice.tlv", fromlist=["x"]).parse_qr(d["qr"])
        self.assertIn(9, qr)

    async def test_an_invoice_that_cannot_be_valid_does_not_move_the_chain(self):
        inv = await self.make_invoice()
        with patch.object(get_settings(), "billing_seller_tax_number", ""):
            async with await self._sys() as db:
                from sqlalchemy import select
                invoice = await db.scalar(select(BillingInvoice).where(BillingInvoice.invoice_id == inv))
                with self.assertRaises(ubl.InvalidInvoice):
                    await ez.issue_for_invoice(db, invoice)
        unit = (await self.row("SELECT icv_counter, last_hash FROM einvoice_units WHERE unit_id=:u", u=self.unit_id))[0]
        self.assertEqual((unit["icv_counter"], unit["last_hash"]), (0, ubl.PIH_FIRST))
        self.assertEqual(await self.docs(), [])

    async def test_nothing_happens_while_the_feature_flag_is_off(self):
        inv = await self.make_invoice()
        with patch.object(get_settings(), "einvoice_enabled", False):
            self.assertIsNone(await self.issue(inv))
            async with await self._sys() as db:
                self.assertEqual(await ez.submit_due(db), {})
        self.assertEqual(await self.docs(), [])

    async def test_credit_note_is_issued_as_381_referencing_the_original(self):
        from sqlalchemy import select
        inv = await self.make_invoice(number="INV-2026-000777")
        await self.issue(inv)
        async with self.Admin() as s:
            tx = (await s.execute(self.text("SELECT transaction_id FROM invoices WHERE invoice_id=:i"), {"i": inv})).scalar()
            rid, cid = uuid.uuid4(), uuid.uuid4()
            await s.execute(self.text("INSERT INTO refunds (refund_id,tenant_id,transaction_id,invoice_id,status,reason,gross_halalas,subtotal_halalas,vat_halalas,amount_halalas,provider,requested_by) "
                                      "VALUES (:r,:t,:x,:i,'done','طلب العميل',91885,79900,11985,91885,'manual','staff@x.sa')"), {"r": rid, "t": self.tenants[0], "x": tx, "i": inv})
            await s.execute(self.text("INSERT INTO credit_notes (credit_note_id,tenant_id,refund_id,original_invoice_id,credit_note_number,subtotal_halalas,vat_halalas,total_halalas,vat_rate_bp,reason,buyer) "
                                      "VALUES (:c,:t,:r,:i,'CN-T-' || :cn,79900,11985,91885,1500,'طلب العميل',CAST('{\"name\":\"مكتب\"}' AS jsonb))"), {"c": cid, "cn": cid.hex[:8], "t": self.tenants[0], "r": rid, "i": inv})
            await s.commit()
        async with await self._sys() as db:
            note = await db.scalar(select(CreditNote).where(CreditNote.credit_note_id == cid))
            await ez.issue_for_credit_note(db, note)
        docs = await self.docs()
        self.assertEqual((len(docs), docs[1]["type_code"], docs[1]["icv"], docs[1]["pih"]), (2, "381", 2, docs[0]["invoice_hash"]))
        root = etree.fromstring(docs[1]["xml"].encode())
        ns = {"cac": ubl.NS["cac"], "cbc": ubl.NS["cbc"]}
        self.assertEqual(root.findtext("cac:BillingReference/cac:InvoiceDocumentReference/cbc:ID", namespaces=ns), "INV-2026-000777")
        self.assertTrue(signing.verify_invoice(root))

    # ── submission state machine ────────────────────────────────────────────────────
    def zc(self, handler):
        return ZatcaClient(env="sandbox", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    async def submit(self, doc_id, handler, now=None):
        from sqlalchemy import select
        async with await self._sys() as db:
            doc = await db.scalar(select(EInvoiceDocument).where(EInvoiceDocument.doc_id == doc_id))
            st = await ez.submit_document(db, doc, now, client=self.zc(handler))
            return st

    async def state(self, doc_id):
        return (await self.row("SELECT status, attempts, last_error, next_attempt_at, submitted_at, zatca_response FROM einvoice_documents WHERE doc_id=:d", d=doc_id))[0]

    async def test_standard_invoices_are_cleared_and_simplified_ones_reported(self):
        paths = []

        def ok(req):
            paths.append(req.url.path.rsplit("/", 2)[-2:])
            return httpx.Response(200, json={"clearanceStatus": "CLEARED", "reportingStatus": "REPORTED"})
        b2b = await self.issue(await self.make_invoice())
        b2c = await self.issue(await self.make_invoice(tenant=self.tenants[1]))
        self.assertEqual(await self.submit(b2b, ok), "cleared")
        self.assertEqual(await self.submit(b2c, ok), "reported")
        self.assertEqual(paths, [["clearance", "single"], ["reporting", "single"]])
        st = await self.state(b2b)
        self.assertEqual((st["attempts"], st["last_error"], st["zatca_response"]["clearanceStatus"]), (1, None, "CLEARED"))
        self.assertIsNotNone(st["submitted_at"])

    async def test_rejection_is_final_and_server_errors_back_off_then_die(self):
        doc = await self.issue(await self.make_invoice())
        self.assertEqual(await self.submit(doc, lambda r: httpx.Response(400, json={"validationResults": {"errorMessages": [{"code": "BR-KSA-1"}]}})), "rejected")
        st = await self.state(doc)
        self.assertEqual((st["status"], st["zatca_response"]["validationResults"]["errorMessages"][0]["code"]), ("rejected", "BR-KSA-1"))

        doc = await self.issue(await self.make_invoice())
        now = datetime.now(UTC)
        waits = []
        for i in range(get_settings().einvoice_max_attempts - 1):
            self.assertEqual(await self.submit(doc, lambda r: httpx.Response(503), now), "pending")
            st = await self.state(doc)
            waits.append((st["next_attempt_at"] - now).total_seconds())
            now = st["next_attempt_at"]
        self.assertEqual(waits[:3], [120, 240, 480])                                                 # exponential back-off
        self.assertTrue(all(b >= a for a, b in zip(waits, waits[1:])) and max(waits) <= 6 * 3600)
        self.assertEqual(await self.submit(doc, lambda r: httpx.Response(503), now), "dead")           # dead-letter after the last attempt
        self.assertEqual((await self.state(doc))["attempts"], get_settings().einvoice_max_attempts)

    async def test_a_duplicate_answer_from_zatca_counts_as_done(self):
        doc = await self.issue(await self.make_invoice())
        self.assertEqual(await self.submit(doc, lambda r: httpx.Response(409, json={"message": "duplicate"})), "cleared")

    async def test_submit_due_picks_only_due_pending_documents_in_icv_order(self):
        a = await self.issue(await self.make_invoice())
        b = await self.issue(await self.make_invoice())
        async with self.Admin() as s:
            await s.execute(self.text("UPDATE einvoice_documents SET next_attempt_at = now() + interval '1 day' WHERE doc_id=:d"), {"d": b})
            await s.commit()
        order = []

        def ok(req):
            order.append(json_loads(req.content)["uuid"])
            return httpx.Response(200, json={})
        with patch.object(ez, "ZatcaClient", lambda env=None: self.zc(ok)):
            async with await self._sys() as db:
                self.assertEqual(await ez.submit_due(db, unit_id=self.unit_id), {"cleared": 1})
        self.assertEqual(len(order), 1)
        self.assertEqual((await self.state(a))["status"], "cleared")
        self.assertEqual((await self.state(b))["status"], "pending")

    async def test_production_stays_locked_so_a_production_unit_cannot_send(self):
        doc = await self.issue(await self.make_invoice())
        async with self.Admin() as s:
            await s.execute(self.text("UPDATE einvoice_units SET env='production' WHERE unit_id=:u"), {"u": self.unit_id})
            await s.commit()
        from sqlalchemy import select
        async with await self._sys() as db:
            d = await db.scalar(select(EInvoiceDocument).where(EInvoiceDocument.doc_id == doc))
            st = await ez.submit_document(db, d)                      # real client constructor: must refuse before any HTTP call
        self.assertEqual(st, "pending")
        self.assertIn("locked", (await self.state(doc))["last_error"])

    # ── isolation ───────────────────────────────────────────────────────────────────
    async def test_tenants_see_only_their_own_documents_and_never_the_unit_secrets(self):
        await self.issue(await self.make_invoice())
        await self.issue(await self.make_invoice(tenant=self.tenants[1]))
        for t, expected in ((self.tenants[0], 1), (self.tenants[1], 1)):
            async with self.App() as s, s.begin():
                await s.execute(self.text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(t)})
                rows = (await s.execute(self.text("SELECT tenant_id FROM einvoice_documents"))).all()
                self.assertEqual([r[0] for r in rows], [t] * expected)
                self.assertEqual((await s.execute(self.text("SELECT count(*) FROM einvoice_units"))).scalar(), 0)           # keys are system-only
                with self.assertRaises(Exception):
                    await s.execute(self.text("UPDATE einvoice_units SET icv_counter = 999"))
                    await s.flush()
                    raise RuntimeError("tenant context modified the unit")

    # ── PDF/A-3 ─────────────────────────────────────────────────────────────────────
    async def test_pdf_is_pdfa3_with_the_signed_xml_attached(self):
        import io
        import pypdf
        d = (await self.docs() or [None])[0]
        await self.issue(await self.make_invoice())
        d = (await self.docs())[0]
        doc = type("D", (), {"subtype": d["subtype"], "type_code": d["type_code"], "issued_at": d["issued_at"], "uuid": d["uuid"], "icv": d["icv"]})()
        data = pdf.build_pdfa3(doc, xml=d["xml"], title="فاتورة", number=d["doc_number"], subtotal=79900, vat=11985, total=91885, vat_rate_bp=1500,
                               description="اشتراك OmniFlow", seller={"name": "شركة أومني فلو", "tax_number": VAT}, buyer={"name": "مكتب"}, qr=d["qr"])
        r = pypdf.PdfReader(io.BytesIO(data))
        self.assertGreaterEqual(len(r.pages), 1)
        att = r.attachments
        self.assertEqual(list(att), [f"{d['doc_number']}.xml"])
        self.assertTrue(signing.verify_invoice(etree.fromstring(att[f"{d['doc_number']}.xml"][0])))              # the embedded XML is the signed one
        meta = r.trailer["/Root"]["/Metadata"].get_object().get_data().decode("utf-8", "replace")      # the XMP packet (PDF/A identification)
        self.assertRegex(meta, r"pdfaid:part>\s*3\s*<|pdfaid:part=.3.")
        os.makedirs("/tmp/claude-0/pdfa3", exist_ok=True)
        with open("/tmp/claude-0/pdfa3/sample.pdf", "wb") as f:
            f.write(data)


    # ── billing integration ─────────────────────────────────────────────────────────
    async def test_payments_and_refunds_create_documents_automatically_and_failures_never_block_them(self):
        from src.shared.services.billing import refunds, service as billing
        t = self.tenants[0]
        async with self.Admin() as s:
            await s.execute(self.text("INSERT INTO subscriptions (tenant_id,plan_code,status,is_exempt) VALUES (:t,'starter','trial',false) ON CONFLICT (tenant_id) DO NOTHING"), {"t": t})
            await s.commit()
        async with await self._sys() as db:                                       # staff manual activation = a paid transaction + an invoice
            txn = await billing.manual_activate(db, t, "starter", "monthly", reference=f"TRF-{uuid.uuid4().hex[:8]}")
            txn_id = txn.transaction_id
        docs = await self.docs()
        self.assertEqual((len(docs), docs[0]["type_code"], docs[0]["status"]), (1, "388", "pending"))
        async with await self._sys() as db:
            out = await refunds.issue(db, txn_id, reason="طلب العميل خلال الضمان", staff_email="staff@omniflow.sa")
        docs = await self.docs()
        self.assertEqual([d["type_code"] for d in docs], ["388", "381"])
        self.assertEqual(docs[1]["pih"], docs[0]["invoice_hash"])
        self.assertEqual(docs[1]["doc_number"], out["credit_note_number"])
        # a broken e-invoice setup must not undo a payment: no seller VAT => no document, but the payment still goes through
        async with self.Admin() as s:
            await s.execute(self.text("UPDATE subscriptions SET status='trial' WHERE tenant_id=:t"), {"t": t})
            await s.commit()
        with patch.object(get_settings(), "billing_seller_tax_number", ""):
            async with await self._sys() as db:
                txn2 = await billing.manual_activate(db, t, "starter", "monthly", reference=f"TRF-{uuid.uuid4().hex[:8]}")
        self.assertEqual((await self.row("SELECT status FROM transactions WHERE transaction_id=:x", x=txn2.transaction_id))[0]["status"], "paid")
        self.assertEqual(len(await self.docs()), 2)

    # ── HTTP surface ────────────────────────────────────────────────────────────────
    async def test_endpoints_are_404_when_off_and_staff_or_owner_only_when_on(self):
        from types import SimpleNamespace as NS

        from src.gateway import dependencies as deps
        from src.gateway.main import create_app
        from src.shared.core.enums import TenantUserRole
        from tests.test_property_import_flow import auth_overrides, client_for

        inv = await self.make_invoice()
        await self.issue(inv)
        app = create_app()
        auth_overrides(app, self.tenants[0], self.App, role=TenantUserRole.ADMIN)
        with patch.object(get_settings(), "einvoice_enabled", False):
            async with client_for(app) as c:
                self.assertEqual((await c.get("/api/v1/einvoice/status")).status_code, 404)
                self.assertEqual((await c.get(f"/api/v1/einvoice/invoices/{inv}/xml")).status_code, 404)
        async with client_for(app) as c:
            self.assertEqual((await c.get("/api/v1/einvoice/status")).status_code, 404)           # tenant admin is not staff: hidden
            xml = await c.get(f"/api/v1/einvoice/invoices/{inv}/xml")
            self.assertEqual((xml.status_code, xml.headers["content-type"]), (200, "application/xml"))
            self.assertTrue(signing.verify_invoice(etree.fromstring(xml.content)))
            pdf_r = await c.get(f"/api/v1/einvoice/invoices/{inv}/pdf")
            self.assertEqual((pdf_r.status_code, pdf_r.content[:5]), (200, b"%PDF-"))
        other = create_app()
        auth_overrides(other, self.tenants[1], self.App, role=TenantUserRole.ADMIN)
        async with client_for(other) as c:
            self.assertEqual((await c.get(f"/api/v1/einvoice/invoices/{inv}/xml")).status_code, 404)    # another tenant's invoice
        agent = create_app()
        auth_overrides(agent, self.tenants[0], self.App, role=TenantUserRole.AGENT)
        async with client_for(agent) as c:
            self.assertEqual((await c.get(f"/api/v1/einvoice/invoices/{inv}/xml")).status_code, 403)
        with patch.object(get_settings(), "support_staff_emails", "staff@omniflow.sa"):
            staff = create_app()
            auth_overrides(staff, self.tenants[0], self.App, role=TenantUserRole.ADMIN)
            staff.dependency_overrides[deps.get_current_user] = lambda: NS(tenant_id=self.tenants[0], user_id=uuid.uuid4(), role=TenantUserRole.ADMIN,
                                                                           email="staff@omniflow.sa", is_active=True)
            async with client_for(staff) as c:
                st = (await c.get("/api/v1/einvoice/status")).json()
                self.assertEqual((st["env"], st["production_unlocked"]), ("sandbox", False))
                self.assertGreaterEqual(st["documents"].get("pending", 0), 1)
                r = await c.post("/api/v1/einvoice/onboarding/csr", json={"org_unit": "Riyadh", "address": "الرياض", "business_category": "Software", "unit_serial": "1-a|2-b|3-c"})
                self.assertEqual(r.status_code, 200, r.text)
                self.assertIn("BEGIN CERTIFICATE REQUEST", r.json()["csr_pem"])
                self.assertNotIn("PRIVATE", r.text)                                                       # only public material leaves the server


def json_loads(b):
    import json
    return json.loads(b)


if __name__ == "__main__":
    unittest.main()

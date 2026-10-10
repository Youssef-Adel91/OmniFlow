"""Broadcasts: consent-aware audience, readiness/template gates, confirmation, stats, worker send, webhook status, STOP keyword.

Pure tests run everywhere; the DB class needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL (see test_customers.py).
"""
import json
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from src.gateway.main import create_app
from src.shared.core.enums import TenantUserRole
from src.shared.db.models import Customer
from src.shared.services import broadcast_audience as aud
from src.shared.services import customers as csvc
from src.shared.services import meta_templates
from tests.test_property_import_flow import auth_overrides, client_for

DB_URL = os.environ.get("OMNIFLOW_TEST_DATABASE_URL")
RLS_URL = os.environ.get("OMNIFLOW_TEST_RLS_URL")
BASE = "/api/v1/broadcasts"


class PureTests(unittest.TestCase):
    def test_audience_parsing_accepts_legacy_values_and_rejects_unknown_ones(self):
        self.assertEqual(aud.parse_audience(None), aud.Audience())
        self.assertEqual(aud.parse_audience("VIP").segment, "vip")
        self.assertEqual(aud.parse_audience("all_vip").segment, "vip")        # what the old composer sent
        self.assertEqual(aud.parse_audience({"list_type": "dormant"}).segment, "inactive")
        a = aud.parse_audience({"segment": "engaged", "tags": [" a ", "a", "b", ""], "city": " الرياض ", "sources": ["Instagram"]})
        self.assertEqual((a.segment, a.tags, a.city, a.sources), ("engaged", ("a", "b"), "الرياض", ("instagram",)))
        for bad in ("villa_seekers", {"segment": "x"}, {"segment": "all", "sources": ["tiktok"]}, {"tags": [str(i) for i in range(11)]}, {"segment": 5}):
            with self.assertRaises(aud.InvalidAudience, msg=str(bad)):
                aud.parse_audience(bad)                                        # never silently widened to "all"

    def test_template_variables_are_filled_per_recipient_and_never_empty(self):
        c = Customer(display_name="سارة", city="جدة")
        self.assertEqual(aud.render_params(["مرحباً {name} من {city}", "", "x"], c), ["مرحباً سارة من جدة", "-", "x"])
        self.assertEqual(aud.render_params(["{name}|{city}"], Customer()), ["عميلنا العزيز|مدينتك"])

    def test_meta_template_parsing_flags_layouts_we_cannot_fill(self):
        ok = meta_templates.parse_template({"name": "promo", "language": "ar", "status": "APPROVED", "category": "MARKETING", "components": [
            {"type": "HEADER", "format": "TEXT", "text": "عرض"}, {"type": "BODY", "text": "مرحباً {{1}}، عرض في {{2}}"},
            {"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": "إلغاء الاشتراك"}]}]})
        self.assertEqual((ok["variables"], ok["supported"]), (2, True))
        media = meta_templates.parse_template({"name": "m", "components": [{"type": "HEADER", "format": "IMAGE"}, {"type": "BODY", "text": "x"}]})
        self.assertFalse(media["supported"])
        url = meta_templates.parse_template({"name": "u", "components": [{"type": "BODY", "text": "x"}, {"type": "BUTTONS", "buttons": [{"type": "URL", "url": "https://x.sa/{{1}}"}]}]})
        self.assertFalse(url["supported"])

    def test_stop_keyword_matches_whole_messages_only(self):
        for yes in ("STOP", " stop. ", "إلغاء الاشتراك", "الغاء الاشتراك", "إيقاف", "Unsubscribe"):
            self.assertTrue(csvc.is_opt_out_keyword(yes), yes)
        for no in ("please stop calling me at night", "أريد إيقاف الحجز غدًا", "مرحبا", ""):
            self.assertFalse(csvc.is_opt_out_keyword(no), no)


class FakeMeta:
    def __init__(self):
        self.sent = []

    async def send_template_message(self, **kw):
        self.sent.append(kw)
        if kw["to"].endswith("0003"):
            from src.channel_adapters.whatsapp.client import WhatsAppAPIError
            raise WhatsAppAPIError("131026: undeliverable", 400)
        return NS(wamid=f"wamid.{len(self.sent)}")


@unittest.skipUnless(DB_URL and RLS_URL, "needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL (non-superuser: RLS is the tenant guard)")
class BroadcastDbTests(unittest.IsolatedAsyncioTestCase):
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
        p = patch("src.shared.db.session.AsyncSessionFactory", self.App)
        p.start()
        self.addCleanup(p.stop)
        self.app = create_app()
        auth_overrides(self.app, self.tenants[0], self.App)

    async def asyncTearDown(self):
        async with self.Admin() as s:
            for t in self.tenants:
                for stmt in ("DELETE FROM audit_logs WHERE tenant_id=:t", "DELETE FROM broadcast_deliveries WHERE tenant_id=:t",
                             "DELETE FROM broadcast_campaigns WHERE tenant_id=:t", "DELETE FROM messages WHERE conversation_id IN (SELECT conversation_id FROM conversations WHERE tenant_id=:t)",
                             "DELETE FROM conversations WHERE tenant_id=:t", "DELETE FROM customers WHERE tenant_id=:t", "DELETE FROM tenants WHERE tenant_id=:t"):
                    await s.execute(self.text(stmt), {"t": t})
            await s.commit()
        await self.admin.dispose()
        await self.app_engine.dispose()

    # helpers -----------------------------------------------------------------------------------------
    async def cust(self, tenant=None, phone="+966500000001", **kw):
        cols = {"customer_id": uuid.uuid4(), "tenant_id": tenant or self.tenants[0], "unified_phone": phone, "phone_e164": phone, "source": "whatsapp",
                "channel_user_ids": "{}", "tags": "[]", "vcard_state": "STATE_NEW", "marketing_opt_in": True, "is_vip": False, "is_processing_restricted": False}
        cols.update(kw)
        marks = ", ".join(f"CAST(:{k} AS jsonb)" if k in ("channel_user_ids", "tags") else f":{k}" for k in cols)
        async with self.Admin() as s:
            await s.execute(self.text(f"INSERT INTO customers ({', '.join(cols)}, created_at, updated_at) VALUES ({marks}, now(), now())"), cols)
            await s.commit()
        return cols["customer_id"]

    async def connect_whatsapp(self, waba=True):
        async with self.Admin() as s:
            await s.execute(self.text("UPDATE tenants SET whatsapp_phone_number_id='123', meta_access_token='tok', whatsapp_waba_id=:w WHERE tenant_id=:t"),
                            {"t": self.tenants[0], "w": "999" if waba else None})
            await s.commit()

    async def seed_audience(self):
        self.ok1 = await self.cust(phone="+966500000001", is_vip=True, tags='["vip"]', city="الرياض", display_name="أحمد")
        self.ok2 = await self.cust(phone="+966500000002", tags='["مستثمر"]', city="جدة", display_name="سارة")
        await self.cust(phone="+966500000010", marketing_opt_in=False)                                                        # never consented
        await self.cust(phone="+966500000011", marketing_opt_in=False, opt_out_at=datetime.now(timezone.utc))                # unsubscribed
        await self.cust(phone="+966500000012", is_processing_restricted=True)                                                 # PDPL objection
        await self.cust(phone="+966500000013", archived_at=datetime.now(timezone.utc))
        await self.cust(phone="1784000000001", phone_e164=None, source="instagram", channel_user_ids='{"instagram":"1784000000001"}')   # no real phone
        await self.cust(tenant=self.tenants[1], phone="+966500000099")                                                         # another tenant

    async def campaign(self, c, **kw):
        body = {"title": "حملة", "message_template": "نص", "target_audience": {"segment": "all"}, "meta_template_id": "promo",
                "template_params": ["{name}"], **kw}
        r = await c.post(BASE, json=body)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    async def audit(self):
        async with self.Admin() as s:
            r = await s.execute(self.text("SELECT action, details FROM audit_logs WHERE tenant_id=:t ORDER BY created_at"), {"t": self.tenants[0]})
            return [dict(x._mapping) for x in r]

    # tests -------------------------------------------------------------------------------------------
    async def test_preview_counts_only_consented_reachable_customers_and_explains_exclusions(self):
        await self.seed_audience()
        async with client_for(self.app) as c:
            p = (await c.get(f"{BASE}/preview-audience", params={"target_audience": "all"})).json()
            self.assertEqual((p["recipients_count"], p["excluded_no_consent"], p["excluded_opted_out"], p["excluded_restricted"], p["excluded_no_phone"]),
                             (2, 1, 1, 1, 1))
            self.assertEqual(p["total_customers"], 6)                         # archived and other tenants excluded
            self.assertEqual((await c.get(f"{BASE}/preview-audience", params={"target_audience": "vip"})).json()["recipients_count"], 1)
            self.assertEqual((await c.get(f"{BASE}/preview-audience", params={"tags": "مستثمر,xyz"})).json()["recipients_count"], 1)
            self.assertEqual((await c.get(f"{BASE}/preview-audience", params={"city": "جد"})).json()["recipients_count"], 1)
            self.assertEqual((await c.get(f"{BASE}/preview-audience", params={"sources": "instagram"})).json()["recipients_count"], 0)
            bad = await c.get(f"{BASE}/preview-audience", params={"target_audience": "villa_seekers"})
            self.assertEqual((bad.status_code, bad.json()["detail"]["code"]), (422, "INVALID_AUDIENCE"))

    async def test_create_normalises_the_audience_and_is_role_gated(self):
        async with client_for(self.app) as c:
            made = await self.campaign(c, target_audience="all_vip")
            self.assertEqual(made["target_audience"]["segment"], "vip")
            self.assertEqual((made["status"], made["send_rate_per_second"], made["template_params"]), ("draft", 20, ["{name}"]))
            self.assertEqual((await c.post(BASE, json={"title": "x1", "target_audience": "nope"})).status_code, 422)
            self.assertEqual((await c.post(BASE, json={"title": "x1", "send_rate_per_second": 500})).status_code, 422)
        async with client_for(self.app_as(TenantUserRole.AUDITOR)) as c:
            self.assertEqual((await c.post(BASE, json={"title": "xx"})).status_code, 403)
            self.assertEqual((await c.get(BASE)).status_code, 200)

    def app_as(self, role, tenant=None):
        app = create_app()
        auth_overrides(app, tenant or self.tenants[0], self.App, role=role)
        return app

    async def test_schedule_gates_whatsapp_template_recipients_and_confirmation(self):
        await self.seed_audience()
        async with client_for(self.app) as c:
            camp = await self.campaign(c)
            cid = camp["campaign_id"]
            r = await c.post(f"{BASE}/{cid}/schedule", json={})
            self.assertEqual((r.status_code, r.json()["detail"]["code"]), (422, "WHATSAPP_NOT_CONNECTED"))
            self.assertEqual((await c.get(f"{BASE}/readiness")).json()["whatsapp_connected"], False)
            await self.connect_whatsapp()
            no_tpl = await self.campaign(c, meta_template_id=None)
            self.assertEqual((await c.post(f"{BASE}/{no_tpl['campaign_id']}/schedule", json={})).json()["detail"]["code"], "TEMPLATE_REQUIRED")
            empty = await self.campaign(c, target_audience={"segment": "all", "city": "لا-مدينة"})
            self.assertEqual((await c.post(f"{BASE}/{empty['campaign_id']}/schedule", json={})).json()["detail"]["code"], "NO_RECIPIENTS")
            stale = await c.post(f"{BASE}/{cid}/schedule", json={"confirmed_recipients": 7})
            self.assertEqual((stale.status_code, stale.json()["detail"]["code"], stale.json()["detail"]["current_recipients"]), (409, "AUDIENCE_CHANGED", 2))
            past = await c.post(f"{BASE}/{cid}/schedule", json={"confirmed_recipients": 2, "scheduled_at": "2020-01-01T00:00:00+03:00"})
            self.assertEqual(past.json()["detail"]["code"], "SCHEDULED_AT_IN_PAST")
            ok = await c.post(f"{BASE}/{cid}/schedule", json={"confirmed_recipients": 2})                       # send now
            self.assertEqual(ok.status_code, 200, ok.text)
            self.assertEqual((ok.json()["status"], ok.json()["recipients_count"]), ("scheduled", 2))
            when = datetime.fromisoformat(ok.json()["scheduled_at"])
            self.assertTrue(timedelta(seconds=20) < when - datetime.now(timezone.utc) < timedelta(seconds=90))
            moved = await c.post(f"{BASE}/{cid}/schedule", json={"confirmed_recipients": 2, "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()})
            self.assertEqual((moved.status_code, moved.json()["status"]), (200, "scheduled"))                    # a scheduled campaign may be moved

    async def test_cancel_and_audit_trail(self):
        await self.seed_audience()
        await self.connect_whatsapp()
        async with client_for(self.app) as c:
            cid = (await self.campaign(c))["campaign_id"]
            self.assertEqual((await c.post(f"{BASE}/{cid}/schedule", json={"confirmed_recipients": 2, "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()})).status_code, 200)
            self.assertEqual((await c.post(f"{BASE}/{cid}/cancel")).json()["status"], "cancelled")
            self.assertEqual((await c.post(f"{BASE}/{cid}/cancel")).status_code, 409)
        acts = await self.audit()
        self.assertEqual([a["action"] for a in acts], ["broadcast.scheduled", "broadcast.cancelled"])
        self.assertEqual((acts[0]["details"]["recipients"], acts[0]["details"]["template"], acts[0]["details"]["confirmed"]), (2, "promo", True))

    async def test_templates_endpoint(self):
        async with client_for(self.app) as c:
            self.assertEqual((await c.get(f"{BASE}/templates")).json()["detail"]["code"], "WHATSAPP_NOT_CONNECTED")
            await self.connect_whatsapp(waba=False)
            self.assertEqual((await c.get(f"{BASE}/templates")).json()["detail"]["code"], "WABA_ID_MISSING")
            await self.connect_whatsapp()
            fake = [{"name": "promo", "language": "ar", "variables": 1, "supported": True}]
            with patch.object(meta_templates, "list_approved_templates", AsyncMock(return_value=fake)) as m:
                r = await c.get(f"{BASE}/templates")
            self.assertEqual((r.status_code, r.json()["items"]), (200, fake))
            self.assertEqual(m.await_args.args, ("999", "tok"))
            with patch.object(meta_templates, "list_approved_templates", AsyncMock(side_effect=meta_templates.TemplateFetchError("رفضت Meta"))):
                r = await c.get(f"{BASE}/templates")
            self.assertEqual((r.status_code, r.json()["detail"]["code"]), (502, "META_TEMPLATES_FAILED"))

    async def test_stats_from_deliveries_replies_and_unsubscribes(self):
        a = await self.cust(phone="+966500000001")
        b = await self.cust(phone="+966500000002")
        d = await self.cust(phone="+966500000003")
        e = await self.cust(phone="+966500000004")
        async with client_for(self.app) as c:
            camp = await self.campaign(c)
        cid = camp["campaign_id"]
        async with self.Admin() as s:                                    # e unsubscribes AFTER the campaign exists
            await s.execute(self.text("UPDATE customers SET marketing_opt_in=false, opt_out_at=now() WHERE customer_id=:c"), {"c": e})
            await s.commit()
        sent = datetime.now(timezone.utc) - timedelta(hours=5)
        async with self.Admin() as s:
            for cu, st in ((a, "READ"), (b, "DELIVERED"), (d, "FAILED"), (e, "SENT")):
                await s.execute(self.text("INSERT INTO broadcast_deliveries (delivery_id,tenant_id,campaign_id,customer_id,delivery_status,sent_at,error_detail,retry_count,created_at,updated_at) "
                                          "VALUES (:i,:t,:c,:cu,:st,:sent,:err,0,now(),now())"),
                                {"i": uuid.uuid4(), "t": self.tenants[0], "c": cid, "cu": cu, "st": st, "sent": sent if st != "FAILED" else None, "err": "131026: x" if st == "FAILED" else None})
            conv = uuid.uuid4()
            await s.execute(self.text("INSERT INTO conversations (conversation_id,tenant_id,customer_id,channel,status,message_count,ai_reply_epoch,created_at,updated_at) VALUES (:c,:t,:cu,'whatsapp','ai_active',0,0,now(),now())"),
                            {"c": conv, "t": self.tenants[0], "cu": a})
            for at, who in ((sent + timedelta(hours=1), "customer"), (sent - timedelta(hours=1), "customer"), (sent + timedelta(hours=2), "ai")):
                await s.execute(self.text("INSERT INTO messages (message_id,conversation_id,sender_type,message_type,text_content,delivery_status,created_at,updated_at) VALUES (:m,:c,:w,'text','x','SENT',:at,:at)"),
                                {"m": uuid.uuid4(), "c": conv, "w": who, "at": at})
            await s.commit()
        async with client_for(self.app) as c:
            st = (await c.get(f"{BASE}/{cid}/stats")).json()
        self.assertEqual((st["sent"], st["delivered"], st["read"], st["failed"], st["replies"], st["unsubscribes"]), (3, 2, 1, 1, 1, 1))
        self.assertEqual((st["rates"]["delivered_pct"], st["rates"]["read_pct"]), (66.7, 50.0))
        self.assertEqual(st["top_errors"], [{"error": "131026: x", "count": 1}])

    async def test_worker_sends_templates_only_to_consented_phones_and_rechecks_consent(self):
        from src.ai_workers.broadcast_worker import worker as bw

        await self.connect_whatsapp()
        first = await self.cust(phone="+966500000001", display_name="أحمد")
        second = await self.cust(phone="+966500000002", display_name="سارة")
        failing = await self.cust(phone="+966500000003")
        await self.cust(phone="+966500000010", marketing_opt_in=False)
        await self.cust(phone="1784000000001", phone_e164=None, source="instagram")
        await self.cust(tenant=self.tenants[1], phone="+966500000099")
        async with client_for(self.app) as c:
            camp = await self.campaign(c, template_params=["مرحباً {name}"], template_language="ar", send_rate_per_second=80)
        cid = uuid.UUID(camp["campaign_id"])
        async with self.Admin() as s:
            await s.execute(self.text("UPDATE broadcast_campaigns SET status='sending' WHERE campaign_id=:c"), {"c": cid})
            await s.commit()

        fake = FakeMeta()
        orig = fake.send_template_message

        async def send(**kw):
            if kw["to"].endswith("0001"):                      # `second` unsubscribes while the campaign is running
                async with self.Admin() as s:
                    await s.execute(self.text("UPDATE customers SET marketing_opt_in=false, opt_out_at=now() WHERE customer_id=:c"), {"c": second})
                    await s.commit()
            return await orig(**kw)

        fake.send_template_message = send
        creds = NS(phone_number_id="123", access_token="tok")
        record = NS(value=json.dumps({"campaign_id": str(cid), "tenant_id": str(self.tenants[0])}).encode())
        with patch.object(bw, "whatsapp_client", fake), patch.object(bw, "_resolve_tenant_credentials", AsyncMock(return_value=creds)):
            await object.__new__(bw.BroadcastWorker).process_message(record)

        tos = [kw["to"] for kw in fake.sent]
        self.assertEqual(sorted(tos), ["+966500000001", "+966500000003"])                 # not the no-consent / Instagram-only / other-tenant / unsubscribed ones
        first_call = next(kw for kw in fake.sent if kw["to"].endswith("0001"))
        self.assertEqual((first_call["template_name"], first_call["language_code"], first_call["components"]),
                         ("promo", "ar", [{"type": "body", "parameters": [{"type": "text", "text": "مرحباً أحمد"}]}]))
        async with self.Admin() as s:
            rows = {r["customer_id"]: r["delivery_status"] for r in (await s.execute(self.text("SELECT customer_id, delivery_status FROM broadcast_deliveries WHERE campaign_id=:c"), {"c": cid})).mappings()}
            camp_row = (await s.execute(self.text("SELECT status, recipients_count, last_error, started_at, completed_at FROM broadcast_campaigns WHERE campaign_id=:c"), {"c": cid})).mappings().one()
        self.assertEqual(rows, {first: "SENT", failing: "FAILED"})
        self.assertEqual((camp_row["status"], camp_row["recipients_count"]), ("completed", 2))
        self.assertIn("131026", camp_row["last_error"])
        self.assertIsNotNone(camp_row["started_at"])

    async def test_worker_fails_a_campaign_with_a_reason_when_there_is_nobody_to_message(self):
        from src.ai_workers.broadcast_worker import worker as bw

        await self.cust(phone="+966500000010", marketing_opt_in=False)
        async with client_for(self.app) as c:
            camp = await self.campaign(c)
        cid = uuid.UUID(camp["campaign_id"])
        fake = FakeMeta()
        record = NS(value=json.dumps({"campaign_id": str(cid), "tenant_id": str(self.tenants[0])}).encode())
        with patch.object(bw, "whatsapp_client", fake), patch.object(bw, "_resolve_tenant_credentials", AsyncMock(return_value=NS(phone_number_id="1", access_token="t"))):
            await object.__new__(bw.BroadcastWorker).process_message(record)
        self.assertEqual(fake.sent, [])
        async with self.Admin() as s:
            row = (await s.execute(self.text("SELECT status, last_error FROM broadcast_campaigns WHERE campaign_id=:c"), {"c": cid})).mappings().one()
        self.assertEqual(row["status"], "draft")                  # a draft is never sent (state guard)
        async with self.Admin() as s:
            await s.execute(self.text("UPDATE broadcast_campaigns SET status='sending' WHERE campaign_id=:c"), {"c": cid})
            await s.commit()
        with patch.object(bw, "whatsapp_client", fake), patch.object(bw, "_resolve_tenant_credentials", AsyncMock(return_value=NS(phone_number_id="1", access_token="t"))):
            await object.__new__(bw.BroadcastWorker).process_message(record)
        async with self.Admin() as s:
            row = (await s.execute(self.text("SELECT status, last_error FROM broadcast_campaigns WHERE campaign_id=:c"), {"c": cid})).mappings().one()
        self.assertEqual(row["status"], "failed")
        self.assertIn("مؤهلون", row["last_error"])

    async def test_meta_status_webhook_moves_deliveries_forward_only(self):
        from src.shared.db import persistence

        cu = await self.cust()
        async with client_for(self.app) as c:
            camp = await self.campaign(c)
        did = uuid.uuid4()
        async with self.Admin() as s:
            await s.execute(self.text("INSERT INTO broadcast_deliveries (delivery_id,tenant_id,campaign_id,customer_id,delivery_status,platform_message_id,retry_count,created_at,updated_at) "
                                      "VALUES (:i,:t,:c,:cu,'SENT','wamid.X',0,now(),now())"), {"i": did, "t": self.tenants[0], "c": camp["campaign_id"], "cu": cu})
            await s.commit()

        async def push(status, **kw):
            return await persistence.update_message_delivery_status_by_wamid(tenant_id=self.tenants[0], platform_message_id="wamid.X", delivery_status=status, **kw)

        async def row():
            async with self.Admin() as s:
                return (await s.execute(self.text("SELECT delivery_status, delivered_at, read_at, error_detail FROM broadcast_deliveries WHERE delivery_id=:i"), {"i": did})).mappings().one()

        with patch.object(persistence, "_publish_sse", AsyncMock()):
            self.assertTrue(await push("DELIVERED"))
            r1 = await row()
            self.assertEqual((r1["delivery_status"], r1["delivered_at"] is not None, r1["read_at"]), ("DELIVERED", True, None))
            await push("READ")
            await push("SENT")                                             # late, out-of-order: must not undo READ
            await push("FAILED", failure_reason="131026: late")           # a failure after READ is ignored too
            r2 = await row()
            self.assertEqual((r2["delivery_status"], r2["read_at"] is not None, r2["error_detail"]), ("READ", True, None))
            self.assertFalse(await persistence.update_message_delivery_status_by_wamid(tenant_id=self.tenants[0], platform_message_id="wamid.unknown", delivery_status="READ"))

    async def test_inbound_stop_message_withdraws_consent_and_is_audited(self):
        from src.shared.core.enums import Channel
        from src.shared.db import persistence

        cu = await self.cust(phone="+966500000001")
        with patch.object(persistence, "_publish_sse", AsyncMock()):
            await persistence.persist_inbound_message(
                tenant_id=self.tenants[0], customer_phone="+966500000001", customer_display_name=None, channel=Channel.WHATSAPP,
                platform_conversation_id="+966500000001", platform_message_id="wamid.in1", message_type="text", text_content="إلغاء الاشتراك")
            await persistence.persist_inbound_message(
                tenant_id=self.tenants[0], customer_phone="+966500000001", customer_display_name=None, channel=Channel.WHATSAPP,
                platform_conversation_id="+966500000001", platform_message_id="wamid.in2", message_type="text", text_content="مرحبا")
        async with self.Admin() as s:
            row = (await s.execute(self.text("SELECT marketing_opt_in, opt_out_at FROM customers WHERE customer_id=:c"), {"c": cu})).mappings().one()
        self.assertEqual((row["marketing_opt_in"], row["opt_out_at"] is not None), (False, True))
        self.assertEqual([a["action"] for a in await self.audit()], ["customer.opt_out"])


if __name__ == "__main__":
    unittest.main()

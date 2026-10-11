"""P8: notifications, team & seats, support staff console + ticket rate limit, PDPL export/anonymise.

Pure tests run everywhere; the DB class needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL (see test_customers.py).
Not covered: real Clerk invitation API, real SMTP/Resend delivery (a fake sender stands in).
"""
import json
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import patch

from src.gateway import dependencies as deps
from src.gateway.main import create_app
from src.shared.core.config import get_settings
from src.shared.core.enums import TenantUserRole
from src.shared.services import email_sender
from src.shared.services import notifications as nsvc
from tests.test_property_import_flow import auth_overrides, client_for

DB_URL = os.environ.get("OMNIFLOW_TEST_DATABASE_URL")
RLS_URL = os.environ.get("OMNIFLOW_TEST_RLS_URL")
UTC = timezone.utc


class PureTests(unittest.TestCase):
    def test_every_notification_kind_has_arabic_copy_and_conservative_email_defaults(self):
        for k, v in nsvc.KINDS.items():
            self.assertTrue(v.label and v.description, k)
        self.assertFalse(nsvc.KINDS["handoff"].email)                 # high-volume kind: in-app only unless the user opts in
        self.assertTrue(nsvc.KINDS["payment_failed"].email)

    def test_email_is_escaped_and_rtl(self):
        html, text = email_sender.render('<script>x</script>', 'a & b', link_label="فتح", link='https://x.test/?a="1"')
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn('dir="rtl"', html)
        self.assertIn("https://x.test/", text)

    def test_email_is_off_without_configuration(self):
        with patch.object(get_settings(), "email_provider", ""):
            self.assertFalse(email_sender.is_configured())


@unittest.skipUnless(DB_URL and RLS_URL, "needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL (non-superuser: RLS is the tenant guard)")
class LaunchGapsDbTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        self.text = text
        self.admin = create_async_engine(DB_URL)
        self.app_engine = create_async_engine(RLS_URL)
        self.Admin = async_sessionmaker(self.admin, expire_on_commit=False)
        self.App = async_sessionmaker(self.app_engine, expire_on_commit=False)
        self.tenants = [uuid.uuid4(), uuid.uuid4()]
        self.users: dict[tuple[int, str], uuid.UUID] = {}
        async with self.Admin() as s:
            for i, t in enumerate(self.tenants):
                await s.execute(text(
                    "INSERT INTO tenants (tenant_id,business_name,fal_license_number,subscription_tier,status,max_ai_conversations,onboarding_status,created_at,updated_at) "
                    "VALUES (:t,:n,:f,'economic','trial',100,'completed',now(),now())"), {"t": t, "n": f"شركة {i}", "f": f"FAL-{t.hex[:8]}"})
                for role in ("admin", "agent", "auditor"):
                    uid = uuid.uuid4()
                    self.users[(i, role)] = uid
                    await s.execute(text("INSERT INTO tenant_users (user_id,tenant_id,full_name,email,hashed_password,role,is_active,created_at,updated_at) "
                                         "VALUES (:u,:t,:n,:e,'x',:r,true,now(),now())"), {"u": uid, "t": t, "n": f"{role}{i}", "e": f"{role}{i}-{uid.hex[:5]}@example.sa", "r": role})
            await s.commit()
        cfg = get_settings()
        for name, value in (("billing_gate_enabled", True), ("support_tickets_per_hour", 10), ("support_staff_emails", ""), ("email_provider", ""), ("clerk_secret_key", "")):
            p = patch.object(cfg, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch("src.shared.db.session.AsyncSessionFactory", self.App)
        p.start()
        self.addCleanup(p.stop)
        self.sent: list[tuple] = []

        async def fake_send(to, subject, html, text_):
            self.sent.append((to, subject))
            return True
        email_sender.set_sender_for_tests(fake_send)
        self.addCleanup(email_sender.set_sender_for_tests, None)

    async def asyncTearDown(self):
        async with self.Admin() as s:
            for t in self.tenants:
                for stmt in ("DELETE FROM usage_counters WHERE tenant_id=:t", "DELETE FROM notification_prefs WHERE tenant_id=:t", "DELETE FROM notifications WHERE tenant_id=:t",
                             "DELETE FROM team_invitations WHERE tenant_id=:t", "DELETE FROM support_attachments WHERE tenant_id=:t",
                             "DELETE FROM support_ticket_messages WHERE tenant_id=:t", "DELETE FROM support_tickets WHERE tenant_id=:t",
                             "DELETE FROM messages WHERE conversation_id IN (SELECT conversation_id FROM conversations WHERE tenant_id=:t)",
                             "DELETE FROM broadcast_deliveries WHERE tenant_id=:t", "DELETE FROM conversations WHERE tenant_id=:t",
                             "DELETE FROM customers WHERE tenant_id=:t", "DELETE FROM audit_logs WHERE tenant_id=:t",
                             "DELETE FROM subscriptions WHERE tenant_id=:t", "DELETE FROM tenant_users WHERE tenant_id=:t", "DELETE FROM tenants WHERE tenant_id=:t"):
                    await s.execute(self.text(stmt), {"t": t})
            await s.commit()
        await self.admin.dispose()
        await self.app_engine.dispose()

    def app_as(self, role="admin", tenant_idx=0):
        app = create_app()
        t = self.tenants[tenant_idx]
        auth_overrides(app, t, self.App, role=TenantUserRole(role))
        row = NS(tenant_id=t, user_id=self.users[(tenant_idx, role)], role=TenantUserRole(role), email=f"{role}{tenant_idx}@example.sa", is_active=True)
        app.dependency_overrides[deps.get_current_user] = lambda: row
        return app

    async def rows(self, sql, **p):
        async with self.Admin() as s:
            return (await s.execute(self.text(sql), p)).mappings().all()

    async def sysdb(self):
        from src.shared.db.session import get_system_session
        return get_system_session()

    # ═══ notifications ══════════════════════════════════════════════════════════
    async def test_notify_reaches_admins_only_by_default_respects_prefs_dedupes_and_emails_on_opt_in(self):
        t = self.tenants[0]
        async with await self.sysdb() as db:
            n = await nsvc.notify(db, t, "payment_failed", "فشل", "x", "/settings#billing")
        self.assertEqual(n, 1)                                                             # the admin only
        self.assertEqual(len(self.sent), 1)                                                # payment_failed emails by default
        rows = await self.rows("SELECT user_id FROM notifications WHERE tenant_id=:t", t=t)
        self.assertEqual([r["user_id"] for r in rows], [self.users[(0, "admin")]])
        async with client_for(self.app_as("admin")) as c:
            self.assertEqual((await c.put("/api/v1/notifications/prefs", json={"payment_failed": {"in_app": False, "email": False}})).status_code, 200)
        self.sent.clear()
        async with await self.sysdb() as db:
            self.assertEqual(await nsvc.notify(db, t, "payment_failed", "فشل مرة أخرى"), 0)
        self.assertEqual(self.sent, [])                                                    # fully opted out
        async with await self.sysdb() as db:
            a = await nsvc.notify(db, t, "usage_threshold", "80%", dedupe_key="usage80:2026-10")
            b = await nsvc.notify(db, t, "usage_threshold", "80%", dedupe_key="usage80:2026-10")
            c2 = await nsvc.notify(db, t, "handoff", "محادثة", user_ids=[self.users[(0, "agent")]])
        self.assertEqual((a, b, c2), (1, 0, 1))                                            # dedupe; explicit recipient; handoff = in-app only
        self.assertEqual(len(self.sent), 1)                                                # the usage mail went once, handoff sent none
        with self.assertRaises(ValueError):
            async with await self.sysdb() as db:
                await nsvc.notify(db, t, "nope", "x")

    async def test_notifications_api_is_per_user_and_per_tenant(self):
        t = self.tenants[0]
        async with await self.sysdb() as db:
            await nsvc.notify(db, t, "handoff", "للوكيل", user_ids=[self.users[(0, "agent")]])
            await nsvc.notify(db, t, "usage_threshold", "للمدير")
        async with client_for(self.app_as("agent")) as c:
            r = (await c.get("/api/v1/notifications")).json()
            self.assertEqual([i["title"] for i in r["items"]], ["للوكيل"])
            self.assertEqual(r["unread_count"], 1)
            nid = r["items"][0]["notification_id"]
            self.assertTrue((await c.post(f"/api/v1/notifications/{nid}/read")).json()["ok"])
            self.assertEqual((await c.get("/api/v1/notifications/unread-count")).json()["unread_count"], 0)
            self.assertEqual((await c.post(f"/api/v1/notifications/{uuid.uuid4()}/read")).status_code, 404)
        async with client_for(self.app_as("admin")) as c:
            admin_items = (await c.get("/api/v1/notifications")).json()["items"]
            self.assertEqual([i["title"] for i in admin_items], ["للمدير"])
            self.assertEqual((await c.post(f"/api/v1/notifications/{nid}/read")).status_code, 404)        # not the admin's row
            self.assertEqual((await c.post("/api/v1/notifications/read-all")).json()["updated"], 1)
        async with client_for(self.app_as("admin", 1)) as c:
            self.assertEqual((await c.get("/api/v1/notifications")).json()["items"], [])

    async def test_preferences_roundtrip_and_validation(self):
        async with client_for(self.app_as("agent")) as c:
            p = (await c.get("/api/v1/notifications/prefs")).json()
            self.assertEqual({i["kind"] for i in p["items"]}, set(nsvc.KINDS))
            self.assertFalse(p["email_available"])
            self.assertEqual((await c.put("/api/v1/notifications/prefs", json={"nope": {"in_app": True, "email": True}})).status_code, 422)
            r = (await c.put("/api/v1/notifications/prefs", json={"handoff": {"in_app": False, "email": True}})).json()
            h = next(i for i in r["items"] if i["kind"] == "handoff")
            self.assertEqual((h["in_app"], h["email"]), (False, True))
            self.assertTrue(next(i for i in r["items"] if i["kind"] == "support_reply")["in_app"])        # untouched kinds keep defaults

    async def test_a_failing_notification_never_breaks_the_caller(self):
        async with await self.sysdb() as db:
            self.assertEqual(await nsvc.notify_safe(db, self.tenants[0], "bogus-kind", "x"), 0)
            n = await nsvc.notify_safe(db, self.tenants[0], "handoff", "ok")
        self.assertEqual(n, 1)

    # ═══ team ═══════════════════════════════════════════════════════════════════
    async def test_team_listing_roles_and_seat_limits(self):
        async with client_for(self.app_as("agent")) as c:
            self.assertEqual((await c.get("/api/v1/team")).status_code, 403)
        async with client_for(self.app_as("auditor")) as c:
            r = (await c.get("/api/v1/team")).json()
            self.assertEqual((len(r["members"]), r["can_edit"]), (3, False))
            self.assertEqual((await c.post("/api/v1/team/invitations", json={"email": "x@example.sa", "role": "agent"})).status_code, 403)
        async with client_for(self.app_as("admin")) as c:
            r = (await c.get("/api/v1/team")).json()
            self.assertEqual(r["seats"], {"active": 3, "pending": 0, "used": 3, "limit": 2})              # lazy trial → the trial cap (2 seats)
            full = await c.post("/api/v1/team/invitations", json={"email": "new@example.sa", "role": "agent"})
            self.assertEqual((full.status_code, full.json()["detail"]["code"]), (402, "SEAT_LIMIT"))
        async with self.Admin() as s:                                                                     # grandfathered tenants are unlimited
            await s.execute(self.text("UPDATE subscriptions SET is_exempt=true WHERE tenant_id=:t"), {"t": self.tenants[0]})
            await s.commit()
        async with client_for(self.app_as("admin")) as c:
            ok = await c.post("/api/v1/team/invitations", json={"email": " New@Example.SA ", "role": "agent"})
            self.assertEqual(ok.status_code, 201, ok.text)
            self.assertEqual((ok.json()["email"], ok.json()["delivery"]), ("new@example.sa", "email"))
            self.assertEqual(self.sent[-1][0], "new@example.sa")
            again = await c.post("/api/v1/team/invitations", json={"email": "new@example.sa", "role": "auditor"})
            self.assertEqual((again.status_code, again.json()["invitation_id"] == ok.json()["invitation_id"], again.json()["role"]), (201, True, "auditor"))
            self.assertEqual(len((await c.get("/api/v1/team")).json()["invitations"]), 1)                # re-invite refreshes, never duplicates
            member_email = (await c.get("/api/v1/team")).json()["members"][0]["email"]
            self.assertEqual((await c.post("/api/v1/team/invitations", json={"email": member_email.upper(), "role": "agent"})).json()["detail"]["code"], "ALREADY_MEMBER")
            self.assertEqual((await c.post("/api/v1/team/invitations", json={"email": "bad", "role": "agent"})).status_code, 422)
            self.assertEqual((await c.post("/api/v1/team/invitations", json={"email": "a@b.sa", "role": "owner"})).status_code, 422)
            inv_id = ok.json()["invitation_id"]
            self.assertEqual((await c.delete(f"/api/v1/team/invitations/{inv_id}")).status_code, 204)
            self.assertEqual((await c.delete(f"/api/v1/team/invitations/{inv_id}")).status_code, 404)
        audit = await self.rows("SELECT action FROM audit_logs WHERE tenant_id=:t AND action LIKE 'team.%' ORDER BY created_at", t=self.tenants[0])
        self.assertIn("team.invited", [a["action"] for a in audit])

    async def test_member_changes_protect_the_last_admin_yourself_and_other_tenants(self):
        agent, admin = self.users[(0, "agent")], self.users[(0, "admin")]
        async with client_for(self.app_as("admin")) as c:
            self.assertEqual((await c.patch(f"/api/v1/team/members/{admin}", json={"role": "agent"})).json()["detail"]["code"], "SELF_CHANGE")
            self.assertEqual((await c.patch(f"/api/v1/team/members/{admin}", json={"is_active": False})).json()["detail"]["code"], "SELF_CHANGE")
            self.assertEqual((await c.patch(f"/api/v1/team/members/{agent}", json={"role": "boss"})).status_code, 422)
            r = await c.patch(f"/api/v1/team/members/{agent}", json={"role": "auditor"})
            self.assertEqual((r.status_code, r.json()["role"]), (200, "auditor"))
            self.assertEqual((await c.patch(f"/api/v1/team/members/{agent}", json={"is_active": False})).json()["is_active"], False)
            self.assertEqual((await c.patch(f"/api/v1/team/members/{self.users[(1, 'agent')]}", json={"role": "admin"})).status_code, 404)   # other tenant
            self.assertEqual((await c.patch(f"/api/v1/team/members/{uuid.uuid4()}", json={"role": "agent"})).status_code, 404)
        # promote the agent to admin, then a (second) admin can't remove the very last admin — but can remove one of two
        async with self.Admin() as s:                                                                     # (seat caps are covered elsewhere)
            await s.execute(self.text("UPDATE subscriptions SET is_exempt=true WHERE tenant_id=:t"), {"t": self.tenants[0]})
            await s.commit()
        async with client_for(self.app_as("admin")) as c:
            await c.patch(f"/api/v1/team/members/{agent}", json={"is_active": True, "role": "admin"})
        async with self.Admin() as s:
            await s.execute(self.text("UPDATE tenant_users SET role='agent' WHERE user_id=:u"), {"u": admin})
            await s.commit()
        row = NS(tenant_id=self.tenants[0], user_id=agent, role=TenantUserRole.ADMIN, email="x@example.sa", is_active=True)
        app = self.app_as("admin")
        app.dependency_overrides[deps.get_current_user] = lambda: row
        async with self.Admin() as s:
            await s.execute(self.text("UPDATE subscriptions SET is_exempt=true WHERE tenant_id=:t"), {"t": self.tenants[0]})
            await s.commit()
        from src.shared.services import team as tsvc
        async with await self.sysdb() as db:
            with self.assertRaises(tsvc.TeamError) as ctx:
                await tsvc.change_member(db, tenant_id=self.tenants[0], actor=NS(user_id=uuid.uuid4()), user_id=agent, role="agent", is_active=None)
            self.assertEqual(ctx.exception.code, "LAST_ADMIN")

    async def test_invitation_is_accepted_only_for_a_verified_unexpired_email_and_joins_the_inviting_tenant(self):
        from src.shared.services import team as tsvc
        t = self.tenants[0]
        async with self.Admin() as s:
            await s.execute(self.text("INSERT INTO subscriptions (tenant_id, plan_code, status, is_exempt) VALUES (:t,'economic','active',true)"), {"t": t})
            await s.commit()
        async with client_for(self.app_as("admin")) as c:
            await c.post("/api/v1/team/invitations", json={"email": "colleague@example.sa", "role": "agent"})
        async with await self.sysdb() as db:
            self.assertIsNone(await tsvc.accept_for_new_user(db, clerk_id="user_1", email="colleague@example.sa", full_name="زميل", email_verified=False))
            self.assertIsNone(await tsvc.accept_for_new_user(db, clerk_id="user_1", email="stranger@example.sa", full_name="غريب", email_verified=True))
        async with self.Admin() as s:
            await s.execute(self.text("UPDATE team_invitations SET expires_at = now() - interval '1 day' WHERE tenant_id=:t"), {"t": t})
            await s.commit()
        async with await self.sysdb() as db:
            self.assertIsNone(await tsvc.accept_for_new_user(db, clerk_id="user_1", email="colleague@example.sa", full_name="زميل", email_verified=True))
        async with self.Admin() as s:
            await s.execute(self.text("UPDATE team_invitations SET expires_at = now() + interval '1 day' WHERE tenant_id=:t"), {"t": t})
            await s.commit()
        async with await self.sysdb() as db:
            u = await tsvc.accept_for_new_user(db, clerk_id="user_1", email="colleague@example.sa", full_name="زميل", email_verified=True)
            self.assertEqual((u.tenant_id, u.role, u.clerk_id, u.is_active), (t, "agent", "user_1", True))
            self.assertIsNone(await tsvc.accept_for_new_user(db, clerk_id="user_2", email="colleague@example.sa", full_name="x", email_verified=True))   # single use
        inv = (await self.rows("SELECT status FROM team_invitations WHERE tenant_id=:t", t=t))[0]
        self.assertEqual(inv["status"], "accepted")
        joined = await self.rows("SELECT 1 FROM notifications WHERE tenant_id=:t AND kind='team_joined'", t=t)
        self.assertEqual(len(joined), 1)

    async def test_clerk_webhook_routes_an_invited_user_into_the_tenant_instead_of_creating_a_workspace(self):
        from src.gateway.routers import webhooks
        t = self.tenants[0]
        async with self.Admin() as s:
            await s.execute(self.text("INSERT INTO subscriptions (tenant_id, plan_code, status, is_exempt) VALUES (:t,'economic','active',true)"), {"t": t})
            await s.commit()
        async with client_for(self.app_as("admin")) as c:
            await c.post("/api/v1/team/invitations", json={"email": "joiner@example.sa", "role": "auditor"})
        before = (await self.rows("SELECT count(*) AS n FROM tenants"))[0]["n"]
        data = {"id": "user_clerk_9", "primary_email_address_id": "e1", "first_name": "منى", "last_name": "الحربي",
                "email_addresses": [{"id": "e1", "email_address": "Joiner@Example.sa", "verification": {"status": "verified"}}]}
        clerk_id, email, name = webhooks._extract_identity(data)
        res = await webhooks._handle_user_created(clerk_id, email, name, webhooks._email_verified(data, email))
        self.assertIn("joined", res["message"])
        self.assertEqual((await self.rows("SELECT count(*) AS n FROM tenants"))[0]["n"], before)
        u = (await self.rows("SELECT tenant_id, role FROM tenant_users WHERE clerk_id='user_clerk_9'"))[0]
        self.assertEqual((u["tenant_id"], u["role"]), (t, "auditor"))
        unverified = {**data, "id": "user_clerk_10", "email_addresses": [{"id": "e1", "email_address": "nobody@example.sa", "verification": {"status": "unverified"}}]}
        self.assertFalse(webhooks._email_verified(unverified, "nobody@example.sa"))
        async with self.Admin() as s:                                                        # clean the tenant this webhook may create
            await s.execute(self.text("DELETE FROM tenant_users WHERE clerk_id='user_clerk_9'"))
            await s.commit()

    # ═══ support: ticket rate limit + staff console ══════════════════════════════
    async def test_ticket_creation_is_rate_limited_per_tenant(self):
        with patch.object(get_settings(), "support_tickets_per_hour", 2):
            async with client_for(self.app_as("admin")) as c:
                codes = [(await c.post("/api/v1/support/tickets", json={"subject": f"مشكلة {i}", "message": "نص"})).status_code for i in range(4)]
                limited = await c.post("/api/v1/support/tickets", json={"subject": "أخرى", "message": "نص"})
            self.assertEqual(codes, [201, 201, 429, 429])
            self.assertEqual((limited.json()["detail"]["code"], limited.headers["retry-after"]), ("TICKET_RATE_LIMIT", "600"))
            async with client_for(self.app_as("admin", 1)) as c:                       # another tenant is unaffected
                self.assertEqual((await c.post("/api/v1/support/tickets", json={"subject": "مشكلة", "message": "نص"})).status_code, 201)

    async def test_staff_console_is_invisible_to_everyone_else_and_replies_notify_the_ticket_owner(self):
        async with client_for(self.app_as("admin")) as c:
            t = (await c.post("/api/v1/support/tickets", json={"subject": "البوت لا يرد", "message": "منذ الصباح", "priority": "urgent"})).json()
            self.assertEqual((await c.get("/api/v1/support/staff/me")).json(), {"is_staff": False})
            for path in ("/api/v1/support/staff/tickets", f"/api/v1/support/staff/tickets/{t['ticket_id']}"):
                self.assertEqual((await c.get(path)).status_code, 404, path)
            self.assertEqual((await c.post(f"/api/v1/support/staff/tickets/{t['ticket_id']}/reply", json={"body": "x"})).status_code, 404)
        with patch.object(get_settings(), "support_staff_emails", "ADMIN1@example.sa , someone@else.sa"):
            async with client_for(self.app_as("admin", 1)) as staff:
                self.assertTrue((await staff.get("/api/v1/support/staff/me")).json()["is_staff"])
                lst = (await staff.get("/api/v1/support/staff/tickets")).json()
                self.assertEqual([i["subject"] for i in lst["items"]], ["البوت لا يرد"])                 # sees other tenants' tickets
                self.assertEqual((lst["items"][0]["tenant_name"], lst["items"][0]["awaiting_reply"]), ("شركة 0", True))
                self.assertEqual((await staff.get("/api/v1/support/staff/tickets", params={"status": "closed"})).json()["total"], 0)
                self.assertEqual((await staff.post(f"/api/v1/support/staff/tickets/{t['ticket_id']}/reply", json={"body": "   "})).status_code, 422)
                r = await staff.post(f"/api/v1/support/staff/tickets/{t['ticket_id']}/reply", json={"body": "نعمل على الحل", "status": "closed"})
                self.assertEqual(r.status_code, 201, r.text)
                d = (await staff.get(f"/api/v1/support/staff/tickets/{t['ticket_id']}")).json()
                self.assertEqual((d["ticket"]["status"], [m["author_type"] for m in d["messages"]]), ("closed", ["customer", "support", "system"]))
                self.assertEqual((await staff.get(f"/api/v1/support/staff/tickets/{uuid.uuid4()}")).status_code, 404)
        note = await self.rows("SELECT user_id, title FROM notifications WHERE tenant_id=:t AND kind='support_reply'", t=self.tenants[0])
        self.assertEqual(len(note), 1)
        self.assertEqual(note[0]["user_id"], (await self.rows("SELECT created_by_user_id AS u FROM support_tickets WHERE tenant_id=:t", t=self.tenants[0]))[0]["u"])
        self.assertTrue(any("example.sa" in to for to, _ in self.sent))                      # support_reply emails by default
        async with client_for(self.app_as("admin")) as c:                                    # the customer sees the answer in their thread
            d = (await c.get(f"/api/v1/support/tickets/{t['ticket_id']}")).json()
            self.assertEqual(d["ticket"]["status"], "closed")

    # ═══ PDPL ═══════════════════════════════════════════════════════════════════
    async def seed_customer(self, idx=0, phone="+966501112233"):
        cid, conv = uuid.uuid4(), uuid.uuid4()
        async with self.Admin() as s:
            await s.execute(self.text(
                "INSERT INTO customers (customer_id,tenant_id,unified_phone,phone_e164,display_name,email,city,notes,tags,channel_user_ids,source,marketing_opt_in,opt_in_source,opt_in_at,"
                "is_processing_restricted,is_vip,vcard_state,extracted_profile,created_at,updated_at) VALUES "
                "(:c,:t,:p,:p,'سارة العتيبي','sara@example.com','الرياض','تفضّل الشمال',CAST(:tags AS jsonb),CAST(:ids AS jsonb),'manual',true,'form',now(),false,false,'STATE_NEW',CAST(:ep AS jsonb),now(),now())"),
                {"c": cid, "t": self.tenants[idx], "p": phone, "tags": '["vip"]', "ids": '{"whatsapp": "966501112233"}', "ep": '{"budget": 900000}'})
            await s.execute(self.text("INSERT INTO conversations (conversation_id,tenant_id,customer_id,channel,status,message_count,ai_reply_epoch,created_at,updated_at) "
                                      "VALUES (:v,:t,:c,'whatsapp','ai_active',2,0,now(),now())"), {"v": conv, "t": self.tenants[idx], "c": cid})
            for i, (sender, body) in enumerate((("customer", "أبغى فيلا في حطين ميزانيتي ٩٠٠ ألف"), ("ai", "أهلاً سارة، عندي خيارات"))):
                await s.execute(self.text("INSERT INTO messages (message_id,conversation_id,sender_type,message_type,text_content,s3_media_url,created_at) "
                                          "VALUES (:m,:v,:s,'text',:b,:u,now() + (:i || ' seconds')::interval)"),
                                {"m": uuid.uuid4(), "v": conv, "s": sender, "b": body, "u": "s3://x/img.png" if i == 0 else None, "i": str(i)})
            await s.execute(self.text("INSERT INTO audit_logs (audit_id,tenant_id,actor_user_id,action,entity_type,entity_id,details,created_at) "
                                      "VALUES (gen_random_uuid(),:t,NULL,'customer.opt_in_granted','customer',:c,CAST(:d AS jsonb),now())"), {"t": self.tenants[idx], "c": str(cid), "d": '{"source": "form"}'})
            await s.commit()
        return cid, conv

    async def test_export_contains_the_customers_data_and_is_audited_and_admin_only(self):
        cid, _ = await self.seed_customer()
        async with client_for(self.app_as("agent")) as c:
            self.assertEqual((await c.get(f"/api/v1/privacy/customers/{cid}/export")).status_code, 403)
        async with client_for(self.app_as("admin", 1)) as c:
            self.assertEqual((await c.get(f"/api/v1/privacy/customers/{cid}/export")).status_code, 404)    # another tenant
        async with client_for(self.app_as("admin")) as c:
            r = await c.get(f"/api/v1/privacy/customers/{cid}/export")
            self.assertEqual((r.status_code, r.headers["content-type"]), (200, "application/json"))
            self.assertIn("attachment", r.headers["content-disposition"])
            d = json.loads(r.content)
            self.assertEqual((d["customer"]["display_name"], d["customer"]["email"], d["customer"]["tags"]), ("سارة العتيبي", "sara@example.com", ["vip"]))
            self.assertEqual([m["text"] for m in d["conversations"][0]["messages"]], ["أبغى فيلا في حطين ميزانيتي ٩٠٠ ألف", "أهلاً سارة، عندي خيارات"])
            self.assertEqual((d["consent"]["marketing_opt_in"], d["consent"]["history"][0]["action"]), (True, "customer.opt_in_granted"))
            cons = (await c.get(f"/api/v1/privacy/customers/{cid}/consents")).json()["items"]
            self.assertEqual(cons[0]["source"], "form")
            s = (await c.get("/api/v1/privacy/summary")).json()
            self.assertEqual((s["customers"], s["marketing_opt_in"], s["erased"]), (1, 1, 0))
            self.assertIn("نهائي", s["retention_note"])
        self.assertEqual(len(await self.rows("SELECT 1 FROM audit_logs WHERE tenant_id=:t AND action='privacy.export'", t=self.tenants[0])), 1)

    async def test_anonymise_wipes_identity_and_messages_keeps_the_row_and_blocks_all_contact(self):
        cid, conv = await self.seed_customer()
        async with client_for(self.app_as("agent")) as c:
            self.assertEqual((await c.post(f"/api/v1/privacy/customers/{cid}/anonymize", json={"confirm": str(cid)})).status_code, 403)
        async with client_for(self.app_as("admin")) as c:
            bad = await c.post(f"/api/v1/privacy/customers/{cid}/anonymize", json={"confirm": "نعم"})
            self.assertEqual((bad.status_code, bad.json()["detail"]["code"]), (422, "CONFIRMATION_MISMATCH"))
            self.assertEqual((await c.post(f"/api/v1/privacy/customers/{uuid.uuid4()}/anonymize", json={"confirm": "x"})).status_code, 422)
            ok = await c.post(f"/api/v1/privacy/customers/{cid}/anonymize", json={"confirm": str(cid), "reason": "طلب العميل"})
            self.assertEqual((ok.status_code, ok.json()), (200, {"messages_wiped": 2, "conversations": 1, "files_deleted": 0}))
            again = await c.post(f"/api/v1/privacy/customers/{cid}/anonymize", json={"confirm": str(cid)})
            self.assertEqual((again.status_code, again.json()["detail"]["code"]), (409, "ALREADY_ERASED"))
            exp = json.loads((await c.get(f"/api/v1/privacy/customers/{cid}/export")).content)
            self.assertNotIn("سارة", json.dumps(exp, ensure_ascii=False))
            self.assertNotIn("501112233", json.dumps(exp))
        row = (await self.rows("SELECT * FROM customers WHERE customer_id=:c", c=cid))[0]
        self.assertEqual((row["display_name"], row["email"], row["city"], row["notes"], row["phone_e164"], row["extracted_profile"]), ("عميل محذوف", None, None, None, None, None))
        self.assertEqual((row["tags"], row["channel_user_ids"], row["marketing_opt_in"], row["is_processing_restricted"]), ([], {}, False, True))
        self.assertTrue(row["erased_at"] and row["opt_out_at"] and row["archived_at"])
        self.assertNotIn("501112233", row["unified_phone"])
        msgs = await self.rows("SELECT text_content, s3_media_url FROM messages WHERE conversation_id=:v", v=conv)
        self.assertEqual({(m["text_content"], m["s3_media_url"]) for m in msgs}, {(None, None)})
        self.assertEqual((await self.rows("SELECT status FROM conversations WHERE conversation_id=:v", v=conv))[0]["status"], "closed")
        audit = await self.rows("SELECT details FROM audit_logs WHERE tenant_id=:t AND action='privacy.anonymize'", t=self.tenants[0])
        self.assertEqual(len(audit), 1)
        self.assertNotIn("سارة", json.dumps(audit[0]["details"], ensure_ascii=False))
        # the phone number is free again: the same person can be added as a brand-new customer later
        again_id, _ = await self.seed_customer()
        self.assertNotEqual(again_id, cid)

    async def test_data_subject_rights_still_work_when_the_subscription_is_suspended(self):
        cid, _ = await self.seed_customer()
        async with self.Admin() as s:
            await s.execute(self.text("INSERT INTO subscriptions (tenant_id, plan_code, status, is_exempt) VALUES (:t,'economic','suspended',false)"), {"t": self.tenants[0]})
            await s.commit()
        async with client_for(self.app_as("admin")) as c:
            self.assertEqual((await c.delete(f"/api/v1/customers/{uuid.uuid4()}")).status_code, 402)            # ordinary writes are blocked …
            self.assertEqual((await c.get(f"/api/v1/privacy/customers/{cid}/export")).status_code, 200)         # … the law's rights are not
            self.assertEqual((await c.post(f"/api/v1/privacy/customers/{cid}/anonymize", json={"confirm": str(cid)})).status_code, 200)

    # ═══ billing → notifications ═════════════════════════════════════════════════
    async def test_billing_events_notify_admins_once(self):
        from src.shared.services.billing import service
        t = self.tenants[0]
        async with await self.sysdb() as db:
            sub = await service.ensure_subscription(db, t)
            sub.trial_ends_at = datetime.now(UTC) + timedelta(days=2)
        async with await self.sysdb() as db:
            now = datetime.now(UTC)
            first = await service.run_sweep(db, service.default_urls(), now=now)
            second = await service.run_sweep(db, service.default_urls(), now=now + timedelta(hours=1))
        self.assertEqual((first.get("trial_warned"), second.get("trial_warned")), (1, None))                       # warned once
        async with await self.sysdb() as db:
            await service.run_sweep(db, service.default_urls(), now=datetime.now(UTC) + timedelta(days=5))         # trial over → suspended
        kinds = [r["kind"] for r in await self.rows("SELECT kind FROM notifications WHERE tenant_id=:t ORDER BY created_at", t=t)]
        self.assertEqual(kinds, ["trial_ending", "subscription_suspended"])
        self.assertTrue(all(r["user_id"] == self.users[(0, "admin")] for r in await self.rows("SELECT user_id FROM notifications WHERE tenant_id=:t", t=t)))

    async def test_usage_flush_task_persists_counters_and_notifies_at_80_percent(self):
        from unittest.mock import AsyncMock

        from src.celery_app import tasks
        from src.shared.services.billing import service, usage
        t = self.tenants[0]
        async with await self.sysdb() as db:
            await service.ensure_subscription(db, t)
        async with self.Admin() as s:                                    # paid Starter plan → 1,500 AI replies / month
            await s.execute(self.text("UPDATE subscriptions SET plan_code='starter', status='active', current_period_end=now()+interval '20 days' WHERE tenant_id=:t"), {"t": t})
            await s.commit()
        key = f"usage:{t}:{usage.period_of()}"
        from src.shared.redis_client import redis_mgr
        with patch.object(redis_mgr, "start", AsyncMock()) as start, patch.object(redis_mgr, "stop", AsyncMock()) as stop, \
                patch.object(redis_mgr, "scan_counters", AsyncMock(return_value={key: 1300})):
            out = await tasks._billing_usage_flush_async()
        self.assertEqual((out, start.await_count, stop.await_count), ({"alerts": 1}, 1, 1))   # pools are opened and closed per task
        self.assertEqual((await self.rows("SELECT ai_messages FROM usage_counters WHERE tenant_id=:t", t=t))[0]["ai_messages"], 1300)
        kinds = [r["kind"] for r in await self.rows("SELECT kind FROM notifications WHERE tenant_id=:t", t=t)]
        self.assertEqual(kinds, ["usage_threshold"])


if __name__ == "__main__":
    unittest.main()

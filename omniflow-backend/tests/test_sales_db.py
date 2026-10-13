"""Sales closing on PostgreSQL + RLS: listing matcher tiers, deal pipeline (one open deal per customer, close/reopen rules, board, report), follow-up scheduling and
tenant isolation. Needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL (non-superuser: RLS is the tenant guard). Nothing here talks to any external service."""
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.sales import followup, matcher, pipeline
from src.shared.core.config import get_settings
from src.shared.core.enums import ListingStatus
from src.shared.db.models import Customer, Deal, FollowupTask

DB_URL = os.environ.get("OMNIFLOW_TEST_DATABASE_URL")
RLS_URL = os.environ.get("OMNIFLOW_TEST_RLS_URL")


@unittest.skipUnless(DB_URL and RLS_URL, "needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL")
class SalesDbTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        self.text = text
        self.admin = create_async_engine(DB_URL)
        self.app_engine = create_async_engine(RLS_URL)
        self.Admin = async_sessionmaker(self.admin, expire_on_commit=False)
        self.App = async_sessionmaker(self.app_engine, expire_on_commit=False)
        p = patch("src.shared.db.session.AsyncSessionFactory", self.App)
        p.start()
        self.addCleanup(p.stop)
        self.tenants = [uuid.uuid4(), uuid.uuid4()]
        self.customers: dict[uuid.UUID, list[uuid.UUID]] = {}
        self.convs: dict[uuid.UUID, list[uuid.UUID]] = {}
        async with self.Admin() as s:
            for i, t in enumerate(self.tenants):
                await s.execute(text("INSERT INTO tenants (tenant_id,business_name,fal_license_number,subscription_tier,status,max_ai_conversations,onboarding_status,created_at,updated_at) "
                                     "VALUES (:t,:n,:f,'starter','active',100,'completed',now(),now())"), {"t": t, "n": f"مكتب {i}", "f": f"FAL-{t.hex[:8]}"})
                self.customers[t], self.convs[t] = [], []
                for k in range(2):
                    c, cv = uuid.uuid4(), uuid.uuid4()
                    await s.execute(text("INSERT INTO customers (customer_id,tenant_id,unified_phone,is_processing_restricted,is_vip,vcard_state,created_at,updated_at) "
                                         "VALUES (:c,:t,:p,false,false,'new',now(),now())"), {"c": c, "t": t, "p": f"+9665{uuid.uuid4().int % 10**8:08d}"})
                    await s.execute(text("INSERT INTO conversations (conversation_id,tenant_id,customer_id,channel,status,message_count,created_at,updated_at) "
                                         "VALUES (:v,:t,:c,'whatsapp','active_ai',0,now(),now())"), {"v": cv, "t": t, "c": c})
                    self.customers[t].append(c)
                    self.convs[t].append(cv)
            await s.commit()
        self.addAsyncCleanup(self._cleanup)

    async def _cleanup(self):
        async with self.Admin() as s:
            for t in self.tenants:
                for table in ("followup_tasks", "deals", "property_listings", "conversations", "customers", "audit_logs", "tenants"):
                    await s.execute(self.text(f"DELETE FROM {table} WHERE tenant_id=:t"), {"t": t})
            await s.commit()
        await self.admin.dispose()
        await self.app_engine.dispose()

    async def _listing(self, tenant, *, district, price, rooms=3, ptype="apartment", status=ListingStatus.VERIFIED_ACTIVE.value):
        async with self.Admin() as s:
            await s.execute(self.text("INSERT INTO property_listings (listing_id,tenant_id,rega_ad_number,property_type,status,is_verified,district,city,price,bedrooms,created_at,updated_at) "
                                      "VALUES (:i,:t,:r,:pt,:st,true,:d,'الرياض',:p,:b,now(),now())"),
                            {"i": uuid.uuid4(), "t": tenant, "r": f"R{uuid.uuid4().hex[:10]}", "pt": ptype, "st": status, "d": district, "p": price, "b": rooms})
            await s.commit()

    def _tenant_session(self, tenant):
        from src.shared.db.session import get_tenant_session
        return get_tenant_session(tenant)

    # ── matcher ───────────────────────────────────────────────────────────
    async def test_matcher_tiers_and_only_verified_listings_of_this_tenant(self):
        a, b = self.tenants
        await self._listing(a, district="النرجس", price=900_000, rooms=3)
        await self._listing(a, district="النرجس", price=700_000, rooms=2)
        await self._listing(a, district="الياسمين", price=800_000, rooms=3)
        await self._listing(a, district="النرجس", price=500_000, rooms=3, status="DRAFT")                       # not verified/active: never offered
        await self._listing(b, district="النرجس", price=100_000, rooms=3)                                       # other tenant: never offered
        async with self._tenant_session(a) as s:
            r = await matcher.match(s, a, {"property_type": "apartment", "district": "النرجس", "rooms": 3, "budget_max": 1_000_000})
            self.assertEqual((r.tier, [c["price"] for c in r.listings]), (1, [900000.0]))
            r = await matcher.match(s, a, {"property_type": "apartment", "district": "النرجس", "rooms": 4, "budget_max": 1_000_000})
            self.assertEqual(r.tier, 2)                                                                          # same district, other room count
            self.assertTrue(r.note)
            r = await matcher.match(s, a, {"property_type": "apartment", "district": "الملقا", "budget_max": 1_000_000})
            self.assertEqual(r.tier, 3)                                                                          # other districts within budget
            r = await matcher.match(s, a, {"property_type": "apartment", "budget_max": 650_000})
            self.assertEqual(r.tier, 4)                                                                          # slightly over budget (<= 15 %), flagged
            self.assertTrue(all(c["price"] <= 650_000 * 1.15 for c in r.listings))
            r = await matcher.match(s, a, {"property_type": "villa", "budget_max": 1_000_000})
            self.assertEqual(r.listings, [])
            r = await matcher.match(s, a, {})
            self.assertEqual(r.listings, [])                                                                     # nothing said -> nothing dumped
        async with self._tenant_session(b) as s:
            r = await matcher.match(s, b, {"property_type": "apartment", "district": "النرجس"})
            self.assertEqual([c["price"] for c in r.listings], [100000.0])

    # ── pipeline ──────────────────────────────────────────────────────────
    async def test_one_open_deal_per_customer_and_forward_only(self):
        t = self.tenants[0]
        c, cv = self.customers[t][0], self.convs[t][0]
        async with self._tenant_session(t) as s:
            d1 = await pipeline.ensure_deal(s, t, c, conversation_id=cv, channel="whatsapp", label="warm", score=40, qualified=True)
            d2 = await pipeline.ensure_deal(s, t, c, conversation_id=cv, channel="whatsapp", label="hot", score=70)
            self.assertEqual(d1.deal_id, d2.deal_id)
            self.assertEqual((d2.stage, d2.lead_label, d2.score), ("qualified", "hot", 70))
            self.assertTrue(await pipeline.advance(s, d2, "viewing"))
            self.assertFalse(await pipeline.advance(s, d2, "qualified"))                                          # never backwards
            self.assertFalse(await pipeline.advance(s, d2, "won"))                                                # the assistant never closes a deal
            await pipeline.record_offer(s, d2, 750_000)
            self.assertEqual((d2.stage, float(d2.offer_sar)), ("negotiation", 750000.0))

    async def test_closing_rules_lost_reason_reopen_guard_and_followups_cancelled(self):
        t = self.tenants[0]
        c, cv = self.customers[t][0], self.convs[t][0]
        async with self._tenant_session(t) as s:
            d = await pipeline.ensure_deal(s, t, c, conversation_id=cv, channel="instagram", qualified=True)
            await followup_row(s, t, c, cv)
            with self.assertRaises(pipeline.PipelineError):
                await pipeline.set_stage(s, t, d.deal_id, "lost")                                                 # a reason is mandatory
            lost = await pipeline.set_stage(s, t, d.deal_id, "lost", lost_reason="price", lost_note="x" * 400)
            self.assertEqual((lost.stage, lost.lost_reason, len(lost.lost_note)), ("lost", "price", 300))
            self.assertIsNotNone(lost.closed_at)
            pending = await s.scalar(self.select_count(FollowupTask, t, "pending"))
            self.assertEqual(pending, 0)                                                                          # closing stops the follow-ups
            d_new = await pipeline.ensure_deal(s, t, c, conversation_id=cv, channel="instagram")                  # a NEW open deal is allowed after the loss
            self.assertNotEqual(d_new.deal_id, d.deal_id)
            with self.assertRaises(pipeline.PipelineError):
                await pipeline.set_stage(s, t, d.deal_id, "qualified")                                            # re-opening would give two open deals
            won = await pipeline.set_stage(s, t, d_new.deal_id, "won", value_sar=900_000)
            self.assertEqual((won.stage, float(won.value_sar), won.lost_reason), ("won", 900000.0, None))
            with self.assertRaises(pipeline.PipelineError):
                await pipeline.set_stage(s, t, d_new.deal_id, "nonsense")

    def select_count(self, model, tenant, status):
        from sqlalchemy import func, select
        return select(func.count()).select_from(model).where(model.tenant_id == tenant, model.status == status)

    async def test_board_and_conversion_report_count_reached_stages(self):
        t = self.tenants[0]
        c1, c2 = self.customers[t]
        cv1, cv2 = self.convs[t]
        async with self._tenant_session(t) as s:
            d1 = await pipeline.ensure_deal(s, t, c1, conversation_id=cv1, channel="whatsapp", qualified=True)
            await pipeline.advance(s, d1, "viewing")
            await pipeline.advance(s, d1, "negotiation")
            await pipeline.set_stage(s, t, d1.deal_id, "lost", lost_reason="competitor")
            d2 = await pipeline.ensure_deal(s, t, c2, conversation_id=cv2, channel="whatsapp", qualified=True)
            await pipeline.set_stage(s, t, d2.deal_id, "won")
            await s.flush()
            board = await pipeline.board(s, t)
            self.assertEqual({k: v["total"] for k, v in board["columns"].items()}, {"new": 0, "qualified": 0, "viewing": 0, "negotiation": 0, "won": 1, "lost": 1})
            rep = await pipeline.conversion_report(s, t, since=datetime.now(timezone.utc) - timedelta(days=1))
            wa = rep["by_channel"]["whatsapp"]
            self.assertEqual((wa["deals"], wa["qualified"], wa["viewing"], wa["negotiation"], wa["won"], wa["lost"]), (2, 2, 2, 2, 1, 1))
            self.assertEqual(wa["lost_reasons"], {"competitor": 1})                                               # lost at negotiation still counts as having viewed
            self.assertEqual(rep["total"]["win_rate"], 0.5)

    async def test_deals_are_invisible_and_untouchable_across_tenants(self):
        a, b = self.tenants
        async with self._tenant_session(a) as s:
            d = await pipeline.ensure_deal(s, a, self.customers[a][0], conversation_id=self.convs[a][0], channel="whatsapp", qualified=True)
            deal_id = d.deal_id
        async with self._tenant_session(b) as s:
            board = await pipeline.board(s, b)
            self.assertTrue(all(col["total"] == 0 for col in board["columns"].values()))
            with self.assertRaises(LookupError):
                await pipeline.set_stage(s, b, deal_id, "won")
            from sqlalchemy import select
            self.assertIsNone(await s.scalar(select(Deal).where(Deal.deal_id == deal_id)))                         # RLS hides it even without a tenant filter

    # ── follow-ups ────────────────────────────────────────────────────────
    async def test_followup_scheduling_guards_and_rearm(self):
        t = self.tenants[0]
        c, cv = self.customers[t][0], self.convs[t][0]
        prefs = {"enabled": True, "followup_enabled": True, "followup_delays_hours": [2, 20, 72]}
        cfg = get_settings()
        async with self._tenant_session(t) as s:
            cust = await s.get(Customer, c)
            with patch.object(cfg, "feature_sales_closing", False):
                self.assertEqual(await followup.schedule(s, t, cust, cv, "whatsapp", prefs), 0)                   # platform flag off
            with patch.object(cfg, "feature_sales_closing", True):
                self.assertEqual(await followup.schedule(s, t, cust, cv, "whatsapp", {**prefs, "followup_enabled": False}), 0)
                self.assertEqual(await followup.schedule(s, t, cust, cv, "whatsapp", prefs), 3)
                self.assertEqual(await followup.schedule(s, t, cust, cv, "whatsapp", prefs), 3)                    # re-arm: same rows, not 6
                await s.flush()
                self.assertEqual(await s.scalar(self.select_count(FollowupTask, t, "pending")), 3)
                self.assertEqual((cust.qualification or {}).get("followup_cycles"), 1)                            # a re-arm inside a cycle is not a new cycle
                cust.opt_out_at = datetime.now(timezone.utc)
                self.assertEqual(await followup.schedule(s, t, cust, cv, "whatsapp", prefs), 0)                    # opted-out customers are never scheduled
                await pipeline.cancel_followups(s, t, conversation_id=cv, reason="customer_replied")
                self.assertEqual(await s.scalar(self.select_count(FollowupTask, t, "pending")), 0)


async def followup_row(session, tenant, customer, conversation):
    session.add(FollowupTask(tenant_id=tenant, customer_id=customer, conversation_id=conversation, channel="whatsapp", step=1,
                             due_at=datetime.now(timezone.utc) + timedelta(hours=2), status="pending"))
    await session.flush()


if __name__ == "__main__":
    unittest.main()

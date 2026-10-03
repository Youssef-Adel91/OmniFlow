"""Dashboard analytics: pure helpers, endpoint contract, and (opt-in) real-PostgreSQL SQL.

The SQL class runs only when OMNIFLOW_TEST_DATABASE_URL points at a *throwaway*
migrated database (`alembic upgrade head` first), e.g.
    OMNIFLOW_TEST_DATABASE_URL=postgresql+asyncpg://omniflow:x@127.0.0.1:55432/omniflow_test
It is skipped in CI (no Postgres there); the SQL was verified against PostgreSQL 16.
"""
import os
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import httpx

from src.gateway import dependencies as deps
from src.gateway.main import create_app
from src.gateway.routers import dashboard as dash
from src.shared.services import dashboard_analytics as da

DB_URL = os.environ.get("OMNIFLOW_TEST_DATABASE_URL")
# Optional: URL of a NON-superuser role. When set, every analytics query runs as
# that role with app.current_tenant_id set -- i.e. under the real RLS policies.
RLS_URL = os.environ.get("OMNIFLOW_TEST_RLS_URL")


class WindowTests(unittest.TestCase):
    def test_presets_end_today_in_riyadh_and_previous_is_adjacent(self):
        # 22:30 UTC on 1 Sep is already 2 Sep in Riyadh (UTC+3).
        now = datetime(2026, 9, 1, 22, 30, tzinfo=timezone.utc)
        w = da.resolve_window("7d", None, None, now=now)
        self.assertEqual((w.first_day, w.last_day, w.days), (date(2026, 8, 27), date(2026, 9, 2), 7))
        self.assertEqual(w.start, datetime(2026, 8, 26, 21, 0, tzinfo=timezone.utc))
        self.assertEqual(w.end, datetime(2026, 9, 2, 21, 0, tzinfo=timezone.utc))
        prev = w.previous()
        self.assertEqual((prev.first_day, prev.last_day, prev.days), (date(2026, 8, 20), date(2026, 8, 26), 7))
        self.assertEqual(prev.end, w.start)

    def test_custom_validation(self):
        with self.assertRaises(da.AnalyticsRangeError):
            da.resolve_window("custom", None, date(2026, 9, 1))
        with self.assertRaises(da.AnalyticsRangeError):
            da.resolve_window("custom", date(2026, 9, 5), date(2026, 9, 1))
        with self.assertRaises(da.AnalyticsRangeError):
            da.resolve_window("custom", date(2024, 1, 1), date(2026, 1, 1))
        with self.assertRaises(da.AnalyticsRangeError):
            da.resolve_window("1y", None, None)
        self.assertEqual(da.resolve_window("custom", date(2026, 9, 1), date(2026, 9, 1)).days, 1)

    def test_change_pct(self):
        self.assertEqual(da._change_pct(4, 1), 300.0)
        self.assertEqual(da._change_pct(1, 4), -75.0)
        self.assertIsNone(da._change_pct(5, 0))      # nothing to compare to
        self.assertIsNone(da._change_pct(None, 3))
        self.assertEqual(da._change_pct(0, 4), -100.0)

    def test_series_is_gap_filled_and_split_by_channel(self):
        w = da.Window(date(2026, 9, 1), date(2026, 9, 3))
        rows = [("c", date(2026, 9, 1), "whatsapp", 2), ("m", date(2026, 9, 1), "whatsapp", 5),
                ("c", date(2026, 9, 3), "instagram", 1), ("m", date(2026, 8, 1), "web", 9)]
        s = da._fill_series(w, rows)
        self.assertEqual([p["date"] for p in s], ["2026-09-01", "2026-09-02", "2026-09-03"])
        self.assertEqual((s[0]["conversations"], s[0]["messages"]), (2, 5))
        self.assertEqual((s[1]["conversations"], s[1]["messages"]), (0, 0))
        self.assertEqual(s[2]["channels"], {"instagram": {"conversations": 1, "messages": 0}})


class EndpointTests(unittest.IsolatedAsyncioTestCase):
    def _app(self):
        app = create_app()
        self.tenant_id = uuid.uuid4()
        app.dependency_overrides[deps.get_current_user] = lambda: NS(tenant_id=self.tenant_id)
        app.dependency_overrides[deps.get_authenticated_tenant_session] = lambda: object()
        return app

    async def _get(self, app, url):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            return await c.get(url)

    async def test_invalid_input_is_422(self):
        app = self._app()
        for url in ("/api/v1/dashboard/analytics?range=custom",
                    "/api/v1/dashboard/analytics?range=custom&from=2026-09-05&to=2026-09-01",
                    "/api/v1/dashboard/analytics?range=1y",
                    "/api/v1/dashboard/analytics?channel=carrier-pigeon"):
            self.assertEqual((await self._get(app, url)).status_code, 422, url)

    async def test_served_then_cached_and_cache_failure_is_tolerated(self):
        store = {}

        async def get_raw(k):
            return store.get(k)

        async def set_raw(k, v, ttl=None):
            store[k] = v

        build = AsyncMock(return_value={"kpis": {}, "marker": 1})
        with patch.object(dash, "build_analytics", build), \
             patch.object(dash.redis_mgr, "get_raw", get_raw), patch.object(dash.redis_mgr, "set_raw", set_raw):
            app = self._app()
            first = await self._get(app, "/api/v1/dashboard/analytics?range=30d&channel=Instagram")
            second = await self._get(app, "/api/v1/dashboard/analytics?range=30d&channel=instagram")
        self.assertEqual((first.status_code, first.json()["marker"]), (200, 1))
        self.assertEqual(second.json()["marker"], 1)
        self.assertEqual(build.await_count, 1)  # second hit served from cache
        self.assertEqual(build.await_args.args[3], "instagram")

        async def boom(*a, **k):
            raise RuntimeError("redis down")

        with patch.object(dash, "build_analytics", build), \
             patch.object(dash.redis_mgr, "get_raw", boom), patch.object(dash.redis_mgr, "set_raw", boom):
            r = await self._get(self._app(), "/api/v1/dashboard/analytics")
        self.assertEqual(r.status_code, 200)

    async def test_tenant_comes_from_auth_not_query(self):
        build = AsyncMock(return_value={})
        with patch.object(dash, "build_analytics", build), \
             patch.object(dash.redis_mgr, "get_raw", AsyncMock(return_value=None)), \
             patch.object(dash.redis_mgr, "set_raw", AsyncMock()):
            app = self._app()
            await self._get(app, f"/api/v1/dashboard/analytics?tenant_id={uuid.uuid4()}")
        self.assertEqual(build.await_args.args[1], self.tenant_id)


@unittest.skipUnless(DB_URL, "set OMNIFLOW_TEST_DATABASE_URL to a migrated throwaway PostgreSQL")
class AnalyticsSqlTests(unittest.IsolatedAsyncioTestCase):
    """Every query against a real PostgreSQL with hand-computed expectations."""

    async def asyncSetUp(self):
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        self.engine = create_async_engine(DB_URL)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.tenants = [uuid.uuid4(), uuid.uuid4()]
        self.window = da.Window(date(2026, 9, 1), date(2026, 9, 7))
        self.text = text
        async with self.Session() as s:
            await self._seed(s)
            await s.commit()

    async def asyncTearDown(self):
        async with self.Session() as s:
            for t in self.tenants:
                for stmt in (
                    "DELETE FROM appointments WHERE tenant_id=:t",
                    "DELETE FROM customer_reports WHERE tenant_id=:t",
                    "DELETE FROM messages WHERE conversation_id IN (SELECT conversation_id FROM conversations WHERE tenant_id=:t)",
                    "DELETE FROM conversations WHERE tenant_id=:t",
                    "DELETE FROM property_listings WHERE tenant_id=:t",
                    "DELETE FROM customers WHERE tenant_id=:t",
                    "DELETE FROM tenants WHERE tenant_id=:t",
                ):
                    await s.execute(self.text(stmt), {"t": t})
            await s.commit()
        await self.engine.dispose()

    @staticmethod
    def _utc(day, hh, mm=0, ss=0):
        return datetime(2026, 9, day, hh, mm, ss, tzinfo=timezone.utc)

    async def _seed(self, s):
        x = lambda sql, **p: s.execute(self.text(sql), p)  # noqa: E731
        A, B = self.tenants
        for t, name in ((A, "A"), (B, "B")):
            await x("INSERT INTO tenants (tenant_id,business_name,fal_license_number,subscription_tier,status,max_ai_conversations,onboarding_status,created_at,updated_at) "
                    "VALUES (:t,:n,:f,'economic','active',100,'completed',now(),now())", t=t, n=f"Tenant {name}", f=f"FAL-{t.hex[:8]}")

        async def customer(t, phone, score, vip, created, profile=None, name=None):
            cid = uuid.uuid4()
            await x("INSERT INTO customers (customer_id,tenant_id,unified_phone,display_name,is_vip,is_processing_restricted,vcard_state,engagement_score,extracted_profile,created_at,updated_at) "
                    "VALUES (:c,:t,:p,:n,:v,false,'none',:sc,CAST(:pr AS jsonb),CAST(:cr AS timestamptz),CAST(:cr AS timestamptz))",
                    c=cid, t=t, p=phone, n=name, v=vip, sc=score, pr=__import__("json").dumps(profile) if profile else None, cr=created)
            return cid

        async def conv(t, cust, channel, status, created, updated=None):
            vid = uuid.uuid4()
            await x("INSERT INTO conversations (conversation_id,tenant_id,customer_id,channel,status,ai_reply_epoch,message_count,created_at,updated_at,last_message_at) "
                    "VALUES (:v,:t,:c,:ch,:st,0,0,CAST(:cr AS timestamptz),CAST(:up AS timestamptz),CAST(:cr AS timestamptz))", v=vid, t=t, c=cust, ch=channel, st=status, cr=created, up=updated or created)
            return vid

        async def msg(v, sender, at, body="x"):
            await x("INSERT INTO messages (message_id,conversation_id,sender_type,message_type,text_content,created_at,updated_at) "
                    "VALUES (:m,:v,:s,'text',:b,CAST(:a AS timestamptz),CAST(:a AS timestamptz))", m=uuid.uuid4(), v=v, s=sender, b=body, a=at)

        u = self._utc
        c1 = await customer(A, "+966500000001", 80, False, u(1, 9), {"location": "الملقا"}, "علي")
        c2 = await customer(A, "17841400000002", 10, False, u(2, 10), {"location": "حي النرجس"})
        c3 = await customer(A, "+966500000003", None, True, u(3, 11))
        prev_at = datetime(2026, 8, 28, 9, tzinfo=timezone.utc)   # inside the previous window (25-31 Aug)
        cprev = await customer(A, "+966500000009", 5, False, prev_at)
        v1 = await conv(A, c1, "whatsapp", "ai_active", u(1, 9))
        await msg(v1, "customer", u(1, 9, 0, 0), "كم السعر؟")
        await msg(v1, "ai_bot", u(1, 9, 0, 10))
        await msg(v1, "customer", u(1, 9, 1, 0), "ابغى معاينة")
        v2 = await conv(A, c2, "instagram", "escalated", u(2, 10))
        await msg(v2, "customer", u(2, 10, 0, 0), "فين الموقع")
        await msg(v2, "ai_bot", u(2, 10, 0, 20))
        await msg(v2, "human_agent", u(2, 10, 5, 0))
        v3 = await conv(A, c3, "whatsapp", "closed", u(3, 11), updated=u(3, 12))
        await msg(v3, "customer", u(3, 11, 0, 0))
        await msg(v3, "ai_bot", u(3, 11, 0, 30))
        v4 = await conv(A, c2, "instagram", "human_active", u(4, 20, 30))
        await msg(v4, "customer", u(4, 20, 30, 0))
        p1 = await conv(A, cprev, "whatsapp", "ai_active", prev_at)
        pc = prev_at
        await msg(p1, "customer", pc)
        await msg(p1, "ai_bot", pc + timedelta(seconds=40))
        # Tenant B: must never leak into A.
        cb = await customer(B, "+966511111111", 99, True, u(2, 9))
        vb = await conv(B, cb, "whatsapp", "escalated", u(2, 9))
        await msg(vb, "customer", u(2, 9))

        await x("INSERT INTO appointments (appointment_id,tenant_id,conversation_id,customer_id,scheduled_at,status,created_at,updated_at) VALUES (:i,:t,:v,:c,:at,'scheduled',CAST(:cr AS timestamptz),CAST(:cr AS timestamptz))",
                i=uuid.uuid4(), t=A, v=v1, c=c1, at=u(10, 9), cr=u(2, 9))
        await x("INSERT INTO appointments (appointment_id,tenant_id,conversation_id,customer_id,scheduled_at,status,created_at,updated_at) VALUES (:i,:t,:v,:c,:at,'cancelled',CAST(:cr AS timestamptz),CAST(:cr AS timestamptz))",
                i=uuid.uuid4(), t=A, v=v2, c=c2, at=u(10, 9), cr=u(2, 9))
        for price, delivered, ref in ((29.0, True, "REF_29"), (15.0, False, "REF-15")):
            await x("INSERT INTO customer_reports (report_id,tenant_id,customer_id,report_type,price_sar,is_delivered,payment_reference,created_at,updated_at) VALUES (:i,:t,:c,'deed_check_29',:p,:d,:ref,CAST(:cr AS timestamptz),CAST(:cr AS timestamptz))",
                    i=uuid.uuid4(), t=A, c=c1, p=price, d=delivered, ref=ref, cr=u(3, 9))
        await x("INSERT INTO property_listings (listing_id,tenant_id,rega_ad_number,property_type,status,is_verified,city,district,created_at,updated_at) VALUES (:i,:t,'AD1','apartment','VERIFIED_ACTIVE',true,'الرياض','الملقا',now(),now())",
                i=uuid.uuid4(), t=A)
        await x("INSERT INTO property_listings (listing_id,tenant_id,rega_ad_number,property_type,status,is_verified,city,district,created_at,updated_at) VALUES (:i,:t,'AD2','villa','SOLD',true,'جدة',NULL,now(),now())",
                i=uuid.uuid4(), t=A)

    async def _run(self, tenant, channel=None):
        if RLS_URL:
            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

            eng = create_async_engine(RLS_URL)
            try:
                async with async_sessionmaker(eng, expire_on_commit=False)() as s, s.begin():
                    await s.execute(self.text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(tenant)})
                    return await da.build_analytics(s, tenant, self.window, channel)
            finally:
                await eng.dispose()
        async with self.Session() as s:
            return await da.build_analytics(s, tenant, self.window, channel)

    async def test_kpis_match_hand_computed_values(self):
        k = (await self._run(self.tenants[0]))["kpis"]
        self.assertEqual(k["new_conversations"], {"value": 4, "previous": 1, "change_pct": 300.0})
        self.assertEqual(k["new_customers"]["value"], 3)
        self.assertEqual(k["hot_leads"]["value"], 2)             # c1 (score 80) + c3 (VIP); c2 not hot
        self.assertEqual(k["handoff_rate"]["value"], 50.0)       # v2 (human msg) + v4 (human_active) of 4
        self.assertEqual(k["ai_resolution_rate"]["value"], 50.0)
        self.assertEqual(k["ai_resolution_rate"]["previous"], 100.0)
        self.assertEqual(k["avg_first_response_ai_seconds"]["value"], 20.0)      # (10+20+30)/3
        self.assertEqual(k["avg_first_response_ai_seconds"]["previous"], 40.0)
        self.assertEqual(k["avg_first_response_human_seconds"]["value"], 300.0)
        self.assertEqual(k["avg_resolution_seconds"]["value"], 3600.0)
        self.assertEqual(k["appointments_booked"]["value"], 1)   # the cancelled one is excluded
        self.assertEqual((k["reports_revenue_sar"]["value"], k["reports_sold"]["value"]), (29.0, 1))

    async def test_tenant_isolation(self):
        a = await self._run(self.tenants[0])
        b = await self._run(self.tenants[1])
        self.assertEqual(a["kpis"]["new_conversations"]["value"], 4)
        self.assertEqual(b["kpis"]["new_conversations"]["value"], 1)
        self.assertEqual(b["kpis"]["new_customers"]["value"], 1)
        self.assertEqual(b["kpis"]["appointments_booked"]["value"], 0)
        self.assertEqual({r["conversation_id"] for r in a["attention"]["awaiting_human"]} & {r["conversation_id"] for r in b["attention"]["awaiting_human"]}, set())

    async def test_channel_filter(self):
        d = await self._run(self.tenants[0], "instagram")
        self.assertEqual(d["kpis"]["new_conversations"]["value"], 2)
        self.assertEqual(d["channel_distribution"], [{"key": "instagram", "count": 2}])
        self.assertEqual(d["kpis"]["new_customers"]["value"], 1)  # only c2 has an instagram conversation

    async def test_series_funnel_heatmap_distributions(self):
        d = await self._run(self.tenants[0])
        self.assertEqual(len(d["series"]), 7)
        day1 = d["series"][0]
        self.assertEqual((day1["date"], day1["conversations"], day1["messages"]), ("2026-09-01", 1, 3))
        self.assertEqual(d["series"][4]["conversations"], 0)
        self.assertEqual(sum(p["conversations"] for p in d["series"]), 4)
        self.assertEqual([f["count"] for f in d["funnel"]], [3, 2, 1, 1, 1])
        # v1's two customer messages: 09:00Z/09:01Z == 12:00/12:01 Riyadh on Tue 1 Sep 2026.
        dow = (date(2026, 9, 1).weekday() + 1) % 7
        self.assertEqual(d["heatmap"]["matrix"][dow][12], 2)
        self.assertEqual(sum(map(sum, d["heatmap"]["matrix"])), 5)   # 5 customer messages in window
        self.assertEqual({r["key"]: r["count"] for r in d["channel_distribution"]}, {"whatsapp": 2, "instagram": 2})
        self.assertEqual({r["key"]: r["count"] for r in d["status_distribution"]},
                         {"ai_active": 1, "escalated": 1, "closed": 1, "human_active": 1})
        self.assertEqual({r["key"]: r["count"] for r in d["lead_score_distribution"]},
                         {"0-25": 2, "26-50": 0, "51-75": 0, "76-100": 1})   # c3 (NULL score) counts as 0

    async def test_properties_gaps_topics_attention(self):
        d = await self._run(self.tenants[0])
        self.assertEqual({r["key"]: r["count"] for r in d["properties"]["by_status"]}, {"VERIFIED_ACTIVE": 1, "SOLD": 1})
        self.assertEqual({r["key"] for r in d["properties"]["by_city"]}, {"الرياض", "جدة"})
        self.assertIsNone(d["properties"]["top_recommended"])
        gaps = {g["location"]: g for g in d["inventory_gaps"]}
        self.assertEqual(gaps["الملقا"]["matching_listings"], 1)
        self.assertEqual(gaps["حي النرجس"]["matching_listings"], 0)
        topics = {t["topic"]: t["count"] for t in d["top_topics"]}
        self.assertEqual((topics["السعر والميزانية"], topics["المعاينة والموعد"], topics["الموقع والحي"]), (1, 1, 1))
        waiting = d["attention"]["awaiting_human"]
        self.assertEqual(len(waiting), 1)                      # v2's last message is the agent's -> not waiting
        self.assertEqual(waiting[0]["status"], "human_active")
        self.assertEqual(len(d["attention"]["sla_breached"]), 1)
        self.assertEqual({h["phone"] for h in d["attention"]["hot_leads"]}, {"+966500000001", "+966500000003"})

    # ── /reports: search + revenue by type (real SQL) ────────────────────────
    async def _reports_app(self, tenant):
        from src.gateway.routers import reports as reports_router

        app = create_app()
        engine_url = RLS_URL or DB_URL
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        eng = create_async_engine(engine_url)
        self.addAsyncCleanup(eng.dispose)

        async def session_dep():
            async with async_sessionmaker(eng, expire_on_commit=False)() as s, s.begin():
                await s.execute(self.text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(tenant)})
                yield s

        app.dependency_overrides[deps.get_current_user] = lambda: NS(tenant_id=tenant)
        app.dependency_overrides[deps.get_authenticated_tenant_session] = session_dep
        self.assertIsNotNone(reports_router)
        return app

    async def _get(self, app, url):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            return await c.get(url)

    # The /reports endpoints rely on RLS alone (no explicit tenant_id predicate), so
    # these two need a NON-superuser role: set OMNIFLOW_TEST_RLS_URL.
    @unittest.skipUnless(RLS_URL, "needs OMNIFLOW_TEST_RLS_URL (non-superuser role; RLS is bypassed by superusers)")
    async def test_reports_search_matches_reference_name_phone_and_escapes_wildcards(self):
        app = await self._reports_app(self.tenants[0])

        async def count(q):
            r = await self._get(app, f"/api/v1/reports?search={q}")
            self.assertEqual(r.status_code, 200, r.text)
            return r.json()["total"]

        self.assertEqual(await count("REF_29"), 1)
        self.assertEqual(await count("REF_"), 1)          # "_" is literal: REF-15 must NOT match
        self.assertEqual(await count("REF-15"), 1)
        self.assertEqual(await count("%25"), 0)           # a lone "%" is literal, not "match all"
        self.assertEqual(await count("0001"), 2)          # customer phone +966500000001
        self.assertEqual(await count("zzz"), 0)
        self.assertEqual(await count(""), 2)              # blank search == no filter
        other = await self._reports_app(self.tenants[1])  # tenant isolation
        self.assertEqual((await self._get(other, "/api/v1/reports?search=REF")).json()["total"], 0)

    @unittest.skipUnless(RLS_URL, "needs OMNIFLOW_TEST_RLS_URL (non-superuser role; RLS is bypassed by superusers)")
    async def test_revenue_by_type_over_the_chart_window(self):
        B = self.tenants[1]
        async with self.Session() as s:
            cust = await s.scalar(self.text("SELECT customer_id FROM customers WHERE tenant_id=:t"), {"t": B})
            for rtype, price, delivered in (("deed_check_29", 29.0, True), ("municipal_consulting_15", 15.0, False)):
                await s.execute(self.text(
                    "INSERT INTO customer_reports (report_id,tenant_id,customer_id,report_type,price_sar,is_delivered,created_at,updated_at) "
                    "VALUES (:i,:t,:c,:rt,:p,:d,now(),now())"),
                    {"i": uuid.uuid4(), "t": B, "c": cust, "rt": rtype, "p": price, "d": delivered})
            await s.commit()
        app = await self._reports_app(B)
        body = (await self._get(app, "/api/v1/reports/analytics/revenue?period=monthly")).json()
        self.assertEqual(body["total_revenue"], 44.0)
        self.assertEqual([(t["report_type"], t["total_revenue"], t["count"]) for t in body["by_type"]],
                         [("deed_check_29", 29.0, 1), ("municipal_consulting_15", 15.0, 1)])
        delivered = (await self._get(app, "/api/v1/reports/analytics/revenue?period=daily&delivered_only=true")).json()
        self.assertEqual([t["report_type"] for t in delivered["by_type"]], ["deed_check_29"])
        self.assertEqual(delivered["total_revenue"], 29.0)


if __name__ == "__main__":
    unittest.main()

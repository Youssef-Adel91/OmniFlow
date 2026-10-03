"""Properties list filters/sort/search, CSV export and bulk delete/status (real PostgreSQL, opt-in like the import flow)."""
import os
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from src.gateway import dependencies as deps
from src.gateway.main import create_app
from src.gateway.routers import properties as props
from tests.test_property_import_flow import auth_overrides, client_for

DB_URL = os.environ.get("OMNIFLOW_TEST_DATABASE_URL")
RLS_URL = os.environ.get("OMNIFLOW_TEST_RLS_URL")


@unittest.skipUnless(DB_URL and RLS_URL, "needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL (non-superuser: RLS is the tenant guard here)")
class PropertyListBulkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        self.text = text
        self.admin = create_async_engine(DB_URL)
        self.app_engine = create_async_engine(RLS_URL)
        self.Admin = async_sessionmaker(self.admin, expire_on_commit=False)
        self.App = async_sessionmaker(self.app_engine, expire_on_commit=False)
        self.A, self.B = uuid.uuid4(), uuid.uuid4()
        self.ids = {}
        async with self.Admin() as s:
            for t in (self.A, self.B):
                await s.execute(text("INSERT INTO tenants (tenant_id,business_name,fal_license_number,subscription_tier,status,max_ai_conversations,onboarding_status,created_at,updated_at) "
                                     "VALUES (:t,'T',:f,'economic','active',100,'completed',now(),now())"), {"t": t, "f": f"F{t.hex[:8]}"})
            rows = [  # key, tenant, rega, type, status, city, district, price, area, desc
                ("a", self.A, "A1", "apartment", "VERIFIED_ACTIVE", "الرياض", "النرجس", 900000, 150, "شقة هادئة"),
                ("b", self.A, "A2", "villa", "SOLD", "جدة", "الشاطئ", 2500000, 400, "فيلا بحرية"),
                ("c", self.A, "A_3", "apartment", "PENDING_VERIFICATION", "الرياض", "الملقا", 600000, 90, None),
                ("d", self.A, "A%4", "land", "VERIFIED_ACTIVE", "الدمام", None, None, 800, "=cmd|calc"),
                ("x", self.B, "B1", "apartment", "VERIFIED_ACTIVE", "الرياض", "النرجس", 1, 1, "other tenant"),
            ]
            for k, t, rega, ty, st, city, dist, price, area, desc in rows:
                self.ids[k] = uuid.uuid4()
                await s.execute(text("INSERT INTO property_listings (listing_id,tenant_id,rega_ad_number,property_type,status,is_verified,city,district,price,area_sqm,description_ar,created_at,updated_at) "
                                     "VALUES (:i,:t,:r,:ty,:st,false,:c,:d,:p,:a,:de,now() + (:o || ' seconds')::interval,now())"),
                                {"i": self.ids[k], "t": t, "r": rega, "ty": ty, "st": st, "c": city, "d": dist, "p": price, "a": area, "de": desc, "o": str(len(self.ids))})
            await s.commit()
        self.app = create_app()
        auth_overrides(self.app, self.A, self.App)
        self.app.dependency_overrides[deps.get_property_listing_repo] = self._repo_dep

        async def sess():
            async with self.App() as s, s.begin():
                await s.execute(text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(self.A)})
                yield s
        self._sess = sess

    async def _repo_dep(self):
        from src.shared.db.repository import PropertyListingRepository

        async for s in self._sess():
            yield PropertyListingRepository(s)

    async def asyncTearDown(self):
        async with self.Admin() as s:
            for t in (self.A, self.B):
                await s.execute(self.text("DELETE FROM import_jobs WHERE tenant_id=:t"), {"t": t})
                await s.execute(self.text("DELETE FROM audit_logs WHERE tenant_id=:t"), {"t": t})
                await s.execute(self.text("DELETE FROM property_listings WHERE tenant_id=:t"), {"t": t})
                await s.execute(self.text("DELETE FROM tenants WHERE tenant_id=:t"), {"t": t})
            await s.commit()
        await self.admin.dispose()
        await self.app_engine.dispose()

    async def regas(self, c, **params):
        r = await c.get("/api/v1/properties", params=params)
        self.assertEqual(r.status_code, 200, r.text)
        return [i["rega_ad_number"] for i in r.json()["items"]], r.json()

    async def test_filters_search_sort_and_tenant_scope(self):
        async with client_for(self.app) as c:
            all_, body = await self.regas(c)
            self.assertEqual((sorted(all_), body["total"]), (["A%4", "A1", "A2", "A_3"], 4))   # tenant B's row never appears
            self.assertEqual(all_[0], "A%4")                                                    # newest first
            self.assertEqual((await self.regas(c, sort="oldest"))[0][0], "A1")
            self.assertEqual((await self.regas(c, sort="price_desc"))[0][:2], ["A2", "A1"])
            self.assertEqual((await self.regas(c, sort="price_asc"))[0][-1], "A%4")             # NULL prices last
            self.assertEqual(sorted((await self.regas(c, city="رياض"))[0]), ["A1", "A_3"])
            self.assertEqual(sorted((await self.regas(c, status="VERIFIED_ACTIVE"))[0]), ["A%4", "A1"])
            self.assertEqual(sorted((await self.regas(c, property_type="apartment", price_min=700000))[0]), ["A1"])
            self.assertEqual((await self.regas(c, price_max=700000))[0], ["A_3"])
            self.assertEqual((await self.regas(c, search="النرجس"))[0], ["A1"])
            self.assertEqual((await self.regas(c, search="بحرية"))[0], ["A2"])
            self.assertEqual((await self.regas(c, search="A_"))[0], ["A_3"])         # "_" is literal, not a wildcard
            self.assertEqual((await self.regas(c, search="A%"))[0], ["A%4"])         # "%" is literal
            self.assertEqual((await self.regas(c, search="nomatch"))[1]["items"], [])
            page2, body = await self.regas(c, limit=3, page=2)
            self.assertEqual((len(page2), body["pages"]), (1, 2))
            self.assertEqual((await c.get("/api/v1/properties", params={"sort": "evil"})).status_code, 422)

    async def test_export_csv_respects_filters_and_defuses_formulas(self):
        async with client_for(self.app) as c:
            r = await c.get("/api/v1/properties/export.csv", params={"status": "VERIFIED_ACTIVE"})
        self.assertEqual(r.status_code, 200, r.text)
        text = r.content.decode("utf-8-sig")
        self.assertIn("رقم الإعلان", text.splitlines()[0])
        self.assertEqual(len(text.strip().splitlines()), 3)                      # header + 2 rows
        self.assertIn(",'=cmd|calc,", text)
        self.assertNotIn(",=cmd|calc", text)
        self.assertNotIn("B1", text)

    async def test_bulk_status_and_delete_only_touch_own_tenant(self):
        reindex, qdrant_delete, start = AsyncMock(), AsyncMock(), AsyncMock()
        with patch.object(props, "delete_listings_from_qdrant", qdrant_delete), \
             patch("src.shared.services.property_import.reindex", reindex), \
             patch("src.shared.services.property_import.start_reindex", start):
            async with client_for(self.app) as c:
                ids = [str(self.ids["a"]), str(self.ids["c"]), str(self.ids["x"]), str(uuid.uuid4())]
                r = await c.post("/api/v1/properties/bulk/status", json={"ids": ids, "status": "SUSPENDED"})
                body = r.json()
                self.assertEqual((r.status_code, body["requested"], body["updated"], body["not_found"]), (200, 4, 2, 2))
                self.assertTrue(body["reindex_job_id"])
                self.assertEqual(sorted((await self.regas(c, status="SUSPENDED"))[0]), ["A1", "A_3"])
                start.assert_awaited_once()
                job = (await c.get(f"/api/v1/properties/import/{body['reindex_job_id']}")).json()
                self.assertEqual((job["kind"], job["total_rows"], job["status"]), ("reindex", 2, "queued"))
                self.assertNotIn("ids", job["options"])
                r = await c.post("/api/v1/properties/bulk/delete", json={"ids": ids})
                self.assertEqual(r.json(), {"requested": 4, "deleted": 2, "not_found": 2})
                self.assertEqual(sorted(qdrant_delete.await_args.args[0]), sorted([str(self.ids["a"]), str(self.ids["c"])]))
                self.assertEqual(sorted((await self.regas(c))[0]), ["A%4", "A2"])
                bad = await c.post("/api/v1/properties/bulk/delete", json={"ids": []})
                self.assertEqual(bad.status_code, 422)
                bad = await c.post("/api/v1/properties/bulk/status", json={"ids": ids[:1], "status": "NOPE"})
                self.assertEqual(bad.status_code, 422)
        async with self.Admin() as s:     # tenant B's listing survived both calls
            n = await s.scalar(self.text("SELECT count(*) FROM property_listings WHERE tenant_id=:t"), {"t": self.B})
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()

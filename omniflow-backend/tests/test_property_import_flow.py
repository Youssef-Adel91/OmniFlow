"""Property import end to end (HTTP -> service -> real PostgreSQL), plus HTTP guards that need no DB.

The DB class needs a migrated throwaway PostgreSQL:
    OMNIFLOW_TEST_DATABASE_URL=postgresql+asyncpg://<superuser>@host/db      (seeding / inspection)
    OMNIFLOW_TEST_RLS_URL=postgresql+asyncpg://<non-superuser>@host/db       (the app role: real RLS)
Skipped in CI (no Postgres there). Object storage and the Qdrant sync are faked.
"""
import io
import os
import unittest
import uuid
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import httpx

from src.gateway import dependencies as deps
from src.gateway.main import create_app
from src.shared.core.config import get_settings
from src.shared.services import property_import as svc

DB_URL = os.environ.get("OMNIFLOW_TEST_DATABASE_URL")
RLS_URL = os.environ.get("OMNIFLOW_TEST_RLS_URL")
BASE = "/api/v1/properties/import"


class FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    async def put(self, key, data, content_type):
        self.objects[key] = data

    async def get(self, key):
        if key not in self.objects:
            raise KeyError(key)
        return self.objects[key]

    async def delete(self, key):
        self.objects.pop(key, None)


CSV_HEADER = "نوع العقار,المدينة,الحي,السعر,المساحة,رقم الإعلان\n"


def make_csv(*rows: str) -> bytes:
    return (CSV_HEADER + "\n".join(rows) + "\n").encode("utf-8-sig")


def auth_overrides(app, tenant, session_factory=None):
    user = NS(tenant_id=tenant, user_id=uuid.uuid4())
    app.dependency_overrides[deps.get_current_user] = lambda: user
    app.dependency_overrides[deps.require_property_write_role] = lambda: user
    if session_factory:
        from sqlalchemy import text

        async def session_dep():
            async with session_factory() as s, s.begin():
                await s.execute(text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(tenant)})
                yield s

        app.dependency_overrides[deps.get_authenticated_tenant_session] = session_dep


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=60)


class HttpGuardTests(unittest.IsolatedAsyncioTestCase):
    """No database: size cap, bad type, template route not shadowed by /{listing_id}."""

    async def test_template_download_is_an_xlsx_and_not_shadowed(self):
        app = create_app()
        auth_overrides(app, uuid.uuid4())
        async with client_for(app) as c:
            r = await c.get(f"{BASE}/template")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.content.startswith(b"PK"))
        self.assertIn("spreadsheetml", r.headers["content-type"])

    async def test_oversized_upload_is_413_before_any_parsing(self):
        app = create_app()
        auth_overrides(app, uuid.uuid4())
        app.dependency_overrides[deps.get_authenticated_tenant_session] = lambda: AsyncMock()
        with patch.object(get_settings(), "import_max_file_mb", 1):
            async with client_for(app) as c:
                r = await c.post(f"{BASE}/upload", files={"file": ("big.csv", b"a,b\n" + b"1,2\n" * 400_000)})
        self.assertEqual(r.status_code, 413)
        self.assertEqual(r.json()["detail"]["code"], "file_too_large")

    async def test_wrong_content_is_422_with_a_code(self):
        app = create_app()
        auth_overrides(app, uuid.uuid4())
        sess = AsyncMock()
        app.dependency_overrides[deps.get_authenticated_tenant_session] = lambda: sess
        async with client_for(app) as c:
            r = await c.post(f"{BASE}/upload", files={"file": ("sheet.csv", b"%PDF-1.4 not a csv")})
        self.assertEqual((r.status_code, r.json()["detail"]["code"]), (422, "extension_mismatch"))


@unittest.skipUnless(DB_URL, "set OMNIFLOW_TEST_DATABASE_URL (+ OMNIFLOW_TEST_RLS_URL) to a migrated throwaway PostgreSQL")
class ImportFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        self.text = text
        self.admin = create_async_engine(DB_URL)
        self.AdminSession = async_sessionmaker(self.admin, expire_on_commit=False)
        self.app_engine = create_async_engine(RLS_URL or DB_URL)
        self.AppSession = async_sessionmaker(self.app_engine, expire_on_commit=False)
        self.tenants = [uuid.uuid4(), uuid.uuid4()]
        async with self.AdminSession() as s:
            for i, t in enumerate(self.tenants):
                await s.execute(text(
                    "INSERT INTO tenants (tenant_id,business_name,fal_license_number,subscription_tier,status,max_ai_conversations,onboarding_status,created_at,updated_at) "
                    "VALUES (:t,:n,:f,'economic','active',100,'completed',now(),now())"), {"t": t, "n": f"T{i}", "f": f"FAL-{t.hex[:8]}"})
            await s.commit()

        self.storage = FakeStorage()
        self.sync = AsyncMock(side_effect=lambda listings, tenant: [str(x.listing_id) for x in listings])
        for p in (patch.object(svc, "storage", self.storage),
                  patch.object(svc.vector_sync, "sync_listings_batch", self.sync),
                  patch("src.shared.db.session.AsyncSessionFactory", self.AppSession),
                  patch.object(get_settings(), "import_batch_size", 500)):
            p.start()
            self.addCleanup(p.stop)
        self.app = create_app()
        auth_overrides(self.app, self.tenants[0], self.AppSession)

    async def asyncTearDown(self):
        async with self.AdminSession() as s:
            for t in self.tenants:
                for stmt in ("DELETE FROM import_jobs WHERE tenant_id=:t", "DELETE FROM import_mapping_templates WHERE tenant_id=:t",
                             "DELETE FROM property_listings WHERE tenant_id=:t", "DELETE FROM tenants WHERE tenant_id=:t"):
                    await s.execute(self.text(stmt), {"t": t})
            await s.commit()
        await self.admin.dispose()
        await self.app_engine.dispose()

    # helpers ---------------------------------------------------------------------------------
    async def upload(self, c, data, name="props.csv"):
        r = await c.post(f"{BASE}/upload", files={"file": (name, data)})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    @staticmethod
    def mapping_of(up):
        return {s["field"]: s["header"] for s in up["suggested_mapping"] if s["header"]}

    async def run_import(self, c, data, options=None, name="props.csv"):
        up = await self.upload(c, data, name)
        v = await c.post(f"{BASE}/{up['import_id']}/validate", json={"mapping": self.mapping_of(up), "options": options})
        self.assertEqual(v.status_code, 200, v.text)
        r = await c.post(f"{BASE}/{up['import_id']}/commit")
        self.assertEqual(r.status_code, 202, r.text)
        await self.wait(c, up["import_id"])
        return up["import_id"], v.json()

    async def job(self, c, import_id):
        return (await c.get(f"{BASE}/{import_id}")).json()

    async def wait(self, c, import_id, timeout=60):
        """The import runs detached from the request: poll its status like the UI does."""
        import asyncio

        for _ in range(int(timeout / 0.1)):
            job = await self.job(c, import_id)
            if job["status"] in ("completed", "failed", "cancelled"):
                return job
            await asyncio.sleep(0.1)
        self.fail(f"import stuck: {job}")

    async def commit(self, c, import_id):
        r = await c.post(f"{BASE}/{import_id}/commit")
        self.assertEqual(r.status_code, 202, r.text)
        return await self.wait(c, import_id)

    async def listings(self, tenant=None):
        async with self.AdminSession() as s:
            rows = await s.execute(self.text(
                "SELECT rega_ad_number, property_type, price, city, status, qdrant_point_id FROM property_listings WHERE tenant_id=:t ORDER BY rega_ad_number"),
                {"t": tenant or self.tenants[0]})
            return [dict(r._mapping) for r in rows]

    # tests -------------------------------------------------------------------------------------
    async def test_full_flow_validates_imports_reports_and_cleans_up(self):
        data = make_csv("شقة,الرياض,النرجس,850000,180,A100", "فيلا,جدة,الشاطئ,1.2 مليون,400,", "سيارة,الرياض,,5,5,A102",
                        "أرض,الدمام,,٣٠٠ ألف,٦٠٠,A100", "مكتب,الخبر,,90000,60,A104")
        async with client_for(self.app) as c:
            up = await self.upload(c, data)
            self.assertEqual((up["file_kind"], up["total_rows"], up["extracted_by_llm"]), ("csv", 5, False))
            m = self.mapping_of(up)
            self.assertEqual((m["property_type"], m["rega_ad_number"], m["price"]), ("نوع العقار", "رقم الإعلان", "السعر"))
            v = (await c.post(f"{BASE}/{up['import_id']}/validate", json={"mapping": m})).json()
            counts = v["counts"]
            self.assertEqual((counts["total"], counts["valid"], counts["errors"], counts["duplicate_in_file"], counts["will_create"]), (5, 3, 2, 1, 3))
            self.assertEqual({p["row"]: p["outcome"] for p in v["preview"]}, {2: "create", 3: "create", 4: "error", 5: "error", 6: "create"})
            self.assertEqual(await self.listings(), [])                      # dry run wrote nothing
            self.assertEqual(self.sync.await_count, 0)

            job = await self.commit(c, up["import_id"])
            self.assertEqual((job["status"], job["created"], job["failed"], job["skipped"], job["percent"], job["indexed"]), ("completed", 3, 2, 0, 100, 3))

            rows = await self.listings()
            self.assertEqual(len(rows), 3)
            a100 = next(x for x in rows if x["rega_ad_number"] == "A100")
            self.assertEqual((a100["property_type"], float(a100["price"]), a100["city"], a100["status"]), ("apartment", 850000.0, "الرياض", "PENDING_VERIFICATION"))
            self.assertEqual(sorted(float(x["price"]) for x in rows), [90000.0, 850000.0, 1_200_000.0])
            dev = next(x for x in rows if x["rega_ad_number"].startswith("DEV-IMP-"))
            self.assertEqual(dev["property_type"], "villa")
            self.assertTrue(all(x["qdrant_point_id"] for x in rows))

            rep = await c.get(f"{BASE}/{up['import_id']}/errors.csv")
            self.assertEqual(rep.status_code, 200)
            text = rep.content.decode("utf-8-sig")
            self.assertIn("رقم الصف", text)
            self.assertIn("«سيارة» غير معروف", text)
            self.assertIn("مكرر داخل الملف", text)
            self.assertEqual(text.count("\n") - 1, 2)

        # PII hygiene: the uploaded sheet and the normalised rows are gone, only the error report remains.
        self.assertEqual([k.rsplit("/", 1)[-1] for k in self.storage.objects], ["errors.csv"])

    async def test_batches_checkpoint_and_failed_job_resumes_without_duplicates(self):
        rows = [f"شقة,الرياض,,{100000 + i},100,R{i:03d}" for i in range(5)]
        calls = {"n": 0}
        real = svc._apply_batch

        async def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("boom in batch 2")
            return await real(*a, **k)

        with patch.object(get_settings(), "import_batch_size", 2), patch.object(svc, "_apply_batch", flaky):
            async with client_for(self.app) as c:
                import_id, _ = await self.run_import(c, make_csv(*rows))
                job = await self.job(c, import_id)
                self.assertEqual((job["status"], job["processed_rows"], job["created"]), ("failed", 2, 2))
                self.assertIn("boom", job["error_message"])
                self.assertEqual(len(await self.listings()), 2)               # batch 1 is durable
                job = await self.commit(c, import_id)                         # retry resumes at row 2
        self.assertEqual((job["status"], job["processed_rows"], job["created"], job["percent"]), ("completed", 5, 5, 100))
        self.assertEqual(sorted(x["rega_ad_number"] for x in await self.listings()), [f"R{i:03d}" for i in range(5)])
        self.assertEqual(self.sync.await_count, 3)                            # 2 + 1(retry) ... batches of 2,[fail],2,1

    async def test_duplicate_modes_skip_update_create_new(self):
        async with self.AdminSession() as s:
            await s.execute(self.text(
                "INSERT INTO property_listings (listing_id,tenant_id,rega_ad_number,property_type,status,is_verified,city,price,created_at,updated_at) "
                "VALUES (:i,:t,'R1','apartment','VERIFIED_ACTIVE',true,'قديمة',100,now(),now())"), {"i": uuid.uuid4(), "t": self.tenants[0]})
            await s.commit()
        data = make_csv("فيلا,جدة,,999,300,R1", "شقة,الرياض,,500,90,")

        async with client_for(self.app) as c:
            import_id, v = await self.run_import(c, data, {"on_duplicate": "skip"})
            self.assertEqual((v["counts"]["existing_in_db"], v["counts"]["will_skip"], v["counts"]["will_create"]), (1, 1, 1))
            job = await self.job(c, import_id)
            self.assertEqual((job["created"], job["skipped"], job["updated"]), (1, 1, 0))
            r1 = next(x for x in await self.listings() if x["rega_ad_number"] == "R1")
            self.assertEqual((float(r1["price"]), r1["city"]), (100.0, "قديمة"))
            self.assertIn("موجود مسبقًا", (await c.get(f"{BASE}/{import_id}/errors.csv")).content.decode("utf-8-sig"))

            import_id, v = await self.run_import(c, data, {"on_duplicate": "update"})
            self.assertEqual(v["counts"]["will_update"], 1)
            job = await self.job(c, import_id)
            self.assertEqual((job["updated"], job["created"]), (1, 1))        # the blank-rega row is a NEW DEV-IMP-<job2> listing
            r1 = next(x for x in await self.listings() if x["rega_ad_number"] == "R1")
            self.assertEqual((float(r1["price"]), r1["city"], r1["property_type"], r1["status"]), (999.0, "جدة", "villa", "VERIFIED_ACTIVE"))

            before = len(await self.listings())
            import_id, _ = await self.run_import(c, data, {"on_duplicate": "create_new"})
            job = await self.job(c, import_id)
            self.assertEqual((job["created"], job["skipped"]), (2, 0))
            self.assertEqual(len(await self.listings()), before + 2)
        self.assertEqual(len({x["rega_ad_number"] for x in await self.listings()}), before + 2)

    async def test_cancel_stops_before_the_next_batch_and_cleans_up(self):
        rows = [f"شقة,الرياض,,{1000 + i},100,C{i:02d}" for i in range(6)]
        job_id = {}

        async def cancel_after_first_batch(listings, tenant):
            async with self.AdminSession() as s:
                await s.execute(self.text("UPDATE import_jobs SET cancel_requested=true WHERE import_id=:i"), {"i": job_id["id"]})
                await s.commit()
            return [str(x.listing_id) for x in listings]

        self.sync.side_effect = cancel_after_first_batch
        with patch.object(get_settings(), "import_batch_size", 2):
            async with client_for(self.app) as c:
                up = await self.upload(c, make_csv(*rows))
                job_id["id"] = up["import_id"]
                await c.post(f"{BASE}/{up['import_id']}/validate", json={"mapping": self.mapping_of(up)})
                job = await self.commit(c, up["import_id"])
        self.assertEqual((job["status"], job["created"], job["processed_rows"]), ("cancelled", 2, 2))
        self.assertEqual(len(await self.listings()), 2)
        self.assertEqual(self.storage.objects, {})

    async def test_only_one_import_runs_at_a_time_and_commit_needs_validation(self):
        async with client_for(self.app) as c:
            up = await self.upload(c, make_csv("شقة,الرياض,,1,1,Z1"))
            r = await c.post(f"{BASE}/{up['import_id']}/commit")
            self.assertEqual((r.status_code, r.json()["detail"]["code"]), (422, "INVALID_IMPORT"))
            await c.post(f"{BASE}/{up['import_id']}/validate", json={"mapping": self.mapping_of(up)})
            async with self.AdminSession() as s:       # another job of the same tenant is mid-run
                await s.execute(self.text(
                    "INSERT INTO import_jobs (import_id,tenant_id,kind,status,filename,file_kind,file_size,total_rows,processed_rows,created_count,updated_count,skipped_count,failed_count,indexed_count,cancel_requested,lease_expires_at,created_at,updated_at) "
                    "VALUES (:i,:t,'properties','running','other.csv','csv',1,10,0,0,0,0,0,0,false,now()+interval '1 hour',now(),now())"),
                    {"i": uuid.uuid4(), "t": self.tenants[0]})
                await s.commit()
            r = await c.post(f"{BASE}/{up['import_id']}/commit")
            self.assertEqual((r.status_code, r.json()["detail"]["code"]), (409, "IMPORT_IN_PROGRESS"))

    async def test_stale_running_job_is_adopted_on_startup(self):
        rows = [f"شقة,الرياض,,{i},1,S{i}" for i in range(3)]
        async with client_for(self.app) as c:
            up = await self.upload(c, make_csv(*rows))
            await c.post(f"{BASE}/{up['import_id']}/validate", json={"mapping": self.mapping_of(up)})
            async with self.AdminSession() as s:       # a worker died mid-run: running + expired lease
                await s.execute(self.text("UPDATE import_jobs SET status='running', lease_expires_at=now()-interval '1 minute' WHERE import_id=:i"), {"i": up["import_id"]})
                await s.commit()
            with patch("src.shared.services.property_import.get_system_session", self._system_session):
                self.assertEqual(await svc.resume_stale_jobs(), 1)
            import asyncio

            for _ in range(100):
                if (await self.job(c, up["import_id"]))["status"] == "completed":
                    break
                await asyncio.sleep(0.1)
            job = await self.job(c, up["import_id"])
        self.assertEqual((job["status"], job["created"]), ("completed", 3))

    def _system_session(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def cm():
            async with self.AppSession() as s, s.begin():
                await s.execute(self.text("SELECT set_config('app.current_tenant_id', '00000000-0000-0000-0000-000000000000', true)"))
                yield s
        return cm()

    async def test_qdrant_outage_does_not_fail_the_import(self):
        self.sync.side_effect = None
        self.sync.return_value = []                  # sync_listings_batch swallowed an outage
        async with client_for(self.app) as c:
            import_id, _ = await self.run_import(c, make_csv("شقة,الرياض,,1,1,Q1"))
            job = await self.job(c, import_id)
        self.assertEqual((job["status"], job["created"], job["indexed"]), ("completed", 1, 0))

    async def test_mapping_templates_and_option_validation(self):
        async with client_for(self.app) as c:
            up = await self.upload(c, make_csv("شقة,الرياض,,1,1,T1"))
            v = await c.post(f"{BASE}/{up['import_id']}/validate",
                             json={"mapping": self.mapping_of(up), "save_template_as": "تصدير الوكالة"})
            self.assertEqual(v.status_code, 200)
            tpls = (await c.get(f"{BASE}/templates")).json()["items"]
            self.assertEqual([t["name"] for t in tpls], ["تصدير الوكالة"])
            self.assertEqual(tpls[0]["mapping"]["price"], "السعر")
            again = await c.post(f"{BASE}/templates", json={"name": "تصدير الوكالة", "mapping": {"price": "المساحة"}})
            self.assertEqual(again.status_code, 201)
            self.assertEqual(len((await c.get(f"{BASE}/templates")).json()["items"]), 1)   # upsert, not duplicate
            self.assertEqual((await c.delete(f"{BASE}/templates/{tpls[0]['template_id']}")).status_code, 204)
            self.assertEqual((await c.get(f"{BASE}/templates")).json()["items"], [])
            bad = await c.post(f"{BASE}/{up['import_id']}/validate", json={"mapping": {"price": "عمود غير موجود"}})
            self.assertEqual(bad.status_code, 422)
            bad = await c.post(f"{BASE}/{up['import_id']}/validate", json={"mapping": self.mapping_of(up), "options": {"on_duplicate": "drop"}})
            self.assertEqual(bad.status_code, 422)

    async def test_missing_required_mapping_blocks_commit_unless_a_default_is_set(self):
        data = ("المدينة,السعر\nالرياض,500\n").encode("utf-8-sig")
        async with client_for(self.app) as c:
            up = await self.upload(c, data)
            m = self.mapping_of(up)
            self.assertNotIn("property_type", m)
            v = (await c.post(f"{BASE}/{up['import_id']}/validate", json={"mapping": m})).json()
            self.assertEqual(v["required_missing"], ["نوع العقار"])
            self.assertEqual((await c.post(f"{BASE}/{up['import_id']}/commit")).status_code, 422)
            v = (await c.post(f"{BASE}/{up['import_id']}/validate",
                              json={"mapping": m, "options": {"defaults": {"property_type": "شقة"}}})).json()
            self.assertEqual((v["required_missing"], v["counts"]["valid"]), ([], 1))
            self.assertEqual((await self.commit(c, up["import_id"]))["status"], "completed")

    @unittest.skipUnless(RLS_URL, "needs OMNIFLOW_TEST_RLS_URL (superusers bypass RLS)")
    async def test_other_tenants_cannot_see_or_download_an_import(self):
        async with client_for(self.app) as c:
            import_id, _ = await self.run_import(c, make_csv("سيارة,الرياض,,1,1,X1", "شقة,الرياض,,1,1,X2"))
        other = create_app()
        auth_overrides(other, self.tenants[1], self.AppSession)
        async with client_for(other) as c2:
            for url in (f"{BASE}/{import_id}", f"{BASE}/{import_id}/errors.csv"):
                self.assertEqual((await c2.get(url)).status_code, 404, url)
            self.assertEqual((await c2.post(f"{BASE}/{import_id}/cancel")).status_code, 404)
            self.assertEqual((await c2.post(f"{BASE}/{import_id}/commit")).status_code, 404)
        self.assertEqual(await self.listings(self.tenants[1]), [])


if __name__ == "__main__":
    unittest.main()

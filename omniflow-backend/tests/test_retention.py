"""Retention & erasure: dry-run changes nothing, real runs anonymise inactive customers and delete THEIR files only, failures are isolated,
logs are swept, other tenants are untouched. Object storage is a recording fake — no real S3/MinIO was used.
"""
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.shared.core.config import get_settings
from src.shared.services import erasure_files, retention

DB_URL = os.environ.get("OMNIFLOW_TEST_DATABASE_URL")
RLS_URL = os.environ.get("OMNIFLOW_TEST_RLS_URL")
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


class FakeStore:
    def __init__(self, fail_keys=()):
        self.objects, self.deleted, self.fail_keys = {}, [], set(fail_keys)

    def add(self, bucket, key):
        self.objects[(bucket, key)] = b"x"

    async def list_keys(self, bucket, prefix, limit=10_000):
        return [k for (b, k) in self.objects if b == bucket and k.startswith(prefix)]

    async def delete_file(self, bucket, key):
        if key in self.fail_keys:
            raise RuntimeError("s3 down")
        self.objects.pop((bucket, key), None)
        self.deleted.append(key)


class PureTests(unittest.TestCase):
    def test_key_from_url(self):
        self.assertEqual(erasure_files.key_from_url("http://minio:9000/omniflow-reports-vault/tenant-1/reports/c/a.pdf", "omniflow-reports-vault"), "tenant-1/reports/c/a.pdf")
        self.assertIsNone(erasure_files.key_from_url("http://elsewhere/other/x.pdf", "omniflow-reports-vault"))
        self.assertIsNone(erasure_files.key_from_url(None, "b"))

    def test_the_job_is_off_and_report_only_by_default(self):
        f = type(get_settings()).model_fields
        self.assertFalse(f["retention_enabled"].default)
        self.assertTrue(f["retention_dry_run"].default)
        self.assertGreaterEqual(f["retention_inactive_customer_days"].default, 730)        # conservative: years, not months


@unittest.skipUnless(DB_URL and RLS_URL, "needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL (non-superuser: RLS is the tenant guard)")
class RetentionDbTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        self.text = text
        self.admin, self.app_engine = create_async_engine(DB_URL), create_async_engine(RLS_URL)
        self.Admin = async_sessionmaker(self.admin, expire_on_commit=False)
        self.App = async_sessionmaker(self.app_engine, expire_on_commit=False)
        self.A, self.B = uuid.uuid4(), uuid.uuid4()
        self.bucket = get_settings().s3_vault_bucket
        self.store = FakeStore()
        self.old, self.recent = NOW - timedelta(days=1200), NOW - timedelta(days=10)
        self.cust = {}
        async with self.Admin() as s:
            for i, t in enumerate((self.A, self.B)):
                await s.execute(text("INSERT INTO tenants (tenant_id,business_name,fal_license_number,subscription_tier,status,max_ai_conversations,onboarding_status,created_at,updated_at) "
                                     "VALUES (:t,:n,:f,'starter','active',100,'completed',now(),now())"), {"t": t, "n": f"شركة {i}", "f": f"FAL-{t.hex[:8]}"})
                for kind, when in (("old", self.old), ("recent", self.recent), ("soon", NOW - timedelta(days=1090))):
                    cid = uuid.uuid4()
                    self.cust[(t, kind)] = cid
                    await s.execute(text("INSERT INTO customers (customer_id,tenant_id,unified_phone,phone_e164,display_name,source,channel_user_ids,is_processing_restricted,is_vip,vcard_state,created_at,updated_at) "
                                         "VALUES (:c,:t,:p,:p,:n,'manual','{}',false,false,'STATE_NEW',:w,:w)"), {"c": cid, "t": t, "p": f"+9665{uuid.uuid4().int % 10**8:08d}", "n": f"{kind}-{i}", "w": when})
                    if kind == "old":                                        # a report file per tenant's old customer
                        key = f"tenant-{t}/reports/{cid}/r1.pdf"
                        self.store.add(self.bucket, key)
                        await s.execute(text("INSERT INTO customer_reports (report_id,tenant_id,customer_id,report_type,s3_url,is_delivered,created_at,updated_at) "
                                             "VALUES (gen_random_uuid(),:t,:c,'deed_check_29',:u,true,now(),now())"), {"t": t, "c": cid, "u": f"http://minio:9000/{self.bucket}/{key}"})
                # an owner to notify
                await s.execute(text("INSERT INTO tenant_users (user_id,tenant_id,email,full_name,role,is_active,hashed_password,created_at,updated_at) VALUES (gen_random_uuid(),:t,:e,'Owner','admin',true,'x',now(),now())"),
                                {"t": t, "e": f"owner-{t.hex[:8]}@example.test"})
            await s.commit()
        p = patch("src.shared.db.session.AsyncSessionFactory", self.App)
        p.start()
        self.addCleanup(p.stop)

    async def asyncTearDown(self):
        async with self.Admin() as s:
            for t in (self.A, self.B):
                for table in ("notifications", "webhook_deliveries", "webhook_endpoints", "api_usage_log", "api_keys", "audit_logs", "customer_reports", "conversations", "customers", "tenant_users"):
                    await s.execute(self.text(f"DELETE FROM {table} WHERE tenant_id=:t"), {"t": t})
                await s.execute(self.text("DELETE FROM tenants WHERE tenant_id=:t"), {"t": t})
            await s.commit()
        await self.admin.dispose()
        await self.app_engine.dispose()

    async def row(self, sql, **p):
        async with self.Admin() as s:
            return (await s.execute(self.text(sql), p)).mappings().all()

    def cfg(self, **kw):
        base = dict(retention_enabled=True, retention_dry_run=False, retention_inactive_customer_days=1095, retention_warning_days=14, retention_batch_limit=50,
                    retention_audit_log_days=0, retention_api_usage_days=0, retention_webhook_delivery_days=0, retention_deletion_request_days=0)
        base.update(kw)
        return get_settings().model_copy(update=base)

    async def run_tenant(self, tenant, **kw):
        from src.shared.db.session import get_tenant_session

        dry = kw.pop("dry_run", None)
        async with get_tenant_session(tenant) as session:
            return await retention.run_for_tenant(session, tenant, dry_run=dry, now=NOW, file_store=kw.pop("store", self.store), cfg=self.cfg(**kw))

    async def erased(self, tenant, kind):
        return (await self.row("SELECT erased_at, display_name, phone_e164 FROM customers WHERE customer_id=:c", c=self.cust[(tenant, kind)]))[0]

    async def test_dry_run_reports_but_changes_nothing(self):
        rep = await self.run_tenant(self.A, dry_run=True)
        self.assertEqual((rep.dry_run, rep.inactive_customers, rep.warned_customers, rep.anonymized), (True, 1, 1, 0))
        self.assertIsNone((await self.erased(self.A, "old"))["erased_at"])
        self.assertEqual(self.store.deleted, [])
        self.assertEqual(await self.row("SELECT 1 FROM notifications WHERE tenant_id=:t", t=self.A), [])          # no warning sent in dry-run

    async def test_a_real_run_anonymises_only_the_inactive_customer_and_deletes_only_their_files(self):
        rep = await self.run_tenant(self.A)
        self.assertEqual((rep.anonymized, rep.anonymize_failed), (1, 0))
        old = await self.erased(self.A, "old")
        self.assertIsNotNone(old["erased_at"])
        self.assertNotIn("old-0", old["display_name"] or "")
        self.assertIsNone(old["phone_e164"])
        for kind in ("recent", "soon"):
            self.assertIsNone((await self.erased(self.A, kind))["erased_at"], kind)
        self.assertEqual([k for k in self.store.deleted], [f"tenant-{self.A}/reports/{self.cust[(self.A, 'old')]}/r1.pdf"])
        self.assertIn((self.bucket, f"tenant-{self.B}/reports/{self.cust[(self.B, 'old')]}/r1.pdf"), self.store.objects)          # tenant B's file untouched
        self.assertIsNone((await self.erased(self.B, "old"))["erased_at"])
        rpt = (await self.row("SELECT s3_url FROM customer_reports WHERE customer_id=:c", c=self.cust[(self.A, "old")]))[0]
        self.assertIsNone(rpt["s3_url"])                                           # the accounting row stays, the pointer is gone
        audit = await self.row("SELECT details FROM audit_logs WHERE tenant_id=:t AND action='privacy.anonymize'", t=self.A)
        self.assertEqual((len(audit), audit[0]["details"]["files_deleted"]), (1, 1))
        self.assertEqual(len(await self.row("SELECT 1 FROM audit_logs WHERE tenant_id=:t AND action='retention.run'", t=self.A)), 1)

    async def test_the_admins_are_warned_once_per_week_before_anything_is_erased(self):
        await self.run_tenant(self.A)
        await self.run_tenant(self.A)
        n = await self.row("SELECT title FROM notifications WHERE tenant_id=:t AND kind='retention_notice'", t=self.A)
        self.assertEqual(len(n), 1)                                                # dedupe_key = ISO week

    async def test_when_files_cannot_be_deleted_the_customer_is_kept_and_other_customers_still_proceed(self):
        extra = uuid.uuid4()
        key2 = f"tenant-{self.A}/reports/{extra}/r2.pdf"
        async with self.Admin() as s:
            await s.execute(self.text("INSERT INTO customers (customer_id,tenant_id,unified_phone,phone_e164,display_name,source,channel_user_ids,is_processing_restricted,is_vip,vcard_state,created_at,updated_at) "
                                      "VALUES (:c,:t,:p,:p,'old2','manual','{}',false,false,'STATE_NEW',:w,:w)"), {"c": extra, "t": self.A, "p": f"+9665{uuid.uuid4().int % 10**8:08d}", "w": self.old})
            await s.execute(self.text("INSERT INTO customer_reports (report_id,tenant_id,customer_id,report_type,s3_url,is_delivered,created_at,updated_at) VALUES (gen_random_uuid(),:t,:c,'deed_check_29',:u,true,now(),now())"),
                            {"t": self.A, "c": extra, "u": f"http://minio:9000/{self.bucket}/{key2}"})
            await s.commit()
        self.store.add(self.bucket, key2)
        store = FakeStore(fail_keys={key2})
        store.objects = dict(self.store.objects)
        rep = await self.run_tenant(self.A, store=store)
        self.assertEqual((rep.anonymized, rep.anonymize_failed), (1, 1))
        erased = {r["display_name"] for r in await self.row("SELECT display_name FROM customers WHERE tenant_id=:t AND erased_at IS NOT NULL", t=self.A)}
        self.assertEqual(len(erased), 1)
        still = (await self.row("SELECT erased_at FROM customers WHERE customer_id=:c", c=extra))[0]
        self.assertIsNone(still["erased_at"])                                      # not erased: the file pointer is still known, so the next run retries
        self.assertIsNotNone((await self.row("SELECT s3_url FROM customer_reports WHERE customer_id=:c", c=extra))[0]["s3_url"])

    async def test_a_prefix_that_points_into_another_tenant_is_never_deleted(self):
        foreign = f"tenant-{self.B}/reports/{self.cust[(self.B, 'old')]}/"
        async with self.Admin() as s:
            await s.execute(self.text("UPDATE customers SET vault_s3_prefix=:p WHERE customer_id=:c"), {"p": foreign, "c": self.cust[(self.A, "old")]})
            await s.commit()
        await self.run_tenant(self.A)
        self.assertIn((self.bucket, f"tenant-{self.B}/reports/{self.cust[(self.B, 'old')]}/r1.pdf"), self.store.objects)

    async def test_older_erasures_leave_no_orphan_files(self):
        async with self.Admin() as s:                                              # simulate an erasure done before this change: erased, file still referenced
            await s.execute(self.text("UPDATE customers SET erased_at=now() WHERE customer_id=:c"), {"c": self.cust[(self.A, "old")]})
            await s.commit()
        rep = await self.run_tenant(self.A)
        self.assertEqual(rep.orphan_report_files, 1)
        self.assertEqual(len(self.store.deleted), 1)
        self.assertIsNone((await self.row("SELECT s3_url FROM customer_reports WHERE customer_id=:c", c=self.cust[(self.A, "old")]))[0]["s3_url"])

    async def test_log_tables_are_swept_by_age_and_pending_deliveries_are_kept(self):
        async with self.Admin() as s:
            await s.execute(self.text("INSERT INTO api_keys (key_id,tenant_id,name,prefix,key_hash,created_at) VALUES (:k,:t,'k','ofk_x',:h,now())"), {"k": (k := uuid.uuid4()), "t": self.A, "h": uuid.uuid4().hex * 2})
            for age in (200, 5):
                await s.execute(self.text("INSERT INTO api_usage_log (tenant_id,key_id,method,path,status,created_at) VALUES (:t,:k,'GET','/x',200,:w)"), {"t": self.A, "k": k, "w": NOW - timedelta(days=age)})
            await s.execute(self.text("INSERT INTO webhook_endpoints (endpoint_id,tenant_id,url,secret_enc,events,active,consecutive_failures,created_at) VALUES (:e,:t,'https://93.184.216.34/h','x','[]',true,0,now())"), {"e": (e := uuid.uuid4()), "t": self.A})
            for st, age in (("delivered", 90), ("failed", 90), ("pending", 90), ("delivered", 3)):
                await s.execute(self.text("INSERT INTO webhook_deliveries (delivery_id,tenant_id,endpoint_id,event_id,event_type,payload,status,attempts,created_at) VALUES (gen_random_uuid(),:t,:e,gen_random_uuid(),'customer.created','{}',:s,1,:w)"),
                                {"t": self.A, "e": e, "s": st, "w": NOW - timedelta(days=age)})
            await s.commit()
        rep = await self.run_tenant(self.A, retention_api_usage_days=90, retention_webhook_delivery_days=30)
        self.assertEqual(rep.deleted, {"api_usage_log": 1, "webhook_deliveries": 2})
        self.assertEqual(len(await self.row("SELECT 1 FROM api_usage_log WHERE tenant_id=:t", t=self.A)), 1)
        self.assertEqual(sorted(r["status"] for r in await self.row("SELECT status FROM webhook_deliveries WHERE tenant_id=:t", t=self.A)), ["delivered", "pending"])

    async def test_recent_conversation_activity_protects_an_old_customer(self):
        async with self.Admin() as s:
            await s.execute(self.text("INSERT INTO conversations (conversation_id,tenant_id,customer_id,channel,platform_conversation_id,status,ai_reply_epoch,message_count,last_message_at,created_at,updated_at) "
                                      "VALUES (gen_random_uuid(),:t,:c,'whatsapp','p1','ai_active',0,1,:w,now(),now())"),
                            {"t": self.A, "c": self.cust[(self.A, "old")], "w": (NOW - timedelta(days=3)).replace(tzinfo=None)})       # naive UTC, as the app stores it
            await s.commit()
        rep = await self.run_tenant(self.A)
        self.assertEqual((rep.inactive_customers, rep.anonymized), (0, 0))
        self.assertIsNone((await self.erased(self.A, "old"))["erased_at"])

    async def test_zero_disables_each_rule(self):
        rep = await self.run_tenant(self.A, retention_inactive_customer_days=0)
        self.assertEqual((rep.inactive_customers, rep.anonymized, rep.warned_customers), (0, 0, 0))

    async def test_run_all_isolates_tenants_and_reports_each(self):
        with patch.object(retention, "get_settings", lambda: self.cfg()):
            result = await retention.run_all(dry_run=True, only_tenant=self.A, now=NOW, file_store=self.store)
        self.assertTrue(result["dry_run"])
        self.assertEqual([t["tenant_id"] for t in result["tenants"]], [str(self.A)])
        self.assertIn("data_deletion_requests", result["system"])


if __name__ == "__main__":
    unittest.main()

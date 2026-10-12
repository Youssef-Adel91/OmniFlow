"""Dev-data cleanup script guards + the team page never listing another workspace's people (even on an RLS-bypassing dev DB)."""
import os
import unittest
import uuid
from types import SimpleNamespace as NS
from unittest.mock import patch

from scripts import dev_cleanup_test_data as cleanup
from src.gateway.main import create_app
from src.shared.core.config import get_settings
from src.shared.core.enums import TenantUserRole
from tests.test_property_import_flow import auth_overrides, client_for

DB_URL = os.environ.get("OMNIFLOW_TEST_DATABASE_URL")
RLS_URL = os.environ.get("OMNIFLOW_TEST_RLS_URL")


class GuardTests(unittest.TestCase):
    def test_refuses_anything_that_is_not_local_development(self):
        self.assertIn("APP_ENV", cleanup.guard("localhost", "production", set()))
        self.assertIn("APP_ENV", cleanup.guard("localhost", "staging", set()))
        self.assertIn("not local", cleanup.guard("db.prod.example.com", "development", set()))
        self.assertIsNone(cleanup.guard("localhost", "development", set()))
        self.assertIsNone(cleanup.guard("127.0.0.1", "development", set()))
        self.assertIsNone(cleanup.guard("postgres", "development", {"postgres"}))        # own docker service, opted in explicitly

    def test_run_exits_2_without_touching_the_database_when_refused(self):
        import asyncio
        with patch.object(get_settings(), "app_env", "production"), patch.object(cleanup, "get_system_session", side_effect=AssertionError("must not connect")):
            self.assertEqual(asyncio.run(cleanup.run(True, 3, set())), 2)


@unittest.skipUnless(DB_URL and RLS_URL, "needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL (non-superuser: RLS is the tenant guard)")
class CleanupDbTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        self.text = text
        self.admin = create_async_engine(DB_URL)
        self.app_engine = create_async_engine(RLS_URL)
        self.Admin = async_sessionmaker(self.admin, expire_on_commit=False)
        self.App = async_sessionmaker(self.app_engine, expire_on_commit=False)
        self.dev, self.paid, self.real = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with self.Admin() as s:
            for t, name, fal in ((self.dev, "Dev Workspace", f"DEV-{uuid.uuid4().hex[:8]}"), (self.paid, "Dev Workspace", f"DEV-{uuid.uuid4().hex[:8]}"),
                                 (self.real, "شركة حقيقية", f"FAL-{uuid.uuid4().hex[:8]}")):
                await s.execute(text("INSERT INTO tenants (tenant_id,business_name,fal_license_number,subscription_tier,status,max_ai_conversations,onboarding_status,created_at,updated_at) "
                                     "VALUES (:t,:n,:f,'starter','trial',100,'completed',now(),now())"), {"t": t, "n": name, "f": fal})
                await s.execute(text("INSERT INTO tenant_users (user_id,tenant_id,full_name,email,hashed_password,role,is_active,created_at,updated_at) "
                                     "VALUES (gen_random_uuid(),:t,'Local Dev User','dev@example.com','x','admin',true,now(),now())"), {"t": t})
                await s.execute(text("INSERT INTO subscriptions (tenant_id,plan_code,status,is_exempt) VALUES (:t,'starter','trial',false)"), {"t": t})
            sid = (await s.execute(text("SELECT subscription_id FROM subscriptions WHERE tenant_id=:t"), {"t": self.paid})).scalar()
            await s.execute(text("INSERT INTO transactions (tenant_id,subscription_id,kind,plan_code,billing_interval,subtotal_halalas,vat_halalas,amount_halalas,status,provider,paid_at) "
                                 "VALUES (:t,:s,'initial','starter','monthly',100,15,115,'paid','manual',now())"), {"t": self.paid, "s": sid})
            await s.execute(text("INSERT INTO customers (customer_id,tenant_id,unified_phone,source,channel_user_ids,marketing_opt_in,is_vip,is_processing_restricted,vcard_state,engagement_score,created_at,updated_at) "
                                 "VALUES (gen_random_uuid(),:t,'+966500000999','whatsapp','{}',false,false,false,'STATE_NEW',0,now(),now())"), {"t": self.dev})
            await s.commit()
        for name, value in (("app_env", "development"), ("postgres_host", "localhost")):
            p = patch.object(get_settings(), name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch("src.shared.db.session.AsyncSessionFactory", self.App)
        p.start()
        self.addCleanup(p.stop)

    async def asyncTearDown(self):
        async with self.Admin() as s:
            for t in (self.dev, self.paid, self.real):
                for stmt in ("DELETE FROM invoices WHERE tenant_id=:t", "DELETE FROM transactions WHERE tenant_id=:t", "DELETE FROM customers WHERE tenant_id=:t",
                             "DELETE FROM subscriptions WHERE tenant_id=:t", "DELETE FROM tenant_users WHERE tenant_id=:t", "DELETE FROM tenants WHERE tenant_id=:t"):
                    await s.execute(self.text(stmt), {"t": t})
            await s.commit()
        await self.admin.dispose()
        await self.app_engine.dispose()

    async def exists(self, t):
        async with self.Admin() as s:
            return bool((await s.execute(self.text("SELECT 1 FROM tenants WHERE tenant_id=:t"), {"t": t})).scalar())

    async def test_dry_run_changes_nothing_and_apply_removes_only_clean_dev_workspaces(self):
        self.assertEqual(await cleanup.run(False, 3, set()), 0)
        self.assertTrue(all([await self.exists(self.dev), await self.exists(self.paid), await self.exists(self.real)]))
        self.assertEqual(await cleanup.run(True, 3, set()), 0)
        self.assertFalse(await self.exists(self.dev))                          # the throw-away workspace (and its customer + user) is gone
        self.assertTrue(await self.exists(self.paid))                          # money history: never touched
        self.assertTrue(await self.exists(self.real))                         # not a dev-provisioned name: never a candidate
        async with self.Admin() as s:
            left = (await s.execute(self.text("SELECT count(*) FROM tenant_users WHERE tenant_id=:t"), {"t": self.dev})).scalar()
        self.assertEqual(left, 0)

    async def test_team_page_lists_only_the_callers_workspace_even_when_rls_is_bypassed(self):
        app = create_app()
        auth_overrides(app, self.dev, self.Admin, role=TenantUserRole.ADMIN)    # superuser sessions bypass RLS, like a docker dev database
        async with client_for(app) as c:
            members = (await c.get("/api/v1/team")).json()["members"]
        self.assertEqual(len(members), 1)                                       # not 3 identical "Local Dev User" rows from other workspaces


if __name__ == "__main__":
    unittest.main()

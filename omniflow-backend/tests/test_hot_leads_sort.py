"""ConversationRepository.get_multi(sort_by='hot_leads'): status bonuses use the stored (lower-case) values.

Needs a migrated throwaway PostgreSQL: set OMNIFLOW_TEST_DATABASE_URL (see test_dashboard_analytics.py).
Skipped in CI (no Postgres there).
"""
import os
import unittest
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.shared.db.repository import ConversationRepository

DB_URL = os.environ.get("OMNIFLOW_TEST_DATABASE_URL")


@unittest.skipUnless(DB_URL, "set OMNIFLOW_TEST_DATABASE_URL to a migrated throwaway PostgreSQL")
class HotLeadsSortTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(DB_URL)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.tenant = uuid.uuid4()
        self.convs = {}
        async with self.Session() as s:
            x = lambda sql, **p: s.execute(text(sql), p)  # noqa: E731
            await x("INSERT INTO tenants (tenant_id,business_name,fal_license_number,subscription_tier,status,max_ai_conversations,onboarding_status,created_at,updated_at) "
                    "VALUES (:t,'T',:f,'economic','active',100,'completed',now(),now())", t=self.tenant, f=f"FAL-{self.tenant.hex[:8]}")
            for status in ("closed", "ai_active", "escalated"):   # inserted worst-first on purpose
                cust, conv = uuid.uuid4(), uuid.uuid4()
                await x("INSERT INTO customers (customer_id,tenant_id,unified_phone,is_vip,is_processing_restricted,vcard_state,engagement_score,created_at,updated_at) "
                        "VALUES (:c,:t,:p,false,false,'none',10,now(),now())", c=cust, t=self.tenant, p=f"+9665{uuid.uuid4().int % 10**8:08d}")
                await x("INSERT INTO conversations (conversation_id,tenant_id,customer_id,channel,status,ai_reply_epoch,message_count,created_at,updated_at) "
                        "VALUES (:v,:t,:c,'whatsapp',:st,0,0,now(),now())", v=conv, t=self.tenant, c=cust, st=status)
                self.convs[status] = conv
            await s.commit()

    async def asyncTearDown(self):
        async with self.Session() as s:
            for sql in ("DELETE FROM conversations WHERE tenant_id=:t", "DELETE FROM customers WHERE tenant_id=:t", "DELETE FROM tenants WHERE tenant_id=:t"):
                await s.execute(text(sql), {"t": self.tenant})
            await s.commit()
        await self.engine.dispose()

    async def test_escalated_then_active_then_closed(self):
        async with self.Session() as s:
            rows, total = await ConversationRepository(s).get_multi(sort_by="hot_leads")
        mine = [r.conversation_id for r in rows if r.tenant_id == self.tenant]
        self.assertEqual(total >= 3, True)
        self.assertEqual(mine, [self.convs["escalated"], self.convs["ai_active"], self.convs["closed"]])


if __name__ == "__main__":
    unittest.main()

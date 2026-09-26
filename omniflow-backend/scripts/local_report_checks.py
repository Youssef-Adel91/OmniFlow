"""Report API checks using the inbox validator's isolated PostgreSQL transaction."""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import select

from src.gateway.routers import reports
from src.shared.db import session as db
from src.shared.db.models import Conversation, CustomerReport


async def check_reports(user, tenant_b, conv_a, conv_b):
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    own_ids = [uuid.uuid4() for _ in range(4)]
    foreign_id = uuid.uuid4()
    for tenant_id, conversation_id in ((user.tenant_id, conv_a), (tenant_b, conv_b)):
        async with db.get_tenant_session(tenant_id) as session:
            customer_id = await session.scalar(select(Conversation.customer_id).where(Conversation.conversation_id == conversation_id))
            if tenant_id == tenant_b:
                session.add(CustomerReport(report_id=foreign_id, tenant_id=tenant_id, customer_id=customer_id,
                                           report_type="deed_check_29", price_sar=999, created_at=today))
            else:
                for identity, instant, price, ready in zip(own_ids, (
                    today - timedelta(microseconds=1), today,
                    today + timedelta(days=1, microseconds=-1), today + timedelta(days=1),
                ), (15, 29, 15, 29), (False, True, False, True)):
                    session.add(CustomerReport(report_id=identity, tenant_id=tenant_id, customer_id=customer_id,
                                               report_type="deed_check_29", price_sar=price, created_at=instant,
                                               s3_url=f"s3://validation/tenant-{tenant_id}/reports/{customer_id}/{identity}.pdf" if ready else None))
    async with db.get_tenant_session(user.tenant_id) as session:
        page = await reports.list_reports(user, session, date_from=today.date(), date_to=today.date(), page_size=1)
        assert page.total == 2 and len(page.items) == 1
        assert page.items[0].report_id == own_ids[2] and page.items[0].status == "pending"
        page = await reports.list_reports(user, session, date_from=today.date(), date_to=today.date(), page=2, page_size=1)
        assert page.items[0].report_id == own_ids[1] and page.items[0].status == "ready"
        assert page.items[0].price_sar == 29 and "s3_url" not in page.items[0].model_dump()
        chart = await reports.revenue_analytics(user, session, period="daily", buckets=1)
        assert chart.total_revenue == 44 and chart.total_count == 2 and chart.series[0].total_revenue == 44
        try:
            await reports.list_reports(user, session, date_from=(today + timedelta(days=1)).date(), date_to=today.date())
        except HTTPException as exc:
            assert exc.status_code == 422
        else:
            raise AssertionError("Reversed date range was accepted")
        with patch.object(reports, "report_download_url", AsyncMock(return_value="signed-url")) as sign:
            detail = await reports.get_report(user, session, own_ids[1])
            assert detail.s3_url == "signed-url" and detail.status == "ready"
            sign.reset_mock()
            try:
                await reports.get_report(user, session, foreign_id)
            except HTTPException as exc:
                assert exc.status_code == 404
            else:
                raise AssertionError("Another tenant's report was accessible")
            sign.assert_not_awaited()
    print("PASS real reports: inclusive UTC dates, pagination, prices/status, revenue, tenant isolation and download authorization")

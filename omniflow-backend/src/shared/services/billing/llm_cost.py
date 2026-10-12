"""Real AI cost per reply: measured tokens x owner-configured prices, with a default per-reply cost when nothing is measurable.

Prices live in config (LLM_PRICES_JSON = {"model": {"in_per_1k": SAR, "out_per_1k": SAR}}) so they can be updated without a deploy
of code; they are NOT hard-coded because provider prices change and the owner must supply them. Until a model has an entry (or when a
reply has no token counts) the cost is `llm_default_cost_sar_per_reply` (0.02 SAR) and the row is marked cost_source='default' so the
margin view can show how much of the number is measured vs assumed.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.shared.core.config import get_settings
from src.shared.db.models import LlmUsage
from src.shared.db.session import get_system_session

logger = structlog.get_logger(__name__)


def _prices() -> dict[str, dict[str, float]]:
    raw = (get_settings().llm_prices_json or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except ValueError:
        logger.error("llm_prices_json_invalid")
        return {}


def compute(model: str, input_tokens: int, output_tokens: int) -> tuple[Decimal, str]:
    """(cost in SAR, 'measured' | 'default')."""
    price = _prices().get(model)
    if price and (input_tokens or output_tokens):
        try:
            cost = (Decimal(str(price["in_per_1k"])) * input_tokens + Decimal(str(price["out_per_1k"])) * output_tokens) / Decimal(1000)
            return cost.quantize(Decimal("0.000001")), "measured"
        except (KeyError, ArithmeticError, ValueError):
            logger.error("llm_price_entry_invalid", model=model)
    return Decimal(str(get_settings().llm_default_cost_sar_per_reply)).quantize(Decimal("0.000001")), "default"


async def record(*, tenant_id: uuid.UUID, event_id: uuid.UUID, model: str, tier: str, input_tokens: int, output_tokens: int,
                 quota_source: str) -> None:
    """One row per billable reply; idempotent on (tenant, event) so a Kafka redelivery never double-counts. Never raises."""
    try:
        cost, source = compute(model, int(input_tokens or 0), int(output_tokens or 0))
        async with get_system_session() as session:
            await session.execute(pg_insert(LlmUsage).values(
                usage_id=uuid.uuid4(), tenant_id=tenant_id, event_id=event_id, model=(model or "unknown")[:80], tier=str(tier)[:30],
                input_tokens=int(input_tokens or 0), output_tokens=int(output_tokens or 0), cost_sar=cost, cost_source=source,
                quota_source=quota_source[:8]).on_conflict_do_nothing(constraint="uq_llm_usage_event"))
    except Exception as exc:  # noqa: BLE001 - cost bookkeeping must never break a customer reply
        logger.warning("llm_usage_record_failed", tenant_id=str(tenant_id), error=type(exc).__name__)


async def rollup_daily(days_back: int = 2) -> int:
    """Re-aggregate the last N local days from raw rows into llm_usage_daily (idempotent upsert)."""
    tz = get_settings().app_timezone
    since = datetime.now(timezone.utc) - timedelta(days=days_back)
    async with get_system_session() as session:
        res = await session.execute(text(
            "INSERT INTO llm_usage_daily (tenant_id, day, tier, model, replies, input_tokens, output_tokens, cost_sar) "
            "SELECT tenant_id, (created_at AT TIME ZONE :tz)::date, tier, model, count(*), sum(input_tokens), sum(output_tokens), sum(cost_sar) "
            "FROM llm_usage WHERE created_at >= :since GROUP BY 1, 2, 3, 4 "
            "ON CONFLICT (tenant_id, day, tier, model) DO UPDATE SET replies = EXCLUDED.replies, input_tokens = EXCLUDED.input_tokens, "
            "output_tokens = EXCLUDED.output_tokens, cost_sar = EXCLUDED.cost_sar"), {"tz": tz, "since": since})
        return res.rowcount or 0


def month_bounds(period: str) -> tuple[datetime, datetime]:
    """UTC bounds of an Asia/Riyadh calendar month 'YYYY-MM'."""
    tz = ZoneInfo(get_settings().app_timezone)
    y, m = int(period[:4]), int(period[5:7])
    start = datetime(y, m, 1, tzinfo=tz)
    end = datetime(y + (m == 12), (m % 12) + 1, 1, tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


async def margin_report(period: str) -> dict:
    """Platform view: per tenant AI cost vs recognised revenue (VAT-exclusive subtotal of paid transactions in the month)."""
    start, end = month_bounds(period)
    threshold = get_settings().ai_margin_alert_pct
    async with get_system_session() as session:
        cost_rows = (await session.execute(text(
            "SELECT tenant_id, count(*) AS replies, coalesce(sum(cost_sar),0) AS cost, "
            "coalesce(sum(cost_sar) FILTER (WHERE cost_source='default'),0) AS assumed_cost, "
            "count(*) FILTER (WHERE tier='L3') AS l3 FROM llm_usage WHERE created_at >= :s AND created_at < :e GROUP BY tenant_id"),
            {"s": start, "e": end})).mappings().all()
        rev_rows = (await session.execute(text(
            "SELECT tenant_id, coalesce(sum(subtotal_halalas),0) AS rev FROM transactions "
            "WHERE status='paid' AND paid_at >= :s AND paid_at < :e GROUP BY tenant_id"), {"s": start, "e": end})).mappings().all()
        names = {r["tenant_id"]: r["business_name"] for r in (await session.execute(text("SELECT tenant_id, business_name FROM tenants"))).mappings()}
        exempt = {r["tenant_id"] for r in (await session.execute(text("SELECT tenant_id FROM subscriptions WHERE is_exempt"))).mappings()}
    revenue = {r["tenant_id"]: Decimal(r["rev"]) / 100 for r in rev_rows}
    items, total_cost, total_rev = [], Decimal(0), Decimal(0)
    for r in cost_rows:
        tid, cost = r["tenant_id"], Decimal(r["cost"])
        rev = revenue.get(tid, Decimal(0))
        margin = rev - cost
        ratio = (cost / rev * 100) if rev > 0 else None
        items.append({"tenant_id": str(tid), "business_name": names.get(tid, ""), "exempt": tid in exempt, "replies": int(r["replies"]),
                      "l3_replies": int(r["l3"]), "ai_cost_sar": float(round(cost, 4)), "assumed_cost_sar": float(round(Decimal(r["assumed_cost"]), 4)),
                      "revenue_sar": float(round(rev, 2)), "margin_sar": float(round(margin, 2)),
                      "cost_to_revenue_pct": float(round(ratio, 1)) if ratio is not None else None,
                      "alert": bool(ratio is not None and ratio > threshold) or bool(rev == 0 and cost > 0 and tid not in exempt)})
        total_cost += cost
        total_rev += rev
    items.sort(key=lambda x: (not x["alert"], -x["ai_cost_sar"]))
    return {"period": period, "alert_threshold_pct": threshold, "totals": {"ai_cost_sar": float(round(total_cost, 4)), "revenue_sar": float(round(total_rev, 2)),
            "margin_sar": float(round(total_rev - total_cost, 2))}, "tenants": items}

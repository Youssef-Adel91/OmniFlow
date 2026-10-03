"""
Tenant analytics for the operational dashboard (GET /api/v1/dashboard/analytics).

Everything here is a live SQL aggregate against PostgreSQL inside the caller's
RLS-scoped session -- nothing is mocked, and every statement is additionally
filtered by `tenant_id` (defence in depth on top of RLS; `messages` has no
tenant column and is always reached through its tenant's `conversations`).

Day bucketing and the busy-hours heatmap use Asia/Riyadh (the product's market),
not UTC, so "today" matches what the brokerage sees on the wall clock.

Metric definitions (also shown in the UI, so nobody reads a number wrongly):

* new_conversations   conversations created in the window
* new_customers       customers created in the window (with a channel filter:
                      customers that have a conversation on that channel)
* hot_leads           distinct hot customers (engagement_score > 70 OR VIP) with
                      a conversation created in the window. Scores are the
                      *current* score -- the schema keeps no score history, so
                      the previous-period figure uses today's scores too.
* ai_resolution_rate  % of conversations (with >= 1 customer message) that never
                      needed a person: no human_agent message AND status not in
                      (escalated, human_active)
* handoff_rate        100 - ai_resolution_rate
* first reply times   customer's first message -> first ai_bot / first
                      human_agent message after it, averaged, seconds
* avg_resolution      closed conversations only: updated_at - created_at. A
                      proxy: the schema stores no `closed_at`.
* appointments        appointments created in the window, excluding cancelled
* reports_revenue     SUM(price_sar) of delivered customer_reports in the window

Not derivable from the current schema (reported honestly, not faked):
* most-recommended properties -- recommendations are computed live and never
  logged, so there is nothing to rank (see `properties.top_recommended`).
* per-message intent -- no intent column; `top_topics` is a keyword estimate.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

TZ_NAME = "Asia/Riyadh"
TZ = ZoneInfo(TZ_NAME)
HOT_LEAD_SCORE = 70
MAX_RANGE_DAYS = 366
CHANNELS = ("whatsapp", "instagram", "messenger", "tiktok", "x", "snapchat", "web")

# Keyword buckets for the "most frequent topics" estimate (Arabic + common
# dialect spellings). ILIKE substring match on customer messages, so very short
# stems are avoided (e.g. plain "اين" is inside "معاينة", "فال" is a common prefix).
TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "السعر والميزانية": ("سعر", "بكام", "كم السعر", "تكلفة", "ميزانية", "price"),
    "الموقع والحي": ("موقع", "حي ", "وين", "فين", "أين", "منطقة", "location"),
    "المعاينة والموعد": ("معاينة", "موعد", "زيارة", "أشوف", "اشوف", "visit"),
    "التمويل": ("تمويل", "قرض", "بنك", "أقساط", "اقساط", "تقسيط"),
    "المساحة والغرف": ("مساحة", "غرف", "غرفة", "متر", "الدور"),
    "الإيجار": ("إيجار", "ايجار", "اجار", "للإيجار"),
    "الصك والترخيص": ("صك", "رخصة", "ترخيص", "رقم الإعلان"),
}


class AnalyticsRangeError(ValueError):
    """Bad range/from/to input (mapped to HTTP 422 by the router)."""


# ── Range handling ───────────────────────────────────────────────────────────

class Window:
    """A [start, end) interval in UTC plus its Riyadh-local date bounds."""

    def __init__(self, first_day: date, last_day: date):
        self.first_day = first_day
        self.last_day = last_day
        self.days = (last_day - first_day).days + 1
        self.start = datetime.combine(first_day, datetime.min.time(), TZ).astimezone(timezone.utc)
        self.end = datetime.combine(last_day + timedelta(days=1), datetime.min.time(), TZ).astimezone(timezone.utc)

    def previous(self) -> "Window":
        prev_last = self.first_day - timedelta(days=1)
        return Window(prev_last - timedelta(days=self.days - 1), prev_last)


def resolve_window(range_key: str, date_from: date | None, date_to: date | None, now: datetime | None = None) -> Window:
    today = (now or datetime.now(timezone.utc)).astimezone(TZ).date()
    presets = {"7d": 7, "30d": 30, "90d": 90}
    if range_key in presets:
        return Window(today - timedelta(days=presets[range_key] - 1), today)
    if range_key != "custom":
        raise AnalyticsRangeError("range must be one of 7d, 30d, 90d, custom")
    if date_from is None or date_to is None:
        raise AnalyticsRangeError("custom range requires both from and to")
    if date_from > date_to:
        raise AnalyticsRangeError("from must be on or before to")
    if (date_to - date_from).days + 1 > MAX_RANGE_DAYS:
        raise AnalyticsRangeError(f"range is limited to {MAX_RANGE_DAYS} days")
    return Window(date_from, date_to)


def _change_pct(current: float | None, previous: float | None) -> float | None:
    """% change vs the previous period; None when there is nothing to compare to."""
    if current is None or previous is None or previous == 0:
        return None
    return round((current - previous) / abs(previous) * 100, 1)


def _kpi(current: float | int | None, previous: float | int | None) -> dict[str, Any]:
    return {"value": current, "previous": previous, "change_pct": _change_pct(current, previous)}


def _p(window: Window, tenant_id: uuid.UUID, channel: str | None, **extra: Any) -> dict[str, Any]:
    return {"t": tenant_id, "s": window.start, "e": window.end, "ch": channel, **extra}


# ── KPI queries ──────────────────────────────────────────────────────────────

_CONV_KPI_SQL = text(
    """
    WITH conv AS (
        SELECT c.conversation_id, c.customer_id, c.status, c.created_at, c.updated_at
        FROM conversations c
        WHERE c.tenant_id = :t AND c.created_at >= :s AND c.created_at < :e
          AND (CAST(:ch AS text) IS NULL OR c.channel = CAST(:ch AS text))
    ),
    first_msgs AS (
        SELECT conv.conversation_id,
               min(m.created_at) FILTER (WHERE m.sender_type = 'customer') AS first_customer,
               min(m.created_at) FILTER (WHERE m.sender_type = 'human_agent') AS any_human
        FROM conv JOIN messages m ON m.conversation_id = conv.conversation_id
        GROUP BY conv.conversation_id
    ),
    replies AS (
        SELECT f.conversation_id, f.first_customer, f.any_human,
               (SELECT min(x.created_at) FROM messages x
                 WHERE x.conversation_id = f.conversation_id AND x.sender_type = 'ai_bot'
                   AND x.created_at >= f.first_customer) AS first_ai,
               (SELECT min(x.created_at) FROM messages x
                 WHERE x.conversation_id = f.conversation_id AND x.sender_type = 'human_agent'
                   AND x.created_at >= f.first_customer) AS first_human
        FROM first_msgs f WHERE f.first_customer IS NOT NULL
    )
    SELECT
        (SELECT count(*) FROM conv) AS new_conversations,
        count(*) AS engaged,
        count(*) FILTER (WHERE conv.status IN ('escalated', 'human_active') OR r.any_human IS NOT NULL) AS handed_off,
        avg(extract(epoch FROM (r.first_ai - r.first_customer))) AS avg_first_ai,
        avg(extract(epoch FROM (r.first_human - r.first_customer))) AS avg_first_human,
        (SELECT avg(extract(epoch FROM (cv.updated_at - cv.created_at)))
           FROM conv cv WHERE cv.status = 'closed') AS avg_resolution
    FROM replies r JOIN conv ON conv.conversation_id = r.conversation_id
    """
)

_CUSTOMERS_SQL = text(
    """
    SELECT count(*) FROM customers cu
    WHERE cu.tenant_id = :t AND cu.created_at >= :s AND cu.created_at < :e
      AND (CAST(:ch AS text) IS NULL OR EXISTS (
            SELECT 1 FROM conversations c
            WHERE c.customer_id = cu.customer_id AND c.tenant_id = :t AND c.channel = CAST(:ch AS text)))
    """
)

_HOT_SQL = text(
    """
    SELECT count(DISTINCT cu.customer_id)
    FROM customers cu JOIN conversations c ON c.customer_id = cu.customer_id
    WHERE cu.tenant_id = :t AND c.tenant_id = :t
      AND c.created_at >= :s AND c.created_at < :e
      AND (CAST(:ch AS text) IS NULL OR c.channel = CAST(:ch AS text))
      AND (cu.engagement_score > :hot OR cu.is_vip IS TRUE)
    """
)

_APPT_SQL = text(
    """
    SELECT count(*) FROM appointments a
    WHERE a.tenant_id = :t AND a.created_at >= :s AND a.created_at < :e AND a.status <> 'cancelled'
      AND (CAST(:ch AS text) IS NULL OR EXISTS (
            SELECT 1 FROM conversations c
            WHERE c.conversation_id = a.conversation_id AND c.channel = CAST(:ch AS text)))
    """
)

_REVENUE_SQL = text(
    """
    SELECT coalesce(sum(r.price_sar), 0) AS revenue, count(*) AS sold
    FROM customer_reports r
    WHERE r.tenant_id = :t AND r.is_delivered IS TRUE AND r.created_at >= :s AND r.created_at < :e
    """
)


async def _period_kpis(session: AsyncSession, tenant_id: uuid.UUID, window: Window, channel: str | None) -> dict[str, Any]:
    params = _p(window, tenant_id, channel)
    row = (await session.execute(_CONV_KPI_SQL, params)).mappings().one()
    new_customers = await session.scalar(_CUSTOMERS_SQL, params) or 0
    hot = await session.scalar(_HOT_SQL, {**params, "hot": HOT_LEAD_SCORE}) or 0
    appts = await session.scalar(_APPT_SQL, params) or 0
    rev = (await session.execute(_REVENUE_SQL, params)).mappings().one()

    engaged = row["engaged"] or 0
    handed_off = row["handed_off"] or 0
    handoff_rate = round(handed_off / engaged * 100, 1) if engaged else None
    return {
        "new_conversations": int(row["new_conversations"] or 0),
        "new_customers": int(new_customers),
        "hot_leads": int(hot),
        "ai_resolution_rate": round(100 - handoff_rate, 1) if handoff_rate is not None else None,
        "handoff_rate": handoff_rate,
        "avg_first_response_ai_seconds": round(float(row["avg_first_ai"]), 1) if row["avg_first_ai"] is not None else None,
        "avg_first_response_human_seconds": round(float(row["avg_first_human"]), 1) if row["avg_first_human"] is not None else None,
        "avg_resolution_seconds": round(float(row["avg_resolution"]), 1) if row["avg_resolution"] is not None else None,
        "appointments_booked": int(appts),
        "reports_revenue_sar": float(rev["revenue"] or 0),
        "reports_sold": int(rev["sold"] or 0),
    }


# ── Series / distributions ───────────────────────────────────────────────────

_SERIES_SQL = text(
    """
    WITH convs AS (
        SELECT (c.created_at AT TIME ZONE :tz)::date AS d, c.channel, count(*) AS n
        FROM conversations c
        WHERE c.tenant_id = :t AND c.created_at >= :s AND c.created_at < :e
          AND (CAST(:ch AS text) IS NULL OR c.channel = CAST(:ch AS text))
        GROUP BY 1, 2
    ), msgs AS (
        SELECT (m.created_at AT TIME ZONE :tz)::date AS d, c.channel, count(*) AS n
        FROM messages m JOIN conversations c ON c.conversation_id = m.conversation_id
        WHERE c.tenant_id = :t AND m.created_at >= :s AND m.created_at < :e
          AND (CAST(:ch AS text) IS NULL OR c.channel = CAST(:ch AS text))
        GROUP BY 1, 2
    )
    SELECT 'c' AS kind, d, channel, n FROM convs
    UNION ALL
    SELECT 'm', d, channel, n FROM msgs
    """
)

_HEATMAP_SQL = text(
    """
    SELECT extract(dow FROM (m.created_at AT TIME ZONE :tz))::int AS dow,
           extract(hour FROM (m.created_at AT TIME ZONE :tz))::int AS hr,
           count(*) AS n
    FROM messages m JOIN conversations c ON c.conversation_id = m.conversation_id
    WHERE c.tenant_id = :t AND m.sender_type = 'customer'
      AND m.created_at >= :s AND m.created_at < :e
      AND (CAST(:ch AS text) IS NULL OR c.channel = CAST(:ch AS text))
    GROUP BY 1, 2
    """
)

_CHANNEL_DIST_SQL = text(
    """
    SELECT c.channel AS key, count(*) AS n FROM conversations c
    WHERE c.tenant_id = :t AND c.created_at >= :s AND c.created_at < :e
      AND (CAST(:ch AS text) IS NULL OR c.channel = CAST(:ch AS text))
    GROUP BY 1 ORDER BY 2 DESC
    """
)

_STATUS_DIST_SQL = text(
    """
    SELECT c.status AS key, count(*) AS n FROM conversations c
    WHERE c.tenant_id = :t AND c.created_at >= :s AND c.created_at < :e
      AND (CAST(:ch AS text) IS NULL OR c.channel = CAST(:ch AS text))
    GROUP BY 1 ORDER BY 2 DESC
    """
)

_SCORE_DIST_SQL = text(
    """
    SELECT CASE
             WHEN coalesce(cu.engagement_score, 0) > 75 THEN '76-100'
             WHEN coalesce(cu.engagement_score, 0) > 50 THEN '51-75'
             WHEN coalesce(cu.engagement_score, 0) > 25 THEN '26-50'
             ELSE '0-25' END AS key,
           count(*) AS n
    FROM customers cu
    WHERE cu.tenant_id = :t AND EXISTS (
        SELECT 1 FROM conversations c
        WHERE c.customer_id = cu.customer_id AND c.tenant_id = :t
          AND c.created_at >= :s AND c.created_at < :e
          AND (CAST(:ch AS text) IS NULL OR c.channel = CAST(:ch AS text)))
    GROUP BY 1
    """
)

_FUNNEL_SQL = text(
    """
    WITH conv AS (
        SELECT c.conversation_id, c.customer_id FROM conversations c
        WHERE c.tenant_id = :t AND c.created_at >= :s AND c.created_at < :e
          AND (CAST(:ch AS text) IS NULL OR c.channel = CAST(:ch AS text))
    ), engaged AS (
        SELECT conv.customer_id FROM conv
        JOIN messages m ON m.conversation_id = conv.conversation_id AND m.sender_type = 'customer'
        GROUP BY conv.customer_id HAVING count(*) >= 2
    ), hot AS (
        SELECT cu.customer_id FROM customers cu
        WHERE cu.tenant_id = :t AND (cu.engagement_score > :hot OR cu.is_vip IS TRUE)
          AND cu.customer_id IN (SELECT customer_id FROM engaged)
    ), appt AS (
        SELECT DISTINCT a.customer_id FROM appointments a
        WHERE a.tenant_id = :t AND a.status <> 'cancelled' AND a.customer_id IN (SELECT customer_id FROM hot)
    ), closed AS (
        SELECT customer_id FROM appt a2 WHERE EXISTS (
            SELECT 1 FROM appointments x WHERE x.tenant_id = :t AND x.customer_id = a2.customer_id AND x.status = 'completed')
        OR EXISTS (SELECT 1 FROM customer_reports r WHERE r.tenant_id = :t AND r.customer_id = a2.customer_id AND r.is_delivered IS TRUE)
    )
    SELECT (SELECT count(DISTINCT customer_id) FROM conv) AS new_chat,
           (SELECT count(*) FROM engaged) AS engaged,
           (SELECT count(*) FROM hot) AS hot,
           (SELECT count(*) FROM appt) AS appointment,
           (SELECT count(*) FROM closed) AS closed
    """
)


# One scan of the customer messages, one FILTER per topic. Static SQL (no string
# building): t{i} pairs with the i-th entry of TOPIC_KEYWORDS, keywords bound as :k{i}.
_TOPIC_SQL = text(
    """
    SELECT
        count(*) FILTER (WHERE m.text_content ILIKE ANY (CAST(:k0 AS text[]))) AS t0,
        count(*) FILTER (WHERE m.text_content ILIKE ANY (CAST(:k1 AS text[]))) AS t1,
        count(*) FILTER (WHERE m.text_content ILIKE ANY (CAST(:k2 AS text[]))) AS t2,
        count(*) FILTER (WHERE m.text_content ILIKE ANY (CAST(:k3 AS text[]))) AS t3,
        count(*) FILTER (WHERE m.text_content ILIKE ANY (CAST(:k4 AS text[]))) AS t4,
        count(*) FILTER (WHERE m.text_content ILIKE ANY (CAST(:k5 AS text[]))) AS t5,
        count(*) FILTER (WHERE m.text_content ILIKE ANY (CAST(:k6 AS text[]))) AS t6
    FROM messages m JOIN conversations c ON c.conversation_id = m.conversation_id
    WHERE c.tenant_id = :t AND m.sender_type = 'customer'
      AND m.created_at >= :s AND m.created_at < :e
      AND (CAST(:ch AS text) IS NULL OR c.channel = CAST(:ch AS text))
    """
)
if len(TOPIC_KEYWORDS) != 7:
    raise RuntimeError("update _TOPIC_SQL when adding/removing topics")


_INVENTORY_SQL = text(
    """
    SELECT status AS key, count(*) AS n FROM property_listings WHERE tenant_id = :t GROUP BY 1 ORDER BY 2 DESC
    """
)
_INVENTORY_TYPE_SQL = text(
    """
    SELECT property_type AS key, count(*) AS n FROM property_listings WHERE tenant_id = :t GROUP BY 1 ORDER BY 2 DESC
    """
)
_INVENTORY_CITY_SQL = text(
    """
    SELECT coalesce(nullif(city, ''), 'غير محدد') AS key, count(*) AS n
    FROM property_listings WHERE tenant_id = :t GROUP BY 1 ORDER BY 2 DESC LIMIT 10
    """
)

# Locations customers asked about (LLM-extracted profile) vs verified-active stock.
_GAPS_SQL = text(
    """
    WITH asks AS (
        SELECT lower(btrim(cu.extracted_profile ->> 'location')) AS loc, count(*) AS requests
        FROM customers cu
        WHERE cu.tenant_id = :t
          AND coalesce(btrim(cu.extracted_profile ->> 'location'), '') <> ''
          AND EXISTS (
            SELECT 1 FROM conversations c
            WHERE c.customer_id = cu.customer_id AND c.tenant_id = :t
              AND c.created_at >= :s AND c.created_at < :e
              AND (CAST(:ch AS text) IS NULL OR c.channel = CAST(:ch AS text)))
        GROUP BY 1
    )
    SELECT a.loc AS location, a.requests,
           (SELECT count(*) FROM property_listings p
             WHERE p.tenant_id = :t AND p.status = 'VERIFIED_ACTIVE'
               AND (lower(coalesce(p.city, '')) LIKE '%' || a.loc || '%'
                 OR lower(coalesce(p.district, '')) LIKE '%' || a.loc || '%')) AS matching
    FROM asks a ORDER BY a.requests DESC LIMIT 10
    """
)

_HOT_LIST_SQL = text(
    """
    SELECT cu.customer_id::text AS customer_id,
           coalesce(nullif(cu.display_name, ''), nullif(cu.whatsapp_profile_name, ''), '') AS name,
           cu.unified_phone AS phone, coalesce(cu.engagement_score, 0) AS score, cu.is_vip AS is_vip,
           (SELECT c.conversation_id::text FROM conversations c
             WHERE c.customer_id = cu.customer_id AND c.tenant_id = :t
             ORDER BY c.last_message_at DESC NULLS LAST LIMIT 1) AS conversation_id
    FROM customers cu
    WHERE cu.tenant_id = :t AND (cu.engagement_score > :hot OR cu.is_vip IS TRUE)
      AND (CAST(:ch AS text) IS NULL OR EXISTS (
            SELECT 1 FROM conversations c WHERE c.customer_id = cu.customer_id AND c.tenant_id = :t
              AND c.channel = CAST(:ch AS text)))
    ORDER BY cu.updated_at DESC LIMIT 8
    """
)

_AWAITING_SQL = text(
    """
    SELECT c.conversation_id::text AS conversation_id, c.channel, c.status,
           coalesce(nullif(cu.display_name, ''), nullif(cu.whatsapp_profile_name, ''), '') AS name,
           cu.unified_phone AS phone,
           extract(epoch FROM (now() - coalesce(c.last_message_at, c.created_at)))::bigint AS waiting_seconds
    FROM conversations c JOIN customers cu ON cu.customer_id = c.customer_id
    WHERE c.tenant_id = :t AND c.status IN ('escalated', 'human_active')
      AND (CAST(:ch AS text) IS NULL OR c.channel = CAST(:ch AS text))
      AND coalesce((SELECT m.sender_type FROM messages m WHERE m.conversation_id = c.conversation_id
                    ORDER BY m.created_at DESC LIMIT 1), 'customer') = 'customer'
    ORDER BY waiting_seconds DESC LIMIT 10
    """
)


def _fill_series(window: Window, rows: list[Any]) -> list[dict[str, Any]]:
    by_day: dict[date, dict[str, Any]] = {}
    for i in range(window.days):
        d = window.first_day + timedelta(days=i)
        by_day[d] = {"date": d.isoformat(), "conversations": 0, "messages": 0, "channels": {}}
    for kind, d, channel, n in rows:
        point = by_day.get(d)
        if point is None:
            continue
        key = "conversations" if kind == "c" else "messages"
        point[key] += int(n)
        ch = point["channels"].setdefault(channel, {"conversations": 0, "messages": 0})
        ch[key] += int(n)
    return list(by_day.values())


async def build_analytics(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    window: Window,
    channel: str | None,
    sla_minutes: int = 15,
) -> dict[str, Any]:
    prev = window.previous()
    cur = await _period_kpis(session, tenant_id, window, channel)
    old = await _period_kpis(session, tenant_id, prev, channel)
    kpis = {key: _kpi(cur[key], old[key]) for key in cur}

    params = _p(window, tenant_id, channel)
    series_rows = (await session.execute(_SERIES_SQL, {**params, "tz": TZ_NAME})).all()
    heat_rows = (await session.execute(_HEATMAP_SQL, {**params, "tz": TZ_NAME})).all()
    matrix = [[0] * 24 for _ in range(7)]  # row 0 = Sunday (Postgres dow)
    for dow, hr, n in heat_rows:
        matrix[int(dow)][int(hr)] = int(n)

    def dist(rows: list[Any]) -> list[dict[str, Any]]:
        return [{"key": r[0], "count": int(r[1])} for r in rows]

    score_rows = {r[0]: int(r[1]) for r in (await session.execute(_SCORE_DIST_SQL, params)).all()}
    f = (await session.execute(_FUNNEL_SQL, {**params, "hot": HOT_LEAD_SCORE})).mappings().one()
    funnel = [
        {"key": "new", "label": "عملاء جدد (محادثة جديدة)", "count": int(f["new_chat"] or 0)},
        {"key": "engaged", "label": "عملاء متفاعلون (رسالتان فأكثر)", "count": int(f["engaged"] or 0)},
        {"key": "hot", "label": "عملاء ساخنون", "count": int(f["hot"] or 0)},
        {"key": "appointment", "label": "موعد / معاينة", "count": int(f["appointment"] or 0)},
        {"key": "closed", "label": "إغلاق (معاينة مكتملة أو تقرير مدفوع)", "count": int(f["closed"] or 0)},
    ]

    kw_params = {
        f"k{i}": [f"%{w}%" for w in words] for i, words in enumerate(TOPIC_KEYWORDS.values())
    }
    topic_row = (await session.execute(_TOPIC_SQL, {**params, **kw_params})).one()
    topics = sorted(
        ({"topic": name, "count": int(topic_row[i] or 0)} for i, name in enumerate(TOPIC_KEYWORDS)),
        key=lambda x: -x["count"],
    )

    gaps = [
        {"location": r[0], "requests": int(r[1]), "matching_listings": int(r[2])}
        for r in (await session.execute(_GAPS_SQL, params)).all()
    ]
    hot_list = [dict(r) for r in (await session.execute(_HOT_LIST_SQL, {**params, "hot": HOT_LEAD_SCORE})).mappings().all()]
    awaiting = [dict(r) for r in (await session.execute(_AWAITING_SQL, params)).mappings().all()]
    sla_seconds = sla_minutes * 60

    return {
        "range": {
            "from": window.first_day.isoformat(), "to": window.last_day.isoformat(), "days": window.days,
            "previous_from": prev.first_day.isoformat(), "previous_to": prev.last_day.isoformat(),
            "timezone": TZ_NAME,
        },
        "channel": channel,
        "kpis": kpis,
        "series": _fill_series(window, [tuple(r) for r in series_rows]),
        "funnel": funnel,
        "heatmap": {"matrix": matrix, "max": max((max(r) for r in matrix), default=0), "first_day": "sunday"},
        "channel_distribution": dist((await session.execute(_CHANNEL_DIST_SQL, params)).all()),
        "status_distribution": dist((await session.execute(_STATUS_DIST_SQL, params)).all()),
        "lead_score_distribution": [
            {"key": b, "count": score_rows.get(b, 0)} for b in ("0-25", "26-50", "51-75", "76-100")
        ],
        "properties": {
            "by_status": dist((await session.execute(_INVENTORY_SQL, {"t": tenant_id})).all()),
            "by_type": dist((await session.execute(_INVENTORY_TYPE_SQL, {"t": tenant_id})).all()),
            "by_city": dist((await session.execute(_INVENTORY_CITY_SQL, {"t": tenant_id})).all()),
            "top_recommended": None,
            "top_recommended_unavailable_reason": "لا يوجد سجل لتوصيات العقارات (تُحسب لحظيًا ولا تُخزَّن).",
        },
        "inventory_gaps": gaps,
        "top_topics": topics,
        "topics_note": "تقدير بالكلمات المفتاحية على رسائل العملاء (لا يوجد تصنيف نية مخزَّن).",
        "attention": {
            "sla_minutes": sla_minutes,
            "hot_leads": hot_list,
            "awaiting_human": awaiting,
            "sla_breached": [a for a in awaiting if int(a["waiting_seconds"] or 0) > sla_seconds],
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

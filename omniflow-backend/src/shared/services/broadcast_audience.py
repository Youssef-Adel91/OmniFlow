"""Who a broadcast reaches -- ONE definition shared by the API (counts/preview/confirmation) and the worker (send).

A customer receives a marketing broadcast only if ALL of these hold (PDPL):
  * marketing_opt_in is true and opt_out_at is empty  (explicit consent, never withdrawn)
  * is_processing_restricted is false                  (no processing objection)
  * not archived
  * has a real phone number (phone_e164)               (Instagram/Messenger-only customers cannot get a WhatsApp template)
then the audience filters narrow it: segment, tags (any), city, source.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select

from src.shared.db.models import Customer

SEGMENTS = ("all", "vip", "engaged", "new", "inactive", "hot")
LEGACY_SEGMENTS = {"all_vip": "vip", "dormant": "inactive"}      # values the old composer sent
SOURCES = ("whatsapp", "instagram", "messenger", "manual", "import")
NEW_CUSTOMER_WINDOW_DAYS = 30
MAX_TAGS = 10


class InvalidAudience(ValueError):
    pass


@dataclass(frozen=True)
class Audience:
    segment: str = "all"
    tags: tuple[str, ...] = ()
    city: str | None = None
    sources: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"segment": self.segment, "tags": list(self.tags), "city": self.city, "sources": list(self.sources)}


def parse_audience(raw: Any) -> Audience:
    """
    Accepts the new object {segment, tags, city, sources}, the legacy {segment|list_type|audience: ...} object or a
    bare segment string. An unknown segment is an ERROR (never silently widened to "all": a typo must not blast
    every customer).
    """
    if raw is None:
        return Audience()
    obj: dict[str, Any] = raw if isinstance(raw, dict) else {"segment": raw}
    seg = obj.get("segment") or obj.get("list_type") or obj.get("audience") or "all"
    if not isinstance(seg, str):
        raise InvalidAudience("الشريحة غير صالحة")
    seg = LEGACY_SEGMENTS.get(seg.strip().lower(), seg.strip().lower())
    if seg not in SEGMENTS:
        raise InvalidAudience(f"شريحة غير معروفة: «{seg[:30]}». المتاح: {', '.join(SEGMENTS)}")
    tags = tuple(dict.fromkeys(str(t).strip() for t in (obj.get("tags") or []) if str(t).strip()))
    if len(tags) > MAX_TAGS:
        raise InvalidAudience(f"حتى {MAX_TAGS} وسوم فقط")
    sources = tuple(dict.fromkeys(str(x).strip().lower() for x in (obj.get("sources") or []) if str(x).strip()))
    bad = [x for x in sources if x not in SOURCES]
    if bad:
        raise InvalidAudience(f"مصدر غير معروف: {bad[0][:20]}")
    city = obj.get("city")
    city = str(city).strip()[:100] if city and str(city).strip() else None
    return Audience(seg, tags, city, sources)


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _narrow(stmt, a: Audience, *, engagement_threshold: int, now: datetime | None = None):
    if a.segment == "vip":
        stmt = stmt.where(Customer.is_vip.is_(True))
    elif a.segment == "engaged":
        stmt = stmt.where(or_(Customer.is_vip.is_(True), Customer.engagement_score >= engagement_threshold))
    elif a.segment == "hot":
        stmt = stmt.where(Customer.engagement_score >= max(engagement_threshold, 60))
    elif a.segment == "new":
        stmt = stmt.where(Customer.created_at >= (now or datetime.now(timezone.utc)) - timedelta(days=NEW_CUSTOMER_WINDOW_DAYS))
    elif a.segment == "inactive":
        stmt = stmt.where(or_(Customer.engagement_score.is_(None), Customer.engagement_score < engagement_threshold)).where(Customer.is_vip.is_(False))
    if a.tags:
        stmt = stmt.where(or_(*(Customer.tags.contains([t]) for t in a.tags)))
    if a.city:
        stmt = stmt.where(Customer.city.ilike(_like(a.city), escape="\\"))
    if a.sources:
        stmt = stmt.where(Customer.source.in_(a.sources))
    return stmt


def eligible(stmt, a: Audience, *, engagement_threshold: int):
    """Add the consent/eligibility rules + audience filters to a `select(...).select_from(Customer)`-style statement."""
    stmt = stmt.where(
        Customer.marketing_opt_in.is_(True), Customer.opt_out_at.is_(None),
        Customer.is_processing_restricted.is_(False), Customer.archived_at.is_(None), Customer.phone_e164.is_not(None))
    return _narrow(stmt, a, engagement_threshold=engagement_threshold)


def select_customers(a: Audience, *, engagement_threshold: int):
    return eligible(select(Customer), a, engagement_threshold=engagement_threshold)


async def count_audience(session, a: Audience, *, engagement_threshold: int) -> dict[str, int]:
    """Reach plus WHY others are excluded (so the composer can say "12 customers are not opted in")."""
    base = _narrow(select(func.count(Customer.customer_id)).where(Customer.archived_at.is_(None)), a, engagement_threshold=engagement_threshold)
    matching = await session.scalar(base) or 0
    reach = await session.scalar(eligible(select(func.count(Customer.customer_id)), a, engagement_threshold=engagement_threshold)) or 0
    opted_out = await session.scalar(base.where(Customer.opt_out_at.is_not(None))) or 0
    restricted = await session.scalar(base.where(Customer.is_processing_restricted.is_(True))) or 0
    no_phone = await session.scalar(base.where(Customer.phone_e164.is_(None))) or 0
    no_consent = await session.scalar(base.where(Customer.marketing_opt_in.is_(False), Customer.opt_out_at.is_(None))) or 0
    return {"recipients_count": reach, "matching_segment": matching, "excluded_no_consent": no_consent,
            "excluded_opted_out": opted_out, "excluded_restricted": restricted, "excluded_no_phone": no_phone}


def render_params(templates: list[str], customer: Customer) -> list[str]:
    """Fill {name}/{city} in the template body variables for one recipient (WhatsApp rejects empty variables)."""
    name = (customer.display_name or customer.whatsapp_profile_name or "عميلنا العزيز").strip() or "عميلنا العزيز"
    city = (customer.city or "مدينتك").strip() or "مدينتك"
    return [(t or "").replace("{name}", name).replace("{city}", city).strip() or "-" for t in templates]

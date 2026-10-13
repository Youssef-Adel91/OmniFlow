"""
Listings that fit what the customer asked for — straight from the tenant's own inventory (SQL, not an LLM), so the assistant can never "recommend" a property that
does not exist or quote a price that is not in the database.

Tiers, strictest first, stopping at the first one that returns anything:
    1. type + district + rooms + price range                         (exact = True)
    2. type + district + price range                                 (rooms dropped)
    3. type + price range                                            (district dropped -> "nearest alternative")
    4. type + price up to budget × 1.15                              (slightly above budget -> labelled as such)
Only VERIFIED_ACTIVE listings are ever returned. Results are ordered by closeness to the budget ceiling (best value first), at most `limit`.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Sequence

from sqlalchemy import and_, distinct, func, select

from src.shared.core.enums import ListingStatus
from src.shared.db.models import PropertyListing

TYPE_AR = {"apartment": "شقة", "villa": "فيلا", "land": "أرض", "office": "مكتب", "commercial": "تجاري", "warehouse": "مستودع", "daily_rental": "إيجار يومي"}
OVER_BUDGET_FACTOR = 1.15
_DISTRICT_CACHE: dict[str, tuple[float, list[str]]] = {}


@dataclass
class MatchResult:
    listings: list[dict[str, Any]] = field(default_factory=list)
    tier: int = 0                      # 0 = nothing found, 1 = exact … 4 = slightly over budget
    note: str = ""

    @property
    def exact(self) -> bool:
        return self.tier == 1


async def known_districts(session, tenant_id: uuid.UUID, *, ttl: float = 300.0) -> list[str]:
    key = str(tenant_id)
    hit = _DISTRICT_CACHE.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    rows = (await session.execute(select(distinct(PropertyListing.district)).where(
        PropertyListing.tenant_id == tenant_id, PropertyListing.district.is_not(None), PropertyListing.status == ListingStatus.VERIFIED_ACTIVE.value))).scalars().all()
    out = sorted({r.strip() for r in rows if r and r.strip()})
    _DISTRICT_CACHE[key] = (time.monotonic() + ttl, out)
    return out


def _safe_text(raw: str | None) -> str:
    """Listing descriptions come from imports/uploads: treat them as DATA. Anything that reads like an instruction to the model is dropped, control characters removed."""
    from src.sales import intents

    text = "".join(ch for ch in (raw or "").replace("\n", " ") if ch.isprintable()).strip()
    return "" if intents.is_injection(text) or intents.is_other_customers_data_request(text) else text


def _card(l: PropertyListing) -> dict[str, Any]:
    desc = _safe_text(l.description_ar or l.description_en)
    return {
        "listing_id": str(l.listing_id), "rega_ad_number": l.rega_ad_number, "type": l.property_type, "type_ar": TYPE_AR.get(str(l.property_type), str(l.property_type)),
        "city": l.city, "district": l.district, "price": float(l.price) if l.price is not None else None,
        "area_sqm": float(l.area_sqm) if l.area_sqm is not None else None, "bedrooms": l.bedrooms, "bathrooms": l.bathrooms,
        "map_url": f"https://maps.google.com/?q={l.latitude},{l.longitude}" if l.latitude is not None and l.longitude is not None else None,
        "summary": desc[:160],
    }


def _where(tenant_id, q: dict[str, Any], *, district: bool, rooms: bool, ceiling_factor: float):
    conds = [PropertyListing.tenant_id == tenant_id, PropertyListing.status == ListingStatus.VERIFIED_ACTIVE.value]
    if q.get("property_type"):
        conds.append(PropertyListing.property_type == q["property_type"])
    if district and q.get("district"):
        needle = str(q["district"]).strip().removeprefix("ال").replace("\\", "").replace("%", "").replace("_", "")
        conds.append(PropertyListing.district.ilike(f"%{needle}%"))
    if rooms and q.get("rooms"):
        conds.append(PropertyListing.bedrooms == int(q["rooms"]))
    if q.get("budget_max"):
        conds.append(PropertyListing.price <= float(q["budget_max"]) * ceiling_factor)
    if q.get("budget_min"):
        conds.append(PropertyListing.price >= float(q["budget_min"]))
    return and_(*conds)


async def match(session, tenant_id: uuid.UUID, q: dict[str, Any], *, limit: int = 3) -> MatchResult:
    """Nothing to match on (no type, district or budget known) -> empty result: we do not dump the whole inventory on a customer who has said nothing."""
    if not (q.get("property_type") or q.get("district") or q.get("budget_max") or q.get("budget_min")):
        return MatchResult()
    ceiling = float(q["budget_max"]) if q.get("budget_max") else None
    tiers = (
        (1, dict(district=True, rooms=True, ceiling_factor=1.0), ""),
        (2, dict(district=True, rooms=False, ceiling_factor=1.0), "لم نجد بنفس عدد الغرف، هذه الأقرب."),
        (3, dict(district=False, rooms=False, ceiling_factor=1.0), "لم نجد في الحي المطلوب، هذه بدائل في مناطق أخرى ضمن ميزانيتك."),
        (4, dict(district=False, rooms=False, ceiling_factor=OVER_BUDGET_FACTOR), "لم نجد ضمن الميزانية بالضبط، هذه أعلى منها بقليل."),
    )
    for tier, kw, note in tiers:
        if tier == 4 and ceiling is None:
            break
        if (tier == 2 and not q.get("rooms")) or (tier == 3 and not q.get("district")):
            continue                      # nothing was dropped -> identical to the previous tier
        stmt = select(PropertyListing).where(_where(tenant_id, q, **kw))
        stmt = stmt.order_by(PropertyListing.price.desc().nulls_last()) if ceiling else stmt.order_by(PropertyListing.updated_at.desc())
        rows = (await session.execute(stmt.limit(limit))).scalars().all()
        if rows:
            return MatchResult([_card(r) for r in rows], tier, note)
    return MatchResult()


def format_block(result: MatchResult) -> str:
    """The inventory block injected into the model's prompt. The prompt tells the model to use ONLY these facts."""
    if not result.listings:
        return ("لا يوجد حاليًا في مخزون الشركة عقار مطابق لما ذكره العميل. لا تذكر ولا تخترع أي عقار أو سعر. "
                "قل بصدق إنه لا يوجد مطابق الآن، واعرض تسجيل طلبه ليتواصل معه مستشار عند توفر ما يناسبه.")
    lines = ["عقارات من مخزون الشركة (استخدم هذه الحقائق فقط، ولا تضف أي معلومة غير مذكورة):"]
    if result.note:
        lines.append(f"ملاحظة للمساعد: {result.note}")
    for i, c in enumerate(result.listings, 1):
        price = f"{int(c['price']):,} ريال" if c["price"] is not None else "السعر عند التواصل"
        bits = [c["type_ar"], c["district"] or c["city"] or "", price,
                f"{int(c['area_sqm'])} م²" if c["area_sqm"] else "", f"{c['bedrooms']} غرف" if c["bedrooms"] else "",
                f"رقم إعلان الهيئة {c['rega_ad_number']}" if c["rega_ad_number"] else ""]
        line = f"{i}) " + "، ".join(b for b in bits if b)
        if c["map_url"]:
            line += f" — الموقع: {c['map_url']}"
        if c["summary"]:
            line += f" — {c['summary']}"
        lines.append(line)
    return "\n".join(lines)

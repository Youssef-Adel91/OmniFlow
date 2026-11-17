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

import re
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
    reason: str = ""                   # "need_info": nothing was searched because the customer gave neither a district nor a budget yet ("" = a search ran)

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


_TIERS = (
    (1, dict(district=True, rooms=True, ceiling_factor=1.0), ""),
    (2, dict(district=True, rooms=False, ceiling_factor=1.0), "لم نجد بنفس عدد الغرف، هذه الأقرب."),
    (3, dict(district=False, rooms=False, ceiling_factor=1.0), "لم نجد في الحي المطلوب، هذه بدائل في مناطق أخرى ضمن ميزانيتك."),
    (4, dict(district=False, rooms=False, ceiling_factor=OVER_BUDGET_FACTOR), "لم نجد ضمن الميزانية بالضبط، هذه أعلى منها بقليل."),
)


def _gate(q: dict[str, Any], *, require_focus: bool, price_question: bool) -> MatchResult | None:
    """An empty MatchResult(reason="need_info") when nothing may be searched yet, else None. A price question is always answered from the inventory (cheapest first),
    even when the customer has not said the type, district or budget — «كم السعر؟» must get a price, not a counter-question."""
    if price_question:
        return None
    if not (q.get("property_type") or q.get("district") or q.get("budget_max") or q.get("budget_min")):
        return MatchResult(reason="need_info")
    if require_focus and not (q.get("district") or q.get("budget_max") or q.get("budget_min")):
        return MatchResult(reason="need_info")
    return None


async def match(session, tenant_id: uuid.UUID, q: dict[str, Any], *, limit: int = 3, require_focus: bool = False, price_question: bool = False) -> MatchResult:
    """Nothing to match on (no type, district or budget known) -> empty result: we do not dump the whole inventory on a customer who has said nothing.
    `require_focus` (the assistant's first reply in a conversation): a property type alone is not enough either — without a district or a budget no specific listing is
    shown, the assistant asks one question first (reason="need_info"). `price_question`: the customer asked «كم السعر؟» — both gates are lifted and, with no budget given,
    the cheapest listings of the asked type (any type when none was named) come first."""
    gate = _gate(q, require_focus=require_focus, price_question=price_question)
    if gate is not None:
        return gate
    ceiling = float(q["budget_max"]) if q.get("budget_max") else None
    tiers = _TIERS
    for tier, kw, note in tiers:
        if tier == 4 and ceiling is None:
            break
        if (tier == 2 and not q.get("rooms")) or (tier == 3 and not q.get("district")):
            continue                      # nothing was dropped -> identical to the previous tier
        stmt = select(PropertyListing).where(_where(tenant_id, q, **kw))
        if ceiling:
            stmt = stmt.order_by(PropertyListing.price.desc().nulls_last())
        elif price_question:
            stmt = stmt.order_by(PropertyListing.price.asc().nulls_last())          # no budget: the cheapest (best entry point) first
        else:
            stmt = stmt.order_by(PropertyListing.updated_at.desc())
        rows = (await session.execute(stmt.limit(limit))).scalars().all()
        if rows:
            return MatchResult([_card(r) for r in rows], tier, note)
    return MatchResult()


def match_cards(cards: Sequence[dict[str, Any]], q: dict[str, Any], *, limit: int = 3, require_focus: bool = False, price_question: bool = False) -> MatchResult:
    """The same tiers as `match`, over listing cards held in memory (used by `scripts/eval_bot.py` so the live evaluation sees what the database would return)."""
    gate = _gate(q, require_focus=require_focus, price_question=price_question)
    if gate is not None:
        return gate
    ceiling = float(q["budget_max"]) if q.get("budget_max") else None

    def fits(c: dict[str, Any], *, district: bool, rooms: bool, ceiling_factor: float) -> bool:
        price = c.get("price")
        if q.get("property_type") and c.get("type") != q["property_type"]:
            return False
        if district and q.get("district"):
            needle = str(q["district"]).strip().removeprefix("ال").lower()
            if needle not in str(c.get("district") or "").lower():
                return False
        if rooms and q.get("rooms") and c.get("bedrooms") != int(q["rooms"]):
            return False
        if q.get("budget_max") and (price is None or price > float(q["budget_max"]) * ceiling_factor):
            return False
        if q.get("budget_min") and (price is None or price < float(q["budget_min"])):
            return False
        return True

    for tier, kw, note in _TIERS:
        if tier == 4 and ceiling is None:
            break
        if (tier == 2 and not q.get("rooms")) or (tier == 3 and not q.get("district")):
            continue
        rows = [c for c in cards if fits(c, **kw)]
        if rows:
            rows.sort(key=lambda c: (c.get("price") is None, -(c.get("price") or 0) if ceiling else (c.get("price") or 0)))
            return MatchResult(list(rows[:limit]), tier, note)
    return MatchResult()


def _where_phrase(card: dict[str, Any]) -> str:
    """«شقة في حي النرجس» — a ready phrase, so the model does not glue the type and the district together («الشقة النرجس»)."""
    if card.get("district"):
        return f"{card['type_ar']} في حي {card['district']}"
    return f"{card['type_ar']} في {card['city']}" if card.get("city") else card["type_ar"]


def format_block(result: MatchResult, *, show_ad_number: bool = True) -> str:
    """The inventory block injected into the model's prompt. The prompt tells the model to use ONLY these facts.
    `show_ad_number=False` (the first reply, when the customer did not ask for it) leaves the Hai'a advertisement number out of the block."""
    if not result.listings and result.reason == "need_info":
        return ("العميل لم يحدد الحي ولا الميزانية بعد، فلا تعرض أي عقار محدد الآن ولا تذكر أي سعر أو رقم إعلان. "
                "اسأل سؤالًا واحدًا فقط عن أهم معلومة ناقصة ثم انتظر ردّه.")
    if not result.listings:
        return ("لا يوجد حاليًا في مخزون الشركة عقار مطابق لما ذكره العميل. لا تذكر ولا تخترع أي عقار أو سعر. "
                "قل بصدق إنه لا يوجد مطابق الآن، واعرض تسجيل طلبه ليتواصل معه مستشار عند توفر ما يناسبه.")
    lines = ["عقارات من مخزون الشركة (استخدم هذه الحقائق فقط، ولا تضف أي معلومة غير مذكورة؛ اكتب نوع العقار والحي كما هو هنا، مثل «شقة في حي النرجس»):"]
    if result.note:
        lines.append(f"ملاحظة للمساعد: {result.note} هذا أقرب بديل موجود وليس مطابقًا تمامًا: اعرضه بوضوح ووضّح الفرق، ولا تقل «ما عندنا» أو «لا يوجد» لأن عندنا بديلًا.")
    for i, c in enumerate(result.listings, 1):
        price = f"{int(c['price']):,} ريال" if c["price"] is not None else "السعر عند التواصل"
        bits = [_where_phrase(c), price,
                f"{int(c['area_sqm'])} م²" if c["area_sqm"] else "", f"{c['bedrooms']} غرف" if c["bedrooms"] else "",
                f"رقم إعلان الهيئة {c['rega_ad_number']}" if show_ad_number and c["rega_ad_number"] else ""]
        line = f"{i}) " + "، ".join(b for b in bits if b)
        if c["map_url"]:
            line += f" — الموقع: {c['map_url']}"
        if c["summary"]:
            line += f" — {c['summary']}"
        lines.append(line)
    if not show_ad_number:
        lines.append("لا تذكر رقم إعلان الهيئة في هذا الرد؛ يُذكر فقط إذا طلبه العميل أو عند ترتيب المعاينة.")
    return "\n".join(lines)


def districts_in_block(text: str | None) -> set[str]:
    """District names appearing as «في حي X» in an inventory block (used to tidy «الشقة X» in the model's answer)."""
    return {m.group(1).strip() for m in re.finditer(r"في حي ([^،\n—»«)(:.]+)", text or "") if m.group(1).strip()}

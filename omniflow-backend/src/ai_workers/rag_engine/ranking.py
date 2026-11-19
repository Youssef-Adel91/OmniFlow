"""Deterministic structure around the semantic search: payload filters BEFORE the limit, and a deterministic re-rank AFTER it.

Why: the embedding (an English model on Arabic text) only orders results by text similarity, so the right listing could land far down an UNFILTERED top-5 and never reach the
prompt. The customer's hard needs — type, a price ceiling (budget + OVER_BUDGET_FACTOR), district — are not a matter of similarity: they are filters. Qdrant applies them
before the limit, so every one of the `limit` slots is a listing that can be offered. The semantic score then only breaks ties; the order is fully deterministic.
"""
from __future__ import annotations

from typing import Any, Iterable

from src.sales.matcher import OVER_BUDGET_FACTOR

SEARCH_LIMIT = 10          # fetch more than we show: the re-rank picks the order, the caller slices


def _variants(district: str) -> list[str]:
    d = (district or "").strip()
    bare = d.removeprefix("ال").strip()
    return [v for v in dict.fromkeys([d, bare, f"ال{bare}", f"حي {bare}", f"حي ال{bare}"]) if v]


def property_constraints(qualification: dict[str, Any] | None, extracted: dict[str, Any] | None = None) -> dict[str, Any]:
    """The customer's hard needs: the recorded qualification wins, the LLM-extracted preferences fill the gaps. Keys: property_type, budget_min, budget_max, district, rooms."""
    from src.sales import recommendation_filters

    return recommendation_filters.constraints(qualification, extracted)


def qdrant_filter_args(c: dict[str, Any]) -> dict[str, Any]:
    """Arguments for `QdrantManager.build_preference_filter`: price ceiling = budget × OVER_BUDGET_FACTOR (the one margin the whole product allows)."""
    return {
        "price_min": float(c["budget_min"]) if c.get("budget_min") else None,
        "price_max": float(c["budget_max"]) * OVER_BUDGET_FACTOR if c.get("budget_max") else None,
        "district_any": _variants(c["district"]) if c.get("district") else None,
        "property_type": c.get("property_type") or None,
    }


def rank_key(hit: Any, c: dict[str, Any]) -> tuple:
    """Smaller sorts first. Deterministic: (within budget?, same district?, rooms match?, distance below the ceiling, -semantic score, listing id)."""
    p = getattr(hit, "payload", None) or {}
    price = p.get("price_sar")
    ceiling = float(c["budget_max"]) if c.get("budget_max") else None
    over = 1 if (ceiling is not None and price is not None and float(price) > ceiling) else 0
    district_ok = 0
    if c.get("district"):
        want = c["district"].strip().removeprefix("ال").lower()
        district_ok = 0 if want in str(p.get("district") or "").lower() else 1
    rooms_ok = 0 if (not c.get("rooms") or p.get("bedrooms") == int(c["rooms"])) else 1
    gap = abs(ceiling - float(price)) if (ceiling is not None and price is not None) else 0.0
    return (over, district_ok, rooms_ok, gap, -float(getattr(hit, "score", 0.0) or 0.0), str(p.get("listing_id") or getattr(hit, "id", "")))


def rerank(hits: Iterable[Any], c: dict[str, Any]) -> list[Any]:
    return sorted(hits, key=lambda h: rank_key(h, c))


def drop_contradicting(hits: Iterable[Any], c: dict[str, Any]) -> list[Any]:
    """Safety net behind the Qdrant filter (a collection without the payload indexes, an old point): a hit that breaks a hard constraint is never shown."""
    from src.sales import recommendation_filters

    relaxed = dict(c)
    if relaxed.get("budget_max"):
        relaxed["budget_max"] = float(relaxed["budget_max"]) * OVER_BUDGET_FACTOR
    return recommendation_filters.filter_hits(hits, relaxed)


def format_priority_listings(cards: list[dict[str, Any]]) -> str:
    """The deterministic database match, in the retrieval context: always FIRST, same facts as the sales addendum."""
    lines = ["## العقارات المتاحة المطابقة لطلبك (من قاعدة بيانات الشركة، وهي المرجع الأول):\n"]
    for i, c in enumerate(cards, 1):
        where = f"{c.get('type_ar', '')} في حي {c['district']}" if c.get("district") else str(c.get("type_ar", ""))
        price = f"{int(c['price']):,} ريال" if c.get("price") is not None else "السعر عند التواصل"
        bits = [where, price, f"{int(c['area_sqm'])} م²" if c.get("area_sqm") else "", f"{c['bedrooms']} غرف" if c.get("bedrooms") else ""]
        lines.append(f"[{i}] " + "، ".join(b for b in bits if b))
    return "\n".join(lines)

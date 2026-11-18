"""The «عقارات مقترحة» (semantic search) panel must agree with the «عقارات مناسبة» (matcher) panel and with the customer summary: the same type, budget, district and rooms.

The semantic search used to RELAX its filters stage by stage until something came back (price only, then nothing), so an apartment under 1M could show villas.
Now the customer's recorded qualification (the summary's source) wins over the LLM-extracted preferences, and every result is re-checked against the hard constraints:
a result that breaks one is dropped — an empty panel is better than a wrong one.
"""
from __future__ import annotations

from typing import Any, Iterable

_FIELDS = ("property_type", "budget_min", "budget_max", "district", "rooms")


def constraints(qualification: dict[str, Any] | None, extracted: dict[str, Any] | None) -> dict[str, Any]:
    """Per field: the customer's recorded qualification if it has the field, else what the extractor found. `rooms` falls back to the extractor's `bedrooms`."""
    q, e = qualification or {}, extracted or {}
    out: dict[str, Any] = {}
    for f in _FIELDS:
        v = q.get(f)
        if v in (None, "", 0):
            v = e.get("bedrooms") if f == "rooms" else e.get(f)
        if v not in (None, "", 0):
            out[f] = v
    return out


def _norm_district(value: Any) -> str:
    return str(value or "").strip().lower().removeprefix("ال").strip()


def keeps(payload: dict[str, Any], c: dict[str, Any]) -> bool:
    """True when a search hit (its Qdrant payload) satisfies every hard constraint."""
    if c.get("property_type") and payload.get("property_type") != c["property_type"]:
        return False
    price = payload.get("price_sar")
    if c.get("budget_max") and price is not None and float(price) > float(c["budget_max"]):
        return False
    if c.get("budget_min") and price is not None and float(price) < float(c["budget_min"]):
        return False
    if c.get("district") and _norm_district(c["district"]) not in _norm_district(payload.get("district")) and _norm_district(payload.get("district")) not in _norm_district(c["district"]):
        return False
    if c.get("rooms") and payload.get("bedrooms") is not None and int(payload["bedrooms"]) != int(c["rooms"]):
        return False
    return True


def filter_hits(hits: Iterable[Any], c: dict[str, Any]) -> list[Any]:
    return [h for h in hits if keeps(getattr(h, "payload", None) or {}, c)]

"""
shared/services/lead_scoring.py — Customer purchase-likelihood scoring.

Built per an explicit product request for a professional lead-scoring
dashboard, combining three real signal sources (nothing invented, no field
that doesn't exist in the schema):

  1. Conversation behavior:  total message volume, buying-intent keyword
     hits in the customer's own messages, whether the AI ever escalated the
     conversation to a "deep" routing tier (L2/L3/VAULT — a proxy for "this
     needed real product/negotiation handling, not small talk").
  2. Explicit profile data:  `Customer.is_vip`, plus (as of the sector-
     agnostic generalization — see `customer_profile_extractor.py`)
     `Customer.extracted_profile`: a generic, low-hallucination-risk LLM
     extraction of budget/location/stated-need/urgency that now runs for
     every tenant's conversations, not just real estate. Real estate
     tenants additionally get the specialized `rag_engine/preference_
     extractor.py` (item 10, district/property_type/bedrooms) for the
     recommendations panel specifically — that one remains separate and
     unchanged; this formula only reads the generic extraction.
  3. Interaction history:  recency of last activity (decay), number of
     distinct contact sessions (repeat contact), and whether the VCard was
     ever opened (`Customer.vcard_opened_at`, migration 0013 — the closest
     honest proxy available, since WhatsApp has no "contact saved" event)
     or at least confirmed saved (`vcard_state == CONTACT_SAVED_VERIFIED`).

Weights (40% behavior / 25% profile / 35% history) are a genuine product
decision, not derived from the SRS (which specifies no formula at all — see
IMPLEMENTATION_STATUS.md's SRS-gap section). Flagged for sign-off there;
implemented now per the standing "pick the simplest reasonable default and
keep moving" instruction rather than blocking on it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

# Deliberately generic (not real-estate-specific) — a lesson from the Naeem
# test tenant, where real-estate-only classifier keywords left a non-real-
# estate tenant with zero signal. Arabic + English, price/availability/
# purchase-action words that plausibly indicate buying intent regardless of
# vertical.
_BUYING_INTENT_PATTERNS = [
    r"السعر", r"بكام", r"كام\s*سعر", r"كم\s*سعر", r"متوفر", r"متوفره",
    r"احجز", r"حجز", r"الدفع", r"ادفع", r"اشتري", r"اطلب", r"طلب",
    r"التوصيل", r"يوصل", r"خصم", r"عرض", r"ضمان",
    r"\bprice\b", r"\bcost\b", r"\bavailable\b", r"\bbuy\b", r"\border\b",
    r"\bdiscount\b", r"\bdelivery\b", r"\bwarranty\b",
]
_BUYING_INTENT_RE = re.compile("|".join(_BUYING_INTENT_PATTERNS), re.IGNORECASE)

_DEEP_TIERS = frozenset({"L2", "L3", "VAULT"})


class LeadTier(StrEnum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


@dataclass(frozen=True)
class CustomerSignals:
    """Real, per-customer aggregates pulled from the DB — nothing computed here."""
    is_vip: bool
    vcard_state: str
    vcard_opened_at: datetime | None
    conversation_count: int
    total_messages: int
    last_message_at: datetime | None
    reached_deep_tier: bool
    customer_message_texts: list[str]
    extracted_profile: dict | None = None


@dataclass(frozen=True)
class LeadScore:
    score: int  # 0-100
    tier: LeadTier
    conversation_behavior: int
    profile_data: int
    interaction_history: int
    buying_intent_hits: int


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _conversation_behavior_score(signals: CustomerSignals) -> tuple[int, int]:
    """Returns (component_score_0_100, buying_intent_hit_count)."""
    volume_score = _clamp(signals.total_messages * 4)  # 25 messages -> 100
    intent_hits = sum(
        1 for text in signals.customer_message_texts
        if text and _BUYING_INTENT_RE.search(text)
    )
    intent_score = _clamp(intent_hits * 20)  # 5 hits -> 100
    tier_bonus = 20 if signals.reached_deep_tier else 0
    component = _clamp(volume_score * 0.4 + intent_score * 0.4 + tier_bonus * 0.2)
    return round(component), intent_hits


def _profile_data_score(signals: CustomerSignals) -> int:
    # 30 points for being VIP, up to 70 spread across the four generic
    # extracted signals (budget, location, stated need, urgency) — each
    # worth its own points so the component genuinely varies with how much
    # a customer has actually revealed, instead of being a flat VIP-or-not
    # binary. A customer with nothing extracted and no VIP flag scores 0,
    # same as before this component existed.
    vip_points = 30.0 if signals.is_vip else 0.0

    profile = signals.extracted_profile or {}
    has_budget = profile.get("budget_min") is not None or profile.get("budget_max") is not None
    has_location = bool(profile.get("location"))
    has_need = bool(profile.get("stated_need"))
    has_urgency = bool(profile.get("urgency"))

    extraction_points = sum([
        20.0 if has_budget else 0.0,
        20.0 if has_location else 0.0,
        20.0 if has_need else 0.0,
        10.0 if has_urgency else 0.0,
    ])

    return round(_clamp(vip_points + extraction_points))


def _interaction_history_score(signals: CustomerSignals) -> int:
    # Recency: 60 at <=1 day, linearly decaying to 0 at 30 days, 0 if never.
    if signals.last_message_at is None:
        recency = 0.0
    else:
        last = signals.last_message_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        days_since = max(0.0, (datetime.now(tz=timezone.utc) - last).total_seconds() / 86400)
        recency = _clamp(60 * (1 - min(days_since, 30) / 30), 0, 60)

    repeat_contact = min(20.0, max(0, signals.conversation_count - 1) * 10)

    if signals.vcard_opened_at is not None:
        vcard_bonus = 20.0
    elif signals.vcard_state == "STATE_CONTACT_SAVED_VERIFIED":
        vcard_bonus = 10.0
    else:
        vcard_bonus = 0.0

    return round(_clamp(recency + repeat_contact + vcard_bonus))


def compute_lead_score(signals: CustomerSignals) -> LeadScore:
    behavior, intent_hits = _conversation_behavior_score(signals)
    profile = _profile_data_score(signals)
    history = _interaction_history_score(signals)

    final = round(_clamp(behavior * 0.40 + profile * 0.25 + history * 0.35))
    tier = LeadTier.HOT if final >= 70 else LeadTier.WARM if final >= 40 else LeadTier.COLD

    return LeadScore(
        score=final,
        tier=tier,
        conversation_behavior=behavior,
        profile_data=profile,
        interaction_history=history,
        buying_intent_hits=intent_hits,
    )


def _demo() -> None:
    """ponytail self-check: a cold new contact scores far below a hot repeat VIP buyer."""
    cold = CustomerSignals(
        is_vip=False, vcard_state="STATE_NEW", vcard_opened_at=None,
        conversation_count=1, total_messages=1, last_message_at=None,
        reached_deep_tier=False, customer_message_texts=["مرحبا"],
        extracted_profile=None,
    )
    hot = CustomerSignals(
        is_vip=True, vcard_state="STATE_CONTACT_SAVED_VERIFIED",
        vcard_opened_at=datetime.now(tz=timezone.utc),
        conversation_count=3, total_messages=30, last_message_at=datetime.now(tz=timezone.utc),
        reached_deep_tier=True,
        customer_message_texts=["كام السعر؟", "متوفر عندكم؟", "احجز لي واحد", "الدفع كاش ولا اونلاين؟"],
        extracted_profile={"budget_min": None, "budget_max": 500.0, "location": "الرياض",
                            "stated_need": "جهاز مساج للركبة", "urgency": True},
    )
    cold_result = compute_lead_score(cold)
    hot_result = compute_lead_score(hot)
    assert cold_result.tier == LeadTier.COLD, cold_result
    assert hot_result.tier == LeadTier.HOT, hot_result
    assert hot_result.score > cold_result.score
    assert hot_result.buying_intent_hits >= 3
    assert cold_result.profile_data == 0, "no VIP + no extraction must score 0"
    assert hot_result.profile_data == 30 + 20 + 20 + 20 + 10, "VIP + budget + location + need + urgency"
    print(f"cold={cold_result}\nhot={hot_result}\nself-check passed")


if __name__ == "__main__":
    _demo()

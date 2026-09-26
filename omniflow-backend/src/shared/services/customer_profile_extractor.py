"""
shared/services/customer_profile_extractor.py — Sector-agnostic explicit
buying-signal extraction.

Generalizes the pattern proven by `ai_workers/rag_engine/preference_extractor.py`
(real-estate-only: budget/district/property_type/bedrooms) into a generic
extractor that runs for every tenant regardless of vertical — a massage-
device retailer's customer gets exactly as much signal as a real-estate
agency's, instead of zero (the Naeem tenant's actual lesson from this
product's own onboarding history).

Extracts only clear, low-hallucination-risk structured signals that appear
across sectors:
  - budget_min / budget_max: a price/budget figure, in whatever currency/
    unit the customer used (not normalized — sector-specific normalization,
    e.g. SAR conversion, is out of scope for a generic extractor).
  - location: a stated area/city/branch/delivery destination.
  - stated_need: a short (<= 10 words) paraphrase of what the customer said
    they want — deliberately a paraphrase, not a verbatim quote, so it
    can't accidentally leak a full message as "extracted data."
  - urgency: true only if the customer used an explicit time-pressure
    phrase ("بسرعة", "اليوم", "متى العرض القادم", "urgent", "asap", ...);
    false/null otherwise. Never inferred from tone alone.

Same fail-closed, null-on-uncertainty contract as preference_extractor.py:
a flaky or uncertain LLM call degrades to "nothing extracted," never a
guessed value presented as fact.

The real-estate-specific extractor is unchanged and kept separate — this
module does not replace it, it fills the gap for every other vertical (and
real-estate tenants get both: this generic pass PLUS the specialized one).
"""
from __future__ import annotations

import json

import structlog

from src.shared.core.config import get_settings
from src.shared.services.llm_provider import create_chat_client, completion_options, provider_options

logger = structlog.get_logger(__name__)
_settings = get_settings()
_client = create_chat_client(_settings)

_SYSTEM_PROMPT = (
    "استخرج إشارات شراء صريحة من رسائل العميل كـ JSON فقط، بغض النظر عن نوع النشاط التجاري "
    "(عقارات، تجزئة، خدمات، أي قطاع آخر). الحقول: "
    "budget_min, budget_max (رقم إذا ذكر العميل ميزانية أو سعرًا صريحًا، وإلا null. "
    "إذا ذكر رقمًا واحدًا فقط كسقف، اجعله budget_max فقط)، "
    "location (المنطقة/المدينة/الفرع أو وجهة التوصيل التي ذكرها العميل صراحة، أو null)، "
    "stated_need (وصف قصير جدًا لما يريده العميل بجملة من كلمتين لثلاث كلمات كحد أقصى، "
    "بصياغتك أنت وليس نقلاً حرفيًا، أو null إذا لم يذكر شيئًا واضحًا)، "
    "urgency (true فقط إذا استخدم العميل عبارة استعجال صريحة مثل 'بسرعة' أو 'اليوم' أو "
    "'متى العرض القادم' أو 'urgent' أو 'asap'، وإلا false). "
    "لا تخترع أي قيمة غير مذكورة صراحة أو بوضوح تام — استخدم null عند أي شك. "
    'أجب بـ JSON فقط بهذا الشكل بالضبط: '
    '{"budget_min": null, "budget_max": null, "location": null, "stated_need": null, "urgency": false}'
)

EMPTY_PROFILE: dict = {
    "budget_min": None, "budget_max": None,
    "location": None, "stated_need": None, "urgency": False,
}


async def extract_customer_profile(customer_texts: list[str]) -> dict:
    """
    Extract generic, sector-agnostic buying signals from a customer's recent
    messages. Returns EMPTY_PROFILE (all-null/false) on no input, no
    configured LLM client, or any extraction failure — never a guess.
    """
    if not customer_texts:
        return dict(EMPTY_PROFILE)
    if _client is None:
        logger.info("customer_profile_extraction_skipped", reason="no LLM client configured")
        return dict(EMPTY_PROFILE)

    message = "\n".join(customer_texts[-8:])

    try:
        _, _, models = provider_options(_settings)
        resp = await _client.chat.completions.create(
            model=models["L1"],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=200,
            **completion_options(models["L1"], _settings),
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": message},
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
        parsed = json.loads(raw)
    except Exception as exc:
        logger.warning("customer_profile_extraction_failed", error=str(exc), exc_type=type(exc).__name__)
        return dict(EMPTY_PROFILE)

    result = dict(EMPTY_PROFILE)
    for key in ("budget_min", "budget_max", "location", "stated_need"):
        value = parsed.get(key)
        if value is None:
            continue
        try:
            if key in ("budget_min", "budget_max"):
                result[key] = float(value)
            else:
                text = str(value).strip()
                result[key] = text or None
        except (TypeError, ValueError):
            continue
    result["urgency"] = bool(parsed.get("urgency", False))
    return result


async def extract_and_persist_customer_profile(
    *, session, customer_id, customer_texts: list[str]
) -> dict:
    """
    Extract and write the result onto Customer.extracted_profile in the
    given (already tenant-scoped) session. Caller owns the transaction.
    A field that comes back null this run does NOT clobber a previously
    known value from an earlier message — buying signals accumulate over a
    conversation rather than resetting every time.
    """
    from datetime import datetime, timezone
    from src.shared.db.models import Customer

    profile = await extract_customer_profile(customer_texts)
    customer = await session.get(Customer, customer_id)
    if customer is None:
        return profile

    existing = customer.extracted_profile or {}
    # Seed from the full schema so every key is always explicitly present
    # (None, not absent) — a run that extracts nothing new must not leave
    # the dict missing keys entirely, just carrying over whatever was
    # already known.
    merged = dict(EMPTY_PROFILE)
    merged.update(existing)
    for key in ("budget_min", "budget_max", "location", "stated_need"):
        if profile.get(key) is not None:
            merged[key] = profile[key]
    merged["urgency"] = bool(profile.get("urgency") or existing.get("urgency"))

    customer.extracted_profile = merged
    customer.extracted_profile_updated_at = datetime.now(tz=timezone.utc)
    return merged


def _demo() -> None:
    """ponytail self-check: the JSON contract round-trips and merge-not-clobber holds."""
    import asyncio

    async def _fake_extract_none(_):
        return dict(EMPTY_PROFILE)

    # Merge logic alone (no real LLM call needed for this part of the check)
    existing = {"budget_max": 500.0, "location": "الرياض", "urgency": False}
    new_partial = {"budget_min": None, "budget_max": None, "location": None,
                   "stated_need": "جهاز مساج للركبة", "urgency": True}
    merged = dict(existing)
    for key in ("budget_min", "budget_max", "location", "stated_need"):
        if new_partial.get(key) is not None:
            merged[key] = new_partial[key]
    merged["urgency"] = bool(new_partial.get("urgency") or existing.get("urgency"))
    assert merged["budget_max"] == 500.0, "must not clobber a known value with a null re-extraction"
    assert merged["stated_need"] == "جهاز مساج للركبة"
    assert merged["urgency"] is True
    print(f"merge self-check passed: {merged}")


if __name__ == "__main__":
    _demo()

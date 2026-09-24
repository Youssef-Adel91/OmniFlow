"""
ai_workers/rag_engine/preference_extractor.py — Explicit preference extraction
for real-estate recommendations (item 10 sign-off decision).

Before this, the recommendations endpoint just concatenated the customer's
last 5 raw messages and embedded that blob as the Qdrant query — accurate
enough for semantic "what are they talking about" matching, but useless for
the two signals that actually decide whether a listing is a real match:
budget and location. A customer who writes "شقة رخيصة في حي النرجس تحت
مليون ونص" wants a hard price ceiling and a specific district, not just
"something semantically similar to this sentence."

This extracts {budget_min, budget_max, district, property_type, bedrooms}
via a single cheap, low-temperature JSON-mode LLM call (same tier/pattern
as ai_engine/llm_orchestrator.py's `_classify_intent`), so the endpoint can
build a real Qdrant price/location filter instead of relying purely on
text-similarity ranking.

Fails closed to "nothing extracted" on any error — the caller always falls
back to the old raw-text-query behavior, so a flaky LLM call degrades
gracefully instead of breaking the recommendations panel.
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
    "استخرج تفضيلات العميل العقارية من رسائله كـ JSON فقط. "
    "الحقول: budget_min, budget_max (أرقام بالريال السعودي أو null)، "
    "district (اسم الحي/المنطقة أو null)، "
    "property_type (واحد من: apartment, villa, land, office, commercial أو null)، "
    "bedrooms (عدد صحيح أو null). "
    "إذا ذكر العميل رقم واحد فقط للميزانية (مثل \"تحت مليون\")، اجعله budget_max. "
    "لا تخترع قيم غير مذكورة صراحة أو ضمنيًا بوضوح — استخدم null لأي حقل غير واضح. "
    'أجب بـ JSON فقط بهذا الشكل بالضبط: '
    '{"budget_min": null, "budget_max": null, "district": null, "property_type": null, "bedrooms": null}'
)

_EMPTY_PREFERENCES: dict = {
    "budget_min": None, "budget_max": None,
    "district": None, "property_type": None, "bedrooms": None,
}


async def extract_preferences(customer_texts: list[str]) -> dict:
    """
    Extract structured real-estate preferences from a customer's recent
    messages. Returns a dict with keys budget_min/budget_max/district/
    property_type/bedrooms, each None if not mentioned or on any failure.
    """
    if not customer_texts:
        return dict(_EMPTY_PREFERENCES)
    if _client is None:
        logger.info("preference_extraction_skipped", reason="no LLM client configured")
        return dict(_EMPTY_PREFERENCES)

    message = "\n".join(customer_texts[-5:])

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
        logger.warning("preference_extraction_failed", error=str(exc), exc_type=type(exc).__name__)
        return dict(_EMPTY_PREFERENCES)

    result = dict(_EMPTY_PREFERENCES)
    for key in result:
        value = parsed.get(key)
        if value is None:
            continue
        try:
            if key in ("budget_min", "budget_max"):
                result[key] = float(value)
            elif key == "bedrooms":
                result[key] = int(value)
            else:
                result[key] = str(value).strip() or None
        except (TypeError, ValueError):
            continue
    return result

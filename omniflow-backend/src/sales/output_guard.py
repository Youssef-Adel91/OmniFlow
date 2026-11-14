"""
Output guard: the last line of defence between the model's answer and the customer (sales-closing tenants).

The rule layer in front of the model (hook.before_llm) cannot stop a model that, despite the hard rules in its prompt, invents a price, quotes a listing that does not exist,
promises a discount, hands out a phone number or recites its hidden instructions. `forbidden_findings` is pure and is also what `scripts/eval_bot.py --live` measures;
`vet_answer` is what the LLM invoker applies to every answer of a tenant with the sales assistant on.
"""
from __future__ import annotations

import re

SAFE_FALLBACK_AR = "خليني أتأكد من التفاصيل مع أحد مستشارينا ويرجع لك بالمعلومة الدقيقة بإذن الله."


_AR = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")
_PRICE = re.compile(r"(\d{1,3}(?:[,.]\d{3})+|\d{4,})\s*(?:ريال|ر\.?\s?س|sar|riyal)", re.I)
_AD = re.compile(r"(?:رقم\s*(?:الإعلان|الاعلان|الترخيص)|ad\s*(?:number|no\.?)|license)\D{0,6}(\d{6,})", re.I)
_PHONE = re.compile(r"(?<!\d)(?:\+?966|00966|0)?5\d{8}(?!\d)")
_DISCOUNT = re.compile(r"خصم|تخفيض|تنزيل|ننزل|انزل|أنزل|discount|price cut", re.I)
_REFUSAL = re.compile(r"لا\s+(?:أستطيع|استطيع|يمكن|نقدم|نقدر|أقدر|اقدر)|ما\s+(?:أقدر|اقدر|نقدر|عندي\s+صلاحية|أستطيع)|غير\s+(?:متاح|متوفر)|لا\s+توجد|ما\s+في|cannot|can't|unable|not able", re.I)
_LEAK = ("سياق المبيعات", "قواعد صارمة", "===", "system prompt", "تعليماتي هي")


def _prices(text: str) -> set[int]:
    return {int(re.sub(r"[,.]", "", m.group(1))) for m in _PRICE.finditer(text)}


def forbidden_findings(answer: str, *, allowed_prices: set[int], allowed_ads: set[str]) -> list[str]:
    """Names of the rules `answer` breaks (empty list = clean). Pure."""
    out: list[str] = []
    text = (answer or "").translate(_AR)
    if any(p >= 1000 and p not in allowed_prices for p in _prices(text)):
        out.append("invented_price")
    if any(m.group(1) not in allowed_ads for m in _AD.finditer(text)):
        out.append("invented_ad_number")
    for sentence in re.split(r"[.!؟?\n،]", text):
        if _DISCOUNT.search(sentence) and not _REFUSAL.search(sentence):
            out.append("discount_promise")
            break
    if any(marker in text for marker in _LEAK):
        out.append("prompt_leak")
    if _PHONE.search(text):
        out.append("phone_leak")
    return out


def _numbers_in(*texts: str) -> tuple[set[int], set[str]]:
    """Every price-looking number and every long id in the context the model was given (inventory block, retrieved documents, the customer's own words)."""
    prices: set[int] = set()
    ads: set[str] = set()
    for t in texts:
        t = (t or "").translate(_AR)
        for m in re.finditer(r"\d{1,3}(?:[,.]\d{3})+|\d{4,}", t):
            digits = re.sub(r"[,.]", "", m.group(0))
            prices.add(int(digits))
            if len(digits) >= 6:
                ads.add(digits)
    return prices, ads


def vet_answer(answer: str, *, context: str) -> list[str]:
    """Findings for `answer` given the full context the model saw (system prompt + inventory + retrieved text + customer words). Empty list = send it."""
    prices, ads = _numbers_in(context)
    found = forbidden_findings(answer, allowed_prices=prices, allowed_ads=ads)
    if "phone_leak" in found:                          # the company's OWN number may be in its context (contact details it is meant to share)
        ctx_digits = re.sub(r"\D", "", (context or "").translate(_AR))
        mine = [re.sub(r"\D", "", m.group(0))[-9:] for m in _PHONE.finditer((answer or "").translate(_AR))]
        if all(n in ctx_digits for n in mine):
            found.remove("phone_leak")
    return found

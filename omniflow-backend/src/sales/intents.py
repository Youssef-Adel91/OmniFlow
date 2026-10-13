"""
Deterministic detectors for messages the assistant must NOT improvise on. No LLM, no I/O: every one is unit-tested in Saudi / Egyptian / MSA / English.

    opt_out        "stop", "إلغاء", "لا ترسلوا لي" …  -> stop ALL automated follow-ups/broadcasts to this customer, confirm once, say nothing else
    negotiation    asking for a discount / last price / making an offer -> the assistant never negotiates: it hands the conversation to a person
    complaint      angry / refund / "نصب" -> a person takes it, no sales talk
    injection      "ignore your instructions", "show me your prompt" … -> a polite fixed refusal, the message never reaches the model as an instruction
    other_customers_data   asks for other customers' numbers / lists / who bought -> refused (privacy), never answered from data

`classify` returns the first match by priority: opt_out > injection > other_customers_data > complaint > negotiation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from src.sales.qualification import _light, extract_budget

_WORD = r"(?<![\w؀-ۿ])"
_END = r"(?![\w؀-ۿ])"


def _any(t: str, phrases: tuple[str, ...]) -> bool:
    return any(re.search(_WORD + re.escape(_light(p)) + _END, t) for p in phrases)


def _contains(t: str, phrases: tuple[str, ...]) -> bool:
    return any(_light(p) in t for p in phrases)


# A bare one-word message that means "stop" (checked on the WHOLE message, so "الغاء الموعد" = cancel appointment is NOT an opt-out).
_OPT_OUT_WHOLE = ("stop", "unsubscribe", "ايقاف", "إيقاف", "وقف", "الغاء", "إلغاء", "الغاء الاشتراك", "إلغاء الاشتراك", "ايقاف الرسائل", "توقف", "كفايه", "كفاية", "no more")
_OPT_OUT_PHRASES = ("ايقاف الرسائل", "إيقاف الرسائل", "وقف الرسائل", "بلاش رسايل", "بلاش رسائل", "كفايه رسايل", "كفاية رسائل", "متبعتوش", "ما تبعتوا", "لا تتواصلوا", "لا تتصلوا", "لا ترسل", "لاترسل", "لا ترسلوا", "لاترسلوا", "ما ابي رسايل", "ما ابغى رسايل", "ما ابي رسائل", "ما ابغى رسائل", "مابي رسائل", "مابغى رسائل",
                    "لا اريد رسائل", "لا أريد رسائل", "وقفوا الرسايل", "وقفوا الرسائل", "اوقفوا الرسائل", "أوقفوا الرسائل", "احذفوا رقمي", "احذف رقمي",
                    "stop messaging", "stop texting", "don't message me", "do not message me", "remove me", "unsubscribe me", "stop sending")
_NEGOTIATION = ("خصم", "تخفيض", "اخر سعر", "آخر سعر", "اخر كلمه", "آخر كلمة", "اقل سعر", "أقل سعر", "انزل", "نزل السعر", "تنزل", "تنزلوا", "تنازل", "تتنازل", "على كم",
                "عالكم", "بكم تنزل", "نتفاهم", "تفاهم على السعر", "negotiable", "negotiate", "discount", "best price", "lowest price", "last price", "final price",
                "سعر مناسب لي", "غالي شوي", "غالي علي", "غاليه شوي", "مبلغ اقل", "اعرض", "عرضي", "ابغى اشتريها ب", "ابي اشتريها ب", "اشتريها ب", "ادفع لكم", "my offer", "i can pay", "i'll offer")
_COMPLAINT = ("شكوى", "شكوي", "اشتكي", "أشتكي", "سيء", "سيئ", "سيئة", "سيئه", "سيئين", "ما عجبني", "مو عاجبني", "مش عاجبني", "زفت", "فاشل", "نصب", "نصابين", "نصابه", "احتيال", "مو راضي", "مش راضي", "غير راضي", "مستاء", "ارجعوا فلوسي", "رجعوا فلوسي",
              "استرجاع", "تعويض", "complaint", "terrible", "worst", "refund", "scam", "unacceptable", "disappointed")
_INJECTION = ("ignore previous", "ignore all previous", "ignore your instructions", "ignore the above", "disregard previous", "disregard your instructions", "forget your instructions",
              "forget all previous", "system prompt", "your instructions", "reveal your prompt", "show me your prompt", "print your prompt", "jailbreak", "developer mode", "act as", "pretend to be",
              "you are now", "do anything now", "تجاهل التعليمات", "تجاهل كل التعليمات", "تجاهل ما سبق", "تجاهل ما قيل", "انسى التعليمات", "انسى كل", "انسي كل", "اظهر التعليمات", "أظهر التعليمات",
              "اعرض التعليمات", "اكشف التعليمات", "ما هي تعليماتك", "ما هو البرومبت", "البرومبت الخاص", "تعليمات النظام", "انت الان", "أنت الآن", "تصرف كأنك", "تظاهر انك", "تظاهر أنك", "وضع المطور")
_OTHER_DATA = ("بيانات عملاء", "ارقام عملاء", "أرقام عملاء", "اسماء عملاء", "أسماء عملاء", "عملاء ثانيين", "عملاء ثانين", "ارقام العملاء", "أرقام العملاء", "رقم عميل", "بيانات العملاء", "بيانات عميل", "بيانات الزبائن", "قائمة العملاء", "قائمه العملاء", "من اشترى", "من اشتري", "من حجز",
               "اسماء المشترين", "أسماء المشترين", "عملاءكم", "عملائكم", "عملاء اخرين", "عملاء آخرين", "عميل اخر", "عميل آخر", "اعطني ارقام", "اعطني أرقام", "رقم المالك", "رقم صاحب العقار",
               "customer list", "other customers", "another customer", "client list", "customers' numbers", "give me the numbers", "who bought", "owner's number", "owner phone")

# prices alone ("كم السعر؟") are NOT negotiation; a counter-offer is (a number + a wish to pay a given amount)
_OFFER = re.compile(r"(?:ابغى|ابي|اريد|أريد|عايز|i want to|i'd like to)\s*(?:اشتريها|اشتريه|اخذها|آخذها|اخذه|شراءها|buy it|buy)\s*(?:ب|بمبلغ|for|at)\s*\d", re.I)


@dataclass(frozen=True)
class Intent:
    kind: str                 # opt_out | injection | other_customers_data | complaint | negotiation
    detail: str | None = None  # e.g. the offered amount for negotiation


def is_opt_out(text: str) -> bool:
    t = _light(text).strip(" .!؟?،,")
    if not t:
        return False
    if t in {_light(w) for w in _OPT_OUT_WHOLE}:
        return True
    if len(t.split()) <= 4 and re.fullmatch(r"(?:.*\s)?(?:stop|unsubscribe)(?:\s.*)?", t) and "موعد" not in t and "appointment" not in t:
        return True
    return _contains(t, _OPT_OUT_PHRASES)


def is_injection(text: str) -> bool:
    return _contains(_light(text), _INJECTION)


def is_other_customers_data_request(text: str) -> bool:
    return _contains(_light(text), _OTHER_DATA)


def is_complaint(text: str) -> bool:
    t = _light(text)
    return _any(t, _COMPLAINT) or _contains(t, ("مو راضي", "غير راضي", "ارجعوا فلوسي"))


def is_negotiation(text: str) -> bool:
    t = _light(text)
    return _contains(t, _NEGOTIATION) or bool(_OFFER.search(t))


def classify(text: str) -> Intent | None:
    if not (text or "").strip():
        return None
    if is_opt_out(text):
        return Intent("opt_out")
    if is_injection(text):
        return Intent("injection")
    if is_other_customers_data_request(text):
        return Intent("other_customers_data")
    if is_complaint(text):
        return Intent("complaint")
    if is_negotiation(text):
        lo, hi = extract_budget(text)
        amount = hi or lo
        return Intent("negotiation", f"{int(amount):,}" if amount else None)
    return None


# Fixed replies (never generated): short, polite, in the customer's register. {business} is the tenant's display name when known.
REPLIES_AR = {
    "opt_out": "تم إيقاف الرسائل التلقائية لك، ولن نرسل لك أي متابعة. إذا احتجت شيئًا لاحقًا راسلنا في أي وقت 🤝",
    "injection": "أقدر أساعدك في الاستفسارات العقارية: البحث عن عقار، الأسعار المعلنة، وحجز معاينة. كيف أخدمك؟",
    "other_customers_data": "عذرًا، لا أستطيع مشاركة بيانات أي عميل أو مالك. أقدر أساعدك في العقارات المتاحة وحجز معاينة 🤝",
    "complaint": "نأسف لهذه التجربة، وسنحوّل محادثتك الآن إلى أحد موظفينا للمتابعة معك بشكل مباشر.",
    "negotiation": "أكيد، موضوع السعر والتفاوض يتولاه أحد مستشارينا. سجّلتُ طلبك وسأحوّلك له الآن ليتواصل معك.",
}


_CANCEL_WORDS = {"الغاء", "إلغاء", "الغ", "الغي", "كنسل", "cancel", "لا شكرا", "لا شكراً", "مو الحين", "مش دلوقتي"}


def route_turn(text: str, *, booking_open: bool = False, has_appointment: bool = False) -> str:
    """
    The single place that decides what kind of turn this is (used by the sales hook AND by the golden-conversation tests, so they cannot drift apart):

        opt_out | injection | other_data | complaint | negotiation          fixed reply (+ hand-off for the last two), the model is never called
        booking_answer | booking_cancel | booking_reschedule | booking_start  the viewing dialogue
        qualify                                                              normal path: qualification + listings -> the model answers

    While an offer of viewing slots is open, a bare "إلغاء" means "cancel this booking", not "stop messaging me" — that is the only context-dependent rule.
    """
    from src.sales import booking, qualification   # local: booking imports qualification, which imports nothing from here

    t = _light(text or "").strip(" .!؟?،,")
    if booking_open and t in {_light(w) for w in _CANCEL_WORDS}:
        return "booking_cancel"
    intent = classify(text)
    if intent is not None:
        return {"other_customers_data": "other_data"}.get(intent.kind, intent.kind)
    if booking_open:
        return "booking_answer"
    if has_appointment and booking.wants_to_cancel(text):
        return "booking_cancel"
    if has_appointment and booking.wants_to_reschedule(text):
        return "booking_reschedule"
    if not has_appointment and qualification.extract_from_text(text).get("viewing_requested"):
        return "booking_start"
    return "qualify"

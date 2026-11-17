"""
How the sales assistant TALKS (pure, no I/O, unit-tested in Saudi / Egyptian / MSA / English). Four rules that the first live evaluation against a real model found missing:

    1. SALAM    answer with a salam only if the customer opened with one; never "وعليكم السلام" to a message that had no salam.
    2. PAUSE    a customer who asks for time or objects ("اسأل زوجتي وأرجع لك", "بفكر", "شركة ثانية عرضوا علي", "بس أتفرج") gets a short reply and NO question.
    3. VARIETY  every qualification question has several phrasings, rotated; a sentence the assistant already said in this conversation is never repeated word for word.
    4. FOCUS    no specific listing in the first message unless the customer gave a district or a budget: one question first (see matcher.match(require_focus=...)).

`addendum` instructions are produced in hook.build_addendum; `polish` is applied to the model's ANSWER by the LLM invoker as a deterministic safety net (the model does not always obey);
`addendum_violations` / `behaviour_findings` are what the golden tests and `scripts/eval_bot.py` check.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")


def _norm(text: str | None) -> str:
    t = (text or "").translate(_AR_DIGITS).lower()
    t = re.sub("[أإآٱ]", "ا", t).replace("ى", "ي").replace("ة", "ه")
    t = re.sub(r"[ً-ْـ]", "", t)
    return re.sub(r"\s+", " ", t).strip()


# ── 1. greetings ─────────────────────────────────────────────────────────────────────────────────────────────────
_LEAD = r"^[\s\W_]*"
_SALAM = re.compile(_LEAD + r"(?:و?عليكم\s+)?(?:ال)?سلام(?:\s+عليكم)?|" + _LEAD + r"(?:assalam\w*|salam\w*|as-?salam\w*|peace be upon you)", re.I)
_OTHER_GREETING = re.compile(_LEAD + r"(?:مرحبا\w*|اهلا\w*|هلا\w*|هاي|صباح\s+\w+|مساء\s+\w+|حياك\w*|يعطيك العافيه|hi\b|hello\b|hey\b|good\s+(?:morning|evening|afternoon))", re.I)
_SALAM_OPENER = re.compile(r"^[\s\W_]*(?:و?عليكم\s+(?:ال)?سلام(?:\s+و?رحمه\s+الله(?:\s+و?بركاته)?)?|(?:ال)?سلام\s+عليكم(?:\s+و?رحمه\s+الله(?:\s+و?بركاته)?)?|peace be upon you)[\s،,.!:\-–]*", re.I)


def opens_with_salam(text: str | None) -> bool:
    return bool(_SALAM.match(_norm(text)))


def opens_with_greeting(text: str | None) -> bool:
    t = _norm(text)
    return bool(_SALAM.match(t) or _OTHER_GREETING.match(t))


def fix_greeting(answer: str, customer_text: str | None) -> str:
    """Drop a leading salam from the ANSWER when the customer did not start with one (never returns an empty answer)."""
    if not answer or opens_with_salam(customer_text):
        return answer
    probe = _norm(answer)
    m = _SALAM_OPENER.match(probe)
    if not m:
        return answer
    # `probe` is normalised; cut the same number of leading words from the original
    words_dropped = len(probe[: m.end()].split())
    original_words = answer.split()
    rest = " ".join(original_words[words_dropped:]).lstrip(" ،,.!:-–\n")
    return rest or answer


# ── 2. pauses / objections ───────────────────────────────────────────────────────────────────────────────────────
_PAUSE = ("اسال زوجتي", "اسال زوجتى", "اسال اهلي", "اسال والدي", "اسال الوالد", "استشير", "اتشاور", "بشاور", "ارجع لك", "ارجعلك", "ارجع لكم", "برجع لك", "برجعلك",
          "بفكر", "خلني افكر", "خليني افكر", "افكر فيها", "افكر شوي", "بعدين", "لاحقا", "مو الحين", "مو الان", "مش دلوقتي", "مش دلوقت", "مو مقتنع", "مش مقتنع", "مب مقتنع",
          "شركه ثانيه", "شركه اخرى", "عند غيركم", "عرضوا علي", "عرضو علي", "بس ادور", "بس اتفرج", "ادور واتفرج", "بس شوف", "اتفرج بس",
          "let me think", "i'll think", "i will think", "ask my wife", "ask my husband", "talk to my wife", "get back to you", "come back to you", "not now", "maybe later",
          "not convinced", "another company", "other company", "just browsing", "just looking")


def is_pause_or_objection(text: str | None) -> bool:
    t = _norm(text)
    return bool(t) and any(p in t for p in _PAUSE)


PAUSE_REPLIES_AR = ("خذ راحتك، وإذا احتجت أي شيء أنا موجود.", "تمام، فكّر على راحتك وأنا بخدمتك وقت ما تحب.", "حياك الله، لا استعجال. ارجع لي متى ما حبيت.",
                    "على راحتك، ونحن جاهزين متى ما قررت.")


# ── 3. variety ───────────────────────────────────────────────────────────────────────────────────────────────────
QUESTION_VARIANTS_AR: dict[str, tuple[str, ...]] = {
    "budget": ("كم الميزانية التقريبية اللي تفكر فيها؟", "وش الميزانية اللي تناسبك تقريبًا؟", "في أي حدود ميزانيتك عشان أرشّح لك الأنسب؟"),
    "district": ("في أي حي أو منطقة تفضّل؟", "وين تحب يكون العقار؟", "عندك حي معين في بالك؟"),
    "property_type": ("تدوّر على شقة، فيلا، أرض، أو غير ذلك؟", "وش نوع العقار اللي تبغاه؟", "تفضّل شقة ولا فيلا ولا شيء ثاني؟"),
    "purpose": ("الشراء للسكن أو للاستثمار؟", "العقار لك تسكنه ولا للاستثمار؟", "تبغاه سكن لك ولا استثمار؟"),
    "financing": ("الدفع كاش أو عن طريق تمويل بنكي؟", "تفكر تدفع كاش ولا بتمويل بنكي؟", "طريقة الدفع اللي تناسبك كاش ولا تمويل؟"),
    "rooms": ("كم عدد الغرف اللي تحتاجها؟", "كم غرفة تبغى تقريبًا؟", "عدد الغرف المناسب لك كم؟"),
}


def pick_question(field: str, rotation: int) -> str:
    variants = QUESTION_VARIANTS_AR[field]
    return variants[rotation % len(variants)]


_SENTENCE = re.compile(r"[^.!؟?\n]+[.!؟?]*")


def _key(sentence: str) -> str:
    return re.sub(r"[\s\W_]+", " ", _norm(sentence)).strip()


def strip_repeated_sentences(answer: str, previous: Iterable[str]) -> str:
    """Remove sentences the assistant already said, word for word, earlier in this conversation (sentences with a number — a price, an ad number — are information, not filler, and stay)."""
    seen = {_key(s) for text in previous for s in _SENTENCE.findall(text or "") if len(_key(s)) >= 12}
    if not seen:
        return answer
    kept = []
    for s in _SENTENCE.findall(answer):
        if len(_key(s)) >= 12 and _key(s) in seen and not re.search(r"\d", s.translate(_AR_DIGITS)):
            continue
        kept.append(s)
    out = " ".join(x.strip() for x in kept).strip()
    return out or answer


def polish(answer: str, *, customer_text: str | None, previous: Iterable[str] = ()) -> str:
    """Deterministic clean-up of the model's answer: no unprompted salam, no sentence repeated from earlier turns."""
    return strip_repeated_sentences(fix_greeting(answer, customer_text), previous)


# ── 4 + checks ───────────────────────────────────────────────────────────────────────────────────────────────────
def needs_focus(q: dict[str, Any]) -> bool:
    """True while the customer has given neither a district nor a budget (a property type alone is not enough to start recommending)."""
    return not (q.get("district") or q.get("budget_min") or q.get("budget_max"))


_FIELD_ASKED = {"budget": re.compile(r"ميزاني|budget", re.I), "district": re.compile(r"اي حي|في اي منطقه|وين تحب|حي معين|which (?:district|area)|what area", re.I),
                "rooms": re.compile(r"كم غرف|عدد الغرف|how many (?:bed)?rooms", re.I)}


def _known(q: dict[str, Any], field: str) -> bool:
    return bool(q.get("budget_min") or q.get("budget_max")) if field == "budget" else bool(q.get(field))


def behaviour_findings(answer: str, *, customer_line: str, qual: dict[str, Any], first_contact: bool = False) -> list[str]:
    """Behaviour defects in a model ANSWER (used by `scripts/eval_bot.py --live`)."""
    out: list[str] = []
    a = _norm(answer)
    if _SALAM_OPENER.match(a) and not opens_with_salam(customer_line):
        out.append("salam_unprompted")
    questions = [s for s in _SENTENCE.findall(answer) if s.strip().endswith(("؟", "?"))]
    if is_pause_or_objection(customer_line) and questions:
        out.append("question_after_pause")
    for field, rx in _FIELD_ASKED.items():
        if _known(qual, field) and any(rx.search(_norm(s)) for s in questions):
            out.append(f"asked_known_{field}")
    if first_contact and needs_focus(qual) and re.search(r"\d[\d,.]*\s*(?:ريال|sar)", answer.translate(_AR_DIGITS), re.I):
        out.append("premature_listing")
    if len(questions) > 1:
        out.append("more_than_one_question")
    return out


def addendum_violations(addendum: str, *, customer_line: str, qual: dict[str, Any], first_contact: bool = True) -> list[str]:
    """Rules the PROMPT must carry for this customer line (used by the golden tests and by `scripts/eval_bot.py` offline)."""
    bad: list[str] = []
    salam, greeted = opens_with_salam(customer_line), opens_with_greeting(customer_line)
    if not greeted and "لا تبدأ ردك بسلام" not in addendum:
        bad.append("missing_no_greeting_rule")
    if salam and "وعليكم السلام" not in addendum:
        bad.append("missing_salam_reply_rule")
    if greeted and not salam and "لا تقل «وعليكم السلام»" not in addendum:
        bad.append("missing_no_salam_for_plain_greeting")
    pause = is_pause_or_objection(customer_line)
    if pause and ("ولا تطرح أي سؤال" not in addendum or "في نهاية ردك اسأل" in addendum):
        bad.append("question_requested_after_pause")
    if not pause and "في نهاية ردك اسأل" in addendum:
        quoted = re.search(r"«([^»]+)»", addendum.split("في نهاية ردك اسأل", 1)[1])
        for field, variants in QUESTION_VARIANTS_AR.items():
            if _known(qual, field) and quoted and quoted.group(1) in variants:
                bad.append(f"asks_known_{field}")
    if first_contact and needs_focus(qual) and "لا تعرض أي عقار محدد" not in addendum:
        bad.append("missing_focus_rule")
    if "لا تكرر نفس الجملة" not in addendum:
        bad.append("missing_variety_rule")
    return bad

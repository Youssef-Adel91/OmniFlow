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
_PAUSE = ("اسال زوجتي", "اسال زوجتى", "اسال اهلي", "اسال والدي", "اسال الوالد", "استشير", "اتشاور", "بشاور", "ارجع لك", "ارجعلك", "ارجع لكم", "برجع لك", "برجعلك", "هفكر", "هشاور", "هرجع لك", "هرجعلك", "هرد عليك", "سافكر", "سأفكر",
          "بفكر", "خلني افكر", "خليني افكر", "افكر فيها", "افكر شوي", "بعدين", "لاحقا", "مو الحين", "مو الان", "مش دلوقتي", "مش دلوقت", "مو مقتنع", "مش مقتنع", "مب مقتنع",
          "بس ادور", "بس اتفرج", "ادور واتفرج", "بس شوف", "اتفرج بس",
          "let me think", "i'll think", "i will think", "ask my wife", "ask my husband", "talk to my wife", "get back to you", "come back to you", "not now", "maybe later",
          "not convinced", "just browsing", "just looking")


def is_pause_or_objection(text: str | None) -> bool:
    t = _norm(text)
    return bool(t) and any(p in t for p in _PAUSE)


PAUSE_REPLIES_AR = ("خذ راحتك، وإذا احتجت أي شيء أنا موجود.", "تمام، فكّر على راحتك وأنا بخدمتك وقت ما تحب.", "حياك الله، لا استعجال. ارجع لي متى ما حبيت.",
                    "على راحتك، ونحن جاهزين متى ما قررت.")


# ── 3. variety ───────────────────────────────────────────────────────────────────────────────────────────────────
QUESTION_VARIANTS_AR: dict[str, tuple[str, ...]] = {
    "budget": ("وش ميزانيتك تقريبًا؟", "كم تبي تصرف؟", "وش الحدود اللي تناسبك؟"),
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


def polish(answer: str, *, customer_text: str | None, previous: Iterable[str] = (), context: str | None = "", districts: Iterable[str] = (), first_reply: bool = False) -> str:
    """Deterministic clean-up of the model's answer: no unprompted salam, no sentence repeated from earlier turns, no promise of a service the office has not confirmed
    (`context` = the office's own persona/profile), one question at most, no «ما هي الميزانية», «شقة النرجس» not «الشقة النرجس», and no Hai'a ad number in a first reply
    nobody asked it of."""
    text = fix_greeting(answer, customer_text)
    text = strip_repeated_sentences(text, previous)
    text = soften_service_claims(text, context)
    text = limit_questions(text)
    text = fix_grammar(text)
    text = fix_type_district(text, districts)
    if first_reply and not asks_for_ad_number(customer_text):
        text = strip_ad_numbers(text)
    return tidy_listing_format(text)


_DASH_SEP = re.compile(r"\s[-–—]\s")
_LINE_BULLET = re.compile(r"(?m)^[ \t]*[-–—*•][ \t]+")


def tidy_listing_format(text: str) -> str:
    """A reply that offers properties came out as ONE line with mixed dashes («- شقة…، 850,000 ريال - مساحة…»). Make it readable:
    a text that starts with a dash or holds several « - » separators and states a price is rewritten into one line per property (when the dash separates properties)
    or into plain comma-separated phrases; a leading dash is removed. Text without a price, or with no stray dash, is untouched."""
    if not text or not _PRICE_IN_ANSWER.search(text):
        return text
    stripped = text.strip()
    inline = len(_DASH_SEP.findall(stripped))
    starts_dash = bool(re.match(r"[-–—*•]\s", stripped))
    if inline < 2 and not (starts_dash and inline >= 1) and not (starts_dash and "\n" not in stripped):
        return text
    if "\n" in stripped:                                   # already one item per line: only make the bullets uniform and drop dashes inside a line
        out = []
        for ln in stripped.split("\n"):
            bullet = bool(_LINE_BULLET.match(ln))
            body = _DASH_SEP.sub("، ", _LINE_BULLET.sub("", ln, count=1))
            out.append(("• " + body) if bullet else body)
        return "\n".join(out)
    body = re.sub(r"^[-–—*•]\s+", "", stripped)
    parts = [p.strip(" ،") for p in _DASH_SEP.split(body) if p.strip(" ،")]
    # a part that begins with a property noun starts a new property; other parts are attributes of the previous one
    items: list[str] = []
    for p in parts:
        if not items or re.match(rf"(?:\d+[).]\s*)?(?:ال)?(?:{_TYPE_NOUNS})\b", p):
            items.append(p)
        else:
            items[-1] += "، " + p
    return "\n".join(("• " + i) if len(items) > 1 else i for i in items) if len(items) > 1 else items[0]


# ── price questions: answer the price FIRST, from the inventory ──────────────────────────────────────────────────
_PRICE_Q = ("كم سعر", "كم السعر", "كام السعر", "كام سعر", "كم اسعار", "بكم", "بكام", "وش سعر", "وش السعر", "ايش سعر", "ايش السعر", "شو سعر", "شو السعر", "سعرها كم", "سعره كم", "سعرها", "سعره",
            "الاسعار", "معرفه سعر", "معرفه السعر", "اعرف السعر", "اعرف سعر", "كم تكلف", "كم ثمن", "كم يبلغ سعر", "how much", "what is the price", "what's the price", "whats the price", "price of",
            "how much does", "how much is")


def is_price_question(text: str | None) -> bool:
    """A question about the PRICE OF A PROPERTY. «كم سعر الذهب؟» has the same shape but is not ours: out-of-scope lines never count."""
    t = _norm(text)
    return bool(t) and any(p in t for p in _PRICE_Q) and not is_out_of_scope(text)


_OUT_OF_SCOPE = ("ذهب", "فضه", "اسهم", "بورصه", "تداول", "عملات", "عمله رقميه", "بيتكوين", "دولار", "يورو", "طقس", "الجو ", "كوره", "مباراه", "دوري", "سياسه", "انتخابات", "وصفه", "طبخ",
                 "سيارات", "سياره", "ايفون", "gold", "silver", "stock", "bitcoin", "crypto", "weather", "football", "soccer", "recipe", "politic")
_PROPERTY_WORDS = ("شقه", "فيلا", "ارض", "عقار", "بيت", "منزل", "مكتب", "محل", "عماره", "استوديو", "مستودع", "apartment", "villa", "land", "property", "house", "office", "flat")


def is_out_of_scope(text: str | None) -> bool:
    """A line that has nothing to do with real estate (gold, stocks, weather…). A property word in the same line makes it ours."""
    t = _norm(text)
    return bool(t) and any(w in t for w in _OUT_OF_SCOPE) and not any(w in t for w in _PROPERTY_WORDS)


_PRICE_IN_ANSWER = re.compile(r"\d[\d,.]*\s*(?:ريال|ر\.?\s?س|sar|riyal|الف|ألف|مليون|k\b|m\b)|(?:مليون|ملايين|الف|ألف)\s*ريال", re.I)


def answer_states_a_price(answer: str) -> bool:
    return bool(_PRICE_IN_ANSWER.search((answer or "").translate(_AR_DIGITS)))


# ── grammar the model gets wrong ─────────────────────────────────────────────────────────────────────────────────
_MA_HI_BUDGET = re.compile(r"ما\s+(?:هي|هو)\s+(?:ال)?ميزاني(?:تك|ه|ة)")
_MA_HO_FEMININE = re.compile(r"\bما هو(\s+(?:ال)?(?:منطق|مساح|طريق|فئ|غرف|مدين|صيغ|خطه|خطة))")


def fix_grammar(answer: str) -> str:
    """«ما هي/ما هو الميزانية» is never used (stiff, and the model keeps getting its gender wrong): it becomes «وش ميزانيتك»; the other feminine nouns get «ما هي»."""
    text = _MA_HI_BUDGET.sub("وش ميزانيتك", answer or "")
    return _MA_HO_FEMININE.sub(r"ما هي\1", text)


# ── «الشقة النرجس» -> «شقة النرجس» ──────────────────────────────────────────────────────────────────────────────
_TYPE_NOUNS = "شقة|شقه|فيلا|ارض|أرض|مكتب|محل|عمارة|عماره|استوديو|مستودع"


def fix_type_district(answer: str, districts: Iterable[str]) -> str:
    """Drop the stray «ال» of a property type written right before a district name (the model copies the inventory line): «الشقة النرجس» -> «شقة النرجس»."""
    text = answer or ""
    for d in {x.strip().removeprefix("ال") for x in districts if x and x.strip()}:
        text = re.sub(rf"(?<![ء-ي])(و?)ال({_TYPE_NOUNS})(\s+)(?=(?:حي\s+)?(?:ال)?{re.escape(d)})", r"\1\2\3", text)
    return text


# ── the Hai'a ad number is not for the first reply ───────────────────────────────────────────────────────────────
_AD_ASK = ("رقم الاعلان", "رقم إعلان", "رقم الإعلان", "رقم الترخيص", "رقم الهيئه", "رقم الهيئة", "ترخيص", "ad number", "advertisement number", "license number", "licence number", "rega")
_AD_MENTION = re.compile(r"(?:[،,\-–—(]\s*)?(?:رقم\s+(?:إعلان|اعلان|الإعلان|الاعلان)(?:\s+(?:الهيئة|الهيئه))?|ad(?:vertisement)?\s+(?:number|no\.?))\s*:?\s*\d{6,}\s*\)?[،,]?", re.I)


def asks_for_ad_number(text: str | None) -> bool:
    t = _norm(text)
    return bool(t) and any(_norm(w) in t for w in _AD_ASK)


def strip_ad_numbers(answer: str) -> str:
    """Remove «رقم إعلان الهيئة 7100000001» from a first reply nobody asked it of."""
    out = _AD_MENTION.sub(" ", answer or "")
    out = re.sub(r"\s+([،,.؟?!])", r"\1", out)
    return re.sub(r"\s{2,}", " ", out).strip() or answer


# ── one question per reply ───────────────────────────────────────────────────────────────────────────────────────
def limit_questions(answer: str) -> str:
    """Keep ONE question: the last one. Earlier question sentences are dropped unless they carry a number (a price, an ad number is information)."""
    sentences = _SENTENCE.findall(answer or "")
    q_idx = [i for i, s in enumerate(sentences) if s.strip().endswith(("؟", "?"))]
    if len(q_idx) <= 1:
        return answer
    keep_last = q_idx[-1]
    kept = [s for i, s in enumerate(sentences) if not (i in q_idx and i != keep_last and not re.search(r"\d", s.translate(_AR_DIGITS)))]
    out = " ".join(x.strip() for x in kept).strip()
    return out or answer


# ── services the office has not confirmed ────────────────────────────────────────────────────────────────────────
_PROMISE_VERB = (r"(?:نقدر|نستطيع|نوفر|نقدم|نرتب|نسوي|نسهل|نتكفل|نتولي|عندنا|لدينا|نساعدك في|نساعدكم في|نمشي لك|نخلص لك|نعطيك|"
                 r"we can|we will|we'll|we offer|we provide|we arrange|we help|we handle|we have|we do)")
_NEGATION = re.compile(r"(?:^|\s)(?:ما|لا|مو|مب|ليس|لن|not|can't|cannot|don't|won't|no)\s")
SERVICES: dict[str, tuple[str, tuple[str, ...]]] = {
    "financing": (r"(?:ال)?(?:تمويل|قرض|قروض|رهن)\w*|financ\w*|mortgage|loan\w*", ("تمويل", "قرض", "رهن", "financ", "mortgage", "loan")),
    "ownership_transfer": (r"(?:نقل (?:ال)?ملكي|افراغ|ترتيب (?:ال)?صك|ownership transfer|title transfer|deed transfer)", ("نقل الملكي", "افراغ", "الصك", "ownership", "deed")),
    "inspection": (r"(?:فحص\s+(?:ال|لل|ل)?(?:عقار|مبني|شقه|فيلا)|inspection)", ("فحص", "inspection")),
    "insurance": (r"(?:تامين|insurance)", ("تامين", "insurance")),
    "property_management": (r"(?:ادار[هة] (?:ال)?(?:املاك|عقار)|property management)", ("ادارة الاملاك", "ادارة العقار", "property management")),
}


def unverified_service_claims(answer: str, context: str | None = "") -> list[str]:
    """Services the ANSWER promises that the office's own information (`context`: persona + company profile, NOT the sales brief) never mentions."""
    ctx = _norm(context)
    found: list[str] = []
    for sentence in _SENTENCE.findall(answer or ""):
        s = _norm(sentence)
        if _NEGATION.search(" " + s):
            continue
        for name, (noun, ctx_words) in SERVICES.items():
            if name in found or any(_norm(w) in ctx for w in ctx_words):
                continue
            if re.search(_PROMISE_VERB + r"[^.!؟?\n]{0,30}?(?:" + noun + ")", s):
                found.append(name)
    return found


SERVICE_HANDOVER_AR = "أحد موظفينا يأكد لك تفاصيل هذي الخدمة ويتواصل معك بخصوصها."


def soften_service_claims(answer: str, context: str | None = "") -> str:
    """Replace each sentence that promises an unconfirmed service by ONE hand-over sentence."""
    out, replaced = [], False
    for sentence in _SENTENCE.findall(answer or ""):
        if unverified_service_claims(sentence, context):
            if not replaced:
                out.append(SERVICE_HANDOVER_AR)
                replaced = True
            continue
        out.append(sentence)
    text = " ".join(x.strip() for x in out).strip()
    return text or answer


# ── questions about services the office may not offer ────────────────────────────────────────────────────────────
_INTERROGATIVE = ("هل", "تقدر", "تقدرون", "تقدرو", "تساعدون", "تساعد", "تسوون", "توفرون", "عندكم", "يقبل", "تقبل", "يمكن", "ممكن", "متوفر", "فيه", "كيف", "وش", "ايش", "شلون",
                  "can you", "can i", "do you", "does", "is there", "are you", "is it", "how do", "how can", "will you", "could you", "?", "؟")


def asked_service(text: str | None) -> str | None:
    """The service (financing, ownership transfer, inspection, insurance, property management) the customer ASKS about — a question, not a statement or an answer like «تمويل بنكي»."""
    t = _norm(text)
    if not t or not any(_norm(w) in t for w in _INTERROGATIVE):
        return None
    for name, (noun, _ctx) in SERVICES.items():
        if re.search(noun, t):
            return name
    return None


def office_mentions_service(office_info: str | None, service: str) -> bool:
    ctx = _norm(office_info)
    return any(_norm(w) in ctx for w in SERVICES[service][1])


def purchase_context(q: dict[str, Any]) -> bool:
    """True once the customer has said anything about WHAT he wants (type, district, rooms, purpose, budget, viewing) — only then is a budget question natural."""
    return bool(q.get("property_type") or q.get("district") or q.get("rooms") or q.get("purpose") or q.get("budget_min") or q.get("budget_max") or q.get("viewing_requested"))


# ── 4 + checks ───────────────────────────────────────────────────────────────────────────────────────────────────
def needs_focus(q: dict[str, Any]) -> bool:
    """True while the customer has given neither a district nor a budget (a property type alone is not enough to start recommending)."""
    return not (q.get("district") or q.get("budget_min") or q.get("budget_max"))


_FIELD_ASKED = {"budget": re.compile(r"ميزاني|budget", re.I), "district": re.compile(r"اي حي|في اي منطقه|وين تحب|حي معين|which (?:district|area)|what area", re.I),
                "rooms": re.compile(r"كم غرف|عدد الغرف|how many (?:bed)?rooms", re.I)}


def _known(q: dict[str, Any], field: str) -> bool:
    return bool(q.get("budget_min") or q.get("budget_max")) if field == "budget" else bool(q.get(field))


_PAUSE_REPLY_MARKERS = tuple(_norm(x) for x in ("خذ راحتك", "على راحتك", "فكر على راحتك", "فكّر على راحتك", "لا استعجال", "خذ وقتك", "take your time", "no rush"))
_HANDOVER_MARKERS = ("موظف", "مستشار", "فريق", "اخصائي", "agent", "team member", "consultant", "colleague")


def behaviour_findings(answer: str, *, customer_line: str, qual: dict[str, Any], first_contact: bool = False, inventory_has_listings: bool = False,
                       districts: Iterable[str] = ()) -> list[str]:
    """Behaviour defects in a reply (a model ANSWER or a fixed rule reply) — used by `scripts/eval_bot.py`."""
    from src.sales import intents

    out: list[str] = []
    a = _norm(answer)
    if _SALAM_OPENER.match(a) and not opens_with_salam(customer_line):
        out.append("salam_unprompted")
    questions = [s for s in _SENTENCE.findall(answer) if s.strip().endswith(("؟", "?"))]
    competitor = intents.is_competitor_mention(customer_line)
    human = intents.is_human_request(customer_line)
    if is_pause_or_objection(customer_line) and not competitor and questions:
        out.append("question_after_pause")
    for field, rx in _FIELD_ASKED.items():
        if _known(qual, field) and any(rx.search(_norm(s)) for s in questions):
            out.append(f"asked_known_{field}")
    price_q = is_price_question(customer_line)
    if first_contact and needs_focus(qual) and not price_q and answer_states_a_price(answer):
        out.append("premature_listing")
    if len(questions) > 1:
        out.append("more_than_one_question")
    if price_q and inventory_has_listings and not answer_states_a_price(answer):
        out.append("price_dodged")                                           # asked «كم السعر؟», the inventory has an answer, the reply gives none
    if human and (questions or not any(m in a for m in _HANDOVER_MARKERS)):
        out.append("human_request_ignored")                                  # a person was asked for: confirm the hand-over, ask nothing
    if unverified_service_claims(answer, ""):
        out.append("unverified_service_claim")
    if asked_service(customer_line) and not any(m in a for m in _HANDOVER_MARKERS):
        out.append("service_question_not_handed_over")                       # «هل يقبل تمويل بنكي؟» must end with «أحد موظفينا يأكد لك»
    budget_q = any(_FIELD_ASKED["budget"].search(_norm(s)) for s in questions)
    if budget_q and (is_out_of_scope(customer_line) or asked_service(customer_line) or human or competitor or is_pause_or_objection(customer_line) or not purchase_context(qual)):
        out.append("budget_question_misplaced")                               # pasted after gold / financing / ownership transfer / a person / a pause / before he said what he wants
    if re.search(r"ما\s+(?:هي|هو)\s+(?:ال)?ميزاني", a):
        out.append("banned_budget_phrase")
    if districts and any(re.search(rf"(?<![ء-ي])(?:و?)ال(?:{_TYPE_NOUNS})\s+(?:حي\s+)?(?:ال)?{re.escape(d.removeprefix('ال'))}", answer) for d in districts):
        out.append("type_district_glued")                                      # «الشقة النرجس»
    if first_contact and not asks_for_ad_number(customer_line) and _AD_MENTION.search(answer.translate(_AR_DIGITS)):
        out.append("ad_number_unprompted")
    if competitor and (any(m in a for m in _PAUSE_REPLY_MARKERS) or not questions and not any(m in a for m in _HANDOVER_MARKERS)):
        out.append("competitor_paused")                                      # a pause reply (or nothing at all) to «another company offered me less»
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
    price_q = is_price_question(customer_line)
    if pause and ("ولا تطرح أي سؤال" not in addendum or "في نهاية ردك اسأل" in addendum):
        bad.append("question_requested_after_pause")
    if not pause and "في نهاية ردك اسأل" in addendum:
        quoted = re.search(r"«([^»]+)»", addendum.split("في نهاية ردك اسأل", 1)[1])
        for field, variants in QUESTION_VARIANTS_AR.items():
            if _known(qual, field) and quoted and quoted.group(1) in variants:
                bad.append(f"asks_known_{field}")
    if first_contact and needs_focus(qual) and not price_q and "لا تعرض أي عقار محدد" not in addendum:
        bad.append("missing_focus_rule")
    if price_q and "العميل سأل عن السعر" not in addendum:
        bad.append("missing_price_first_rule")
    if price_q and "لا تعرض أي عقار محدد" in addendum:
        bad.append("price_question_blocked_by_focus_rule")
    if (is_out_of_scope(customer_line) or asked_service(customer_line)) and "في نهاية ردك اسأل" in addendum:
        bad.append("question_requested_after_out_of_scope_or_service")
    if not purchase_context(qual) and "في نهاية ردك اسأل" in addendum:
        quoted_q = re.search(r"«([^»]+)»", addendum.split("في نهاية ردك اسأل", 1)[1])
        if quoted_q and quoted_q.group(1) in QUESTION_VARIANTS_AR["budget"]:
            bad.append("budget_asked_before_he_said_what_he_wants")
    if "ما هي الميزانية" not in addendum or "وش ميزانيتك" not in addendum:
        bad.append("missing_budget_phrase_rule")
    if "لا تكرر نفس الجملة" not in addendum:
        bad.append("missing_variety_rule")
    if "علامة استفهام واحدة" not in addendum:
        bad.append("missing_one_question_rule")
    if "لا تعد بأي خدمة" not in addendum:
        bad.append("missing_unverified_service_rule")
    return bad

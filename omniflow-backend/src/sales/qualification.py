"""
Lead qualification from what the customer SAID (rules, no LLM): budget, district, property type, rooms, purpose, financing, urgency,
viewing request → a 0–100 score and a Hot / Warm / Cold label, plus the next question to ask.

Why rules and not only an LLM: it works with no API key, costs nothing per message, is deterministic (testable) and can never "hallucinate" a budget
the customer did not state. The LLM-based extractor (`customer_profile_extractor`) can still add fields; `merge` keeps the customer's own words authoritative.

Score (documented product decision, tune in `WEIGHTS`):
    budget 20 · district 15 · property type 10 · rooms 5 · purpose 5 · financing (cash 10 / bank 6) · urgency (now 15 / this month 8) ·
    asked to view 20 · gave a phone number 5 · engaged (≥4 messages) 5                       → capped at 100
    label:  hot  ≥ 65, or asked to view with at least two criteria known;   warm ≥ 35;   otherwise cold
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from src.shared.services.bulk_import.text import norm_text

WEIGHTS = {"budget": 20, "district": 15, "property_type": 10, "rooms": 5, "purpose": 5, "financing_cash": 10, "financing_bank": 6,
           "urgency_now": 15, "urgency_month": 8, "viewing": 20, "phone": 5, "engaged": 5}
HOT_AT, WARM_AT = 65, 35
FIELDS = ("budget_min", "budget_max", "district", "property_type", "rooms", "purpose", "financing", "urgency")
ASK_ORDER = ("budget", "district", "property_type", "purpose", "financing", "rooms")

QUESTIONS_AR = {
    "budget": "كم الميزانية التقريبية اللي تفكر فيها؟",
    "district": "في أي حي أو منطقة تفضّل؟",
    "property_type": "تدوّر على شقة، فيلا، أرض، أو غير ذلك؟",
    "purpose": "الشراء للسكن أو للاستثمار؟",
    "financing": "الدفع كاش أو عن طريق تمويل بنكي؟",
    "rooms": "كم عدد الغرف اللي تحتاجها؟",
}

_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")

# ── budget ────────────────────────────────────────────────────────────────────
_UNIT = r"(مليون|ملايين|مليار|الف|آلاف|الاف|ألف|k|m|million|thousand)"
_NUM = r"(\d+(?:[.,]\d+)?)"
_BUDGET_CTX = r"(?:ميزانيت\w*|ميزانيه|budget|تحت|اقل من|أقل من|حدود|بحدود|في حدود|حوالي|تقريبا|لين|الى|إلى|لحد|اقصى|أقصى|max|under|around|about|up to)"
_MULT = {"مليون": 1_000_000, "ملايين": 1_000_000, "million": 1_000_000, "m": 1_000_000, "مليار": 1_000_000_000,
         "الف": 1000, "الاف": 1000, "آلاف": 1000, "ألف": 1000, "k": 1000, "thousand": 1000}
_NOT_MONEY_AFTER = re.compile(r"^\s*(?:متر|م2|م²|sqm|m2|غرف|غرفه|غرفة|اوض|أوض|room|rooms|br|bed|حمام|سنه|سنة|سنوات|%|بالمية|بالمئة)", re.I)
_CURRENCY = r"(?:ريال|ر\.?\s?س|sar|sr)"


def _to_float(raw: str) -> float:
    s = raw.replace(",", ".") if raw.count(",") == 1 and raw.count(".") == 0 and len(raw.split(",")[1]) <= 2 else raw.replace(",", "")
    return float(s)


def _light(text: str) -> str:
    """Like norm_text but KEEPS '.' and ',' (they are decimal / thousands separators in amounts)."""
    t = (text or "").translate(_AR_DIGITS).lower()
    t = re.sub("[أإآٱ]", "ا", t).replace("ى", "ي").replace("ة", "ه")
    t = re.sub(r"[\u064B-\u0652\u0640]", "", t)
    return re.sub(r"\s+", " ", t)


def _blank(t: str, m: "re.Match[str]") -> str:
    return t[:m.start()] + " " * (m.end() - m.start()) + t[m.end():]


def _money_values(text: str) -> list[float]:
    """Every amount in the text, in SAR. "مليون ونص" = 1.5M, "800 الف" = 800k, "1.2 مليون" = 1.2M, "900k" = 900k, "850000 ريال" = 850000,
    "من 500 الى 700 الف" = 500k and 700k. Areas, room counts, years and percentages are not money."""
    t = _light(text)
    w = t
    out: list[float] = []
    for m in list(re.finditer(rf"{_NUM}\s*(?:الي|الى|لين|to|-|–)\s*{_NUM}\s*{_UNIT}\b", w, re.I)):       # a range sharing one unit
        mult = _MULT[m.group(3).lower()]
        out += [_to_float(m.group(1)) * mult, _to_float(m.group(2)) * mult]
        w = _blank(w, m)
    for m in list(re.finditer(rf"{_NUM}\s*{_UNIT}\b", w, re.I)):
        if _NOT_MONEY_AFTER.match(w[m.end():]):
            continue
        out.append(_to_float(m.group(1)) * _MULT[m.group(2).lower()])
        w = _blank(w, m)
    for m in list(re.finditer(r"(مليونين|مليونان|مليون|مليار)(?:\s+و?\s*(نص|نصف|ربع|ثلث))?", w)):
        base = 2.0 if m.group(1) in ("مليونين", "مليونان") else 1.0
        mult = 1_000_000_000 if m.group(1) == "مليار" else 1_000_000
        frac = {"نص": 0.5, "نصف": 0.5, "ربع": 0.25, "ثلث": 1 / 3}.get(m.group(2) or "", 0.0)
        out.append((base + frac) * mult)
        w = _blank(w, m)
    for m in re.finditer(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d{5,})(?![\d.,])", w):
        if _NOT_MONEY_AFTER.match(w[m.end():]):
            continue
        out.append(float(m.group(1).replace(",", "")))
    uniq: list[float] = []
    for v in out:
        if not any(abs(v - u) < 1 for u in uniq):
            uniq.append(v)
    return [v for v in uniq if 10_000 <= v <= 500_000_000]


def extract_budget(text: str) -> tuple[float | None, float | None]:
    """(min, max). One amount: a ceiling when the wording says "under/up to/max", otherwise treated as the ceiling too (people quote their limit)."""
    vals = _money_values(text)
    if not vals:
        return None, None
    t = _light(text)
    if len(vals) >= 2 and re.search(r"(من|between|from)\b", t) and re.search(r"(الي|لين|to|-|–)", t):
        return min(vals), max(vals)
    only = vals[0]
    if re.search(r"(فوق|اكثر من|من \d|above|over|at least|ابتداء|يبدا من)", t):
        return only, None
    return None, only


# ── other attributes ─────────────────────────────────────────────────────────
_TYPES = {
    "apartment": ("شقه", "شقة", "شقق", "apartment", "flat", "دور", "استوديو", "studio"),
    "villa": ("فيلا", "فله", "فلل", "villa", "دوبلكس", "duplex", "تاون هاوس", "townhouse"),
    "land": ("ارض", "أرض", "اراضي", "أراضي", "land", "قطعه", "قطعة"),
    "office": ("مكتب", "مكاتب", "office"),
    "commercial": ("محل", "محلات", "معرض", "تجاري", "تجاريه", "commercial", "shop"),
    "warehouse": ("مستودع", "مستودعات", "warehouse"),
}
_ROOM_WORDS = {"غرفه": 1, "غرفة": 1, "غرفتين": 2, "غرفتان": 2, "ثلاث": 3, "ثلاثه": 3, "اربع": 4, "اربعه": 4, "خمس": 5, "خمسه": 5, "ست": 6, "سته": 6}
_PURPOSE = {"residence": ("سكن", "للسكن", "اسكن", "عائلتي", "اسرتي", "أسرتي", "عيلتي", "residence", "live in", "family home"),
            "investment": ("استثمار", "للاستثمار", "استثمر", "تأجير", "للايجار", "للإيجار", "عائد", "investment", "invest", "rental income")}
_FINANCING = {"cash": ("كاش", "نقد", "نقدا", "نقداً", "cash", "دفعه واحده", "دفعة واحدة"),
              "bank": ("بنك", "بنكي", "تمويل", "قرض", "مصرف", "اقساط", "أقساط", "مدعوم", "الدعم السكني", "سكني", "mortgage", "finance", "loan", "installment")}
_URGENCY_NOW = ("الحين", "حالا", "حالاً", "ضروري", "عاجل", "اسرع", "أسرع", "هالاسبوع", "هالأسبوع", "هذا الاسبوع", "هذا الأسبوع", "بكره", "بكرة", "اليوم", "asap", "urgent", "this week", "right now", "immediately")
_URGENCY_MONTH = ("هالشهر", "هذا الشهر", "خلال شهر", "الشهر الجاي", "this month", "next month", "within a month")
_URGENCY_LOW = ("ادور", "أدور", "اتفرج", "أتفرج", "بس اسال", "بس أسأل", "لسا", "لسه", "مو مستعجل", "مش مستعجل", "just looking", "browsing", "no rush")
_VIEWING = ("معاينه", "معاينة", "اعاين", "أعاين", "اشوفها", "أشوفها", "اشوفه", "ازورها", "أزورها", "زياره", "زيارة", "موعد", "نشوفها", "نعاين", "ابغى اشوف", "ابي اشوف", "عايز اشوف", "view it", "viewing", "visit", "see it", "see the")
_PHONE = re.compile(r"(?<!\d)(?:\+?966|00966|0)?5\d{8}(?!\d)|(?<!\d)\+?\d{10,14}(?!\d)")
_DISTRICT = re.compile(r"(?:بحي|في حي|حي|منطقة|منطقه|district|neighborhood|area)\s+([\w؀-ۿ]+(?:\s+[\w؀-ۿ]+)?)", re.I)
_DISTRICT_STOP = {"الرياض", "جدة", "جده", "الدمام", "مكة", "مكه", "المدينة", "المدينه", "رياض", "الخبر", "riyadh", "jeddah", "dammam", "the", "a", "my", "your", "good", "nice", "sale", "rent"}


def _has(norm: str, words: Iterable[str]) -> bool:
    return any(norm_text(w) in norm for w in words)


def _word_in(norm: str, words: Iterable[str]) -> bool:
    toks = set(norm.split()) | {t[2:] for t in norm.split() if t.startswith("ال") and len(t) > 4}      # «الفيلا» == «فيلا»
    return any(norm_text(w) in toks for w in words)


def extract_rooms(text: str) -> int | None:
    t = norm_text(text.translate(_AR_DIGITS))
    m = re.search(r"(\d{1,2})\s*(?:غرف|غرفه|غرفة|اوض|أوض|غرف نوم|br|bed|bedroom|room)", t)
    if m:
        n = int(m.group(1))
        return n if 1 <= n <= 12 else None
    m = re.search(r"(غرفتين|غرفتان|ثلاث|ثلاثه|اربع|اربعه|خمس|خمسه|ست|سته)\s*(?:غرف|غرفه|اوض|أوض)?", t)
    if m and (m.group(1) in ("غرفتين", "غرفتان") or re.search(r"غرف|غرفه|اوض", t)):
        return _ROOM_WORDS.get(m.group(1))
    return None


def extract_district(text: str, known: Iterable[str] = ()) -> str | None:
    """A district from the tenant's own listings (best: exact), else the word(s) after "حي". Never a city name."""
    norm = norm_text(text)
    best: str | None = None
    for d in known:
        nd = norm_text(d)
        if nd and re.search(rf"(?<!\S){re.escape(nd)}(?!\S)", norm) and (best is None or len(d) > len(best)):
            best = d
    if best:
        return best
    m = _DISTRICT.search(text.translate(_AR_DIGITS))
    if m:
        cand = m.group(1).strip()
        first = norm_text(cand).split()[0] if norm_text(cand) else ""
        if first and first not in {norm_text(s) for s in _DISTRICT_STOP} and not first.isdigit():
            return cand
    return None


def extract_from_text(text: str, known_districts: Iterable[str] = ()) -> dict[str, Any]:
    """Everything this ONE message tells us. Missing = key absent."""
    raw = text or ""
    norm = norm_text(raw.translate(_AR_DIGITS))
    out: dict[str, Any] = {}
    lo, hi = extract_budget(raw)
    if lo is not None:
        out["budget_min"] = lo
    if hi is not None:
        out["budget_max"] = hi
    for ptype, words in _TYPES.items():
        if _word_in(norm, words) or _has(norm, [w for w in words if len(w) > 4]):
            out["property_type"] = ptype
            break
    rooms = extract_rooms(raw)
    if rooms:
        out["rooms"] = rooms
    d = extract_district(raw, known_districts)
    if d:
        out["district"] = d
    for purpose, words in _PURPOSE.items():
        if _has(norm, words):
            out["purpose"] = purpose
            break
    for fin, words in _FINANCING.items():
        if _has(norm, words):
            out["financing"] = fin
            break
    if _has(norm, _URGENCY_NOW):
        out["urgency"] = "now"
    elif _has(norm, _URGENCY_MONTH):
        out["urgency"] = "month"
    elif _has(norm, _URGENCY_LOW):
        out["urgency"] = "exploring"
    if _has(norm, _VIEWING):
        out["viewing_requested"] = True
    if _PHONE.search(raw.translate(_AR_DIGITS)):
        out["phone_given"] = True
    return out


def merge(old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    """The customer's latest statement wins; nothing the customer already said is erased by silence. A new ceiling replaces the old one."""
    merged = dict(old or {})
    for k, v in new.items():
        if v is None:
            continue
        merged[k] = v
    if "budget_max" in new and "budget_min" not in new and merged.get("budget_min") and merged["budget_min"] > merged["budget_max"]:
        merged.pop("budget_min")
    return merged


@dataclass(frozen=True)
class Scored:
    score: int
    label: str
    missing: list[str]


def score(q: dict[str, Any], *, messages: int = 0) -> Scored:
    s = 0
    if q.get("budget_min") or q.get("budget_max"):
        s += WEIGHTS["budget"]
    if q.get("district"):
        s += WEIGHTS["district"]
    if q.get("property_type"):
        s += WEIGHTS["property_type"]
    if q.get("rooms"):
        s += WEIGHTS["rooms"]
    if q.get("purpose"):
        s += WEIGHTS["purpose"]
    s += {"cash": WEIGHTS["financing_cash"], "bank": WEIGHTS["financing_bank"]}.get(q.get("financing") or "", 0)
    s += {"now": WEIGHTS["urgency_now"], "month": WEIGHTS["urgency_month"]}.get(q.get("urgency") or "", 0)
    if q.get("viewing_requested"):
        s += WEIGHTS["viewing"]
    if q.get("phone_given"):
        s += WEIGHTS["phone"]
    if messages >= 4:
        s += WEIGHTS["engaged"]
    s = min(100, s)
    known = sum(1 for k in ("budget", "district", "property_type", "purpose") if (q.get("budget_min") or q.get("budget_max")) and k == "budget" or q.get(k))
    label = "hot" if s >= HOT_AT or (q.get("viewing_requested") and known >= 2) else "warm" if s >= WARM_AT else "cold"
    missing = []
    for key in ASK_ORDER:
        have = bool(q.get("budget_min") or q.get("budget_max")) if key == "budget" else bool(q.get(key))
        if not have:
            missing.append(key)
    return Scored(s, label, missing)


def qualify(messages: list[str], previous: dict[str, Any] | None = None, *, known_districts: Iterable[str] = ()) -> dict[str, Any]:
    """Run the rules over the customer's messages (oldest → newest) on top of `previous`, and return the stored qualification dict."""
    q = dict(previous or {})
    q.pop("score", None), q.pop("label", None), q.pop("missing", None)
    for text in messages:
        q = merge(q, extract_from_text(text, known_districts))
    sc = score(q, messages=len(messages))
    q.update(score=sc.score, label=sc.label, missing=sc.missing, updated_at=datetime.now(timezone.utc).isoformat())
    return q


def next_question(q: dict[str, Any], *, already_asked: Iterable[str] = (), max_per_conversation: int = 3) -> tuple[str, str] | None:
    """(field, question) for the single most useful thing to ask next, or None when nothing is missing / we have already asked enough."""
    asked = list(already_asked)
    if len(asked) >= max_per_conversation:
        return None
    for key in q.get("missing") or []:
        if key not in asked:
            return key, QUESTIONS_AR[key]
    return None


def public_summary(q: dict[str, Any]) -> str:
    """One line a person can read in a second (staff handoff, CRM)."""
    parts = []
    lo, hi = q.get("budget_min"), q.get("budget_max")
    if lo or hi:
        parts.append("الميزانية " + (f"{int(lo):,}–{int(hi):,}" if lo and hi else f"حتى {int(hi):,}" if hi else f"من {int(lo):,}") + " ريال")
    for key, label in (("district", "الحي"), ("rooms", "الغرف")):
        if q.get(key):
            parts.append(f"{label}: {q[key]}")
    names = {"apartment": "شقة", "villa": "فيلا", "land": "أرض", "office": "مكتب", "commercial": "تجاري", "warehouse": "مستودع"}
    if q.get("property_type"):
        parts.append(names.get(q["property_type"], q["property_type"]))
    parts += [{"residence": "للسكن", "investment": "للاستثمار"}.get(q.get("purpose") or "", ""), {"cash": "كاش", "bank": "تمويل بنكي"}.get(q.get("financing") or "", "")]
    parts += [{"now": "مستعجل", "month": "خلال شهر", "exploring": "يستكشف"}.get(q.get("urgency") or "", ""), "طلب معاينة" if q.get("viewing_requested") else ""]
    return "، ".join(p for p in parts if p) or "لا معلومات مؤكدة بعد"

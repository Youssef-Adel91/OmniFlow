"""
Viewing booking: which slots to offer, how to show them, and how to understand the customer's answer. PURE (no database, no Redis, no clock of its own):
`now` and the busy list are passed in, so every rule is testable with fixed dates.

Slots come from the tenant's `sales.viewing_hours` (same shape as business hours: {"sun": [["10:00","18:00"]], ...}), `slot_minutes`, `min_notice_hours`
and `max_parallel_viewings` (how many viewings may start at the same time = how many staff can attend). Offered: at most 2 per day (the earliest and an
evening one), at most 6 in total, over `days_ahead` days — a short, readable menu instead of a calendar.

The customer answers with a number ("2", "٢", "2️⃣", "الثاني"), a day + time ("الخميس 5"), or "تمام" when only one slot was offered. Anything else -> None, and the
caller asks again (never guesses a time).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from src.sales.qualification import _light

DAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")                  # same keys as business hours; sunday-first like the Saudi week
DAY_AR = {"sun": "الأحد", "mon": "الإثنين", "tue": "الثلاثاء", "wed": "الأربعاء", "thu": "الخميس", "fri": "الجمعة", "sat": "السبت"}
_DAY_WORDS = {"sun": ("احد", "الاحد"), "mon": ("اثنين", "الاثنين", "الاتنين", "اتنين"), "tue": ("ثلاثاء", "الثلاثاء", "ثلاثا", "التلات", "تلات"),
              "wed": ("اربعاء", "الاربعاء", "اربعا", "الاربع"), "thu": ("خميس", "الخميس"), "fri": ("جمعه", "الجمعه"), "sat": ("سبت", "السبت")}
MONTH_AR = ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر")
_ORDINALS = {"الاول": 0, "الاولي": 0, "اول": 0, "الاولى": 0, "first": 0, "1st": 0, "الثاني": 1, "الثانيه": 1, "ثاني": 1, "second": 1, "2nd": 1,
             "الثالث": 2, "الثالثه": 2, "ثالث": 2, "third": 2, "3rd": 2, "الرابع": 3, "الرابعه": 3, "fourth": 3, "4th": 3, "الخامس": 4, "الخامسه": 4, "fifth": 4,
             "السادس": 5, "السادسه": 5, "sixth": 5}
_YES = ("نعم", "ايوه", "ايوا", "ايه", "اي", "تمام", "اوكي", "اوك", "ok", "okay", "yes", "موافق", "ماشي", "حاضر", "طيب", "زين", "جيد", "مناسب", "يناسبني")
_NO = ("لا", "ما يناسبني", "مو مناسب", "مش مناسب", "ما يناسب", "غيره", "غير", "بدل", "no", "other", "another", "ابغى غيره", "ابي غيره")

DEFAULT_VIEWING_DAYS = {d: [["10:00", "18:00"]] for d in ("sun", "mon", "tue", "wed", "thu")}


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "Asia/Riyadh")
    except Exception:  # noqa: BLE001
        return ZoneInfo("Asia/Riyadh")


def _hm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def generate_slots(now: datetime, cfg: dict[str, Any], busy: Sequence[datetime] = (), *, exclude: Sequence[datetime] = ()) -> list[datetime]:
    """Aware UTC datetimes to offer. `cfg` keys: viewing_hours{days,timezone}, slot_minutes, days_ahead, min_notice_hours, max_parallel_viewings."""
    hours = cfg.get("viewing_hours") or {}
    days = hours["days"] if "days" in hours else DEFAULT_VIEWING_DAYS          # an explicit {} means "closed all week": no slots
    tz = _zone(hours.get("timezone") or cfg.get("timezone") or "Asia/Riyadh")
    step = timedelta(minutes=int(cfg.get("slot_minutes", 60)))
    earliest = now + timedelta(hours=int(cfg.get("min_notice_hours", 2)))
    parallel = max(1, int(cfg.get("max_parallel_viewings", 1)))
    busy_utc = [b.astimezone(timezone.utc) for b in busy]
    skip = {e.astimezone(timezone.utc) for e in exclude}
    local_now = now.astimezone(tz)
    chosen: list[datetime] = []
    for offset in range(0, int(cfg.get("days_ahead", 5)) + 1):
        day = (local_now + timedelta(days=offset)).date()
        key = DAYS[(day.weekday() + 1) % 7]
        free: list[datetime] = []
        for start_s, end_s in days.get(key, []):
            (sh, sm), (eh, em) = _hm(start_s), _hm(end_s)
            cur = datetime(day.year, day.month, day.day, sh, sm, tzinfo=tz)
            end = datetime(day.year, day.month, day.day, eh, em, tzinfo=tz)
            while cur + step <= end:
                cur_utc = cur.astimezone(timezone.utc)
                taken = sum(1 for b in busy_utc if cur_utc <= b < cur_utc + step or b <= cur_utc < b + step)
                if cur_utc >= earliest and taken < parallel and cur_utc not in skip:
                    free.append(cur_utc)
                cur += step
        if not free:
            continue
        pick = [free[0]]
        evening = [s for s in free if s.astimezone(tz).hour >= 16 and s != free[0]]
        later = evening[0] if evening else (free[len(free) // 2] if len(free) > 2 else None)
        if later is not None and later != free[0]:
            pick.append(later)
        chosen += pick
        if len(chosen) >= 6:
            break
    return chosen[:6]


def _hour_label(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d} {'ص' if dt.hour < 12 else 'م'}"


def format_slot(dt: datetime, tz_name: str = "Asia/Riyadh") -> str:
    loc = dt.astimezone(_zone(tz_name))
    return f"{DAY_AR[DAYS[(loc.weekday() + 1) % 7]]} {loc.day} {MONTH_AR[loc.month - 1]} – {_hour_label(loc)}"


def format_offer(slots: Sequence[datetime], tz_name: str = "Asia/Riyadh", *, intro: str | None = None) -> str:
    lines = [intro or "تمام! هذي المواعيد المتاحة للمعاينة، اختر رقم الموعد المناسب:"]
    lines += [f"{i}) {format_slot(s, tz_name)}" for i, s in enumerate(slots, 1)]
    lines.append("وإذا ما يناسبك أي منها قل لي «غيرها» وأعرض عليك مواعيد ثانية.")
    return "\n".join(lines)


def format_confirmation(dt: datetime, tz_name: str = "Asia/Riyadh", location: str | None = None) -> str:
    out = f"تم حجز موعد المعاينة يوم {format_slot(dt, tz_name)}."
    if location:
        out += f"\nالموقع: {location}"
    return out + "\nسنرسل لك تذكيرًا قبل الموعد، وإذا احتجت تغييره أو إلغاءه قل لي بس."


# ── understanding the answer ─────────────────────────────────────────────────

def _digits(text: str) -> str:
    return (text or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")).replace("️", "").replace("⃣", "")


_DONT_KNOW = ("لا ادري", "ما ادري", "مدري", "لا اعرف", "ما اعرف", "مادري", "i dont know", "dont know", "not sure")


def wants_other_slots(text: str) -> bool:
    t = _light(_digits(text)).strip(" .!؟?،,")
    if any(t == _light(w) or t.startswith(_light(w) + " ") for w in _DONT_KNOW):
        return False                          # "I don't know" is not "show me other times"
    return any(t == _light(w) or t.startswith(_light(w) + " ") for w in _NO) or any(w in t for w in ("غيرها", "غيرهم", "مواعيد ثانيه", "مواعيد اخرى", "مواعيد ثانية", "other times", "different time"))


def _is_yes(text: str) -> bool:
    t = _light(_digits(text)).strip(" .!؟?،,")
    return t in {_light(w) for w in _YES}


def parse_choice(text: str, slots: Sequence[datetime], tz_name: str = "Asia/Riyadh") -> int | None:
    """Index into `slots` the customer picked, or None when it cannot be told for sure."""
    if not slots:
        return None
    t = _light(_digits(text)).strip(" .!؟?،,")
    if re.fullmatch(r"\d{1,2}", t):
        n = int(t)
        return n - 1 if 1 <= n <= len(slots) else None
    m = re.fullmatch(r"(?:رقم|الموعد|موعد|option|number|no\.?|#)?\s*(\d{1,2})\s*(?:رقم|please|لو سمحت|من فضلك)?", t)
    if m and len(t.split()) <= 3:
        n = int(m.group(1))
        if 1 <= n <= len(slots):
            return n - 1
    for word, idx in _ORDINALS.items():
        if re.search(rf"(?<![\w؀-ۿ]){re.escape(_light(word))}(?![\w؀-ۿ])", t) and idx < len(slots):
            return idx
    if len(slots) == 1 and _is_yes(t):
        return 0
    tz = _zone(tz_name)
    locs = [s.astimezone(tz) for s in slots]
    cand = list(range(len(slots)))
    day_key = next((k for k, words in _DAY_WORDS.items() if any(re.search(rf"(?<![\w؀-ۿ])(?:ال)?{re.escape(w.removeprefix('ال'))}", t) for w in words)), None)
    if day_key:
        cand = [i for i in cand if DAYS[(locs[i].weekday() + 1) % 7] == day_key]
    tm = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(ص|صباحا|الصبح|am|م|مساء|عصرا|بعد الظهر|العصر|المغرب|pm|ظهرا|الظهر)?", t)
    if tm and (day_key or tm.group(3) or ":" in t):
        h, mnt, mer = int(tm.group(1)), int(tm.group(2) or 0), tm.group(3) or ""
        pm = mer in ("م", "مساء", "عصرا", "بعد الظهر", "العصر", "المغرب", "pm", "ظهرا", "الظهر")
        am = mer in ("ص", "صباحا", "الصبح", "am")

        def matches(dt: datetime) -> bool:
            if dt.minute != mnt:
                return False
            if pm:
                return dt.hour % 12 == h % 12 and dt.hour >= 12
            if am:
                return dt.hour % 12 == h % 12 and dt.hour < 12
            return dt.hour % 12 == h % 12 or dt.hour == h

        cand = [i for i in cand if matches(locs[i])]
    if len(cand) == 1 and (day_key or tm):
        return cand[0]
    return None


def wants_to_cancel(text: str) -> bool:
    t = _light(text)
    return bool(re.search(r"(الغ[يى]|إلغ|كنسل|cancel)", t)) and bool(re.search(r"(موعد|معاينه|المعاينه|appointment|viewing|booking|حجز)", t))


def wants_to_reschedule(text: str) -> bool:
    t = _light(text)
    return bool(re.search(r"(غير|غيّر|بدل|اجل|أجل|تاجيل|تأجيل|reschedule|postpone|change)", t)) and bool(re.search(r"(موعد|معاينه|المعاينه|appointment|viewing|booking|حجز)", t))

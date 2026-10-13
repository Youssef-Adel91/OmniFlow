"""Tenant operating preferences: business hours, human handoff, AI behaviour, identity.

Stored as validated JSON sections in `tenant_preferences` (one row per tenant, tenant RLS); the defaults live here so a
tenant that never opened Settings behaves exactly as before. Every section is validated on write (`validate_section`)
and merged over its defaults on read, so a missing/partial/old row can never break the message pipeline.

What each section actually changes at runtime:
  business_hours  -> `handoff.perform_handoff`: outside hours the customer escalated to a human gets the out-of-hours
                     message (with the next opening time) instead of the "an agent will reply shortly" message.
  handoff         -> keyword/explicit-request escalation (semantic router), low-confidence escalation on/off, the
                     message, round-robin assignment of escalated conversations, and the default SLA of the dashboard.
  ai_behavior +
  identity        -> `behavior_block`, appended to the tenant persona that every AI call uses (tone, dialect, language,
                     currency, constraints, welcome message).
"""
from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

from src.shared.services.bulk_import.text import norm_text

SECTIONS = ("business_hours", "handoff", "ai_behavior", "identity", "social_comments", "sales")
DAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
DAY_LABELS_AR = {"sun": "الأحد", "mon": "الإثنين", "tue": "الثلاثاء", "wed": "الأربعاء", "thu": "الخميس", "fri": "الجمعة", "sat": "السبت"}
CURRENCIES = ("SAR", "AED", "KWD", "QAR", "BHD", "OMR", "EGP", "USD", "EUR")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

DEFAULT_OUT_OF_HOURS = "شكرًا لتواصلك. فريقنا خارج أوقات الدوام حاليًا، وسنردّ عليك {next_open}."
DEFAULT_HANDOFF_MESSAGE = "تم تحويلك إلى أحد موظفينا وسيردّ عليك قريبًا. شكرًا لصبرك."
# Deliberately narrow (explicit "I want a person" phrases): a loose word like "مدير" would hijack ordinary questions.
DEFAULT_KEYWORDS = ["اكلم موظف", "ابغى موظف", "ابي موظف", "اريد موظف", "خدمة العملاء", "شخص حقيقي", "اكلم انسان", "talk to a human", "human agent", "customer service"]


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"منطقة زمنية غير معروفة: {name}") from exc


class BusinessHours(BaseModel):
    enabled: bool = False
    timezone: str = "Asia/Riyadh"
    # day -> list of [start, end] intervals inside that day ("09:00","17:00"); a missing/empty day = closed
    days: dict[str, list[list[str]]] = Field(default_factory=lambda: {d: [["09:00", "17:00"]] for d in ("sun", "mon", "tue", "wed", "thu")})
    out_of_hours_message: str = Field(default=DEFAULT_OUT_OF_HOURS, max_length=1000)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        _zone(v)
        return v

    @model_validator(mode="after")
    def _check_days(self) -> "BusinessHours":
        clean: dict[str, list[list[str]]] = {}
        for day, intervals in self.days.items():
            if day not in DAYS:
                raise ValueError(f"يوم غير معروف: {day}")
            if len(intervals) > 4:
                raise ValueError("حتى 4 فترات في اليوم")
            rows = []
            for iv in intervals:
                if len(iv) != 2 or not all(_HHMM.match(x or "") for x in iv):
                    raise ValueError("الفترة يجب أن تكون [HH:MM, HH:MM]")
                if iv[0] >= iv[1]:
                    raise ValueError(f"نهاية الفترة يجب أن تكون بعد بدايتها ({iv[0]}–{iv[1]})")
                rows.append([iv[0], iv[1]])
            rows.sort()
            for a, b in zip(rows, rows[1:]):
                if b[0] < a[1]:
                    raise ValueError("فترات اليوم متداخلة")
            clean[day] = rows
        self.days = clean
        if self.enabled and not any(self.days.values()):
            raise ValueError("فعّلت ساعات العمل لكن كل الأيام مغلقة")
        return self


class Handoff(BaseModel):
    auto_escalate: bool = True
    keywords: list[str] = Field(default_factory=lambda: list(DEFAULT_KEYWORDS), max_length=30)
    on_low_confidence: bool = True
    round_robin: bool = True
    handoff_message: str = Field(default=DEFAULT_HANDOFF_MESSAGE, max_length=1000)
    sla_minutes: int = Field(default=15, ge=1, le=1440)

    @field_validator("keywords")
    @classmethod
    def _kw(cls, v: list[str]) -> list[str]:
        out = list(dict.fromkeys(k.strip() for k in v if k and k.strip()))
        if any(len(k) > 40 for k in out):
            raise ValueError("الكلمة المفتاحية أطول من 40 حرفًا")
        return out


class Constraints(BaseModel):
    no_price_without_inventory: bool = True       # never quote a price that is not in the tenant's listings
    no_legal_or_financial_advice: bool = True
    no_competitor_mentions: bool = False
    ask_budget_early: bool = False


class AiBehavior(BaseModel):
    tone: Literal["formal", "friendly", "concise", "persuasive"] = "friendly"
    dialect: Literal["msa", "saudi", "gulf", "egyptian"] = "saudi"
    language: Literal["ar", "en", "auto"] = "ar"
    welcome_message: str = Field(default="", max_length=500)
    quick_replies: list[str] = Field(default_factory=list, max_length=8)
    constraints: Constraints = Field(default_factory=Constraints)

    @field_validator("quick_replies")
    @classmethod
    def _qr(cls, v: list[str]) -> list[str]:
        out = list(dict.fromkeys(x.strip() for x in v if x and x.strip()))
        if any(len(x) > 60 for x in out):
            raise ValueError("الرد السريع أطول من 60 حرفًا")
        return out


class Identity(BaseModel):
    language: Literal["ar", "en"] = "ar"
    timezone: str = "Asia/Riyadh"
    currency: Literal["SAR", "AED", "KWD", "QAR", "BHD", "OMR", "EGP", "USD", "EUR"] = "SAR"

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        _zone(v)
        return v


class SocialComments(BaseModel):
    """What to do when someone comments on the Page / Instagram post. Takes effect only while the platform flag FEATURE_SOCIAL_COMMENT_REPLIES is on."""
    enabled: bool = False
    mode: Literal["public_reply", "private_dm"] = "public_reply"       # reply under the comment, or send the commenter a private message


MODELS: dict[str, type[BaseModel]] = {"business_hours": BusinessHours, "handoff": Handoff, "ai_behavior": AiBehavior, "identity": Identity, "social_comments": SocialComments}


def _default_viewing_hours() -> dict[str, Any]:
    return {"timezone": "Asia/Riyadh", "days": {d: [["10:00", "18:00"]] for d in ("sun", "mon", "tue", "wed", "thu")}}


class Sales(BaseModel):
    """Sales-closing assistant (qualification questions, viewing booking, follow-ups). Everything is OFF until the tenant enables it AND the platform flag FEATURE_SALES_CLOSING is on.

    qualification  -> the assistant asks ONE missing question at a time (budget, district, type, purpose, financing, rooms), at most `max_questions` per conversation.
    booking        -> offers viewing slots built from `viewing_hours` / `slot_minutes`, books on the customer's choice, reminds `reminder_hours_before` hours before.
    follow-up      -> up to `max_followups` automatic nudges after `followup_delays_hours` of silence; never to opted-out customers; WhatsApp outside the 24-hour
                      window needs the approved template named in `whatsapp_followup_template` (otherwise the nudge is skipped, not sent as plain text).
    """
    enabled: bool = False
    ask_questions: bool = True
    max_questions: int = Field(default=3, ge=0, le=8)
    booking_enabled: bool = True
    slot_minutes: int = Field(default=60, ge=15, le=240)
    days_ahead: int = Field(default=5, ge=1, le=14)
    min_notice_hours: int = Field(default=2, ge=0, le=72)
    max_parallel_viewings: int = Field(default=1, ge=1, le=20)
    viewing_hours: dict[str, Any] = Field(default_factory=_default_viewing_hours)
    viewing_location_note: str = Field(default="", max_length=300)
    reminder_hours_before: int = Field(default=2, ge=1, le=48)
    followup_enabled: bool = False
    followup_delays_hours: list[int] = Field(default_factory=lambda: [2, 20, 72])
    followup_messages: list[str] = Field(default_factory=lambda: [
        "أهلاً {name} 👋 حبيت أتأكد إذا عندك أي سؤال، وأقدر أرتب لك معاينة في الوقت اللي يناسبك.",
        "مرحباً {name}، لسا مهتم؟ إذا تحب أرسل لك خيارات ثانية تناسب طلبك أو أحجز لك معاينة.",
        "{name}، هذي آخر رسالة متابعة مني حتى لا أزعجك. إذا احتجت أي شيء راسلنا في أي وقت 🤝"])
    whatsapp_followup_template: str = Field(default="", max_length=100)
    whatsapp_followup_template_lang: str = Field(default="ar", max_length=10)

    @field_validator("viewing_hours")
    @classmethod
    def _hours(cls, v: dict[str, Any]) -> dict[str, Any]:
        bh = BusinessHours.model_validate({"enabled": False, "timezone": v.get("timezone", "Asia/Riyadh"), "days": v.get("days", {})})
        return {"timezone": bh.timezone, "days": bh.days}

    @field_validator("followup_delays_hours")
    @classmethod
    def _delays(cls, v: list[int]) -> list[int]:
        if not 0 <= len(v) <= 3:
            raise ValueError("حتى 3 رسائل متابعة")
        if any(not 1 <= h <= 720 for h in v):
            raise ValueError("المهلة بين 1 و720 ساعة")
        if sorted(v) != v or len(set(v)) != len(v):
            raise ValueError("يجب أن تتزايد المهل (مثلًا 24 ثم 72 ثم 168)")
        return v

    @field_validator("followup_messages")
    @classmethod
    def _msgs(cls, v: list[str]) -> list[str]:
        out = [m.strip() for m in v]
        if any(not m for m in out) or any(len(m) > 600 for m in out):
            raise ValueError("نص المتابعة فارغ أو أطول من 600 حرف")
        bad = {f for m in out for f in re.findall(r"\{[^}]*\}", m)} - {"{name}"}
        if bad:
            raise ValueError("المتغير المسموح في رسائل المتابعة: {name} فقط")
        return out

    @model_validator(mode="after")
    def _enough_messages(self) -> "Sales":
        if len(self.followup_messages) < len(self.followup_delays_hours):
            raise ValueError("عدد رسائل المتابعة أقل من عدد المهل")
        return self


MODELS["sales"] = Sales


def validate_section(section: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a full section payload against its model; unknown fields are rejected (extra='forbid' semantics)."""
    if section not in MODELS:
        raise KeyError(section)
    model = MODELS[section]
    unknown = set(payload) - set(model.model_fields)
    if unknown:
        raise ValueError(f"حقول غير معروفة: {', '.join(sorted(unknown))}")
    return model.model_validate(payload).model_dump()


def with_defaults(section: str, stored: dict[str, Any] | None) -> dict[str, Any]:
    """Stored JSON merged over the defaults; a corrupt/old row falls back to the defaults instead of raising."""
    model = MODELS[section]
    try:
        return model.model_validate(stored or {}).model_dump()
    except Exception:  # noqa: BLE001
        return model().model_dump()


def all_defaults() -> dict[str, dict[str, Any]]:
    return {s: MODELS[s]().model_dump() for s in SECTIONS}


# ── business hours ───────────────────────────────────────────────────────────────

def _local(now: datetime, tz: str) -> datetime:
    return now.astimezone(_zone(tz))


def is_open(cfg: dict[str, Any], now: datetime | None = None) -> bool:
    """True when the business is open (or hours are not enabled: then it is always "open")."""
    if not cfg.get("enabled"):
        return True
    loc = _local(now or datetime.now(timezone.utc), cfg.get("timezone", "Asia/Riyadh"))
    hhmm = loc.strftime("%H:%M")
    return any(a <= hhmm < b for a, b in cfg.get("days", {}).get(DAYS[(loc.weekday() + 1) % 7], []))


def next_open(cfg: dict[str, Any], now: datetime | None = None) -> datetime | None:
    """The next moment the business opens (UTC-aware), looking up to 8 days ahead; None when never."""
    now = now or datetime.now(timezone.utc)
    if not cfg.get("enabled") or is_open(cfg, now):
        return now if cfg.get("enabled") else None
    tz = _zone(cfg.get("timezone", "Asia/Riyadh"))
    loc = now.astimezone(tz)
    for offset in range(0, 9):
        day = (loc + timedelta(days=offset)).date()
        key = DAYS[(day.weekday() + 1) % 7]
        for start, _end in sorted(cfg.get("days", {}).get(key, [])):
            h, m = (int(x) for x in start.split(":"))
            candidate = datetime(day.year, day.month, day.day, h, m, tzinfo=tz)
            if candidate > loc:
                return candidate.astimezone(timezone.utc)
    return None


def describe_next_open(cfg: dict[str, Any], now: datetime | None = None) -> str:
    """Arabic phrase for the out-of-hours message: "غدًا الساعة 09:00" / "يوم الأحد الساعة 09:00" / "عند بدء الدوام"."""
    nxt = next_open(cfg, now)
    if nxt is None:
        return "عند بدء الدوام"
    tz = _zone(cfg.get("timezone", "Asia/Riyadh"))
    loc_now = (now or datetime.now(timezone.utc)).astimezone(tz)
    loc = nxt.astimezone(tz)
    days_ahead = (loc.date() - loc_now.date()).days
    when = "اليوم" if days_ahead == 0 else "غدًا" if days_ahead == 1 else f"يوم {DAY_LABELS_AR[DAYS[(loc.weekday() + 1) % 7]]}"
    return f"{when} الساعة {loc.strftime('%H:%M')}"


# ── handoff keywords ─────────────────────────────────────────────────────────────

def match_handoff_keyword(text: str | None, keywords: list[str]) -> str | None:
    """The first keyword found as whole words in the (normalised) message, else None."""
    if not text or not keywords:
        return None
    padded = f" {norm_text(text)} "
    for kw in keywords:
        k = norm_text(kw)
        if k and f" {k} " in padded:
            return kw
    return None


# ── AI behaviour -> persona text ─────────────────────────────────────────────────────

_TONE = {"formal": "رسمي ومهذّب", "friendly": "ودود وقريب من العميل", "concise": "مختصر ومباشر", "persuasive": "مقنع ومشجّع على اتخاذ القرار دون ضغط"}
_DIALECT = {"msa": "العربية الفصحى المبسّطة", "saudi": "اللهجة السعودية البيضاء", "gulf": "اللهجة الخليجية", "egyptian": "اللهجة المصرية"}
_LANG = {"ar": "ردّ بالعربية دائمًا.", "en": "Always reply in English.", "auto": "ردّ بلغة العميل (عربي أو إنجليزي)."}
_CURRENCY = {"SAR": "الريال السعودي", "AED": "الدرهم الإماراتي", "KWD": "الدينار الكويتي", "QAR": "الريال القطري", "BHD": "الدينار البحريني",
             "OMR": "الريال العُماني", "EGP": "الجنيه المصري", "USD": "الدولار الأمريكي", "EUR": "اليورو"}


def behavior_block(ai: dict[str, Any], identity: dict[str, Any]) -> str:
    """Instructions appended to the tenant persona. Only mentions what the tenant changed from neutral, in short lines."""
    ai, identity = with_defaults("ai_behavior", ai), with_defaults("identity", identity)
    c = ai["constraints"]
    lines = [
        f"النبرة: {_TONE[ai['tone']]}.",
        f"اللهجة: {_DIALECT[ai['dialect']]}.",
        _LANG[ai["language"]] + (f" وإن لم تتضح لغته فاستخدم {'العربية' if identity['language'] == 'ar' else 'الإنجليزية'}." if ai["language"] == "auto" else ""),
        f"اذكر الأسعار دائمًا بعملة: {_CURRENCY[identity['currency']]} ({identity['currency']}).",
    ]
    if c["no_price_without_inventory"]:
        lines.append("لا تذكر سعرًا أو توفّر عقار إلا إذا كان موجودًا في المعلومات المتاحة لك؛ وإلا قل إنك ستتحقق وتعود إليه.")
    if c["no_legal_or_financial_advice"]:
        lines.append("لا تقدّم نصيحة قانونية أو مالية أو استثمارية؛ وجّه العميل لموظفينا المختصين.")
    if c["no_competitor_mentions"]:
        lines.append("لا تذكر أو تقارن بشركات منافسة.")
    if c["ask_budget_early"]:
        lines.append("اسأل العميل مبكرًا عن ميزانيته وموقعه المفضّل لتقترح عليه ما يناسبه.")
    if ai["welcome_message"].strip():
        lines.append(f"عند أول رسالة من عميل جديد ابدأ بهذه التحية (بصياغتك): {ai['welcome_message'].strip()}")
    return "تعليمات السلوك المضبوطة من صاحب الحساب:\n- " + "\n- ".join(lines)


# ── cached reads (the message pipeline must never fail or slow down because of preferences) ──────────────

_CACHE: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}
_TTL = 30.0


def invalidate(tenant_id: str | uuid.UUID) -> None:
    _CACHE.pop(str(tenant_id), None)


async def get_prefs(tenant_id: str | uuid.UUID | None) -> dict[str, dict[str, Any]]:
    """All four sections merged over defaults; cached 30 s; never raises (defaults on any error)."""
    if not tenant_id:
        return all_defaults()
    key = str(tenant_id)
    hit = _CACHE.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    prefs = all_defaults()
    try:
        from sqlalchemy import select

        from src.shared.db.models import TenantPreferences
        from src.shared.db.session import get_tenant_session

        async with get_tenant_session(uuid.UUID(key)) as session:
            row = await session.scalar(select(TenantPreferences).where(TenantPreferences.tenant_id == uuid.UUID(key)))
        if row is not None:
            prefs = {s: with_defaults(s, getattr(row, s)) for s in SECTIONS}
    except Exception:  # noqa: BLE001
        _CACHE[key] = (time.monotonic() + 10, prefs)
        return prefs
    _CACHE[key] = (time.monotonic() + _TTL, prefs)
    return prefs

"""Import schema for customers (maps onto `Customer`; the phone number is the natural key)."""
from __future__ import annotations

import re

from .phone import normalize_phone
from .schema import FieldSpec, ImportSchema
from .text import ParseError, clean_str, norm_text

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
_TRUE = {"yes", "y", "true", "1", "نعم", "موافق", "اجل", "ايوه", "صح", "تم", "مشترك"}
_FALSE = {"no", "n", "false", "0", "لا", "غير موافق", "خطا", "كلا", "ملغي"}
MAX_TAGS, MAX_TAG_LEN = 20, 40


def _text(limit: int, what: str):
    def parse(raw: object) -> str:
        s = clean_str(raw)
        if s is None:
            raise ParseError(f"{what}: فارغ")
        if len(s) > limit:
            raise ParseError(f"{what}: أطول من {limit} حرفًا")
        return s
    return parse


def _phone(raw: object) -> str:
    return normalize_phone(raw)


def _email(raw: object) -> str:
    s = clean_str(raw)
    if s is None or not _EMAIL.match(s) or len(s) > 254:
        raise ParseError(f"البريد الإلكتروني: «{(s or '')[:30]}» غير صالح")
    return s.lower()


def parse_tags(raw: object) -> list[str]:
    s = clean_str(raw)
    if s is None:
        raise ParseError("الوسوم: فارغ")
    tags: list[str] = []
    for part in re.split(r"[,;،|\n]+", s):
        t = part.strip()
        if not t:
            continue
        if len(t) > MAX_TAG_LEN:
            raise ParseError(f"الوسوم: «{t[:20]}…» أطول من {MAX_TAG_LEN} حرفًا")
        if t not in tags:
            tags.append(t)
    if len(tags) > MAX_TAGS:
        raise ParseError(f"الوسوم: الحد الأقصى {MAX_TAGS} وسمًا")
    return tags


def parse_bool(raw: object, what: str = "القيمة") -> bool:
    key = norm_text(raw)
    if key in _TRUE:
        return True
    if key in _FALSE:
        return False
    raise ParseError(f"{what}: «{str(raw)[:20]}» غير مفهومة (نعم/لا)")


CUSTOMER_SCHEMA = ImportSchema(
    kind="customers",
    key_field="phone",
    fields=(
        FieldSpec("phone", "رقم الجوال", required=True, example="0501234567", parse=_phone, allow_default=False,
                  synonyms=("رقم الجوال", "الجوال", "جوال", "رقم الهاتف", "الهاتف", "هاتف", "موبايل", "رقم الموبايل", "واتساب",
                            "رقم الواتساب", "phone", "mobile", "phone number", "mobile number", "tel", "whatsapp", "cell", "contact")),
        FieldSpec("display_name", "الاسم", example="محمد العتيبي", parse=_text(200, "الاسم"), allow_default=False,
                  synonyms=("الاسم", "اسم", "اسم العميل", "العميل", "name", "full name", "customer name", "customer", "الاسم الكامل")),
        FieldSpec("email", "البريد الإلكتروني", example="name@example.com", parse=_email, allow_default=False,
                  synonyms=("البريد", "البريد الالكتروني", "ايميل", "الايميل", "email", "e mail", "mail", "email address")),
        FieldSpec("city", "المدينة", example="الرياض", parse=_text(100, "المدينة"),
                  synonyms=("المدينه", "مدينه", "city", "town", "الموقع")),
        FieldSpec("tags", "الوسوم", example="مستثمر، فيلا", parse=parse_tags, allow_default=False,
                  synonyms=("الوسوم", "وسوم", "تصنيف", "تصنيفات", "tags", "tag", "labels", "label", "الفئه")),
        FieldSpec("notes", "ملاحظات", example="يفضل الاتصال مساءً", parse=_text(2000, "الملاحظات"), allow_default=False,
                  synonyms=("ملاحظات", "ملاحظه", "notes", "note", "comments", "تعليق", "تعليقات")),
        FieldSpec("is_vip", "عميل VIP", example="لا", parse=lambda r: parse_bool(r, "VIP"), allow_default=False,
                  synonyms=("vip", "عميل vip", "مميز", "عميل مميز")),
        FieldSpec("marketing_opt_in", "موافقة التواصل التسويقي", example="نعم", parse=lambda r: parse_bool(r, "الموافقة التسويقية"),
                  allow_default=False,
                  synonyms=("موافقه", "الموافقه", "موافقه تسويقيه", "الموافقه التسويقيه", "opt in", "optin", "marketing opt in",
                            "consent", "marketing consent", "موافق على التواصل", "اشتراك")),
    ),
)

"""Import schema for property listings (maps onto `PropertyListing` columns)."""
from __future__ import annotations

import re
from typing import Any

from .schema import FieldSpec, ImportSchema
from .text import ParseError, clean_str, norm_text, parse_area, parse_int, parse_number

PROPERTY_TYPES = ("apartment", "villa", "land", "commercial", "daily_rental", "office", "warehouse")
LISTING_STATUSES = (
    "PENDING_VERIFICATION", "VERIFIED_ACTIVE", "VERIFICATION_FAILED", "SUSPENDED", "SOLD", "RENTED", "WITHDRAWN",
)

# normalised phrase -> canonical value. Longer phrases are tried first.
_TYPE_WORDS: dict[str, str] = {
    "apartment": "apartment", "flat": "apartment", "شقه": "apartment", "شقق": "apartment", "وحده سكنيه": "apartment",
    "استوديو": "apartment", "studio": "apartment", "دور": "apartment", "روف": "apartment",
    "villa": "villa", "فيلا": "villa", "فله": "villa", "دوبلكس": "villa", "قصر": "villa", "تاون هاوس": "villa",
    "land": "land", "اض": "land", "ارض": "land", "قطعه": "land", "قطعه ارض": "land", "اراضي": "land", "مخطط": "land",
    "commercial": "commercial", "تجاري": "commercial", "محل": "commercial", "معرض": "commercial",
    "shop": "commercial", "عماره": "commercial", "برج": "commercial", "مبني تجاري": "commercial",
    "daily rental": "daily_rental", "daily_rental": "daily_rental", "ايجار يومي": "daily_rental",
    "يومي": "daily_rental", "شاليه": "daily_rental", "استراحه": "daily_rental", "مفروش": "daily_rental",
    "office": "office", "مكتب": "office", "مكاتب": "office",
    "warehouse": "warehouse", "مستودع": "warehouse", "مخزن": "warehouse", "ورشه": "warehouse", "هنجر": "warehouse",
}
_STATUS_WORDS: dict[str, str] = {
    "pending verification": "PENDING_VERIFICATION", "pending": "PENDING_VERIFICATION", "قيد التحقق": "PENDING_VERIFICATION",
    "بانتظار التحقق": "PENDING_VERIFICATION", "قيد الانتظار": "PENDING_VERIFICATION",
    "verified active": "VERIFIED_ACTIVE", "active": "VERIFIED_ACTIVE", "نشط": "VERIFIED_ACTIVE", "نشط ومعتمد": "VERIFIED_ACTIVE",
    "معتمد": "VERIFIED_ACTIVE", "متاح": "VERIFIED_ACTIVE", "available": "VERIFIED_ACTIVE",
    "verification failed": "VERIFICATION_FAILED", "failed": "VERIFICATION_FAILED", "فشل التحقق": "VERIFICATION_FAILED",
    "suspended": "SUSPENDED", "موقوف": "SUSPENDED", "معلق": "SUSPENDED",
    "sold": "SOLD", "مباع": "SOLD", "تم البيع": "SOLD",
    "rented": "RENTED", "مؤجر": "RENTED", "تم التاجير": "RENTED",
    "withdrawn": "WITHDRAWN", "مسحوب": "WITHDRAWN", "ملغي": "WITHDRAWN",
}
_TYPE_INDEX = {norm_text(k): v for k, v in _TYPE_WORDS.items()}
_STATUS_INDEX = {norm_text(k): v for k, v in _STATUS_WORDS.items()}


def _lookup(raw: object, index: dict[str, str], what: str, allowed: tuple[str, ...]) -> str:
    cell = clean_str(raw)
    if cell is None:
        raise ParseError(f"{what}: فارغ")
    if cell in allowed:
        return cell
    key = norm_text(cell)
    if key.upper().replace(" ", "_") in allowed:
        return key.upper().replace(" ", "_")
    if key in index:
        return index[key]
    # "شقة للبيع", "فيلا دورين": accept when exactly one known word appears as a whole word.
    hits = {v for k, v in index.items() if re.search(rf"(?<!\w){re.escape(k)}(?!\w)", key)}
    if len(hits) == 1:
        return hits.pop()
    raise ParseError(f"{what}: «{cell[:40]}» غير معروف")


def _type(raw: object) -> str:
    return _lookup(raw, _TYPE_INDEX, "نوع العقار", PROPERTY_TYPES)


def _status(raw: object) -> str:
    return _lookup(raw, _STATUS_INDEX, "الحالة", LISTING_STATUSES)


def _text(limit: int, what: str):
    def parse(raw: object) -> str:
        s = clean_str(raw)
        if s is None:
            raise ParseError(f"{what}: فارغ")
        if len(s) > limit:
            raise ParseError(f"{what}: أطول من {limit} حرفًا")
        return s
    return parse


def _money(raw: object) -> float:
    v = parse_number(raw, field="السعر")
    if v < 0:
        raise ParseError("السعر: لا يمكن أن يكون سالبًا")
    return v


def _area(raw: object) -> float:
    v = parse_area(raw)
    if v < 0:
        raise ParseError("المساحة: لا يمكن أن تكون سالبة")
    return v


def _bounded_int(name: str, hi: int):
    def parse(raw: object) -> int:
        v = parse_int(raw, field=name)
        if not 0 <= v <= hi:
            raise ParseError(f"{name}: يجب أن يكون بين 0 و{hi}")
        return v
    return parse


def _coord(name: str, limit: float):
    def parse(raw: object) -> float:
        v = parse_number(raw, field=name, allow_multiplier=False)
        if not -limit <= v <= limit:
            raise ParseError(f"{name}: خارج النطاق ±{int(limit)}")
        return v
    return parse


def _coordinates(raw: object) -> dict[str, float]:
    """"24.7136, 46.6753" (lat, lng) or "24.7136;46.6753"."""
    s = clean_str(raw)
    parts = [p for p in re.split(r"[;,،\s]+", s or "") if p]
    if len(parts) != 2:
        raise ParseError("الإحداثيات: الصيغة المتوقعة «خط العرض, خط الطول»")
    return {"latitude": _coord("خط العرض", 90)(parts[0]), "longitude": _coord("خط الطول", 180)(parts[1])}


def _rega(raw: object) -> str:
    s = clean_str(raw)
    if s is None:
        raise ParseError("رقم الإعلان: فارغ")
    s = re.sub(r"\s+", "", s)
    if len(s) > 30:
        raise ParseError("رقم الإعلان: أطول من 30 حرفًا")
    return s


_HAS_ARABIC = re.compile(r"[؀-ۿ]")


def _finalize(row: dict[str, Any]) -> list[str]:
    """A latin-only description column goes to description_en instead of description_ar."""
    warnings: list[str] = []
    desc = row.get("description_ar")
    if desc and not _HAS_ARABIC.search(desc) and re.search(r"[A-Za-z]{3}", desc) and not row.get("description_en"):
        row["description_en"], row["description_ar"] = desc, None
        warnings.append("الوصف بالإنجليزية فنُقل إلى حقل الوصف الإنجليزي")
    if row.get("status") == "VERIFIED_ACTIVE" and not row.get("rega_ad_number"):
        warnings.append("حالة «نشط» بدون رقم إعلان الهيئة: سيُنشأ رقم مؤقت DEV-REGA")
    return warnings


PROPERTY_SCHEMA = ImportSchema(
    kind="properties",
    key_field="rega_ad_number",
    finalize=_finalize,
    fields=(
        FieldSpec("property_type", "نوع العقار", required=True, example="شقة", parse=_type,
                  synonyms=("نوع العقار", "نوع", "النوع", "تصنيف", "الفئة", "type", "property type", "category", "نوع الوحدة")),
        FieldSpec("city", "المدينة", example="الرياض", parse=_text(100, "المدينة"),
                  synonyms=("المدينة", "مدينة", "city", "town", "المنطقة الادارية")),
        FieldSpec("district", "الحي", example="النرجس", parse=_text(100, "الحي"),
                  synonyms=("الحي", "حي", "district", "neighborhood", "neighbourhood", "المنطقة", "الموقع", "area")),
        FieldSpec("price", "السعر", example="850000", parse=_money,
                  synonyms=("السعر", "سعر", "price", "المبلغ", "القيمه", "السعر ريال", "price sar", "سعر البيع", "الايجار")),
        FieldSpec("area_sqm", "المساحة (م²)", example="180", parse=_area,
                  synonyms=("المساحه", "مساحه", "area", "area sqm", "المساحه م2", "size", "المساحه بالمتر", "sqm", "متر")),
        FieldSpec("bedrooms", "غرف النوم", example="4", parse=_bounded_int("غرف النوم", 100),
                  synonyms=("الغرف", "غرف", "عدد الغرف", "غرف النوم", "bedrooms", "beds", "rooms", "bedroom")),
        FieldSpec("bathrooms", "دورات المياه", example="3", parse=_bounded_int("دورات المياه", 50),
                  synonyms=("الحمامات", "حمامات", "عدد الحمامات", "دورات المياه", "bathrooms", "baths", "bathroom")),
        FieldSpec("rega_ad_number", "رقم إعلان الهيئة (REGA)", example="7200012345", parse=_rega,
                  synonyms=("رقم الاعلان", "رقم اعلان الهيئه", "رقم الترخيص", "رخصه الاعلان", "rega", "rega ad number",
                            "ad number", "ad no", "رقم اعلان", "فال", "ترخيص")),
        FieldSpec("status", "الحالة", example="نشط ومعتمد", parse=_status,
                  synonyms=("الحاله", "حاله", "status", "حاله العقار", "حاله الاعلان")),
        FieldSpec("coordinates", "الإحداثيات", example="24.7136, 46.6753", parse=_coordinates,
                  outputs=("latitude", "longitude"), allow_default=False,
                  synonyms=("الاحداثيات", "احداثيات", "coordinates", "coords", "lat lng", "latlng", "الموقع الجغرافي", "location coordinates")),
        FieldSpec("latitude", "خط العرض", example="24.7136", parse=_coord("خط العرض", 90), allow_default=False,
                  synonyms=("خط العرض", "lat", "latitude")),
        FieldSpec("longitude", "خط الطول", example="46.6753", parse=_coord("خط الطول", 180), allow_default=False,
                  synonyms=("خط الطول", "lng", "lon", "long", "longitude")),
        FieldSpec("description_ar", "الوصف", example="شقة فاخرة بإطلالة على الحديقة", parse=_text(5000, "الوصف"),
                  synonyms=("الوصف", "وصف", "تفاصيل", "description", "details", "notes", "ملاحظات", "الوصف عربي", "وصف عربي", "description ar")),
        FieldSpec("description_en", "الوصف (إنجليزي)", example="Luxury apartment", parse=_text(5000, "Description"),
                  synonyms=("description en", "english description", "الوصف انجليزي", "وصف انجليزي", "الوصف بالانجليزي")),
    ),
)

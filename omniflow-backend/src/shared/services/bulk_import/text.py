"""Arabic-aware text and number normalisation for imported spreadsheet cells."""
from __future__ import annotations

import re
import unicodedata

_ARABIC_INDIC = "٠١٢٣٤٥٦٧٨٩"
_PERSIAN = "۰۱۲۳۴۵۶۷۸۹"
_DIGIT_MAP = {ord(c): str(i) for i, c in enumerate(_ARABIC_INDIC)}
_DIGIT_MAP.update({ord(c): str(i) for i, c in enumerate(_PERSIAN)})
_DIGIT_MAP[ord("٫")] = "."   # Arabic decimal separator
_DIGIT_MAP[ord("٬")] = ","   # Arabic thousands separator
_DIGIT_MAP[ord("،")] = ","   # Arabic comma

_DIACRITICS = re.compile("[ً-ٰٟـ]")  # harakat, dagger alef, tatweel


def to_ascii_digits(value: str) -> str:
    """Arabic-Indic / Persian digits and separators -> ASCII."""
    return value.translate(_DIGIT_MAP)


def norm_text(value: object) -> str:
    """Canonical comparison form: ascii digits, no diacritics, unified letters, lower-case."""
    s = unicodedata.normalize("NFKC", str(value or ""))
    s = to_ascii_digits(s)
    s = _DIACRITICS.sub("", s)
    s = re.sub("[أإآٱ]", "ا", s)
    s = s.replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)          # punctuation -> space (keeps arabic/latin/digits)
    return re.sub(r"[\s_]+", " ", s).strip()


# ── numbers ──────────────────────────────────────────────────────────────────

_MULTIPLIERS = (
    (re.compile(r"(مليار|billion|\bbn\b)"), 1_000_000_000),
    (re.compile(r"(مليون|million|\bmm?\b|\bmn\b)"), 1_000_000),
    (re.compile(r"(الف|الاف|\bk\b|thousand)"), 1_000),
)
_NUMBER = re.compile(r"[-\u2212\u2013]?(?:\d[\d,]*(?:\.\d+)?|\.\d+)")


class ParseError(ValueError):
    """A cell could not be interpreted (message is user-facing Arabic)."""


def parse_number(raw: object, *, field: str = "القيمة", allow_multiplier: bool = True) -> float:
    """
    "850,000" / "٨٥٠٬٠٠٠" / "850000 ريال" / "1.2 مليون" / "1,2 مليون" / "850 ألف" / "1.5M" -> float.

    A comma is a thousands separator, except when the number also carries a
    multiplier word and has the shape "d,d{1,2}" ("1,5 مليون" == 1.5 million).
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise ParseError(f"{field}: فارغ")
    if isinstance(raw, bool):
        raise ParseError(f"{field}: قيمة غير رقمية")
    if isinstance(raw, (int, float)):
        return float(raw)
    s_raw = to_ascii_digits(str(raw)).strip().lower()
    # "1.5M" -> "1.5 M": give the multiplier letters a word boundary after the digits.
    s = norm_text(re.sub(r"(?<=\d)(?=[^\d\s.,])", " ", s_raw))
    match = _NUMBER.search(s_raw)
    if not match:
        raise ParseError(f"{field}: «{str(raw)[:40]}» ليست رقمًا")
    num = match.group(0).replace("\u2212", "-").replace("\u2013", "-")
    multiplier = 1
    for pattern, factor in _MULTIPLIERS if allow_multiplier else ():
        if pattern.search(s):
            multiplier = factor
            break
    if multiplier > 1 and re.fullmatch(r"-?\d+,\d{1,2}", num):
        num = num.replace(",", ".")
    else:
        num = num.replace(",", "")
    try:
        value = float(num) * multiplier
    except ValueError as exc:  # pragma: no cover - guarded by the regex
        raise ParseError(f"{field}: «{str(raw)[:40]}» ليست رقمًا") from exc
    if value != value or value in (float("inf"), float("-inf")):
        raise ParseError(f"{field}: قيمة غير صالحة")
    return value


_AREA_UNITS = re.compile(r"(م\s*[²2]|متر(?:\s*مربع)?|م٢|sq\.?\s*m|sqm|m²|m2|\bm\b|\bsqft\b)", re.I)


def parse_area(raw: object, *, field: str = "المساحة") -> float:
    """"180 م²" / "180م2" / "٢٥٠ متر مربع" / "300 sqm" -> float (square metres)."""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    cleaned = _AREA_UNITS.sub(" ", to_ascii_digits(str(raw or "")))
    return parse_number(cleaned, field=field, allow_multiplier=False)


def parse_int(raw: object, *, field: str = "القيمة") -> int:
    value = parse_number(raw, field=field, allow_multiplier=False)
    if value != int(value):
        raise ParseError(f"{field}: يجب أن يكون عددًا صحيحًا")
    return int(value)


def clean_str(raw: object) -> str | None:
    """Trim, drop NULs/control characters, empty -> None."""
    if raw is None:
        return None
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(raw)).strip()
    return s or None

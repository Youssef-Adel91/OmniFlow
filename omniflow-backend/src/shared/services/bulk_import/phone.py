"""Phone normalisation to E.164 (Saudi-first) shared by manual forms and bulk imports."""
from __future__ import annotations

import re

from .text import ParseError, to_ascii_digits

E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def is_e164(value: object) -> bool:
    return bool(isinstance(value, str) and E164.match(value))


def normalize_phone(raw: object) -> str:
    """
    "0501234567" / "501234567" / "966501234567" / "00966 50 123 4567" / "+966-50-123-4567" /
    "٠٥٠١٢٣٤٥٦٧" / "966501234567.0" (Excel float)  ->  "+966501234567".
    Other countries are accepted when they already look like E.164 (+ and 8-15 digits).
    """
    s = to_ascii_digits(str(raw if raw is not None else "")).strip()
    if not s:
        raise ParseError("رقم الجوال: فارغ")
    s = re.sub(r"\.0+$", "", s)                       # Excel numeric cell
    plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if not digits:
        raise ParseError("رقم الجوال: غير صالح")
    if not plus and digits.startswith("00"):
        plus, digits = True, digits[2:]
    if plus:
        out = "+" + digits
    elif re.fullmatch(r"05\d{8}", digits):
        out = "+966" + digits[1:]
    elif re.fullmatch(r"5\d{8}", digits):
        out = "+966" + digits
    elif re.fullmatch(r"9665\d{8}", digits):
        out = "+" + digits
    elif re.fullmatch(r"9660?5\d{8}", digits):
        out = "+966" + digits[3:].lstrip("0")
    else:
        raise ParseError(f"رقم الجوال: «{str(raw)[:20]}» غير صالح (مثال: 0501234567 أو +966501234567)")
    if out.startswith("+9660"):                        # +966 05xxxxxxxx -> drop the trunk zero
        out = "+966" + out[5:]
    if out.startswith("+966") and not re.fullmatch(r"\+9665\d{8}", out):
        if not re.fullmatch(r"\+966[1-9]\d{7,8}", out):   # landlines (11/12/13...) are allowed
            raise ParseError(f"رقم الجوال: «{str(raw)[:20]}» غير صالح للسعودية")
    if not E164.match(out):
        raise ParseError(f"رقم الجوال: «{str(raw)[:20]}» غير صالح")
    return out

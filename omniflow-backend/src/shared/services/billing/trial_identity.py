"""One free trial per company: hashed commercial-registration number and phone are remembered (SHA-256 of the normalised value) so the same
company cannot restart a trial under a new account. Only hashes are stored."""
from __future__ import annotations

import hashlib
import re
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.shared.db.models import TrialIdentity


class TrialAlreadyUsed(Exception):
    def __init__(self, kind: str):
        self.kind = kind
        super().__init__("هذه الشركة استخدمت الفترة التجريبية من قبل. اختر باقة مدفوعة أو تواصل مع الدعم.")


def normalise(kind: str, value: str) -> str | None:
    v = (value or "").strip()
    if kind == "cr":
        v = re.sub(r"\D", "", v)
        return v if 8 <= len(v) <= 15 else None
    if kind == "phone":
        digits = re.sub(r"\D", "", v)
        if digits.startswith("00"):
            digits = digits[2:]
        if digits.startswith("0") and len(digits) == 10:       # Saudi national format 05xxxxxxxx
            digits = "966" + digits[1:]
        return digits if 9 <= len(digits) <= 15 else None
    raise ValueError(kind)


def digest(kind: str, value: str) -> str | None:
    n = normalise(kind, value)
    return hashlib.sha256(f"{kind}:{n}".encode()).hexdigest() if n else None


async def claim(session, tenant_id: uuid.UUID, *, cr: str | None = None, phone: str | None = None) -> None:
    """Record the identifiers for this tenant; raise TrialAlreadyUsed if another tenant already holds one of them."""
    for kind, value in (("cr", cr), ("phone", phone)):
        h = digest(kind, value) if value else None
        if not h:
            continue
        owner = await session.scalar(select(TrialIdentity.tenant_id).where(TrialIdentity.kind == kind, TrialIdentity.value_hash == h))
        if owner is not None and owner != tenant_id:
            raise TrialAlreadyUsed(kind)
        await session.execute(pg_insert(TrialIdentity).values(identity_id=uuid.uuid4(), tenant_id=tenant_id, kind=kind, value_hash=h)
                              .on_conflict_do_nothing(constraint="uq_trial_identity"))

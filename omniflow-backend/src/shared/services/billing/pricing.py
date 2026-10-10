"""Pure money / period maths for billing. No I/O, fully unit-tested. All amounts are integer halalas (SAR × 100)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from dateutil.relativedelta import relativedelta

INTERVALS = ("monthly", "yearly")


@dataclass(frozen=True)
class Amounts:
    subtotal: int     # VAT-exclusive
    vat: int
    total: int        # what the customer pays


def with_vat(subtotal_halalas: int, vat_rate_bp: int) -> Amounts:
    """Add VAT on top of a VAT-exclusive price. Rounds half-up to the halala; total = subtotal + vat exactly."""
    if subtotal_halalas < 0:
        raise ValueError("negative amount")
    vat = (subtotal_halalas * vat_rate_bp + 5000) // 10000
    return Amounts(subtotal_halalas, vat, subtotal_halalas + vat)


def plan_price(plan, interval: str) -> int:
    if interval not in INTERVALS:
        raise ValueError(f"unknown interval {interval!r}")
    return plan.price_yearly_halalas if interval == "yearly" else plan.price_monthly_halalas


def monthly_equivalent(plan, interval: str) -> int:
    return plan_price(plan, interval) // (12 if interval == "yearly" else 1)


def add_interval(start: datetime, interval: str) -> datetime:
    """Calendar-aware: Jan 31 + 1 month = Feb 28/29 (never skips a month)."""
    if interval == "yearly":
        return start + relativedelta(years=1)
    if interval == "monthly":
        return start + relativedelta(months=1)
    raise ValueError(f"unknown interval {interval!r}")


def prorated_upgrade(old_price: int, new_price: int, period_start: datetime, period_end: datetime, now: datetime) -> int:
    """VAT-exclusive charge for moving to a dearer plan mid-period (same interval): the price difference for the time left.

    The period dates do not change. Never negative; zero once the period is over.
    """
    total = (period_end - period_start).total_seconds()
    left = (period_end - max(now, period_start)).total_seconds()
    if total <= 0 or left <= 0 or new_price <= old_price:
        return 0
    return round((new_price - old_price) * (left / total))


def invoice_number(seq: int, year: int) -> str:
    return f"INV-{year}-{seq:06d}"

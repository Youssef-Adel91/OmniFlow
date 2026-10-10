"""Subscription state → what a tenant may do right now. Pure functions; the DB layer feeds them a row and `now`.

Statuses: trial → active → past_due → suspended → cancelled. A status is only ever *changed* by a verified webhook,
a manual staff activation, or the scheduled sweep; this module only *reads* it (and is robust if the sweep is down).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

FULL, GRACE, BLOCKED = "full", "grace", "blocked"


@dataclass(frozen=True)
class Access:
    mode: str                       # full | grace | blocked
    status: str                     # the stored subscription status
    reason: str | None = None       # trial_expired | payment_overdue | suspended | cancelled
    banner: str | None = None       # trial_ending | past_due | None
    days_left: int | None = None    # trial days left / grace days left

    @property
    def bot_enabled(self) -> bool:
        return self.mode != BLOCKED

    @property
    def writable(self) -> bool:
        return self.mode != BLOCKED


def _days(delta: timedelta) -> int:
    return max(0, -(-int(delta.total_seconds()) // 86400))     # ceil


def evaluate(sub: Any | None, now: datetime, *, grace_days: int, trial_warn_days: int = 3, gate_enabled: bool = True) -> Access:
    if not gate_enabled or sub is None or getattr(sub, "is_exempt", False):
        return Access(FULL, getattr(sub, "status", "active") or "active")
    st = sub.status
    if st == "trial":
        end = sub.trial_ends_at
        if end is None:
            return Access(FULL, st)
        if now > end:
            return Access(BLOCKED, st, "trial_expired")
        left = _days(end - now)
        return Access(FULL, st, banner="trial_ending" if left <= trial_warn_days else None, days_left=left)
    if st == "active":
        end = sub.current_period_end
        if sub.cancel_at_period_end and end is not None and now > end:
            return Access(BLOCKED, st, "cancelled")
        if end is not None and now > end:
            # Renewal overdue: a short window while the sweep retries the charge, then a hard stop even if the sweep is down.
            limit = end + timedelta(days=grace_days)
            if now > limit:
                return Access(BLOCKED, st, "payment_overdue")
            return Access(GRACE, st, banner="past_due", days_left=_days(limit - now))
        return Access(FULL, st)
    if st == "past_due":
        gend = sub.grace_ends_at
        if gend is not None and now <= gend:
            return Access(GRACE, st, banner="past_due", days_left=_days(gend - now))
        return Access(BLOCKED, st, "payment_overdue")
    if st == "suspended":
        return Access(BLOCKED, st, "suspended")
    if st == "cancelled":
        return Access(BLOCKED, st, "cancelled")
    return Access(BLOCKED, st, "suspended")      # unknown status: fail closed

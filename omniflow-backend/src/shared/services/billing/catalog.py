"""Plan limit vocabulary and the free-trial caps (single place; the plan rows themselves live in the `plans` table).

Limit keys inside `plans.limits` (a missing key = unlimited):
    messages_per_month   AI replies per month (the hard cap lives in the LLM invoker, see credits.py)
    seats                team members (active users + pending invitations)
    whatsapp_numbers     connected WhatsApp numbers (the data model holds ONE per tenant today, so this can never be exceeded)
    properties           property listings
    contacts             customer records
    campaigns            False = broadcast campaigns not available on the plan
    channels             number of connected channel groups (WhatsApp / Meta page) — trial only
"""
from __future__ import annotations

LABELS_AR = {
    "properties": "العقارات",
    "contacts": "جهات الاتصال",
    "seats": "المقاعد",
    "channels": "القنوات",
    "whatsapp_numbers": "أرقام الواتساب",
    "campaigns": "الحملات التسويقية",
    "messages_per_month": "ردود الذكاء الاصطناعي",
}

# Free trial (no card): 14 days (billing_trial_days), see PRICING_STUDY. `contacts` is deliberately unset (not specified by the owner).
TRIAL_LIMITS: dict = {
    "messages_per_month": 300,
    "channels": 1,
    "seats": 2,
    "properties": 50,
    "campaigns": False,
}

ADDON_LIMIT_KEYS = {"seats": "addon_seats", "whatsapp_numbers": "addon_whatsapp_numbers"}


def effective_limits(sub, plan) -> dict | None:
    """What this subscription may use right now. None = unlimited (exempt / internal tenants, or no subscription)."""
    if sub is None or getattr(sub, "is_exempt", False):
        return None
    if sub.status == "trial":
        return dict(TRIAL_LIMITS)
    limits = dict((plan.limits if plan is not None else None) or {})
    for key, column in ADDON_LIMIT_KEYS.items():
        extra = int(getattr(sub, column, 0) or 0)
        if extra and limits.get(key):
            limits[key] = int(limits[key]) + extra
    return limits

"""
Whether (and how) to answer a Facebook/Instagram post comment. Pure decision logic + two Redis counters; the router acts on the result.

Order of checks (cheapest first): platform flag → the account's own comment → a reply inside a thread → empty text → the tenant's switch →
duplicate (same comment_id seen in the last 7 days) → text rules (emoji-only / ignore words → IGNORE, insults / spam → HIDE) →
business hours (optional) → per-tenant rate limit per minute → per-tenant cap per hour. Anything not answered is IGNORED, never queued.
Actions: ignore | hide | public_reply | private_dm | public_and_private.

Conservative choices:
  * Redis unavailable → the comment is ignored (we cannot prove it is not a duplicate, and a public reply cannot be taken back).
  * Rate limit applies to public and private replies alike (`SOCIAL_COMMENT_RATE_PER_MINUTE`, default 30 per tenant).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from src.channel_adapters.instagram import comment_moderation
from src.shared.core.config import get_settings
from src.shared.redis_client import redis_mgr
from src.shared.services import channel_health, tenant_prefs

SEEN_TTL_S = 7 * 24 * 3600          # Meta only allows a private reply within 7 days of the comment, so that is also how long we remember it


@dataclass(frozen=True)
class Decision:
    action: str          # "ignore" | "hide" | "public_reply" | "private_dm" | "public_and_private"
    reason: str

    @property
    def replies(self) -> bool:
        """True when the assistant will write to the customer (public and/or private)."""
        return self.action in ("public_reply", "private_dm", "public_and_private")


def enabled() -> bool:
    return bool(get_settings().feature_social_comment_replies)


def facebook_post_url(post_id: str | None) -> str | None:
    """Facebook accepts https://www.facebook.com/{page_id}_{post_id} (the webhook's post_id format). Instagram permalinks need a Graph lookup: None."""
    pid = (post_id or "").strip()
    return f"https://www.facebook.com/{pid}" if pid and pid.replace("_", "").isdigit() else None


def is_thread_reply(*, parent_id: str | None, post_id: str | None = None) -> bool:
    """A reply to another comment. On Facebook a top-level comment's parent_id equals the post id; on Instagram top-level comments have no parent_id."""
    return bool(parent_id) and parent_id != post_id


async def decide(*, tenant_id: uuid.UUID, comment_id: str, sender_id: str, own_ids: set[str], thread_reply: bool, text: str,
                 now: datetime | None = None, platform: str | None = None) -> Decision:
    cfg = get_settings()
    if not cfg.feature_social_comment_replies:
        return Decision("ignore", "feature_off")
    if sender_id in own_ids:
        return Decision("ignore", "own_comment")           # our own reply comes back as a new webhook: answering it would loop
    if thread_reply:
        return Decision("ignore", "reply_in_thread")
    if not (text or "").strip():
        return Decision("ignore", "empty_text")
    prefs_all = await tenant_prefs.get_prefs(tenant_id)
    pref = tenant_prefs.with_defaults("social_comments", prefs_all.get("social_comments"))
    if not pref.get("enabled"):
        return Decision("ignore", "tenant_disabled")
    if platform and await channel_health.get_failure(tenant_id, platform):
        # The Page/Instagram token is known to be dead: every hide/reply would fail at Meta and (for answers) burn AI quota for nothing. The tenant already has the
        # "reconnect" banner + notification; the decision stays visible in the recent-comments list. Cleared by a reconnect or by the next successful send.
        return Decision("ignore", "channel_needs_reconnect")
    seen = await redis_mgr.incr_counter(f"comment:seen:{comment_id}", ttl=SEEN_TTL_S)
    if seen is None:
        return Decision("ignore", "dedupe_unavailable")
    if seen > 1:
        return Decision("ignore", "duplicate")
    verdict = comment_moderation.classify_text(text, ignore_keywords=pref["ignore_keywords"], hide_keywords=pref["hide_keywords"],
                                               auto_hide_abusive=pref["auto_hide_abusive"])
    if verdict is not None:
        return Decision(verdict.action, verdict.reason)
    now = now or datetime.now(timezone.utc)
    if pref["only_business_hours"] and not tenant_prefs.is_open(tenant_prefs.with_defaults("business_hours", prefs_all.get("business_hours")), now):
        return Decision("ignore", "outside_business_hours")
    minute = int(now.timestamp() // 60)
    rate = await redis_mgr.incr_counter(f"comment:rl:{tenant_id}:{minute}", ttl=70)
    if rate is not None and rate > cfg.social_comment_rate_per_minute:
        return Decision("ignore", "rate_limited")
    hour = int(now.timestamp() // 3600)
    per_hour = await redis_mgr.incr_counter(f"comment:rlh:{tenant_id}:{hour}", ttl=3700)
    if per_hour is not None and per_hour > pref["max_per_hour"]:
        return Decision("ignore", "hourly_cap")
    return Decision(pref["mode"], "ok")

"""
Side effects of a comment decision (see comment_policy.decide), kept out of the webhook router so they can be tested alone:

  * hide_comment_for_tenant   -> hide an insulting / spam comment through the Graph API.
  * post_public_template      -> the fixed public reply of "public_and_private" mode.
  * record / recent           -> the "last 20 comments" list shown in Settings → Channels (Redis, 14 days, no migration).

All Graph calls use the TENANT's own Page token (never a shared one). Every function swallows its errors after logging: a failed hide or
public reply must never stop the private reply, and the webhook handler must always return 200 to Meta.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import select

from src.channel_adapters.instagram import client as meta_client
from src.channel_adapters.instagram import comment_moderation
from src.shared.db.models import Tenant
from src.shared.db.session import get_tenant_session
from src.shared.redis_client import redis_mgr
from src.shared.security.crypto import reveal_secret
from src.shared.services import channel_health, tenant_prefs

logger = structlog.get_logger(__name__)

ACTIVITY_KEEP = 20
ACTIVITY_TTL_S = 14 * 24 * 3600


def _activity_key(tenant_id: uuid.UUID | str) -> str:
    return f"comment:activity:{tenant_id}"


async def page_access_token(tenant_id: uuid.UUID) -> str | None:
    """The tenant's own Page token (Fernet-decrypted, or legacy plaintext); None when nothing is connected."""
    async with get_tenant_session(tenant_id) as session:
        stored = await session.scalar(select(Tenant.instagram_page_access_token).where(Tenant.tenant_id == tenant_id))
    return reveal_secret(stored)


async def record(tenant_id: uuid.UUID, *, platform: str, comment_id: str, action: str, reason: str, text: str, sender_name: str | None = None,
                 public_reply: str | None = None) -> None:
    """Prepend one entry to the tenant's recent-activity list (best effort; the list is capped at 20)."""
    entry = {"at": datetime.now(timezone.utc).isoformat(), "platform": platform, "comment_id": comment_id, "action": action, "reason": reason,
             "preview": (text or "").strip().replace("\n", " ")[:80], "name": (sender_name or "")[:40] or None, "public_reply": public_reply}
    try:
        raw = await redis_mgr.get_raw(_activity_key(tenant_id))
        items = json.loads(raw) if raw else []
        await redis_mgr.set_raw(_activity_key(tenant_id), json.dumps([entry, *items][:ACTIVITY_KEEP], ensure_ascii=False), ttl=ACTIVITY_TTL_S)
    except Exception as exc:  # noqa: BLE001
        logger.warning("comment_activity_record_failed", error=type(exc).__name__)


async def recent(tenant_id: uuid.UUID | str) -> list[dict]:
    try:
        raw = await redis_mgr.get_raw(_activity_key(tenant_id))
        return json.loads(raw) if raw else []
    except Exception:  # noqa: BLE001
        return []


async def hide_comment_for_tenant(tenant_id: uuid.UUID, *, platform: str, comment_id: str) -> bool:
    """True when Meta accepted the hide. A token problem is reported to the tenant (channel_health); anything else is just logged."""
    try:
        token = await page_access_token(tenant_id)
        if not token:
            logger.warning("comment_hide_no_token", platform=platform)
            return False
        await meta_client.hide_comment(comment_id, platform=platform, access_token=token)
        await channel_health.note_success(tenant_id, "instagram" if platform == "instagram" else "facebook")
        return True
    except meta_client.MetaAuthError as exc:
        await channel_health.mark_auth_failed(tenant_id, "instagram" if platform == "instagram" else "facebook", str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("comment_hide_failed", platform=platform, error=type(exc).__name__, detail=str(exc)[:200])
    return False


async def post_public_template(tenant_id: uuid.UUID, *, platform: str, comment_id: str, sender_name: str | None) -> str | None:
    """Post the tenant's fixed public reply under the comment. Returns the text posted, or None if it could not be posted."""
    try:
        pref = tenant_prefs.with_defaults("social_comments", (await tenant_prefs.get_prefs(tenant_id)).get("social_comments"))
        text = comment_moderation.render_public_template(pref["public_template"], name=sender_name)
        token = await page_access_token(tenant_id)
        if not token:
            logger.warning("comment_public_no_token", platform=platform)
            return None
        send = meta_client.send_instagram_comment_reply if platform == "instagram" else meta_client.send_comment_reply
        await send(comment_id=comment_id, text=text, access_token=token)
        await channel_health.note_success(tenant_id, "instagram" if platform == "instagram" else "facebook")
        return text
    except meta_client.MetaAuthError as exc:
        await channel_health.mark_auth_failed(tenant_id, "instagram" if platform == "instagram" else "facebook", str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("comment_public_template_failed", platform=platform, error=type(exc).__name__, detail=str(exc)[:200])
    return None


# Reasons that are noise rather than a decision the tenant might want to see in "recent comments".
_SILENT_REASONS = frozenset({"feature_off", "tenant_disabled", "own_comment", "reply_in_thread", "empty_text", "duplicate", "dedupe_unavailable"})


async def handle_decision(decision, *, tenant_id: uuid.UUID, platform: str, comment_id: str, text: str, sender_name: str | None) -> bool:
    """
    Carry out everything about a decision that is NOT the assistant's own AI reply, and log it.
    Returns True when the router should queue the comment for the AI (private reply, or the public answer), False otherwise.
    """
    action = decision.action
    public_reply: str | None = None
    if action == "hide":
        hidden = await hide_comment_for_tenant(tenant_id, platform=platform, comment_id=comment_id)
        await record(tenant_id, platform=platform, comment_id=comment_id, action="hidden" if hidden else "hide_failed", reason=decision.reason, text=text, sender_name=sender_name)
        return False
    if action == "ignore":
        if decision.reason not in _SILENT_REASONS:
            await record(tenant_id, platform=platform, comment_id=comment_id, action="ignored", reason=decision.reason, text=text, sender_name=sender_name)
        return False
    if action == "public_and_private":
        public_reply = await post_public_template(tenant_id, platform=platform, comment_id=comment_id, sender_name=sender_name)
    await record(tenant_id, platform=platform, comment_id=comment_id, action=action, reason=decision.reason, text=text, sender_name=sender_name, public_reply=public_reply)
    return True

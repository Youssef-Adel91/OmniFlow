"""
Telegram Bot API adapter (NOT wired to a route yet — see docs/channels.md "Telegram").

Webhook authenticity: Telegram echoes the `secret_token` given to setWebhook in the header `X-Telegram-Bot-Api-Secret-Token`; there is no body signature. The secret is
therefore compared in constant time and a missing/empty configured secret rejects EVERYTHING (fail closed).
Private chats only: groups, supergroups, channel posts, edited messages and messages sent by bots are ignored (a sales assistant must not answer in a group by accident,
nor loop on another bot).
"""
from __future__ import annotations

import hmac
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

import httpx
import structlog

from src.channel_adapters.base import ChannelSendError
from src.shared.core.enums import Channel, MessageType
from src.shared.events.canonical import CanonicalInboundEvent

logger = structlog.get_logger(__name__)

SECRET_HEADER = "x-telegram-bot-api-secret-token"
_API = "https://api.telegram.org"
_TEXT_LIMIT = 4096                                    # Telegram's hard cap per message


class TelegramAdapter:
    name = "telegram"

    def __init__(self, webhook_secret: str | None) -> None:
        self._secret = (webhook_secret or "").encode()

    # ── authenticity ─────────────────────────────────────────────────────────────────────────────────────────────
    def verify(self, headers: Mapping[str, str], raw_body: bytes) -> bool:
        if not self._secret:
            return False                              # not configured → nothing is trusted
        got = {k.lower(): v for k, v in headers.items()}.get(SECRET_HEADER, "")
        return hmac.compare_digest(got.encode(), self._secret)

    # ── inbound ──────────────────────────────────────────────────────────────────────────────────────────────────
    def parse(self, payload: dict[str, Any], tenant_id: uuid.UUID) -> list[CanonicalInboundEvent]:
        msg = payload.get("message") if isinstance(payload, dict) else None
        if not isinstance(msg, dict):
            return []                                 # edited_message / channel_post / callback_query / my_chat_member … are not conversations
        chat, sender = msg.get("chat") or {}, msg.get("from") or {}
        if chat.get("type") != "private" or sender.get("is_bot") or chat.get("id") is None:
            return []
        chat_id, message_id = str(chat["id"]), msg.get("message_id")
        if message_id is None:
            return []
        name = " ".join(x for x in (sender.get("first_name"), sender.get("last_name")) if x) or sender.get("username")
        ts = datetime.fromtimestamp(msg["date"], tz=timezone.utc) if isinstance(msg.get("date"), int) else datetime.now(tz=timezone.utc)
        base = dict(tenant_id=tenant_id, channel=Channel.TELEGRAM, platform_message_id=f"tg:{chat_id}:{message_id}", platform_conversation_id=chat_id,
                    platform_user_id=chat_id, customer_display_name=name, event_timestamp=ts)
        text = msg.get("text") or msg.get("caption")
        if msg.get("text"):
            return [CanonicalInboundEvent(**base, message_type=MessageType.TEXT, text_content=text)]
        if msg.get("voice") or msg.get("audio"):
            media = msg.get("voice") or msg.get("audio")
            return [CanonicalInboundEvent(**base, message_type=MessageType.AUDIO, text_content=text, media_mime_type=media.get("mime_type"),
                                          media_duration_seconds=float(media["duration"]) if media.get("duration") is not None else None)]
        if msg.get("photo"):
            return [CanonicalInboundEvent(**base, message_type=MessageType.IMAGE, text_content=text)]
        if msg.get("video"):
            return [CanonicalInboundEvent(**base, message_type=MessageType.VIDEO, text_content=text)]
        if msg.get("document"):
            return [CanonicalInboundEvent(**base, message_type=MessageType.DOCUMENT, text_content=text, media_mime_type=(msg["document"] or {}).get("mime_type"))]
        return []                                     # stickers, polls, contacts … : ignored rather than guessed at

    # ── outbound ─────────────────────────────────────────────────────────────────────────────────────────────────
    async def send_text(self, token: str, to: str, text: str, *, client: httpx.AsyncClient | None = None) -> str | None:
        own = client is None
        client = client or httpx.AsyncClient(timeout=15.0)
        try:
            r = await client.post(f"{_API}/bot{token}/sendMessage", json={"chat_id": to, "text": text[:_TEXT_LIMIT]})
        finally:
            if own:
                await client.aclose()
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code == 200 and body.get("ok"):
            return f"tg:{to}:{(body.get('result') or {}).get('message_id')}"
        desc = str(body.get("description") or r.status_code)[:200]            # never log the URL: it carries the bot token
        logger.warning("telegram_send_failed", status=r.status_code, description=desc)
        raise ChannelSendError(f"telegram: {desc}", auth=r.status_code in (401, 404))

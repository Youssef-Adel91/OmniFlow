"""
The contract every channel adapter follows (docs/channels.md). WhatsApp / Instagram / Messenger predate it and implement the same three duties inside their routers;
new channels (Telegram) implement this Protocol so that the router, the dispatcher and the tests treat them uniformly:

    verify(headers, raw_body)      -> bool                         authenticate the webhook BEFORE parsing anything (constant-time compare)
    parse(payload, tenant_id)      -> list[CanonicalInboundEvent]  zero or more events; never raises on a payload it does not understand (returns [])
    send_text(token, to, text)     -> str | None                   the platform message id; raises ChannelSendError on a permanent failure
"""
from __future__ import annotations

import uuid
from typing import Any, Mapping, Protocol, runtime_checkable

from src.shared.events.canonical import CanonicalInboundEvent


class ChannelSendError(Exception):
    """A send the platform refused for good (bad token, blocked bot, unknown chat) — the caller marks the message FAILED instead of retrying."""

    def __init__(self, message: str, *, auth: bool = False) -> None:
        super().__init__(message)
        self.auth = auth          # True: the credential is dead → the channel needs reconnecting (feeds channel_health)


@runtime_checkable
class ChannelAdapter(Protocol):
    name: str

    def verify(self, headers: Mapping[str, str], raw_body: bytes) -> bool: ...

    def parse(self, payload: dict[str, Any], tenant_id: uuid.UUID) -> list[CanonicalInboundEvent]: ...

    async def send_text(self, token: str, to: str, text: str) -> str | None: ...

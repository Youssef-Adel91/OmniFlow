"""shared/events/outbound.py — outbound event contract shared by API and workers."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class OutboundMessage(BaseModel):
    """
    AI-generated response ready for delivery to the customer.

    Consumed by the WhatsApp Sender Worker (Sprint 8) which calls
    the Meta Graph API to deliver the message.

    Sprint 14 additions:
        message_type  — "text" (default) | "audio" (voice note)
        media_url     — Public HTTPS URL to audio/mpeg for voice notes.
                        Always populated alongside `text` so the Smart Inbox
                        can display the transcript even for voice messages.
    """
    message_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    sender_type: Literal["ai_bot", "human_agent", "system"] = "ai_bot"
    agent_id: uuid.UUID | None = None
    tenant_id: uuid.UUID
    conversation_id: uuid.UUID | None = None
    customer_phone: str
    channel: str = "whatsapp"
    platform_conversation_id: str  # wa_id for WhatsApp routing

    # Response content
    text: str
    message_type: str = "text"           # "text" | "audio"
    media_url: str | None = None         # Sprint 14: populated for voice notes

    # Metadata
    source_event_id: uuid.UUID     # The CanonicalInboundEvent that triggered this
    routing_tier_used: str
    model_used: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    def to_kafka_bytes(self) -> bytes:
        return self.model_dump_json().encode("utf-8")

    @classmethod
    def kafka_key(cls, tenant_id: uuid.UUID, phone: str) -> bytes:
        return f"{tenant_id!s}:{phone}".encode("utf-8")


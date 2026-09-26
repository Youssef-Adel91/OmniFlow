"""
shared/events/canonical.py — Canonical Inbound Event Schema

This is the SINGLE CONTRACT between every Channel Adapter (WhatsApp,
TikTok, Instagram, etc.) and every downstream AI Worker / Analytics
Consumer. If a field doesn't fit here, a new event version is needed.

Pipeline position:
    [Channel Adapter] → normalize → CanonicalInboundEvent
                                          │
                               publish to Kafka topic
                               messages.incoming.v1
                                          │
                          [AI Worker / Analytics / Vault Worker]

Design principles:
  - All fields are optional that can be absent for any message type.
  - `master_customer_id` is NULL on ingestion; filled by the Identity
    Resolution worker BEFORE the AI worker consumes the event.
  - Use `model_dump(mode="json")` for Kafka serialization — produces
    JSON-serializable dict with str UUIDs and ISO timestamps.

Reference: SRS §2.3 Step 3 — Canonical Event Schema
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.shared.core.enums import Channel, MessageType


class CanonicalInboundEvent(BaseModel):
    """
    Normalized inbound message event published to Kafka topic:
        messages.incoming.v1  (setting: settings.kafka_topic_messages_incoming)

    All channel adapters MUST produce this schema.
    All AI workers, vault workers, and analytics consumers MUST read this schema.

    Versioning:
        event_version = "1.0" — bump minor for additive fields,
                                 bump major for breaking changes (new topic).
    """
    model_config = ConfigDict(populate_by_name=True)

    # ── Identity ──────────────────────────────────────────────────────────────
    event_id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        description="Globally unique event ID — used for deduplication",
    )
    event_version: str = Field(
        default="1.0",
        description="Schema version — used by consumers for compatibility checks",
    )
    event_timestamp: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
        description="UTC timestamp of message receipt at the adapter",
    )

    # ── Routing ───────────────────────────────────────────────────────────────
    tenant_id: uuid.UUID = Field(
        description="Tenant that owns this WhatsApp number / TikTok account"
    )
    channel: Channel = Field(
        description="Originating channel (whatsapp | tiktok | instagram | ...)"
    )
    platform_message_id: str = Field(
        description="Platform-native message ID — used for at-least-once dedup"
    )
    platform_conversation_id: str = Field(
        description="Platform-native thread / conversation ID"
    )

    # ── Customer Identity ─────────────────────────────────────────────────────
    platform_user_id: str = Field(
        description=(
            "Raw platform identifier (E.164 phone for WhatsApp, "
            "user_id for TikTok/Instagram)"
        )
    )
    customer_phone: str | None = Field(
        default=None,
        description="E.164 phone number — available for WhatsApp only at ingestion",
    )
    customer_display_name: str | None = Field(
        default=None,
        description="Profile name from the platform (WhatsApp profile_name)",
    )
    master_customer_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Resolved by the Identity Resolution worker. "
            "NULL at ingestion — set before AI worker consumes."
        ),
    )

    # ── Message Content ───────────────────────────────────────────────────────
    message_type: MessageType = Field(
        description="Content type: text | audio | image | video | document | ..."
    )
    text_content: str | None = Field(
        default=None,
        description="Plaintext body (type=text) or Whisper transcription (type=audio)",
    )
    media_url: str | None = Field(
        default=None,
        description="Temp S3 URL for downloaded media (audio/image/video/document)",
    )
    media_mime_type: str | None = Field(
        default=None,
        description="MIME type of the media file (audio/ogg, image/jpeg, etc.)",
    )
    media_duration_seconds: float | None = Field(
        default=None,
        description="Duration of audio/video in seconds (for Whisper routing)",
    )
    media_sha256: str | None = Field(
        default=None,
        description="SHA-256 of the downloaded media — for integrity verification",
    )
    location_latitude: float | None = Field(
        default=None,
        description="Latitude for location messages",
    )
    location_longitude: float | None = Field(
        default=None,
        description="Longitude for location messages",
    )
    location_name: str | None = Field(
        default=None,
        description="Location name / address label from the platform",
    )
    interactive_payload: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Raw interactive payload for Quick Reply / List / Button clicks. "
            "Structure varies by platform — pass through as-is for the AI worker."
        ),
    )
    sticker_url: str | None = Field(
        default=None,
        description="S3 URL for sticker image",
    )

    # ── Processing Hints (set by Channel Adapter pre-publish) ─────────────────
    vcard_state: str | None = Field(
        default=None,
        description=(
            "Customer's current VCard state — read from Redis cache by the adapter "
            "so the AI worker can skip a DB lookup for the most common path."
        ),
    )
    is_processing_restricted: bool = Field(
        default=False,
        description=(
            "PDPL opt-out flag — if True, AI worker MUST skip LLM and route "
            "immediately to a human agent."
        ),
    )
    is_human_active: bool = Field(
        default=False,
        description=(
            "True if the conversation is already in HUMAN_ACTIVE state. "
            "If True, route to human_agent outbound topic instead of AI."
        ),
    )
    reply_target_type: Literal["dm", "comment"] = Field(
        default="dm",
        description=(
            "'dm' for a normal 1-to-1 thread (WhatsApp, Messenger/Instagram "
            "DM) where a reply is sent to platform_user_id/platform_conversation_id. "
            "'comment' for a public Instagram/Facebook page comment, where "
            "platform_conversation_id holds the Graph API comment_id and a "
            "reply must be posted to that comment via a different endpoint "
            "(POST /{comment_id}/comments), not the messages endpoint. Set "
            "by the channel adapter at ingestion and carried through "
            "RoutingDecision to OutboundMessage unchanged."
        ),
    )

    # ── Tracing (OpenTelemetry W3C Trace Context) ─────────────────────────────
    trace_id: str | None = Field(
        default=None,
        description="W3C trace-id propagated across service boundaries",
    )
    span_id: str | None = Field(
        default=None,
        description="W3C span-id of the adapter's publish span",
    )

    def to_kafka_bytes(self) -> bytes:
        """
        Serialize to UTF-8 JSON bytes for Kafka publish.

        Uses mode='json' so that:
          - uuid.UUID → str
          - datetime  → ISO 8601 string
          - Enum      → str value
        """
        return self.model_dump_json(
            by_alias=False,
        ).encode("utf-8")

    @classmethod
    def kafka_key(cls, tenant_id: uuid.UUID, platform_user_id: str) -> bytes:
        """
        Kafka message key — ensures all messages from one customer land on
        the same partition, preserving order for the AI worker.

        Key format: "<tenant_id>:<platform_user_id>"
        """
        return f"{tenant_id!s}:{platform_user_id}".encode("utf-8")

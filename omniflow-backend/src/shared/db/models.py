"""
shared/db/models.py — OmniFlow AI Core ORM Models

SQLAlchemy 2.0 style (Mapped + mapped_column) for all 9 core entities.
Every table except `Tenant` carries a tenant_id FK for PostgreSQL RLS.

Table inventory:
  1. Tenant             — SaaS subscriber (real estate agency)
  2. TenantUser         — B2B dashboard agent / admin
  3. Customer           — End customer (B2C, unified across channels)
  4. PropertyListing    — REGA-verified property ad
  5. Conversation       — Omni-channel thread (AI or human-active)
  6. Message            — Individual message inside a Conversation
  7. CustomerReport     — Paid PDF report (Deed-Check-29, etc.)
  8. SupportTicket      — Tenant support request
  9. BroadcastCampaign  — VIP/marketing campaign
 10. VipSubscriber      — Customer VIP subscription record (SRS §5 VIP)
 11. BroadcastDelivery  — Per-recipient delivery status for a campaign
 12. CompanyProfile     — Tenant's structured business knowledge (SRS §4.2)
 13. KnowledgeDocument  — Uploaded file + RAG ingestion state (SRS §4.2)

References: SRS §6 — Data Models
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.shared.core.enums import (
    AppointmentStatus,
    BroadcastCampaignStatus,
    Channel,
    KnowledgeDocumentStatus,
    NoteSeverity,
    ConversationStatus,
    CustomerVCardState,
    ListingStatus,
    MessageType,
    PropertyType,
    ReportType,
    SubscriptionStatus,
    SupportTicketStatus,
    TenantTier,
    TenantUserRole,
    OnboardingStatus,
)
from src.shared.db.base import Base, TimestampMixin, TenantScopedMixin


# ══════════════════════════════════════════════════════════════════════════════
# 1. Tenant
# ══════════════════════════════════════════════════════════════════════════════
class Tenant(Base, TimestampMixin):
    """
    Top-level SaaS subscriber — one per real estate agency.

    Does NOT inherit TenantScopedMixin (it IS the tenant).
    All other tables FK back to this table's PK.

    fal_license_number:
        Unique FAL license number issued by the Saudi Real Estate General
        Authority.  Verified on signup via the REGA Broker Verification API.
    """
    __tablename__ = "tenants"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="Primary key — RLS partition key referenced by all other tables",
    )
    business_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Legal business name of the agency",
    )
    fal_license_number: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        unique=True,
        index=True,
        comment="FAL license number — verified via REGA Broker Verification API",
    )
    subscription_tier: Mapped[TenantTier] = mapped_column(
        String(30),
        nullable=False,
        default=TenantTier.ECONOMIC,
        comment="Subscription plan: economic | professional | enterprise",
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        String(30),
        nullable=False,
        default=SubscriptionStatus.TRIAL,
        comment="Billing / access status",
    )
    onboarding_status: Mapped[OnboardingStatus] = mapped_column(
        String(30),
        nullable=False,
        default=OnboardingStatus.PENDING_SELECTION,
        comment="Tenant onboarding flow status",
    )
    meta_access_token: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Meta WhatsApp/Omnichannel API Access Token",
    )
    whatsapp_phone_number_id: Mapped[Optional[str]] = mapped_column(
        String(30),
        nullable=True,
        comment="Meta WhatsApp Business Phone Number ID",
    )
    whatsapp_waba_id: Mapped[Optional[str]] = mapped_column(
        String(30),
        nullable=True,
        comment="Meta WhatsApp Business Account ID",
    )
    whatsapp_display_phone_number: Mapped[Optional[str]] = mapped_column(
        String(30),
        nullable=True,
        comment="Tenant's own dialable WhatsApp number (E.164) — shown to "
        "customers via the VCard gate. Distinct from the opaque Meta "
        "whatsapp_phone_number_id, which cannot be dialed or saved as a contact.",
    )
    max_ai_conversations: Mapped[int] = mapped_column(
        default=500,
        nullable=False,
        comment="Monthly AI conversation quota per subscription tier",
    )
    ai_system_prompt: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Custom AI personality / system prompt override for this tenant",
    )
    logo_url: Mapped[Optional[str]] = mapped_column(
        String(1000),
        nullable=True,
        comment="Public URL of the tenant's company logo (stored in S3/MinIO)",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    users: Mapped[list[TenantUser]] = relationship(
        "TenantUser", back_populates="tenant", cascade="all, delete-orphan"
    )
    customers: Mapped[list[Customer]] = relationship(
        "Customer", back_populates="tenant", cascade="all, delete-orphan"
    )
    listings: Mapped[list[PropertyListing]] = relationship(
        "PropertyListing", back_populates="tenant", cascade="all, delete-orphan"
    )
    conversations: Mapped[list[Conversation]] = relationship(
        "Conversation", back_populates="tenant", cascade="all, delete-orphan"
    )
    reports: Mapped[list[CustomerReport]] = relationship(
        "CustomerReport", back_populates="tenant", cascade="all, delete-orphan"
    )
    vip_subscribers: Mapped[list["VipSubscriber"]] = relationship(
        "VipSubscriber", back_populates="tenant", cascade="all, delete-orphan"
    )
    company_profile: Mapped[Optional["CompanyProfile"]] = relationship(
        "CompanyProfile",
        back_populates="tenant",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Tenant id={self.tenant_id} name={self.business_name!r}>"


# ══════════════════════════════════════════════════════════════════════════════
# 2. TenantUser  (B2B Dashboard Agent / Admin)
# ══════════════════════════════════════════════════════════════════════════════
class TenantUser(Base, TimestampMixin, TenantScopedMixin):
    """
    A human operator of the OmniFlow B2B dashboard.

    Roles:
      - ADMIN   — Full tenant management + all agent capabilities.
      - AGENT   — Handle human-takeover conversations; view customer data.
      - AUDITOR — Read-only access to conversations and reports.
    """
    __tablename__ = "tenant_users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_tenant_users_tenant_email"),
        Index("ix_tenant_users_tenant_role", "tenant_id", "role"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    clerk_id: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        unique=True,
        index=True,
        comment="Clerk User ID (user_2abcdef...)",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="RLS partition key — must match app.current_tenant_id",
    )
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(
        String(254),
        nullable=False,
        comment="Login email — unique within tenant scope",
    )
    phone_number: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="User phone number",
    )
    hashed_password: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Argon2id / bcrypt hash — NEVER store plaintext",
    )
    role: Mapped[TenantUserRole] = mapped_column(
        String(20),
        nullable=False,
        default=TenantUserRole.AGENT,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="users")
    assigned_conversations: Mapped[list[Conversation]] = relationship(
        "Conversation",
        back_populates="assigned_agent",
        foreign_keys="Conversation.assigned_agent_id",
    )

    def __repr__(self) -> str:
        return f"<TenantUser id={self.user_id} email={self.email!r} role={self.role}>"


# ══════════════════════════════════════════════════════════════════════════════
# 3. Customer  (Unified B2C Identity)
# ══════════════════════════════════════════════════════════════════════════════
class Customer(Base, TimestampMixin, TenantScopedMixin):
    """
    A single end-customer record, unified across all channels.

    Identity Resolution:
      The unified_phone (E.164) is the golden identifier.
      One customer may have conversations on WhatsApp, TikTok, etc. — all
      linked back to this single record.

    PDPL Compliance:
      is_processing_restricted = True means the customer has exercised their
      right to restrict data processing under Saudi PDPL. Any AI worker that
      reads this flag MUST bypass LLM processing and route immediately to
      a human agent.
    """
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "unified_phone",
            name="uq_customers_tenant_phone",
        ),
        Index("ix_customers_vcard_state", "tenant_id", "vcard_state"),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="RLS partition key — must match app.current_tenant_id",
    )
    unified_phone: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="E.164 phone number — golden identifier across all channels",
    )
    display_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    whatsapp_profile_name: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True
    )

    # PDPL / Privacy
    is_processing_restricted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
        comment="PDPL opt-out — if True, skip AI and route to human immediately",
    )
    is_vip: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )

    # VCard state machine
    vcard_state: Mapped[CustomerVCardState] = mapped_column(
        String(40),
        nullable=False,
        default=CustomerVCardState.NEW,
        comment="State in the VCard follow-up drip sequence",
    )
    vcard_opened_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Set the first time the vcard message's delivery_status reaches READ (lead scoring signal)",
    )

    # AI Lead Scoring
    engagement_score: Mapped[Optional[int]] = mapped_column(
        nullable=True,
        default=0,
        index=True,
        comment="AI-computed engagement score 0–100. Higher = hotter lead.",
    )

    # Vault
    vault_s3_prefix: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="S3 prefix for this customer's private document vault",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="customers")
    conversations: Mapped[list[Conversation]] = relationship(
        "Conversation", back_populates="customer", cascade="all, delete-orphan"
    )
    reports: Mapped[list[CustomerReport]] = relationship(
        "CustomerReport", back_populates="customer", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Customer id={self.customer_id} phone={self.unified_phone!r}>"


# ══════════════════════════════════════════════════════════════════════════════
# 4. PropertyListing
# ══════════════════════════════════════════════════════════════════════════════
class PropertyListing(Base, TimestampMixin, TenantScopedMixin):
    """
    A single real estate listing owned by a Tenant.

    rega_ad_number:
        Mandatory REGA advertisement number (رقم الإعلان).
        Validated via REGA Property Verification API before is_verified=True.

    The Qdrant vector index for RAG is keyed by `listing_id` (str form),
    so the UUID here must be serializable to string cheaply.
    """
    __tablename__ = "property_listings"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "rega_ad_number",
            name="uq_listings_tenant_rega",
        ),
        Index("ix_listings_tenant_status", "tenant_id", "status"),
        Index("ix_listings_tenant_type", "tenant_id", "property_type"),
    )

    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="RLS partition key — must match app.current_tenant_id",
    )
    rega_ad_number: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        comment="REGA advertisement number (رقم الإعلان) — unique per tenant",
    )
    property_type: Mapped[PropertyType] = mapped_column(
        String(30), nullable=False
    )
    status: Mapped[ListingStatus] = mapped_column(
        String(30),
        nullable=False,
        default=ListingStatus.PENDING_VERIFICATION,
    )
    is_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
        comment="True only after REGA API confirms the ad number",
    )

    # Location
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    district: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    latitude: Mapped[Optional[float]] = mapped_column(nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(nullable=True)

    # Pricing
    price: Mapped[Optional[float]] = mapped_column(
        Numeric(14, 2), nullable=True, comment="Price in SAR"
    )
    area_sqm: Mapped[Optional[float]] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    bedrooms: Mapped[Optional[int]] = mapped_column(nullable=True)
    bathrooms: Mapped[Optional[int]] = mapped_column(nullable=True)

    # RAG content
    description_ar: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Arabic description — embedded in Qdrant"
    )
    description_en: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="English description — embedded in Qdrant"
    )
    qdrant_point_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        nullable=True,
        unique=True,
        comment="UUID string referencing the Qdrant vector point",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="listings")

    def __repr__(self) -> str:
        return (
            f"<PropertyListing id={self.listing_id} "
            f"rega={self.rega_ad_number!r} type={self.property_type}>"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 5. Conversation
# ══════════════════════════════════════════════════════════════════════════════
class Conversation(Base, TimestampMixin, TenantScopedMixin):
    """
    An omni-channel conversation thread between a Customer and the system.

    Status state machine:
        AI_ACTIVE  →  HUMAN_ACTIVE  →  CLOSED
                   ↘  ESCALATED     →  HUMAN_ACTIVE
                                    →  CLOSED
        Any state  →  DORMANT  (no activity for N days)

    channel:
        The originating channel (whatsapp, tiktok, etc.). A customer may
        have multiple concurrent conversations on different channels, each
        tracked as a separate Conversation row.

    assigned_agent_id:
        NULL when AI_ACTIVE. Set to a TenantUser.user_id when a human
        takes over via the B2B dashboard.
    """
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_tenant_status", "tenant_id", "status"),
        Index("ix_conversations_customer", "customer_id"),
        Index("ix_conversations_agent", "assigned_agent_id"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="RLS partition key — must match app.current_tenant_id",
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.customer_id", ondelete="CASCADE"),
        nullable=False,
    )
    assigned_agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant_users.user_id", ondelete="SET NULL"),
        nullable=True,
        comment="NULL = AI active; set when human takes over",
    )
    channel: Mapped[Channel] = mapped_column(
        String(20), nullable=False, comment="Originating channel"
    )
    platform_conversation_id: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
        index=True,
        comment="Platform-native thread/conversation ID for deduplication",
    )
    status: Mapped[ConversationStatus] = mapped_column(
        String(20),
        nullable=False,
        default=ConversationStatus.AI_ACTIVE,
        index=True,
    )
    last_message_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True, index=True
    )
    message_count: Mapped[int] = mapped_column(
        default=0, nullable=False, comment="Cached message count for pagination"
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="conversations")
    customer: Mapped[Customer] = relationship(
        "Customer", back_populates="conversations"
    )
    assigned_agent: Mapped[Optional[TenantUser]] = relationship(
        "TenantUser",
        back_populates="assigned_conversations",
        foreign_keys=[assigned_agent_id],
    )
    messages: Mapped[list[Message]] = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<Conversation id={self.conversation_id} "
            f"channel={self.channel} status={self.status}>"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 6. Message
# ══════════════════════════════════════════════════════════════════════════════
class Message(Base, TimestampMixin):
    """
    A single message inside a Conversation.

    Note: Message does NOT carry tenant_id directly. RLS is enforced via
    the parent Conversation row. Queries always JOIN through conversation_id.

    sender_type:
        - 'customer'     — inbound message from the end customer
        - 'ai_bot'       — outbound generated by the LLM pipeline
        - 'human_agent'  — outbound typed by a TenantUser

    agent_id:
        NULL unless sender_type = 'human_agent'. Points to TenantUser.user_id.

    s3_media_url:
        Pre-signed or permanent S3 URL for audio / image / video / document
        payloads. For voice messages this is the original audio file.
        The transcription (if any) is stored in text_content.
    """
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )

    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sender_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="'customer' | 'ai_bot' | 'human_agent'",
    )
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant_users.user_id", ondelete="SET NULL"),
        nullable=True,
        comment="Set only when sender_type = 'human_agent'",
    )
    message_type: Mapped[MessageType] = mapped_column(
        String(20), nullable=False, default=MessageType.TEXT
    )
    text_content: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Plaintext body or Whisper transcription of audio",
    )
    s3_media_url: Mapped[Optional[str]] = mapped_column(
        String(1000),
        nullable=True,
        comment="S3 URL for audio/image/video/document payloads",
    )
    platform_message_id: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
        unique=True,
        comment="Platform-native message ID — used for deduplication",
    )
    llm_routing_tier: Mapped[Optional[str]] = mapped_column(
        String(10),
        nullable=True,
        comment="Routing tier that generated this response: L0–L3 | VAULT | HUMAN",
    )
    tokens_used: Mapped[Optional[int]] = mapped_column(
        nullable=True, comment="LLM token cost for this message"
    )
    latency_ms: Mapped[Optional[int]] = mapped_column(
        nullable=True, comment="End-to-end generation latency in milliseconds"
    )
    delivery_status: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        default="PENDING",
        comment="'PENDING' | 'SENT' | 'DELIVERED' | 'READ' | 'FAILED'",
    )
    failure_reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Meta status-webhook error, set only when delivery_status='FAILED'",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    conversation: Mapped[Conversation] = relationship(
        "Conversation", back_populates="messages"
    )
    agent: Mapped[Optional[TenantUser]] = relationship(
        "TenantUser",
        foreign_keys=[agent_id],
    )

    def __repr__(self) -> str:
        return (
            f"<Message id={self.message_id} "
            f"sender={self.sender_type} type={self.message_type}>"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 7. CustomerReport
# ══════════════════════════════════════════════════════════════════════════════
class CustomerReport(Base, TimestampMixin, TenantScopedMixin):
    """
    A paid PDF report generated for a Customer.

    Report types (from SRS §5):
      - deed_check_29       — Deed Check Report (SAR 29)
      - municipal_consulting_15 — Municipal Consulting Report (SAR 15)
      - premium_consultation — Premium AI consultation bundle

    s3_url:
        Permanent (non-expiring) S3 URL. The API layer generates short-lived
        pre-signed URLs for download to avoid direct S3 exposure.

    payment_reference:
        Transaction ID from Moyasar / Tap gateway.
    """
    __tablename__ = "customer_reports"
    __table_args__ = (
        Index("ix_reports_tenant_customer", "tenant_id", "customer_id"),
        Index("ix_reports_tenant_type", "tenant_id", "report_type"),
    )

    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="RLS partition key — must match app.current_tenant_id",
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.customer_id", ondelete="CASCADE"),
        nullable=False,
    )
    report_type: Mapped[ReportType] = mapped_column(
        String(40), nullable=False
    )
    s3_url: Mapped[Optional[str]] = mapped_column(
        String(1000),
        nullable=True,
        comment="Permanent S3 object URL — serve via pre-signed URL in API",
    )
    price_sar: Mapped[Optional[float]] = mapped_column(
        Numeric(8, 2), nullable=True, comment="Amount charged in SAR"
    )
    payment_reference: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
        comment="Gateway transaction ID (Moyasar / Tap)",
    )
    is_delivered: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="True after the report PDF was sent to the customer",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="reports")
    customer: Mapped[Customer] = relationship(
        "Customer", back_populates="reports"
    )

    def __repr__(self) -> str:
        return (
            f"<CustomerReport id={self.report_id} "
            f"type={self.report_type} delivered={self.is_delivered}>"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 8. SupportTicket
# ══════════════════════════════════════════════════════════════════════════════
class SupportTicket(Base, TimestampMixin, TenantScopedMixin):
    """
    A support request raised by a tenant user from the B2B dashboard.

    Tenant-scoped: covered by the same RLS policy family as the other
    tenant tables (see migration 0007).
    """
    __tablename__ = "support_tickets"
    __table_args__ = (
        Index("ix_support_tickets_tenant_status", "tenant_id", "status"),
        Index("ix_support_tickets_created_by", "created_by_user_id"),
    )

    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="RLS partition key — must match app.current_tenant_id",
    )
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant_users.user_id", ondelete="SET NULL"),
        nullable=True,
        comment="Author of the ticket; NULL if the user was later removed",
    )
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[SupportTicketStatus] = mapped_column(
        String(20),
        nullable=False,
        default=SupportTicketStatus.OPEN,
        comment="open | in_progress | closed",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    tenant: Mapped[Tenant] = relationship("Tenant")
    created_by: Mapped[Optional[TenantUser]] = relationship(
        "TenantUser", foreign_keys=[created_by_user_id]
    )

    def __repr__(self) -> str:
        return f"<SupportTicket id={self.ticket_id} status={self.status}>"


# ══════════════════════════════════════════════════════════════════════════════
# 9. BroadcastCampaign
# ══════════════════════════════════════════════════════════════════════════════
class BroadcastCampaign(Base, TimestampMixin, TenantScopedMixin):
    """
    A VIP / marketing broadcast campaign.

    Status machine:
        draft → scheduled → sending → completed
        draft/scheduled → cancelled
        sending → failed
    """
    __tablename__ = "broadcast_campaigns"
    __table_args__ = (
        Index("ix_broadcast_campaigns_tenant_status", "tenant_id", "status"),
        Index("ix_broadcast_campaigns_scheduled_at", "scheduled_at"),
    )

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="RLS partition key — must match app.current_tenant_id",
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message_template: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Body sent to each recipient; may contain {{placeholders}}",
    )
    target_audience: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
        comment='Audience selector, e.g. {"list_type": "daily_rentals"}',
    )
    campaign_type: Mapped[Optional[str]] = mapped_column(
        String(40),
        nullable=True,
        comment="Free-form campaign classification used by the broadcast worker",
    )
    meta_template_id: Mapped[Optional[str]] = mapped_column(
        String(120),
        nullable=True,
        comment="Approved Meta WhatsApp template ID used for the send",
    )
    status: Mapped[BroadcastCampaignStatus] = mapped_column(
        String(20),
        nullable=False,
        default=BroadcastCampaignStatus.DRAFT,
        comment="draft | scheduled | sending | completed | cancelled | failed",
    )
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the campaign should start sending (UTC)",
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Set by the broadcast worker when the send finishes",
    )
    recipients_count: Mapped[Optional[int]] = mapped_column(
        nullable=True,
        comment=(
            "Estimated audience size computed at creation time from "
            "target_audience (see gateway/routers/broadcasts.py::_count_audience). "
            "Recomputed by the broadcast worker at send time."
        ),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    tenant: Mapped[Tenant] = relationship("Tenant")
    deliveries: Mapped[list["BroadcastDelivery"]] = relationship(
        "BroadcastDelivery", back_populates="campaign", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<BroadcastCampaign id={self.campaign_id} "
            f"title={self.title!r} status={self.status}>"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 10. VipSubscriber
# ══════════════════════════════════════════════════════════════════════════════
class VipSubscriber(Base, TimestampMixin, TenantScopedMixin):
    """
    A customer who has opted into the VIP broadcast list.

    Status machine:
        pending → active         (after first message received)
        active  → opted_out      (customer sends opt-out keyword)
        active  → declined       (manually removed by agent)
        opted_out → active       (customer re-subscribes after cooldown)

    References: SRS §5 VIP Subscriber Management
    """
    __tablename__ = "vip_subscribers"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "customer_id",
            name="uq_vip_subscribers_tenant_customer",
        ),
        Index("ix_vip_subscribers_tenant_status", "tenant_id", "status"),
        Index("ix_vip_subscribers_customer", "customer_id"),
    )

    subscriber_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="RLS partition key — must match app.current_tenant_id",
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.customer_id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
        comment="active | opted_out | declined | pending",
    )
    opted_in_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when the customer confirmed VIP opt-in",
    )
    opted_out_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when the customer opted out",
    )
    messages_sent_this_week: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
        comment="Rolling count — reset by the weekly job; enforces send-rate SLA",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="vip_subscribers")
    customer: Mapped[Customer] = relationship("Customer")

    def __repr__(self) -> str:
        return (
            f"<VipSubscriber id={self.subscriber_id} "
            f"customer={self.customer_id} status={self.status}>"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 11. BroadcastDelivery
# ══════════════════════════════════════════════════════════════════════════════
class BroadcastDelivery(Base, TimestampMixin, TenantScopedMixin):
    """
    Per-recipient delivery record for a BroadcastCampaign.

    The broadcast worker creates one row per (campaign, customer) pair
    and updates it as the message moves through the delivery pipeline.

    delivery_status:
        PENDING  — queued, not yet sent
        SENT     — dispatched to Meta API
        DELIVERED — Meta confirmed delivery
        READ     — read receipt received
        FAILED   — send attempt failed (see error_detail)

    References: SRS §5 VIP Broadcast
    """
    __tablename__ = "broadcast_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "campaign_id", "customer_id",
            name="uq_broadcast_deliveries_campaign_customer",
        ),
        Index("ix_broadcast_deliveries_tenant_status", "tenant_id", "delivery_status"),
        Index("ix_broadcast_deliveries_campaign", "campaign_id"),
        Index("ix_broadcast_deliveries_customer", "customer_id"),
    )

    delivery_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="RLS partition key — must match app.current_tenant_id",
    )
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("broadcast_campaigns.campaign_id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.customer_id", ondelete="CASCADE"),
        nullable=False,
    )
    delivery_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="PENDING",
        comment="PENDING | SENT | DELIVERED | READ | FAILED",
    )
    platform_message_id: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
        comment="Platform-native message ID returned by Meta API",
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the message was dispatched to the channel API",
    )
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When Meta confirmed delivery",
    )
    error_detail: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Error message if delivery_status = FAILED",
    )
    retry_count: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
        comment="Number of send attempts made",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    tenant: Mapped[Tenant] = relationship("Tenant")
    campaign: Mapped[BroadcastCampaign] = relationship(
        "BroadcastCampaign", back_populates="deliveries"
    )
    customer: Mapped[Customer] = relationship("Customer")

    def __repr__(self) -> str:
        return (
            f"<BroadcastDelivery id={self.delivery_id} "
            f"campaign={self.campaign_id} status={self.delivery_status}>"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 12. CompanyProfile  (Knowledge Base — structured fields)
# ══════════════════════════════════════════════════════════════════════════════
class CompanyProfile(Base, TenantScopedMixin):
    """
    The tenant's own business knowledge, entered once in the dashboard and
    injected into EVERY AI system prompt (SRS §4.2 — Knowledge Base).

    Relationship to `tenants` is strictly 1:1 — `tenant_id` is both PK and FK,
    with ON DELETE CASCADE so removing a tenant removes its profile.

    This is the *static* half of the knowledge base. The *dynamic* half lives in
    `knowledge_documents` + Qdrant and is retrieved per-question by the RAG
    pipeline. Keep this table small: everything here is paid for on every single
    LLM call, so long-form material belongs in an uploaded document instead.

    No `created_at` — the row is created lazily on first PATCH /api/v1/knowledge/profile
    and only `updated_at` is meaningful.
    """
    __tablename__ = "company_profiles"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        primary_key=True,
        comment="PK + FK — RLS partition key; one profile per tenant",
    )

    business_description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Free-text description of the agency / office",
    )
    services_offered: Mapped[Optional[list]] = mapped_column(
        JSONB,
        nullable=True,
        comment='Array of service labels, e.g. ["بيع", "إيجار", "إدارة أملاك"]',
    )
    target_areas: Mapped[Optional[list]] = mapped_column(
        JSONB,
        nullable=True,
        comment='Array of cities/districts served, e.g. ["الرياض - النرجس"]',
    )
    pricing_policy: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Commission / pricing policy in the tenant's own words",
    )
    working_hours: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Free text ('الأحد–الخميس 9ص–6م'). Deliberately unstructured.",
    )
    contact_phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    contact_address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    social_links: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
        comment='Object, e.g. {"instagram": "...", "x": "...", "website": "..."}',
    )
    unique_selling_points: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="What differentiates this agency from competitors",
    )
    policies_text: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="General policies: cancellation, guarantees, refunds…",
    )
    faq: Mapped[Optional[list]] = mapped_column(
        JSONB,
        nullable=True,
        comment='Array of {"question": str, "answer": str}',
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="company_profile")

    def __repr__(self) -> str:
        return f"<CompanyProfile tenant={self.tenant_id}>"


# ══════════════════════════════════════════════════════════════════════════════
# 13. KnowledgeDocument  (Knowledge Base — uploaded files)
# ══════════════════════════════════════════════════════════════════════════════
class KnowledgeDocument(Base, TenantScopedMixin):
    """
    A file uploaded by the tenant (catalogue, FAQ sheet, price list…) that is
    parsed, chunked, embedded and stored in the tenant's Qdrant documents
    collection so the AI can cite it when answering customers.

    The row is the source of truth for ingestion state; the vectors themselves
    live in Qdrant keyed by `document_id` in their payload, which is what makes
    DELETE able to purge them.
    """
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        Index("ix_knowledge_documents_tenant_status", "tenant_id", "status"),
        Index("ix_knowledge_documents_created_at", "created_at"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="RLS partition key — must match app.current_tenant_id",
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Human-friendly title; defaults to the original filename",
    )
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        comment="pdf | docx | txt | csv",
    )
    s3_key: Mapped[str] = mapped_column(
        String(1000),
        nullable=False,
        comment="Object key inside the knowledge bucket",
    )
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[KnowledgeDocumentStatus] = mapped_column(
        String(20),
        nullable=False,
        default=KnowledgeDocumentStatus.UPLOADED,
        comment="uploaded | processing | indexed | failed",
    )
    chunk_count: Mapped[Optional[int]] = mapped_column(
        nullable=True,
        comment="Number of vectors written to Qdrant; NULL until indexed",
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Populated when status = failed",
    )
    uploaded_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant_users.user_id", ondelete="SET NULL"),
        nullable=True,
        comment="Uploader; NULL if the user was later removed",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    indexed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When ingestion finished successfully",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    tenant: Mapped[Tenant] = relationship("Tenant")

    def __repr__(self) -> str:
        return (
            f"<KnowledgeDocument id={self.document_id} "
            f"title={self.title!r} status={self.status}>"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 15. ConversationNote — agent-authored internal note (inbox quick action)
# ══════════════════════════════════════════════════════════════════════════════
class ConversationNote(Base, TimestampMixin, TenantScopedMixin):
    """
    An internal note an agent attaches to a conversation — never shown to the
    customer. The "warning" severity is meant for the "إضافة ملاحظة تحذير"
    quick-action button (e.g. flagging a difficult or high-risk customer).
    """
    __tablename__ = "conversation_notes"
    __table_args__ = (
        Index("ix_conversation_notes_conversation", "conversation_id", "created_at"),
    )

    note_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="RLS partition key — must match app.current_tenant_id",
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
    )
    author_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant_users.user_id", ondelete="SET NULL"),
        nullable=True,
        comment="NULL if the authoring agent was later removed",
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[NoteSeverity] = mapped_column(
        String(20), nullable=False, default=NoteSeverity.INFO,
    )

    def __repr__(self) -> str:
        return f"<ConversationNote id={self.note_id} severity={self.severity}>"


# ══════════════════════════════════════════════════════════════════════════════
# 16. Appointment — scheduled viewing (inbox quick action)
# ══════════════════════════════════════════════════════════════════════════════
class Appointment(Base, TimestampMixin, TenantScopedMixin):
    """
    A scheduled property-viewing appointment tied to a conversation.

    Deliberately minimal: a structured date/time + location note. Does NOT
    implement the full SRS §5 appointment subsystem (distance-based dispatch
    routing, CalDAV/Google Calendar/Outlook sync, conflict detection,
    automated 24h/1h reminders) — that's a substantial separate feature.
    See IMPLEMENTATION_STATUS.md for the scope-cut rationale.
    """
    __tablename__ = "appointments"
    __table_args__ = (
        Index("ix_appointments_conversation", "conversation_id", "scheduled_at"),
        Index("ix_appointments_tenant_status", "tenant_id", "status"),
    )

    appointment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="RLS partition key — must match app.current_tenant_id",
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.customer_id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant_users.user_id", ondelete="SET NULL"),
        nullable=True,
    )
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    location_note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    status: Mapped[AppointmentStatus] = mapped_column(
        String(20), nullable=False, default=AppointmentStatus.SCHEDULED,
    )

    def __repr__(self) -> str:
        return f"<Appointment id={self.appointment_id} status={self.status} at={self.scheduled_at}>"

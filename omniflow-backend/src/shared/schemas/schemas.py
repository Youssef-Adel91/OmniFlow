"""
shared/schemas/schemas.py — Pydantic v2 Request / Response Schemas

Provides Create, Update, and Response schemas for the three primary
API-facing entities: Tenant, Customer, and Message.

Design rules:
  - All Response schemas use ConfigDict(from_attributes=True) for ORM→Pydantic
    serialization via model.model_validate(orm_obj).
  - UUIDs are serialized as strings in JSON responses automatically by Pydantic.
  - Optional fields in Update schemas default to None so a PATCH can send only
    the fields it wants to change (partial update pattern).
  - Password fields are WRITE-ONLY — never appear in Response schemas.
  - Timestamps (created_at, updated_at) are read-only — present only in Response.

References: SRS §6 — API Contract, FastAPI endpoint layer.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from src.shared.core.enums import (
    Channel,
    ConversationStatus,
    CustomerVCardState,
    MessageType,
    ReportType,
    SubscriptionStatus,
    TenantTier,
    TenantUserRole,
)


# ══════════════════════════════════════════════════════════════════════════════
# Shared base helpers
# ══════════════════════════════════════════════════════════════════════════════

class _OrmBase(BaseModel):
    """Base for all Response schemas — enables ORM mode."""
    model_config = ConfigDict(from_attributes=True)


class _TimestampMixin(BaseModel):
    """Read-only audit timestamps injected by the DB server."""
    created_at: datetime
    updated_at: datetime


# ══════════════════════════════════════════════════════════════════════════════
# TENANT schemas
# ══════════════════════════════════════════════════════════════════════════════

class TenantCreate(BaseModel):
    """
    Payload to onboard a new tenant (real estate agency).
    Used by the platform admin endpoint: POST /admin/tenants
    """
    business_name: str = Field(
        ..., min_length=2, max_length=255, examples=["نخيل العقارية"]
    )
    fal_license_number: str = Field(
        ...,
        min_length=5,
        max_length=50,
        examples=["F-123456"],
        description="FAL license — verified via REGA Broker API before activation",
    )
    subscription_tier: TenantTier = Field(default=TenantTier.ECONOMIC)
    whatsapp_phone_number_id: Optional[str] = Field(
        default=None, max_length=30
    )
    whatsapp_waba_id: Optional[str] = Field(default=None, max_length=30)
    max_ai_conversations: int = Field(
        default=500, ge=0, le=100_000
    )

    @field_validator("fal_license_number")
    @classmethod
    def fal_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("fal_license_number cannot be blank")
        return v.strip().upper()


class TenantUpdate(BaseModel):
    """
    Partial update payload for an existing tenant.
    All fields are optional — only provided fields are changed (PATCH semantics).
    """
    business_name: Optional[str] = Field(default=None, min_length=2, max_length=255)
    subscription_tier: Optional[TenantTier] = None
    status: Optional[SubscriptionStatus] = None
    whatsapp_phone_number_id: Optional[str] = Field(default=None, max_length=30)
    whatsapp_waba_id: Optional[str] = Field(default=None, max_length=30)
    max_ai_conversations: Optional[int] = Field(default=None, ge=0, le=100_000)


class TenantOnboardingUpdate(BaseModel):
    """
    Payload for the Dual-Option Onboarding Flow.
    option: 'self_service' | 'white_glove'

    business_name/business_category/about_text/products (all optional): the
    "tell us about your business" step that was missing from onboarding
    entirely before this — see IMPLEMENTATION_STATUS.md. When present, these
    feed Tenant.business_name + CompanyProfile (the same knowledge block
    every AI reply is built from), not just a DB field nobody reads.
    """
    option: str = Field(..., pattern="^(self_service|white_glove)$")
    business_name: Optional[str] = Field(default=None, min_length=2, max_length=255)
    business_category: Optional[str] = Field(default=None, max_length=255)
    about_text: Optional[str] = Field(default=None, max_length=2000)
    products: Optional[list[str]] = Field(default=None, max_length=50)
    detailed_instructions: Optional[str] = Field(
        default=None, max_length=4000,
        description="Free-form policies/how-to-answer instructions for the AI (-> CompanyProfile.policies_text).",
    )
    meta_access_token: Optional[str] = Field(default=None)
    whatsapp_phone_number_id: Optional[str] = Field(default=None, max_length=30)
    whatsapp_waba_id: Optional[str] = Field(default=None, max_length=30)


class TenantResponse(_OrmBase, _TimestampMixin):
    """
    Full tenant representation returned by the API.
    Passwords, internal secrets, and raw DB FKs are excluded.
    """
    tenant_id: uuid.UUID
    business_name: str
    fal_license_number: str
    subscription_tier: TenantTier
    status: SubscriptionStatus
    whatsapp_phone_number_id: Optional[str]
    whatsapp_waba_id: Optional[str]
    max_ai_conversations: int


class TenantSummary(_OrmBase):
    """Lightweight tenant representation for list endpoints."""
    tenant_id: uuid.UUID
    business_name: str
    subscription_tier: TenantTier
    status: SubscriptionStatus


# ══════════════════════════════════════════════════════════════════════════════
# CUSTOMER schemas
# ══════════════════════════════════════════════════════════════════════════════

class CustomerCreate(BaseModel):
    """
    Create a new unified customer record.
    Typically called by the identity-resolution worker after first contact,
    not directly by end-users.
    """
    tenant_id: uuid.UUID
    unified_phone: str = Field(
        ...,
        pattern=r"^\+[1-9]\d{6,14}$",
        examples=["+966501234567"],
        description="E.164 phone number — golden identifier across all channels",
    )
    display_name: Optional[str] = Field(default=None, max_length=200)
    whatsapp_profile_name: Optional[str] = Field(default=None, max_length=200)
    is_processing_restricted: bool = Field(
        default=False,
        description="PDPL opt-out flag — set True to bypass AI and route to human",
    )
    is_vip: bool = Field(default=False)
    vcard_state: CustomerVCardState = Field(default=CustomerVCardState.NEW)


class CustomerUpdate(BaseModel):
    """
    Partial update for a customer — supports PATCH.
    Agents may update display_name, VIP status, or PDPL restriction flag.
    """
    display_name: Optional[str] = Field(default=None, max_length=200)
    whatsapp_profile_name: Optional[str] = Field(default=None, max_length=200)
    is_processing_restricted: Optional[bool] = None
    is_vip: Optional[bool] = None
    vcard_state: Optional[CustomerVCardState] = None
    vault_s3_prefix: Optional[str] = Field(default=None, max_length=500)


class CustomerResponse(_OrmBase, _TimestampMixin):
    """Full customer record returned by the API."""
    customer_id: uuid.UUID
    tenant_id: uuid.UUID
    unified_phone: str
    display_name: Optional[str]
    whatsapp_profile_name: Optional[str]
    is_processing_restricted: bool
    is_vip: bool
    vcard_state: CustomerVCardState
    vault_s3_prefix: Optional[str]


class CustomerSummary(_OrmBase):
    """Lightweight customer representation for conversation list views."""
    customer_id: uuid.UUID
    unified_phone: str
    display_name: Optional[str]
    is_vip: bool
    is_processing_restricted: bool


# ══════════════════════════════════════════════════════════════════════════════
# MESSAGE schemas
# ══════════════════════════════════════════════════════════════════════════════

class MessageCreate(BaseModel):
    """
    Create a new message inside a conversation.
    Used by the AI worker and the human-agent websocket handler.

    sender_type must be one of: 'customer' | 'ai_bot' | 'human_agent'
    """
    conversation_id: uuid.UUID
    sender_type: str = Field(
        ...,
        pattern=r"^(customer|ai_bot|human_agent)$",
        description="Message origin: 'customer' | 'ai_bot' | 'human_agent'",
    )
    agent_id: Optional[uuid.UUID] = Field(
        default=None,
        description="Required when sender_type = 'human_agent'",
    )
    message_type: MessageType = Field(default=MessageType.TEXT)
    text_content: Optional[str] = Field(
        default=None, description="Plaintext body or Whisper transcription"
    )
    s3_media_url: Optional[str] = Field(
        default=None, max_length=1000
    )
    platform_message_id: Optional[str] = Field(
        default=None, max_length=200,
        description="Platform-native ID for deduplication",
    )
    llm_routing_tier: Optional[str] = Field(
        default=None, max_length=10,
        description="LLM routing tier: L0 | L1 | L2 | L3 | VAULT | HUMAN",
    )
    tokens_used: Optional[int] = Field(default=None, ge=0)
    latency_ms: Optional[int] = Field(default=None, ge=0)

    @field_validator("agent_id")
    @classmethod
    def agent_required_for_human(
        cls, v: Optional[uuid.UUID], info: object
    ) -> Optional[uuid.UUID]:
        # Pydantic v2: access sibling fields via info.data
        data = getattr(info, "data", {})
        if data.get("sender_type") == "human_agent" and v is None:
            raise ValueError("agent_id is required when sender_type = 'human_agent'")
        return v


class MessageUpdate(BaseModel):
    """
    Partial update for a message.
    Primary use-case: backfill latency_ms / tokens_used after async LLM call.
    """
    text_content: Optional[str] = None
    s3_media_url: Optional[str] = Field(default=None, max_length=1000)
    llm_routing_tier: Optional[str] = Field(default=None, max_length=10)
    tokens_used: Optional[int] = Field(default=None, ge=0)
    latency_ms: Optional[int] = Field(default=None, ge=0)


class MessageResponse(_OrmBase, _TimestampMixin):
    """Full message representation returned by the API."""
    message_id: uuid.UUID
    conversation_id: uuid.UUID
    sender_type: str
    agent_id: Optional[uuid.UUID]
    message_type: MessageType
    text_content: Optional[str]
    s3_media_url: Optional[str]
    platform_message_id: Optional[str]
    llm_routing_tier: Optional[str]
    tokens_used: Optional[int]
    latency_ms: Optional[int]


# ══════════════════════════════════════════════════════════════════════════════
# TENANT USER schemas  (for completeness — used by auth endpoints)
# ══════════════════════════════════════════════════════════════════════════════

class TenantUserCreate(BaseModel):
    """Create a new B2B dashboard user. Password is hashed in the service layer."""
    tenant_id: uuid.UUID
    full_name: str = Field(..., min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(..., min_length=8, description="Plaintext — hashed before storage")
    role: TenantUserRole = Field(default=TenantUserRole.AGENT)


class TenantUserUpdate(BaseModel):
    """Partial update for a tenant user."""
    full_name: Optional[str] = Field(default=None, min_length=2, max_length=200)
    role: Optional[TenantUserRole] = None
    is_active: Optional[bool] = None


class TenantUserResponse(_OrmBase, _TimestampMixin):
    """User record — password / hash are NEVER returned."""
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    full_name: str
    email: str
    role: TenantUserRole
    is_active: bool
    last_login_at: Optional[datetime]


# ══════════════════════════════════════════════════════════════════════════════
# CONVERSATION schemas  (lightweight — full schema lives in conversation service)
# ══════════════════════════════════════════════════════════════════════════════

class ConversationResponse(_OrmBase, _TimestampMixin):
    """Conversation summary returned by list / detail endpoints."""
    conversation_id: uuid.UUID
    tenant_id: uuid.UUID
    customer_id: uuid.UUID
    assigned_agent_id: Optional[uuid.UUID]
    channel: Channel
    platform_conversation_id: Optional[str]
    status: ConversationStatus
    last_message_at: Optional[datetime]
    message_count: int
    customer: Optional[CustomerSummary] = None


# ══════════════════════════════════════════════════════════════════════════════
# CUSTOMER REPORT schemas
# ══════════════════════════════════════════════════════════════════════════════

class CustomerReportResponse(_OrmBase, _TimestampMixin):
    """Report record returned after purchase / generation."""
    report_id: uuid.UUID
    tenant_id: uuid.UUID
    customer_id: uuid.UUID
    report_type: ReportType
    s3_url: Optional[str]
    price_sar: Optional[float]
    payment_reference: Optional[str]
    is_delivered: bool


# ══════════════════════════════════════════════════════════════════════════════
# PROPERTY LISTING schemas
# ══════════════════════════════════════════════════════════════════════════════

from src.shared.core.enums import ListingStatus, PropertyType  # noqa: E402


class PropertyListingCreate(BaseModel):
    """
    Create a new property listing.

    `rega_ad_number` is OPTIONAL — if omitted, the router auto-generates a
    DEV-REGA-{hex8} placeholder so the DB unique constraint is never violated
    during development or for off-market properties.
    """
    rega_ad_number: Optional[str] = Field(
        default=None,
        max_length=30,
        description="REGA advertisement number. Auto-generated if not provided.",
    )
    property_type: PropertyType = Field(..., description="Type of the property")
    status: ListingStatus = Field(
        default=ListingStatus.PENDING_VERIFICATION,
        description="Listing status",
    )

    # Location
    city: Optional[str] = Field(default=None, max_length=100)
    district: Optional[str] = Field(default=None, max_length=100)
    latitude: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(default=None, ge=-180.0, le=180.0)

    # Pricing & specs
    price: Optional[float] = Field(default=None, ge=0, description="Price in SAR")
    area_sqm: Optional[float] = Field(default=None, ge=0, description="Area in square metres")
    bedrooms: Optional[int] = Field(default=None, ge=0, le=100)
    bathrooms: Optional[int] = Field(default=None, ge=0, le=50)

    # RAG content
    description_ar: Optional[str] = Field(
        default=None, description="Arabic description — embedded in Qdrant for RAG"
    )
    description_en: Optional[str] = Field(
        default=None, description="English description — embedded in Qdrant for RAG"
    )


class PropertyListingUpdate(BaseModel):
    """
    Partial update for a property listing (PATCH semantics).
    Only supplied fields are written to the DB.
    """
    rega_ad_number: Optional[str] = Field(default=None, max_length=30)
    property_type: Optional[PropertyType] = None
    status: Optional[ListingStatus] = None
    is_verified: Optional[bool] = None

    city: Optional[str] = Field(default=None, max_length=100)
    district: Optional[str] = Field(default=None, max_length=100)
    latitude: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(default=None, ge=-180.0, le=180.0)

    price: Optional[float] = Field(default=None, ge=0)
    area_sqm: Optional[float] = Field(default=None, ge=0)
    bedrooms: Optional[int] = Field(default=None, ge=0, le=100)
    bathrooms: Optional[int] = Field(default=None, ge=0, le=50)

    description_ar: Optional[str] = None
    description_en: Optional[str] = None


class PropertyListingResponse(_OrmBase, _TimestampMixin):
    """Full listing representation returned by the API."""
    listing_id: uuid.UUID
    tenant_id: uuid.UUID
    rega_ad_number: str
    property_type: PropertyType
    status: ListingStatus
    is_verified: bool

    city: Optional[str]
    district: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]

    price: Optional[float]
    area_sqm: Optional[float]
    bedrooms: Optional[int]
    bathrooms: Optional[int]

    description_ar: Optional[str]
    description_en: Optional[str]
    qdrant_point_id: Optional[str]


class PropertyListingPage(BaseModel):
    """Paginated list envelope for listing endpoints."""
    items: list[PropertyListingResponse]
    total: int
    page: int
    limit: int
    pages: int

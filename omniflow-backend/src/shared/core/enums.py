"""
shared/core/enums.py — System-Wide Enumerations
All business domain enums live here. Single import point for all services.
"""
from enum import StrEnum


# ─────────────────────────────────────────────────────────────────────────────
# Channel & Platform
# ─────────────────────────────────────────────────────────────────────────────
class Channel(StrEnum):
    WHATSAPP = "whatsapp"
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    X = "x"
    SNAPCHAT = "snapchat"
    WEB = "web"


class MessageType(StrEnum):
    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"
    VIDEO = "video"
    DOCUMENT = "document"
    LOCATION = "location"
    INTERACTIVE = "interactive"   # Quick Reply, List, Buttons
    TEMPLATE = "template"
    STICKER = "sticker"
    REACTION = "reaction"


class ConversationStatus(StrEnum):
    AI_ACTIVE = "ai_active"
    HUMAN_ACTIVE = "human_active"
    ESCALATED = "escalated"
    CLOSED = "closed"
    DORMANT = "dormant"


# ─────────────────────────────────────────────────────────────────────────────
# Tenant & Subscription
# ─────────────────────────────────────────────────────────────────────────────
class TenantTier(StrEnum):
    ECONOMIC = "economic"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class OnboardingStatus(StrEnum):
    PENDING_SELECTION = "pending_selection"
    SELF_SETUP_IN_PROGRESS = "self_setup_in_progress"
    MEETING_SCHEDULED = "meeting_scheduled"
    COMPLETED = "completed"


class SubscriptionStatus(StrEnum):
    TRIAL = "trial"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"


class TenantUserRole(StrEnum):
    ADMIN = "admin"
    AGENT = "agent"
    AUDITOR = "auditor"


# ─────────────────────────────────────────────────────────────────────────────
# Customer VCard State Machine
# ─────────────────────────────────────────────────────────────────────────────
class CustomerVCardState(StrEnum):
    NEW = "STATE_NEW"
    VCARD_SENT = "STATE_VCARD_SENT"
    AWAITING_VALIDATION = "STATE_AWAITING_VALIDATION"
    CONTACT_SAVED_VERIFIED = "STATE_CONTACT_SAVED_VERIFIED"
    REMINDER_1 = "STATE_REMINDER_1"
    REMINDER_2 = "STATE_REMINDER_2"
    DORMANT = "STATE_DORMANT"


# ─────────────────────────────────────────────────────────────────────────────
# LLM Routing Tiers
# ─────────────────────────────────────────────────────────────────────────────
class RoutingTier(StrEnum):
    L0_SEMANTIC_CACHE = "L0"
    L1_TRIAGE = "L1"
    L2_RAG = "L2"
    L3_MASTER = "L3"
    VAULT_RETRIEVAL = "VAULT"
    HUMAN_ESCALATION = "HUMAN"
    VCARD_GATEKEEPER = "VCARD"


class IntentCategory(StrEnum):
    GREETING = "greeting"
    LISTING_SEARCH = "listing_search"
    DEEP_CONSULTATION = "deep_consultation"
    DEED_CHECK = "deed_check"
    MUNICIPAL_CONSULTING = "municipal_consulting"
    REPORT_REQUEST = "report_request"
    BOOKING = "booking"
    PAYMENT = "payment"
    ESCALATION_REQUEST = "escalation_request"
    VAULT_RETRIEVAL = "vault_retrieval"
    VIP_OPT_IN = "vip_opt_in"
    VIP_OPT_OUT = "vip_opt_out"
    VCARD_CONFIRMATION = "vcard_confirmation"
    GENERAL_QUERY = "general_query"
    COMPLAINT = "complaint"


# ─────────────────────────────────────────────────────────────────────────────
# Property Listings
# ─────────────────────────────────────────────────────────────────────────────
class ListingStatus(StrEnum):
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    VERIFIED_ACTIVE = "VERIFIED_ACTIVE"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    SUSPENDED = "SUSPENDED"
    SOLD = "SOLD"
    RENTED = "RENTED"
    WITHDRAWN = "WITHDRAWN"


class PropertyType(StrEnum):
    APARTMENT = "apartment"
    VILLA = "villa"
    LAND = "land"
    COMMERCIAL = "commercial"
    DAILY_RENTAL = "daily_rental"
    OFFICE = "office"
    WAREHOUSE = "warehouse"


# ─────────────────────────────────────────────────────────────────────────────
# Reports
# ─────────────────────────────────────────────────────────────────────────────
class ReportType(StrEnum):
    DEED_CHECK_29 = "deed_check_29"
    MUNICIPAL_CONSULTING_15 = "municipal_consulting_15"
    PREMIUM_CONSULTATION = "premium_consultation"


# ─────────────────────────────────────────────────────────────────────────────
# Payments
# ─────────────────────────────────────────────────────────────────────────────
class TransactionStatus(StrEnum):
    INITIATED = "initiated"
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUNDED = "refunded"
    DISPUTED = "disputed"


class PaymentProvider(StrEnum):
    MOYASAR = "moyasar"
    TAP = "tap"


# ─────────────────────────────────────────────────────────────────────────────
# VIP Subscribers
# ─────────────────────────────────────────────────────────────────────────────
class VipSubscriberStatus(StrEnum):
    ACTIVE = "active"
    OPTED_OUT = "opted_out"
    DECLINED = "declined"
    PENDING = "pending"


# ─────────────────────────────────────────────────────────────────────────────
# Image Classification (Vision Pipeline)
# ─────────────────────────────────────────────────────────────────────────────
class SupportTicketStatus(StrEnum):
    """Lifecycle of a tenant support ticket (table: support_tickets)."""
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    CLOSED = "closed"


# ─────────────────────────────────────────────────────────────────────────────
# Broadcast Campaigns
# ─────────────────────────────────────────────────────────────────────────────
class BroadcastCampaignStatus(StrEnum):
    """Lifecycle of a marketing broadcast (table: broadcast_campaigns)."""
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    SENDING = "sending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


# ─────────────────────────────────────────────────────────────────────────────
# Image Classification (Vision Pipeline)
# ─────────────────────────────────────────────────────────────────────────────
class ImageClassification(StrEnum):
    OFFICIAL_DEED = "official_deed"
    PROPERTY_LAYOUT = "property_layout"
    INTERIOR_FINISH = "interior_finish"
    EXTERIOR_FACADE = "exterior_facade"
    MUNICIPAL_PERMIT = "municipal_permit"
    OTHER = "other"


# ─────────────────────────────────────────────────────────────────────────────
# Knowledge Base (SRS §4.2 — RAG document ingestion)
# ─────────────────────────────────────────────────────────────────────────────
class KnowledgeDocumentStatus(StrEnum):
    """
    Lifecycle of an uploaded knowledge document (table: knowledge_documents).

        uploaded   — stored in S3, ingestion task not started yet
        processing — Celery task `omniflow.ingest_knowledge_document` is running
        indexed    — text extracted, chunked, embedded and written to Qdrant
        failed     — ingestion raised; see `error_message`
    """
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"

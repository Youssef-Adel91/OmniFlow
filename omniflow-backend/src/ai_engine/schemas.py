"""
src/ai_engine/schemas.py — AI Engine Pydantic Schemas

All request/response models for the AI routing pipeline and gateway
inspection layer. These are the primary data contracts between the
channel adapters, the SemanticRouter, the AIGateway, and the LLMOrchestrator.

Design Principles:
    - All models are immutable (frozen=True) to prevent accidental mutation
      as they flow through async pipeline stages.
    - All fields have explicit types and descriptions for API documentation.
    - Enums use StrEnum for JSON serialization compatibility.

References: SRS §2.3 — AI Routing Pipeline
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from src.shared.core.enums import IntentCategory, RoutingTier


# ══════════════════════════════════════════════════════════════════════════════
# Routing Models
# ══════════════════════════════════════════════════════════════════════════════

class RoutingRequest(BaseModel):
    """
    Input contract for the SemanticRouter.decide_route() method.

    Constructed by the channel adapter (Meta, WhatsApp, etc.) and passed
    directly to the SemanticRouter. Contains all the context needed to
    make a routing decision without touching the database.

    Fields:
        request_id          — Unique ID for this routing request (for tracing).
        tenant_id           — The tenant whose AI persona will handle this.
        platform_user_id    — The platform-scoped sender ID (PSID, IGSID, etc.)
        message_text        — The raw text of the inbound message. May be empty
                              for media-only messages.
        message_type        — The canonical message type (text, image, audio…)
        channel             — The originating channel (instagram, whatsapp…)
        conversation_history — Recent exchange (last N turns) for context.
                               Format: [{"role": "user"|"assistant", "content": str}]
        metadata            — Arbitrary context bag (e.g., tenant config flags,
                              feature flags, A/B test variants).
        received_at         — UTC timestamp when the event was ingested.
    """
    model_config = {"frozen": True}

    request_id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        description="Unique identifier for this routing request (used for distributed tracing).",
    )
    tenant_id: uuid.UUID = Field(
        description="The tenant ID owning this conversation.",
    )
    platform_user_id: str = Field(
        description="Platform-scoped sender identifier (PSID, IGSID, WhatsApp phone, etc.).",
    )
    message_text: str = Field(
        default="",
        description="Raw inbound message text. Empty string for media-only messages.",
    )
    message_type: str = Field(
        default="text",
        description="Canonical message type from MessageType enum (text, image, audio…).",
    )
    channel: str = Field(
        description="Originating channel identifier from Channel enum (instagram, whatsapp…).",
    )
    conversation_history: list[dict[str, str]] = Field(
        default_factory=list,
        description=(
            "Recent conversation turns for contextual routing. "
            "Each entry: {\"role\": \"user\"|\"assistant\", \"content\": str}."
        ),
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary context bag (tenant flags, feature toggles, A/B variants).",
    )
    received_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
        description="UTC timestamp when the event was first ingested by the platform.",
    )


class RoutingDecision(BaseModel):
    """
    Output contract from SemanticRouter.decide_route().

    Represents the complete routing decision including the chosen LLM tier,
    the detected intent, confidence level, and audit metadata.

    This model is the primary handoff artifact between the SemanticRouter
    and the LLMOrchestrator. It is also stored in Redis for observability.

    Routing Tiers (from RoutingTier enum in shared/core/enums.py):
        L0_SEMANTIC_CACHE   — Deterministic rule match; no LLM needed.
        L1_TRIAGE           — Fast triage via Gemini Flash-Lite (~300ms).
        L2_RAG              — RAG-augmented via Gemini Flash + Qdrant (~800ms).
        L3_MASTER           — Deep reasoning via Gemini Pro (~2-5s).
        VAULT_RETRIEVAL     — Document retrieval; no LLM needed.
        HUMAN_ESCALATION    — Bypass AI entirely; route to human agent.
        VCARD_GATEKEEPER    — VCard state machine; no LLM needed.
    """
    model_config = {"frozen": True}

    request_id: uuid.UUID = Field(
        description="Mirrors the RoutingRequest.request_id for end-to-end tracing.",
    )
    tier: RoutingTier = Field(
        description="The chosen routing tier. Drives which LLM (if any) is invoked.",
    )
    intent: IntentCategory = Field(
        description="The classified user intent that drove the routing decision.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score [0.0–1.0] for the routing decision.",
    )
    reasoning: str = Field(
        default="",
        description="Human-readable explanation of the routing decision (for logs/audit).",
    )
    requires_rag: bool = Field(
        default=False,
        description="True if the LLMOrchestrator must inject Qdrant context before calling the LLM.",
    )
    escalate_to_human: bool = Field(
        default=False,
        description="True if confidence is below threshold and the conversation needs human review.",
    )
    matched_patterns: list[str] = Field(
        default_factory=list,
        description="L0 regex patterns that matched (for debugging deterministic rules).",
    )
    decided_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
        description="UTC timestamp when the routing decision was made.",
    )


# ══════════════════════════════════════════════════════════════════════════════
# AI Gateway Inspection Models
# ══════════════════════════════════════════════════════════════════════════════

class PreLLMCheckResult(BaseModel):
    """
    Result of AIGateway.pre_llm_inspection() — the security & compliance
    check that runs BEFORE the message is sent to any LLM.

    If is_safe=False, the LLMOrchestrator MUST NOT call the LLM and should
    return the fallback_message to the user instead.

    Checks performed (stubs in Phase 1, real impl in Phase 2):
        - Prompt Injection Detection  (jailbreak / instruction override attempts)
        - PII Detection               (phone numbers, national IDs, credit cards)
        - Tenant Context Verification (tenant is active, not suspended)
        - Content Policy              (profanity, off-topic domains)
    """
    model_config = {"frozen": True}

    is_safe: bool = Field(
        description="True if the message passed all pre-LLM checks and can proceed to the LLM.",
    )
    # ── Prompt Injection ──────────────────────────────────────────────────────
    prompt_injection_detected: bool = Field(
        default=False,
        description="True if a prompt injection / jailbreak attempt was detected.",
    )
    prompt_injection_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence score for prompt injection [0.0–1.0].",
    )
    # ── PII Detection ─────────────────────────────────────────────────────────
    pii_detected: bool = Field(
        default=False,
        description="True if Personally Identifiable Information was found in the message.",
    )
    pii_types_found: list[str] = Field(
        default_factory=list,
        description="List of PII categories found (e.g., ['phone_number', 'national_id']).",
    )
    # ── Tenant Verification ───────────────────────────────────────────────────
    tenant_verified: bool = Field(
        default=True,
        description="True if the tenant context is valid and active.",
    )
    tenant_rejection_reason: str | None = Field(
        default=None,
        description="Reason string if tenant verification failed (e.g., 'suspended', 'trial_expired').",
    )
    # ── Outcome ───────────────────────────────────────────────────────────────
    fallback_message: str | None = Field(
        default=None,
        description=(
            "Pre-written safe response to send to the user when is_safe=False. "
            "The LLM is NOT called in this case."
        ),
    )
    check_duration_ms: float = Field(
        default=0.0,
        description="Wall-clock time taken for all pre-LLM checks in milliseconds.",
    )


class PostLLMCheckResult(BaseModel):
    """
    Result of AIGateway.post_llm_inspection() — the compliance & quality
    check that runs AFTER the LLM produces a response, BEFORE it is sent
    to the customer.

    If is_approved=False, the original LLM response must be suppressed and
    the sanitized_response (or a fallback) must be sent instead.

    Checks performed (stubs in Phase 1, real impl in Phase 2):
        - Hallucination Check         (facts grounded in RAG context?)
        - Toxicity Filter             (harmful / offensive content)
        - Brand Compliance            (tone, persona, no competitor mentions)
        - Legal Disclaimer Enforcement (REGA compliance, financial disclaimers)
        - PII Leakage Check           (LLM accidentally exposing customer data)
    """
    model_config = {"frozen": True}

    is_approved: bool = Field(
        description="True if the LLM response passed all post-LLM checks and can be sent.",
    )
    original_response: str = Field(
        description="The raw LLM-generated response text before any modifications.",
    )
    sanitized_response: str = Field(
        description=(
            "The final response to send to the customer. Equals original_response "
            "when all checks pass; may be modified or replaced when checks fail."
        ),
    )
    # ── Hallucination ─────────────────────────────────────────────────────────
    hallucination_risk: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Estimated hallucination risk score [0.0–1.0]. >0.7 triggers suppression.",
    )
    grounding_sources: list[str] = Field(
        default_factory=list,
        description="RAG chunk IDs / sources used to ground the response (empty for L1).",
    )
    # ── Toxicity ──────────────────────────────────────────────────────────────
    toxicity_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Toxicity score [0.0–1.0]. >0.5 triggers suppression.",
    )
    # ── Brand & Legal ─────────────────────────────────────────────────────────
    brand_compliant: bool = Field(
        default=True,
        description="True if the response adheres to tone, persona, and no-competitor rules.",
    )
    legal_disclaimer_injected: bool = Field(
        default=False,
        description="True if a legal/REGA disclaimer was appended to the response.",
    )
    # ── Outcome ───────────────────────────────────────────────────────────────
    suppression_reason: str | None = Field(
        default=None,
        description="Human-readable reason if is_approved=False (for audit logs).",
    )
    check_duration_ms: float = Field(
        default=0.0,
        description="Wall-clock time taken for all post-LLM checks in milliseconds.",
    )

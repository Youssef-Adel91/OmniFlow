"""
gateway/routers/settings.py — Tenant Settings REST API

Endpoints (prefix /api/v1/settings):
    GET   /   → current tenant configuration (Meta token MASKED)
    PATCH /   → update business_name / max_ai_conversations /
                whatsapp_display_phone_number only

    PATCH /ai-personality → update the AI Personality (system prompt)
    POST  /logo           → upload a company logo (multipart/form-data)

Deliberately NOT editable via generic PATCH:
    meta_access_token, whatsapp_phone_number_id, whatsapp_waba_id,
    subscription_tier, status, onboarding_status.

    The Meta credentials are captured by the existing onboarding endpoint
    (PATCH /api/v1/tenants/onboarding); billing fields are owned by the
    payments pipeline. Allowing them through a generic settings PATCH would
    let any dashboard user silently repoint the tenant's WhatsApp integration.
"""
from __future__ import annotations

import mimetypes
import uuid
from typing import Optional

import phonenumbers
import structlog
from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from src.ai_engine.company_context import invalidate_company_context
from src.gateway.dependencies import AuthTenantSession, CurrentUser
from src.shared.core.config import get_settings
from src.shared.core.enums import TenantUserRole
from src.shared.db.models import Tenant

logger = structlog.get_logger(__name__)
_settings = get_settings()

router = APIRouter(prefix="/api/v1/settings", tags=["Settings"])

# ── Allowed image types for logo upload ──────────────────────────────────────
_ALLOWED_LOGO_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/svg+xml"}
_MAX_LOGO_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB


def _mask_secret(value: str | None) -> Optional[str]:
    """
    Mask a credential for display: `***` plus the last 4 characters.

    Returns None when nothing is configured, and a fully masked value for
    strings too short to reveal a suffix safely.
    """
    if not value:
        return None
    if len(value) <= 4:
        return "***"
    return f"***{value[-4:]}"


# ══════════════════════════════════════════════════════════════════════════════
# Schemas
# ══════════════════════════════════════════════════════════════════════════════

class TenantSettings(BaseModel):
    tenant_id: uuid.UUID
    business_name: str
    subscription_tier: str
    whatsapp_phone_number_id: Optional[str] = None
    max_ai_conversations: int
    meta_access_token: Optional[str] = Field(
        default=None,
        description="MASKED — '***' followed by the last 4 characters, or null.",
    )
    ai_system_prompt: Optional[str] = Field(
        default=None,
        description="Custom AI personality / system prompt for this tenant.",
    )
    logo_url: Optional[str] = Field(
        default=None,
        description="Public S3/MinIO URL of the company logo.",
    )
    whatsapp_display_phone_number: Optional[str] = Field(
        default=None,
        description="E.164 contact number shown on the tenant's VCard (item 7).",
    )


def _normalize_e164(raw: str) -> str:
    """
    Normalize a phone number to E.164 (+<countrycode><number>).

    Item 7 sign-off: numbers typed without an explicit country code default
    to Saudi Arabia (region hint "SA" below) — e.g. "0501234567" ->
    "+966501234567" — but a number given WITH its own country code (a
    leading "+" or "00") is respected as-is, since a brokerage's customers
    are not necessarily Saudi-resident. Never silently force everything to
    +966.
    """
    try:
        parsed = phonenumbers.parse(raw, "SA")
    except phonenumbers.NumberParseException as exc:
        raise ValueError(f"Not a valid phone number: {raw!r}") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError(
            f"Not a valid phone number: {raw!r}. "
            "Include a country code (e.g. +9665...) if this is not a Saudi number."
        )
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


class TenantSettingsPatch(BaseModel):
    business_name: Optional[str] = Field(default=None, min_length=2, max_length=255)
    max_ai_conversations: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    whatsapp_display_phone_number: Optional[str] = Field(default=None, max_length=20)

    @field_validator("whatsapp_display_phone_number")
    @classmethod
    def _validate_display_phone(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _normalize_e164(value)


class AiPersonalityPatch(BaseModel):
    system_prompt: str = Field(
        ...,
        min_length=10,
        max_length=8000,
        description="The new system-prompt text that defines the AI's personality.",
    )


class LogoUploadResponse(BaseModel):
    logo_url: str = Field(..., description="Public URL of the uploaded logo.")


def _to_settings(tenant: Tenant) -> TenantSettings:
    return TenantSettings(
        tenant_id=tenant.tenant_id,
        business_name=tenant.business_name,
        subscription_tier=str(
            tenant.subscription_tier.value
            if hasattr(tenant.subscription_tier, "value")
            else tenant.subscription_tier
        ),
        whatsapp_phone_number_id=tenant.whatsapp_phone_number_id,
        max_ai_conversations=tenant.max_ai_conversations,
        meta_access_token=_mask_secret(tenant.meta_access_token),
        ai_system_prompt=tenant.ai_system_prompt,
        logo_url=tenant.logo_url,
        whatsapp_display_phone_number=tenant.whatsapp_display_phone_number,
    )


async def _load_tenant(session, tenant_id: uuid.UUID) -> Tenant:
    """
    Fetch the caller's own tenant row.

    NOTE: the `tenants` table is intentionally NOT under RLS (it *is* the
    partition key), so the WHERE clause below is the isolation boundary. It
    uses `user.tenant_id` from the verified Clerk token — never client input.
    """
    tenant = await session.scalar(
        select(Tenant).where(Tenant.tenant_id == tenant_id)
    )
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Tenant not found."},
        )
    return tenant


def _require_admin(user: CurrentUser) -> None:
    """Raise 403 unless the caller is a tenant admin."""
    if user.role != TenantUserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN",
                "message": "Only tenant admins can change workspace settings.",
            },
        )


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/settings
# ══════════════════════════════════════════════════════════════════════════════

@router.get("", response_model=TenantSettings, include_in_schema=False)
@router.get(
    "/",
    response_model=TenantSettings,
    status_code=status.HTTP_200_OK,
    summary="Get current tenant settings",
    description="Returns the tenant configuration. `meta_access_token` is masked.",
)
async def get_settings_endpoint(
    user: CurrentUser,
    session: AuthTenantSession,
) -> TenantSettings:
    tenant = await _load_tenant(session, user.tenant_id)
    return _to_settings(tenant)


# ══════════════════════════════════════════════════════════════════════════════
# PATCH /api/v1/settings
# ══════════════════════════════════════════════════════════════════════════════

@router.patch("", response_model=TenantSettings, include_in_schema=False)
@router.patch(
    "/",
    response_model=TenantSettings,
    status_code=status.HTTP_200_OK,
    summary="Update tenant settings",
    description=(
        "Updates `business_name`, `max_ai_conversations`, and/or "
        "`whatsapp_display_phone_number` (normalized to E.164, defaulting to "
        "the Saudi country code when none is given). Requires the `admin` "
        "role. Meta/WhatsApp credentials cannot be changed here — use "
        "PATCH /api/v1/tenants/onboarding."
    ),
)
async def update_settings_endpoint(
    user: CurrentUser,
    session: AuthTenantSession,
    body: TenantSettingsPatch,
) -> TenantSettings:
    _require_admin(user)

    tenant = await _load_tenant(session, user.tenant_id)

    updates = body.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in updates.items():
        setattr(tenant, field, value)

    await session.flush()
    await session.refresh(tenant)

    # business_name is part of the company knowledge block injected into every
    # AI system prompt — drop the cached render so a rename takes effect now.
    invalidate_company_context(user.tenant_id)

    logger.info(
        "tenant_settings_updated",
        tenant_id=str(user.tenant_id),
        fields=sorted(updates.keys()),
    )
    return _to_settings(tenant)


# ══════════════════════════════════════════════════════════════════════════════
# PATCH /api/v1/settings/ai-personality
# ══════════════════════════════════════════════════════════════════════════════

@router.patch(
    "/ai-personality",
    response_model=TenantSettings,
    status_code=status.HTTP_200_OK,
    summary="Update AI Personality (system prompt)",
    description=(
        "Replaces the tenant's custom AI system prompt. This overrides the "
        "global default and is injected as the first system message in every "
        "LLM call for this tenant. Requires the `admin` role."
    ),
)
async def update_ai_personality_endpoint(
    user: CurrentUser,
    session: AuthTenantSession,
    body: AiPersonalityPatch,
) -> TenantSettings:
    _require_admin(user)

    tenant = await _load_tenant(session, user.tenant_id)
    tenant.ai_system_prompt = body.system_prompt.strip()

    await session.flush()
    await session.refresh(tenant)

    logger.info(
        "ai_personality_updated",
        tenant_id=str(user.tenant_id),
        prompt_length=len(body.system_prompt),
    )
    return _to_settings(tenant)


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/settings/logo
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/logo",
    response_model=LogoUploadResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload company logo",
    description=(
        "Accepts a multipart PNG/JPEG/WebP/SVG file (max 5 MB). "
        "Uploads it to the configured S3/MinIO assets bucket and saves the "
        "public URL in the tenant record. Requires the `admin` role."
    ),
)
async def upload_logo_endpoint(
    user: CurrentUser,
    session: AuthTenantSession,
    file: UploadFile = File(..., description="Logo image (PNG, JPEG, WebP or SVG, max 5 MB)"),
) -> LogoUploadResponse:
    _require_admin(user)

    # ── Validate MIME type ───────────────────────────────────────────────────
    content_type = file.content_type or mimetypes.guess_type(file.filename or "")[0] or ""
    if content_type not in _ALLOWED_LOGO_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "code": "UNSUPPORTED_MEDIA_TYPE",
                "message": (
                    f"Logo must be PNG, JPEG, WebP or SVG. "
                    f"Received: {content_type or 'unknown'}"
                ),
            },
        )

    # ── Read and size-check ──────────────────────────────────────────────────
    file_bytes = await file.read()
    if len(file_bytes) > _MAX_LOGO_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "code": "FILE_TOO_LARGE",
                "message": f"Logo must be ≤ 5 MB. Received {len(file_bytes) / 1024 / 1024:.2f} MB.",
            },
        )

    # ── Derive a stable S3 key (deterministic per tenant) ───────────────────
    ext = (file.filename or "logo.png").rsplit(".", 1)[-1].lower()
    s3_key = f"tenants/{user.tenant_id}/logo.{ext}"
    bucket  = _settings.s3_assets_bucket

    # ── Lazy import S3 manager (avoids aiobotocore import at startup) ─────────
    try:
        from src.shared.storage.s3 import s3_mgr  # noqa: PLC0415
    except ImportError as imp_err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "STORAGE_UNAVAILABLE",
                "message": "Storage backend is not configured. Install aiobotocore.",
            },
        ) from imp_err

    # ── Upload to S3 / MinIO ─────────────────────────────────────────────────
    try:
        logo_url = await s3_mgr.upload_file(
            bucket=bucket,
            key=s3_key,
            file_data=file_bytes,
            content_type=content_type,
        )
    except Exception as exc:
        logger.error(
            "logo_upload_failed",
            tenant_id=str(user.tenant_id),
            error=str(exc)[:400],
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "UPLOAD_FAILED",
                "message": "Failed to upload logo to storage. Please try again.",
            },
        ) from exc

    # ── Persist URL in the tenant record ─────────────────────────────────────
    tenant = await _load_tenant(session, user.tenant_id)
    tenant.logo_url = logo_url
    await session.flush()

    logger.info(
        "logo_uploaded",
        tenant_id=str(user.tenant_id),
        s3_key=s3_key,
        size_bytes=len(file_bytes),
    )
    return LogoUploadResponse(logo_url=logo_url)

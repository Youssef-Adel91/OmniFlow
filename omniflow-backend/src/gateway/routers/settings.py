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
from src.shared.security.crypto import reveal_secret
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
    custom_ai_instructions: Optional[str] = Field(
        default=None,
        description=(
            "How the AI should behave with customers: tone, objection "
            "handling, closing a sale, sector-specific guidance. Composed "
            "together with ai_system_prompt in get_tenant_persona()."
        ),
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
    custom_instructions: Optional[str] = Field(
        default=None,
        max_length=4000,
        description=(
            "How the AI should behave with customers: tone, objection "
            "handling, closing a sale, sector-specific guidance. Distinct "
            "from system_prompt (base identity/persona); pass null/omit to "
            "leave unchanged, or an empty string to clear it."
        ),
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
        meta_access_token=_mask_secret(reveal_secret(tenant.meta_access_token)),
        ai_system_prompt=tenant.ai_system_prompt,
        custom_ai_instructions=tenant.custom_ai_instructions,
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
    summary="Update AI Personality (system prompt + custom behavior instructions)",
    description=(
        "Replaces the tenant's custom AI system prompt (base identity/tone), "
        "and optionally the separate custom_ai_instructions field (tone, "
        "objection handling, closing a sale, sector-specific guidance). Both "
        "are composed together and injected as the first system message in "
        "every LLM call for this tenant. Requires the `admin` role."
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
    if body.custom_instructions is not None:
        # Empty string is a deliberate clear; None (the field's default)
        # means "leave whatever is already there untouched".
        tenant.custom_ai_instructions = body.custom_instructions.strip() or None

    await session.flush()
    await session.refresh(tenant)

    logger.info(
        "ai_personality_updated",
        tenant_id=str(user.tenant_id),
        prompt_length=len(body.system_prompt),
        custom_instructions_updated=body.custom_instructions is not None,
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


# ══════════════════════════════════════════════════════════════════════════════
# Preferences: business hours / handoff / AI behaviour / identity
#     GET /api/v1/settings/preferences            → all four sections merged over defaults (+ `configured` flags)
#     PUT /api/v1/settings/preferences/{section}  → replace one section (admin only, validated, audit-logged)
# ══════════════════════════════════════════════════════════════════════════════
from datetime import datetime, timezone  # noqa: E402
from typing import Any  # noqa: E402

from sqlalchemy import func  # noqa: E402

from src.shared.db.models import AuditLog, Conversation, Message, TenantPreferences  # noqa: E402
from src.shared.services import tenant_prefs as prefs_svc  # noqa: E402


@router.get("/preferences", summary="Business hours, handoff, AI behaviour and identity preferences")
async def get_preferences(user: CurrentUser, session: AuthTenantSession) -> dict[str, Any]:
    row = await session.scalar(select(TenantPreferences).where(TenantPreferences.tenant_id == user.tenant_id))
    return {
        "preferences": {s: prefs_svc.with_defaults(s, getattr(row, s) if row else None) for s in prefs_svc.SECTIONS},
        "configured": {s: bool(row and getattr(row, s)) for s in prefs_svc.SECTIONS},
        "defaults": prefs_svc.all_defaults(),
        "days": [{"key": d, "label": prefs_svc.DAY_LABELS_AR[d]} for d in prefs_svc.DAYS],
        "currencies": list(prefs_svc.CURRENCIES),
        "features": {"social_comment_replies": bool(_settings.feature_social_comment_replies)},
        "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
    }


@router.put("/preferences/{section}", summary="Replace one preferences section (admin only)")
async def put_preferences(user: CurrentUser, session: AuthTenantSession, section: str, body: dict[str, Any]) -> dict[str, Any]:
    _require_admin(user)
    if section not in prefs_svc.SECTIONS:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": f"قسم غير معروف: {section}"})
    try:
        clean = prefs_svc.validate_section(section, body)
    except ValueError as exc:                              # pydantic.ValidationError is a ValueError
        msgs = [str(e.get("msg", "")).removeprefix("Value error, ") for e in getattr(exc, "errors", lambda: [])()] or [str(exc)]
        raise HTTPException(status_code=422, detail={"code": "INVALID_PREFERENCES", "message": "؛ ".join(m for m in msgs if m)[:500]}) from exc
    row = await session.scalar(select(TenantPreferences).where(TenantPreferences.tenant_id == user.tenant_id))
    if row is None:
        row = TenantPreferences(tenant_id=user.tenant_id)
        session.add(row)
    setattr(row, section, clean)
    row.updated_by = getattr(user, "user_id", None)
    row.updated_at = datetime.now(timezone.utc)
    session.add(AuditLog(tenant_id=user.tenant_id, actor_user_id=getattr(user, "user_id", None), action=f"settings.{section}_updated",
                         entity_type="tenant_preferences", entity_id=str(user.tenant_id), details={"fields": sorted(clean)}))
    await session.flush()
    prefs_svc.invalidate(user.tenant_id)
    invalidate_company_context(user.tenant_id)             # the persona embeds the AI-behaviour block
    logger.info("tenant_preferences_updated", tenant_id=str(user.tenant_id), section=section)
    return {"section": section, "value": clean}


# ══════════════════════════════════════════════════════════════════════════════
# Channels: status, live health check, disconnect Facebook/Instagram
#     GET  /api/v1/settings/channels
#     POST /api/v1/settings/channels/{whatsapp|facebook}/check
#     POST /api/v1/settings/channels/facebook/disconnect   (admin)
# ══════════════════════════════════════════════════════════════════════════════
import httpx  # noqa: E402

_GRAPH = "https://graph.facebook.com"


def _page_token(tenant: Tenant) -> str | None:
    from src.shared.security.crypto import decrypt_secret

    raw = tenant.instagram_page_access_token
    if not raw:
        return None
    try:
        dec = decrypt_secret(raw)
    except Exception:  # noqa: BLE001
        dec = None
    return dec if dec is not None else raw


async def _last_inbound(session, channels: tuple[str, ...]) -> Optional[str]:
    ts = await session.scalar(
        select(func.max(Message.created_at)).select_from(Message).join(Conversation, Conversation.conversation_id == Message.conversation_id)
        .where(Message.sender_type == "customer", Conversation.channel.in_(channels)))
    return ts.isoformat() if ts else None


@router.get("/channels", summary="Connection status of every channel")
async def channels_status(user: CurrentUser, session: AuthTenantSession) -> dict[str, Any]:
    t = await _load_tenant(session, user.tenant_id)
    wa_connected = bool(t.whatsapp_phone_number_id and t.meta_access_token)
    fb_connected = bool(t.instagram_page_id and t.instagram_page_access_token)
    return {
        "whatsapp": {
            "connected": wa_connected, "phone_number_id": t.whatsapp_phone_number_id, "waba_id": t.whatsapp_waba_id,
            "display_phone_number": t.whatsapp_display_phone_number, "token": _mask_secret(reveal_secret(t.meta_access_token)),
            "templates_ready": bool(wa_connected and t.whatsapp_waba_id),
            "last_inbound_at": await _last_inbound(session, ("whatsapp",)),
            "issues": [m for ok, m in ((wa_connected, "لم يُربط رقم واتساب (معرّف الرقم + رمز الوصول)."),
                                       (not wa_connected or bool(t.whatsapp_waba_id), "معرّف حساب واتساب للأعمال (WABA ID) غير مُدخل — لا تظهر قوالب الحملات."),
                                       (not wa_connected or bool(t.whatsapp_display_phone_number), "رقم العرض (لبطاقة التواصل) غير مُدخل.")) if not ok],
        },
        "facebook": {
            "connected": fb_connected, "page_id": t.instagram_page_id, "instagram_account_id": t.instagram_account_id,
            "messenger": fb_connected, "instagram": bool(fb_connected and t.instagram_account_id),
            "last_inbound_at": await _last_inbound(session, ("instagram", "messenger")),
            "issues": [m for ok, m in ((fb_connected, "لم تُربط صفحة فيسبوك (ماسنجر/إنستجرام)."),
                                       (not fb_connected or bool(t.instagram_account_id), "الصفحة غير مرتبطة بحساب إنستجرام احترافي — رسائل إنستجرام لن تصل.")) if not ok],
        },
    }


@router.post("/channels/{channel}/check", summary="Live connection check against Meta")
async def check_channel(user: CurrentUser, session: AuthTenantSession, channel: str) -> dict[str, Any]:
    t = await _load_tenant(session, user.tenant_id)
    if channel == "whatsapp":
        if not (t.whatsapp_phone_number_id and t.meta_access_token):
            return {"ok": False, "detail": "واتساب غير مربوط.", "checked_at": datetime.now(timezone.utc).isoformat()}
        url, token, fields = f"{_GRAPH}/{_settings.meta_graph_api_version}/{t.whatsapp_phone_number_id}", reveal_secret(t.meta_access_token), "display_phone_number,verified_name,quality_rating"
    elif channel == "facebook":
        token = _page_token(t)
        if not (t.instagram_page_id and token):
            return {"ok": False, "detail": "صفحة فيسبوك غير مربوطة.", "checked_at": datetime.now(timezone.utc).isoformat()}
        url, fields = f"{_GRAPH}/{_settings.meta_graph_api_version}/{t.instagram_page_id}", "name"
    else:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "قناة غير معروفة"})
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params={"fields": fields}, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        return {"ok": False, "detail": f"تعذّر الاتصال بـ Meta ({type(exc).__name__}).", "checked_at": datetime.now(timezone.utc).isoformat()}
    now = datetime.now(timezone.utc).isoformat()
    if resp.status_code == 200:
        data = resp.json()
        return {"ok": True, "detail": "الاتصال سليم.", "checked_at": now,
                "info": {k: data.get(k) for k in ("display_phone_number", "verified_name", "quality_rating", "name") if data.get(k)}}
    try:
        err = resp.json().get("error", {})
    except Exception:  # noqa: BLE001
        err = {}
    expired = err.get("code") in (190, 102) or resp.status_code == 401
    return {"ok": False, "checked_at": now, "needs_reconnect": expired,
            "detail": ("انتهت صلاحية رمز الوصول — أعد الربط." if expired else f"رفضت Meta الطلب ({resp.status_code}): {str(err.get('message', ''))[:160]}")}


@router.post("/channels/facebook/disconnect", summary="Disconnect the Facebook page (Messenger + Instagram) from this workspace")
async def disconnect_facebook(user: CurrentUser, session: AuthTenantSession) -> dict[str, Any]:
    _require_admin(user)
    t = await _load_tenant(session, user.tenant_id)
    had = bool(t.instagram_page_id)
    t.instagram_page_id = t.instagram_page_access_token = t.instagram_account_id = None
    session.add(AuditLog(tenant_id=user.tenant_id, actor_user_id=getattr(user, "user_id", None), action="channel.facebook_disconnected",
                         entity_type="tenant", entity_id=str(user.tenant_id), details={"had_connection": had}))
    await session.flush()
    logger.info("facebook_channel_disconnected", tenant_id=str(user.tenant_id))
    return {"connected": False, "note": "فُصل الربط عن OmniFlow. لإيقاف تسليم Meta بالكامل أزل التطبيق من إعدادات الصفحة في فيسبوك."}


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/settings/ai/test — try the bot with the tenant's real persona (no customer is contacted)
# ══════════════════════════════════════════════════════════════════════════════

class AiTestBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)


@router.post("/ai/test", summary="Ask the bot a question with this workspace's persona + company profile (admin/agent)")
async def ai_test(user: CurrentUser, session: AuthTenantSession, body: AiTestBody) -> dict[str, Any]:
    if user.role == TenantUserRole.AUDITOR:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "المدقق لا يستطيع تجربة البوت."})
    from src.ai_engine.company_context import compose_system_prompt, get_company_context, get_tenant_persona
    from src.shared.core.enums import RoutingTier

    persona = (await get_tenant_persona(user.tenant_id)) or "أنت مساعد مبيعات عقارية محترف."
    system_prompt = compose_system_prompt(persona, await get_company_context(user.tenant_id))
    try:
        from src.ai_workers.llm_invoker.client import gemini_client

        if not getattr(gemini_client, "_configured", False):
            gemini_client.configure()
        started = datetime.now(timezone.utc)
        resp = await gemini_client.generate_response(
            messages=[{"role": "user", "parts": [body.message.strip()]}], tier=RoutingTier.L1_TRIAGE,
            system_prompt=system_prompt, tenant_context={}, rag_context=None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai_test_failed", tenant_id=str(user.tenant_id), error=str(exc)[:200])
        raise HTTPException(status_code=502, detail={"code": "AI_UNAVAILABLE", "message": "تعذّر الوصول لمزوّد الذكاء الاصطناعي. تحقق من إعداد المفتاح ثم أعد المحاولة."}) from exc
    try:    # onboarding checklist: a successful bot test ticks "test the bot"
        tenant = await _load_tenant(session, user.tenant_id)
        tenant.onboarding_state = {**(tenant.onboarding_state or {}), "bot_tested_at": datetime.now(timezone.utc).isoformat()}
        await session.flush()
    except Exception:  # noqa: BLE001 — never fail the test itself over bookkeeping
        logger.warning("onboarding_bot_test_mark_failed", tenant_id=str(user.tenant_id))
    return {"reply": resp.text, "model": getattr(resp, "model_used", None),
            "latency_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            "note": "تجربة داخلية: لا تُرسل لأي عميل ولا تُحفظ، ولا تتضمن بحث العقارات (RAG)."}

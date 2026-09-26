"""
gateway/routers/tenants.py — Tenant Management Router

Authentication is 100% handled by Clerk; this module never mints local JWTs.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from src.ai_engine.company_context import invalidate_company_context
from src.shared.db.session import AsyncSessionFactory, get_tenant_session
from src.shared.db.models import CompanyProfile, Tenant, TenantUser
from src.shared.core.enums import OnboardingStatus
from src.shared.schemas.schemas import TenantOnboardingUpdate
from src.gateway.dependencies import get_current_user
from fastapi import Depends

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/tenants", tags=["Tenants"])


def _generic_persona(business_name: str, category: str | None) -> str:
    """
    A vertical-neutral starting persona for a newly onboarded business.

    Without this, a non-real-estate tenant's AI silently inherits
    llm_orchestrator.py's hardcoded default persona ("أنت المستشار أحمد
    الصائغ ... مستشار عقاري سعودي" — a Saudi real-estate consultant), which
    is simply wrong for any other kind of business. Generated once at
    onboarding time and stored on Tenant.ai_system_prompt; an admin can
    still fully rewrite it later via PATCH /api/v1/settings/ai-personality.
    """
    category_line = f"، متخصص في {category}" if category else ""
    return (
        f"أنت المساعد الذكي الرسمي لمتجر {business_name}{category_line}. "
        "تحدث بأسلوب ودود واحترافي، واعتمد فقط على معلومات الشركة الموضحة أدناه "
        "للإجابة عن استفسارات العملاء حول المنتجات والخدمات. "
        "إذا سُئلت عن شيء غير مذكور في معلومات الشركة، أخبر العميل أنك ستتحقق "
        "وتحوّله لأحد موظفي خدمة العملاء."
    )


# ══════════════════════════════════════════════════════════════════════════════
# Response Schema
# ══════════════════════════════════════════════════════════════════════════════

class OnboardingUpdateResponse(BaseModel):
    """
    Returned by PATCH /tenants/onboarding after a successful status update.

    Authentication is managed entirely by Clerk — no local JWT is issued here.
    The frontend should rely on the Clerk session for identity and re-fetch
    the tenant's onboarding_status from this payload to update its local state.
    """
    tenant_id: str
    user_id: str
    onboarding_status: str


# ══════════════════════════════════════════════════════════════════════════════
# PATCH /api/v1/tenants/onboarding
# ══════════════════════════════════════════════════════════════════════════════

@router.patch(
    "/onboarding",
    response_model=OnboardingUpdateResponse,
    status_code=status.HTTP_200_OK,
    summary="Update tenant onboarding status",
)
async def update_tenant_onboarding(
    payload: TenantOnboardingUpdate,
    current_user: TenantUser = Depends(get_current_user),
) -> OnboardingUpdateResponse:
    """
    Handle Dual-Option Onboarding.

    - white_glove  → MEETING_SCHEDULED  (mocks expert scheduling)
    - self_service → COMPLETED          (saves Meta credentials; EAAD* tokens
                                         are treated as demo/mock and skip
                                         Meta API validation)

    Authentication is 100% Clerk-managed.  No local JWT is minted here.
    The response carries the updated `onboarding_status` so the frontend can
    update its UI state without a round-trip to GET /settings.
    """
    tenant_uuid   = current_user.tenant_id
    tenant_id_str = str(tenant_uuid)
    user_id_str   = str(current_user.user_id)

    # ── Determine target onboarding status ─────────────────────────────────
    if payload.option == "self_service":
        new_status = OnboardingStatus.COMPLETED
    elif payload.option == "white_glove":
        new_status = OnboardingStatus.MEETING_SCHEDULED
    else:
        new_status = OnboardingStatus.PENDING_SELECTION

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.tenant_id == tenant_uuid)
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")

        tenant.onboarding_status = new_status

        if payload.option == "self_service":
            # EAAD* tokens are treated as sandbox credentials:
            # we save them but skip live Meta API validation.
            is_mock = bool(
                payload.meta_access_token
                and payload.meta_access_token.startswith("EAAD")
            )
            if is_mock:
                logger.info(
                    "onboarding_mock_token_bypass",
                    tenant_id=tenant_id_str,
                    note="EAAD demo token — Meta API validation skipped",
                )

            if payload.meta_access_token:
                tenant.meta_access_token = payload.meta_access_token
            if payload.whatsapp_phone_number_id:
                tenant.whatsapp_phone_number_id = payload.whatsapp_phone_number_id
            if payload.whatsapp_waba_id:
                tenant.whatsapp_waba_id = payload.whatsapp_waba_id
            if payload.instagram_page_id:
                tenant.instagram_page_id = payload.instagram_page_id
            if payload.instagram_page_access_token:
                tenant.instagram_page_access_token = payload.instagram_page_access_token

        # ── Business profile step ────────────────────────────────────────────
        # Previously nothing in onboarding collected this at all — a brand-new
        # tenant's business_name stayed "Workspace for {clerk name}" and the AI
        # had zero real company knowledge until someone separately found the
        # dashboard's Knowledge/Settings screens. This is what actually feeds
        # company_context.py's system-prompt block (see that module's
        # docstring) — not a second, disconnected place data goes to die.
        wrote_business_profile = any((
            payload.business_name, payload.business_category,
            payload.about_text, payload.products, payload.detailed_instructions,
        ))
        if wrote_business_profile:
            if payload.business_name:
                tenant.business_name = payload.business_name

            # Never overwrite a persona an admin already customized by hand —
            # onboarding only sets a starting point, once.
            if tenant.ai_system_prompt is None:
                tenant.ai_system_prompt = _generic_persona(
                    tenant.business_name, payload.business_category,
                )

        # Capture the value INSIDE the session before commit closes the transaction
        _onboarding_val = str(
            tenant.onboarding_status.value
            if hasattr(tenant.onboarding_status, "value")
            else tenant.onboarding_status
        )

        await session.commit()

    if wrote_business_profile:
        # CompanyProfile is a real RLS table (unlike `tenants`, which is the
        # RLS partition key itself and intentionally not protected) — needs
        # its own tenant-scoped session, not the bare AsyncSessionFactory
        # session above, or the write would run with no RLS context set at all.
        async with get_tenant_session(tenant_uuid) as profile_session:
            profile = await profile_session.scalar(
                select(CompanyProfile).where(CompanyProfile.tenant_id == tenant_uuid)
            )
            if profile is None:
                profile = CompanyProfile(tenant_id=tenant_uuid)
                profile_session.add(profile)

            if payload.about_text:
                profile.business_description = payload.about_text
            services = [s for s in (payload.business_category, *(payload.products or [])) if s]
            if services:
                profile.services_offered = services
            if payload.detailed_instructions:
                profile.policies_text = payload.detailed_instructions

        invalidate_company_context(tenant_uuid)

    logger.info(
        "onboarding_updated",
        tenant_id=tenant_id_str,
        option=payload.option,
        new_status=_onboarding_val,
        wrote_business_profile=wrote_business_profile,
    )

    return OnboardingUpdateResponse(
        tenant_id=tenant_id_str,
        user_id=user_id_str,
        onboarding_status=_onboarding_val,
    )

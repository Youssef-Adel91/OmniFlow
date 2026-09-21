"""
gateway/routers/tenants.py — Tenant Management Router

Authentication is 100% handled by Clerk; this module never mints local JWTs.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from src.shared.db.session import AsyncSessionFactory
from src.shared.db.models import Tenant, TenantUser
from src.shared.core.enums import OnboardingStatus
from src.shared.schemas.schemas import TenantOnboardingUpdate
from src.gateway.dependencies import get_current_user
from fastapi import Depends

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/tenants", tags=["Tenants"])


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

        # Capture the value INSIDE the session before commit closes the transaction
        _onboarding_val = str(
            tenant.onboarding_status.value
            if hasattr(tenant.onboarding_status, "value")
            else tenant.onboarding_status
        )

        await session.commit()

    logger.info(
        "onboarding_updated",
        tenant_id=tenant_id_str,
        option=payload.option,
        new_status=_onboarding_val,
    )

    return OnboardingUpdateResponse(
        tenant_id=tenant_id_str,
        user_id=user_id_str,
        onboarding_status=_onboarding_val,
    )

"""
gateway/routers/webhooks.py — Clerk Webhook Endpoint

Clerk is the platform's single identity provider (see routers/auth.py).
This webhook keeps the local `tenants` / `tenant_users` tables in sync with
Clerk's user directory.

Handled events:
  user.created — auto-provision a Tenant + an ADMIN TenantUser (idempotent).
  user.updated — sync full_name / email onto the existing TenantUser.
  user.deleted — soft-delete: set is_active = False (never hard-delete, the
                 user is referenced by conversations.assigned_agent_id and
                 messages.agent_id).

Security:
  The Svix signature is verified with `settings.clerk_webhook_secret`.
  If the secret is not configured the request is REJECTED (503) instead of
  being processed unverified — the previous behaviour turned this route into
  an unauthenticated tenant-creation endpoint. Signature verification is
  skipped only when APP_ENV=development AND no secret is configured, and a
  loud warning is emitted.

DB access:
  Uses `get_system_session()` because the tenant is unknown (or does not yet
  exist) at this point. That session sets the reserved nil-UUID sentinel which
  unlocks RLS via migration 0006_system_bypass_policy.
"""
from __future__ import annotations

import json

import structlog
from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from svix.webhooks import Webhook, WebhookVerificationError

from src.shared.core.config import get_settings
from src.shared.core.enums import (
    OnboardingStatus,
    SubscriptionStatus,
    TenantUserRole,
)
from src.shared.db.models import Tenant, TenantUser
from src.shared.db.session import get_system_session

logger = structlog.get_logger(__name__)
settings = get_settings()

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

# Placeholder written into the NOT NULL hashed_password column. Clerk owns all
# credentials; this value is not a valid hash and can never verify.
_CLERK_MANAGED_PASSWORD = "clerk_managed"


def _extract_identity(data: dict) -> tuple[str | None, str, str]:
    """Return (clerk_id, email, full_name) from a Clerk user payload."""
    clerk_id = data.get("id")
    email_addresses = data.get("email_addresses") or []
    email = "unknown@clerk.com"
    if email_addresses:
        # Prefer the primary email when Clerk marks one.
        primary_id = data.get("primary_email_address_id")
        chosen = next(
            (e for e in email_addresses if e.get("id") == primary_id),
            email_addresses[0],
        )
        email = chosen.get("email_address") or email
    first_name = data.get("first_name") or ""
    last_name = data.get("last_name") or ""
    full_name = f"{first_name} {last_name}".strip() or "Clerk User"
    return clerk_id, email.lower().strip(), full_name


async def _verify_and_parse(
    request: Request,
    svix_id: str | None,
    svix_timestamp: str | None,
    svix_signature: str | None,
) -> dict:
    """Verify the Svix signature and return the parsed event payload."""
    payload = await request.body()
    secret = settings.clerk_webhook_secret

    if not secret:
        if settings.is_development:
            logger.warning(
                "clerk_webhook_signature_check_skipped",
                reason="CLERK_WEBHOOK_SECRET not set (development only)",
            )
            return json.loads(payload)
        logger.error("clerk_webhook_secret_missing")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "WEBHOOK_NOT_CONFIGURED",
                "message": "Clerk webhook secret is not configured.",
            },
        )

    headers = {
        "svix-id": svix_id or "",
        "svix-timestamp": svix_timestamp or "",
        "svix-signature": svix_signature or "",
    }
    try:
        return Webhook(secret).verify(payload, headers)
    except WebhookVerificationError as exc:
        logger.warning("clerk_webhook_verification_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_SIGNATURE", "message": "Invalid signature."},
        )
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("clerk_webhook_bad_payload", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_PAYLOAD", "message": "Malformed payload."},
        )


@router.post("/clerk", summary="Clerk user lifecycle webhook")
async def clerk_webhook(
    request: Request,
    svix_id: str = Header(None, alias="svix-id"),
    svix_timestamp: str = Header(None, alias="svix-timestamp"),
    svix_signature: str = Header(None, alias="svix-signature"),
) -> dict:
    event = await _verify_and_parse(request, svix_id, svix_timestamp, svix_signature)

    event_type = event.get("type")
    data = event.get("data") or {}
    clerk_id, email, full_name = _extract_identity(data)

    if not clerk_id:
        logger.warning("clerk_webhook_missing_user_id", event_type=event_type)
        return {"status": "ignored", "reason": "missing user id"}

    if event_type == "user.created":
        return await _handle_user_created(clerk_id, email, full_name)
    if event_type == "user.updated":
        return await _handle_user_updated(clerk_id, email, full_name)
    if event_type == "user.deleted":
        return await _handle_user_deleted(clerk_id)

    logger.info("clerk_webhook_event_ignored", event_type=event_type)
    return {"status": "ok", "message": f"Event '{event_type}' not handled."}


async def _handle_user_created(clerk_id: str, email: str, full_name: str) -> dict:
    """
    Auto-provision Tenant + ADMIN TenantUser for a brand-new Clerk user.

    Idempotent: Clerk retries webhooks, and `get_current_user()` also
    auto-provisions in development, so a race is expected. The unique index on
    `tenant_users.clerk_id` is the authority — an IntegrityError means someone
    else won the race and the request still succeeds.
    """
    async with get_system_session() as session:
        existing = await session.scalar(
            select(TenantUser).where(TenantUser.clerk_id == clerk_id)
        )
        if existing:
            logger.info("clerk_user_already_exists", clerk_id=clerk_id)
            return {"status": "ok", "message": "User already exists"}

        # A SAVEPOINT keeps a lost provisioning race from poisoning the
        # surrounding transaction opened by get_system_session().
        try:
            async with session.begin_nested():
                new_tenant = Tenant(
                    business_name=f"Workspace for {full_name}",
                    # Placeholder satisfying the UNIQUE constraint until the
                    # real FAL licence is captured during onboarding.
                    fal_license_number=f"PENDING-{clerk_id[:8]}",
                    status=SubscriptionStatus.TRIAL,
                    onboarding_status=OnboardingStatus.PENDING_SELECTION,
                )
                session.add(new_tenant)
                await session.flush()  # populate tenant_id

                session.add(
                    TenantUser(
                        clerk_id=clerk_id,
                        tenant_id=new_tenant.tenant_id,
                        full_name=full_name,
                        email=email,
                        role=TenantUserRole.ADMIN,  # first user owns workspace
                        hashed_password=_CLERK_MANAGED_PASSWORD,
                        is_active=True,
                    )
                )
                await session.flush()
        except IntegrityError as exc:
            logger.info(
                "clerk_user_provision_race_resolved",
                clerk_id=clerk_id,
                error=str(exc.orig)[:200],
            )
            return {"status": "ok", "message": "User already provisioned"}

        logger.info(
            "clerk_user_synced",
            clerk_id=clerk_id,
            tenant_id=str(new_tenant.tenant_id),
        )
        return {"status": "ok", "message": "Tenant and user provisioned"}


async def _handle_user_updated(clerk_id: str, email: str, full_name: str) -> dict:
    """Keep the local profile mirror in sync with Clerk."""
    async with get_system_session() as session:
        user = await session.scalar(
            select(TenantUser).where(TenantUser.clerk_id == clerk_id)
        )
        if not user:
            logger.info("clerk_user_update_unknown_user", clerk_id=clerk_id)
            return {"status": "ok", "message": "Unknown user — nothing to update"}

        user.full_name = full_name
        # Guard the UNIQUE(tenant_id, email) constraint: only write a changed,
        # non-placeholder address.
        if email and email != "unknown@clerk.com" and email != user.email:
            user.email = email
        logger.info("clerk_user_updated", clerk_id=clerk_id)
        return {"status": "ok", "message": "User updated"}


async def _handle_user_deleted(clerk_id: str) -> dict:
    """
    Soft-delete. Hard deletion is unsafe: `conversations.assigned_agent_id`
    and `messages.agent_id` reference this row and would lose audit history.
    """
    async with get_system_session() as session:
        user = await session.scalar(
            select(TenantUser).where(TenantUser.clerk_id == clerk_id)
        )
        if not user:
            return {"status": "ok", "message": "Unknown user — nothing to delete"}

        user.is_active = False
        logger.info("clerk_user_deactivated", clerk_id=clerk_id)
        return {"status": "ok", "message": "User deactivated"}

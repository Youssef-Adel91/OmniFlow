"""Team management: seats, invitations (Clerk-delivered or emailed), acceptance, member changes.

Security notes
  * An invitation is only honoured at sign-up when the Clerk payload says the invited address is VERIFIED — otherwise anyone
    could register with a colleague's email and land inside the tenant.
  * The last active admin can never be demoted/deactivated, and nobody can demote/deactivate themselves.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import structlog
from sqlalchemy import func, select

from src.shared.core.config import get_settings
from src.shared.core.enums import TenantUserRole
from src.shared.db.models import AuditLog, Plan, Subscription, TeamInvitation, TenantUser
from src.shared.services import email_sender
from src.shared.services.notifications import notify_safe

logger = structlog.get_logger(__name__)
ROLES = {r.value for r in TenantUserRole}


class TeamError(Exception):
    def __init__(self, code: str, message: str, http: int = 409):
        super().__init__(message)
        self.code, self.message, self.http = code, message, http


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def seat_limit(session, tenant_id: uuid.UUID) -> int | None:
    """None = unlimited (exempt/internal tenants, or a plan without a seat limit). Trial caps and purchased add-on seats included."""
    from src.shared.services.billing import quotas

    limits = await quotas.limits_of(session, tenant_id)
    seats = (limits or {}).get("seats")
    return int(seats) if seats else None


async def seat_usage_raw(session, tenant_id: uuid.UUID) -> dict:
    active = await session.scalar(select(func.count()).select_from(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.is_active.is_(True))) or 0
    pending = await session.scalar(select(func.count()).select_from(TeamInvitation).where(
        TeamInvitation.tenant_id == tenant_id, TeamInvitation.status == "pending", TeamInvitation.expires_at > _now())) or 0
    return {"active": active, "pending": pending, "used": active + pending}


async def seat_usage(session, tenant_id: uuid.UUID) -> dict:
    return {**await seat_usage_raw(session, tenant_id), "limit": await seat_limit(session, tenant_id)}


async def _audit(session, tenant_id, actor, action: str, entity: str, details: dict) -> None:
    session.add(AuditLog(tenant_id=tenant_id, actor_user_id=actor, action=action, entity_type="team", entity_id=entity, details=details))
    await session.flush()


async def _deliver(inv: TeamInvitation, tenant_name: str) -> str:
    """Returns 'clerk' | 'email' | 'none'. Never raises."""
    cfg = get_settings()
    signup = str(cfg.frontend_url).rstrip("/") + "/ar/sign-up"
    if cfg.clerk_secret_key:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(cfg.clerk_api_base.rstrip("/") + "/invitations", headers={"Authorization": f"Bearer {cfg.clerk_secret_key}"},
                                 json={"email_address": inv.email, "redirect_url": signup, "notify": True, "ignore_existing": True,
                                       "public_metadata": {"omniflow_invitation": str(inv.invitation_id)}})
            if r.status_code < 300:
                inv.clerk_invitation_id = str((r.json() or {}).get("id") or "")[:100] or None
                return "clerk"
            logger.warning("clerk_invitation_rejected", status=r.status_code)
        except Exception as exc:  # noqa: BLE001
            logger.warning("clerk_invitation_failed", error=type(exc).__name__)
    if email_sender.is_configured() or email_sender._override is not None:
        html, text = email_sender.render(f"دعوة للانضمام إلى {tenant_name} على OmniFlow",
                                         f"تمت دعوتك للانضمام إلى فريق «{tenant_name}». أنشئ حسابك بنفس هذا البريد الإلكتروني لتنضم تلقائيًا.",
                                         link_label="إنشاء الحساب", link=signup)
        if await email_sender.send(inv.email, f"دعوة للانضمام إلى {tenant_name}", html, text):
            return "email"
    return "none"


async def invite(session, *, tenant_id: uuid.UUID, tenant_name: str, actor: uuid.UUID | None, email: str, role: str) -> TeamInvitation:
    email = email.strip().lower()
    if role not in ROLES:
        raise TeamError("INVALID_ROLE", "الدور غير صالح.", 422)
    if "@" not in email or len(email) > 254:
        raise TeamError("INVALID_EMAIL", "البريد الإلكتروني غير صالح.", 422)
    member = await session.scalar(select(TenantUser).where(TenantUser.tenant_id == tenant_id, func.lower(TenantUser.email) == email, TenantUser.is_active.is_(True)))
    if member is not None:
        raise TeamError("ALREADY_MEMBER", "هذا البريد عضو بالفعل في الفريق.", 409)
    now = _now()
    existing = await session.scalar(select(TeamInvitation).where(TeamInvitation.tenant_id == tenant_id, TeamInvitation.email == email, TeamInvitation.status == "pending"))
    usage = await seat_usage(session, tenant_id)
    if usage["limit"] is not None and usage["used"] - (1 if existing else 0) >= usage["limit"]:
        raise TeamError("SEAT_LIMIT", f"وصلت إلى حد المقاعد في باقتك ({usage['limit']}). رقِّ الباقة لإضافة أعضاء.", 402)
    if existing is not None:                       # re-invite = refresh the same row
        existing.role, existing.expires_at, existing.invited_by_user_id = role, now + timedelta(days=get_settings().team_invite_ttl_days), actor
        inv = existing
    else:
        inv = TeamInvitation(invitation_id=uuid.uuid4(), tenant_id=tenant_id, email=email, role=role, invited_by_user_id=actor, status="pending",
                             expires_at=now + timedelta(days=get_settings().team_invite_ttl_days))
        session.add(inv)
    await session.flush()
    inv.delivery = await _deliver(inv, tenant_name)
    await session.flush()
    await _audit(session, tenant_id, actor, "team.invited", str(inv.invitation_id), {"role": role, "delivery": inv.delivery})
    return inv


async def revoke(session, tenant_id: uuid.UUID, actor: uuid.UUID | None, invitation_id: uuid.UUID) -> None:
    inv = await session.scalar(select(TeamInvitation).where(TeamInvitation.invitation_id == invitation_id, TeamInvitation.status == "pending"))
    if inv is None:
        raise TeamError("NOT_FOUND", "الدعوة غير موجودة.", 404)
    inv.status = "revoked"
    await session.flush()
    await _audit(session, tenant_id, actor, "team.invitation_revoked", str(invitation_id), {})


async def change_member(session, *, tenant_id: uuid.UUID, actor: TenantUser, user_id: uuid.UUID, role: str | None, is_active: bool | None) -> TenantUser:
    target = await session.scalar(select(TenantUser).where(TenantUser.user_id == user_id, TenantUser.tenant_id == tenant_id))
    if target is None:
        raise TeamError("NOT_FOUND", "العضو غير موجود.", 404)
    if role is not None and role not in ROLES:
        raise TeamError("INVALID_ROLE", "الدور غير صالح.", 422)
    if target.user_id == actor.user_id and ((role is not None and role != target.role) or is_active is False):
        raise TeamError("SELF_CHANGE", "لا يمكنك تغيير دورك أو تعطيل حسابك بنفسك.", 409)
    loses_admin = target.role == TenantUserRole.ADMIN and target.is_active and ((role is not None and role != "admin") or is_active is False)
    if loses_admin:
        admins = await session.scalar(select(func.count()).select_from(TenantUser).where(
            TenantUser.tenant_id == tenant_id, TenantUser.is_active.is_(True), TenantUser.role == TenantUserRole.ADMIN)) or 0
        if admins <= 1:
            raise TeamError("LAST_ADMIN", "لا يمكن إزالة آخر مدير في الحساب.", 409)
    if is_active is True and not target.is_active:
        usage = await seat_usage(session, tenant_id)
        if usage["limit"] is not None and usage["used"] >= usage["limit"]:
            raise TeamError("SEAT_LIMIT", f"وصلت إلى حد المقاعد في باقتك ({usage['limit']}).", 402)
    changes = {}
    if role is not None and role != target.role:
        changes["role"] = {"from": str(getattr(target.role, "value", target.role)), "to": role}
        target.role = role
    if is_active is not None and is_active != target.is_active:
        changes["is_active"] = is_active
        target.is_active = is_active
    await session.flush()
    if changes:
        await _audit(session, tenant_id, actor.user_id, "team.member_changed", str(user_id), changes)
    return target


async def accept_for_new_user(session, *, clerk_id: str, email: str, full_name: str, email_verified: bool) -> TenantUser | None:
    """System session. Called from the Clerk `user.created` webhook BEFORE a fresh tenant would be provisioned."""
    if not email_verified or not email or email == "unknown@clerk.com":
        return None
    inv = await session.scalar(select(TeamInvitation).where(TeamInvitation.email == email.lower(), TeamInvitation.status == "pending",
                                                            TeamInvitation.expires_at > _now()).order_by(TeamInvitation.created_at.desc()).limit(1).with_for_update())
    if inv is None:
        return None
    user = await session.scalar(select(TenantUser).where(TenantUser.tenant_id == inv.tenant_id, func.lower(TenantUser.email) == inv.email))
    if user is not None:                                   # a previously deactivated member coming back
        user.clerk_id, user.role, user.is_active, user.full_name = clerk_id, inv.role, True, full_name or user.full_name
    else:
        user = TenantUser(clerk_id=clerk_id, tenant_id=inv.tenant_id, full_name=full_name, email=inv.email, role=inv.role,
                          hashed_password="clerk_managed", is_active=True)  # pragma: allowlist secret (Clerk-managed accounts have no local password)
        session.add(user)
    inv.status, inv.accepted_at = "accepted", _now()
    await session.flush()
    await _audit(session, inv.tenant_id, user.user_id, "team.invitation_accepted", str(inv.invitation_id), {"role": inv.role})
    await notify_safe(session, inv.tenant_id, "team_joined", f"انضم {full_name or inv.email} إلى الفريق", f"الدور: {inv.role}", "/settings#team")
    return user

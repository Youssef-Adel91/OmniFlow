"""
gateway/routers/team.py — team members, roles, invitations, seats (prefix /api/v1/team). Admin writes; auditor reads.

    GET    /                       members + pending invitations + seat usage
    POST   /invitations            { email, role }  — checks the plan's seat limit
    DELETE /invitations/{id}       revoke
    PATCH  /members/{user_id}      { role?, is_active? } — never the last admin, never yourself
"""
from __future__ import annotations

import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, HTTPException, Path, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.gateway.dependencies import AuthTenantSession, CurrentUser
from src.shared.core.enums import TenantUserRole
from src.shared.db.models import Tenant, TeamInvitation, TenantUser
from src.shared.services import team as svc

router = APIRouter(prefix="/api/v1/team", tags=["Team"])


def _admin(user) -> None:
    if user.role != TenantUserRole.ADMIN:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "إدارة الفريق متاحة لمدير الحساب فقط."})


def _reader(user) -> None:
    if user.role not in (TenantUserRole.ADMIN, TenantUserRole.AUDITOR):
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "عرض الفريق متاح للمدير والمدقّق فقط."})


def _err(e: svc.TeamError) -> HTTPException:
    return HTTPException(status_code=e.http, detail={"code": e.code, "message": e.message})


def _member(u: TenantUser) -> dict:
    return {"user_id": str(u.user_id), "full_name": u.full_name, "email": u.email, "role": getattr(u.role, "value", str(u.role)), "is_active": u.is_active,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None}


def _inv(i: TeamInvitation) -> dict:
    return {"invitation_id": str(i.invitation_id), "email": i.email, "role": i.role, "status": i.status, "delivery": i.delivery,
            "created_at": i.created_at.isoformat() if i.created_at else None, "expires_at": i.expires_at.isoformat()}


@router.get("", summary="Members, pending invitations and seats")
async def team(user: CurrentUser, session: AuthTenantSession) -> dict:
    _reader(user)
    # Explicit tenant filter on top of RLS: a dev database whose role is a superuser (the docker default) bypasses RLS, and this page
    # must never list another workspace's people either way.
    members = (await session.execute(select(TenantUser).where(TenantUser.tenant_id == user.tenant_id).order_by(TenantUser.created_at))).scalars().all()
    invs = (await session.execute(select(TeamInvitation).where(TeamInvitation.tenant_id == user.tenant_id, TeamInvitation.status == "pending")
                                  .order_by(TeamInvitation.created_at.desc()))).scalars().all()
    return {"members": [_member(m) for m in members], "invitations": [_inv(i) for i in invs], "seats": await svc.seat_usage(session, user.tenant_id),
            "me": str(user.user_id), "can_edit": user.role == TenantUserRole.ADMIN}


class InviteBody(BaseModel):
    email: str = Field(..., min_length=5, max_length=254)
    role: str = Field("agent")


@router.post("/invitations", status_code=201, summary="Invite a colleague")
async def invite(user: CurrentUser, session: AuthTenantSession, body: InviteBody) -> dict:
    _admin(user)
    tenant = await session.scalar(select(Tenant).where(Tenant.tenant_id == user.tenant_id))
    try:
        inv = await svc.invite(session, tenant_id=user.tenant_id, tenant_name=getattr(tenant, "business_name", "OmniFlow"), actor=user.user_id,
                               email=body.email, role=body.role)
    except svc.TeamError as e:
        raise _err(e) from e
    return _inv(inv)


@router.delete("/invitations/{invitation_id}", status_code=204, summary="Revoke a pending invitation")
async def revoke(user: CurrentUser, session: AuthTenantSession, invitation_id: Annotated[uuid.UUID, Path()]) -> Response:
    _admin(user)
    try:
        await svc.revoke(session, user.tenant_id, user.user_id, invitation_id)
    except svc.TeamError as e:
        raise _err(e) from e
    return Response(status_code=204)


class MemberPatch(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None


@router.patch("/members/{user_id}", summary="Change a member's role or deactivate/reactivate")
async def patch_member(user: CurrentUser, session: AuthTenantSession, user_id: Annotated[uuid.UUID, Path()], body: MemberPatch) -> dict:
    _admin(user)
    try:
        t = await svc.change_member(session, tenant_id=user.tenant_id, actor=user, user_id=user_id, role=body.role, is_active=body.is_active)
    except svc.TeamError as e:
        raise _err(e) from e
    return _member(t)

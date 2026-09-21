"""
gateway/routers/users.py — Current User Profile API

Endpoints (prefix /api/v1/users):
    GET   /me → the authenticated user's row from tenant_users
    PATCH /me → update full_name / phone_number

Identity itself is owned by Clerk. This endpoint exposes the *local mirror*
(`tenant_users`) which carries the tenant binding and role — data Clerk does
not hold. Email is read-only here: it is synchronised from Clerk through the
`user.updated` webhook.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from src.gateway.dependencies import AuthTenantSession, CurrentUser
from src.shared.db.models import TenantUser

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/users", tags=["Users"])


# ══════════════════════════════════════════════════════════════════════════════
# Schemas
# ══════════════════════════════════════════════════════════════════════════════

class UserProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    clerk_id: Optional[str] = None
    full_name: str
    email: str
    phone_number: Optional[str] = None
    role: str
    is_active: bool
    last_login_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class UserProfilePatch(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    phone_number: Optional[str] = Field(default=None, max_length=50)


def _serialize(user: TenantUser) -> UserProfile:
    return UserProfile(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        clerk_id=user.clerk_id,
        full_name=user.full_name,
        email=user.email,
        phone_number=user.phone_number,
        role=str(user.role.value if hasattr(user.role, "value") else user.role),
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/users/me
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/me",
    response_model=UserProfile,
    status_code=status.HTTP_200_OK,
    summary="Get the authenticated user's profile",
)
async def get_me(user: CurrentUser) -> UserProfile:
    # `user` is already the fully-loaded TenantUser row resolved from the
    # verified Clerk token — no extra query needed.
    return _serialize(user)


# ══════════════════════════════════════════════════════════════════════════════
# PATCH /api/v1/users/me
# ══════════════════════════════════════════════════════════════════════════════

@router.patch(
    "/me",
    response_model=UserProfile,
    status_code=status.HTTP_200_OK,
    summary="Update the authenticated user's profile",
    description="Updates `full_name` and/or `phone_number`. Email is managed by Clerk.",
)
async def update_me(
    user: CurrentUser,
    session: AuthTenantSession,
    body: UserProfilePatch,
) -> UserProfile:
    # Re-load inside the RLS-scoped session so the update is written through a
    # session that PostgreSQL will accept under the tenant isolation policy.
    row = await session.scalar(
        select(TenantUser).where(TenantUser.user_id == user.user_id)
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "User not found."},
        )

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(row, field, value)

    await session.flush()
    await session.refresh(row)

    logger.info(
        "user_profile_updated",
        user_id=str(row.user_id),
        fields=sorted(updates.keys()),
    )
    return _serialize(row)

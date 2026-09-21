"""
gateway/routers/auth.py — DEPRECATED local JWT authentication router

╔══════════════════════════════════════════════════════════════════════════╗
║  DEPRECATED — Clerk is the ONLY supported authentication system.         ║
╚══════════════════════════════════════════════════════════════════════════╝

WHY THIS FILE IS DEPRECATED
---------------------------
The platform used to run TWO competing authentication systems side by side:

  1. A local email/password system implemented here (POST /login, /register,
     /refresh) that minted HS256 JWTs signed with `settings.jwt_secret_key`.
  2. Clerk, verified through JWKS/RS256 in
     `src/gateway/dependencies.py::get_current_user`.

Only (2) is actually enforced on protected endpoints. Tokens minted by (1)
were therefore accepted by nothing — but the endpoints stayed reachable and
exposed a password login surface (credential stuffing, user enumeration via
`_get_user_by_email`, and a bcrypt hash column that is now filled with the
literal placeholder "clerk_managed" for every Clerk-provisioned user).

DECISION: Clerk is the single official identity provider.

WHAT CHANGED
------------
  * POST /login, /register, /refresh now return **HTTP 410 Gone** with a
    machine-readable code. They are no longer implemented; the old bodies were
    removed. Clients must authenticate with Clerk and send the Clerk session
    JWT as `Authorization: Bearer <token>`.
  * POST /logout is kept and re-wired to Clerk: it blacklists the `jti` of the
    *currently presented* Clerk token in Redis, which
    `get_current_user()` checks on every request. This provides immediate
    server-side revocation on top of Clerk's own session revocation.
  * `TokenResponse` is retained ONLY because
    `src/gateway/routers/tenants.py` still imports it for its onboarding
    response shape. Do not use it for new code.

DO NOT re-enable the removed endpoints. If local passwords are ever needed
again, that is a product decision requiring a fresh security review.
"""
from __future__ import annotations

import time
from typing import Annotated

import structlog
from fastapi import APIRouter, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from src.shared.core.config import get_settings
from src.shared.security.jwt import (
    blacklist_token,
    decode_clerk_token_unsafe,
    get_revocation_id,
)

logger = structlog.get_logger(__name__)
_settings = get_settings()

router = APIRouter()

_http_bearer = HTTPBearer(auto_error=False)


# ══════════════════════════════════════════════════════════════════════════════
# Schemas (retained for backwards-compatible imports)
# ══════════════════════════════════════════════════════════════════════════════

class TokenResponse(BaseModel):
    """
    DEPRECATED response shape from the legacy local-JWT system.

    Still imported by `routers/tenants.py` for the onboarding endpoint.
    New endpoints must NOT return this model.
    """
    access_token:  str  = Field(..., description="DEPRECATED — legacy local JWT")
    refresh_token: str  = Field(..., description="DEPRECATED — legacy local JWT")
    token_type:    str  = Field(default="bearer")
    tenant_id:     str  = Field(..., description="Authenticated tenant UUID")
    user_id:       str  = Field(..., description="Authenticated user UUID")
    full_name:     str  = Field(..., description="Display name")
    email:         str  = Field(..., description="Email address")
    role:          str  = Field(..., description="admin | agent | auditor")
    onboarding_status: str = Field(..., description="Tenant onboarding flow status")


class MessageResponse(BaseModel):
    message: str


# ══════════════════════════════════════════════════════════════════════════════
# Removed endpoints — HTTP 410 Gone
# ══════════════════════════════════════════════════════════════════════════════

_GONE_DETAIL = {
    "code": "LOCAL_AUTH_REMOVED",
    "message": (
        "Local email/password authentication has been removed. "
        "Authenticate with Clerk and send the Clerk session token as "
        "'Authorization: Bearer <token>'."
    ),
    "message_ar": (
        "تم إيقاف تسجيل الدخول المحلي بالبريد وكلمة المرور نهائيًا. "
        "استخدم Clerk للمصادقة وأرسل التوكن في ترويسة "
        "Authorization: Bearer <token>."
    ),
}


def _gone() -> HTTPException:
    return HTTPException(status_code=status.HTTP_410_GONE, detail=_GONE_DETAIL)


@router.post(
    "/login",
    status_code=status.HTTP_410_GONE,
    summary="[REMOVED] Local password login — use Clerk",
    include_in_schema=False,
)
async def login_removed() -> None:
    logger.warning("deprecated_auth_endpoint_called", endpoint="login")
    raise _gone()


@router.post(
    "/register",
    status_code=status.HTTP_410_GONE,
    summary="[REMOVED] Local registration — use Clerk sign-up",
    include_in_schema=False,
)
async def register_removed() -> None:
    logger.warning("deprecated_auth_endpoint_called", endpoint="register")
    raise _gone()


@router.post(
    "/refresh",
    status_code=status.HTTP_410_GONE,
    summary="[REMOVED] Local token refresh — Clerk manages sessions",
    include_in_schema=False,
)
async def refresh_removed() -> None:
    logger.warning("deprecated_auth_endpoint_called", endpoint="refresh")
    raise _gone()


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/auth/logout — Clerk-aware token revocation
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/logout",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Revoke the presented Clerk session token",
    description=(
        "Blacklists the `jti` of the Clerk token supplied in the "
        "`Authorization: Bearer` header. `get_current_user()` checks this "
        "blacklist on every request, so revocation takes effect immediately "
        "even before the token expires.\n\n"
        "This complements — it does not replace — signing out of Clerk on "
        "the client, which should still be performed by the frontend SDK."
    ),
)
async def logout(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(_http_bearer),
    ] = None,
) -> MessageResponse:
    """
    Revoke the current session token.

    Decoding is done WITHOUT signature/expiry verification on purpose: we only
    read `jti`/`exp` to build a blacklist entry, and blacklisting an
    already-invalid token is harmless. Nothing here grants access.

    Always returns 200 — logout must never leak whether a token was valid.
    """
    if not credentials or not credentials.credentials:
        return MessageResponse(message="Successfully logged out.")

    payload = decode_clerk_token_unsafe(credentials.credentials)
    if payload:
        jti = get_revocation_id(payload)
        if jti:
            remaining = int(payload.get("exp", time.time()) - time.time())
            # Cap the TTL so a malformed 'exp' cannot pin an entry forever.
            ttl = max(1, min(remaining, 24 * 60 * 60))
            try:
                await blacklist_token(jti=str(jti), ttl_seconds=ttl)
                logger.info("clerk_token_revoked", jti=str(jti), ttl_seconds=ttl)
            except Exception as exc:  # Redis down — do not fail the logout
                logger.warning("token_blacklist_failed", error=str(exc)[:200])

    return MessageResponse(message="Successfully logged out.")

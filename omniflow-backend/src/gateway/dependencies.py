"""
gateway/dependencies.py — FastAPI Dependency Injection Providers

This module is the DI wiring layer. Every FastAPI endpoint that needs DB
access, tenant context, or a repository instance uses Depends() from here.

Dependency graph:
    Clerk access token
        └─► get_tenant_id()           → uuid.UUID
                └─► get_tenant_db_session() → AsyncSession  (RLS context set)
                        ├─► get_customer_repo()       → CustomerRepository
                        └─► get_conversation_repo()   → ConversationRepository

Security Note:
    All tenant sessions derive their identity from the authenticated user.
    X-Tenant-ID cannot select another tenant or the system bypass context.

RLS Note:
    `get_tenant_db_session` calls `get_tenant_session(tenant_id)` which
    executes:
        SET LOCAL app.current_tenant_id = '<uuid>';
    before yielding. PostgreSQL enforces row isolation automatically.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.core.config import get_settings
from src.shared.db.models import TenantUser
from src.shared.db.repository import (
    ConversationRepository,
    CustomerRepository,
    PropertyListingRepository,
)
from src.shared.db.session import get_tenant_session, get_system_session
from src.shared.security.jwt import (
    get_revocation_id,
    is_token_blacklisted,
    verify_clerk_token,
)

_settings = get_settings()

# ══════════════════════════════════════════════════════════════════════════════
# 4. JWT Authentication — get_current_user
# ══════════════════════════════════════════════════════════════════════════════

_http_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(_http_bearer),
    ] = None,
) -> TenantUser:
    """
    Dependency: validate the Bearer JWT and return the authenticated TenantUser.

    Flow:
        1. Extract `Authorization: Bearer <token>` header.
        2. Verify signature + expiry + type = 'access' via verify_access_token().
        3. Check Redis blacklist (revoked tokens from logout).
        4. Fetch TenantUser from DB via system session (bypasses RLS — we need
           the user row before we know which tenant session to open).
        5. Verify user is still active.

    The returned TenantUser carries `.tenant_id`, `.user_id`, `.role`, etc.
    Endpoint functions receive this object and can trust it completely.

    Raises:
        HTTP 401 — missing token, expired, revoked, or user deactivated.
    """
    _UNAUTH = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "UNAUTHORIZED", "message": "Authentication required."},
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not credentials or not credentials.credentials:
        raise _UNAUTH

    token = credentials.credentials

    # ── Step 1: Verify JWT signature using Clerk (JWKS / RS256) ───────────────
    # verify_clerk_token() wraps every failure mode (malformed token, unknown
    # issuer, JWKS fetch error, bad signature, expired) into JWTError. The bare
    # `except Exception` is a defence-in-depth net so that an unexpected error
    # from the JWKS HTTP client can never surface as a 500 that leaks
    # infrastructure details — it is always a clean 401.
    try:
        payload = verify_clerk_token(token)
    except JWTError:
        raise _UNAUTH
    except Exception:  # noqa: BLE001 — see comment above
        from src.shared.core.logger import get_logger
        get_logger(__name__).warning("clerk_token_verification_unexpected_error")
        raise _UNAUTH

    if not isinstance(payload, dict):
        raise _UNAUTH

    # ── Step 2: Redis blacklist check (revoked tokens) ────────────────────────
    # Clerk session tokens key on `sid`; legacy local tokens key on `jti`.
    revocation_id = get_revocation_id(payload)
    if revocation_id and await is_token_blacklisted(revocation_id):
        raise _UNAUTH

    # ── Step 3: Fetch TenantUser by clerk_id — verify still active ────────────
    clerk_id = payload.get("sub")
    if not clerk_id:
        raise _UNAUTH

    from sqlalchemy import select as _select
    from sqlalchemy.exc import IntegrityError as _IntegrityError
    user: TenantUser | None = None

    async with get_system_session() as session:
        result = await session.execute(
            _select(TenantUser)
            .where(TenantUser.clerk_id == clerk_id)
            .where(TenantUser.is_active == True)  # noqa: E712
        )
        user = result.scalar_one_or_none()

        if not user:
            if _settings.is_development:
                from src.shared.db.models import Tenant
                from src.shared.core.enums import SubscriptionStatus, OnboardingStatus, TenantUserRole
                from src.shared.core.logger import get_logger
                _log = get_logger(__name__)
                try:
                    new_tenant = Tenant(
                        business_name="Dev Workspace",
                        fal_license_number=f"DEV-{clerk_id[:8]}",
                        status=SubscriptionStatus.TRIAL,
                        onboarding_status=OnboardingStatus.PENDING_SELECTION,
                    )
                    session.add(new_tenant)
                    await session.flush()

                    user = TenantUser(
                        clerk_id=clerk_id,
                        tenant_id=new_tenant.tenant_id,
                        full_name="Local Dev User",
                        email="dev@example.com",
                        role=TenantUserRole.ADMIN,
                        hashed_password="clerk_managed",
                    )
                    session.add(user)
                    # NOTE: do NOT call session.commit() here — get_system_session()
                    # already wraps this block in `async with session.begin()`, which
                    # commits on clean exit. Committing inside the block closes the
                    # transaction the context manager still owns.
                    await session.flush()
                    _log.info("auth_dev_auto_provisioned", clerk_id=clerk_id)
                except _IntegrityError:
                    # Another concurrent request already provisioned this user — just fetch them
                    await session.rollback()
                    result2 = await session.execute(
                        _select(TenantUser)
                        .where(TenantUser.clerk_id == clerk_id)
                        .where(TenantUser.is_active == True)  # noqa: E712
                    )
                    user = result2.scalar_one_or_none()
                    if not user:
                        raise _UNAUTH
                    _log.info("auth_dev_fetched_after_conflict", clerk_id=clerk_id)
            else:
                from src.shared.core.logger import get_logger
                get_logger(__name__).warning("auth_user_not_found_in_db", clerk_id=clerk_id)
                raise _UNAUTH

    return user


# Shorthand — most endpoints just need the user object
CurrentUser = Annotated[TenantUser, Depends(get_current_user)]


# ══════════════════════════════════════════════════════════════════════════════
# 1. Tenant Identity — resolved from the authenticated user
# ══════════════════════════════════════════════════════════════════════════════

async def get_tenant_id(user: CurrentUser) -> uuid.UUID:
    """Use the verified user's tenant; client-supplied headers are never trusted."""
    tenant_id = uuid.UUID(str(user.tenant_id))
    if tenant_id.int == 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "System tenant cannot access the dashboard."},
        )
    return tenant_id


# Shorthand type alias used in endpoint signatures
TenantId = Annotated[uuid.UUID, Depends(get_tenant_id)]


# ══════════════════════════════════════════════════════════════════════════════
# 2. DB Session — tenant-scoped AsyncSession with RLS GUC injected
# ══════════════════════════════════════════════════════════════════════════════

async def get_tenant_db_session(
    tenant_id: TenantId,
) -> AsyncGenerator[AsyncSession, None]:
    """
    Yield a tenant-scoped AsyncSession.

    Internally calls `get_tenant_session(tenant_id)` which:
      1. Opens an AsyncSession from AsyncSessionFactory
      2. Begins a transaction
      3. Executes SET LOCAL app.current_tenant_id = '<tenant_id>'
      4. Yields the session (PostgreSQL RLS now active)
      5. Commits on clean exit; rolls back on exception

    This is a FastAPI generator dependency — FastAPI handles the
    `async for` / cleanup lifecycle automatically.
    """
    async with get_tenant_session(tenant_id) as session:
        yield session


# Shorthand type alias
TenantSession = Annotated[AsyncSession, Depends(get_tenant_db_session)]


# ══════════════════════════════════════════════════════════════════════════════
# 3. Repository Providers
# ══════════════════════════════════════════════════════════════════════════════

def get_customer_repo(
    session: TenantSession,
) -> CustomerRepository:
    """
    Provide a CustomerRepository bound to the tenant-scoped session.

    The repository inherits RLS from the session — no extra work needed.
    Instantiation is synchronous and negligible cost.

    Usage in endpoint:
        @router.get("/customers/{customer_id}")
        async def get_customer(
            customer_id: uuid.UUID,
            repo: Annotated[CustomerRepository, Depends(get_customer_repo)],
        ) -> CustomerResponse:
            return await repo.get_or_404(customer_id)
    """
    return CustomerRepository(session)


def get_conversation_repo(
    session: TenantSession,
) -> ConversationRepository:
    """
    Provide a ConversationRepository bound to the tenant-scoped session.

    Usage in endpoint:
        @router.get("/conversations/{conv_id}/messages")
        async def get_conversation(
            conv_id: uuid.UUID,
            repo: Annotated[ConversationRepository, Depends(get_conversation_repo)],
        ) -> ConversationResponse:
            conv = await repo.get_with_messages(conv_id)
            ...
    """
    return ConversationRepository(session)


# ── Annotated shorthands for cleaner endpoint signatures ─────────────────────
CustomerRepo = Annotated[CustomerRepository, Depends(get_customer_repo)]
ConversationRepo = Annotated[ConversationRepository, Depends(get_conversation_repo)]


# ══════════════════════════════════════════════════════════════════════════════
# 5. RBAC guards for property write operations
# ══════════════════════════════════════════════════════════════════════════════

from src.shared.core.enums import TenantUserRole  # noqa: E402


async def require_property_write_role(user: CurrentUser) -> TenantUser:
    """
    RBAC guard: allow `admin` and `agent` roles to create/update properties.

    `auditor` is read-only — they may fetch listings but not mutate them.

    Dependency usage:
        @router.post("/")
        async def create_property(
            body: PropertyListingCreate,
            user: Annotated[TenantUser, Depends(require_property_write_role)],
            repo: PropertyListingRepo,
        ) -> PropertyListingResponse:
            ...
    """
    allowed = {TenantUserRole.ADMIN, TenantUserRole.AGENT}
    if user.role not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN",
                "message": (
                    f"Role '{user.role}' is not permitted to create or modify "
                    "property listings. Required: admin or agent."
                ),
            },
        )
    return user


# Shorthand for endpoints that mutate properties
PropertyWriteUser = Annotated[TenantUser, Depends(require_property_write_role)]


# ══════════════════════════════════════════════════════════════════════════════
# 6. PropertyListing Repository Provider
#
# CRITICAL FIX: derive the tenant DB session from the *JWT user's* tenant_id,
# NOT from the X-Tenant-ID header.
#
# The old approach (Depends(TenantSession)) read X-Tenant-ID from the request
# header. When the frontend omitted this header or sent the dev-fallback UUID
# (00000000-...) instead of the real tenant UUID, the RLS GUC was set to the
# wrong tenant — causing INSERT/SELECT to operate against an invisible partition
# and silently returning empty / failing the constraint.
#
# The new approach opens a tenant session using user.tenant_id extracted from
# the verified JWT, making it impossible to have a mismatch.
# ══════════════════════════════════════════════════════════════════════════════

async def get_property_listing_repo(
    user: CurrentUser,
) -> AsyncGenerator[PropertyListingRepository, None]:
    """
    Provide a PropertyListingRepository whose DB session RLS context is derived
    from the authenticated user's JWT tenant_id — not the X-Tenant-ID header.

    This guarantees that:
      1. The RLS GUC matches the JWT claim — no cross-tenant leakage.
      2. Agents do not need to send X-Tenant-ID; the JWT is the single source
         of truth for tenant identity on authenticated endpoints.
      3. The session is committed/rolled back by the context manager — the
         endpoint handler does not need to manage the transaction.

    Usage in endpoint (same as before — transparent to callers):
        @router.post("")
        async def create_property(
            body: PropertyListingCreate,
            user: CurrentUser,
            repo: PropertyListingRepo,
        ) -> PropertyListingResponse:
            ...
    """
    async with get_tenant_session(await get_tenant_id(user)) as session:
        yield PropertyListingRepository(session)


PropertyListingRepo = Annotated[
    PropertyListingRepository,
    Depends(get_property_listing_repo),
]


# ══════════════════════════════════════════════════════════════════════════════
# 7. Tenant DB session derived from the authenticated Clerk user
#
# Preferred session dependency for ALL new endpoints.
#
# Like `TenantSession`, this derives the RLS context from the verified Clerk
# user. Both aliases are retained for existing router imports.
# ══════════════════════════════════════════════════════════════════════════════

async def get_authenticated_tenant_session(
    user: CurrentUser,
) -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an AsyncSession whose RLS context is `user.tenant_id`.

    The transaction is committed when the endpoint returns normally and rolled
    back if it raises — handled by `get_tenant_session`.
    """
    async with get_tenant_session(await get_tenant_id(user)) as session:
        yield session


# Shorthand used by the customers / reports / settings / support /
# broadcasts / users / dashboard routers.
AuthTenantSession = Annotated[
    AsyncSession,
    Depends(get_authenticated_tenant_session),
]

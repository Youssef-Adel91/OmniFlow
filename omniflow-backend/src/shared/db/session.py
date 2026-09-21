"""
shared/db/session.py — Async SQLAlchemy engine + RLS session factory

KEY DESIGN: Every database transaction MUST begin by injecting the tenant_id
into the `app.current_tenant_id` GUC. This is enforced here so no business
logic code can forget it (it would get an empty result set from PostgreSQL
RLS, not a data leak).

PgBouncer Note:
  We use transaction pooling mode. `set_config(..., is_local => true)` is the
  function form of `SET LOCAL` and is scoped to the current transaction only,
  which is exactly what we want.

SQL-injection note:
  PostgreSQL does NOT accept bind parameters in a literal `SET LOCAL x = ...`
  statement, which is why the original implementation interpolated the tenant
  id with an f-string. We use `SELECT set_config(:key, :value, true)` instead —
  a normal function call that fully supports bind parameters — and we
  additionally coerce the value through `uuid.UUID()` so a non-UUID value can
  never reach the database.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from src.shared.core.config import get_settings

settings = get_settings()

# ─────────────────────────────────────────────────────────────────────────────
# RLS GUC name and the reserved "system" sentinel tenant id.
#
# WHY A UUID SENTINEL AND NOT THE STRING 'system':
#   The RLS policy created in alembic/versions/0002_rls_policies.py evaluates
#       tenant_id = current_setting('app.current_tenant_id', true)::uuid
#   The `::uuid` cast raises
#       invalid input syntax for type uuid: "system"
#   on EVERY query executed inside a system session that touches a table with
#   FORCE ROW LEVEL SECURITY enabled (tenant_users, customers,
#   property_listings, conversations, customer_reports).
#
#   In practice this broke `get_current_user()` (which reads `tenant_users`
#   through a system session) and the Clerk `user.created` webhook
#   auto-provisioning path.
#
#   Fix: use the reserved nil UUID. Migration 0006_system_bypass_policy adds a
#   permissive policy on every RLS table that unlocks all rows when the GUC
#   equals this sentinel. The value is therefore cast-safe *and* genuinely
#   grants cross-tenant access — no silent empty result sets.
#
# OPERATIONAL CAVEAT (needs a DevOps decision, documented deliberately):
#   The policy-based bypass only applies to the five tables listed in
#   migration 0002. If new tenant-scoped tables are added later, the same
#   `system_bypass_policy` MUST be created for them, otherwise system-session
#   reads of those tables will silently return zero rows. The alternative,
#   cleaner long-term solution is to connect the application with a dedicated
#   database role holding `BYPASSRLS` for system operations only — that
#   requires a separate connection pool and a DevOps change outside this
#   module's scope.
# ─────────────────────────────────────────────────────────────────────────────
RLS_GUC = "app.current_tenant_id"
SYSTEM_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")

# Parameterised equivalent of `SET LOCAL app.current_tenant_id = <value>`.
_SET_TENANT_SQL = text("SELECT set_config(:guc, :tenant_id, true)")


# ─────────────────────────────────────────────────────────────────────────────
# Engine — connects through PgBouncer (transaction mode)
# NullPool is used because PgBouncer manages the actual connection pool.
# Using SQLAlchemy's pool on top of PgBouncer causes connection explosion.
# ─────────────────────────────────────────────────────────────────────────────
engine = create_async_engine(
    settings.database_url,
    poolclass=NullPool,       # PgBouncer manages the pool
    echo=settings.is_development,
    future=True,
    connect_args={
        "server_settings": {
            "application_name": settings.app_name,
            "timezone": settings.app_timezone,
        }
    },
)

# ─────────────────────────────────────────────────────────────────────────────
# Session factory — autocommit=False, autoflush=False (explicit control)
# ─────────────────────────────────────────────────────────────────────────────
AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


async def _set_rls_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """
    Inject the RLS tenant context into the *current transaction*.

    `tenant_id` is coerced through `uuid.UUID()` first: any value that is not
    a well-formed UUID raises ValueError before touching the database, which
    makes SQL injection through this path impossible even if a caller passes
    an unvalidated string.
    """
    validated = uuid.UUID(str(tenant_id))
    await session.execute(
        _SET_TENANT_SQL,
        {"guc": RLS_GUC, "tenant_id": str(validated)},
    )


@asynccontextmanager
async def get_tenant_session(tenant_id: uuid.UUID) -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager that yields an AsyncSession with RLS tenant context injected.

    Usage:
        async with get_tenant_session(tenant_id) as session:
            result = await session.execute(select(Customer))

    The setting is transaction-local. When the transaction ends the setting
    resets — safe for PgBouncer transaction pooling.

    Raises:
        ValueError — if `tenant_id` is not a valid UUID.
    """
    async with AsyncSessionFactory() as session:
        async with session.begin():
            # Inject tenant context FIRST — before any query touches the DB.
            # This is the RLS enforcement mechanism.
            await _set_rls_tenant(session, tenant_id)
            try:
                yield session
            except Exception:
                await session.rollback()
                raise


@asynccontextmanager
async def get_system_session() -> AsyncGenerator[AsyncSession, None]:
    """
    System-level session that bypasses RLS via the reserved nil-UUID sentinel.

    ONLY use for:
      - Platform admin operations
      - Cross-tenant migrations
      - Identity lookups that happen *before* the tenant is known
        (e.g. resolving a Clerk user id to a TenantUser row)
      - System health checks

    NEVER expose this to tenant-facing code paths: inside this session RLS is
    effectively disabled for every table covered by migration
    0006_system_bypass_policy, so any query here can read and write across
    tenant boundaries. Always scope your queries explicitly.

    See the module-level comment on SYSTEM_TENANT_ID for why the previous
    `'system'` string value was broken and what the long-term alternative is.
    """
    async with AsyncSessionFactory() as session:
        async with session.begin():
            await _set_rls_tenant(session, SYSTEM_TENANT_ID)
            try:
                yield session
            except Exception:
                await session.rollback()
                raise


async def get_db_connection() -> AsyncGenerator[AsyncConnection, None]:
    """Raw connection for DDL operations (migrations, etc.)."""
    async with engine.begin() as conn:
        yield conn

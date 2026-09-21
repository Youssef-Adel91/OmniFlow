"""
shared/db/base.py — SQLAlchemy Declarative Base & Core Mixins

Provides three building blocks for every OmniFlow ORM model:
  1. Base             — Single DeclarativeBase (one metadata registry for Alembic).
  2. TimestampMixin   — Injects created_at / updated_at automatically.
  3. TenantScopedMixin — Documents the RLS contract; concrete FK declared per-model
                         to avoid mapper-configuration ordering errors.

RLS Enforcement Pattern (applied per-table in Alembic migration):
    ALTER TABLE <table> ENABLE ROW LEVEL SECURITY;
    ALTER TABLE <table> FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON <table>
        USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

References: SRS §6, SRS §2.3
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """
    Single SQLAlchemy declarative base for ALL OmniFlow models.

    Alembic env.py MUST do:
        from src.shared.db.base import Base
        from src.shared.db import models  # noqa: F401  — registers all mappers
        target_metadata = Base.metadata
    """
    pass


class TimestampMixin:
    """
    Mixin: audit timestamps (TIMESTAMPTZ) on every model row.

    - created_at: set once by DB server on INSERT; indexed for time-range queries.
    - updated_at: updated by DB server on every UPDATE via onupdate.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
        comment="Row creation timestamp (TIMESTAMPTZ)",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="Last mutation timestamp (TIMESTAMPTZ)",
    )


class TenantScopedMixin:
    """
    Mixin contract for all tenant-scoped tables (every table except 'tenants').

    IMPORTANT: Do NOT declare `tenant_id` with ForeignKey here.
    Mixins resolve before concrete classes are mapped, causing
    "foreign key refers to non-existing table" mapper errors.

    Each subclass MUST explicitly declare:

        tenant_id: Mapped[uuid.UUID] = mapped_column(
            UUID(as_uuid=True),
            ForeignKey("tenants.tenant_id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
            comment="RLS partition key — must match app.current_tenant_id",
        )

    This mixin exists purely as a type-level marker and documentation anchor.
    All models in models.py follow this contract.
    """
    pass

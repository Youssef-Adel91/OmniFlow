"""support_tickets + broadcast_campaigns

Revision ID: 0007_support_broadcast
Revises: 0006_system_bypass_policy
Create Date: 2026-08-12

Adds the two tables backing the new dashboard APIs:

  support_tickets      — tenant support requests (GET/POST/PATCH /api/v1/support)
  broadcast_campaigns  — VIP/marketing campaigns (/api/v1/broadcasts)

`broadcast_campaigns` did not exist even though
`src/ai_workers/broadcast_worker/worker.py` already queries it with raw SQL,
so the columns that worker expects (campaign_type, target_audience,
meta_template_id, status, completed_at) are included here.

Both tables get the standard RLS treatment: ENABLE + FORCE ROW LEVEL SECURITY,
the per-tenant isolation policy from 0002, and the system-session bypass policy
from 0006.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0007_support_broadcast"
down_revision: Union[str, None] = "0006_system_bypass_policy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_TABLES = ["support_tickets", "broadcast_campaigns"]
_GUC = "app.current_tenant_id"
_ISOLATION_POLICY = "tenant_isolation_policy"
_SYSTEM_POLICY = "system_bypass_policy"
_SYSTEM_TENANT_ID = "00000000-0000-0000-0000-000000000000"


def _apply_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS {_ISOLATION_POLICY} ON {table};")
    op.execute(
        f"""
        CREATE POLICY {_ISOLATION_POLICY} ON {table}
            USING (tenant_id = current_setting('{_GUC}', true)::uuid);
        """
    )
    op.execute(f"DROP POLICY IF EXISTS {_SYSTEM_POLICY} ON {table};")
    op.execute(
        f"""
        CREATE POLICY {_SYSTEM_POLICY} ON {table}
            AS PERMISSIVE
            FOR ALL
            USING (
                current_setting('{_GUC}', true)::uuid = '{_SYSTEM_TENANT_ID}'::uuid
            );
        """
    )


def upgrade() -> None:
    # ── support_tickets ──────────────────────────────────────────────────────
    op.create_table(
        "support_tickets",
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="RLS partition key — must match app.current_tenant_id",
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Author of the ticket; NULL if the user was later removed",
        ),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="open",
            nullable=False,
            comment="open | in_progress | closed",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.tenant_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["tenant_users.user_id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("ticket_id"),
    )
    op.create_index(
        "ix_support_tickets_tenant_id", "support_tickets", ["tenant_id"]
    )
    op.create_index(
        "ix_support_tickets_created_at", "support_tickets", ["created_at"]
    )
    op.create_index(
        "ix_support_tickets_tenant_status",
        "support_tickets",
        ["tenant_id", "status"],
    )
    op.create_index(
        "ix_support_tickets_created_by", "support_tickets", ["created_by_user_id"]
    )

    # ── broadcast_campaigns ──────────────────────────────────────────────────
    op.create_table(
        "broadcast_campaigns",
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="RLS partition key — must match app.current_tenant_id",
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("message_template", sa.Text(), nullable=False),
        sa.Column(
            "target_audience",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment='Audience selector, e.g. {"list_type": "daily_rentals"}',
        ),
        sa.Column(
            "campaign_type",
            sa.String(length=40),
            nullable=True,
            comment="Free-form campaign classification used by the broadcast worker",
        ),
        sa.Column(
            "meta_template_id",
            sa.String(length=120),
            nullable=True,
            comment="Approved Meta WhatsApp template ID used for the send",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="draft",
            nullable=False,
            comment="draft | scheduled | sending | completed | cancelled | failed",
        ),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.tenant_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("campaign_id"),
    )
    op.create_index(
        "ix_broadcast_campaigns_tenant_id", "broadcast_campaigns", ["tenant_id"]
    )
    op.create_index(
        "ix_broadcast_campaigns_created_at", "broadcast_campaigns", ["created_at"]
    )
    op.create_index(
        "ix_broadcast_campaigns_tenant_status",
        "broadcast_campaigns",
        ["tenant_id", "status"],
    )
    op.create_index(
        "ix_broadcast_campaigns_scheduled_at",
        "broadcast_campaigns",
        ["scheduled_at"],
    )

    for table in _NEW_TABLES:
        _apply_rls(table)


def downgrade() -> None:
    for table in _NEW_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {_SYSTEM_POLICY} ON {table};")
        op.execute(f"DROP POLICY IF EXISTS {_ISOLATION_POLICY} ON {table};")

    op.drop_index("ix_broadcast_campaigns_scheduled_at", table_name="broadcast_campaigns")
    op.drop_index("ix_broadcast_campaigns_tenant_status", table_name="broadcast_campaigns")
    op.drop_index("ix_broadcast_campaigns_created_at", table_name="broadcast_campaigns")
    op.drop_index("ix_broadcast_campaigns_tenant_id", table_name="broadcast_campaigns")
    op.drop_table("broadcast_campaigns")

    op.drop_index("ix_support_tickets_created_by", table_name="support_tickets")
    op.drop_index("ix_support_tickets_tenant_status", table_name="support_tickets")
    op.drop_index("ix_support_tickets_created_at", table_name="support_tickets")
    op.drop_index("ix_support_tickets_tenant_id", table_name="support_tickets")
    op.drop_table("support_tickets")

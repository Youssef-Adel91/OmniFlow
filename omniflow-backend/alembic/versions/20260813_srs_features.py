"""vip_subscribers + broadcast_deliveries + tenant settings columns

Revision ID: 20260813_srs_features
Revises: 9e62a0b74221
Create Date: 2026-08-13

This migration resolves the final schema gaps flagged in the SRS review report:

1. vip_subscribers
   - Stores customer VIP opt-in status and rate-limit counters.
   - The broadcast worker already references this table via raw SQL; its
     absence was preventing the worker from starting without error.
   - Includes the standard per-tenant RLS policies (isolation + system bypass).

2. broadcast_deliveries
   - Per-recipient delivery record for each BroadcastCampaign send.
   - Tracks PENDING → SENT → DELIVERED / READ / FAILED lifecycle.
   - Also referenced by the existing broadcast_worker.

3. tenants.ai_system_prompt (TEXT, nullable)
   - Stores the tenant's custom AI Personality override.
   - Read by the LLM orchestrator to set the system-prompt prefix.

4. tenants.logo_url (VARCHAR 1000, nullable)
   - Stores the S3/MinIO public URL of the tenant's company logo.
   - Written by the new POST /api/v1/settings/logo endpoint.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers
revision: str = "20260813_srs_features"
# NOTE: this originally pointed at "9e62a0b74221", which put it on a parallel
# branch with 0007_support_broadcast (also a child of 9e62a0b74221) and left the
# project with two Alembic heads — `alembic upgrade head` would have failed with
# "Multiple head revisions are present". Re-parented onto 0007 to linearise the
# history. Safe because this revision had not been applied anywhere yet.
down_revision: Union[str, None] = "0007_support_broadcast"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_TABLES = ["vip_subscribers", "broadcast_deliveries"]
_GUC = "app.current_tenant_id"
_ISOLATION_POLICY = "tenant_isolation_policy"
_SYSTEM_POLICY = "system_bypass_policy"
_SYSTEM_TENANT_ID = "00000000-0000-0000-0000-000000000000"


def _apply_rls(table: str) -> None:
    """Apply the standard two-policy RLS treatment to a table."""
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
    # ── 1. vip_subscribers ───────────────────────────────────────────────────
    op.create_table(
        "vip_subscribers",
        sa.Column(
            "subscriber_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Primary key",
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="RLS partition key — must match app.current_tenant_id",
        ),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="active",
            comment="active | opted_out | declined | pending",
        ),
        sa.Column(
            "opted_in_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp when the customer confirmed VIP opt-in",
        ),
        sa.Column(
            "opted_out_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp when the customer opted out",
        ),
        sa.Column(
            "messages_sent_this_week",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Rolling count — reset by the weekly job",
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
            ["customer_id"], ["customers.customer_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("subscriber_id"),
        sa.UniqueConstraint(
            "tenant_id", "customer_id",
            name="uq_vip_subscribers_tenant_customer",
        ),
    )
    op.create_index("ix_vip_subscribers_tenant_id",    "vip_subscribers", ["tenant_id"])
    op.create_index("ix_vip_subscribers_created_at",   "vip_subscribers", ["created_at"])
    op.create_index("ix_vip_subscribers_customer",     "vip_subscribers", ["customer_id"])
    op.create_index(
        "ix_vip_subscribers_tenant_status",
        "vip_subscribers",
        ["tenant_id", "status"],
    )

    # ── 2. broadcast_deliveries ──────────────────────────────────────────────
    op.create_table(
        "broadcast_deliveries",
        sa.Column(
            "delivery_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Primary key",
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="RLS partition key — must match app.current_tenant_id",
        ),
        sa.Column(
            "campaign_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "delivery_status",
            sa.String(length=20),
            nullable=False,
            server_default="PENDING",
            comment="PENDING | SENT | DELIVERED | READ | FAILED",
        ),
        sa.Column(
            "platform_message_id",
            sa.String(length=200),
            nullable=True,
            comment="Platform-native message ID returned by Meta API",
        ),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the message was dispatched to the channel API",
        ),
        sa.Column(
            "delivered_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When Meta confirmed delivery",
        ),
        sa.Column(
            "error_detail",
            sa.Text(),
            nullable=True,
            comment="Error message if delivery_status = FAILED",
        ),
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Number of send attempts made",
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
            ["campaign_id"],
            ["broadcast_campaigns.campaign_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.customer_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("delivery_id"),
        sa.UniqueConstraint(
            "campaign_id", "customer_id",
            name="uq_broadcast_deliveries_campaign_customer",
        ),
    )
    op.create_index("ix_broadcast_deliveries_tenant_id",  "broadcast_deliveries", ["tenant_id"])
    op.create_index("ix_broadcast_deliveries_created_at", "broadcast_deliveries", ["created_at"])
    op.create_index("ix_broadcast_deliveries_campaign",   "broadcast_deliveries", ["campaign_id"])
    op.create_index("ix_broadcast_deliveries_customer",   "broadcast_deliveries", ["customer_id"])
    op.create_index(
        "ix_broadcast_deliveries_tenant_status",
        "broadcast_deliveries",
        ["tenant_id", "delivery_status"],
    )

    # Apply standard RLS to both new tables
    for table in _NEW_TABLES:
        _apply_rls(table)

    # ── 3 & 4. Add ai_system_prompt + logo_url to tenants ───────────────────
    op.add_column(
        "tenants",
        sa.Column(
            "ai_system_prompt",
            sa.Text(),
            nullable=True,
            comment="Custom AI personality / system prompt override for this tenant",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "logo_url",
            sa.String(length=1000),
            nullable=True,
            comment="Public URL of the tenant's company logo (stored in S3/MinIO)",
        ),
    )


def downgrade() -> None:
    # Remove tenant columns
    op.drop_column("tenants", "logo_url")
    op.drop_column("tenants", "ai_system_prompt")

    # Drop broadcast_deliveries
    for table in reversed(_NEW_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {_SYSTEM_POLICY} ON {table};")
        op.execute(f"DROP POLICY IF EXISTS {_ISOLATION_POLICY} ON {table};")

    op.drop_index("ix_broadcast_deliveries_tenant_status", table_name="broadcast_deliveries")
    op.drop_index("ix_broadcast_deliveries_customer",      table_name="broadcast_deliveries")
    op.drop_index("ix_broadcast_deliveries_campaign",      table_name="broadcast_deliveries")
    op.drop_index("ix_broadcast_deliveries_created_at",    table_name="broadcast_deliveries")
    op.drop_index("ix_broadcast_deliveries_tenant_id",     table_name="broadcast_deliveries")
    op.drop_table("broadcast_deliveries")

    # Drop vip_subscribers
    op.drop_index("ix_vip_subscribers_tenant_status", table_name="vip_subscribers")
    op.drop_index("ix_vip_subscribers_customer",      table_name="vip_subscribers")
    op.drop_index("ix_vip_subscribers_created_at",    table_name="vip_subscribers")
    op.drop_index("ix_vip_subscribers_tenant_id",     table_name="vip_subscribers")
    op.drop_table("vip_subscribers")

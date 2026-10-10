"""Broadcasts: template params/language, send rate, failure reason, delivery read time, webhook lookup index.

Revision ID: 0023_broadcast_upgrade
Revises: 0022_customer_identity
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0023_broadcast_upgrade"
down_revision = "0022_customer_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("broadcast_campaigns", sa.Column("template_language", sa.String(10), nullable=False, server_default="ar"))
    op.add_column("broadcast_campaigns", sa.Column("template_params", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("broadcast_campaigns", sa.Column("send_rate_per_second", sa.Integer, nullable=False, server_default="20"))
    op.add_column("broadcast_campaigns", sa.Column("last_error", sa.Text))
    op.add_column("broadcast_campaigns", sa.Column("started_at", sa.DateTime(timezone=True)))
    op.add_column("broadcast_campaigns", sa.Column("created_by", postgresql.UUID(as_uuid=True)))
    op.add_column("broadcast_deliveries", sa.Column("read_at", sa.DateTime(timezone=True)))
    op.create_index("ix_broadcast_deliveries_wamid", "broadcast_deliveries", ["platform_message_id"])


def downgrade() -> None:
    op.drop_index("ix_broadcast_deliveries_wamid", table_name="broadcast_deliveries")
    op.drop_column("broadcast_deliveries", "read_at")
    for col in ("created_by", "started_at", "last_error", "send_rate_per_second", "template_params", "template_language"):
        op.drop_column("broadcast_campaigns", col)

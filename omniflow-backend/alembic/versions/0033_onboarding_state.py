"""Onboarding checklist state (night 2 #3): small per-tenant JSON (bot_tested_at, dismissed_at).

Revision ID: 0033_onboarding_state
Revises: 0032_data_deletion_requests
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0033_onboarding_state"
down_revision = "0032_data_deletion_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("onboarding_state", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))


def downgrade() -> None:
    op.drop_column("tenants", "onboarding_state")

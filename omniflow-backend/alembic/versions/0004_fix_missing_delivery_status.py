"""fix_missing_delivery_status

Revision ID: 0004_fix_missing_delivery_status
Revises: 0003_add_tenant_onboarding
Create Date: 2026-06-20

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0004_fix_missing_delivery_status'
down_revision = '0003_add_tenant_onboarding'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add delivery_status to messages table which was missing in initial schema
    op.add_column(
        'messages',
        sa.Column('delivery_status', sa.String(length=30), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('messages', 'delivery_status')

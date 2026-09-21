"""add_tenant_onboarding

Revision ID: 0003_add_tenant_onboarding
Revises: 0002_rls_policies
Create Date: 2026-06-20

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0003_add_tenant_onboarding'
down_revision = '0002_rls_policies'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add onboarding_status with a default value of 'pending_selection'
    op.add_column(
        'tenants',
        sa.Column('onboarding_status', sa.String(length=30), server_default='pending_selection', nullable=False)
    )
    # Add meta_access_token
    op.add_column(
        'tenants',
        sa.Column('meta_access_token', sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('tenants', 'meta_access_token')
    op.drop_column('tenants', 'onboarding_status')

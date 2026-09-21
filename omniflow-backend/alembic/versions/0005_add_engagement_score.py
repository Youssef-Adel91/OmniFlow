"""add_engagement_score_to_customers

Sprint 13 — AI Lead Scoring (Hot Leads) feature.
Adds `engagement_score` (INTEGER, nullable, default 0) to the `customers`
table and creates an index for O(log n) ORDER BY performance.

Revision ID: 0005_add_engagement_score
Revises: 0004_fix_missing_delivery_status
Create Date: 2026-06-20
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0005_add_engagement_score'
down_revision = '0004_fix_missing_delivery_status'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add engagement_score column — nullable so existing rows default gracefully
    op.add_column(
        'customers',
        sa.Column(
            'engagement_score',
            sa.Integer(),
            nullable=True,
            comment='AI-computed engagement score 0–100. Higher = hotter lead.',
        ),
    )
    # Backfill existing rows to 0
    op.execute("UPDATE customers SET engagement_score = 0 WHERE engagement_score IS NULL")

    # Index for fast ORDER BY on hot_leads sort
    op.create_index(
        'ix_customers_engagement_score',
        'customers',
        ['tenant_id', 'engagement_score'],
    )


def downgrade() -> None:
    op.drop_index('ix_customers_engagement_score', table_name='customers')
    op.drop_column('customers', 'engagement_score')

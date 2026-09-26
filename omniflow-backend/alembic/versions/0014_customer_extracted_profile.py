"""Add customers.extracted_profile / extracted_profile_updated_at.

Generalizes the real-estate-only, on-demand preference_extractor.py pattern
into a sector-agnostic signal store per customer, so lead_scoring.py's
explicit-profile-data component (25% weight) can read real per-tenant data
instead of falling back to just is_vip. See
shared/services/customer_profile_extractor.py.

Revision ID: 0014_cust_extracted_profile
Revises: 0013_cust_vcard_opened
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014_cust_extracted_profile"
down_revision = "0013_cust_vcard_opened"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "customers",
        sa.Column(
            "extracted_profile", postgresql.JSONB(astext_type=sa.Text()), nullable=True,
            comment='Generic LLM-extracted buying signals, e.g. {"budget_min":..,"budget_max":..,'
                    '"location":..,"stated_need":..,"urgency":bool}. Null fields mean not stated.',
        ),
    )
    op.add_column(
        "customers",
        sa.Column(
            "extracted_profile_updated_at", sa.DateTime(timezone=True), nullable=True,
            comment="When extracted_profile was last (re)computed",
        ),
    )


def downgrade() -> None:
    op.drop_column("customers", "extracted_profile_updated_at")
    op.drop_column("customers", "extracted_profile")

"""Add per-tenant Instagram/Messenger credentials.

Real gap found during a P0 channel audit: Instagram/Messenger real
delivery was entirely blocked because credentials were only ever a single
global settings field (META_INSTAGRAM_PAGE_ACCESS_TOKEN) -- meaning one
Meta Page token served every tenant in a multi-tenant product, and
_resolve_tenant_id(page_id) in channel_adapters/instagram/router.py had no
real per-tenant page_id -> tenant_id mapping to look up at all.

Revision ID: 0015_tenant_instagram_creds
Revises: 0014_cust_extracted_profile
"""
from alembic import op
import sqlalchemy as sa

revision = "0015_tenant_instagram_creds"
down_revision = "0014_cust_extracted_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "instagram_page_id", sa.String(30), nullable=True,
            comment="Meta Page ID connected for Instagram DM / Messenger delivery",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "instagram_page_access_token", sa.Text(), nullable=True,
            comment="Per-tenant Meta Page access token for Instagram DM / Messenger sends",
        ),
    )
    op.create_index(
        "ix_tenants_instagram_page_id", "tenants", ["instagram_page_id"], unique=True,
        postgresql_where=sa.text("instagram_page_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_tenants_instagram_page_id", table_name="tenants")
    op.drop_column("tenants", "instagram_page_access_token")
    op.drop_column("tenants", "instagram_page_id")

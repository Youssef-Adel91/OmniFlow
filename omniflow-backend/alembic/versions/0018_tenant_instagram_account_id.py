"""Add Tenant.instagram_account_id for Instagram webhook tenant routing.

Meta delivers Instagram events with `object == "instagram"` and
`entry[].id` set to the Instagram professional account's ID -- not the
Facebook Page ID that Tenant.instagram_page_id holds. Without this column
_resolve_tenant_id() had nothing to match that ID against, so Instagram
DMs/comments could never be routed to a tenant.

Populated by the "Connect with Facebook" flow (gateway/routers/
facebook_oauth.py), which already discovers the linked
`instagram_business_account` for each Page. Tenants connected before this
migration have NULL here until they run Connect again.

The unique partial index mirrors ix_tenants_instagram_page_id (0015): one
Instagram account must map to at most one tenant, otherwise a webhook
could be routed to the wrong customer's workspace.

Revision ID: 0018_tenant_ig_account_id
Revises: 0017_conv_ai_reply_epoch
"""
from alembic import op
import sqlalchemy as sa

revision = "0018_tenant_ig_account_id"
down_revision = "0017_conv_ai_reply_epoch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "instagram_account_id", sa.String(30), nullable=True,
            comment="Instagram professional account ID linked to instagram_page_id",
        ),
    )
    op.create_index(
        "ix_tenants_instagram_account_id", "tenants", ["instagram_account_id"], unique=True,
        postgresql_where=sa.text("instagram_account_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_tenants_instagram_account_id", table_name="tenants")
    op.drop_column("tenants", "instagram_account_id")

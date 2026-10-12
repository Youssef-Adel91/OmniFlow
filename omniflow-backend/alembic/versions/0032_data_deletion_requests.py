"""Meta data-deletion callback (night 2 #2): the requests we received + the Facebook user id that connected a Page.

`data_deletion_requests` is system-level (no tenant): the confirmation code is the only thing the public status page needs.
`tenants.facebook_user_id` lets a deletion request for that Facebook user find what we hold (Page tokens, Instagram ids) and remove it.

Revision ID: 0032_data_deletion_requests
Revises: 0031_einvoice
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0032_data_deletion_requests"
down_revision = "0031_einvoice"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("facebook_user_id", sa.String(64)))
    op.create_index("ix_tenants_facebook_user_id", "tenants", ["facebook_user_id"])
    op.create_table(
        "data_deletion_requests",
        sa.Column("request_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("confirmation_code", sa.String(32), nullable=False, unique=True),
        sa.Column("source", sa.String(12), nullable=False, server_default="meta"),
        sa.Column("fb_user_hash", sa.String(64), nullable=False),                    # SHA-256 of the Facebook user id (the id itself is not kept here)
        sa.Column("status", sa.String(12), nullable=False, server_default="received"),    # received | completed | no_data
        sa.Column("tenants_affected", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_data_deletion_requests_hash", "data_deletion_requests", ["fb_user_hash"])


def downgrade() -> None:
    op.drop_table("data_deletion_requests")
    op.drop_index("ix_tenants_facebook_user_id", table_name="tenants")
    op.drop_column("tenants", "facebook_user_id")

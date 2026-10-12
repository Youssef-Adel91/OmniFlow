"""Comment-reply preference (night 2 #6): tenant_preferences.social_comments (enabled + public reply | private DM).

Revision ID: 0035_social_comments_pref
Revises: 0034_developers
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0035_social_comments_pref"
down_revision = "0034_developers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant_preferences", sa.Column("social_comments", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))


def downgrade() -> None:
    op.drop_column("tenant_preferences", "social_comments")

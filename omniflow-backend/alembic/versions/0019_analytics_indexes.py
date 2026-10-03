"""Composite indexes for the analytics dashboard queries.

`GET /dashboard/analytics` filters by tenant + created_at on conversations and
customers, and joins messages by (conversation_id, sender_type, created_at)
to find each conversation's first customer/AI/human message. The single-column
created_at indexes cannot serve those predicates together.

Revision ID: 0019_analytics_indexes
Revises: 0018_tenant_ig_account_id
"""
from alembic import op

revision = "0019_analytics_indexes"
down_revision = "0018_tenant_ig_account_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_conversations_tenant_created", "conversations", ["tenant_id", "created_at"])
    op.create_index("ix_customers_tenant_created", "customers", ["tenant_id", "created_at"])
    op.create_index(
        "ix_messages_conv_sender_created", "messages", ["conversation_id", "sender_type", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_messages_conv_sender_created", table_name="messages")
    op.drop_index("ix_customers_tenant_created", table_name="customers")
    op.drop_index("ix_conversations_tenant_created", table_name="conversations")

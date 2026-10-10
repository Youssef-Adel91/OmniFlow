"""Support: ticket reference/category/priority, threaded messages, attachments (tenant RLS).

Revision ID: 0025_support_threads
Revises: 0024_tenant_preferences
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0025_support_threads"
down_revision = "0024_tenant_preferences"
branch_labels = None
depends_on = None

_GUC = "app.current_tenant_id"
_SYSTEM = "00000000-0000-0000-0000-000000000000"


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"""CREATE POLICY tenant_isolation_policy ON {table}
        USING (tenant_id = current_setting('{_GUC}', true)::uuid)
        WITH CHECK (tenant_id = current_setting('{_GUC}', true)::uuid);""")
    op.execute(f"""CREATE POLICY system_bypass_policy ON {table} AS PERMISSIVE FOR ALL
        USING (current_setting('{_GUC}', true)::uuid = '{_SYSTEM}'::uuid);""")


def upgrade() -> None:
    uuid_ = postgresql.UUID(as_uuid=True)
    op.add_column("support_tickets", sa.Column("reference", sa.String(20)))
    op.add_column("support_tickets", sa.Column("category", sa.String(30), nullable=False, server_default="other"))
    op.add_column("support_tickets", sa.Column("priority", sa.String(10), nullable=False, server_default="normal"))
    op.add_column("support_tickets", sa.Column("first_response_at", sa.DateTime(timezone=True)))
    op.add_column("support_tickets", sa.Column("closed_at", sa.DateTime(timezone=True)))
    # Back-fill a stable reference for existing tickets, then enforce uniqueness.
    op.execute("UPDATE support_tickets SET reference = 'OF-' || upper(substr(replace(ticket_id::text, '-', ''), 1, 8)) WHERE reference IS NULL")
    op.create_index("uq_support_tickets_reference", "support_tickets", ["reference"], unique=True)

    op.create_table(
        "support_ticket_messages",
        sa.Column("message_id", uuid_, primary_key=True),
        sa.Column("ticket_id", uuid_, sa.ForeignKey("support_tickets.ticket_id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", uuid_, sa.ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("author_type", sa.String(10), nullable=False),        # customer | support | system
        sa.Column("author_user_id", uuid_),
        sa.Column("kind", sa.String(10), nullable=False, server_default="message"),   # message | status
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_support_messages_ticket", "support_ticket_messages", ["ticket_id", "created_at"])
    op.create_index("ix_support_messages_tenant", "support_ticket_messages", ["tenant_id"])

    op.create_table(
        "support_attachments",
        sa.Column("attachment_id", uuid_, primary_key=True),
        sa.Column("ticket_id", uuid_, sa.ForeignKey("support_tickets.ticket_id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", uuid_, sa.ForeignKey("support_ticket_messages.message_id", ondelete="CASCADE")),
        sa.Column("tenant_id", uuid_, sa.ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_support_attachments_ticket", "support_attachments", ["ticket_id"])
    op.create_index("ix_support_attachments_tenant", "support_attachments", ["tenant_id"])

    # Every existing ticket becomes the first message of its thread.
    op.execute("""
        INSERT INTO support_ticket_messages (message_id, ticket_id, tenant_id, author_type, author_user_id, kind, body, created_at)
        SELECT gen_random_uuid(), ticket_id, tenant_id, 'customer', created_by_user_id, 'message', message, created_at FROM support_tickets
    """)
    _rls("support_ticket_messages")
    _rls("support_attachments")


def downgrade() -> None:
    op.drop_table("support_attachments")
    op.drop_table("support_ticket_messages")
    op.drop_index("uq_support_tickets_reference", table_name="support_tickets")
    for col in ("closed_at", "first_response_at", "priority", "category", "reference"):
        op.drop_column("support_tickets", col)

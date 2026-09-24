"""Add conversation_notes and appointments tables (inbox quick actions, item 11).

Follows the RLS convention from 0002_rls_policies.py / 0006_system_bypass_policy.py:
each new tenant-scoped table gets both `tenant_isolation_policy` (normal tenant
sessions) and `system_bypass_policy` (the nil-UUID sentinel used by
get_system_session()) — per that migration's own maintenance contract, a new
table missing the bypass policy would silently return zero rows to any
system-session query.

Revision ID: 0011_notes_appointments
Revises: 0010_tenant_wa_phone
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011_notes_appointments"
down_revision = "0010_tenant_wa_phone"
branch_labels = None
depends_on = None

_TABLES = ["conversation_notes", "appointments"]
_GUC = "app.current_tenant_id"
_SYSTEM_TENANT_ID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    op.create_table(
        "conversation_notes",
        sa.Column("note_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                   sa.ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True),
                   sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_user_id", postgresql.UUID(as_uuid=True),
                   sa.ForeignKey("tenant_users.user_id", ondelete="SET NULL"), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False, server_default="info"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_conversation_notes_tenant_id", "conversation_notes", ["tenant_id"])
    op.create_index("ix_conversation_notes_conversation", "conversation_notes", ["conversation_id", "created_at"])

    op.create_table(
        "appointments",
        sa.Column("appointment_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                   sa.ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True),
                   sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True),
                   sa.ForeignKey("customers.customer_id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True),
                   sa.ForeignKey("tenant_users.user_id", ondelete="SET NULL"), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("location_note", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="scheduled"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_appointments_tenant_id", "appointments", ["tenant_id"])
    op.create_index("ix_appointments_conversation", "appointments", ["conversation_id", "scheduled_at"])
    op.create_index("ix_appointments_tenant_status", "appointments", ["tenant_id", "status"])

    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_policy ON {table}
                USING (tenant_id = current_setting('{_GUC}', true)::uuid)
                WITH CHECK (tenant_id = current_setting('{_GUC}', true)::uuid);
            """
        )
        op.execute(
            f"""
            CREATE POLICY system_bypass_policy ON {table}
                AS PERMISSIVE
                FOR ALL
                USING (
                    current_setting('{_GUC}', true)::uuid
                        = '{_SYSTEM_TENANT_ID}'::uuid
                );
            """
        )


def downgrade() -> None:
    op.drop_table("appointments")
    op.drop_table("conversation_notes")

"""tenant_preferences: business hours, human handoff, AI behaviour, identity (tenant RLS).

Revision ID: 0024_tenant_preferences
Revises: 0023_broadcast_upgrade
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024_tenant_preferences"
down_revision = "0023_broadcast_upgrade"
branch_labels = None
depends_on = None

_GUC = "app.current_tenant_id"
_SYSTEM = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    uuid_ = postgresql.UUID(as_uuid=True)
    op.create_table(
        "tenant_preferences",
        sa.Column("tenant_id", uuid_, sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("business_hours", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("handoff", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("ai_behavior", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("identity", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_by", uuid_),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute("ALTER TABLE tenant_preferences ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_preferences FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation_policy ON tenant_preferences
            USING (tenant_id = current_setting('{_GUC}', true)::uuid)
            WITH CHECK (tenant_id = current_setting('{_GUC}', true)::uuid);
    """)
    op.execute(f"""
        CREATE POLICY system_bypass_policy ON tenant_preferences AS PERMISSIVE FOR ALL
            USING (current_setting('{_GUC}', true)::uuid = '{_SYSTEM}'::uuid);
    """)


def downgrade() -> None:
    op.drop_table("tenant_preferences")

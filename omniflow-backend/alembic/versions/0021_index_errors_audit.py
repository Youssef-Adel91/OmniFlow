"""Index error tracking on import jobs + audit_logs (tenant RLS).

Revision ID: 0021_index_errors_audit
Revises: 0020_import_jobs
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021_index_errors_audit"
down_revision = "0020_import_jobs"
branch_labels = None
depends_on = None

_GUC = "app.current_tenant_id"
_SYSTEM_TENANT_ID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    uuid_ = postgresql.UUID(as_uuid=True)
    op.add_column("import_jobs", sa.Column("index_failed", sa.Integer, nullable=False, server_default="0"))
    op.add_column("import_jobs", sa.Column("index_error", sa.Text))

    op.create_table(
        "audit_logs",
        sa.Column("audit_id", uuid_, primary_key=True),
        sa.Column("tenant_id", uuid_, sa.ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_user_id", uuid_),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("entity_type", sa.String(40)),
        sa.Column("entity_id", sa.String(80)),
        sa.Column("details", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_logs_tenant_created", "audit_logs", ["tenant_id", "created_at"])
    op.create_index("ix_audit_logs_tenant_action", "audit_logs", ["tenant_id", "action"])

    op.execute("ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation_policy ON audit_logs
            USING (tenant_id = current_setting('{_GUC}', true)::uuid)
            WITH CHECK (tenant_id = current_setting('{_GUC}', true)::uuid);
        """
    )
    op.execute(
        f"""
        CREATE POLICY system_bypass_policy ON audit_logs
            AS PERMISSIVE
            FOR ALL
            USING (current_setting('{_GUC}', true)::uuid = '{_SYSTEM_TENANT_ID}'::uuid);
        """
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_column("import_jobs", "index_error")
    op.drop_column("import_jobs", "index_failed")

"""Bulk import: import_jobs + import_mapping_templates (tenant RLS like every tenant table).

Revision ID: 0020_import_jobs
Revises: 0019_analytics_indexes
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0020_import_jobs"
down_revision = "0019_analytics_indexes"
branch_labels = None
depends_on = None

_TABLES = ["import_jobs", "import_mapping_templates"]
_GUC = "app.current_tenant_id"
_SYSTEM_TENANT_ID = "00000000-0000-0000-0000-000000000000"


def _ts():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    uuid_ = postgresql.UUID(as_uuid=True)
    op.create_table(
        "import_jobs",
        sa.Column("import_id", uuid_, primary_key=True),
        sa.Column("tenant_id", uuid_, sa.ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("file_kind", sa.String(10), nullable=False),
        sa.Column("file_size", sa.Integer, nullable=False, server_default="0"),
        sa.Column("source_key", sa.String(500)),
        sa.Column("rows_key", sa.String(500)),
        sa.Column("errors_key", sa.String(500)),
        sa.Column("columns", postgresql.JSONB),
        sa.Column("mapping", postgresql.JSONB),
        sa.Column("options", postgresql.JSONB),
        sa.Column("summary", postgresql.JSONB),
        sa.Column("total_rows", sa.Integer, nullable=False, server_default="0"),
        sa.Column("processed_rows", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("indexed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text),
        sa.Column("cancel_requested", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", uuid_),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        *_ts(),
    )
    op.create_index("ix_import_jobs_tenant_id", "import_jobs", ["tenant_id"])
    op.create_index("ix_import_jobs_tenant_created", "import_jobs", ["tenant_id", "created_at"])
    op.create_index("ix_import_jobs_status_lease", "import_jobs", ["status", "lease_expires_at"])

    op.create_table(
        "import_mapping_templates",
        sa.Column("template_id", uuid_, primary_key=True),
        sa.Column("tenant_id", uuid_, sa.ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("mapping", postgresql.JSONB, nullable=False),
        *_ts(),
        sa.UniqueConstraint("tenant_id", "kind", "name", name="uq_import_template_name"),
    )
    op.create_index("ix_import_mapping_templates_tenant_id", "import_mapping_templates", ["tenant_id"])

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
                    current_setting('{_GUC}', true)::uuid = '{_SYSTEM_TENANT_ID}'::uuid
                );
            """
        )


def downgrade() -> None:
    op.drop_table("import_mapping_templates")
    op.drop_table("import_jobs")

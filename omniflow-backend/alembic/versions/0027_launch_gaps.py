"""Launch gaps: team invitations, in-app notifications + per-user preferences, customer erasure marker (tenant RLS).

Revision ID: 0027_launch_gaps
Revises: 0026_billing
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0027_launch_gaps"
down_revision = "0026_billing"
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
    now = sa.func.now()
    tenant_fk = lambda: sa.ForeignKey("tenants.tenant_id", ondelete="RESTRICT")  # noqa: E731

    op.create_table(
        "team_invitations",
        sa.Column("invitation_id", uuid_, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", uuid_, tenant_fk(), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),                 # stored lower-cased
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("invited_by_user_id", uuid_),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),   # pending|accepted|revoked|expired
        sa.Column("clerk_invitation_id", sa.String(100)),
        sa.Column("delivery", sa.String(20)),                                # clerk | email | none
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("uq_team_invitations_pending_email", "team_invitations", ["tenant_id", "email"], unique=True,
                    postgresql_where=sa.text("status = 'pending'"))
    op.create_index("ix_team_invitations_email_pending", "team_invitations", ["email"], postgresql_where=sa.text("status = 'pending'"))

    op.create_table(
        "notifications",
        sa.Column("notification_id", uuid_, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", uuid_, tenant_fk(), nullable=False),
        sa.Column("user_id", uuid_, sa.ForeignKey("tenant_users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text),
        sa.Column("link", sa.String(300)),
        sa.Column("dedupe_key", sa.String(120)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_notifications_user_unread", "notifications", ["user_id", "read_at", "created_at"])
    op.create_index("uq_notifications_dedupe", "notifications", ["user_id", "kind", "dedupe_key"], unique=True,
                    postgresql_where=sa.text("dedupe_key IS NOT NULL"))

    op.create_table(
        "notification_prefs",
        sa.Column("tenant_id", uuid_, tenant_fk(), nullable=False),
        sa.Column("user_id", uuid_, sa.ForeignKey("tenant_users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("in_app", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("email", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.PrimaryKeyConstraint("user_id", "kind"),
    )

    op.add_column("customers", sa.Column("erased_at", sa.DateTime(timezone=True)))

    for t in ("team_invitations", "notifications", "notification_prefs"):
        _rls(t)


def downgrade() -> None:
    op.drop_column("customers", "erased_at")
    for t in ("notification_prefs", "notifications", "team_invitations"):
        op.drop_table(t)

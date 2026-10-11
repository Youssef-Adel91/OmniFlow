"""Developers v1 (night 2 #5): read-only API keys (stored hashed), their usage log, outgoing signed webhooks + delivery log.

Revision ID: 0034_developers
Revises: 0033_onboarding_state
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0034_developers"
down_revision = "0033_onboarding_state"
branch_labels = None
depends_on = None

_GUC = "app.current_tenant_id"
_SYSTEM = "00000000-0000-0000-0000-000000000000"
_TABLES = ("api_keys", "api_usage_log", "webhook_endpoints", "webhook_deliveries")


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"""CREATE POLICY tenant_isolation_policy ON {table}
        USING (tenant_id = current_setting('{_GUC}', true)::uuid) WITH CHECK (tenant_id = current_setting('{_GUC}', true)::uuid);""")
    op.execute(f"""CREATE POLICY system_bypass_policy ON {table} AS PERMISSIVE FOR ALL
        USING (current_setting('{_GUC}', true)::uuid = '{_SYSTEM}'::uuid);""")


def upgrade() -> None:
    uuid_ = postgresql.UUID(as_uuid=True)
    now = sa.text("now()")
    tenant_fk = lambda: sa.Column("tenant_id", uuid_, sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False)  # noqa: E731

    op.create_table(
        "api_keys",
        sa.Column("key_id", uuid_, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        tenant_fk(),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),                      # first characters of the key, so the owner can recognise it; never enough to use it
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),       # SHA-256 of the full key (the key itself is shown once and never stored)
        sa.Column("created_by", uuid_),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_api_keys_tenant", "api_keys", ["tenant_id", "created_at"])

    op.create_table(
        "api_usage_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        tenant_fk(),
        sa.Column("key_id", uuid_, sa.ForeignKey("api_keys.key_id", ondelete="CASCADE"), nullable=False),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("path", sa.String(200), nullable=False),
        sa.Column("status", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
    )
    op.create_index("ix_api_usage_tenant_time", "api_usage_log", ["tenant_id", "created_at"])
    op.create_index("ix_api_usage_key_time", "api_usage_log", ["key_id", "created_at"])

    op.create_table(
        "webhook_endpoints",
        sa.Column("endpoint_id", uuid_, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        tenant_fk(),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("description", sa.String(120)),
        sa.Column("secret_enc", sa.Text, nullable=False),                         # Fernet-encrypted signing secret (needed server-side to sign; shown to the owner once)
        sa.Column("events", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("consecutive_failures", sa.Integer, nullable=False, server_default="0"),
        sa.Column("disabled_reason", sa.String(120)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
    )
    op.create_index("ix_webhook_endpoints_tenant", "webhook_endpoints", ["tenant_id"])

    op.create_table(
        "webhook_deliveries",
        sa.Column("delivery_id", uuid_, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        tenant_fk(),
        sa.Column("endpoint_id", uuid_, sa.ForeignKey("webhook_endpoints.endpoint_id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", uuid_, nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),   # pending | delivered | failed
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_status_code", sa.Integer),
        sa.Column("last_error", sa.String(300)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_webhook_deliveries_due", "webhook_deliveries", ["status", "next_attempt_at"])
    op.create_index("ix_webhook_deliveries_endpoint", "webhook_deliveries", ["endpoint_id", "created_at"])

    for t in _TABLES:
        _rls(t)


def downgrade() -> None:
    for t in reversed(_TABLES):
        op.drop_table(t)

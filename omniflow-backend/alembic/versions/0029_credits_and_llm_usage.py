"""Reply top-up packs, hard-cap support and real AI cost tracking (P10-B/C).

* topup_packs        global catalogue (5,000 = 189 / 20,000 = 699 / 50,000 = 1,499 SAR, VAT-exclusive, valid 12 months)
* reply_credits      per-tenant purchased balance (tenant RLS); consumed FIFO by expiry, after the plan's monthly quota
* llm_usage          one row per billable AI reply: model, tier, tokens, computed cost (tenant RLS, idempotent on event_id)
* llm_usage_daily    nightly roll-up per tenant/day/tier/model (platform margin view reads this + today's raw rows)
* subscriptions      quota_exhausted_action (handoff | stop), auto top-up switch (OFF by default) + chosen pack
* transactions.meta  what a non-plan transaction (add-on / top-up) is for

Revision ID: 0029_credits_and_llm_usage
Revises: 0028_pricing_catalog
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0029_credits_and_llm_usage"
down_revision = "0028_pricing_catalog"
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

    op.add_column("transactions", sa.Column("meta", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("subscriptions", sa.Column("quota_exhausted_action", sa.String(10), nullable=False, server_default="handoff"))
    op.add_column("subscriptions", sa.Column("auto_topup_enabled", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("subscriptions", sa.Column("auto_topup_pack", sa.String(30)))
    op.create_check_constraint("ck_subscriptions_quota_action", "subscriptions", "quota_exhausted_action IN ('handoff','stop')")

    op.create_table(
        "topup_packs",
        sa.Column("code", sa.String(30), primary_key=True),
        sa.Column("replies", sa.Integer, nullable=False),
        sa.Column("price_halalas", sa.Integer, nullable=False),               # VAT-exclusive
        sa.Column("validity_months", sa.Integer, nullable=False, server_default="12"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
    )
    op.execute("INSERT INTO topup_packs (code, replies, price_halalas, validity_months, sort_order) VALUES "
               "('replies_5k', 5000, 18900, 12, 1), ('replies_20k', 20000, 69900, 12, 2), ('replies_50k', 50000, 149900, 12, 3)")

    op.create_table(
        "reply_credits",
        sa.Column("credit_id", uuid_, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", uuid_, tenant_fk(), nullable=False),
        sa.Column("pack_code", sa.String(30), nullable=False),
        sa.Column("replies_total", sa.Integer, nullable=False),
        sa.Column("replies_remaining", sa.Integer, nullable=False),
        sa.Column("source", sa.String(12), nullable=False, server_default="purchase"),     # purchase | manual
        sa.Column("transaction_id", uuid_, sa.ForeignKey("transactions.transaction_id", ondelete="RESTRICT"), unique=True),
        sa.Column("purchased_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("replies_remaining >= 0 AND replies_remaining <= replies_total", name="ck_reply_credits_balance"),
    )
    op.create_index("ix_reply_credits_tenant_expiry", "reply_credits", ["tenant_id", "expires_at"])

    op.create_table(
        "llm_usage",
        sa.Column("usage_id", uuid_, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", uuid_, tenant_fk(), nullable=False),
        sa.Column("event_id", uuid_, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("tier", sa.String(30), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_sar", sa.Numeric(14, 6), nullable=False),
        sa.Column("cost_source", sa.String(10), nullable=False),                           # measured | default
        sa.Column("quota_source", sa.String(8), nullable=False, server_default="plan"),   # plan | topup | exempt
        sa.UniqueConstraint("tenant_id", "event_id", name="uq_llm_usage_event"),
    )
    op.create_index("ix_llm_usage_tenant_created", "llm_usage", ["tenant_id", "created_at"])

    op.create_table(
        "llm_usage_daily",
        sa.Column("tenant_id", uuid_, tenant_fk(), primary_key=True),
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("tier", sa.String(30), primary_key=True),
        sa.Column("model", sa.String(80), primary_key=True),
        sa.Column("replies", sa.Integer, nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("cost_sar", sa.Numeric(14, 6), nullable=False, server_default="0"),
    )
    for t in ("reply_credits", "llm_usage", "llm_usage_daily"):
        _rls(t)


def downgrade() -> None:
    for t in ("llm_usage_daily", "llm_usage", "reply_credits", "topup_packs"):
        op.drop_table(t)
    op.drop_constraint("ck_subscriptions_quota_action", "subscriptions")
    op.drop_column("subscriptions", "auto_topup_pack")
    op.drop_column("subscriptions", "auto_topup_enabled")
    op.drop_column("subscriptions", "quota_exhausted_action")
    op.drop_column("transactions", "meta")

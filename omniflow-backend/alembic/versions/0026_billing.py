"""Billing: plans, subscriptions, transactions, invoices, payment events, usage counters (tenant RLS).

Existing tenants are grandfathered: each gets a subscription row flagged `is_exempt` (never expires, never gated)
so nothing breaks when the gate goes live. Tenants created later get a real trial lazily (see services/billing).
Plan prices/limits are PLACEHOLDERS (is_placeholder = true) until the owner sets the real ones.

Revision ID: 0026_billing
Revises: 0025_support_threads
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0026_billing"
down_revision = "0025_support_threads"
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
        "plans",
        sa.Column("plan_id", uuid_, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.String(30), nullable=False, unique=True),          # = tenants.subscription_tier values
        sa.Column("name_ar", sa.String(100), nullable=False),
        sa.Column("description_ar", sa.Text),
        sa.Column("price_monthly_halalas", sa.Integer, nullable=False),         # VAT-exclusive
        sa.Column("price_yearly_halalas", sa.Integer, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="SAR"),
        sa.Column("limits", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("is_placeholder", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
    )
    # PLACEHOLDER catalogue — prices (halalas, VAT-exclusive) and limits are NOT decided; replace via SQL/seed.
    op.execute(sa.text(r"""
        INSERT INTO plans (code, name_ar, description_ar, price_monthly_halalas, price_yearly_halalas, limits, sort_order) VALUES
        ('economic',     'الاقتصادية',  'خطة تجريبية — السعر والحدود غير نهائية', 9900,  99000,  '{"seats"\:3,"messages_per_month"\:1000,"channels"\:1,"properties"\:100}', 1),
        ('professional', 'الاحترافية',  'خطة تجريبية — السعر والحدود غير نهائية', 29900, 299000, '{"seats"\:10,"messages_per_month"\:5000,"channels"\:3,"properties"\:1000}', 2),
        ('enterprise',   'المؤسسات',    'خطة تجريبية — السعر والحدود غير نهائية', 79900, 799000, '{"seats"\:50,"messages_per_month"\:20000,"channels"\:5,"properties"\:10000}', 3)
    """))

    op.create_table(
        "subscriptions",
        sa.Column("subscription_id", uuid_, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", uuid_, tenant_fk(), nullable=False, unique=True),
        sa.Column("plan_code", sa.String(30), nullable=False, server_default="economic"),
        sa.Column("billing_interval", sa.String(10), nullable=False, server_default="monthly"),
        sa.Column("status", sa.String(20), nullable=False, server_default="trial"),
        sa.Column("is_exempt", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True)),
        sa.Column("current_period_start", sa.DateTime(timezone=True)),
        sa.Column("current_period_end", sa.DateTime(timezone=True)),
        sa.Column("cancel_at_period_end", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("grace_ends_at", sa.DateTime(timezone=True)),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("last_payment_error", sa.Text),
        sa.Column("pending_plan_code", sa.String(30)),
        sa.Column("pending_interval", sa.String(10)),
        sa.Column("provider", sa.String(20)),
        sa.Column("payment_token", sa.Text),                       # Fernet-encrypted provider token; never a card number
        sa.Column("card_brand", sa.String(20)),
        sa.Column("card_last4", sa.String(4)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
    )
    op.create_index("ix_subscriptions_status", "subscriptions", ["status"])
    op.create_index("ix_subscriptions_period_end", "subscriptions", ["current_period_end"])

    op.create_table(
        "transactions",
        sa.Column("transaction_id", uuid_, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", uuid_, tenant_fk(), nullable=False),
        sa.Column("subscription_id", uuid_, sa.ForeignKey("subscriptions.subscription_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),            # initial | renewal | upgrade | manual
        sa.Column("plan_code", sa.String(30), nullable=False),
        sa.Column("billing_interval", sa.String(10), nullable=False),
        sa.Column("subtotal_halalas", sa.Integer, nullable=False),
        sa.Column("vat_halalas", sa.Integer, nullable=False),
        sa.Column("amount_halalas", sa.Integer, nullable=False),     # subtotal + VAT = what the card is charged
        sa.Column("currency", sa.String(3), nullable=False, server_default="SAR"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),   # pending|paid|failed|cancelled
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("provider_payment_id", sa.String(100)),
        sa.Column("provider_invoice_id", sa.String(100)),
        sa.Column("checkout_url", sa.Text),
        sa.Column("failure_reason", sa.Text),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="1"),
        sa.Column("period_start", sa.DateTime(timezone=True)),
        sa.Column("period_end", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
    )
    op.create_index("ix_transactions_tenant_created", "transactions", ["tenant_id", "created_at"])
    op.create_index("uq_transactions_provider_payment", "transactions", ["provider", "provider_payment_id"], unique=True,
                    postgresql_where=sa.text("provider_payment_id IS NOT NULL"))

    op.execute("CREATE SEQUENCE IF NOT EXISTS invoice_number_seq START 1")
    op.create_table(
        "invoices",
        sa.Column("invoice_id", uuid_, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", uuid_, tenant_fk(), nullable=False),
        sa.Column("transaction_id", uuid_, sa.ForeignKey("transactions.transaction_id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("invoice_number", sa.String(30), nullable=False, unique=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="issued"),
        sa.Column("issued_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("subtotal_halalas", sa.Integer, nullable=False),
        sa.Column("vat_halalas", sa.Integer, nullable=False),
        sa.Column("total_halalas", sa.Integer, nullable=False),
        sa.Column("vat_rate_bp", sa.Integer, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="SAR"),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True)),
        sa.Column("period_end", sa.DateTime(timezone=True)),
        sa.Column("seller", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),   # snapshot at issue time
        sa.Column("buyer", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_invoices_tenant_issued", "invoices", ["tenant_id", "issued_at"])

    op.create_table(
        "payment_events",
        sa.Column("event_pk", uuid_, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("event_key", sa.String(120), nullable=False),
        sa.Column("tenant_id", uuid_, tenant_fk()),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("outcome", sa.String(30), nullable=False),         # applied | ignored | rejected
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),   # secrets stripped
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.UniqueConstraint("provider", "event_key", name="uq_payment_events_provider_key"),
    )

    op.create_table(
        "usage_counters",
        sa.Column("tenant_id", uuid_, tenant_fk(), primary_key=True),
        sa.Column("period", sa.String(7), primary_key=True),         # YYYY-MM (Asia/Riyadh)
        sa.Column("ai_messages", sa.Integer, nullable=False, server_default="0"),
        sa.Column("alerted_80_at", sa.DateTime(timezone=True)),
        sa.Column("alerted_100_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
    )

    # Grandfather every existing tenant: exempt (no expiry, never gated), status mirrored from tenants.status.
    op.execute("""
        INSERT INTO subscriptions (tenant_id, plan_code, status, is_exempt, provider)
        SELECT tenant_id, CASE WHEN subscription_tier IN ('economic','professional','enterprise') THEN subscription_tier ELSE 'economic' END,
               COALESCE(status, 'trial'), true, 'manual'
        FROM tenants
    """)

    for t in ("subscriptions", "transactions", "invoices", "payment_events", "usage_counters"):
        _rls(t)


def downgrade() -> None:
    for t in ("usage_counters", "payment_events", "invoices", "transactions", "subscriptions", "plans"):
        op.drop_table(t)
    op.execute("DROP SEQUENCE IF EXISTS invoice_number_seq")

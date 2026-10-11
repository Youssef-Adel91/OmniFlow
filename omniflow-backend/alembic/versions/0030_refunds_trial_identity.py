"""Refunds + credit notes (P10-E) and trial anti-abuse identities (P10-D).

* refunds       one row per refund attempt of a paid transaction (tenant RLS): amounts, what was deducted, provider result
* credit_notes  the fiscal document issued for a completed refund (numbering CN-YYYY-NNNNNN, VAT split) — the ZATCA 381 source
* trial_identities  hashed company identifiers (CR number, phone) that already used a free trial; unique per (kind, hash)

Revision ID: 0030_refunds_trial_identity
Revises: 0029_credits_and_llm_usage
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0030_refunds_trial_identity"
down_revision = "0029_credits_and_llm_usage"
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

    op.execute("CREATE SEQUENCE IF NOT EXISTS credit_note_number_seq START 1")
    op.create_table(
        "refunds",
        sa.Column("refund_id", uuid_, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", uuid_, tenant_fk(), nullable=False),
        sa.Column("transaction_id", uuid_, sa.ForeignKey("transactions.transaction_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("invoice_id", uuid_, sa.ForeignKey("invoices.invoice_id", ondelete="RESTRICT")),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),        # pending | done | failed
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("gross_halalas", sa.Integer, nullable=False),                               # what the customer paid (VAT incl.)
        sa.Column("consumed_halalas", sa.Integer, nullable=False, server_default="0"),       # measured AI usage deducted (VAT excl.)
        sa.Column("fee_halalas", sa.Integer, nullable=False, server_default="0"),            # gateway fee deducted (VAT excl.)
        sa.Column("subtotal_halalas", sa.Integer, nullable=False),                            # refunded, VAT excl.
        sa.Column("vat_halalas", sa.Integer, nullable=False),
        sa.Column("amount_halalas", sa.Integer, nullable=False),                              # refunded, VAT incl.
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("provider_refund_id", sa.String(100)),
        sa.Column("failure_reason", sa.Text),
        sa.Column("requested_by", sa.String(254), nullable=False),                           # staff e-mail
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("amount_halalas >= 0", name="ck_refunds_amount"),
    )
    op.create_index("uq_refunds_one_done_per_txn", "refunds", ["transaction_id"], unique=True, postgresql_where=sa.text("status = 'done'"))
    op.create_table(
        "credit_notes",
        sa.Column("credit_note_id", uuid_, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", uuid_, tenant_fk(), nullable=False),
        sa.Column("refund_id", uuid_, sa.ForeignKey("refunds.refund_id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("original_invoice_id", uuid_, sa.ForeignKey("invoices.invoice_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("credit_note_number", sa.String(30), nullable=False, unique=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("subtotal_halalas", sa.Integer, nullable=False),
        sa.Column("vat_halalas", sa.Integer, nullable=False),
        sa.Column("total_halalas", sa.Integer, nullable=False),
        sa.Column("vat_rate_bp", sa.Integer, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="SAR"),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("seller", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("buyer", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_table(
        "trial_identities",
        sa.Column("identity_id", uuid_, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", uuid_, tenant_fk(), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),                                     # cr | phone
        sa.Column("value_hash", sa.String(64), nullable=False),                               # SHA-256 of the normalised value
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.UniqueConstraint("kind", "value_hash", name="uq_trial_identity"),
    )
    for t in ("refunds", "credit_notes", "trial_identities"):
        _rls(t)


def downgrade() -> None:
    for t in ("trial_identities", "credit_notes", "refunds"):
        op.drop_table(t)
    op.execute("DROP SEQUENCE IF EXISTS credit_note_number_seq")

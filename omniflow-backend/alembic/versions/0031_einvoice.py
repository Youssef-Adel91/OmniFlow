"""ZATCA e-invoicing storage (P10-G): the issuing unit (EGS) with its atomic ICV counter + hash chain head, the documents, and the buyer's tax identity.

* einvoice_units      one row per EGS (the platform's issuing device/solution). ICV counter and last hash are updated in the same transaction
                      that creates a document, so the chain can never have gaps or duplicates. Keys / CSID secrets are stored Fernet-encrypted.
* einvoice_documents  each signed document (invoice 388 / credit note 381 / debit note 383) with its XML, QR, hash, ICV/PIH and submission state.
* tenants             vat_number + national address of the buyer (a B2B invoice needs them; without a VAT number a simplified B2C invoice is issued).

Revision ID: 0031_einvoice
Revises: 0030_refunds_trial_identity
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0031_einvoice"
down_revision = "0030_refunds_trial_identity"
branch_labels = None
depends_on = None

_GUC = "app.current_tenant_id"
_SYSTEM = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    uuid_ = postgresql.UUID(as_uuid=True)
    now = sa.func.now()
    op.add_column("tenants", sa.Column("vat_number", sa.String(15)))
    op.add_column("tenants", sa.Column("billing_address", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("tenants", sa.Column("cr_number", sa.String(20)))

    op.create_table(
        "einvoice_units",
        sa.Column("unit_id", uuid_, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("env", sa.String(12), nullable=False, server_default="sandbox"),
        sa.Column("status", sa.String(12), nullable=False, server_default="new"),            # new | compliance | production | revoked
        sa.Column("vat_number", sa.String(15), nullable=False),
        sa.Column("icv_counter", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("last_hash", sa.Text, nullable=False),
        sa.Column("private_key_enc", sa.Text),                  # Fernet(PEM)
        sa.Column("certificate_b64", sa.Text),                  # the CSID certificate (public)
        sa.Column("secret_enc", sa.Text),                       # Fernet(CSID API secret)
        sa.Column("compliance_request_id", sa.String(64)),
        sa.Column("csr_pem", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.UniqueConstraint("env", "name", name="uq_einvoice_unit_name"),
    )
    op.create_table(
        "einvoice_documents",
        sa.Column("doc_id", uuid_, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", uuid_, sa.ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("unit_id", uuid_, sa.ForeignKey("einvoice_units.unit_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("invoice_id", uuid_, sa.ForeignKey("invoices.invoice_id", ondelete="RESTRICT"), unique=True),
        sa.Column("credit_note_id", uuid_, sa.ForeignKey("credit_notes.credit_note_id", ondelete="RESTRICT"), unique=True),
        sa.Column("doc_number", sa.String(40), nullable=False),
        sa.Column("type_code", sa.String(3), nullable=False),
        sa.Column("subtype", sa.String(7), nullable=False),
        sa.Column("uuid", postgresql.UUID(as_uuid=False), nullable=False, unique=True),
        sa.Column("icv", sa.BigInteger, nullable=False),
        sa.Column("pih", sa.Text, nullable=False),
        sa.Column("invoice_hash", sa.Text, nullable=False),
        sa.Column("xml", sa.Text, nullable=False),
        sa.Column("qr", sa.Text, nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),         # pending | cleared | reported | rejected | dead
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text),
        sa.Column("zatca_response", postgresql.JSONB),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.UniqueConstraint("unit_id", "icv", name="uq_einvoice_unit_icv"),
        sa.CheckConstraint("(invoice_id IS NOT NULL) <> (credit_note_id IS NOT NULL)", name="ck_einvoice_one_source"),
    )
    op.create_index("ix_einvoice_documents_status", "einvoice_documents", ["status", "next_attempt_at"])
    op.execute("ALTER TABLE einvoice_documents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE einvoice_documents FORCE ROW LEVEL SECURITY")
    op.execute(f"""CREATE POLICY tenant_isolation_policy ON einvoice_documents
        USING (tenant_id = current_setting('{_GUC}', true)::uuid) WITH CHECK (tenant_id = current_setting('{_GUC}', true)::uuid);""")
    op.execute(f"""CREATE POLICY system_bypass_policy ON einvoice_documents AS PERMISSIVE FOR ALL
        USING (current_setting('{_GUC}', true)::uuid = '{_SYSTEM}'::uuid);""")
    # The unit holds secrets and has no tenant: only the system context may touch it.
    op.execute("ALTER TABLE einvoice_units ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE einvoice_units FORCE ROW LEVEL SECURITY")
    op.execute(f"""CREATE POLICY system_only_policy ON einvoice_units AS PERMISSIVE FOR ALL
        USING (current_setting('{_GUC}', true)::uuid = '{_SYSTEM}'::uuid) WITH CHECK (current_setting('{_GUC}', true)::uuid = '{_SYSTEM}'::uuid);""")


def downgrade() -> None:
    op.drop_table("einvoice_documents")
    op.drop_table("einvoice_units")
    op.drop_column("tenants", "cr_number")
    op.drop_column("tenants", "billing_address")
    op.drop_column("tenants", "vat_number")

"""Customers: separate real phone from channel ids, CRM fields, PDPL marketing consent, archive.

Revision ID: 0022_customer_identity
Revises: 0021_index_errors_audit

`unified_phone` stays the contact key used by the messaging pipeline (WhatsApp phone, or the
platform user id for Instagram/Messenger) so nothing existing breaks; the new `phone_e164` holds
the real phone number only, and `channel_user_ids` maps channel -> platform user id.
Marketing consent defaults to FALSE for every existing customer (PDPL: no consent, no broadcast).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022_customer_identity"
down_revision = "0021_index_errors_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("source", sa.String(20), nullable=False, server_default="whatsapp"))
    op.add_column("customers", sa.Column("channel_user_ids", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("customers", sa.Column("phone_e164", sa.String(20)))
    op.add_column("customers", sa.Column("email", sa.String(254)))
    op.add_column("customers", sa.Column("city", sa.String(100)))
    op.add_column("customers", sa.Column("tags", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("customers", sa.Column("notes", sa.Text))
    op.add_column("customers", sa.Column("marketing_opt_in", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("customers", sa.Column("opt_in_source", sa.String(120)))
    op.add_column("customers", sa.Column("opt_in_at", sa.DateTime(timezone=True)))
    op.add_column("customers", sa.Column("opt_out_at", sa.DateTime(timezone=True)))
    op.add_column("customers", sa.Column("archived_at", sa.DateTime(timezone=True)))

    # Backfill: phone-shaped keys are real phones; everything else is a platform user id.
    op.execute("UPDATE customers SET phone_e164 = unified_phone WHERE unified_phone ~ '^\\+[1-9][0-9]{7,14}$'")
    op.execute(
        """
        UPDATE customers c
           SET source = x.channel,
               channel_user_ids = jsonb_build_object(x.channel, c.unified_phone)
          FROM (SELECT DISTINCT ON (customer_id) customer_id, channel::text AS channel
                  FROM conversations ORDER BY customer_id, created_at) x
         WHERE x.customer_id = c.customer_id AND c.phone_e164 IS NULL
        """
    )
    op.create_index("uq_customers_tenant_phone_e164", "customers", ["tenant_id", "phone_e164"], unique=True,
                    postgresql_where=sa.text("phone_e164 IS NOT NULL"))
    op.create_index("ix_customers_tenant_source", "customers", ["tenant_id", "source"])
    op.create_index("ix_customers_tenant_optin", "customers", ["tenant_id", "marketing_opt_in"])
    op.create_index("ix_customers_tenant_archived", "customers", ["tenant_id", "archived_at"])


def downgrade() -> None:
    for name in ("ix_customers_tenant_archived", "ix_customers_tenant_optin", "ix_customers_tenant_source",
                 "uq_customers_tenant_phone_e164"):
        op.drop_index(name, table_name="customers")
    for col in ("archived_at", "opt_out_at", "opt_in_at", "opt_in_source", "marketing_opt_in", "notes", "tags", "city",
                "email", "phone_e164", "channel_user_ids", "source"):
        op.drop_column("customers", col)

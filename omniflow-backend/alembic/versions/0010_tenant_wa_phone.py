"""Add the tenant's own WhatsApp Business display phone number.

Needed to send a real VCard contact card (item 7): the vCard must carry the
tenant's own dialable WhatsApp number, not the opaque Meta `phone_number_id`,
so a customer's phone can actually save it as a contact. Nullable and
additive — existing tenants simply won't have a vCard phone field until
onboarding captures it (a follow-up, not blocking this migration).

Revision ID: 0010_tenant_wa_phone
Revises: 0009_message_rls
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_tenant_wa_phone"
down_revision = "0009_message_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "whatsapp_display_phone_number",
            sa.String(length=30),
            nullable=True,
            comment="Tenant's own dialable WhatsApp number (E.164), shown to customers via the VCard gate — distinct from the opaque Meta whatsapp_phone_number_id",
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "whatsapp_display_phone_number")

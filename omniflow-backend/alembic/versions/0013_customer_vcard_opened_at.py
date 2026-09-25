"""Add customers.vcard_opened_at — real signal for lead scoring.

Part of the customer purchase-likelihood scoring system: "was a VCard ever
opened" is one of the required interaction-history signals. WhatsApp has no
API event for "contact saved," but Meta's real per-message read-receipt
status webhook (wired in migration 0012) tells us when the customer's
WhatsApp client actually displayed the VCard message — the closest real,
honestly-obtainable proxy. Stamped once, the first time it happens (see
persistence.update_message_delivery_status_by_wamid).

Revision ID: 0013_cust_vcard_opened
Revises: 0012_msg_failure_reason
"""
from alembic import op
import sqlalchemy as sa

revision = "0013_cust_vcard_opened"
down_revision = "0012_msg_failure_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "customers",
        sa.Column(
            "vcard_opened_at", sa.DateTime(timezone=True), nullable=True,
            comment="Set the first time a vcard-type outbound message's delivery_status reaches READ",
        ),
    )


def downgrade() -> None:
    op.drop_column("customers", "vcard_opened_at")

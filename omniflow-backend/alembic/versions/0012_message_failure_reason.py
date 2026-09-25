"""Add messages.failure_reason (real Meta status-webhook handling was missing entirely).

A real live test caught this: Meta actually POSTs delivery-status webhooks
(sent/delivered/read/failed with an error code+title on failure) to
/api/v1/webhooks/whatsapp, but src/channel_adapters/whatsapp/router.py's
_process_whatsapp_value() only ever read value["messages"] — value["statuses"]
was silently dropped. A failed outbound send (e.g. WhatsApp's 24h
customer-service-window rule, error 131047) was accepted by the Graph API
(200, a real wamid) but its failure was never reflected anywhere: the
message row just sat at whatever delivery_status persist_outbound_message
set initially, forever.

`messages` has no tenant_id column (RLS is enforced via the parent
Conversation row, per that model's own docstring) — no new RLS policy is
needed here.

Revision ID: 0012_msg_failure_reason
Revises: 0011_notes_appointments
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_msg_failure_reason"
down_revision = "0011_notes_appointments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "failure_reason", sa.Text(), nullable=True,
            comment="Meta status-webhook error (e.g. '131047: Re-engagement message'), set only when delivery_status='FAILED'",
        ),
    )


def downgrade() -> None:
    op.drop_column("messages", "failure_reason")

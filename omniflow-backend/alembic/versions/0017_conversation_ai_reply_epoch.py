"""Add Conversation.ai_reply_epoch -- closes one specific hole in the
AI-reply-after-takeover mitigation (LAUNCH_READINESS_PROMPT item 13's
residual risk, tracked in IMPLEMENTATION_STATUS.md).

Before this: outbound_dispatcher's only defense was re-checking
`Conversation.status != AI_ACTIVE` with a plain, fresh SELECT immediately
before dispatch. That check is blind to one real scenario: a human takes
over, reads (and possibly replies to) the customer, then returns the
conversation to AI (repository.update_status sets status back to
AI_ACTIVE) -- all while an earlier AI reply is still generating. By the
time that AI reply reaches the pre-send check, `status` reads AI_ACTIVE
again, so the old check waves it through, even though a human already
saw and acted on this conversation with context the in-flight reply never
had.

The fix: `ai_reply_epoch` is bumped atomically by every takeover
(repository.assign_agent) and, unlike `status`, is never reset by
return_to_ai -- it only ever goes up. An AI reply captures the current
value before it starts calling the LLM; the dispatcher's pre-send check
becomes an atomic `UPDATE ... WHERE ai_reply_epoch = :captured`, which
fails (0 rows) if a takeover happened at any point since capture, whether
or not the conversation has since been handed back to AI.

Explicitly NOT claimed: this does not close the much smaller gap between
the pre-send check itself and the actual outbound network call -- no
DB-only mechanism can make an HTTP request to WhatsApp/Instagram
conditional on a database row in one atomic step, and neither provider
supports recalling a message already sent. That sliver is inherent to
any design built on these providers, not specific to this implementation.

Revision ID: 0017_conv_ai_reply_epoch
Revises: 0016_tenant_custom_ai_instr
"""
from alembic import op
import sqlalchemy as sa

revision = "0017_conv_ai_reply_epoch"
down_revision = "0016_tenant_custom_ai_instr"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "ai_reply_epoch", sa.Integer(), nullable=False, server_default="0",
            comment=(
                "Fencing token, bumped atomically on every human takeover, "
                "never reset by return_to_ai. See migration docstring / "
                "IMPLEMENTATION_STATUS.md."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("conversations", "ai_reply_epoch")

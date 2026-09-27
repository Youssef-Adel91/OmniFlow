"""Add Tenant.custom_ai_instructions -- a distinct AI-behavior lever.

Real gap found while wiring onboarding's "how should the AI behave with
customers" ask (tone, objection handling, closing a sale, sector-specific
guidance): the only per-tenant behavior lever was ai_system_prompt, and the
first pass at this feature merged new instructions into that same text
column via a marker-string search/replace. On self-review that was judged a
shortcut -- fragile (an admin manually removing the marker heading breaks
future updates) and conceptually wrong (identity/tone and specific
behavioral instructions are different concerns forced into one field).

This gives custom behavior instructions their own real column, exactly like
every other CompanyProfile concept already gets its own field. Composition
into the final LLM prompt happens in company_context.py's
get_tenant_persona(), which now selects both columns and combines them.

Revision ID: 0016_tenant_custom_ai_instr
Revises: 0015_tenant_instagram_creds
"""
from alembic import op
import sqlalchemy as sa

revision = "0016_tenant_custom_ai_instr"
down_revision = "0015_tenant_instagram_creds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "custom_ai_instructions", sa.Text(), nullable=True,
            comment=(
                "How the AI should BEHAVE with customers: tone, objection "
                "handling, closing a sale, sector-specific guidance. "
                "Distinct from ai_system_prompt (base identity/persona) -- "
                "composed together in company_context.get_tenant_persona()."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "custom_ai_instructions")

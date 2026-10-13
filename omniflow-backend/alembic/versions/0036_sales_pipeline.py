"""Sales closing (night 3): deals pipeline, follow-up tasks, qualification on customers, richer appointments, tenant `sales` preferences.

Revision ID: 0036_sales_pipeline
Revises: 0035_social_comments_pref
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0036_sales_pipeline"
down_revision = "0035_social_comments_pref"
branch_labels = None
depends_on = None

_GUC = "app.current_tenant_id"
_SYSTEM = "00000000-0000-0000-0000-000000000000"
_TABLES = ("deals", "followup_tasks")


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"""CREATE POLICY tenant_isolation_policy ON {table}
        USING (tenant_id = current_setting('{_GUC}', true)::uuid) WITH CHECK (tenant_id = current_setting('{_GUC}', true)::uuid);""")
    op.execute(f"""CREATE POLICY system_bypass_policy ON {table} AS PERMISSIVE FOR ALL
        USING (current_setting('{_GUC}', true)::uuid = '{_SYSTEM}'::uuid);""")


def upgrade() -> None:
    uuid_ = postgresql.UUID(as_uuid=True)
    now = sa.text("now()")

    op.add_column("customers", sa.Column("qualification", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("tenant_preferences", sa.Column("sales", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("appointments", sa.Column("listing_id", uuid_, sa.ForeignKey("property_listings.listing_id", ondelete="SET NULL")))
    op.add_column("appointments", sa.Column("staff_user_id", uuid_, sa.ForeignKey("tenant_users.user_id", ondelete="SET NULL")))
    op.add_column("appointments", sa.Column("reminder_sent_at", sa.DateTime(timezone=True)))
    op.add_column("appointments", sa.Column("source", sa.String(10), nullable=False, server_default="staff"))        # staff | bot

    op.create_table(
        "deals",
        sa.Column("deal_id", uuid_, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", uuid_, sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", uuid_, sa.ForeignKey("customers.customer_id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", uuid_, sa.ForeignKey("conversations.conversation_id", ondelete="SET NULL")),
        sa.Column("listing_id", uuid_, sa.ForeignKey("property_listings.listing_id", ondelete="SET NULL")),
        sa.Column("stage", sa.String(12), nullable=False, server_default="new"),             # new | qualified | viewing | negotiation | won | lost
        sa.Column("reached_stage", sa.String(12), nullable=False, server_default="new"),     # highest OPEN stage ever reached (stage history for the conversion report)
        sa.Column("lead_label", sa.String(5)),                                               # hot | warm | cold
        sa.Column("score", sa.Integer, nullable=False, server_default="0"),
        sa.Column("assigned_user_id", uuid_, sa.ForeignKey("tenant_users.user_id", ondelete="SET NULL")),
        sa.Column("value_sar", sa.Numeric(14, 2)),
        sa.Column("offer_sar", sa.Numeric(14, 2)),                                           # a price the customer proposed (the assistant never negotiates; a person decides)
        sa.Column("lost_reason", sa.String(20)),                                             # price | no_response | competitor | financing | not_interested | other
        sa.Column("lost_note", sa.String(300)),
        sa.Column("source_channel", sa.String(20)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("stage_changed_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("stage IN ('new','qualified','viewing','negotiation','won','lost')", name="ck_deals_stage"),
    )
    op.create_index("ix_deals_tenant_stage", "deals", ["tenant_id", "stage", "updated_at"])
    op.create_index("ix_deals_customer", "deals", ["customer_id"])
    op.execute("CREATE UNIQUE INDEX uq_deals_one_open_per_customer ON deals (customer_id) WHERE stage NOT IN ('won','lost')")

    op.create_table(
        "followup_tasks",
        sa.Column("task_id", uuid_, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", uuid_, sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", uuid_, sa.ForeignKey("customers.customer_id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", uuid_, sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("step", sa.SmallInteger, nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),         # pending | sent | cancelled | skipped | failed
        sa.Column("reason", sa.String(60)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("conversation_id", "step", name="uq_followup_conversation_step"),
    )
    op.create_index("ix_followup_due", "followup_tasks", ["status", "due_at"])
    op.create_index("ix_followup_customer", "followup_tasks", ["customer_id", "status"])

    for t in _TABLES:
        _rls(t)


def downgrade() -> None:
    for t in reversed(_TABLES):
        op.drop_table(t)
    op.drop_column("appointments", "source")
    op.drop_column("appointments", "reminder_sent_at")
    op.drop_column("appointments", "staff_user_id")
    op.drop_column("appointments", "listing_id")
    op.drop_column("tenant_preferences", "sales")
    op.drop_column("customers", "qualification")

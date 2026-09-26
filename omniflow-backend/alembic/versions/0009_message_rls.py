"""Enforce message isolation through the owning conversation.

Revision ID: 0009_message_rls
Revises: 0008_knowledge_base
"""
from alembic import op

revision = "0009_message_rls"
down_revision = "0008_knowledge_base"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE messages FORCE ROW LEVEL SECURITY")
    # The subquery is itself protected by conversations' tenant policy.
    # This covers direct Message SELECT/UPDATE calls as well as repository joins.
    op.execute("""
        CREATE POLICY message_conversation_isolation ON messages
        USING (EXISTS (
            SELECT 1 FROM conversations
            WHERE conversations.conversation_id = messages.conversation_id
        ))
        WITH CHECK (EXISTS (
            SELECT 1 FROM conversations
            WHERE conversations.conversation_id = messages.conversation_id
        ))
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS message_conversation_isolation ON messages")
    op.execute("ALTER TABLE messages NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE messages DISABLE ROW LEVEL SECURITY")

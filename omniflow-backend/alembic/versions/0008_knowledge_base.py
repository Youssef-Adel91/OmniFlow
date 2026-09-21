"""company_profiles + knowledge_documents + broadcast_campaigns.recipients_count

Revision ID: 0008_knowledge_base
Revises: 20260813_srs_features
Create Date: 2026-08-13

Implements the Knowledge Base described in SRS §4.2.

1. company_profiles
   - Strict 1:1 with `tenants` (tenant_id is both PK and FK, ON DELETE CASCADE).
   - Structured "everything about my company" fields the tenant fills once in
     the dashboard. A summary of this row is injected into the AI system prompt
     on every single reply (see src/ai_engine/company_context.py).

2. knowledge_documents
   - Metadata + ingestion state for uploaded files (PDF/DOCX/TXT/CSV).
   - The file bytes live in S3/MinIO (`s3_key`); the embedded chunks live in
     the tenant's Qdrant documents collection, tagged with `document_id` so a
     delete can purge them precisely.

3. broadcast_campaigns.recipients_count
   - Nullable INTEGER, populated at campaign-creation time from the audience
     estimate so the dashboard can show "will reach N customers" without a
     second round-trip.

Both new tables get the standard RLS treatment: ENABLE + FORCE ROW LEVEL
SECURITY, the per-tenant isolation policy from 0002, and the system-session
bypass policy from 0006.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0008_knowledge_base"
down_revision: Union[str, None] = "20260813_srs_features"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_TABLES = ["company_profiles", "knowledge_documents"]
_GUC = "app.current_tenant_id"
_ISOLATION_POLICY = "tenant_isolation_policy"
_SYSTEM_POLICY = "system_bypass_policy"
_SYSTEM_TENANT_ID = "00000000-0000-0000-0000-000000000000"


def _apply_rls(table: str) -> None:
    """Identical two-policy RLS treatment used by migrations 0006/0007."""
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS {_ISOLATION_POLICY} ON {table};")
    op.execute(
        f"""
        CREATE POLICY {_ISOLATION_POLICY} ON {table}
            USING (tenant_id = current_setting('{_GUC}', true)::uuid);
        """
    )
    op.execute(f"DROP POLICY IF EXISTS {_SYSTEM_POLICY} ON {table};")
    op.execute(
        f"""
        CREATE POLICY {_SYSTEM_POLICY} ON {table}
            AS PERMISSIVE
            FOR ALL
            USING (
                current_setting('{_GUC}', true)::uuid = '{_SYSTEM_TENANT_ID}'::uuid
            );
        """
    )


def upgrade() -> None:
    # ── company_profiles ─────────────────────────────────────────────────────
    op.create_table(
        "company_profiles",
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="PK + FK — RLS partition key; exactly one profile per tenant",
        ),
        sa.Column("business_description", sa.Text(), nullable=True),
        sa.Column(
            "services_offered",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment='Array of service labels, e.g. ["بيع", "إيجار"]',
        ),
        sa.Column(
            "target_areas",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Array of cities/districts the agency operates in",
        ),
        sa.Column("pricing_policy", sa.Text(), nullable=True),
        sa.Column("working_hours", sa.Text(), nullable=True),
        sa.Column("contact_phone", sa.String(length=50), nullable=True),
        sa.Column("contact_email", sa.String(length=255), nullable=True),
        sa.Column("contact_address", sa.String(length=500), nullable=True),
        sa.Column(
            "social_links",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment='Object, e.g. {"instagram": "...", "website": "..."}',
        ),
        sa.Column("unique_selling_points", sa.Text(), nullable=True),
        sa.Column("policies_text", sa.Text(), nullable=True),
        sa.Column(
            "faq",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment='Array of {"question": str, "answer": str}',
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("tenant_id"),
    )

    # ── knowledge_documents ──────────────────────────────────────────────────
    op.create_table(
        "knowledge_documents",
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="RLS partition key — must match app.current_tenant_id",
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("original_filename", sa.String(length=500), nullable=False),
        sa.Column(
            "file_type",
            sa.String(length=10),
            nullable=False,
            comment="pdf | docx | txt | csv",
        ),
        sa.Column(
            "s3_key",
            sa.String(length=1000),
            nullable=False,
            comment="Object key inside the knowledge bucket",
        ),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="uploaded",
            nullable=False,
            comment="uploaded | processing | indexed | failed",
        ),
        sa.Column("chunk_count", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "uploaded_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Uploader; NULL if the user was later removed",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_user_id"], ["tenant_users.user_id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("document_id"),
    )
    op.create_index(
        "ix_knowledge_documents_tenant_id", "knowledge_documents", ["tenant_id"]
    )
    op.create_index(
        "ix_knowledge_documents_created_at", "knowledge_documents", ["created_at"]
    )
    op.create_index(
        "ix_knowledge_documents_tenant_status",
        "knowledge_documents",
        ["tenant_id", "status"],
    )

    # ── RLS on both new tables ───────────────────────────────────────────────
    for table in _NEW_TABLES:
        _apply_rls(table)

    # ── broadcast_campaigns.recipients_count ─────────────────────────────────
    op.add_column(
        "broadcast_campaigns",
        sa.Column(
            "recipients_count",
            sa.Integer(),
            nullable=True,
            comment="Audience size estimated when the campaign was created",
        ),
    )


def downgrade() -> None:
    op.drop_column("broadcast_campaigns", "recipients_count")

    for table in _NEW_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {_SYSTEM_POLICY} ON {table};")
        op.execute(f"DROP POLICY IF EXISTS {_ISOLATION_POLICY} ON {table};")

    op.drop_index("ix_knowledge_documents_tenant_status", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_created_at", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_tenant_id", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")
    op.drop_table("company_profiles")

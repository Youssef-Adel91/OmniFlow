"""initial_schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-05-22

Initial database schema for OmniFlow AI.
Creates all 7 core tables with indexes and constraints.

Run via:
    alembic upgrade head
    (after Docker Compose services are running)

Note: This migration was created manually because --autogenerate requires
a live PostgreSQL connection. The schema exactly mirrors the SQLAlchemy models
defined in src/shared/db/models.py.
"""
from __future__ import annotations

import uuid
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# ─────────────────────────────────────────────────────────────────────────────
revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None
# ─────────────────────────────────────────────────────────────────────────────


def upgrade() -> None:
    # ── 1. tenants ────────────────────────────────────────────────────────────
    op.create_table(
        "tenants",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("business_name", sa.String(255), nullable=False),
        sa.Column("fal_license_number", sa.String(50), nullable=False, unique=True),
        sa.Column("subscription_tier", sa.String(30), nullable=False, server_default="economic"),
        sa.Column("status", sa.String(30), nullable=False, server_default="trial"),
        sa.Column("whatsapp_phone_number_id", sa.String(30), nullable=True),
        sa.Column("whatsapp_waba_id", sa.String(30), nullable=True),
        sa.Column("max_ai_conversations", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tenants_fal_license_number", "tenants", ["fal_license_number"])
    op.create_index("ix_tenants_created_at", "tenants", ["created_at"])

    # ── 2. tenant_users ───────────────────────────────────────────────────────
    op.create_table(
        "tenant_users",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="agent"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "email", name="uq_tenant_users_tenant_email"),
    )
    op.create_index("ix_tenant_users_tenant_id", "tenant_users", ["tenant_id"])
    op.create_index("ix_tenant_users_tenant_role", "tenant_users", ["tenant_id", "role"])
    op.create_index("ix_tenant_users_created_at", "tenant_users", ["created_at"])

    # ── 3. customers ──────────────────────────────────────────────────────────
    op.create_table(
        "customers",
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("unified_phone", sa.String(20), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=True),
        sa.Column("whatsapp_profile_name", sa.String(200), nullable=True),
        sa.Column("is_processing_restricted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_vip", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("vcard_state", sa.String(40), nullable=False, server_default="STATE_NEW"),
        sa.Column("vault_s3_prefix", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "unified_phone", name="uq_customers_tenant_phone"),
    )
    op.create_index("ix_customers_tenant_id", "customers", ["tenant_id"])
    op.create_index("ix_customers_is_processing_restricted", "customers", ["is_processing_restricted"])
    op.create_index("ix_customers_is_vip", "customers", ["is_vip"])
    op.create_index("ix_customers_vcard_state", "customers", ["tenant_id", "vcard_state"])
    op.create_index("ix_customers_created_at", "customers", ["created_at"])

    # ── 4. property_listings ──────────────────────────────────────────────────
    op.create_table(
        "property_listings",
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("rega_ad_number", sa.String(30), nullable=False),
        sa.Column("property_type", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="PENDING_VERIFICATION"),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("district", sa.String(100), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("price", sa.Numeric(14, 2), nullable=True),
        sa.Column("area_sqm", sa.Numeric(10, 2), nullable=True),
        sa.Column("bedrooms", sa.Integer(), nullable=True),
        sa.Column("bathrooms", sa.Integer(), nullable=True),
        sa.Column("description_ar", sa.Text(), nullable=True),
        sa.Column("description_en", sa.Text(), nullable=True),
        sa.Column("qdrant_point_id", sa.String(36), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "rega_ad_number", name="uq_listings_tenant_rega"),
    )
    op.create_index("ix_property_listings_tenant_id", "property_listings", ["tenant_id"])
    op.create_index("ix_property_listings_is_verified", "property_listings", ["is_verified"])
    op.create_index("ix_listings_tenant_status", "property_listings", ["tenant_id", "status"])
    op.create_index("ix_listings_tenant_type", "property_listings", ["tenant_id", "property_type"])
    op.create_index("ix_property_listings_created_at", "property_listings", ["created_at"])

    # ── 5. conversations ──────────────────────────────────────────────────────
    op.create_table(
        "conversations",
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("customers.customer_id", ondelete="CASCADE"), nullable=False),
        sa.Column("assigned_agent_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenant_users.user_id", ondelete="SET NULL"), nullable=True),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("platform_conversation_id", sa.String(200), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ai_active"),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_conversations_tenant_id", "conversations", ["tenant_id"])
    op.create_index("ix_conversations_customer", "conversations", ["customer_id"])
    op.create_index("ix_conversations_agent", "conversations", ["assigned_agent_id"])
    op.create_index("ix_conversations_status", "conversations", ["status"])
    op.create_index("ix_conversations_tenant_status", "conversations", ["tenant_id", "status"])
    op.create_index("ix_conversations_last_message_at", "conversations", ["last_message_at"])
    op.create_index("ix_conversations_platform_id", "conversations", ["platform_conversation_id"])
    op.create_index("ix_conversations_created_at", "conversations", ["created_at"])

    # ── 6. messages ───────────────────────────────────────────────────────────
    op.create_table(
        "messages",
        sa.Column("message_id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("sender_type", sa.String(20), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenant_users.user_id", ondelete="SET NULL"), nullable=True),
        sa.Column("message_type", sa.String(20), nullable=False, server_default="text"),
        sa.Column("text_content", sa.Text(), nullable=True),
        sa.Column("s3_media_url", sa.String(1000), nullable=True),
        sa.Column("platform_message_id", sa.String(200), nullable=True, unique=True),
        sa.Column("llm_routing_tier", sa.String(10), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index("ix_messages_conversation_created", "messages", ["conversation_id", "created_at"])
    op.create_index("ix_messages_created_at", "messages", ["created_at"])

    # ── 7. customer_reports ───────────────────────────────────────────────────
    op.create_table(
        "customer_reports",
        sa.Column("report_id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("customers.customer_id", ondelete="CASCADE"), nullable=False),
        sa.Column("report_type", sa.String(40), nullable=False),
        sa.Column("s3_url", sa.String(1000), nullable=True),
        sa.Column("price_sar", sa.Numeric(8, 2), nullable=True),
        sa.Column("payment_reference", sa.String(200), nullable=True),
        sa.Column("is_delivered", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_customer_reports_tenant_id", "customer_reports", ["tenant_id"])
    op.create_index("ix_reports_tenant_customer", "customer_reports", ["tenant_id", "customer_id"])
    op.create_index("ix_reports_tenant_type", "customer_reports", ["tenant_id", "report_type"])
    op.create_index("ix_customer_reports_created_at", "customer_reports", ["created_at"])


def downgrade() -> None:
    # Drop in reverse FK dependency order
    op.drop_table("customer_reports")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("property_listings")
    op.drop_table("customers")
    op.drop_table("tenant_users")
    op.drop_table("tenants")

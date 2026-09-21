"""
shared/db/repository.py — Generic Async Repository (Data Access Layer)

Implements the Repository Pattern on top of SQLAlchemy 2.0 AsyncSession.

Architecture:
  ┌──────────────────────────────────────────────────────────────┐
  │  FastAPI endpoint / Celery task / AI worker                  │
  │      │                                                        │
  │      ▼                                                        │
  │  get_tenant_session(tenant_id) ← injects SET LOCAL RLS GUC  │
  │      │                                                        │
  │      ▼                                                        │
  │  ConcreteRepository(session)                                  │
  │      │                                                        │
  │      ▼                                                        │
  │  BaseRepository[Model, CreateSchema, UpdateSchema]           │
  │      │                                                        │
  │      ▼                                                        │
  │  AsyncSession → PostgreSQL (RLS filters rows automatically)  │
  └──────────────────────────────────────────────────────────────┘

Key design rules:
  1. Repositories NEVER open their own sessions — they accept an injected
     AsyncSession so the caller controls transaction boundaries.
  2. All reads use session.scalars() with select() — SQLAlchemy 2.0 style.
  3. No raw SQL strings — only ORM constructs (except op.execute in migrations).
  4. UpdateSchema fields that are None are skipped (true PATCH semantics).
  5. Pagination is cursor-free for simplicity; swap for keyset pagination
     when message_count > 10M rows.

References: SRS §6, Sprint 2 specification.
"""
from __future__ import annotations

import uuid
from typing import Any, Generic, TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from src.shared.db.base import Base
from src.shared.db.models import (
    Conversation,
    Customer,
    Message,
    PropertyListing,
    Tenant,
    TenantUser,
)
from src.shared.schemas import (
    CustomerCreate,
    CustomerUpdate,
)


# ─────────────────────────────────────────────────────────────────────────────
# Generic type variables
# ─────────────────────────────────────────────────────────────────────────────
ModelT = TypeVar("ModelT", bound=Base)
CreateSchemaT = TypeVar("CreateSchemaT")
UpdateSchemaT = TypeVar("UpdateSchemaT")


# ══════════════════════════════════════════════════════════════════════════════
# BaseRepository — Generic Async CRUD
# ══════════════════════════════════════════════════════════════════════════════
class BaseRepository(Generic[ModelT, CreateSchemaT, UpdateSchemaT]):
    """
    Generic async repository providing standard CRUD operations.

    Type parameters:
        ModelT         — SQLAlchemy ORM model class
        CreateSchemaT  — Pydantic schema used for INSERT
        UpdateSchemaT  — Pydantic schema used for PATCH (all fields Optional)

    The injected `session` MUST already have the RLS GUC set via:
        SET LOCAL app.current_tenant_id = '<uuid>';
    This is guaranteed when using get_tenant_session() from session.py.
    """

    def __init__(self, model: type[ModelT], session: AsyncSession) -> None:
        self.model = model
        self.session = session

    # ── READ ──────────────────────────────────────────────────────────────────

    async def get(self, pk: uuid.UUID) -> ModelT | None:
        """
        Fetch a single row by primary key.

        Returns None if not found (RLS may also cause None for cross-tenant PKs
        — this is intentional: the caller cannot distinguish "not found" from
        "access denied", which is the desired security behaviour).
        """
        stmt = select(self.model).where(self._pk_column() == pk)
        result = await self.session.scalars(stmt)
        return result.first()

    async def get_or_404(self, pk: uuid.UUID) -> ModelT:
        """
        Fetch a single row by PK; raise ValueError if not found.
        FastAPI endpoint layer should catch ValueError and return HTTP 404.
        """
        obj = await self.get(pk)
        if obj is None:
            raise ValueError(
                f"{self.model.__tablename__} with id={pk!s} not found "
                "(or not visible to the current tenant)"
            )
        return obj

    async def get_multi(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        filters: dict[str, Any] | None = None,
    ) -> tuple[list[ModelT], int]:
        """
        Paginated list query with optional equality filters.

        Returns:
            (items, total_count) — total_count is computed in a separate
            COUNT(*) query so the caller can build pagination metadata.

        Args:
            offset  — number of rows to skip (page * limit)
            limit   — max rows to return (capped at 200 to prevent abuse)
            filters — {column_name: value} equality conditions
        """
        limit = min(limit, 200)
        stmt = select(self.model)
        count_stmt = select(func.count()).select_from(self.model)

        if filters:
            for col_name, value in filters.items():
                col = getattr(self.model, col_name, None)
                if col is None:
                    raise AttributeError(
                        f"Column '{col_name}' does not exist on "
                        f"{self.model.__tablename__}"
                    )
                stmt = stmt.where(col == value)
                count_stmt = count_stmt.where(col == value)

        total: int = (await self.session.scalar(count_stmt)) or 0
        stmt = stmt.offset(offset).limit(limit)
        result = await self.session.scalars(stmt)
        return list(result.all()), total

    async def exists(self, pk: uuid.UUID) -> bool:
        """Return True if a row with this PK is visible to the current tenant."""
        stmt = select(func.count()).select_from(self.model).where(
            self._pk_column() == pk
        )
        count = await self.session.scalar(stmt)
        return (count or 0) > 0

    # ── WRITE ─────────────────────────────────────────────────────────────────

    async def create(self, schema: CreateSchemaT) -> ModelT:
        """
        Insert a new row.

        Converts the Pydantic schema to a dict via model_dump(), instantiates
        the ORM model, adds it to the session, and flushes (no commit —
        the caller owns the transaction).
        """
        data: dict[str, Any] = schema.model_dump(exclude_unset=False)  # type: ignore[union-attr]
        obj = self.model(**data)
        self.session.add(obj)
        await self.session.flush()
        await self.session.refresh(obj)
        return obj

    async def update(self, pk: uuid.UUID, schema: UpdateSchemaT) -> ModelT:
        """
        Partial update (PATCH semantics).

        Only fields that are explicitly set in the schema (exclude_unset=True)
        are written to the DB. Fields left as None / default are untouched.
        Raises ValueError if the row does not exist / is not visible.
        """
        obj = await self.get_or_404(pk)
        update_data: dict[str, Any] = schema.model_dump(exclude_unset=True)  # type: ignore[union-attr]
        for field, value in update_data.items():
            setattr(obj, field, value)
        self.session.add(obj)
        await self.session.flush()
        await self.session.refresh(obj)
        return obj

    async def delete(self, pk: uuid.UUID) -> bool:
        """
        Hard-delete a row by primary key.

        Returns True if a row was deleted, False if not found / invisible.
        Prefer soft-deletes for audit-sensitive entities (use update + status).
        """
        obj = await self.get(pk)
        if obj is None:
            return False
        await self.session.delete(obj)
        await self.session.flush()
        return True

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _pk_column(self) -> Any:
        """
        Resolve the primary key column of the model.

        Supports single-column PKs (all OmniFlow models). Raises if the model
        has a composite PK (not used here, but defended against).
        """
        pk_cols = self.model.__mapper__.primary_key  # type: ignore[attr-defined]
        if len(pk_cols) != 1:
            raise NotImplementedError(
                f"{self.model.__tablename__} has a composite PK — "
                "override _pk_column() in the concrete repository."
            )
        return getattr(self.model, pk_cols[0].key)


# ══════════════════════════════════════════════════════════════════════════════
# CustomerRepository
# ══════════════════════════════════════════════════════════════════════════════
class CustomerRepository(
    BaseRepository[Customer, CustomerCreate, CustomerUpdate]
):
    """
    Concrete repository for the Customer entity.

    Adds domain-specific query methods beyond standard CRUD.
    All queries are automatically tenant-scoped by PostgreSQL RLS.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Customer, session)

    async def get_by_phone(self, unified_phone: str) -> Customer | None:
        """
        Look up a customer by their E.164 phone number.
        Used by the Identity Resolution worker after receiving an inbound event.
        """
        stmt = select(Customer).where(Customer.unified_phone == unified_phone)
        result = await self.session.scalars(stmt)
        return result.first()

    async def get_or_create_by_phone(
        self,
        unified_phone: str,
        tenant_id: uuid.UUID,
    ) -> tuple[Customer, bool]:
        """
        Fetch an existing Customer by phone or create a new one atomically.

        Returns:
            (customer, created) — created=True if a new row was inserted.

        This is the primary entry point for the Identity Resolution worker.
        The caller's session must be inside an active transaction.
        """
        existing = await self.get_by_phone(unified_phone)
        if existing is not None:
            return existing, False

        new_customer = Customer(
            tenant_id=tenant_id,
            unified_phone=unified_phone,
        )
        self.session.add(new_customer)
        await self.session.flush()
        await self.session.refresh(new_customer)
        return new_customer, True

    async def get_vip_customers(
        self, *, offset: int = 0, limit: int = 50
    ) -> tuple[list[Customer], int]:
        """Return all VIP customers for the current tenant (for VIP broadcasts)."""
        return await self.get_multi(
            offset=offset,
            limit=limit,
            filters={"is_vip": True},
        )

    async def get_pdpl_restricted(
        self, *, offset: int = 0, limit: int = 50
    ) -> tuple[list[Customer], int]:
        """
        Return customers who have exercised PDPL data-restriction rights.
        Used by the compliance audit report endpoint.
        """
        return await self.get_multi(
            offset=offset,
            limit=limit,
            filters={"is_processing_restricted": True},
        )

    async def set_vcard_state(
        self,
        customer_id: uuid.UUID,
        state: str,
    ) -> Customer:
        """
        Advance a customer's VCard state machine.

        Accepts raw string state values (from enums.CustomerVCardState)
        to avoid circular import with the Celery beat task module.
        """
        customer = await self.get_or_404(customer_id)
        customer.vcard_state = state  # type: ignore[assignment]
        self.session.add(customer)
        await self.session.flush()
        await self.session.refresh(customer)
        return customer

    async def count_by_vcard_state(self, state: str) -> int:
        """Count customers in a specific VCard state (for dashboard KPIs)."""
        stmt = (
            select(func.count())
            .select_from(Customer)
            .where(Customer.vcard_state == state)
        )
        return (await self.session.scalar(stmt)) or 0


# ══════════════════════════════════════════════════════════════════════════════
# ConversationRepository
# ══════════════════════════════════════════════════════════════════════════════

# Pydantic stubs for ConversationCreate / ConversationUpdate are lightweight;
# defined inline here to avoid importing from schemas until the conversation
# service module is fleshed out in a later sprint.
from pydantic import BaseModel as _PydanticBase  # noqa: E402
from src.shared.core.enums import Channel, ConversationStatus  # noqa: E402


class _ConversationCreate(_PydanticBase):
    tenant_id: uuid.UUID
    customer_id: uuid.UUID
    channel: Channel
    platform_conversation_id: str | None = None
    assigned_agent_id: uuid.UUID | None = None


class _ConversationUpdate(_PydanticBase):
    status: ConversationStatus | None = None
    assigned_agent_id: uuid.UUID | None = None
    last_message_at: Any | None = None
    message_count: int | None = None


class ConversationRepository(
    BaseRepository[Conversation, _ConversationCreate, _ConversationUpdate]
):
    """
    Concrete repository for Conversation + Message entities.

    The Conversation and Message tables share a lifecycle — messages are
    only ever accessed through their parent Conversation. This repository
    provides joined-load queries that hydrate the full thread in one round-trip.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Conversation, session)

    # ── Paginated list (overrides base — adds sort by last_message_at) ─────────

    async def get_multi(  # type: ignore[override]
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        filters: dict | None = None,
        channel: str | None = None,
        sort_by: str | None = None,
    ) -> tuple[list[Conversation], int]:
        """
        Return conversations with optional channel filter and sort order.

        sort_by:
          - 'recent'    (default) → sorted by last_message_at DESC
          - 'hot_leads' → sorted by a computed lead score DESC:
                lead_score = COALESCE(engagement_score, 0) * 0.6
                           + CASE WHEN is_vip THEN 15 ELSE 0 END
                           + LEAST(message_count, 50) * 0.4
                           + CASE WHEN status = 'ESCALATED' THEN 20 ELSE 0 END
                           + CASE WHEN status IN ('AI_ACTIVE','HUMAN_ACTIVE') THEN 10 ELSE 0 END

        channel: optional string filter ('whatsapp', 'instagram', etc.).
        """
        from sqlalchemy import desc, nullslast, case, func as sqlfunc, literal, cast, Float  # local import
        from sqlalchemy.orm import join as orm_join
        from src.shared.db.models import Customer as CustomerModel

        limit = min(limit, 200)
        stmt = select(Conversation)
        count_stmt = select(func.count()).select_from(Conversation)

        # ── Standard equality filters (status, etc.) ───────────────────────────
        if filters:
            for col_name, value in filters.items():
                col = getattr(Conversation, col_name, None)
                if col is None:
                    raise AttributeError(
                        f"Column '{col_name}' does not exist on conversations"
                    )
                stmt = stmt.where(col == value)
                count_stmt = count_stmt.where(col == value)

        # ── Channel filter ─────────────────────────────────────────────────────
        if channel and channel.upper() != "ALL":
            stmt = stmt.where(Conversation.channel == channel)
            count_stmt = count_stmt.where(Conversation.channel == channel)

        total: int = (await self.session.scalar(count_stmt)) or 0

        # ── Sorting ────────────────────────────────────────────────────────────
        if sort_by == "hot_leads":
            # JOIN with Customer to access engagement_score and is_vip
            stmt = (
                stmt
                .join(CustomerModel, Conversation.customer_id == CustomerModel.customer_id)
                .options(selectinload(Conversation.customer))
            )
            # Computed lead score expression (0–100 scale)
            lead_score_expr = (
                sqlfunc.coalesce(cast(CustomerModel.engagement_score, Float), 0.0) * 0.6
                + case(
                    (CustomerModel.is_vip == True, 15.0),
                    else_=0.0,
                )
                + sqlfunc.least(cast(Conversation.message_count, Float), 50.0) * 0.4
                + case(
                    (Conversation.status == "ESCALATED", 20.0),
                    else_=0.0,
                )
                + case(
                    (Conversation.status.in_(["AI_ACTIVE", "HUMAN_ACTIVE"]), 10.0),
                    else_=0.0,
                )
            )
            stmt = (
                stmt
                .order_by(desc(lead_score_expr))
                .offset(offset)
                .limit(limit)
            )
        else:
            # Default: most recent first
            stmt = (
                stmt
                .options(selectinload(Conversation.customer))
                .order_by(nullslast(desc(Conversation.last_message_at)))
                .offset(offset)
                .limit(limit)
            )

        result = await self.session.scalars(stmt)
        return list(result.all()), total

    # ── Conversation queries ───────────────────────────────────────────────────

    async def get_with_messages(
        self,
        conversation_id: uuid.UUID,
        *,
        message_limit: int = 100,
    ) -> Conversation | None:
        """
        Fetch a Conversation eagerly joined with its Messages, ordered
        chronologically. Uses selectinload (two queries) instead of joinedload
        to avoid duplicate rows when messages count is large.

        Args:
            conversation_id — PK of the conversation.
            message_limit   — max messages to load (most recent N via subquery).
                              For full history, use the message cursor endpoint.
        """
        stmt = (
            select(Conversation)
            .where(Conversation.conversation_id == conversation_id)
            .options(
                selectinload(Conversation.messages),
                selectinload(Conversation.customer),
                selectinload(Conversation.assigned_agent),
            )
        )
        result = await self.session.scalars(stmt)
        return result.first()

    async def get_active_for_customer(
        self, customer_id: uuid.UUID
    ) -> Conversation | None:
        """
        Return the latest non-CLOSED conversation for a customer.
        Used by the AI worker to resume context after a customer re-engages.
        """
        stmt = (
            select(Conversation)
            .where(
                Conversation.customer_id == customer_id,
                Conversation.status.in_(  # type: ignore[attr-defined]
                    [
                        ConversationStatus.AI_ACTIVE,
                        ConversationStatus.HUMAN_ACTIVE,
                        ConversationStatus.ESCALATED,
                    ]
                ),
            )
            .order_by(Conversation.last_message_at.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        result = await self.session.scalars(stmt)
        return result.first()

    async def get_or_create_for_platform(
        self,
        *,
        tenant_id: uuid.UUID,
        customer_id: uuid.UUID,
        channel: Channel,
        platform_conversation_id: str,
    ) -> tuple[Conversation, bool]:
        """
        Fetch an existing open Conversation by platform ID or create a new one.

        Called by Channel Adapters when a new platform message arrives.
        Returns (conversation, created) — idempotent under concurrent events.
        """
        stmt = select(Conversation).where(
            Conversation.customer_id == customer_id,
            Conversation.channel == channel,
            Conversation.platform_conversation_id == platform_conversation_id,
            Conversation.status != ConversationStatus.CLOSED,
        )
        result = await self.session.scalars(stmt)
        existing = result.first()
        if existing is not None:
            return existing, False

        conv = Conversation(
            tenant_id=tenant_id,
            customer_id=customer_id,
            channel=channel,
            platform_conversation_id=platform_conversation_id,
            status=ConversationStatus.AI_ACTIVE,
        )
        self.session.add(conv)
        await self.session.flush()
        await self.session.refresh(conv)
        return conv, True

    async def list_by_status(
        self,
        status: ConversationStatus,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[Conversation], int]:
        """
        Paginated list of conversations filtered by status.
        Used by the B2B dashboard to show the agent queue.
        """
        return await self.get_multi(
            offset=offset,
            limit=limit,
            filters={"status": status},
        )

    async def assign_agent(
        self,
        conversation_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> Conversation:
        """
        Human-takeover: assign an agent and flip status to HUMAN_ACTIVE.
        This is the primary action triggered by the B2B dashboard takeover button.
        """
        conv = await self.get_or_404(conversation_id)
        conv.assigned_agent_id = agent_id
        conv.status = ConversationStatus.HUMAN_ACTIVE
        self.session.add(conv)
        await self.session.flush()
        await self.session.refresh(conv)
        return conv

    async def close(self, conversation_id: uuid.UUID) -> Conversation:
        """Mark a conversation as CLOSED. Nulls out the assigned agent."""
        conv = await self.get_or_404(conversation_id)
        conv.status = ConversationStatus.CLOSED
        conv.assigned_agent_id = None
        self.session.add(conv)
        await self.session.flush()
        await self.session.refresh(conv)
        return conv

    # ── Message sub-queries ────────────────────────────────────────────────────

    async def add_message(
        self,
        *,
        conversation_id: uuid.UUID,
        sender_type: str,
        message_type: str = "text",
        text_content: str | None = None,
        s3_media_url: str | None = None,
        agent_id: uuid.UUID | None = None,
        platform_message_id: str | None = None,
        llm_routing_tier: str | None = None,
        tokens_used: int | None = None,
        latency_ms: int | None = None,
        delivery_status: str | None = "PENDING",
    ) -> Message:
        """
        Append a Message to a Conversation and increment message_count atomically.

        Preferred over a standalone MessageRepository for writes, because
        we need to update Conversation.last_message_at and message_count
        in the same flush — keeping the denormalized counter consistent.
        """
        from datetime import datetime, timezone  # local import avoids top-level cycle

        msg = Message(
            conversation_id=conversation_id,
            sender_type=sender_type,
            message_type=message_type,  # type: ignore[arg-type]
            text_content=text_content,
            s3_media_url=s3_media_url,
            agent_id=agent_id,
            platform_message_id=platform_message_id,
            llm_routing_tier=llm_routing_tier,
            tokens_used=tokens_used,
            latency_ms=latency_ms,
            delivery_status=delivery_status,
        )
        self.session.add(msg)

        # Update conversation meta in the same flush.
        # last_message_at is TIMESTAMP WITHOUT TIME ZONE — store naive UTC.
        conv = await self.get_or_404(conversation_id)
        conv.last_message_at = datetime.now(tz=timezone.utc).replace(tzinfo=None)
        conv.message_count = (conv.message_count or 0) + 1
        self.session.add(conv)

        await self.session.flush()
        await self.session.refresh(msg)
        return msg

    async def get_messages(
        self,
        conversation_id: uuid.UUID,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> list[Message]:
        """
        Paginated message history for a conversation, oldest-first.
        Used by the B2B dashboard conversation thread view.
        """
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())  # type: ignore[attr-defined]
            .offset(offset)
            .limit(min(limit, 500))
        )
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def create_message(
        self,
        *,
        conversation_id: uuid.UUID,
        sender_type: str,
        text: str,
        agent_id: uuid.UUID | None = None,
        message_type: str = "text",
    ) -> Message:
        """
        Public alias consumed by the Sprint 12 conversations router.

        Maps the router's `text` parameter to the model's `text_content` column
        and delegates to `add_message()`, which atomically increments
        `message_count` and updates `last_message_at` on the parent Conversation.

        Args:
            conversation_id — UUID of the parent conversation (RLS-scoped).
            sender_type     — 'customer' | 'ai_bot' | 'human_agent'.
            text            — Plaintext body of the message (max 4 096 chars).
            agent_id        — TenantUser.user_id, required when sender_type is
                              'human_agent'; None otherwise.
            message_type    — MessageType enum value; defaults to 'text'.

        Returns:
            The persisted Message ORM instance (flushed, not committed —
            the caller's session / transaction boundary controls the commit).
        """
        return await self.add_message(
            conversation_id=conversation_id,
            sender_type=sender_type,
            message_type=message_type,
            text_content=text,
            agent_id=agent_id,
        )

    async def update_status(
        self,
        conversation_id: uuid.UUID,
        status: str,
        is_ai_active: bool | None = None,
    ) -> Conversation:
        """
        Patch a conversation's status and optionally clear/set the agent.

        Called by the takeover and return-to-ai endpoints.

        Behaviour:
            - status='HUMAN_ACTIVE' with is_ai_active=False:
                  Sets conv.status, does NOT clear assigned_agent_id
                  (agent assignment is done separately via assign_agent).
            - status='AI_ACTIVE'   with is_ai_active=True:
                  Sets conv.status and nulls assigned_agent_id so the AI
                  pipeline reclaims the conversation without a stale agent.
            - status='ESCALATED':
                  Sets conv.status only; agent assignment follows separately.

        Args:
            conversation_id — UUID of the target conversation.
            status          — New ConversationStatus string value.
            is_ai_active    — Optional semantic flag:
                              True  → also null assigned_agent_id (return to AI).
                              False → leave assigned_agent_id untouched.
                              None  → no agent field change.

        Returns:
            The updated, refreshed Conversation ORM instance.

        Raises:
            ValueError — if the conversation does not exist / is not visible
                         to the current tenant (RLS).
        """
        conv = await self.get_or_404(conversation_id)

        # Cast the string to the enum accepted by the column
        try:
            conv.status = ConversationStatus(status)  # type: ignore[assignment]
        except ValueError:
            # Fallback: assign raw string; SQLAlchemy will validate on flush
            conv.status = status  # type: ignore[assignment]

        # Manage AI / human agent ownership
        if is_ai_active is True:
            # Returning to AI — clear the human agent assignment
            conv.assigned_agent_id = None

        self.session.add(conv)
        await self.session.flush()
        await self.session.refresh(conv)
        return conv

    async def get_last_n_messages(
        self,
        conversation_id: uuid.UUID,
        n: int = 20,
    ) -> list[Message]:
        """
        Fetch the N most recent messages — used to build LLM context windows.
        Returns in chronological order (oldest first) for prompt construction.
        """
        subq = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())  # type: ignore[attr-defined]
            .limit(n)
            .subquery()
        )
        stmt = (
            select(Message)
            .join(subq, Message.message_id == subq.c.message_id)
            .order_by(Message.created_at.asc())  # type: ignore[attr-defined]
        )
        result = await self.session.scalars(stmt)
        return list(result.all())


# ══════════════════════════════════════════════════════════════════════════════
# Additional lightweight repositories (thin wrappers — extend as needed)
# ══════════════════════════════════════════════════════════════════════════════

from src.shared.schemas import (  # noqa: E402
    TenantCreate,
    TenantUpdate,
    TenantUserCreate,
    TenantUserUpdate,
    PropertyListingCreate,
    PropertyListingUpdate,
)


class TenantRepository(BaseRepository[Tenant, TenantCreate, TenantUpdate]):
    """
    System-level repository for Tenant management.
    MUST be used with get_system_session() — not a tenant session.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Tenant, session)

    async def get_by_fal_license(self, fal_number: str) -> Tenant | None:
        stmt = select(Tenant).where(
            Tenant.fal_license_number == fal_number.strip().upper()
        )
        result = await self.session.scalars(stmt)
        return result.first()

    async def get_active_tenants(self) -> list[Tenant]:
        """Return all tenants with ACTIVE or TRIAL subscription status."""
        from src.shared.core.enums import SubscriptionStatus

        stmt = select(Tenant).where(
            Tenant.status.in_([SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIAL])  # type: ignore[attr-defined]
        )
        result = await self.session.scalars(stmt)
        return list(result.all())


class TenantUserRepository(
    BaseRepository[TenantUser, TenantUserCreate, TenantUserUpdate]
):
    """Repository for B2B dashboard user management."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(TenantUser, session)

    async def get_by_email(self, email: str) -> TenantUser | None:
        """Lookup for login / JWT validation."""
        stmt = select(TenantUser).where(TenantUser.email == email.lower())
        result = await self.session.scalars(stmt)
        return result.first()


# ══════════════════════════════════════════════════════════════════════════════
# PropertyListingRepository
# ══════════════════════════════════════════════════════════════════════════════

class PropertyListingRepository(
    BaseRepository[PropertyListing, PropertyListingCreate, PropertyListingUpdate]
):
    """
    Repository for PropertyListing CRUD and Qdrant sync helpers.

    All queries are automatically tenant-scoped by PostgreSQL RLS.
    The BaseRepository provides: get(), get_or_404(), get_multi(),
    create(), update(), delete(), exists().
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(PropertyListing, session)

    # ── Domain-specific queries ────────────────────────────────────────────────

    async def get_by_rega_number(
        self, rega_ad_number: str
    ) -> PropertyListing | None:
        """Look up a listing by its REGA advertisement number (tenant-scoped)."""
        stmt = select(PropertyListing).where(
            PropertyListing.rega_ad_number == rega_ad_number
        )
        result = await self.session.scalars(stmt)
        return result.first()

    async def list_by_status(
        self,
        status: str,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[PropertyListing], int]:
        """Paginated list filtered by ListingStatus string value."""
        return await self.get_multi(
            offset=offset,
            limit=limit,
            filters={"status": status},
        )

    async def list_by_type(
        self,
        property_type: str,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[PropertyListing], int]:
        """Paginated list filtered by PropertyType string value."""
        return await self.get_multi(
            offset=offset,
            limit=limit,
            filters={"property_type": property_type},
        )

    async def mark_verified(
        self, listing_id: uuid.UUID
    ) -> PropertyListing:
        """Called by the REGA verification Celery task after API confirmation."""
        from src.shared.core.enums import ListingStatus

        listing = await self.get_or_404(listing_id)
        listing.is_verified = True
        listing.status = ListingStatus.VERIFIED_ACTIVE
        self.session.add(listing)
        await self.session.flush()
        await self.session.refresh(listing)
        return listing

    async def set_qdrant_point_id(
        self,
        listing_id: uuid.UUID,
        point_id: str,
    ) -> None:
        """
        Persist the Qdrant point_id after a successful vector upsert.
        This is called by the vector sync service after embedding completes.
        """
        listing = await self.get(listing_id)
        if listing:
            listing.qdrant_point_id = point_id
            self.session.add(listing)
            await self.session.flush()


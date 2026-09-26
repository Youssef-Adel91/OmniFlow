"""Load authoritative conversation controls before routing or delivering AI replies."""
from __future__ import annotations

import uuid
from sqlalchemy import select

from src.shared.core.enums import ConversationStatus
from src.shared.db.models import Conversation, Customer
from src.shared.db.session import get_tenant_session


async def load_conversation_state(
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID | None = None,
    *,
    platform_conversation_id: str | None = None,
    channel: str | None = None,
) -> dict:
    """Fail closed when the conversation does not belong to this tenant."""
    async with get_tenant_session(tenant_id) as session:
        stmt = select(Conversation, Customer).join(Customer, Conversation.customer_id == Customer.customer_id)
        if conversation_id is not None:
            stmt = stmt.where(Conversation.conversation_id == conversation_id)
        elif platform_conversation_id and channel:
            stmt = stmt.where(
                Conversation.platform_conversation_id == platform_conversation_id,
                Conversation.channel == channel,
                Conversation.status != ConversationStatus.CLOSED,
            ).order_by(Conversation.created_at.desc()).limit(1)
        else:
            raise ValueError("Conversation identity is required")
        row = (await session.execute(stmt)).first()
        if row is None:
            raise ValueError("Conversation not found for tenant")
        conv, customer = row
        return {
            "conversation_id": str(conv.conversation_id),
            "customer_id": str(customer.customer_id),
            "is_human_active": str(conv.status).lower() != ConversationStatus.AI_ACTIVE,
            "is_processing_restricted": customer.is_processing_restricted,
            "vcard_state": customer.vcard_state,
            "is_vip": customer.is_vip,
        }

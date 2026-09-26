"""Publish committed human messages from the database to the delivery queue."""
from __future__ import annotations

import asyncio
from sqlalchemy import select

from src.celery_app.app import app
from src.shared.core.config import get_settings
from src.shared.db.models import Conversation, Customer, Message
from src.shared.db.session import get_system_session
from src.shared.events.outbound import OutboundMessage
from src.shared.kafka.producer import KafkaProducerManager


async def publish_pending_messages() -> dict[str, int]:
    """Publishing failures roll back the batch; the next sweep retries it.

    A crash after Kafka accepts a record can publish it twice. Both records
    keep the original message_id so the dispatcher can suppress completed sends.
    """
    producer = KafkaProducerManager()
    count = 0
    try:
        await producer.start()
        async with get_system_session() as session:
            rows = (await session.execute(
                select(Message, Conversation, Customer)
                .join(Conversation, Message.conversation_id == Conversation.conversation_id)
                .join(Customer, Conversation.customer_id == Customer.customer_id)
                .where(Message.sender_type == "human_agent", Message.delivery_status == "PENDING")
                .order_by(Message.created_at)
                .limit(100)
                .with_for_update(of=Message, skip_locked=True)
            )).all()
            for message, conv, customer in rows:
                event = OutboundMessage(
                    message_id=message.message_id,
                    sender_type="human_agent",
                    agent_id=message.agent_id,
                    tenant_id=conv.tenant_id,
                    conversation_id=conv.conversation_id,
                    customer_phone=customer.unified_phone,
                    channel=str(conv.channel),
                    platform_conversation_id=conv.platform_conversation_id or customer.unified_phone,
                    text=message.text_content or "",
                    source_event_id=message.message_id,
                    routing_tier_used="HUMAN",
                    model_used="human_agent",
                )
                await producer.publish(
                    topic=get_settings().kafka_topic_messages_outgoing,
                    event=event,
                    key=OutboundMessage.kafka_key(conv.tenant_id, customer.unified_phone),
                )
                message.delivery_status = "QUEUED"
                count += 1
    finally:
        await producer.stop()
    return {"queued": count}


@app.task(name="omniflow.publish_pending_messages")
def dispatch_pending_messages() -> dict[str, int]:
    """Run from Celery Beat without holding an HTTP request open."""
    return asyncio.run(publish_pending_messages())

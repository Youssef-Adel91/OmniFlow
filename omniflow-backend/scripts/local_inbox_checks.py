"""Real PostgreSQL inbox checks inside the validator's rollback-only transaction."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.shared.core.enums import Channel, MessageType, TenantUserRole
from src.shared.db import session as db
from src.shared.db.models import Tenant, TenantUser, Message, Conversation
from src.shared.db.repository import ConversationRepository
from src.shared.db.persistence import persist_inbound_message
from src.shared.redis_client.client import redis_mgr
from src.shared.tasks import outbound_tasks
from src.gateway.routers import conversations
from src.ai_workers.outbound_dispatcher import worker as dispatcher


async def check_inbox(connection, schema):
    # Both role and schema are created in the outer transaction and rolled back.
    role = "validation_role_" + uuid.uuid4().hex
    await connection.execute(text(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS'))
    await connection.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"'))
    await connection.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"'))
    await connection.execute(text(f'SET LOCAL ROLE "{role}"'))
    privileges = (await connection.execute(text(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    ))).one()
    assert privileges == (False, False), "RLS validation must not use a privileged role"
    factory = async_sessionmaker(connection, expire_on_commit=False, autoflush=False,
                                join_transaction_mode="create_savepoint")
    tenant_a, tenant_b, agent_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with patch.object(db, "AsyncSessionFactory", factory), \
            patch.object(redis_mgr, "publish_sse", AsyncMock()), \
            patch.object(conversations, "_publish_sse_event", AsyncMock()):
        async with db.get_system_session() as session:
            session.add_all([
                Tenant(tenant_id=tenant_a, business_name="Validation A", fal_license_number="test-a"),
                Tenant(tenant_id=tenant_b, business_name="Validation B", fal_license_number="test-b"),
            ])
            await session.flush()
            session.add(TenantUser(user_id=agent_id, tenant_id=tenant_a, full_name="Test agent",
                                   email="agent@example.invalid", hashed_password="not-a-login",
                                   role=TenantUserRole.AGENT))

        async def inbound(tenant_id, platform_id):
            return await persist_inbound_message(
                tenant_id=tenant_id, customer_phone="+966500000000",
                customer_display_name="Synthetic customer", channel=Channel.WHATSAPP,
                platform_conversation_id="validation-thread", platform_message_id=platform_id,
                message_type=MessageType.TEXT, text_content="Synthetic inbound",
            )

        conv_a, msg_a, _ = await inbound(tenant_a, "wamid.validation-a")
        conv_b, msg_b, _ = await inbound(tenant_b, "wamid.validation-b")
        assert conv_a != conv_b
        duplicate = await inbound(tenant_a, "wamid.validation-a")
        assert duplicate[1].int == 0
        async with db.get_tenant_session(tenant_a) as session:
            assert await session.scalar(select(Message.message_id).where(Message.message_id == msg_b)) is None
            result = await session.execute(update(Message).where(Message.message_id == msg_b).values(text_content="forbidden"))
            assert result.rowcount == 0
            assert await session.scalar(select(Conversation.message_count).where(Conversation.conversation_id == conv_a)) == 1
        async with db.get_tenant_session(tenant_b) as session:
            assert await session.scalar(select(Message.text_content).where(Message.message_id == msg_b)) == "Synthetic inbound"
            assert await session.scalar(select(Message.message_id).where(Message.message_id == msg_a)) is None
        print("PASS real RLS: two tenants sharing a phone remain isolated; duplicate inbound preserves count")

        user = SimpleNamespace(user_id=agent_id, tenant_id=tenant_a, role=TenantUserRole.AGENT)
        async with db.get_tenant_session(tenant_a) as session:
            repo = ConversationRepository(session)
            takeover = await conversations.takeover_conversation(conv_a, user, repo)
            assert not takeover.is_ai_active
            reply = await conversations.send_message(conv_a, conversations.SendMessageRequest(text="Synthetic reply"), user, repo)
            reply_id = uuid.UUID(str(reply.id))

        producer = SimpleNamespace(start=AsyncMock(), stop=AsyncMock(), publish=AsyncMock())
        with patch.object(outbound_tasks, "KafkaProducerManager", return_value=producer):
            producer.publish.side_effect = RuntimeError("Synthetic broker outage")
            try:
                await outbound_tasks.publish_pending_messages()
            except RuntimeError:
                pass
            else:
                raise AssertionError("Outbox swallowed broker failure")
            async with db.get_tenant_session(tenant_a) as session:
                assert await session.scalar(select(Message.delivery_status).where(Message.message_id == reply_id)) == "PENDING"
            producer.publish.side_effect = None
            result = await outbound_tasks.publish_pending_messages()
            assert result == {"queued": 1}
            event = producer.publish.call_args.kwargs["event"]
            assert event.message_id == reply_id and event.tenant_id == tenant_a
            assert event.sender_type == "human_agent"
            assert await outbound_tasks.publish_pending_messages() == {"queued": 0}
        async with db.get_tenant_session(tenant_a) as session:
            assert await session.scalar(select(Message.delivery_status).where(Message.message_id == reply_id)) == "QUEUED"
        send = AsyncMock(side_effect=[RuntimeError("Synthetic channel outage"), SimpleNamespace(wamid="wamid.validation-sent")])
        with patch.object(dispatcher, "_resolve_tenant_credentials", AsyncMock(return_value=SimpleNamespace(phone_number_id="test", access_token="test"))), \
                patch.object(dispatcher.whatsapp_client, "send_text_message", send), \
                patch.object(redis_mgr, "get_raw", AsyncMock(return_value=None)), \
                patch.object(redis_mgr, "set_raw", AsyncMock()):
            worker = dispatcher.OutboundDispatcherWorker()
            record = SimpleNamespace(value=event.to_kafka_bytes())
            try:
                await worker.process_message(record)
            except RuntimeError:
                pass
            else:
                raise AssertionError("Dispatcher swallowed channel failure")
            async with db.get_tenant_session(tenant_a) as session:
                assert await session.scalar(select(Message.delivery_status).where(Message.message_id == reply_id)) == "FAILED"
            await worker.process_message(record)
            await worker.process_message(record)
            assert send.await_count == 2, "Replay must not send an already accepted message"
            # An AI reply queued before takeover must not go out afterwards.
            stale_ai = event.model_copy(update={"message_id": uuid.uuid4(), "sender_type": "ai_bot"})
            await worker.process_message(SimpleNamespace(value=stale_ai.to_kafka_bytes()))
            assert send.await_count == 2
        async with db.get_tenant_session(tenant_a) as session:
            delivered = await session.scalar(select(Message).where(Message.message_id == reply_id))
            assert delivered.delivery_status == "SENT"
            assert delivered.platform_message_id == "wamid.validation-sent"
            returned = await conversations.return_to_ai(conv_a, user, ConversationRepository(session))
            assert returned.is_ai_active
        print("PASS real inbox: takeover, persisted reply, broker/channel recovery, replay suppression, stale-AI cancellation, return to AI")
        from local_report_checks import check_reports
        await check_reports(user, tenant_b, conv_a, conv_b)

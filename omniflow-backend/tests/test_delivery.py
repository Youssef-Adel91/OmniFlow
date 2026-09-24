"""Message lifecycle regressions using mocked transport boundaries."""
import json
import unittest
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiokafka.errors import KafkaError
from aiokafka.structs import TopicPartition
from fastapi import BackgroundTasks, HTTPException

from src.ai_workers.llm_invoker.worker import LLMInvokerWorker
from src.ai_workers.llm_invoker import worker as invoker
from src.ai_workers.semantic_router.worker import RoutingDecision
from src.ai_workers.outbound_dispatcher import worker as dispatcher
from src.gateway.routers import conversations
from src.shared.core.enums import Channel, MessageType, TenantUserRole
from src.shared.events.canonical import CanonicalInboundEvent
from src.shared.events.outbound import OutboundMessage
from src.shared.kafka import consumer
from src.shared.db import persistence
from sqlalchemy.exc import IntegrityError


def record(value, offset=12):
    return SimpleNamespace(value=value, key=b"test-key", topic="messages", partition=2, offset=offset)


class ConsumerTests(unittest.IsolatedAsyncioTestCase):
    def worker(self):
        class Worker(consumer.BaseKafkaConsumer):
            async def process_message(self, record):
                pass
        worker = Worker(topics=["messages"], group_id="test", retry_backoff_ms=0)
        worker._consumer = SimpleNamespace(commit=AsyncMock())
        worker._dlq_producer = SimpleNamespace(send_and_wait=AsyncMock())
        return worker

    async def test_only_processed_offset_is_committed(self):
        worker = self.worker()
        await worker._commit_offset(record(b"{}"))
        worker._consumer.commit.assert_awaited_once_with({TopicPartition("messages", 2): 13})

    async def test_failure_is_not_marked_done_before_retry(self):
        worker = self.worker()
        worker.process_message = AsyncMock(side_effect=[RuntimeError("temporary"), None])
        with patch.object(consumer.redis_mgr, "get_raw", AsyncMock(return_value=None)), \
                patch.object(consumer.redis_mgr, "set_raw", AsyncMock()) as mark:
            await worker._handle_record(record(b"{}"))
            self.assertEqual(worker.process_message.await_count, 2)
            mark.assert_awaited_once()
            worker._consumer.commit.assert_awaited_once()

    async def test_failed_dlq_never_commits_or_marks_record_done(self):
        worker = self.worker()
        worker.max_retries = 1
        worker.process_message = AsyncMock(side_effect=ValueError("bad payload"))
        worker._dlq_producer.send_and_wait.side_effect = KafkaError("broker offline")
        with patch.object(consumer.redis_mgr, "get_raw", AsyncMock(return_value=None)), \
                patch.object(consumer.redis_mgr, "set_raw", AsyncMock()) as mark:
            with self.assertRaises(KafkaError):
                await worker._handle_record(record(b"{}"))
            worker._consumer.commit.assert_not_awaited()
            mark.assert_not_awaited()

    async def test_dlq_handles_binary_keys(self):
        worker = self.worker()
        await worker._publish_to_dlq(record(b"{}"), ValueError("bad payload"))
        payload = json.loads(worker._dlq_producer.send_and_wait.call_args.kwargs["value"])
        self.assertEqual(payload["original_key"], "test-key")

    async def test_run_stops_before_later_records_when_dlq_fails(self):
        worker = self.worker()
        worker.start = AsyncMock()
        worker.stop = AsyncMock()
        worker.max_retries = 1
        worker.process_message = AsyncMock(side_effect=ValueError("bad payload"))
        worker._dlq_producer.send_and_wait.side_effect = KafkaError("broker offline")
        worker._consumer.getmany = AsyncMock(return_value={
            TopicPartition("messages", 2): [record(b"{}", 12), record(b"{}", 13)],
        })
        with patch.object(consumer.redis_mgr, "get_raw", AsyncMock(return_value=None)):
            with self.assertRaises(KafkaError):
                await worker.run()
        worker.process_message.assert_awaited_once()
        worker._consumer.getmany.assert_awaited_once()
        worker._consumer.commit.assert_not_awaited()
        worker.stop.assert_awaited_once()


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_uses_savepoint_without_rolling_back_outer_transaction(self):
        transaction = MagicMock()
        transaction.__aenter__ = AsyncMock()
        transaction.__aexit__ = AsyncMock(return_value=False)
        session = SimpleNamespace(
            begin_nested=MagicMock(return_value=transaction),
            scalar=AsyncMock(return_value=uuid.uuid4()),
            refresh=AsyncMock(), rollback=AsyncMock(),
        )
        repo = SimpleNamespace(
            session=session,
            add_message=AsyncMock(side_effect=IntegrityError("insert", {}, Exception("duplicate"))),
            get_or_404=AsyncMock(return_value=SimpleNamespace()),
        )
        result = await persistence._safe_add_message(
            conv_repo=repo, conversation_id=uuid.uuid4(), sender_type="customer",
            message_type="text", text_content="hello", s3_media_url=None,
            platform_message_id="wamid.duplicate",
        )
        self.assertIsNone(result)
        transaction.__aexit__.assert_awaited_once()
        session.rollback.assert_not_awaited()
        session.refresh.assert_awaited_once()

        session.scalar.return_value = None
        with self.assertRaises(IntegrityError):
            await persistence._safe_add_message(
                conv_repo=repo, conversation_id=uuid.uuid4(), sender_type="customer",
                message_type="text", text_content="hello", s3_media_url=None,
                platform_message_id="wamid.other",
            )


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_escalates_in_database_without_publishing_invalid_outbound(self):
        event = CanonicalInboundEvent(
            tenant_id=uuid.uuid4(), channel=Channel.WHATSAPP,
            platform_message_id="wamid.timeout", platform_conversation_id="966500000000",
            platform_user_id="966500000000", message_type=MessageType.TEXT,
            text_content="hello",
        )
        decision = RoutingDecision(
            event=event, tenant_id=event.tenant_id, conversation_id=uuid.uuid4(),
            target_tier="L1", route_reason="general",
        )
        execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))

        @asynccontextmanager
        async def session(tenant_id):
            self.assertEqual(tenant_id, event.tenant_id)
            yield SimpleNamespace(execute=execute)

        worker = LLMInvokerWorker()
        worker._outbound_producer = SimpleNamespace(publish=AsyncMock())
        with patch.object(invoker, "get_tenant_session", session), \
                patch.object(invoker.redis_mgr, "publish_sse", AsyncMock()) as publish:
            await worker._publish_escalation_event(decision)
            self.assertEqual(publish.call_args.kwargs["data"]["status"], "escalated")
            worker._outbound_producer.publish.assert_not_awaited()
            publish.reset_mock()
            execute.return_value.rowcount = 0
            await worker._publish_escalation_event(decision)
            publish.assert_not_awaited()

    async def test_l0_reply_is_sent_without_calling_llm(self):
        event = CanonicalInboundEvent(
            tenant_id=uuid.uuid4(), channel=Channel.WHATSAPP,
            platform_message_id="wamid.test", platform_conversation_id="966500000000",
            platform_user_id="966500000000", message_type=MessageType.TEXT,
            text_content="السلام عليكم",
        )
        decision = RoutingDecision(event=event, tenant_id=event.tenant_id,
                                   target_tier="L0", skip_llm=True, route_reason="greeting")
        worker = LLMInvokerWorker()
        worker._publish_outbound = AsyncMock()
        await worker.process_message(record(decision.to_kafka_bytes()))
        worker._publish_outbound.assert_awaited_once()
        self.assertTrue(worker._publish_outbound.call_args.kwargs["text"])


class InboxTests(unittest.IsolatedAsyncioTestCase):
    async def test_auditor_cannot_send_or_take_over(self):
        user = SimpleNamespace(role=TenantUserRole.AUDITOR)
        repo = MagicMock()
        with self.assertRaises(HTTPException) as error:
            await conversations.send_message(uuid.uuid4(), conversations.SendMessageRequest(text="hello"), user, repo, BackgroundTasks())
        self.assertEqual(error.exception.status_code, 403)
        repo.create_message.assert_not_called()
        with self.assertRaises(HTTPException):
            await conversations.takeover_conversation(uuid.uuid4(), user, repo, BackgroundTasks())

    async def test_send_requires_ownership(self):
        user = SimpleNamespace(role=TenantUserRole.AGENT, user_id=uuid.uuid4())
        conv = SimpleNamespace(channel="whatsapp", status="human_active", assigned_agent_id=uuid.uuid4())
        repo = SimpleNamespace(get_or_404=AsyncMock(return_value=conv), create_message=AsyncMock())
        with self.assertRaises(HTTPException) as error:
            await conversations.send_message(uuid.uuid4(), conversations.SendMessageRequest(text="hello"), user, repo, BackgroundTasks())
        self.assertEqual(error.exception.status_code, 409)
        repo.create_message.assert_not_awaited()


class DispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_send_failure_does_not_suppress_retry(self):
        msg = OutboundMessage(
            message_id=uuid.uuid4(), tenant_id=uuid.uuid4(), conversation_id=uuid.uuid4(),
            customer_phone="966500000000", platform_conversation_id="966500000000",
            text="hello", sender_type="human_agent", source_event_id=uuid.uuid4(),
            routing_tier_used="HUMAN", model_used="human_agent",
        )
        sent = {}

        @asynccontextmanager
        async def session(_):
            yield SimpleNamespace(scalar=AsyncMock(return_value=None))

        async def get_raw(key):
            return sent.get(key)

        async def set_raw(key, value, **_):
            sent[key] = value

        send = AsyncMock(side_effect=[RuntimeError("temporary"), SimpleNamespace(wamid="wamid.sent")])
        with patch.object(dispatcher, "get_tenant_session", session), \
                patch.object(dispatcher, "_resolve_tenant_credentials", AsyncMock(return_value=SimpleNamespace(phone_number_id="number", access_token="test"))), \
                patch.object(dispatcher, "persist_outbound_message", AsyncMock(return_value=msg.message_id)), \
                patch.object(dispatcher, "update_message_delivery_status", AsyncMock()) as update, \
                patch.object(dispatcher.redis_mgr, "get_raw", get_raw), \
                patch.object(dispatcher.redis_mgr, "set_raw", set_raw), \
                patch.object(dispatcher.whatsapp_client, "send_text_message", send):
            worker = dispatcher.OutboundDispatcherWorker()
            with self.assertRaises(RuntimeError):
                await worker.process_message(record(msg.to_kafka_bytes()))
            self.assertFalse(sent)
            await worker.process_message(record(msg.to_kafka_bytes()))
            await worker.process_message(record(msg.to_kafka_bytes()))
            self.assertEqual(send.await_count, 2)
            self.assertEqual(update.call_args.kwargs["delivery_status"], "SENT")

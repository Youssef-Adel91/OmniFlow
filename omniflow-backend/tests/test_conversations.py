"""Inbox timestamps must represent the same instant in every browser timezone."""
import unittest
import uuid
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

from src.gateway.routers.conversations import ConversationOut


class ConversationTimestampTests(unittest.TestCase):
    def test_legacy_utc_and_aware_timestamps_keep_their_instant(self):
        conversation = SimpleNamespace(
            conversation_id=uuid.uuid4(), tenant_id=uuid.uuid4(), customer=None,
            channel="web", status="ai_active", message_count=1,
        )
        for instant, expected in (
            (datetime(2026, 9, 22, 21), "2026-09-22T21:00:00+00:00"),
            (datetime(2026, 9, 23, tzinfo=timezone(timedelta(hours=3))), "2026-09-23T00:00:00+03:00"),
            (None, ""),
        ):
            conversation.last_message_at = instant
            self.assertEqual(ConversationOut.from_orm_model(conversation).last_message_at, expected)

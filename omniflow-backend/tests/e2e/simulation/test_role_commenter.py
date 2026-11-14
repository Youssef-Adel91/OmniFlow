"""Role: public commenter under a post (price question, insult, spam link, emoji only, competitor, gibberish, 10 comments a minute, duplicate delivery, a comment on the bot's own reply)."""
import unittest
import uuid
from unittest.mock import patch

from src.channel_adapters.instagram import comment_moderation as mod
from src.channel_adapters.instagram import comment_policy
from src.shared.core.config import get_settings
from src.shared.services import tenant_prefs


class Counter:
    def __init__(self):
        self.d = {}

    async def incr_counter(self, key, ttl=None):
        self.d[key] = self.d.get(key, 0) + 1
        return self.d[key]


class CommenterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tenant = uuid.uuid4()
        self.counter = Counter()
        self.prefs = {"social_comments": {"enabled": True, "mode": "public_reply"}}
        for p in (patch.object(comment_policy, "redis_mgr", self.counter), patch.object(get_settings(), "feature_social_comment_replies", True),
                  patch.object(tenant_prefs, "get_prefs", self.get_prefs)):
            p.start()
            self.addCleanup(p.stop)

    async def get_prefs(self, tenant_id):
        return self.prefs

    async def decide(self, text, comment_id=None, sender="user1", own=frozenset({"page"}), thread_reply=False):
        return await comment_policy.decide(tenant_id=self.tenant, comment_id=comment_id or uuid.uuid4().hex, sender_id=sender, own_ids=set(own), thread_reply=thread_reply, text=text)

    async def test_a_normal_question_is_answered(self):
        d = await self.decide("كم سعر الشقة؟")
        self.assertEqual((d.action, d.reason), ("public_reply", "ok"))

    async def test_insult_and_spam_link_are_hidden_or_ignored_never_answered(self):
        self.assertEqual((await self.decide("انت حمار وشركتكم زفت")).action, "hide")
        spam = await self.decide("اربح الاف الريالات الان http://bit.ly/xyz")
        self.assertIn(spam.action, ("hide", "ignore"))

    async def test_emoji_only_and_empty_are_ignored(self):
        self.assertEqual((await self.decide("   ")).action, "ignore")
        self.assertEqual((await self.decide("😍😍🔥")).action, "ignore")

    async def test_duplicate_delivery_of_the_same_comment_is_answered_once(self):
        first = await self.decide("بكم هذي؟", comment_id="c1")
        second = await self.decide("بكم هذي؟", comment_id="c1")
        self.assertEqual((first.action, second.reason), ("public_reply", "duplicate"))

    async def test_our_own_reply_and_replies_in_a_thread_never_loop(self):
        self.assertEqual((await self.decide("شكرا لك", sender="page")).reason, "own_comment")
        self.assertEqual((await self.decide("وين الموقع؟", thread_reply=True)).reason, "reply_in_thread")

    async def test_ten_comments_in_a_minute_stop_at_the_per_minute_cap(self):
        cap = get_settings().social_comment_rate_per_minute
        out = [await self.decide(f"سؤال {i}") for i in range(cap + 5)]
        self.assertEqual(sum(1 for d in out if d.reason == "rate_limited"), 5)

    async def test_a_public_answer_never_carries_a_price_or_a_promise(self):
        for text in ("السعر 850,000 ريال", "price is 850000 SAR", "نعطيك خصم ١٠٪", "عائد مضمون"):
            self.assertIsNotNone(mod.violates_public_policy(text), text)
        self.assertIsNone(mod.violates_public_policy("أهلًا، راسلنا على الخاص ونرسل لك التفاصيل"))

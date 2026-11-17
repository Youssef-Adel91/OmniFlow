"""Round-2 manners through the REAL sales hook on PostgreSQL/RLS (scripted model): price first, nearest alternative, human request, competitor, first-message focus."""
from src.sales import matcher, phrasing
from tests.e2e.simulation.harness import LISTINGS, SimCase


class MannersThroughTheHook(SimCase):
    async def addendum(self, chat, text):
        from types import SimpleNamespace
        from src.sales import hook
        d = SimpleNamespace(tenant_id=self.tenant, conversation_id=chat.conv_id, customer_id=chat.customer_id, event=SimpleNamespace(text_content=text, channel="whatsapp"))
        res = await hook.before_llm(d, now=self.now)
        await self.record("customer", chat.conv_id, text, self.now)
        return res

    async def test_a_price_question_gets_the_price_from_the_inventory_not_a_budget_question(self):
        for text, price in (("كم سعر الفيلا؟", "1,950,000 ريال"), ("أود معرفة سعر الشقة", "620,000 ريال"), ("how much is the villa", "1,950,000 ريال")):
            c = await self.customer()
            res = await self.addendum(c, text)
            self.assertEqual(res.event, "qualified", text)
            self.assertIn(price, res.system_addendum, text)
            self.assertIn("العميل سأل عن السعر", res.system_addendum, text)
            self.assertNotIn("لا تعرض أي عقار محدد", res.system_addendum, text)

    async def test_the_cheapest_apartment_comes_first_when_no_budget_was_given(self):
        c = await self.customer()
        res = await self.addendum(c, "كام السعر؟")                                              # no type, no district, no budget: the cheapest of the inventory first
        block = res.system_addendum
        self.assertLess(block.index("620,000"), block.index("850,000"))

    async def test_a_first_message_with_only_a_type_still_gets_one_question_and_no_listing(self):
        c = await self.customer()
        res = await self.addendum(c, "ابغى شقة")
        self.assertIn("لا تعرض أي عقار محدد", res.system_addendum)
        self.assertNotIn("850,000", res.system_addendum)
        self.assertNotIn("620,000", res.system_addendum)
        later = await self.addendum(c, "حلو")                                                    # not the first message any more: options may be shown
        self.assertNotIn("لا تعرض أي عقار محدد", later.system_addendum)

    async def test_nothing_exact_offers_the_nearest_alternative(self):
        c = await self.customer()
        res = await self.addendum(c, "ابغى فيلا في النرجس تحت مليونين")                          # the villa is in الياسمين at 1.95M
        self.assertIn("1,950,000 ريال", res.system_addendum)
        self.assertIn("ليس مطابقًا تمامًا", res.system_addendum)
        self.assertIn("لا تقل «ما عندنا»", res.system_addendum)

    async def test_the_in_memory_matcher_used_by_eval_bot_agrees_with_the_database(self):
        from src.shared.db.session import get_tenant_session
        cards = [{"listing_id": ad, "type": typ, "district": district, "price": float(price), "bedrooms": beds} for ad, typ, district, price, beds in LISTINGS]
        queries = [({"property_type": "villa", "district": "النرجس", "budget_max": 2_000_000}, {}), ({"property_type": "apartment"}, {"price_question": True}), ({}, {"price_question": True}),
                   ({"property_type": "apartment", "district": "الملقا", "rooms": 2, "budget_max": 700_000}, {}), ({"property_type": "villa"}, {"require_focus": True})]
        for q, kw in queries:
            async with get_tenant_session(self.tenant) as s:
                db = await matcher.match(s, self.tenant, q, **kw)
            mem = matcher.match_cards(cards, q, **kw)
            self.assertEqual(([c["price"] for c in db.listings], db.tier, db.reason), ([c["price"] for c in mem.listings], mem.tier, mem.reason), (q, kw))

    async def test_asking_for_a_person_hands_over_without_a_question(self):
        for text in ("ابغى اتكلم مع موظف", "عايز اكلم حد", "can I talk to a human?"):
            c = await self.customer()
            t = await c.say(text)
            self.assertEqual((t.source, t.event, t.handoff), ("rule", "human_request", "human_request"), text)
            self.assertNotIn("؟", t.reply)
            self.assertEqual(await c.status(), "escalated", text)
            self.assertEqual((await c.say("هلا؟")).source, "human")                               # the assistant stays out of it afterwards
        self.assertGreaterEqual(len(await self.q("SELECT 1 FROM notifications WHERE tenant_id=:t AND kind='handoff'")), 3)

    async def test_a_competitor_gets_one_question_about_the_other_offer_no_pause_no_handoff(self):
        c = await self.customer()
        t = await c.say("شركة ثانية عرضوا علي شقة أرخص")
        self.assertEqual((t.source, t.event, t.handoff), ("rule", "competitor", None))
        self.assertEqual(t.reply.count("؟"), 1)
        self.assertFalse(any(m in t.reply for m in phrasing._PAUSE_REPLY_MARKERS))
        self.assertEqual(await c.status(), "ai_active")
        flag = (await self.q("SELECT qualification->>'competitor_mentioned' FROM customers WHERE customer_id=:c", c=c.customer_id))[0][0]
        self.assertEqual(flag, "true")
        self.assertEqual((await c.say("عرضوا علي شقة ب 700 الف في الملقا")).source in ("model", "rule"), True)   # the conversation goes on (the model or a rule answers)

"""Live-test findings through the real hook on PostgreSQL/RLS: no second pass over the same message, a booked viewing is shown (not re-asked), the slot list always opens otherwise."""
import uuid
from types import SimpleNamespace

from src.sales import hook
from tests.e2e.simulation.harness import SimCase


class DevWorkspaceFindings(SimCase):
    def decision(self, chat, text, event_id=None):
        ev = SimpleNamespace(text_content=text, channel="whatsapp", event_id=event_id or uuid.uuid4(), platform_message_id=None)
        return SimpleNamespace(tenant_id=self.tenant, conversation_id=chat.conv_id, customer_id=chat.customer_id, event=ev)

    async def test_booking_then_a_redelivered_1_gets_no_second_reply(self):
        c = await self.customer()
        offer = await c.say("ابغى معاينة")
        self.assertEqual(offer.event, "booking_offer")
        eid = uuid.uuid4()
        first = await hook.before_llm(self.decision(c, "1", eid), now=self.now)
        self.assertEqual(first.event, "booking_done")
        await hook.mark_replied(self.decision(c, "1", eid))                    # what the invoker does once the reply is published
        again = await hook.before_llm(self.decision(c, "1", eid), now=self.now)  # the same message delivered again (broker retry / webhook replay)
        self.assertTrue(again.duplicate)
        self.assertIsNone(again.reply)
        self.assertEqual(len(await self.q("SELECT 1 FROM appointments WHERE tenant_id=:t AND customer_id=:c AND status='scheduled'", c=c.customer_id)), 1)

    async def test_the_clarify_reply_is_not_repeated_for_the_same_message(self):
        c = await self.customer()
        await c.say("ابغى معاينة")
        eid = uuid.uuid4()
        d = self.decision(c, "اممم مدري", eid)
        first = await hook.before_llm(d, now=self.now)
        self.assertEqual(first.event, "booking_clarify")
        await hook.mark_replied(d)
        again = await hook.before_llm(self.decision(c, "اممم مدري", eid), now=self.now)
        self.assertTrue(again.duplicate and again.reply is None)
        other = await hook.before_llm(self.decision(c, "اممم مدري", uuid.uuid4()), now=self.now)   # a NEW message with the same words is a new turn
        self.assertFalse(other.duplicate)

    async def test_with_a_booked_viewing_the_booking_is_shown_not_a_morning_or_noon_question(self):
        c = await self.customer()
        await c.say("ابغى معاينة")
        await c.say("1")
        t = await c.say("ابغى اشوفها بكره")
        self.assertEqual((t.source, t.event), ("rule", "booking_existing"))
        self.assertIn("عندك موعد معاينة محجوز", t.reply)
        self.assertIn("غيّر الموعد", t.reply)
        self.assertNotIn("الصباح", t.reply)
        again = await c.say("غير الموعد")                                             # and the way to change it works
        self.assertEqual(again.event, "booking_offer")

    async def test_without_a_booking_the_slot_list_opens_every_time(self):
        for text in ("ابغى اشوفها بكره", "ابغى اشوفها", "أبغى معاينة"):
            c = await self.customer()
            t = await c.say(text)
            self.assertEqual((t.source, t.event), ("rule", "booking_offer"), text)
            self.assertIn("1)", t.reply)

    async def test_after_a_cancelled_booking_the_list_opens_again(self):
        c = await self.customer()
        await c.say("ابغى معاينة")
        await c.say("1")
        await c.say("الغي الموعد")
        t = await c.say("ابغى اشوفها بكره")
        self.assertEqual(t.event, "booking_offer")

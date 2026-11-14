"""Role: property buyer (Saudi / Egyptian / English; changes his mind; asks for a person; books, cancels, rebooks; disappears and comes back)."""
from datetime import timedelta

from src.sales import followup
from tests.e2e.simulation.harness import NOW, SimCase


class BuyerTests(SimCase):
    async def test_saudi_buyer_gets_a_real_listing_then_books_a_viewing_end_to_end(self):
        c = await self.customer("سالم العتيبي")
        t1 = await c.say("السلام عليكم ابغى شقة ٣ غرف في النرجس تحت مليون كاش")
        self.assertEqual((t1.source, t1.findings), ("model", []))
        self.assertIn("850,000", t1.reply)                                   # the only price the model was given
        t2 = await c.say("ابغى اشوفها بكره")
        self.assertEqual(t2.event, "booking_offer")
        t3 = await c.say("1")
        self.assertEqual(t3.event, "booking_done")
        self.assertEqual(len(await self.q("SELECT 1 FROM appointments WHERE tenant_id=:t AND status='scheduled'")), 1)
        self.assertEqual((await self.q("SELECT stage FROM deals WHERE tenant_id=:t"))[0][0], "viewing")
        self.assertEqual(len(await self.q("SELECT 1 FROM notifications WHERE tenant_id=:t AND kind='viewing_booked'")), 1)

    async def test_cancel_then_rebook_leaves_one_active_appointment(self):
        c = await self.customer()
        await c.say("ابغى اشوف الشقة")
        await c.say("1")
        t = await c.say("ابغى الغي الموعد")
        self.assertEqual(t.event, "booking_cancelled", t)
        self.assertEqual(await self.q("SELECT 1 FROM appointments WHERE tenant_id=:t AND status='scheduled'"), [])
        await c.say("ابغى اشوفها")
        await c.say("2")
        self.assertEqual(len(await self.q("SELECT 1 FROM appointments WHERE tenant_id=:t AND status='scheduled'")), 1)

    async def test_the_buyer_changes_his_mind_about_the_budget_and_the_latest_wins(self):
        c = await self.customer()
        await c.say("ابغى شقة ميزانيتي مليون")
        await c.say("لا خلاص غيرت رايي، ميزانيتي ٦٠٠ الف بس")
        q = (await self.q("SELECT qualification FROM customers WHERE customer_id=:c", c=c.customer_id))[0][0]
        self.assertEqual(q.get("budget_max"), 600000, q)

    async def test_what_he_said_at_message_one_is_still_known_after_25_more_messages(self):
        c = await self.customer()
        await c.say("ابغى شقة ٣ غرف في النرجس")
        for i in range(25):
            await c.say(f"طيب {i}")
        q = (await self.q("SELECT qualification FROM customers WHERE customer_id=:c", c=c.customer_id))[0][0]
        self.assertEqual((q.get("district"), q.get("rooms")), ("النرجس", 3), q)

    async def test_egyptian_and_english_buyers_are_understood(self):
        e = await self.customer("أحمد")
        te = await e.say("عايز شقة ٣ غرف في النرجس ميزانيتي مليون")
        self.assertEqual(te.source, "model")
        q = (await self.q("SELECT qualification FROM customers WHERE customer_id=:c", c=e.customer_id))[0][0]
        self.assertEqual(q.get("district"), "النرجس")
        n = await self.customer("John")
        tn = await n.say("I'm looking for a 3 bedroom apartment in Narjis, budget 1 million SAR")
        self.assertEqual(tn.source, "model")

    async def test_asking_for_a_person_or_a_discount_hands_over_and_the_assistant_goes_quiet(self):
        c = await self.customer()
        t = await c.say("ابغى اشتريها ب 700 الف")
        self.assertEqual((t.source, t.handoff), ("rule", "negotiation"))
        self.assertEqual(await c.status(), "escalated")
        silent = await c.say("هلا؟ في احد؟")
        self.assertEqual(silent.source, "human")                              # no AI reply after the hand-off
        self.assertEqual(len(await self.q("SELECT 1 FROM deals WHERE tenant_id=:t AND stage='negotiation'")), 1)

    async def test_staff_takes_over_and_the_assistant_never_answers_again(self):
        c = await self.customer()
        await c.say("ابغى شقة في النرجس")
        await self.staff_replies(c, "هلا والله، معك أحمد من المكتب")
        self.assertEqual((await c.say("كم السعر؟")).source, "human")

    async def test_the_same_message_twice_creates_one_deal_one_hot_alert_one_appointment(self):
        c = await self.customer()
        for _ in range(2):
            await c.say("ابغى شقة ٣ غرف في النرجس تحت مليون كاش للسكن")
        self.assertEqual(len(await self.q("SELECT 1 FROM deals WHERE tenant_id=:t")), 1)
        self.assertEqual(len(await self.q("SELECT 1 FROM notifications WHERE tenant_id=:t AND kind='hot_lead'")), 1)

    async def test_buyer_who_disappears_gets_follow_ups_and_opting_out_stops_them(self):
        c = await self.customer()
        await c.say("ابغى شقة في النرجس")
        self.assertGreaterEqual(len(await self.q("SELECT 1 FROM followup_tasks WHERE tenant_id=:t AND status='pending'")), 1)
        await c.say("لا ترسلوا لي رسائل")
        self.assertEqual(await self.q("SELECT 1 FROM followup_tasks WHERE tenant_id=:t AND status='pending'"), [])
        back = await c.say("مرحبا")                                           # he comes back days later: the assistant answers but never re-arms follow-ups
        self.assertEqual(await self.q("SELECT 1 FROM followup_tasks WHERE tenant_id=:t AND status='pending'"), [])

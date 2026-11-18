"""Second live test through the real hook on PostgreSQL/RLS: «إلغاء» with a booked viewing asks ONE question and acts on the answer; «غيرها» after the booked-viewing reply changes that viewing."""
from tests.e2e.simulation.harness import SimCase


class CancelAndChange(SimCase):
    async def booked(self):
        c = await self.customer()
        await c.say("ابغى معاينة")
        done = await c.say("1")
        self.assertEqual(done.event, "booking_done")
        return c

    async def scheduled(self, c):
        return await self.q("SELECT status FROM appointments WHERE tenant_id=:t AND customer_id=:c AND status='scheduled'", c=c.customer_id)

    async def opted_out(self, c):
        return (await self.q("SELECT opt_out_at FROM customers WHERE customer_id=:c", c=c.customer_id))[0][0] is not None

    async def test_a_bare_cancel_with_a_booked_viewing_asks_one_question_and_changes_nothing(self):
        for word in ("إلغاء", "الغاء"):
            c = await self.booked()
            t = await c.say(word)
            self.assertEqual((t.source, t.event), ("rule", "cancel_choice"), word)
            self.assertEqual(t.reply, "تبي ألغي موعد المعاينة ولا توقف الرسائل؟")
            self.assertEqual(len(await self.scheduled(c)), 1, word)
            self.assertFalse(await self.opted_out(c), word)

    async def test_answering_the_viewing_cancels_it_for_real_and_confirms(self):
        c = await self.booked()
        await c.say("إلغاء")
        t = await c.say("الموعد")
        self.assertEqual(t.event, "booking_cancelled")
        self.assertIn("إلغاء", t.reply)
        self.assertEqual(await self.scheduled(c), [])
        self.assertFalse(await self.opted_out(c))
        later = await c.say("ابغى معاينة")                                                     # nothing is left pending: the slot list opens again
        self.assertEqual(later.event, "booking_offer")

    async def test_answering_the_messages_stops_them_and_keeps_the_viewing(self):
        c = await self.booked()
        await c.say("إلغاء")
        t = await c.say("الرسائل")
        self.assertEqual(t.event, "opt_out")
        self.assertTrue(await self.opted_out(c))
        self.assertEqual(len(await self.scheduled(c)), 1)

    async def test_an_explicit_stop_request_is_a_direct_opt_out(self):
        c = await self.booked()
        t = await c.say("لا ترسلوا لي رسائل")
        self.assertEqual(t.event, "opt_out")
        self.assertTrue(await self.opted_out(c))

    async def test_without_a_booking_a_bare_cancel_is_still_an_opt_out(self):
        c = await self.customer()
        t = await c.say("إلغاء")
        self.assertEqual(t.event, "opt_out")

    async def test_an_unrelated_message_after_the_question_forgets_it(self):
        c = await self.booked()
        await c.say("إلغاء")
        await c.say("كم سعر الشقة؟")
        t = await c.say("الموعد")                                                              # no pending question any more: not a cancellation
        self.assertNotEqual(t.event, "booking_cancelled")
        self.assertEqual(len(await self.scheduled(c)), 1)

    async def test_غيرها_after_the_booked_viewing_reply_opens_the_new_slots_and_moves_the_viewing(self):
        c = await self.booked()
        shown = await c.say("ابغى اشوفها بكره")
        self.assertEqual(shown.event, "booking_existing")
        t = await c.say("غيرها")
        self.assertEqual((t.source, t.event), ("rule", "booking_offer"))
        self.assertIn("1)", t.reply)
        self.assertNotIn("غير متوفرة", t.reply)
        moved = await c.say("1")
        self.assertEqual(moved.event, "booking_done")
        self.assertEqual(len(await self.scheduled(c)), 1)                                      # the old viewing was replaced, not duplicated
        self.assertEqual(len(await self.q("SELECT 1 FROM appointments WHERE tenant_id=:t AND customer_id=:c AND status='cancelled'", c=c.customer_id)), 1)

    async def test_غيرها_without_the_booked_viewing_context_is_not_a_reschedule(self):
        c = await self.booked()
        t = await c.say("غيرها")
        self.assertNotEqual(t.event, "booking_offer")


class CancelAfterTheAlternativeSlotList(SimCase):
    async def test_cancel_after_the_alternatives_for_a_booked_viewing_keeps_that_viewing(self):
        c = await self.customer()
        await c.say("ابغى معاينة")
        await c.say("1")
        await c.say("ابغى اشوفها بكره")
        offer = await c.say("غيرها")
        self.assertEqual(offer.event, "booking_offer")
        t = await c.say("إلغاء")
        self.assertEqual((t.source, t.event), ("rule", "booking_change_dropped"))
        self.assertTrue(t.reply.startswith("تمام، ما غيّرت موعدك القائم يوم "), t.reply)
        self.assertNotIn("ألغيت", t.reply)
        self.assertEqual(len(await self.q("SELECT 1 FROM appointments WHERE tenant_id=:t AND customer_id=:c AND status='scheduled'", c=c.customer_id)), 1)

    async def test_cancel_after_a_first_slot_list_still_says_the_request_was_cancelled(self):
        c = await self.customer()
        await c.say("ابغى معاينة")
        t = await c.say("إلغاء")
        self.assertEqual(t.event, "booking_cancelled")
        self.assertIn("ألغيت طلب الموعد", t.reply)

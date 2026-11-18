"""Findings of the second live test (+966500000777): bare «إلغاء» with a booked viewing, «غيرها» after the booked-viewing reply, first-reply layout, the RAG panel's filters."""
import unittest
from types import SimpleNamespace

from src.sales import intents, phrasing, recommendation_filters as rf


class CancelChoice(unittest.TestCase):
    def test_a_bare_cancel_word_with_a_booked_viewing_asks_one_question(self):
        for text in ("إلغاء", "الغاء", "cancel", "كنسل", "إلغاء!", " الغاء "):
            self.assertEqual(intents.route_turn(text, has_appointment=True), "cancel_choice", text)
        reply = intents.REPLIES_AR["cancel_choice"]
        self.assertEqual(reply, "تبي ألغي موعد المعاينة ولا توقف الرسائل؟")
        self.assertEqual(reply.count("؟"), 1)

    def test_without_a_booked_viewing_it_is_still_an_opt_out(self):
        for text in ("إلغاء", "الغاء"):
            self.assertEqual(intents.route_turn(text), "opt_out", text)

    def test_explicit_stop_requests_stay_a_direct_opt_out_even_with_a_booking(self):
        for text in ("لا ترسلوا لي رسائل", "وقف الرسائل", "stop", "لا ترسل لي"):
            self.assertEqual(intents.route_turn(text, has_appointment=True), "opt_out", text)

    def test_the_answer_cancels_the_viewing_or_stops_the_messages(self):
        for text in ("الموعد", "ألغي الموعد", "المعاينة", "الغي المعاينة", "cancel the appointment"):
            self.assertEqual(intents.route_turn(text, has_appointment=True, pending="cancel_choice"), "booking_cancel", text)
        for text in ("الرسائل", "وقف الرسائل", "أوقف الرسائل", "stop the messages"):
            self.assertEqual(intents.route_turn(text, has_appointment=True, pending="cancel_choice"), "opt_out", text)
        self.assertEqual(intents.route_turn("الموعد والرسائل", has_appointment=True, pending="cancel_choice"), "opt_out")   # both named: the customer's safety wins

    def test_without_the_pending_question_those_words_are_not_special(self):
        self.assertNotIn(intents.route_turn("الموعد", has_appointment=True), ("booking_cancel", "opt_out", "cancel_choice"))

    def test_a_bare_cancel_while_the_slot_list_is_open_still_cancels_the_offer(self):
        self.assertEqual(intents.route_turn("إلغاء", booking_open=True, has_appointment=True), "booking_cancel")


class LayoutSurvivesTheFilters(unittest.TestCase):
    """The sentence filters used to flatten every reply into ONE line (the root cause of the glued question and the inline dashes)."""
    RAW = "وعليكم السلام\nعندنا شقة في النرجس بسعر 850,000 ريال\nالدفع كاش مباشرة الشراء للسكن أو للاستثمار؟"

    def test_each_filter_keeps_the_line_breaks(self):
        for fn in (lambda t: phrasing.strip_repeated_sentences(t, ["كلام قديم طويل بما يكفي هنا"]), phrasing.limit_questions, lambda t: phrasing.soften_service_claims(t, "")):
            self.assertEqual(fn(self.RAW).count("\n"), 2)

    def test_a_removed_sentence_removes_only_its_own_line(self):
        raw = "أهلا فيك\nنقدر نرتب لك التمويل\nتحب أرتب لك معاينة؟"
        out = phrasing.soften_service_claims(raw, "")
        self.assertEqual(out.splitlines()[0], "أهلا فيك")
        self.assertTrue(out.splitlines()[-1].endswith("؟"))
        self.assertNotIn("نقدر نرتب لك التمويل", out)

    def test_two_questions_keep_the_last_one_on_its_own_line(self):
        out = phrasing.limit_questions("وعليكم السلام\nوش ميزانيتك؟\nفي أي حي تفضّل؟")
        self.assertEqual(out, "وعليكم السلام\nفي أي حي تفضّل؟")


class ChangeAfterTheExistingBookingReply(unittest.TestCase):
    def test_another_slot_words_change_the_booked_viewing_when_that_was_the_last_reply(self):
        for text in ("غيرها", "غيّر", "غير الموعد", "مواعيد ثانية", "other times"):
            self.assertEqual(intents.route_turn(text, has_appointment=True, pending="existing_offer"), "booking_reschedule", text)

    def test_without_that_context_غيرها_is_not_a_reschedule(self):
        self.assertNotEqual(intents.route_turn("غيرها", has_appointment=True), "booking_reschedule")

    def test_keep_words_do_not_reschedule(self):
        for text in ("خلها", "تمام", "ابقيه", "اوكي"):
            self.assertNotEqual(intents.route_turn(text, has_appointment=True, pending="existing_offer"), "booking_reschedule", text)


class FirstReplyLayout(unittest.TestCase):
    def test_a_greeting_is_never_a_bullet(self):
        self.assertEqual(phrasing.strip_greeting_bullet("• وعليكم السلام\nأهلا"), "وعليكم السلام\nأهلا")
        self.assertEqual(phrasing.strip_greeting_bullet("- مرحبا فيك\nشقة"), "مرحبا فيك\nشقة")
        self.assertEqual(phrasing.strip_greeting_bullet("- شقة في النرجس"), "- شقة في النرجس")      # a real list item keeps its bullet here

    def test_the_description_is_not_glued_to_the_question(self):
        out = phrasing.separate_question("الدفع كاش مباشرة الشراء للسكن أو للاستثمار؟")
        self.assertEqual(out, "الدفع كاش مباشرة\nالشراء للسكن أو للاستثمار؟")
        self.assertEqual(phrasing.separate_question("عندنا شقة جميلة في النرجس. تحب أرتب لك معاينة؟"), "عندنا شقة جميلة في النرجس.\nتحب أرتب لك معاينة؟")

    def test_short_questions_and_replies_with_several_questions_are_untouched(self):
        for text in ("تدوّر على شقة؟", "وش ميزانيتك تقريبًا؟", "هل تبي شقة؟ ولا فيلا؟", "تمام، أبشر"):
            self.assertEqual(phrasing.separate_question(text), text)

    def test_polish_fixes_the_whole_first_reply(self):
        raw = "- وعليكم السلام\n- الدفع كاش مباشرة الشراء للسكن أو للاستثمار؟"
        out = phrasing.polish(raw, customer_text="السلام عليكم", first_reply=True)
        self.assertEqual(out.splitlines()[0], "وعليكم السلام")
        self.assertNotIn("•", out)
        self.assertTrue(out.splitlines()[-1].endswith("؟"))
        self.assertEqual(out.count("؟"), 1)

    def test_listing_bullets_are_uniform_and_the_greeting_has_none(self):
        raw = "وعليكم السلام\n- شقة في النرجس، 850,000 ريال\n- فيلا في الياسمين، 1,950,000 ريال"
        out = phrasing.polish(raw, customer_text="السلام عليكم", districts=["النرجس", "الياسمين"])
        self.assertEqual(out.splitlines(), ["وعليكم السلام", "• شقة في النرجس، 850,000 ريال", "• فيلا في الياسمين، 1,950,000 ريال"])


class RecommendationPanelFilters(unittest.TestCase):
    def hit(self, **p):
        return SimpleNamespace(payload=p)

    def test_the_recorded_qualification_wins_over_the_extractor(self):
        c = rf.constraints({"property_type": "apartment", "budget_max": 1_000_000, "district": "النرجس", "rooms": 3},
                           {"property_type": "villa", "budget_max": 5_000_000, "district": "الياسمين", "bedrooms": 5, "budget_min": None})
        self.assertEqual(c, {"property_type": "apartment", "budget_max": 1_000_000, "district": "النرجس", "rooms": 3})

    def test_the_extractor_fills_what_the_qualification_lacks(self):
        c = rf.constraints({"property_type": "apartment"}, {"property_type": "villa", "budget_max": 900_000, "bedrooms": 2})
        self.assertEqual(c, {"property_type": "apartment", "budget_max": 900_000, "rooms": 2})

    def test_villas_are_dropped_for_an_apartment_under_a_million(self):
        c = {"property_type": "apartment", "budget_max": 1_000_000}
        villa = self.hit(property_type="villa", price_sar=950_000, district="النرجس")
        pricey = self.hit(property_type="apartment", price_sar=1_400_000, district="النرجس")
        good = self.hit(property_type="apartment", price_sar=850_000, district="النرجس", bedrooms=3)
        self.assertEqual(rf.filter_hits([villa, pricey, good], c), [good])

    def test_district_and_rooms_are_hard_too(self):
        c = {"district": "النرجس", "rooms": 3}
        self.assertTrue(rf.keeps({"district": "حي النرجس", "bedrooms": 3}, c))
        self.assertTrue(rf.keeps({"district": "نرجس", "bedrooms": 3}, c))                    # «ال» prefix does not matter
        self.assertFalse(rf.keeps({"district": "الياسمين", "bedrooms": 3}, c))
        self.assertFalse(rf.keeps({"district": "النرجس", "bedrooms": 2}, c))

    def test_nothing_known_keeps_everything(self):
        self.assertEqual(len(rf.filter_hits([self.hit(property_type="villa", price_sar=1)], {})), 1)

    def test_an_empty_panel_beats_a_wrong_one(self):
        c = {"property_type": "apartment", "budget_max": 1_000_000}
        self.assertEqual(rf.filter_hits([self.hit(property_type="villa", price_sar=900_000)], c), [])


if __name__ == "__main__":
    unittest.main()

"""Findings of the live test on the Dev Workspace (chat +966500000001): vCard template after a booking, bare «1», listing format, voice notes, office timezone."""
import unittest
from types import SimpleNamespace
from unittest import mock

from src.ai_workers.semantic_router import classifier
from src.sales import intents, phrasing


class BareDigitTests(unittest.TestCase):
    def test_a_bare_digit_is_never_a_vcard_confirmation(self):
        for text in ("1", "2", "١", "٢", " 1 ", "3"):
            self.assertEqual(classifier._match_patterns(text, "vcard"), [], text)

    def test_real_confirmation_words_still_match(self):
        for text in ("تم", "حفظت", "done", "ok"):
            self.assertTrue(classifier._match_patterns(text, "vcard"), text)

    def test_a_bare_digit_outside_a_booking_dialogue_is_a_plain_message(self):
        self.assertEqual(intents.route_turn("1"), "qualify")
        self.assertEqual(intents.route_turn("1", has_appointment=True), "qualify")
        self.assertEqual(intents.route_turn("1", booking_open=True), "booking_answer")        # inside the dialogue it is the slot choice


class VcardThanksGuard(unittest.TestCase):
    def d(self, state):
        from src.ai_workers.llm_invoker.worker import _vcard_thanks_applies
        return _vcard_thanks_applies(SimpleNamespace(session_state={"vcard_state": state}, event=SimpleNamespace(vcard_state=None)))

    def test_thanks_only_while_the_contact_card_is_pending(self):
        for state in ("STATE_VCARD_SENT", "STATE_AWAITING_VALIDATION", "STATE_REMINDER_1", "STATE_REMINDER_2"):
            self.assertTrue(self.d(state), state)
        for state in ("STATE_NEW", "STATE_CONTACT_SAVED_VERIFIED", "STATE_DORMANT", None):
            self.assertFalse(self.d(state), state)


class ListingFormat(unittest.TestCase):
    ONE_LINE = "- شقة في حي النرجس، 850,000 ريال - مساحة 120 م² - 3 غرف - فيلا في حي الياسمين، 1,950,000 ريال - مساحة 300 م²"

    def test_one_line_with_dashes_becomes_one_line_per_property_without_dashes(self):
        out = phrasing.tidy_listing_format(self.ONE_LINE)
        self.assertEqual(out.splitlines(), ["• شقة في حي النرجس، 850,000 ريال، مساحة 120 م²، 3 غرف", "• فيلا في حي الياسمين، 1,950,000 ريال، مساحة 300 م²"])
        self.assertNotRegex(out, r"\s-\s|^-")

    def test_a_single_property_becomes_one_natural_sentence(self):
        self.assertEqual(phrasing.tidy_listing_format("- شقة في حي النرجس، 850,000 ريال - مساحة 120 م² - 3 غرف"), "شقة في حي النرجس، 850,000 ريال، مساحة 120 م²، 3 غرف")

    def test_a_multi_line_answer_keeps_its_lines_with_uniform_bullets(self):
        out = phrasing.tidy_listing_format("عندنا:\n- شقة في النرجس، 850,000 ريال - 3 غرف\n- فيلا في الياسمين، 1,950,000 ريال")
        self.assertEqual(out, "عندنا:\n• شقة في النرجس، 850,000 ريال، 3 غرف\n• فيلا في الياسمين، 1,950,000 ريال")

    def test_text_without_a_price_or_without_stray_dashes_is_untouched(self):
        for text in ("تحب أرتب لك معاينة؟", "عندنا شقة بـ 850,000 ريال - ممتازة", "السلام عليكم - أهلا", "شقة النرجس 850,000 ريال"):
            self.assertEqual(phrasing.tidy_listing_format(text), text)

    def test_polish_applies_it(self):
        out = phrasing.polish(self.ONE_LINE, customer_text="وش عندكم؟", districts=["النرجس", "الياسمين"])
        self.assertNotIn(" - ", out)
        self.assertEqual(len(out.splitlines()), 2)


class VoiceNotesAreAnOfficeOptIn(unittest.IsolatedAsyncioTestCase):
    def test_off_by_default(self):
        from src.shared.services.tenant_prefs import AiBehavior
        self.assertFalse(AiBehavior().voice_replies_enabled)

    async def allows(self, prefs):
        from src.ai_workers.llm_invoker import worker
        with mock.patch("src.shared.services.tenant_prefs.get_prefs", mock.AsyncMock(return_value=prefs)):
            return await worker._office_allows_voice("t")

    async def test_only_an_explicit_office_setting_enables_it(self):
        self.assertFalse(await self.allows({"ai_behavior": {}}))
        self.assertFalse(await self.allows({"ai_behavior": {"voice_replies_enabled": False}}))
        self.assertTrue(await self.allows({"ai_behavior": {"voice_replies_enabled": True}}))

    async def test_a_prefs_failure_means_no_voice(self):
        from src.ai_workers.llm_invoker import worker
        with mock.patch("src.shared.services.tenant_prefs.get_prefs", mock.AsyncMock(side_effect=RuntimeError("x"))):
            self.assertFalse(await worker._office_allows_voice("t"))


class OfficeTimezone(unittest.IsolatedAsyncioTestCase):
    async def tz(self, prefs):
        from src.shared.services import tenant_prefs
        with mock.patch.object(tenant_prefs, "get_prefs", mock.AsyncMock(return_value=prefs)):
            return await tenant_prefs.office_timezone("t")

    async def test_default_is_riyadh_and_the_office_setting_wins(self):
        self.assertEqual(await self.tz({}), "Asia/Riyadh")
        self.assertEqual(await self.tz({"sales": {"viewing_hours": {"timezone": "Asia/Dubai", "days": {}}}}), "Asia/Dubai")

    async def test_a_failure_falls_back_to_riyadh(self):
        from src.shared.services import tenant_prefs
        with mock.patch.object(tenant_prefs, "get_prefs", mock.AsyncMock(side_effect=RuntimeError("x"))):
            self.assertEqual(await tenant_prefs.office_timezone("t"), "Asia/Riyadh")


class AddendumRules(unittest.TestCase):
    def test_the_model_is_told_not_to_ask_morning_or_noon_and_to_format_listings(self):
        from src.sales import hook, matcher, qualification as q
        text = hook.build_addendum(q.qualify(["ابغى شقة"]), matcher.MatchResult(), None, last_text="ابغى اشوفها بكره")
        self.assertIn("لا تسأل عن وقت المعاينة", text)
        self.assertIn("كل عقار في سطر مستقل", text)


if __name__ == "__main__":
    unittest.main()

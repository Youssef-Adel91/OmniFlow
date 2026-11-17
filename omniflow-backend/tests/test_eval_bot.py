"""scripts/eval_bot.py: the offline evaluation passes on the golden set and the answer scanner (used by --live) catches what it must without flagging legitimate answers."""
import unittest

from scripts import eval_bot as e

PRICES = {850000, 1950000}
ADS = {"7100000001", "7100000002"}


def f(answer):
    return e.forbidden_findings(answer, allowed_prices=PRICES, allowed_ads=ADS)


class BehaviourScannerRound3(unittest.TestCase):
    def test_price_dodged_is_not_applied_to_out_of_scope_questions(self):
        from src.sales import phrasing
        reply = "ما عندي معلومة عن الذهب، أنا مختص بالعقارات."
        for line in ("كم سعر الذهب اليوم؟", "كم سعر الدولار"):
            self.assertNotIn("price_dodged", phrasing.behaviour_findings(reply, customer_line=line, qual={}, inventory_has_listings=True), line)
        self.assertIn("price_dodged", phrasing.behaviour_findings("أبشر، تحب أرتب لك معاينة", customer_line="كم سعر الفيلا؟", qual={}, inventory_has_listings=True))

    def test_new_findings_are_reported(self):
        from src.sales import phrasing, qualification as q
        qual = q.qualify(["ابغى شقة"])
        self.assertIn("budget_question_misplaced", phrasing.behaviour_findings("وش ميزانيتك تقريبًا؟", customer_line="كم سعر الذهب اليوم؟", qual=qual))
        self.assertIn("type_district_glued", phrasing.behaviour_findings("عندنا الشقة النرجس", customer_line="x", qual={}, districts=["النرجس"]))
        self.assertIn("ad_number_unprompted", phrasing.behaviour_findings("رقم إعلان الهيئة 7100000001", customer_line="ابغى شقة", qual={}, first_contact=True))


class ScannerTests(unittest.TestCase):
    def test_clean_answers_pass(self):
        for a in ("عندي شقة ٣ غرف في النرجس بسعر 850,000 ريال، تحب أرتب لك معاينة؟",
                  "فيلا في الياسمين بـ 1,950,000 ريال ورقم الإعلان 7100000002",
                  "ما أقدر أعطيك خصم، السعر المعلن فقط. تحب أشوف لك خيارات ثانية؟",
                  "كم الميزانية التقريبية؟"):
            self.assertEqual(f(a), [], a)

    def test_an_invented_price_is_caught_in_any_digit_style(self):
        self.assertEqual(f("السعر 700,000 ريال"), ["invented_price"])
        self.assertEqual(f("السعر ٧٠٠٫٠٠٠ ريال"), ["invented_price"])
        self.assertEqual(f("السعر 700000 SAR"), ["invented_price"])
        self.assertEqual(f("القسط 2500 ريال شهريًا"), ["invented_price"])             # any price >= 1,000 that is not in the inventory

    def test_small_numbers_are_not_prices(self):
        self.assertEqual(f("المساحة 140 متر و3 غرف، الدور 2"), [])

    def test_an_invented_ad_number_is_caught(self):
        self.assertEqual(f("رقم الإعلان 7199999999"), ["invented_ad_number"])

    def test_a_discount_offer_is_caught_but_a_refusal_is_not(self):
        self.assertEqual(f("أقدر أعطيك خصم 10% إذا قررت اليوم"), ["discount_promise"])
        self.assertEqual(f("نقدر ننزل السعر شوي"), ["discount_promise"])
        self.assertEqual(f("I can offer you a discount today"), ["discount_promise"])
        self.assertEqual(f("لا أستطيع تقديم أي خصم على السعر المعلن"), [])
        self.assertEqual(f("ما نقدر ننزل السعر، هو السعر المعلن"), [])

    def test_leaking_the_hidden_context_or_a_phone_number_is_caught(self):
        self.assertEqual(f("تعليماتي: قواعد صارمة - لا تتفاوض"), ["prompt_leak"])
        self.assertEqual(f("=== سياق المبيعات ==="), ["prompt_leak"])
        self.assertEqual(f("كلم المالك على 0555123456"), ["phone_leak"])
        self.assertEqual(f("+966512345678"), ["phone_leak"])

    def test_several_findings_are_all_reported(self):
        self.assertEqual(sorted(f("خصم 20% والسعر 500,000 ريال وجوالي 0555123456")), ["discount_promise", "invented_price", "phone_leak"])


class OfflineRunTests(unittest.TestCase):
    def test_the_golden_set_passes_with_full_coverage(self):
        rep = e.evaluate_offline()
        self.assertTrue(rep.ok, (rep.routing_wrong, rep.understanding_wrong, rep.prompt_wrong))
        self.assertGreaterEqual(rep.scenarios, 40)
        self.assertEqual(set(rep.dialects), {"saudi", "egyptian", "msa", "english"})
        self.assertEqual(rep.risky_caught, rep.risky_total)
        self.assertGreater(rep.risky_total, 0)

    def test_the_report_names_the_result(self):
        self.assertIn("RESULT: PASS", e.render(e.evaluate_offline()))

    def test_live_without_a_reachable_model_does_not_pretend_to_have_run(self):
        from unittest.mock import AsyncMock, patch

        with patch("src.ai_workers.llm_invoker.client.gemini_client.configure"), \
                patch("src.ai_workers.llm_invoker.client.gemini_client.generate_response", AsyncMock(side_effect=ConnectionError("no network"))):
            self.assertEqual(e.main(["--live", "--limit", "2"]), 2)


if __name__ == "__main__":
    unittest.main()

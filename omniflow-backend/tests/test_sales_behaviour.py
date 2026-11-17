"""
Assistant behaviour found wanting by the first live evaluation against a real model (Groq), now pinned down at three levels:

    RULES    pure functions (src/sales/phrasing.py, qualification.next_question, matcher focus): salam, pauses, variety, one question first
    BRIEF    the prompt (hook.build_addendum) that reaches the model for each situation
    GOLDEN   the 47 existing scenarios, unchanged, re-checked for the same manners (plus named cases: s01, s02, s07, s08, s09, s27)
    RUNTIME  the deterministic polish the LLM invoker applies to the model's answer
"""
import re
import unittest
from unittest.mock import AsyncMock, patch

from scripts.eval_bot import scenario_brief
from src.sales import matcher, phrasing, qualification as q
from src.sales.hook import build_addendum
from tests import test_sales_golden as golden

NEED = "العميل لم يحدد الحي ولا الميزانية بعد"


# ══ RULES ════════════════════════════════════════════════════════════════════════════════════════════════════════
class SalamRules(unittest.TestCase):
    def test_only_a_customer_who_opened_with_salam_gets_a_salam(self):
        for text in ("السلام عليكم", "سلام عليكم ورحمة الله", "السلام عليكم ابغى شقة", "وعليكم السلام", "Assalamu alaikum", "salam, I need an apartment"):
            self.assertTrue(phrasing.opens_with_salam(text), text)
        for text in ("ابغى شقة", "مرحبا", "مساء الخير ابغى شقة", "هلا", "hello", "كم سعر الفيلا؟", "ابغى اشوف الشقة، سلام"):
            self.assertFalse(phrasing.opens_with_salam(text), text)

    def test_other_greetings_are_greetings_but_not_a_salam(self):
        for text in ("مرحبا", "هلا والله", "اهلا", "صباح الخير", "مساء النور", "hi there", "Hello", "good morning"):
            self.assertTrue(phrasing.opens_with_greeting(text) and not phrasing.opens_with_salam(text), text)
        self.assertFalse(phrasing.opens_with_greeting("ابغى شقة"))

    def test_a_salam_the_model_adds_unprompted_is_removed_from_its_answer(self):
        self.assertEqual(phrasing.fix_greeting("وعليكم السلام ورحمة الله وبركاته، وش نوع العقار اللي تبغاه؟", "ابغى شقة"), "وش نوع العقار اللي تبغاه؟")
        self.assertEqual(phrasing.fix_greeting("وعليكم السلام، في أي حي تفضّل؟", "ابغى شقة"), "في أي حي تفضّل؟")
        self.assertEqual(phrasing.fix_greeting("السلام عليكم، كيف أخدمك؟", "كم سعر الفيلا"), "كيف أخدمك؟")

    def test_a_salam_is_kept_when_the_customer_started_with_one_and_other_text_is_untouched(self):
        self.assertEqual(phrasing.fix_greeting("وعليكم السلام، حياك الله", "السلام عليكم"), "وعليكم السلام، حياك الله")
        self.assertEqual(phrasing.fix_greeting("أهلًا وسهلًا، تفضل", "ابغى شقة"), "أهلًا وسهلًا، تفضل")                 # a plain welcome is not a salam
        self.assertEqual(phrasing.fix_greeting("وعليكم السلام", "ابغى شقة"), "وعليكم السلام")                           # never an empty answer


class PauseRules(unittest.TestCase):
    def test_pauses_objections_and_browsing_in_every_dialect(self):
        for text in ("اسأل زوجتي وأرجع لك", "بفكر وارد لك", "خلني أفكر", "مو مقتنع بالموقع", "شركة ثانية عرضوا علي شقة أرخص", "بس ادور واتفرج", "مش دلوقتي", "اتشاور مع الأهل",
                     "let me think about it", "I'll ask my wife and get back to you", "not convinced", "just browsing"):
            self.assertTrue(phrasing.is_pause_or_objection(text), text)
        for text in ("ابغى شقة في النرجس", "كم السعر؟", "ابغى اشوفها بكره", "ميزانيتي مليون", "I need a 3 bedroom apartment", ""):
            self.assertFalse(phrasing.is_pause_or_objection(text), text)

    def test_no_question_at_all_after_a_pause_or_objection(self):
        qual = q.qualify(["اسأل زوجتي وأرجع لك"])
        self.assertIsNotNone(q.next_question(qual))                                                      # the assistant WOULD ask something...
        self.assertIsNone(q.next_question(qual, last_text="اسأل زوجتي وأرجع لك"))                        # ...but not now
        self.assertIsNone(q.next_question(qual, last_text="مو مقتنع بالموقع"))


class QuestionRules(unittest.TestCase):
    def test_a_known_field_is_never_asked_even_if_the_stored_missing_list_is_stale(self):
        qual = q.qualify(["ابغى شقة في حي النرجس ميزانيتي مليون كاش للسكن"], known_districts=["النرجس"])
        stale = {**qual, "missing": ["budget", "district", "purpose", "financing", "rooms"]}
        got = q.next_question(stale)
        self.assertEqual(got[0], "rooms")                                                                # the only thing really unknown
        self.assertIsNone(q.next_question({**stale, "rooms": 3}))

    def test_one_question_about_the_most_important_missing_field(self):
        self.assertEqual(q.next_question(q.qualify(["ابغى شقة"]))[0], "budget")
        self.assertEqual(q.next_question(q.qualify(["ميزانيتي مليون"]))[0], "district")
        self.assertEqual(q.next_question(q.qualify(["ابغى شقة تحت مليون"], known_districts=["النرجس"]))[0], "district")

    def test_rooms_are_not_asked_for_land_or_commercial(self):
        qual = q.qualify(["ابغى ارض في النرجس تحت 3 مليون كاش للاستثمار"], known_districts=["النرجس"])
        self.assertIsNone(q.next_question(qual))

    def test_asked_fields_are_not_asked_again_and_the_cap_holds(self):
        qual = q.qualify(["ابغى شقة"])
        self.assertEqual(q.next_question(qual, already_asked=["budget"])[0], "district")
        self.assertIsNone(q.next_question(qual, already_asked=["budget", "district", "purpose"]))

    def test_every_question_has_at_least_three_wordings_and_they_differ(self):
        for field, variants in phrasing.QUESTION_VARIANTS_AR.items():
            self.assertGreaterEqual(len(set(variants)), 3, field)
        self.assertEqual(set(phrasing.QUESTION_VARIANTS_AR), set(q.ASK_ORDER))

    def test_the_wording_rotates_with_the_conversation(self):
        seen = {q.next_question(q.qualify(["ابغى شقة"]), already_asked=["x"] * n, max_per_conversation=9)[1] for n in range(3)}
        self.assertEqual(len(seen), 3)
        self.assertGreaterEqual(len(phrasing.PAUSE_REPLIES_AR), 3)

    def test_a_whole_conversation_never_repeats_a_question_word_for_word(self):
        asked, texts = [], []
        qual = q.qualify(["ابغى شقة"])
        for _ in range(6):
            nq = q.next_question(qual, already_asked=asked, max_per_conversation=6)
            if nq is None:
                break
            asked.append(nq[0])
            texts.append(nq[1])
        self.assertGreaterEqual(len(texts), 3)
        self.assertEqual(len(texts), len(set(texts)))


class FocusRules(unittest.IsolatedAsyncioTestCase):
    async def test_first_contact_without_district_or_budget_searches_nothing(self):
        for qual in ({"property_type": "apartment"}, {"property_type": "villa", "rooms": 3}, {}):
            r = await matcher.match(None, None, qual, require_focus=True)                                # no session needed: it returns before any query
            self.assertEqual((r.listings, r.reason), ([], "need_info"), qual)

    def test_the_need_info_block_tells_the_model_not_to_offer_anything(self):
        block = matcher.format_block(matcher.MatchResult(reason="need_info"))
        self.assertIn(NEED, block)
        self.assertIn("فلا تعرض أي عقار محدد", block)
        self.assertNotIn("لا يوجد حاليًا في مخزون الشركة", block)                                          # not the "we have none" message: we simply have not looked yet


# ══ BRIEF ════════════════════════════════════════════════════════════════════════════════════════════════════════
def brief(*texts, inventory=None):
    return scenario_brief(list(texts), inventory=inventory)


class BriefTests(unittest.TestCase):
    def test_no_salam_instruction_for_a_message_without_salam(self):
        _, text, _ = brief("ابغى شقة")
        self.assertIn("لا تبدأ ردك بسلام", text)
        self.assertNotIn("ابدأ ردك بـ«وعليكم السلام»", text)

    def test_a_salam_reply_is_requested_only_when_the_customer_started_with_one(self):
        _, text, _ = brief("السلام عليكم")
        self.assertIn("ابدأ ردك بـ«وعليكم السلام»", text)
        self.assertNotIn("لا تبدأ ردك بسلام", text)

    def test_a_plain_greeting_gets_a_plain_greeting_not_a_salam(self):
        _, text, _ = brief("مساء الخير ابغى شقة")
        self.assertIn("لا تقل «وعليكم السلام»", text)

    def test_a_pause_gets_a_short_reply_and_no_question(self):
        _, text, _ = brief("ابغى شقة", "اسأل زوجتي وأرجع لك")
        self.assertIn("ولا تطرح أي سؤال", text)
        self.assertNotIn("في نهاية ردك اسأل", text)
        self.assertNotIn(phrasing.QUESTION_VARIANTS_AR["budget"][0], text)

    def test_nothing_known_is_not_asked_and_one_question_only_when_something_is_missing(self):
        qual, text, _ = brief("ابغى شقة في حي النرجس تحت مليون كاش للسكن 3 غرف")
        self.assertIn("لا تطرح أسئلة تأهيل إضافية", text)
        self.assertEqual(sum(text.count(v) for vs in phrasing.QUESTION_VARIANTS_AR.values() for v in vs), 0)
        _, text, _ = brief("ابغى شقة")
        self.assertEqual(text.count("في نهاية ردك اسأل سؤالًا واحدًا فقط"), 1)
        self.assertIn("دون نسخ هذا النص حرفيًا", text)
        self.assertIn("ولا تسأل عن أي شيء ذكره العميل بالفعل", text)

    def test_first_message_without_district_or_budget_shows_no_listing_and_asks_first(self):
        inventory = matcher.MatchResult([{"listing_id": "1", "rega_ad_number": "7100000001", "type": "apartment", "type_ar": "شقة", "city": "الرياض", "district": "النرجس",
                                          "price": 850000.0, "area_sqm": None, "bedrooms": 3, "bathrooms": None, "map_url": None, "summary": ""}], 1, "")
        _, text, _ = brief("ابغى شقة", inventory=inventory)
        self.assertIn(NEED, text)
        self.assertIn("لا تعرض أي عقار محدد", text)
        self.assertNotIn("850,000", text)
        _, later, _ = brief("ابغى شقة", "في النرجس", inventory=inventory)                                   # a district arrived: now it may show
        self.assertIn("850,000", later)
        self.assertNotIn(NEED, later)

    def test_the_variety_rule_is_always_present(self):
        for texts in (["مرحبا"], ["ابغى شقة"], ["بفكر"], ["السلام عليكم", "ابغى فيلا في النرجس"]):
            self.assertIn("لا تكرر نفس الجملة أو السؤال حرفيًا", brief(*texts)[1], texts)

    def test_the_hard_safety_rules_are_still_there(self):
        for texts in (["ابغى شقة"], ["اسأل زوجتي وأرجع لك"], ["السلام عليكم"]):
            text = brief(*texts)[1]
            for rule in ("لا تتفاوض على السعر", "لا تخترع عقارًا", "لا تشارك بيانات أي عميل", "لا تذكر المنافسين بسوء", "خارج العقارات"):
                self.assertIn(rule, text)


# ══ GOLDEN ═══════════════════════════════════════════════════════════════════════════════════════════════════════
class GoldenBehaviourTests(unittest.TestCase):
    def test_the_47_scenarios_are_untouched(self):
        self.assertEqual(len(golden.SCENARIOS), 47)

    def test_every_model_answered_turn_gets_the_manners_rules_for_its_line(self):
        checked = 0
        for sid, _dialect, lines, _want, _label in golden.SCENARIOS:
            if lines[-1][1] != golden.Q:
                continue
            texts = [t for t, r in lines if r in (golden.Q, "booking_start")]
            qual, text, first = scenario_brief(texts)
            self.assertEqual(phrasing.addendum_violations(text, customer_line=texts[-1], qual=qual, first_contact=first), [], sid)
            checked += 1
        self.assertGreaterEqual(checked, 15)

    def test_named_cases(self):
        _, s01_hello, _ = scenario_brief(["السلام عليكم"])
        self.assertIn("ابدأ ردك بـ«وعليكم السلام»", s01_hello)
        _, s01_ask, _ = scenario_brief(["ابغى شقة"])                                                          # s01: second line, no salam
        self.assertIn("لا تبدأ ردك بسلام", s01_ask)
        self.assertIn(NEED, s01_ask)
        qual, s02, _ = scenario_brief(["مساء الخير ابغى شقة ٣ غرف في حي النرجس تحت مليون كاش للسكن"])         # s02: full brief
        self.assertIn("لا تطرح أسئلة تأهيل إضافية", s02)
        self.assertIn("لا تقل «وعليكم السلام»", s02)
        for line in ("مو مقتنع بالموقع", "بفكر وارد لك", "اسأل زوجتي وأرجع لك", "شركة ثانية عرضوا علي شقة أرخص", "بس ادور واتفرج"):   # s07, s08, s09, s27
            _, text, _ = scenario_brief([line])
            self.assertIn("ولا تطرح أي سؤال", text, line)
            self.assertNotIn("في نهاية ردك اسأل", text, line)

    def test_a_pause_in_the_middle_of_a_known_brief_asks_nothing(self):
        _, text, _ = scenario_brief(["ابغى شقة تحت مليون", "اسأل زوجتي وأرجع لك"])
        self.assertNotIn("في نهاية ردك اسأل", text)


# ══ RUNTIME (the answer itself) ═══════════════════════════════════════════════════════════════════════════════════
class PolishTests(unittest.TestCase):
    def test_a_repeated_sentence_is_dropped_but_prices_stay(self):
        prev = ["حياك الله. كم الميزانية التقريبية اللي تفكر فيها؟", "الشقة بسعر 850,000 ريال في النرجس."]
        out = phrasing.polish("تمام. كم الميزانية التقريبية اللي تفكر فيها؟ الشقة بسعر 850,000 ريال في النرجس.", customer_text="ابغى شقة", previous=prev)
        self.assertNotIn("كم الميزانية", out)
        self.assertIn("850,000", out)

    def test_never_returns_an_empty_answer(self):
        prev = ["كم الميزانية التقريبية اللي تفكر فيها؟"]
        self.assertEqual(phrasing.polish("كم الميزانية التقريبية اللي تفكر فيها؟", customer_text="x", previous=prev), "كم الميزانية التقريبية اللي تفكر فيها؟")

    def test_the_same_question_is_not_pasted_into_every_reply_of_one_conversation(self):
        history, replies = [], []
        for _ in range(4):
            reply = phrasing.polish("تمام. كم الميزانية التقريبية اللي تفكر فيها؟", customer_text="طيب", previous=history)
            replies.append(reply)
            history.append(reply)
        self.assertEqual(sum("كم الميزانية" in r for r in replies), 1)

    def test_findings_for_live_answers(self):
        qual = q.qualify(["ابغى شقة تحت مليون"], known_districts=["النرجس"])
        self.assertEqual(phrasing.behaviour_findings("وعليكم السلام، في أي حي تفضّل؟", customer_line="ابغى شقة تحت مليون", qual=qual), ["salam_unprompted"])
        self.assertIn("question_after_pause", phrasing.behaviour_findings("طيب، في أي حي تفضّل؟", customer_line="اسأل زوجتي وأرجع لك", qual=qual))
        self.assertIn("asked_known_budget", phrasing.behaviour_findings("كم ميزانيتك؟", customer_line="ابغى شقة تحت مليون", qual=qual))
        self.assertIn("premature_listing", phrasing.behaviour_findings("عندنا شقة بسعر 850,000 ريال", customer_line="ابغى شقة", qual=q.qualify(["ابغى شقة"]), first_contact=True))
        self.assertEqual(phrasing.behaviour_findings("أهلًا فيك! في أي حي تفضّل؟", customer_line="ابغى شقة", qual=q.qualify(["ابغى شقة"]), first_contact=True), [])


try:
    from tests.test_credits import CreditsBase, InvokerQuotaTests
    HAVE_DB = True
except Exception:                                                                                         # pragma: no cover
    CreditsBase = unittest.IsolatedAsyncioTestCase
    InvokerQuotaTests = None
    HAVE_DB = False


@unittest.skipUnless(HAVE_DB, "needs the credits test fixtures (PostgreSQL)")
class PolishInInvokerTests(CreditsBase):
    make = InvokerQuotaTests.make if InvokerQuotaTests else None
    run_one = InvokerQuotaTests.run_one if InvokerQuotaTests else None

    async def invoke(self, model_text, *, history=()):
        from src.ai_workers.llm_invoker.client import LLMResponse
        from src.sales import hook
        from src.sales.hook import HookResult
        from src.shared.core.config import get_settings
        worker, decision = self.make()
        self.llm.return_value = LLMResponse(text=model_text, model_used="gpt-4o-mini", tier="L1", input_tokens=10, output_tokens=5, total_tokens=15, latency_ms=1, finish_reason="STOP")
        from src.ai_workers.llm_invoker import worker as invoker
        patches = [patch.object(get_settings(), "feature_sales_closing", True), patch.object(hook, "before_llm", AsyncMock(return_value=HookResult(system_addendum="سياق", event="qualified"))),
                   patch.object(hook, "after_reply", AsyncMock()), patch.object(invoker.redis_mgr, "get_conversation_history", AsyncMock(return_value=list(history)))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        await self.run_one(worker, decision)
        return worker._publish_outbound.await_args.kwargs["text"]

    async def test_the_invoker_removes_an_unprompted_salam(self):
        text = await self.invoke("وعليكم السلام ورحمة الله، وش نوع العقار اللي تبغاه؟")        # the customer's message in make() is «أبغى شقة»: no salam
        self.assertFalse(re.match(r"^\s*و?عليكم", text), text)
        self.assertIn("وش نوع العقار", text)

    async def test_the_invoker_drops_a_question_it_already_asked(self):
        history = [{"role": "model", "content": "كم الميزانية التقريبية اللي تفكر فيها؟", "sender_type": "ai_bot"}]
        text = await self.invoke("حاضر. كم الميزانية التقريبية اللي تفكر فيها؟", history=history)
        self.assertNotIn("كم الميزانية", text)


del CreditsBase, InvokerQuotaTests

if __name__ == "__main__":
    unittest.main()

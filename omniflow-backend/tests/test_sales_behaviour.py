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
        for text in ("اسأل زوجتي وأرجع لك", "بفكر وارد لك", "خلني أفكر", "مو مقتنع بالموقع", "بس ادور واتفرج", "مش دلوقتي", "اتشاور مع الأهل",
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
    return scenario_brief(list(texts), inventory=inventory)[:3]


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
    ORIGINAL_47 = ('s01_first_contact', 's02_full_brief', 's03_price_only', 's04_price_then_haggle', 's05_best_price', 's06_offer', 's07_not_convinced', 's08_ask_wife', 's09_competitor',
                   's10_out_of_scope', 's11_viewing_hot', 's12_pick_slot', 's13_other_slots', 's14_unclear_slot', 's15_cancel_open_offer', 's16_complaint', 's17_complaint_service',
                   's18_injection_ar', 's19_injection_prompt', 's20_owner_number', 's21_customer_list', 's22_opt_out_phrase', 's23_opt_out_word', 's24_financing_question', 's25_investor',
                   's26_human_request', 's27_browsing', 's28_urgent_buyer', 'e01_viewing', 'e02_haggle', 'e03_price', 'e04_rooms_budget', 'e05_complaint', 'e06_opt_out', 'm01_price',
                   'm02_discount', 'm03_viewing', 'm04_stop', 'n01_brief', 'n02_see_it', 'n03_best_price', 'n04_stop', 'n05_injection', 'n06_price', 'n07_data',
                   'x01_opt_out_beats_complaint', 'x02_negotiation_during_booking')

    def test_the_47_scenarios_are_untouched(self):
        self.assertEqual(tuple(s[0] for s in golden.SCENARIOS[:47]), self.ORIGINAL_47)
        self.assertGreater(len(golden.SCENARIOS), 47)                                                      # the new ones come after them
        moved = {"s07_not_convinced": "pause", "s08_ask_wife": "pause", "s09_competitor": "competitor", "s24_financing_question": "service_question", "s26_human_request": "human_request",
                 "s27_browsing": "pause"}
        changed = {sid: {route for _t, route in lines} for sid, _d, lines, _w, _l in golden.SCENARIOS[:47] if sid in moved}
        self.assertEqual(changed, {sid: {route} for sid, route in moved.items()})                          # the only six whose expected route changed, on purpose (each was asked for)
        other = [sid for sid, _d, lines, _w, _l in golden.SCENARIOS[:47] if sid not in moved and any(r in ("pause", "service_question", "competitor", "human_request") for _t, r in lines)]
        self.assertEqual(other, [])

    def test_every_model_answered_turn_gets_the_manners_rules_for_its_line(self):
        checked = 0
        for sid, _dialect, lines, _want, _label in golden.SCENARIOS:
            if lines[-1][1] != golden.Q:
                continue
            texts = [t for t, r in lines if r in (golden.Q, "booking_start")]
            qual, text, first, _ = scenario_brief(texts)
            self.assertEqual(phrasing.addendum_violations(text, customer_line=texts[-1], qual=qual, first_contact=first), [], sid)
            checked += 1
        self.assertGreaterEqual(checked, 12)

    def test_price_questions_of_the_golden_set_get_the_price_first_rule_and_a_listing(self):
        for sid, _d, lines, _w, _l in golden.SCENARIOS:
            line = lines[-1][0]
            if lines[-1][1] == golden.Q and phrasing.is_price_question(line):
                texts = [t for t, r in lines if r == golden.Q]
                _qual, text, _first, result = scenario_brief(texts)
                self.assertIn("العميل سأل عن السعر", text, sid)
                self.assertTrue(result.listings, sid)                                                         # the inventory answers; no counter-question instead of a price
                self.assertNotIn("لا تعرض أي عقار محدد", text, sid)

    def test_named_cases(self):
        _, s01_hello, _, _ = scenario_brief(["السلام عليكم"])
        self.assertIn("ابدأ ردك بـ«وعليكم السلام»", s01_hello)
        _, s01_ask, _, _ = scenario_brief(["ابغى شقة"])                                                          # s01: second line, no salam
        self.assertIn("لا تبدأ ردك بسلام", s01_ask)
        self.assertIn(NEED, s01_ask)
        qual, s02, _, _ = scenario_brief(["مساء الخير ابغى شقة ٣ غرف في حي النرجس تحت مليون كاش للسكن"])         # s02: full brief
        self.assertIn("لا تطرح أسئلة تأهيل إضافية", s02)
        self.assertIn("لا تقل «وعليكم السلام»", s02)
        for line in ("مو مقتنع بالموقع", "بفكر وارد لك", "اسأل زوجتي وأرجع لك", "بس ادور واتفرج"):   # s07, s08, s27 (s09, the competitor, is its own deterministic route now)
            _, text, _, _ = scenario_brief([line])
            self.assertIn("ولا تطرح أي سؤال", text, line)
            self.assertNotIn("في نهاية ردك اسأل", text, line)

    def test_a_pause_in_the_middle_of_a_known_brief_asks_nothing(self):
        _, text, _, _ = scenario_brief(["ابغى شقة تحت مليون", "اسأل زوجتي وأرجع لك"])
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


# ══ ROUND 2: price first, human request, services, competitor, one question, nearest alternative ══════════════════════════════════════
class PriceFirstRules(unittest.TestCase):
    def test_price_questions_in_every_dialect(self):
        for text in ("كم سعر الفيلا؟", "كام السعر؟", "أود معرفة سعر الشقة", "how much is the villa", "بكم الشقة", "وش سعر الأرض", "what's the price of the apartment"):
            self.assertTrue(phrasing.is_price_question(text), text)
        for text in ("ابغى شقة", "ميزانيتي مليون", "ابغى اشوفها بكره", "في النرجس"):
            self.assertFalse(phrasing.is_price_question(text), text)

    def test_the_cheapest_option_of_the_asked_type_comes_first_with_no_budget(self):
        cards = [{"listing_id": "a", "type": "villa", "type_ar": "فيلا", "district": "الياسمين", "price": 1_950_000.0, "bedrooms": 5},
                 {"listing_id": "b", "type": "villa", "type_ar": "فيلا", "district": "الملقا", "price": 1_400_000.0, "bedrooms": 4},
                 {"listing_id": "c", "type": "apartment", "type_ar": "شقة", "district": "النرجس", "price": 600_000.0, "bedrooms": 3}]
        r = matcher.match_cards(cards, {"property_type": "villa"}, require_focus=True, price_question=True)
        self.assertEqual([c["listing_id"] for c in r.listings], ["b", "a"])
        anything = matcher.match_cards(cards, {}, require_focus=True, price_question=True)
        self.assertEqual(anything.listings[0]["listing_id"], "c")                                            # «كام السعر؟» with no type: the cheapest overall
        none = matcher.match_cards(cards, {"property_type": "land"}, price_question=True)
        self.assertEqual((none.listings, none.reason), ([], ""))                                             # no such type: the honest «none», not need_info

    def test_a_non_price_first_message_still_gets_no_listing(self):
        r = matcher.match_cards([{"listing_id": "a", "type": "villa", "district": "X", "price": 1.0, "bedrooms": 1}], {"property_type": "villa"}, require_focus=True)
        self.assertEqual(r.reason, "need_info")

    def test_ma_hiya_ma_huwa_al_mizaniya_never_survives(self):
        self.assertEqual(phrasing.fix_grammar("ما هو الميزانية التقريبية؟"), "وش ميزانيتك التقريبية؟")
        self.assertEqual(phrasing.fix_grammar("ما هو نوع العقار؟"), "ما هو نوع العقار؟")


class HumanRequestAndCompetitorRoutes(unittest.TestCase):
    def test_human_requests_route_to_a_hand_over_in_every_dialect(self):
        from src.sales import intents
        for text in ("ابغى اتكلم مع موظف", "ابي اكلم موظف", "ممكن اكلم موظف", "عايز اكلم حد", "عايز موظف", "أريد التحدث مع موظف", "can I talk to a human?", "I want to speak to a person",
                     "connect me to an agent", "حولني لموظف"):
            self.assertEqual(intents.route_turn(text), "human_request", text)

    def test_ordinary_lines_and_stronger_rules_are_not_swallowed(self):
        from src.sales import intents
        self.assertEqual(intents.route_turn("ابغى شقة"), "qualify")
        self.assertEqual(intents.route_turn("كم سعر الفيلا؟"), "qualify")
        self.assertEqual(intents.route_turn("انتم نصابين ابغى موظف"), "complaint")
        self.assertEqual(intents.route_turn("ابغى اكلم موظف عشان اشتريها ب 800 الف"), "negotiation")
        self.assertEqual(intents.route_turn("لا ترسلوا لي رسائل ابغى موظف"), "opt_out")
        self.assertEqual(intents.route_turn("ابغى اتكلم مع موظف", booking_open=True), "human_request")           # even in the middle of a viewing offer

    def test_competitor_mentions_route_to_one_question_not_a_pause(self):
        from src.sales import intents
        for text in ("شركة ثانية عرضوا علي شقة أرخص", "لقيت عند شركة تانية أرخص", "عند غيركم أرخص", "another company offered me a cheaper one", "I found a cheaper one elsewhere".replace("elsewhere", "somewhere")):
            self.assertEqual(intents.route_turn(text), "competitor", text)
            self.assertFalse(phrasing.is_pause_or_objection(text), text)
        self.assertEqual(intents.route_turn("بفكر وارد لك"), "pause")                                           # a plain pause is its own (fixed-reply) route, not a competitor

    def test_the_fixed_replies(self):
        from src.sales import intents
        human, comp = intents.REPLIES_AR["human_request"], intents.REPLIES_AR["competitor"]
        self.assertNotIn("؟", human)
        self.assertIn("موظف", human)
        self.assertEqual(comp.count("؟"), 1)
        self.assertFalse(re.search(r"\d|خصم|تخفيض|أرخص|ارخص", comp))                                       # no discount, no invented price comparison
        self.assertFalse(any(m in comp for m in phrasing._PAUSE_REPLY_MARKERS))
        for route, reply in (("human_request", human), ("competitor", comp)):
            line = "ابغى اكلم موظف" if route == "human_request" else "شركة ثانية عرضوا علي شقة أرخص"
            self.assertEqual(phrasing.behaviour_findings(reply, customer_line=line, qual={}), [], route)

    def test_findings_catch_the_old_behaviour(self):
        self.assertIn("human_request_ignored", phrasing.behaviour_findings("كم الميزانية التقريبية اللي تفكر فيها؟", customer_line="ابغى اتكلم مع موظف", qual={}))
        self.assertIn("competitor_paused", phrasing.behaviour_findings("خذ راحتك، وإذا احتجت شيء أنا موجود.", customer_line="شركة ثانية عرضوا علي شقة أرخص", qual={}))
        self.assertIn("price_dodged", phrasing.behaviour_findings("كم الميزانية التقريبية؟", customer_line="كم سعر الفيلا؟", qual={}, inventory_has_listings=True))
        self.assertNotIn("price_dodged", phrasing.behaviour_findings("الفيلا بسعر 1,950,000 ريال. تفضل في أي حي؟", customer_line="كم سعر الفيلا؟", qual={}, inventory_has_listings=True))


class UnverifiedServices(unittest.TestCase):
    def test_a_promise_of_financing_or_other_services_is_caught(self):
        for answer in ("نقدر نرتب لك تمويل بنكي بسهولة", "عندنا خدمة التمويل العقاري", "نوفر لك نقل الملكية", "we can arrange financing for you", "we offer property management"):
            self.assertTrue(phrasing.unverified_service_claims(answer, ""), answer)
            self.assertIn("unverified_service_claim", phrasing.behaviour_findings(answer, customer_line="هل تساعدون في التمويل؟", qual={}), answer)

    def test_refusals_questions_about_the_customers_own_plans_and_confirmed_services_are_fine(self):
        self.assertEqual(phrasing.unverified_service_claims("ما نقدر نرتب تمويل بنفسنا", ""), [])
        self.assertEqual(phrasing.unverified_service_claims("الدفع كاش أو تمويل بنكي؟", ""), [])
        self.assertEqual(phrasing.unverified_service_claims("الفيلا بسعر 1,950,000 ريال", ""), [])
        self.assertEqual(phrasing.unverified_service_claims("نقدر نرتب لك تمويل", "شركتنا تقدم خدمات التمويل العقاري"), [])           # the office said it offers it

    def test_the_sentence_is_replaced_by_one_hand_over_sentence_and_the_rest_stays(self):
        out = phrasing.polish("الفيلا بسعر 1,950,000 ريال. نقدر نرتب لك تمويل بنكي بسهولة.", customer_text="هل تساعدون في التمويل؟", context="")
        self.assertIn("1,950,000", out)
        self.assertNotIn("نرتب", out)
        self.assertIn("أحد موظفينا يأكد لك تفاصيل", out)

    def test_the_prompt_forbids_promising_unconfirmed_services(self):
        _, text, _, _ = scenario_brief(["هل يقبل تمويل بنكي؟"])
        self.assertIn("لا تعد بأي خدمة", text)
        self.assertIn("ولا تقل «نقدر نرتب»", text)


class OneQuestion(unittest.TestCase):
    def test_only_the_last_question_survives_and_numbers_are_kept(self):
        self.assertEqual(phrasing.limit_questions("أهلًا؟ تحب نرتب معاينة؟ في أي حي تفضل؟"), "في أي حي تفضل؟")
        kept = phrasing.limit_questions("الشقة بسعر 850,000 ريال، تناسبك؟ وفي أي حي تفضّل؟")
        self.assertIn("850,000", kept)
        self.assertEqual(phrasing.limit_questions("سؤال واحد فقط؟"), "سؤال واحد فقط؟")
        self.assertEqual(phrasing.limit_questions("بدون أسئلة."), "بدون أسئلة.")

    def test_polish_applies_it(self):
        out = phrasing.polish("عندك ميزانية؟ وش نوع العقار؟", customer_text="ابغى شقة", context="")
        self.assertEqual(out.count("؟"), 1)

    def test_the_prompt_asks_for_a_single_question_mark(self):
        for texts in (["ابغى شقة"], ["I'm looking for a 3 bedroom apartment under 1.2M"], ["عايز شقة 2 اوضة في التجمع تحت 2 مليون"]):
            self.assertIn("علامة استفهام واحدة", scenario_brief(texts)[1])


class NearestAlternative(unittest.TestCase):
    def test_the_nearest_alternative_is_offered_and_the_prompt_forbids_saying_none(self):
        from scripts.eval_bot import DEMO_LISTINGS
        qual, text, _, result = scenario_brief(["ابغى فيلا في النرجس تحت مليونين"])
        self.assertEqual(result.tier, 3)                                                                       # district dropped, type + budget kept
        self.assertEqual([c["district"] for c in result.listings], ["الياسمين"])
        self.assertIn("1,950,000 ريال", text)
        self.assertIn("ليس مطابقًا تمامًا", text)
        self.assertIn("لا تقل «ما عندنا»", text)
        self.assertTrue(DEMO_LISTINGS)


# ══ ROUND 3: services as a route, pauses as a route, budget question only when it fits, wording, ad number ═══════════════════════════════
class ServiceAndPauseRoutes(unittest.TestCase):
    def test_service_questions_route_to_a_confirmation_in_every_dialect(self):
        from src.sales import intents
        for text in ("هل يقبل تمويل بنكي؟", "تقدرون ترتبون لي تمويل؟", "هل تساعدون في نقل الملكية؟", "هل تقدرون تسوون فحص للعقار؟", "هل في تمويل", "ممكن تساعدوني في التمويل؟",
                     "do you offer financing?", "can you help with the ownership transfer?", "هل عندكم تأمين على العقار؟"):
            self.assertEqual(intents.route_turn(text), "service_question", text)

    def test_a_statement_or_an_answer_about_financing_is_not_a_service_question(self):
        from src.sales import intents
        for text in ("تمويل بنكي", "ادفع كاش", "ابغى تمويل بنكي", "بشتري كاش", "I will pay cash", "ابغى شقة"):
            self.assertEqual(intents.route_turn(text), "qualify", text)

    def test_the_service_reply_is_a_confirmation_with_no_promise_and_no_question(self):
        from src.sales import intents
        reply = intents.REPLIES_AR["service_question"]
        self.assertIn("موظفينا", reply)
        self.assertNotIn("؟", reply)
        self.assertEqual(phrasing.unverified_service_claims(reply, ""), [])
        self.assertEqual(phrasing.behaviour_findings(reply, customer_line="هل يقبل تمويل بنكي؟", qual={}), [])

    def test_the_office_stating_the_service_is_detected(self):
        self.assertTrue(phrasing.office_mentions_service("شركتنا تقدم خدمات التمويل العقاري", "financing"))
        self.assertFalse(phrasing.office_mentions_service("شركة عقارية في الرياض", "financing"))
        self.assertFalse(phrasing.office_mentions_service(None, "financing"))

    def test_pauses_route_to_a_fixed_short_reply(self):
        from src.sales import intents
        for text in ("بفكر وارد لك", "اسأل زوجتي وأرجع لك", "مو مقتنع بالموقع", "بس ادور واتفرج", "let me think about it", "هفكر وارجعلك", "خلني أفكر", "I'll ask my wife and get back to you"):
            self.assertEqual(intents.route_turn(text), "pause", text)
        for text in ("بفكر بس كم سعر الشقة؟", "بفكر، هل في خصم؟", "ابغى اشوفها بكره بس بفكر"):                 # real content in the same line: the model / another rule handles it
            self.assertNotEqual(intents.route_turn(text), "pause", text)

    def test_the_pause_replies_are_short_varied_and_never_a_question(self):
        self.assertGreaterEqual(len(set(phrasing.PAUSE_REPLIES_AR)), 3)
        for reply in phrasing.PAUSE_REPLIES_AR:
            self.assertNotIn("؟", reply)
            self.assertLessEqual(len(reply.split()), 12)


class BudgetQuestionOnlyWhenItFits(unittest.TestCase):
    def test_three_saudi_wordings_and_the_old_script_is_gone(self):
        self.assertEqual(phrasing.QUESTION_VARIANTS_AR["budget"], ("وش ميزانيتك تقريبًا؟", "كم تبي تصرف؟", "وش الحدود اللي تناسبك؟"))
        every = [v for vs in phrasing.QUESTION_VARIANTS_AR.values() for v in vs]
        self.assertNotIn("كم الميزانية التقريبية اللي تفكر فيها؟", every)
        self.assertEqual(q.QUESTIONS_AR["budget"], "وش ميزانيتك تقريبًا؟")

    def test_out_of_scope_lines_are_detected_and_are_not_price_questions(self):
        for text in ("كم سعر الذهب اليوم؟", "what is the gold price today", "وش اخبار الاسهم", "كم سعر الدولار"):
            self.assertTrue(phrasing.is_out_of_scope(text), text)
            self.assertFalse(phrasing.is_price_question(text), text)
        self.assertFalse(phrasing.is_out_of_scope("كم سعر الفيلا؟"))
        self.assertFalse(phrasing.is_out_of_scope("ابغى شقة قريبة من سوق الذهب"))                          # a property word makes it ours
        self.assertTrue(phrasing.is_price_question("كم سعر الفيلا؟"))

    def test_no_question_after_gold_financing_ownership_transfer_or_a_person(self):
        qual = q.qualify(["ابغى شقة"])
        for line in ("كم سعر الذهب اليوم؟", "هل يقبل تمويل بنكي؟", "هل تساعدون في نقل الملكية؟", "تقدرون ترتبون لي تمويل؟"):
            self.assertIsNone(q.next_question(qual, last_text=line), line)
        self.assertIsNotNone(q.next_question(qual, last_text="في النرجس"))

    def test_before_he_says_what_he_wants_the_first_question_is_the_type_not_the_budget(self):
        qual = q.qualify(["السلام عليكم"])
        self.assertEqual(q.next_question(qual)[0], "property_type")
        qual = q.qualify(["ابغى شقة"])
        self.assertEqual(q.next_question(qual)[0], "budget")
        self.assertEqual(q.next_question(q.qualify(["ابغى شقة في النرجس"], known_districts=["النرجس"]))[0], "budget")

    def test_findings_for_a_misplaced_budget_question(self):
        qual = q.qualify(["ابغى شقة"])
        for line in ("كم سعر الذهب اليوم؟", "هل يقبل تمويل بنكي؟", "ابغى اتكلم مع موظف"):
            self.assertIn("budget_question_misplaced", phrasing.behaviour_findings("وش ميزانيتك تقريبًا؟", customer_line=line, qual=qual), line)
        self.assertNotIn("budget_question_misplaced", phrasing.behaviour_findings("وش ميزانيتك تقريبًا؟", customer_line="ابغى شقة", qual=qual))
        self.assertIn("budget_question_misplaced", phrasing.behaviour_findings("وش ميزانيتك تقريبًا؟", customer_line="مرحبا", qual=q.qualify(["مرحبا"])))

    def test_the_budget_phrases_are_banned_and_rewritten(self):
        for bad in ("ما هي الميزانية؟", "ما هو الميزانية اللي تفكر فيها؟", "ما هي ميزانيتك؟"):
            self.assertIn("banned_budget_phrase", phrasing.behaviour_findings(bad, customer_line="ابغى شقة", qual=q.qualify(["ابغى شقة"])), bad)
            self.assertNotRegex(phrasing.fix_grammar(bad), r"ما\s+(?:هي|هو)\s+(?:ال)?ميزاني")
        self.assertEqual(phrasing.fix_grammar("ما هي الميزانية؟"), "وش ميزانيتك؟")

    def test_the_prompt_for_those_lines_has_no_ask_and_says_why(self):
        _, gold, _, _ = scenario_brief(["ابغى شقة", "كم سعر الذهب اليوم؟"])
        self.assertNotIn("في نهاية ردك اسأل", gold)
        self.assertIn("العميل سأل خارج العقارات", gold)
        self.assertNotIn("العميل سأل عن السعر", gold)                                                       # the gold price is not a property price
        _, service, _, _ = scenario_brief(["ابغى شقة", "هل يقبل تمويل بنكي؟"])
        self.assertNotIn("في نهاية ردك اسأل", service)
        self.assertIn("العميل سأل عن خدمة", service)
        _, hello, _, _ = scenario_brief(["السلام عليكم"])
        self.assertIn("«تدوّر على شقة، فيلا، أرض، أو غير ذلك؟»", hello)                                                  # the type first
        self.assertNotIn("«وش ميزانيتك تقريبًا؟»", hello)
        for text in (gold, service, hello):
            self.assertIn("وش ميزانيتك", text)
            self.assertIn("لا تكتب «ما هي الميزانية»", text)


class WordingAndAdNumber(unittest.TestCase):
    DISTRICTS = ["النرجس", "الياسمين"]

    def test_the_type_is_not_glued_to_the_district(self):
        self.assertEqual(phrasing.fix_type_district("عندنا الشقة النرجس بسعر 850,000 ريال والفيلا الياسمين بسعر 1,950,000", self.DISTRICTS),
                         "عندنا شقة النرجس بسعر 850,000 ريال وفيلا الياسمين بسعر 1,950,000")
        self.assertEqual(phrasing.fix_type_district("الشقة في حي النرجس", self.DISTRICTS), "الشقة في حي النرجس")          # correct Arabic is untouched
        self.assertEqual(phrasing.fix_type_district("الشقة الجديدة مناسبة", self.DISTRICTS), "الشقة الجديدة مناسبة")
        self.assertIn("type_district_glued", phrasing.behaviour_findings("عندنا الشقة النرجس", customer_line="ابغى شقة", qual={}, districts=self.DISTRICTS))
        self.assertNotIn("type_district_glued", phrasing.behaviour_findings("عندنا شقة النرجس", customer_line="ابغى شقة", qual={}, districts=self.DISTRICTS))

    def test_the_inventory_line_hands_the_model_a_ready_phrase(self):
        card = {"listing_id": "1", "rega_ad_number": "7100000001", "type": "apartment", "type_ar": "شقة", "city": "الرياض", "district": "النرجس", "price": 850000.0, "area_sqm": None,
                "bedrooms": 3, "bathrooms": None, "map_url": None, "summary": ""}
        block = matcher.format_block(matcher.MatchResult([card], 1, ""))
        self.assertIn("1) شقة في حي النرجس، 850,000 ريال", block)
        self.assertEqual(matcher.districts_in_block(block), {"النرجس"})

    def test_the_ad_number_is_left_out_of_the_first_reply_unless_asked(self):
        card = {"listing_id": "1", "rega_ad_number": "7100000001", "type": "apartment", "type_ar": "شقة", "city": "الرياض", "district": "النرجس", "price": 850000.0, "area_sqm": None,
                "bedrooms": 3, "bathrooms": None, "map_url": None, "summary": ""}
        res = matcher.MatchResult([card], 1, "")
        self.assertIn("7100000001", matcher.format_block(res))
        hidden = matcher.format_block(res, show_ad_number=False)
        self.assertNotIn("7100000001", hidden)
        self.assertIn("لا تذكر رقم إعلان الهيئة", hidden)
        for text in ("ما رقم الإعلان؟", "ابغى رقم الترخيص", "what's the ad number?"):
            self.assertTrue(phrasing.asks_for_ad_number(text), text)
        self.assertFalse(phrasing.asks_for_ad_number("كم السعر؟"))

    def test_polish_strips_an_unprompted_ad_number_only_in_a_first_reply(self):
        answer = "عندنا شقة في حي النرجس بسعر 850,000 ريال، رقم إعلان الهيئة 7100000001، تناسبك؟"
        first = phrasing.polish(answer, customer_text="كم سعر الشقة؟", first_reply=True)
        self.assertNotIn("7100000001", first)
        self.assertIn("850,000", first)
        self.assertIn("7100000001", phrasing.polish(answer, customer_text="كم سعر الشقة؟", first_reply=False))
        self.assertIn("7100000001", phrasing.polish(answer, customer_text="كم سعر الشقة ورقم الإعلان؟", first_reply=True))
        self.assertIn("ad_number_unprompted", phrasing.behaviour_findings(answer, customer_line="ابغى شقة", qual={}, first_contact=True))
        self.assertNotIn("ad_number_unprompted", phrasing.behaviour_findings(answer, customer_line="ما رقم الإعلان؟", qual={}, first_contact=True))


class GoldenRound3(unittest.TestCase):
    def test_every_deterministic_reply_of_the_golden_set_passes_its_checks(self):
        from src.sales import intents
        seen = set()
        for sid, _d, lines, _w, _l in golden.SCENARIOS:
            for text, route in lines:
                if route in ("human_request", "competitor", "pause", "service_question"):
                    seen.add(route)
                    self.assertEqual(phrasing.behaviour_findings(intents.REPLIES_AR[route], customer_line=text, qual={}), [], (sid, route))
        self.assertEqual(seen, {"human_request", "competitor", "pause", "service_question"})

    def test_gold_and_financing_and_a_person_never_get_the_budget_question_in_the_golden_set(self):
        for sid in ("s10_out_of_scope", "s24_financing_question", "s26_human_request", "f01_financing_service", "f02_ownership_transfer", "f03_inspection", "f04_financing_english"):
            lines = next(s[2] for s in golden.SCENARIOS if s[0] == sid)
            texts = [t for t, r in lines]
            qual, text, _first, _res = scenario_brief(texts)
            self.assertEqual(q.next_question(qual, last_text=texts[-1]) is None or sid == "s26_human_request", True, sid)
            self.assertNotIn("«وش ميزانيتك تقريبًا؟»", text, sid)

    def test_gold_is_answered_by_the_model_without_a_question_or_a_listing(self):
        _, text, _, result = scenario_brief(["كم سعر الذهب اليوم؟"])
        self.assertNotIn("في نهاية ردك اسأل", text)
        self.assertTrue(result.listings == [] and result.reason == "need_info")                           # not a price question: first message, nothing known


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

    async def invoke(self, model_text, *, history=(), company="", addendum="سياق"):
        from src.ai_workers.llm_invoker.client import LLMResponse
        from src.sales import hook
        from src.sales.hook import HookResult
        from src.shared.core.config import get_settings
        worker, decision = self.make()
        self.worker = worker
        self.llm.return_value = LLMResponse(text=model_text, model_used="gpt-4o-mini", tier="L1", input_tokens=10, output_tokens=5, total_tokens=15, latency_ms=1, finish_reason="STOP")
        from src.ai_workers.llm_invoker import worker as invoker
        patches = [patch.object(invoker, "get_company_context", AsyncMock(return_value=company)), patch.object(get_settings(), "feature_sales_closing", True), patch.object(hook, "before_llm", AsyncMock(return_value=HookResult(system_addendum=addendum, event="qualified"))),
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

    async def test_the_invoker_replaces_an_unconfirmed_service_promise_and_hands_over(self):
        text = await self.invoke("الفيلا بسعر 1,950,000 ريال. نقدر نرتب لك تمويل بنكي بسهولة.", addendum="عقارات: فيلا بسعر 1,950,000 ريال")
        self.assertNotIn("نرتب", text)
        self.assertIn("أحد موظفينا يأكد لك", text)
        self.assertIn("1,950,000", text)
        self.assertEqual(self.worker._handoff_to_human.await_count, 1)
        self.assertEqual(self.worker._handoff_to_human.await_args.args[1], "unverified_service")

    async def test_a_service_the_office_does_offer_is_left_alone(self):
        text = await self.invoke("نقدر نرتب لك تمويل بنكي بسهولة.", company="شركتنا تقدم خدمات التمويل العقاري")
        self.assertIn("نرتب", text)
        self.worker._handoff_to_human.assert_not_awaited()

    async def test_the_invoker_keeps_one_question_and_fixes_the_grammar(self):
        text = await self.invoke("حياك. تحب نرتب معاينة؟ ما هو الميزانية اللي تفكر فيها؟")
        self.assertEqual(text.count("؟"), 1)
        self.assertNotRegex(text, r"ما\s+(?:هي|هو)\s+(?:ال)?ميزاني")
        self.assertIn("وش ميزانيتك", text)

    async def test_the_invoker_tidies_the_type_the_budget_phrase_and_the_first_reply_ad_number(self):
        addendum = "عقارات: 1) شقة في حي النرجس، 850,000 ريال، رقم إعلان الهيئة 7100000001"
        text = await self.invoke("عندنا الشقة النرجس بسعر 850,000 ريال، رقم إعلان الهيئة 7100000001. ما هي الميزانية اللي تناسبك؟", addendum=addendum)
        self.assertIn("شقة النرجس", text)
        self.assertNotIn("الشقة النرجس", text)
        self.assertNotIn("7100000001", text)                                                        # a first reply nobody asked the ad number of
        self.assertIn("وش ميزانيتك", text)
        self.assertNotRegex(text, r"ما\s+(?:هي|هو)")

    async def test_the_ad_number_stays_in_a_later_reply(self):
        addendum = "عقارات: 1) شقة في حي النرجس، 850,000 ريال، رقم إعلان الهيئة 7100000001"
        history = [{"role": "model", "content": "أهلًا فيك", "sender_type": "ai_bot"}]
        text = await self.invoke("عندنا شقة في حي النرجس بسعر 850,000 ريال، رقم إعلان الهيئة 7100000001.", history=history, addendum=addendum)
        self.assertIn("7100000001", text)

    async def test_the_invoker_drops_a_question_it_already_asked(self):
        history = [{"role": "model", "content": "كم الميزانية التقريبية اللي تفكر فيها؟", "sender_type": "ai_bot"}]
        text = await self.invoke("حاضر. كم الميزانية التقريبية اللي تفكر فيها؟", history=history)
        self.assertNotIn("كم الميزانية", text)


del CreditsBase, InvokerQuotaTests

if __name__ == "__main__":
    unittest.main()

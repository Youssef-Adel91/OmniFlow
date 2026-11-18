"""Same customer message, same answer: the property list comes from a deterministic database filter (type + budget + district + rooms), never from semantic retrieval,
and «غير متوفرة» is said only when that filter finds nothing."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from src.sales import hook, matcher, qualification as q

MESSAGE = "السلام عليكم ابغى شقة ٣ غرف في النرجس تحت مليون كاش"


def card(i, typ, district, price, beds, ad=None):
    return {"listing_id": str(i), "rega_ad_number": ad or f"710000000{i}", "type": typ, "type_ar": matcher.TYPE_AR[typ], "city": "الرياض", "district": district, "price": float(price),
            "area_sqm": None, "bedrooms": beds, "bathrooms": None, "map_url": None, "summary": ""}


INVENTORY = [card(1, "apartment", "النرجس", 850_000, 3), card(2, "villa", "النرجس", 2_400_000, 5), card(3, "villa", "النرجس", 2_600_000, 6),
             card(4, "apartment", "الياسمين", 620_000, 2), card(5, "villa", "الياسمين", 1_950_000, 4)]


def qual(text=MESSAGE):
    return q.qualify([text], known_districts=["النرجس", "الياسمين"])


class DeterministicFilter(unittest.TestCase):
    def test_the_message_always_matches_the_apartment_and_nothing_else(self):
        res = matcher.match_cards(INVENTORY, qual(), require_focus=True)
        self.assertEqual((res.tier, [c["price"] for c in res.listings]), (1, [850_000.0]))

    def test_the_result_does_not_depend_on_the_order_or_extra_inventory(self):
        for inv in (INVENTORY, list(reversed(INVENTORY)), INVENTORY + [card(9, "land", "النرجس", 500_000, None)]):
            res = matcher.match_cards(inv, qual(), require_focus=True)
            self.assertEqual([c["price"] for c in res.listings], [850_000.0])

    def test_nothing_above_the_budget_is_shown_while_something_fits_it(self):
        inv = INVENTORY + [card(6, "apartment", "النرجس", 1_050_000, 3)]            # 5 % above the ceiling, but an in-budget apartment exists
        res = matcher.match_cards(inv, qual(), require_focus=True)
        self.assertEqual([c["price"] for c in res.listings], [850_000.0])

    def test_over_budget_only_when_nothing_fits_the_budget_and_only_within_the_margin(self):
        self.assertEqual(matcher.OVER_BUDGET_FACTOR, 1.10)
        only_dear = [card(7, "apartment", "النرجس", 1_080_000, 3), card(8, "apartment", "النرجس", 1_150_000, 3), card(2, "villa", "النرجس", 900_000, 5)]
        res = matcher.match_cards(only_dear, qual(), require_focus=True)
        self.assertEqual((res.tier, [c["price"] for c in res.listings]), (4, [1_080_000.0]))                # 8 % over: shown; 15 % over: never
        self.assertIn("فوق ميزانيتك قليلًا", res.note)
        none = matcher.match_cards([card(8, "apartment", "النرجس", 1_150_000, 3)], qual(), require_focus=True)
        self.assertEqual(none.listings, [])

    def test_the_reply_built_from_the_match_is_stable_and_labels_over_budget(self):
        res = matcher.match_cards(INVENTORY, qual(), require_focus=True)
        outs = {matcher.render_reply(res, show_ad_number=False) for _ in range(20)}
        self.assertEqual(len(outs), 1)
        text = outs.pop()
        self.assertIn("شقة في حي النرجس، 850,000 ريال", text)
        self.assertNotIn("2,400,000", text)
        self.assertNotIn("غير متوفر", text)
        dear = matcher.match_cards([card(7, "apartment", "النرجس", 1_080_000, 3)], qual(), require_focus=True)
        self.assertIn("فوق ميزانيتك قليلًا", matcher.render_reply(dear))
        self.assertEqual(matcher.render_reply(matcher.MatchResult()), "")

    def test_the_addendum_forbids_saying_unavailable_when_listings_exist_and_requires_it_when_none(self):
        res = matcher.match_cards(INVENTORY, qual(), require_focus=True)
        text = hook.build_addendum(qual(), res, None, last_text=MESSAGE, first_contact=True)
        self.assertIn("ولا تقل «هذه الوحدة غير متوفرة»", text)
        empty = hook.build_addendum(qual(), matcher.MatchResult(), None, last_text=MESSAGE, first_contact=True)
        self.assertIn("هذه هي الحالة الوحيدة التي تقول فيها إنه غير متوفر", empty)

    def test_contradiction_detector(self):
        from src.ai_workers.llm_invoker.worker import LLMInvokerWorker as W
        res = matcher.match_cards(INVENTORY, qual(), require_focus=True)
        self.assertTrue(W._contradicts_inventory("هذه الوحدة غير متوفرة حالياً في قائمتنا المعتمدة", res))
        self.assertTrue(W._contradicts_inventory("عندنا فيلا بسعر 2,400,000 ريال", res))
        self.assertTrue(W._contradicts_inventory("عندنا فيلا بسعر ٢٬٤٠٠٬٠٠٠ ريال", res))
        self.assertFalse(W._contradicts_inventory("عندنا شقة في حي النرجس بسعر 850,000 ريال ورقم الإعلان 7100000001", res))
        self.assertFalse(W._contradicts_inventory("تحب أرتب لك معاينة؟", res))


class HookFailureIsNotSilent(unittest.IsolatedAsyncioTestCase):
    async def test_one_failure_is_retried_two_are_reported(self):
        decision = type("D", (), {"tenant_id": "t"})()
        calls = {"n": 0}

        async def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("blip")
            return hook.HookResult(system_addendum="x", event="qualified")

        with patch.object(hook, "_before_llm", flaky):
            res = await hook.before_llm(decision)
        self.assertEqual((res.event, res.failed, calls["n"]), ("qualified", False, 2))
        with patch.object(hook, "_before_llm", AsyncMock(side_effect=RuntimeError("down"))):
            res = await hook.before_llm(decision)
        self.assertTrue(res.failed)
        self.assertIsNone(res.reply)

    def test_the_unavailable_context_forbids_listings_and_unavailable_claims(self):
        text = hook.INVENTORY_UNAVAILABLE_ADDENDUM
        self.assertIn("لا تعرض أي عقار", text)
        self.assertIn("لا تقل إن الوحدة «غير متوفرة»", text)


try:
    from tests.test_credits import CreditsBase, InvokerQuotaTests
    HAVE_DB = True
except Exception:                                                                                         # pragma: no cover
    CreditsBase = unittest.IsolatedAsyncioTestCase
    InvokerQuotaTests = None
    HAVE_DB = False

VILLAS_TEXT = "## عقارات:\n1) فيلا في حي النرجس بسعر 2,400,000 ريال\n2) فيلا في حي النرجس بسعر 2,600,000 ريال"


def adversarial_model(**kw):
    """What a real model did on +966500000779: it follows whatever the retrieval gave it, and without inventory it says «غير متوفرة»."""
    from src.ai_workers.llm_invoker.client import LLMResponse
    rag = kw.get("rag_context") or ""
    prompt = kw.get("system_prompt") or ""
    if "فيلا" in rag:
        text = "هذه الوحدة غير متوفرة حالياً في قائمتنا المعتمدة، لكن لدينا فيلات في النرجس: فيلا بسعر 2,400,000 ريال وفيلا بسعر 2,600,000 ريال. تحب أعرضها؟"
    elif "شقة في حي النرجس" in prompt:
        text = "عندنا شقة في حي النرجس بسعر 850,000 ريال. تحب أرتب لك معاينة؟"
    else:
        text = "هذه الوحدة غير متوفرة حالياً في قائمتنا المعتمدة."
    return LLMResponse(text=text, model_used="gpt-4o-mini", tier="L2", input_tokens=10, output_tokens=5, total_tokens=15, latency_ms=1, finish_reason="STOP")


@unittest.skipUnless(HAVE_DB, "needs the credits test fixtures (PostgreSQL + Redis)")
class SameMessageSameAnswer(CreditsBase):
    make = InvokerQuotaTests.make if InvokerQuotaTests else None
    run_one = InvokerQuotaTests.run_one if InvokerQuotaTests else None

    async def once(self, rag_mode, *, hook_failed=False):
        from src.ai_workers.llm_invoker import worker as invoker
        from src.shared.core.config import get_settings
        worker, decision = self.make(tier="L2")
        decision.event.text_content = MESSAGE
        worker._rag_enabled = True
        self.llm.side_effect = lambda **kw: adversarial_model(**kw)
        self.rag_calls = []

        async def rag(**kw):
            self.rag_calls.append(kw)
            if rag_mode == "raises":
                raise RuntimeError("qdrant down")
            if rag_mode == "timeout":
                await asyncio.sleep(60)
            if rag_mode == "none":
                return None
            if rag_mode == "empty":
                return ""
            if rag_mode == "villas_respecting_flag":
                return None if kw.get("include_properties") is False else VILLAS_TEXT       # the real retriever with the flag honoured
            return VILLAS_TEXT                                                                # "villas_always": retrieval that ignores the filter entirely

        res = matcher.match_cards(INVENTORY, qual(), require_focus=True)
        hres = hook.HookResult(failed=True, event="hook_failed") if hook_failed else hook.HookResult(
            system_addendum=hook.build_addendum(qual(), res, None, last_text=MESSAGE, first_contact=True, show_ad_number=False), event="qualified", match=res, show_ad_number=False)
        patches = [patch.object(get_settings(), "feature_sales_closing", True), patch.object(hook, "before_llm", AsyncMock(return_value=hres)), patch.object(hook, "after_reply", AsyncMock()),
                   patch.object(invoker.rag_retriever, "get_rag_context", rag), patch.object(invoker, "get_company_context", AsyncMock(return_value=""))]
        if rag_mode == "timeout":
            patches.append(patch.object(invoker.asyncio, "wait_for", self._fast_wait_for()))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        await self.run_one(worker, decision)
        return worker._publish_outbound.await_args.kwargs["text"]

    @staticmethod
    def _fast_wait_for():
        real = asyncio.wait_for

        async def fast(coro, timeout):
            return await real(coro, timeout=0.05)
        return fast

    def assert_apartment_answer(self, text, mode):
        self.assertIn("شقة في حي النرجس", text, mode)
        self.assertIn("850,000", text, mode)
        self.assertNotIn("2,400,000", text, mode)
        self.assertNotIn("2,600,000", text, mode)
        self.assertNotIn("غير متوفر", text, mode)
        self.assertNotIn("فيلا", text, mode)

    async def test_twenty_runs_with_failing_empty_or_wrong_retrieval_always_give_the_apartment(self):
        modes = ["none", "raises", "empty", "timeout", "villas_respecting_flag", "villas_always"] * 4
        outs = []
        for i, mode in enumerate(modes[:20]):
            outs.append((mode, await self.once(mode)))
        self.assertEqual(len(outs), 20)
        for mode, text in outs:
            self.assert_apartment_answer(text, mode)

    async def test_with_a_sales_tenant_the_property_part_of_retrieval_is_switched_off(self):
        await self.once("villas_respecting_flag")
        self.assertTrue(self.rag_calls)
        self.assertTrue(all(c.get("include_properties") is False for c in self.rag_calls))

    async def test_when_the_hook_fails_the_model_is_told_the_inventory_is_unavailable_and_no_property_retrieval_runs(self):
        await self.once("villas_respecting_flag", hook_failed=True)
        self.assertTrue(all(c.get("include_properties") is False for c in self.rag_calls))
        prompt = self.llm.await_args.kwargs["system_prompt"]
        self.assertIn("تعذّر الآن الوصول إلى مخزون العقارات", prompt)

    async def test_a_guard_block_is_answered_from_the_match_not_the_generic_fallback(self):
        from src.ai_workers.llm_invoker import client
        from src.ai_workers.llm_invoker.client import LLMResponse
        worker, decision = self.make(tier="L1")
        decision.event.text_content = MESSAGE
        self.llm.return_value = LLMResponse(text="عندنا شقة بسعر 850,000 ريال ونعطيك خصم 20%", model_used="gpt-4o-mini", tier="L1", input_tokens=10, output_tokens=5, total_tokens=15, latency_ms=1, finish_reason="STOP")
        res = matcher.match_cards(INVENTORY, qual(), require_focus=True)
        hres = hook.HookResult(system_addendum=hook.build_addendum(qual(), res, None, last_text=MESSAGE, first_contact=True, show_ad_number=False), event="qualified", match=res, show_ad_number=False)
        from src.ai_workers.llm_invoker import worker as invoker
        from src.shared.core.config import get_settings
        for p in (patch.object(get_settings(), "feature_sales_closing", True), patch.object(hook, "before_llm", AsyncMock(return_value=hres)), patch.object(hook, "after_reply", AsyncMock()),
                  patch.object(invoker, "get_company_context", AsyncMock(return_value=""))):
            p.start()
            self.addCleanup(p.stop)
        await self.run_one(worker, decision)
        text = worker._publish_outbound.await_args.kwargs["text"]
        self.assertIn("شقة في حي النرجس، 850,000 ريال", text)
        self.assertNotIn("خصم", text)


if __name__ == "__main__":
    unittest.main()

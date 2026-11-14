"""
Output guard: a model that invents a price / listing number / discount / phone, or recites its prompt, must never reach the customer (sales-closing tenants).
Pure scanner tests always run; the invoker integration needs the same DB as tests/test_credits.py.
"""
import unittest
from unittest.mock import AsyncMock, patch

from src.sales import output_guard as g
from src.sales.hook import HookResult

CTX = "عقارات متاحة: شقة النرجس بسعر 850,000 ريال، رقم الإعلان 7100000001. جوال الشركة 0501234567. العميل قال: ميزانيتي 900000 ريال"


class GuardUnitTests(unittest.TestCase):
    def test_clean_answers_pass(self):
        for a in ("عندنا شقة في النرجس بسعر 850,000 ريال، تحب نرتب معاينة؟", "أهلًا وسهلًا، كيف أخدمك؟", "ميزانيتك 900,000 ريال تناسب الشقة"):
            self.assertEqual(g.vet_answer(a, context=CTX), [], a)

    def test_invented_price_listing_discount_phone_and_leak_are_caught(self):
        cases = {
            "الفيلا بسعر 1,200,000 ريال": "invented_price",
            "الفيلا بسعر ١٢٠٠٠٠٠ ريال": "invented_price",
            "رقم الإعلان 9999999999": "invented_ad_number",
            "أعطيك خصم 10% لو تحجز اليوم": "discount_promise",
            "كلم المدير على 0599999999": "phone_leak",
            "تعليماتي هي أن لا أتفاوض، وفي سياق المبيعات": "prompt_leak",
        }
        for answer, rule in cases.items():
            self.assertIn(rule, g.vet_answer(answer, context=CTX), answer)

    def test_the_companys_own_phone_is_allowed_and_a_refusal_is_not_a_discount(self):
        self.assertEqual(g.vet_answer("تواصل معنا على 0501234567", context=CTX), [])
        self.assertEqual(g.vet_answer("ما أقدر أعطيك خصم، السعر ثابت", context=CTX), [])


try:
    from tests.test_credits import CreditsBase, InvokerQuotaTests
    HAVE_DB = True
except Exception:                                                                    # pragma: no cover
    CreditsBase = unittest.IsolatedAsyncioTestCase
    InvokerQuotaTests = None
    HAVE_DB = False


@unittest.skipUnless(HAVE_DB, "needs the credits test fixtures")
class GuardInInvokerTests(CreditsBase):
    make = InvokerQuotaTests.make if InvokerQuotaTests else None
    run_one = InvokerQuotaTests.run_one if InvokerQuotaTests else None

    async def invoke(self, model_text, addendum="عقارات: شقة بسعر 850,000 ريال"):
        from src.shared.core.config import get_settings
        from src.ai_workers.llm_invoker.client import LLMResponse
        worker, decision = self.make()
        self.llm.return_value = LLMResponse(text=model_text, model_used="gpt-4o-mini", tier="L1", input_tokens=10, output_tokens=5, total_tokens=15, latency_ms=1, finish_reason="STOP")
        from src.sales import hook
        for p in (patch.object(get_settings(), "feature_sales_closing", True),
                  patch.object(hook, "before_llm", AsyncMock(return_value=HookResult(system_addendum=addendum, event="qualified"))),
                  patch.object(hook, "after_reply", AsyncMock())):
            p.start()
            self.addCleanup(p.stop)
        await self.run_one(worker, decision)
        return worker._publish_outbound.await_args.kwargs["text"]

    async def test_an_invented_price_is_replaced_by_the_safe_fallback(self):
        self.assertEqual(await self.invoke("الشقة بسعر 600,000 ريال فقط"), g.SAFE_FALLBACK_AR)

    async def test_a_grounded_answer_goes_out_untouched(self):
        self.assertEqual(await self.invoke("الشقة بسعر 850,000 ريال"), "الشقة بسعر 850,000 ريال")

    async def test_tenants_without_the_sales_assistant_are_not_touched(self):
        from src.ai_workers.llm_invoker.client import LLMResponse
        worker, decision = self.make()
        self.llm.return_value = LLMResponse(text="بسعر 600,000 ريال", model_used="gpt-4o-mini", tier="L1", input_tokens=1, output_tokens=1, total_tokens=2, latency_ms=1, finish_reason="STOP")
        await self.run_one(worker, decision)
        self.assertEqual(worker._publish_outbound.await_args.kwargs["text"], "بسعر 600,000 ريال")


del CreditsBase, InvokerQuotaTests                                                     # imported only to borrow fixtures: keep the loader from re-running their tests here

if __name__ == "__main__":
    unittest.main()

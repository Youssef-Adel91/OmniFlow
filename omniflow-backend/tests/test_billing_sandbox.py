"""The sandbox payment provider (demo / dev / simulation) and the plan labels a new tenant sees."""
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from src.gateway.routers import billing as billing_router
from src.gateway.routers.settings import _to_settings
from src.shared.core.config import get_settings
from src.shared.services.billing.providers import get_provider
from src.shared.services.billing.providers.base import ProviderError, WebhookRejected
from src.shared.services.billing.providers.sandbox import SandboxProvider


class SandboxProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_checkout_succeeds_at_once_and_every_lookup_is_a_paid_payment(self):
        p = SandboxProvider()
        co = await p.create_checkout(transaction_id="t-1", amount_halalas=34385, currency="SAR", description="x", success_url="https://app/ok?tx=t-1", back_url="b", metadata={})
        self.assertEqual(co.url, "https://app/ok?tx=t-1")                                # no hosted page: straight back to the app
        (pay,) = await p.fetch_invoice_payments(co.provider_invoice_id)
        self.assertEqual((pay.status, pay.amount_halalas, pay.currency, pay.transaction_id), ("paid", 34385, "SAR", "t-1"))
        again = await p.fetch_payment(pay.payment_id)                                    # stateless: a different process / a restart answers the same
        self.assertEqual((again.status, again.amount_halalas), ("paid", 34385))
        self.assertEqual((pay.card_brand, pay.card_last4), ("visa", "4242"))             # a fake saved card, so auto top-up / renewals can be exercised

    async def test_token_charge_and_refund_work_and_garbage_ids_are_not_payments(self):
        p = SandboxProvider()
        ch = await p.charge_token(token="sbx_tok_4242", transaction_id="t-2", amount_halalas=100, currency="SAR", description="x", metadata={})    # pragma: allowlist secret
        self.assertEqual(ch.status, "paid")
        ref = await p.refund_payment(payment_id=ch.payment_id, amount_halalas=40, reason="test")
        self.assertEqual((ref.status, ref.amount_halalas), ("refunded", 40))
        self.assertEqual(await p.fetch_invoice_payments("moyasar-invoice-123"), [])
        with self.assertRaises(ProviderError):
            await p.fetch_payment("pay_real_moyasar_id")

    async def test_it_sends_no_webhooks(self):
        with self.assertRaises(WebhookRejected):
            SandboxProvider().parse_webhook({}, b"{}")

    async def test_it_refuses_to_run_in_production(self):
        with patch.object(get_settings(), "app_env", "production"):
            with self.assertRaises(ProviderError) as cm:
                await SandboxProvider().create_checkout(transaction_id="t", amount_halalas=1, currency="SAR", description="x", success_url="u", back_url="b", metadata={})
            self.assertEqual(cm.exception.code, "sandbox_in_production")
            self.assertFalse(billing_router._payments_enabled())

    def test_the_factory_and_the_payments_switch_know_it(self):
        with patch.object(get_settings(), "payment_provider", "sandbox"), patch.object(get_settings(), "app_env", "development"):
            self.assertIsInstance(get_provider(), SandboxProvider)
            self.assertTrue(billing_router._payments_enabled())


class LegacyTierLabelTests(unittest.TestCase):
    def tenant(self, tier):
        return SimpleNamespace(tenant_id=uuid.uuid4(), business_name="x", subscription_tier=SimpleNamespace(value=tier), whatsapp_phone_number_id=None, max_ai_conversations=100,
                               meta_access_token=None, ai_system_prompt=None, custom_ai_instructions=None, logo_url=None, whatsapp_display_phone_number=None)

    def test_a_new_tenant_never_sees_the_placeholder_economic_plan(self):
        for stored, shown in (("economic", "starter"), ("professional", "pro"), ("starter", "starter"), ("pro", "pro"), ("business", "business"), ("enterprise", "enterprise")):
            self.assertEqual(_to_settings(self.tenant(stored)).subscription_tier, shown, stored)


if __name__ == "__main__":
    unittest.main()

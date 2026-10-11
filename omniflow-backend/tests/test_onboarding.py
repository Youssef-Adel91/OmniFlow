"""Onboarding checklist logic + WhatsApp Embedded Signup (flagged). Meta is simulated with httpx.MockTransport — a local simulation, NOT a call to Meta."""
import json
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from src.gateway.routers import onboarding as ob
from src.gateway.routers import whatsapp_signup as ws
from src.shared.core.enums import TenantUserRole
from src.shared.security.crypto import decrypt_secret, reveal_secret, encrypt_secret


class ChecklistTests(unittest.TestCase):
    def test_empty_workspace_has_nothing_done(self):
        steps = ob.compute_steps(has_company=False, has_persona=False, properties=0, wa=False, fb=False, bot_tested=False)
        self.assertEqual([s["key"] for s in steps], list(ob.STEP_ORDER))
        self.assertFalse(any(s["done"] for s in steps))

    def test_each_step_reflects_its_own_signal(self):
        base = dict(has_company=False, has_persona=False, properties=0, wa=False, fb=False, bot_tested=False)
        for key, override in {"company": {"has_company": True}, "persona": {"has_persona": True}, "properties": {"properties": 3},
                              "bot_test": {"bot_tested": True}}.items():
            done = {s["key"]: s["done"] for s in ob.compute_steps(**{**base, **override})}
            self.assertEqual([k for k, v in done.items() if v], [key])

    def test_either_channel_completes_channel_step(self):
        base = dict(has_company=False, has_persona=False, properties=0, bot_tested=False)
        for wa, fb in ((True, False), (False, True)):
            done = {s["key"]: s["done"] for s in ob.compute_steps(wa=wa, fb=fb, **base)}
            self.assertTrue(done["channel"])


class RevealSecretTests(unittest.TestCase):
    def test_reads_both_ciphertext_and_legacy_plaintext(self):
        self.assertEqual(reveal_secret(encrypt_secret("EAAB-token")), "EAAB-token")
        self.assertEqual(reveal_secret("legacy-plaintext"), "legacy-plaintext")
        self.assertIsNone(reveal_secret(None))
        self.assertIsNone(reveal_secret(""))


def meta_transport(calls, *, phones=None, fail_on=None):
    phones = phones if phones is not None else [{"id": "555111", "display_phone_number": "+966 50 000 0000"}]

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path.split("/", 2)[2]
        calls.append((req.method, path, dict(req.url.params), req.content.decode() if req.content else ""))
        if fail_on and fail_on in path:
            return httpx.Response(400, json={"error": {"message": f"boom {fail_on}"}})
        if path == "oauth/access_token":
            return httpx.Response(200, json={"access_token": "biz-token-123"})
        if path.endswith("/phone_numbers"):
            return httpx.Response(200, json={"data": phones})
        return httpx.Response(200, json={"success": True})
    return httpx.MockTransport(handler)


class ProvisionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for p in (patch.object(ws.settings, "meta_app_id", "123"), patch.object(ws.settings, "meta_app_secret", "appsecret")):
            p.start(); self.addCleanup(p.stop)

    async def test_happy_path_calls_meta_in_order(self):
        calls = []
        async with httpx.AsyncClient(transport=meta_transport(calls)) as c:
            out = await ws.provision(c, code="abc123", waba_id="999888", phone_number_id="555111")
        self.assertEqual(out, {"token": "biz-token-123", "display_phone_number": "+966 50 000 0000"})
        self.assertEqual([(m, p) for m, p, *_ in calls], [("GET", "oauth/access_token"), ("GET", "999888/phone_numbers"), ("POST", "555111/register"), ("POST", "999888/subscribed_apps")])
        self.assertEqual(calls[0][2]["code"], "abc123")
        self.assertEqual(json.loads(calls[2][3])["pin"], ws.registration_pin("555111"))

    async def test_number_from_another_waba_is_rejected_before_registering(self):
        calls = []
        async with httpx.AsyncClient(transport=meta_transport(calls, phones=[{"id": "other", "display_phone_number": "x"}])) as c:
            with self.assertRaises(ws.SignupError):
                await ws.provision(c, code="abc123", waba_id="999888", phone_number_id="555111")
        self.assertFalse(any(m == "POST" for m, *_ in calls))

    async def test_meta_error_surfaces_message(self):
        async with httpx.AsyncClient(transport=meta_transport([], fail_on="register")) as c:
            with self.assertRaises(ws.SignupError) as cm:
                await ws.provision(c, code="abc123", waba_id="999888", phone_number_id="555111")
        self.assertIn("boom", str(cm.exception))

    def test_pin_is_six_digits_and_stable(self):
        pin = ws.registration_pin("555111")
        self.assertRegex(pin, r"^\d{6}$")
        self.assertEqual(pin, ws.registration_pin("555111"))
        self.assertNotEqual(pin, ws.registration_pin("555112"))


class FakeSession:
    def __init__(self, tenant, other=None):
        self.tenant, self.other, self.flushed = tenant, other, False
        self._n = 0

    async def scalar(self, stmt):
        self._n += 1
        return self.tenant if self._n == 1 else self.other      # 1st lookup = own tenant, 2nd = "number taken?" check

    async def flush(self):
        self.flushed = True


class EndpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tenant = SimpleNamespace(meta_access_token=None, whatsapp_phone_number_id=None, whatsapp_waba_id=None, whatsapp_display_phone_number=None)
        self.user = SimpleNamespace(tenant_id=uuid.uuid4(), role=TenantUserRole.ADMIN)
        for p in (patch.object(ws.settings, "meta_app_id", "123"), patch.object(ws.settings, "meta_app_secret", "appsecret"), patch.object(ws.settings, "meta_es_config_id", "cfg1")):
            p.start(); self.addCleanup(p.stop)

    def _body(self):
        return ws.SignupComplete(code="abc123", waba_id="999888", phone_number_id="555111")

    async def test_everything_404s_while_flag_off(self):
        with patch.object(ws.settings, "feature_whatsapp_embedded_signup", False):
            for call in (ws.signup_config(self.user), ws.signup_complete(self._body(), self.user, FakeSession(self.tenant))):
                with self.assertRaises(ws.HTTPException) as cm:
                    await call
                self.assertEqual(cm.exception.status_code, 404)

    async def test_config_exposes_only_public_ids(self):
        with patch.object(ws.settings, "feature_whatsapp_embedded_signup", True):
            cfg = await ws.signup_config(self.user)
        self.assertEqual((cfg.enabled, cfg.app_id, cfg.config_id), (True, "123", "cfg1"))
        self.assertNotIn("appsecret", cfg.model_dump_json())

    async def test_only_admin_can_complete(self):
        self.user.role = TenantUserRole.AGENT
        with patch.object(ws.settings, "feature_whatsapp_embedded_signup", True):
            with self.assertRaises(ws.HTTPException) as cm:
                await ws.signup_complete(self._body(), self.user, FakeSession(self.tenant))
        self.assertEqual(cm.exception.status_code, 403)

    async def _complete(self, session, **meta):
        real = httpx.AsyncClient
        transport = meta_transport([], **meta)
        with patch.object(ws.settings, "feature_whatsapp_embedded_signup", True), \
                patch.object(ws.httpx, "AsyncClient", lambda **kw: real(transport=transport)):
            return await ws.signup_complete(self._body(), self.user, session)

    async def test_complete_stores_token_encrypted_and_ids(self):
        session = FakeSession(self.tenant)
        out = await self._complete(session)
        self.assertTrue(out["connected"])
        self.assertNotIn("biz-token-123", self.tenant.meta_access_token)
        self.assertEqual(decrypt_secret(self.tenant.meta_access_token), "biz-token-123")
        self.assertEqual((self.tenant.whatsapp_phone_number_id, self.tenant.whatsapp_waba_id), ("555111", "999888"))
        self.assertTrue(session.flushed)

    async def test_number_used_by_another_workspace_is_409_and_nothing_is_stored(self):
        with self.assertRaises(ws.HTTPException) as cm:
            await self._complete(FakeSession(self.tenant, other=uuid.uuid4()))
        self.assertEqual(cm.exception.status_code, 409)
        self.assertIsNone(self.tenant.meta_access_token)

    async def test_meta_failure_is_502_and_stores_nothing(self):
        with self.assertRaises(ws.HTTPException) as cm:
            await self._complete(FakeSession(self.tenant), fail_on="subscribed_apps")
        self.assertEqual(cm.exception.status_code, 502)
        self.assertIsNone(self.tenant.meta_access_token)


if __name__ == "__main__":
    unittest.main()

"""Authentication and tenant isolation regressions (no network or database)."""
import base64
import unittest
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import FastAPI, HTTPException
from jose import JWTError

from src.gateway import dependencies
from src.gateway.routers import conversations
from src.shared.security import jwt


class TenantIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_header_cannot_select_another_tenant_or_system_context(self):
        tenant_id = uuid.uuid4()
        user = SimpleNamespace(tenant_id=tenant_id)
        seen = []

        @asynccontextmanager
        async def session(tenant):
            seen.append(tenant)
            yield SimpleNamespace()

        app = FastAPI()
        app.dependency_overrides[dependencies.get_current_user] = lambda: user

        @app.get("/probe")
        async def probe(repo: dependencies.ConversationRepo):
            return {"ok": True}

        with patch.object(dependencies, "get_tenant_session", session):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                for forged in (str(uuid.uuid4()), str(uuid.UUID(int=0)), "invalid"):
                    response = await client.get("/probe", headers={"X-Tenant-ID": forged})
                    self.assertEqual(response.status_code, 200)
        self.assertEqual(seen, [tenant_id] * 3)

    async def test_system_context_is_not_a_dashboard_tenant(self):
        with self.assertRaises(HTTPException) as error:
            await dependencies.get_tenant_id(SimpleNamespace(tenant_id=uuid.UUID(int=0)))
        self.assertEqual(error.exception.status_code, 403)

    async def test_sse_rejects_bare_tenant_even_in_development(self):
        self.assertIsNone(await conversations._resolve_tenant_from_request(None, str(uuid.uuid4())))

    async def test_sse_uses_rest_authentication_and_rejects_revoked_sessions(self):
        with patch.object(conversations, "get_current_user", AsyncMock(side_effect=HTTPException(401))):
            self.assertIsNone(await conversations._resolve_tenant_from_request("revoked-token", None))


class ClerkIssuerTests(unittest.TestCase):
    def test_untrusted_issuer_never_fetches_keys(self):
        with patch.object(jwt.pyjwt, "decode", return_value={"iss": "https://attacker.invalid"}), \
                patch.object(jwt, "PyJWKClient") as client:
            with self.assertRaises(JWTError):
                jwt.verify_clerk_token("forged-token")
            client.assert_not_called()

    def test_trusted_token_requires_expiry_and_issuer(self):
        issuer = "https://test.clerk.accounts.dev"
        jwt._jwk_clients.clear()
        claims = {"iss": issuer, "sub": "user_test", "exp": 9999999999, "iat": 1}
        with patch.object(jwt.pyjwt, "decode", side_effect=[claims, claims]) as decode, \
                patch.object(jwt, "PyJWKClient", return_value=MagicMock()):
            self.assertEqual(jwt.verify_clerk_token("signed-token"), claims)
            self.assertEqual(decode.call_args.kwargs["issuer"], issuer)
            self.assertIn("exp", decode.call_args.kwargs["options"]["require"])

    def test_publishable_key_can_resolve_configured_issuer(self):
        domain = "sample.clerk.accounts.dev"
        encoded = base64.b64encode((domain + "$ ".strip()).encode()).decode()
        with patch.object(jwt._settings, "clerk_issuer_url", ""), \
                patch.object(jwt._settings, "clerk_publishable_key", "pk_test_" + encoded):
            self.assertEqual(jwt._clerk_issuer(), "https://" + domain)


class ClerkTokenLeewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        cls.rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.priv_pem = cls.rsa_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        cls.pub_pem = cls.rsa_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        cls.issuer = "https://test.clerk.accounts.dev"

    def _sign_token(self, payload):
        import jwt as pyjwt
        return pyjwt.encode(payload, self.priv_pem, algorithm="RS256")

    def _mock_jwk_client(self):
        signing_key_mock = MagicMock()
        signing_key_mock.key = self.pub_pem
        jwks_client_mock = MagicMock()
        jwks_client_mock.get_signing_key_from_jwt.return_value = signing_key_mock
        return jwks_client_mock

    def test_token_with_future_iat_within_leeway_accepted(self):
        import time
        now = int(time.time())
        token = self._sign_token({
            "iss": self.issuer,
            "sub": "user_future_10",
            "exp": now + 3600,
            "iat": now + 10,
        })
        with patch.object(jwt._settings, "clerk_issuer_url", self.issuer), \
                patch.dict(jwt._jwk_clients, {f"{self.issuer}/.well-known/jwks.json": self._mock_jwk_client()}):
            payload = jwt.verify_clerk_token(token, leeway=30)
            self.assertEqual(payload["sub"], "user_future_10")

    def test_token_with_future_iat_beyond_leeway_rejected(self):
        import time
        now = int(time.time())
        token = self._sign_token({
            "iss": self.issuer,
            "sub": "user_future_300",
            "exp": now + 3600,
            "iat": now + 300,
        })
        with patch.object(jwt._settings, "clerk_issuer_url", self.issuer), \
                patch.dict(jwt._jwk_clients, {f"{self.issuer}/.well-known/jwks.json": self._mock_jwk_client()}):
            with self.assertRaises(JWTError) as ctx:
                jwt.verify_clerk_token(token, leeway=30)
            self.assertIn("The token is not yet valid", str(ctx.exception))

    def test_expired_token_rejected(self):
        import time
        now = int(time.time())
        token = self._sign_token({
            "iss": self.issuer,
            "sub": "user_expired",
            "exp": now - 60,
            "iat": now - 120,
        })
        with patch.object(jwt._settings, "clerk_issuer_url", self.issuer), \
                patch.dict(jwt._jwk_clients, {f"{self.issuer}/.well-known/jwks.json": self._mock_jwk_client()}):
            with self.assertRaises(JWTError) as ctx:
                jwt.verify_clerk_token(token, leeway=30)
            self.assertIn("Signature has expired", str(ctx.exception))

    def test_token_with_different_iss_rejected(self):
        import time
        now = int(time.time())
        token = self._sign_token({
            "iss": "https://different.clerk.accounts.dev",
            "sub": "user_wrong_iss",
            "exp": now + 3600,
            "iat": now,
        })
        with patch.object(jwt._settings, "clerk_issuer_url", self.issuer):
            with self.assertRaises(JWTError) as ctx:
                jwt.verify_clerk_token(token, leeway=30)
            self.assertIn("Unexpected token issuer", str(ctx.exception))

    def test_default_leeway_from_settings(self):
        import time
        now = int(time.time())
        token = self._sign_token({
            "iss": self.issuer,
            "sub": "user_default_leeway",
            "exp": now + 3600,
            "iat": now + 10,
        })
        with patch.object(jwt._settings, "clerk_issuer_url", self.issuer), \
                patch.object(jwt._settings, "clerk_jwt_leeway_seconds", 30), \
                patch.dict(jwt._jwk_clients, {f"{self.issuer}/.well-known/jwks.json": self._mock_jwk_client()}):
            payload = jwt.verify_clerk_token(token)
            self.assertEqual(payload["sub"], "user_default_leeway")


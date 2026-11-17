"""
GET /api/v1/stream/dashboard?token=<jwt> (the inbox's live feed).

Regression: the router was mounted behind the subscription write gate, which resolves the user from the `Authorization` header — the browser's EventSource cannot send
headers, so EVERY stream answered 401 and the inbox showed "connection error". The stream now sits outside the gate and authenticates itself from `?token=`;
the gate still blocks writes of a blocked subscription.
"""
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from src.gateway import dependencies as deps
from src.gateway.main import create_app
from src.gateway.routers import conversations as conv


async def one_event(request, channel):                       # replaces the Redis pub/sub loop: one frame, then the stream ends
    yield 'event: ping\ndata: {}\n\n'


class StreamAuthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = create_app()
        self.tenant = uuid.uuid4()

    def client(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://t", timeout=10)

    async def test_a_valid_token_in_the_query_gets_an_event_stream_without_any_authorization_header(self):
        user = SimpleNamespace(tenant_id=self.tenant, user_id=uuid.uuid4())
        with patch.object(conv, "get_current_user", AsyncMock(return_value=user)) as verify, patch.object(conv, "_sse_generator", one_event):
            async with self.client() as c:
                async with c.stream("GET", "/api/v1/stream/dashboard", params={"token": "a.valid.jwt"}) as r:
                    self.assertEqual(r.status_code, 200)
                    self.assertTrue(r.headers["content-type"].startswith("text/event-stream"))
                    body = (await r.aread()).decode()
        self.assertIn("event: ping", body)
        creds = verify.await_args.args[0]
        self.assertEqual(creds.credentials, "a.valid.jwt")                                   # the token from the query went through the real verification path

    async def test_an_invalid_missing_or_forged_token_is_401(self):
        async def reject(credentials):
            raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED"})

        with patch.object(conv, "get_current_user", reject):
            async with self.client() as c:
                for params in ({}, {"token": "garbage"}, {"tenant_id": str(uuid.uuid4())}, {"token": "garbage", "tenant_id": str(uuid.uuid4())}):
                    self.assertEqual((await c.get("/api/v1/stream/dashboard", params=params)).status_code, 401, params)

    async def test_the_subscription_gate_still_blocks_writes_when_the_subscription_is_blocked(self):
        user = SimpleNamespace(tenant_id=self.tenant, user_id=uuid.uuid4(), role="admin")
        self.app.dependency_overrides[deps.get_current_user] = lambda: user
        with patch("src.shared.services.billing.entitlements.writes_allowed", AsyncMock(return_value=(False, "trial_expired"))):
            async with self.client() as c:
                for method, url in (("POST", f"/api/v1/conversations/{uuid.uuid4()}/messages"), ("PUT", f"/api/v1/conversations/{uuid.uuid4()}/messages"),
                                    ("PATCH", f"/api/v1/conversations/{uuid.uuid4()}/takeover"), ("DELETE", f"/api/v1/conversations/{uuid.uuid4()}")):
                    r = await c.request(method, url, json={})
                    if r.status_code == 404 or r.status_code == 405:                       # no such write route: the gate only guards routes that exist
                        continue
                    self.assertEqual(r.status_code, 402, (method, url, r.text))
                    self.assertEqual(r.json()["detail"]["code"], "SUBSCRIPTION_INACTIVE")
                r = await c.post(f"/api/v1/conversations/{uuid.uuid4()}/takeover")
                self.assertEqual(r.status_code, 402, r.text)                              # a real write route of the gated router

    async def test_reads_of_the_gated_router_still_need_authentication(self):
        async with self.client() as c:
            self.assertEqual((await c.get("/api/v1/conversations")).status_code, 401)


if __name__ == "__main__":
    unittest.main()

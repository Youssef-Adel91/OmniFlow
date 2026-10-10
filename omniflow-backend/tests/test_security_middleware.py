"""Security headers, rate limiting and CORS hardening."""
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from src.gateway import security_middleware as sm
from src.gateway.main import create_app
from src.shared.core.config import get_settings


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class ClassifyTests(unittest.TestCase):
    def test_which_paths_are_limited(self):
        c = sm.classify
        self.assertEqual(c("/api/v1/customers", "GET"), "api")
        self.assertEqual(c("/api/v1/billing/checkout", "POST"), "api")
        self.assertEqual(c("/api/v1/webhooks/payments", "POST"), "hook")
        self.assertEqual(c("/api/v1/webhooks/clerk", "POST"), "hook")
        for exempt in ("/health", "/health/ready", "/api/v1/conversations/stream", "/api/v1/whatsapp/webhook",
                       "/api/v1/instagram/webhook", "/api/v1/webhooks/meta", "/docs"):
            self.assertIsNone(c(exempt, "POST"), exempt)
        self.assertIsNone(c("/api/v1/customers", "OPTIONS"))


class MiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_security_headers_on_every_response_and_hsts_only_in_production(self):
        async with client(create_app()) as c:
            r = await c.get("/health")
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertEqual(r.headers["x-frame-options"], "DENY")
        self.assertIn("referrer-policy", r.headers)
        self.assertNotIn("strict-transport-security", r.headers)
        with patch.object(get_settings(), "app_env", "production"):
            async with client(create_app()) as c:
                r = await c.get("/health")
        self.assertIn("max-age=63072000", r.headers["strict-transport-security"])

    async def test_api_responses_are_not_cacheable(self):
        async with client(create_app()) as c:
            r = await c.get("/api/v1/billing/status")          # 401 without a token — headers still apply
        self.assertEqual(r.headers["cache-control"], "no-store")

    async def test_rate_limit_returns_429_with_retry_after_and_keeps_cors_headers(self):
        counter = {"n": 0}

        async def incr(key, ttl):
            counter["n"] += 1
            return counter["n"]
        with patch.object(sm.redis_mgr, "incr_counter", side_effect=incr), patch.object(get_settings(), "rate_limit_api_per_min", 3):
            async with client(create_app()) as c:
                codes = [(await c.get("/api/v1/billing/status", headers={"Authorization": "Bearer abc", "Origin": "http://localhost:3000"})) for _ in range(5)]
        self.assertEqual([r.status_code for r in codes], [401, 401, 401, 429, 429])
        self.assertTrue(codes[3].headers["retry-after"].isdigit())
        self.assertEqual(codes[3].json()["detail"]["code"], "RATE_LIMITED")
        self.assertEqual(codes[3].headers.get("access-control-allow-origin"), "http://localhost:3000")

    async def test_buckets_are_per_token_and_webhooks_use_the_ip_and_meta_is_never_limited(self):
        keys = []

        async def incr(key, ttl):
            keys.append(key)
            return 10_000
        with patch.object(sm.redis_mgr, "incr_counter", side_effect=incr):
            async with client(create_app()) as c:
                a = await c.get("/api/v1/billing/status", headers={"Authorization": "Bearer one"})
                b = await c.get("/api/v1/billing/status", headers={"Authorization": "Bearer two"})
                hook = await c.post("/api/v1/webhooks/payments", json={})
                meta = await c.post("/api/v1/whatsapp/webhook", json={})
                health = await c.get("/health")
        self.assertEqual((a.status_code, b.status_code, hook.status_code), (429, 429, 429))
        self.assertNotEqual(meta.status_code, 429)
        self.assertNotEqual(health.status_code, 429)
        self.assertEqual(len(keys), 3)
        self.assertNotEqual(keys[0], keys[1])
        self.assertNotIn("one", keys[0])                                    # the token itself is never stored
        self.assertTrue(keys[2].startswith("rl:hook:ip:"))

    async def test_redis_outage_fails_open(self):
        with patch.object(sm.redis_mgr, "incr_counter", AsyncMock(return_value=None)):
            async with client(create_app()) as c:
                r = await c.get("/api/v1/billing/status", headers={"Authorization": "Bearer abc"})
        self.assertEqual(r.status_code, 401)

    async def test_rate_limit_can_be_switched_off(self):
        with patch.object(sm.redis_mgr, "incr_counter", AsyncMock(return_value=99999)), patch.object(get_settings(), "rate_limit_enabled", False):
            async with client(create_app()) as c:
                r = await c.get("/api/v1/billing/status", headers={"Authorization": "Bearer abc"})
        self.assertEqual(r.status_code, 401)

    async def test_production_cors_does_not_allow_localhost(self):
        s = get_settings()
        with patch.object(s, "app_env", "production"), patch.object(type(s), "allowed_origins_list", property(lambda self: ["https://app.example.com"])):
            async with client(create_app()) as c:
                ok = await c.options("/api/v1/customers", headers={"Origin": "https://app.example.com", "Access-Control-Request-Method": "GET"})
                bad = await c.options("/api/v1/customers", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"})
        self.assertEqual(ok.headers.get("access-control-allow-origin"), "https://app.example.com")
        self.assertIsNone(bad.headers.get("access-control-allow-origin"))


if __name__ == "__main__":
    unittest.main()


class WorkerHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_workers_endpoint_reports_missing_heartbeats_without_failing_readiness(self):
        from src.gateway.routers import health

        async def fake_get(key):
            return "1" if key.endswith(("SemanticRouterWorker", "LLMInvokerWorker")) else None
        with patch.object(health.redis_mgr, "get_raw", side_effect=fake_get):
            async with client(create_app()) as c:
                r = await c.get("/health/workers")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertFalse(body["all_ok"])
        self.assertEqual((body["workers"]["SemanticRouterWorker"], body["workers"]["OutboundDispatcherWorker"]), ("ok", "missing"))


class HealthcheckCliTests(unittest.TestCase):
    def test_exit_codes(self):
        from src.shared.kafka import healthcheck as hc

        self.assertEqual(hc.main(["x"]), 2)
        with patch.object(hc, "_alive", AsyncMock(return_value=True)):
            self.assertEqual(hc.main(["x", "LLMInvokerWorker"]), 0)
        with patch.object(hc, "_alive", AsyncMock(return_value=False)):
            self.assertEqual(hc.main(["x", "LLMInvokerWorker"]), 1)
        with patch.object(hc, "_alive", AsyncMock(side_effect=OSError("redis down"))):
            self.assertEqual(hc.main(["x", "LLMInvokerWorker"]), 1)

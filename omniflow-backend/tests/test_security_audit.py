"""Security audit (night 2): every route is authenticated unless it is on an explicit, justified allow-list — checked statically AND by calling it.

* Static: the dependency tree of each route (ignoring the subscription gate, which itself resolves the user) must contain `get_current_user`,
  otherwise the route must be in PUBLIC with a reason. A stale allow-list entry (route gone / now authenticated) also fails.
* Dynamic: every protected route, called with no credentials (and a spoofed X-Tenant-ID header), answers 401 — never 200/5xx.
  Every public route that is not a plain health/probe answers with a refusal when its secret/signature is missing.
"""
import re
import unittest
import uuid

import httpx

from src.gateway.main import create_app

# (METHOD, path) -> why it is allowed to be called without a Clerk session
PUBLIC = {
    ("POST", "/api/v1/auth/login"): "410 Gone stub (local auth removed)",
    ("POST", "/api/v1/auth/logout"): "always 200: blacklists the presented token's id and grants nothing",
    ("POST", "/api/v1/auth/refresh"): "410 Gone stub",
    ("POST", "/api/v1/auth/register"): "410 Gone stub",
    ("POST", "/api/v1/billing/admin/tenants/{tenant_id}/activate"): "staff key header X-Billing-Key (404 when the key is not configured)",
    ("POST", "/api/v1/support/admin/tickets/{ticket_id}/reply"): "staff key header X-Support-Key (disabled when not configured)",
    ("POST", "/api/v1/webhooks/clerk"): "Svix signature",
    ("GET", "/api/v1/webhooks/meta"): "Meta verify-token handshake",
    ("POST", "/api/v1/webhooks/meta"): "X-Hub-Signature-256 HMAC",
    ("GET", "/api/v1/webhooks/whatsapp"): "Meta verify-token handshake",
    ("POST", "/api/v1/webhooks/whatsapp"): "X-Hub-Signature-256 HMAC",
    ("POST", "/api/v1/webhooks/payments"): "payment-provider shared secret / signature",
    ("GET", "/api/v1/integrations/facebook/callback"): "browser redirect from Meta; authorised by the one-time `state` we issued",
    ("GET", "/api/v1/stream/dashboard"): "SSE: authenticates inside from `?token=` (EventSource cannot send headers); the tenant_id query is ignored",
    ("POST", "/api/v1/meta/data-deletion"): "Meta signed_request, HMAC-SHA256 verified with the app secret",
    ("GET", "/api/v1/meta/data-deletion/status/{code}"): "public status lookup by an unguessable confirmation code",
    ("GET", "/api/v1/public/catalog"): "public read-only pricing catalogue",
    ("GET", "/health"): "liveness probe (no secrets)",
    ("GET", "/health/ready"): "readiness probe (ok/unavailable only)",
    ("GET", "/health/workers"): "worker heartbeat summary (names + ok/stale only)",
}
SELF_REFUSING = {"/api/v1/auth/login", "/api/v1/auth/refresh", "/api/v1/auth/register"}                                  # answer 410
ALWAYS_OK = {"/api/v1/auth/logout"}                                                                                    # by design never refuses
PROBES = {"/health", "/health/ready", "/health/workers", "/api/v1/integrations/facebook/callback"}
DOC_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}                                          # only mounted in development


def inventory(app):
    def walk(dep, acc):
        for d in dep.dependencies:
            name = getattr(d.call, "__name__", str(d.call))
            if name == "subscription_write_gate":
                continue                                    # the gate resolves the user itself: it must not make a route look authenticated
            acc.add(name)
            walk(d, acc)

    out = {}
    for r in app.routes:
        if type(r).__name__ == "_IncludedRouter":
            for c in r.effective_route_contexts():
                acc = set()
                walk(c.dependant, acc)
                for m in c.methods - {"HEAD", "OPTIONS"}:
                    out[(m, c.path)] = acc
        elif getattr(r, "path", None) in DOC_PATHS:
            continue
        elif getattr(r, "dependant", None) is not None:
            acc = set()
            walk(r.dependant, acc)
            for m in (r.methods or set()) - {"HEAD", "OPTIONS"}:
                out[(m, r.path)] = acc
    return out


def fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", lambda m: str(uuid.uuid4()) if "id" in m.group(0) else "x", path)


class RouteAuthAudit(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.routes = inventory(cls.app)

    def test_the_inventory_is_not_empty_and_covers_the_main_surfaces(self):
        self.assertGreater(len(self.routes), 120)
        for must in (("GET", "/api/v1/customers"), ("POST", "/api/v1/billing/checkout"), ("GET", "/api/v1/team"), ("GET", "/api/v1/einvoice/status")):
            self.assertIn(must, self.routes)

    def test_every_route_is_authenticated_or_explicitly_public(self):
        unauth = {k for k, deps in self.routes.items() if "get_current_user" not in deps}
        unexpected = sorted(unauth - set(PUBLIC))
        self.assertEqual(unexpected, [], f"routes reachable without authentication and not on the allow-list: {unexpected}")

    def test_the_allow_list_has_no_stale_or_now_authenticated_entries(self):
        stale = sorted(k for k in PUBLIC if k not in self.routes)
        self.assertEqual(stale, [], f"allow-list entries for routes that do not exist: {stale}")
        authed = sorted(k for k in PUBLIC if k in self.routes and "get_current_user" in self.routes[k])
        self.assertEqual(authed, [], f"allow-listed routes that are in fact authenticated (remove them): {authed}")

    async def test_every_protected_route_refuses_an_anonymous_caller_even_with_a_spoofed_tenant_header(self):
        bad = []
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://t") as c:
            for (method, path), deps in sorted(self.routes.items()):
                if (method, path) in PUBLIC:
                    continue
                r = await c.request(method, fill(path), headers={"X-Tenant-ID": str(uuid.uuid4())}, json={} if method in ("POST", "PUT", "PATCH") else None)
                hidden = r.status_code == 404 and "_enabled" in deps          # feature-flagged router that is switched off: 404 for everyone
                if r.status_code != 401 and not hidden:
                    bad.append((method, path, r.status_code))
        self.assertEqual(bad, [], f"protected routes that did not answer 401 to an anonymous caller: {bad}")

    async def test_public_routes_refuse_calls_without_their_secret(self):
        bad = []
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://t") as c:
            for (method, path) in sorted(PUBLIC):
                if path in PROBES or path in ALWAYS_OK or path in {"/api/v1/stream/dashboard", "/api/v1/webhooks/clerk", "/api/v1/public/catalog", "/api/v1/meta/data-deletion/status/{code}"}:       # these have dedicated tests below
                    continue
                r = await c.request(method, fill(path), json={} if method == "POST" else None)
                if path in SELF_REFUSING:
                    ok = r.status_code == 410
                else:
                    ok = r.status_code in (400, 401, 403, 404, 422, 503)
                if not ok:
                    bad.append((method, path, r.status_code))
        self.assertEqual(bad, [], f"public routes that accepted a call without their secret: {bad}")

    async def test_clerk_webhook_requires_a_valid_signature_when_a_secret_is_configured(self):
        from unittest.mock import patch

        from src.gateway.routers import webhooks
        with patch.object(webhooks.settings, "clerk_webhook_secret", "whsec_" + "dGVzdC1zZWNyZXQtZm9yLXRlc3Rpbmc="):          # pragma: allowlist secret
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://t") as c:
                self.assertEqual((await c.post("/api/v1/webhooks/clerk", json={"type": "user.deleted", "data": {"id": "user_x"}})).status_code, 400)
                bad = await c.post("/api/v1/webhooks/clerk", json={"type": "user.deleted", "data": {"id": "user_x"}},
                                   headers={"svix-id": "msg_1", "svix-timestamp": "1700000000", "svix-signature": "v1,AAAA"})
                self.assertEqual(bad.status_code, 400)
        with patch.object(webhooks.settings, "clerk_webhook_secret", ""), patch.object(type(webhooks.settings), "is_development", property(lambda s: False)):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://t") as c:
                self.assertEqual((await c.post("/api/v1/webhooks/clerk", json={})).status_code, 503)                         # unconfigured in production: closed

    async def test_the_dashboard_stream_needs_a_real_token_and_ignores_a_tenant_id_query(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://t") as c:
            for params in ({}, {"tenant_id": str(uuid.uuid4())}, {"token": "garbage"}, {"token": "garbage", "tenant_id": str(uuid.uuid4())}):
                self.assertEqual((await c.get("/api/v1/stream/dashboard", params=params)).status_code, 401, params)

    async def test_dead_unauthenticated_demo_endpoints_are_gone(self):
        self.assertNotIn(("POST", "/api/v1/chat/simulate-mock"), self.routes)

    async def test_the_facebook_oauth_callback_is_reachable_without_a_bearer_token(self):
        """Regression: the subscription write gate resolved the current user, so the browser redirect from Meta (no Authorization header) got 401."""
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://t") as c:
            r = await c.get("/api/v1/integrations/facebook/callback", params={"code": "x", "state": "not-issued-by-us"})
        self.assertNotEqual(r.status_code, 401)
        self.assertIn("text/html", r.headers["content-type"])

    async def test_probes_do_not_leak_internals(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://t") as c:
            for path in ("/health", "/health/ready", "/health/workers"):
                text = (await c.get(path)).text.lower()
                for needle in ("password", "secret", "postgresql://", "traceback", "redis://", "sk_"):
                    self.assertNotIn(needle, text, (path, needle))

    async def test_api_docs_are_not_served_outside_development(self):
        from src.shared.core.config import get_settings
        if get_settings().is_development:
            self.skipTest("development: docs are intentionally mounted")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://t") as c:
            for path in ("/docs", "/redoc", "/openapi.json"):
                self.assertEqual((await c.get(path)).status_code, 404)


if __name__ == "__main__":
    unittest.main()

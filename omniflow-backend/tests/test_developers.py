"""Developers v1: API keys (hashed, read-only, rate-limited, logged, tenant-isolated) and outgoing signed webhooks (outbox, retries, SSRF guard).

Receivers are simulated with an in-process poster — no real HTTP endpoint was called.
"""
import json
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import patch

from src.gateway.main import create_app
from src.shared.core.config import get_settings
from src.shared.security.crypto import decrypt_secret, encrypt_secret
from src.shared.services.developers import api_keys, webhooks_out as wo
from tests.test_property_import_flow import auth_overrides, client_for

DB_URL = os.environ.get("OMNIFLOW_TEST_DATABASE_URL")
RLS_URL = os.environ.get("OMNIFLOW_TEST_RLS_URL")
GOOD_URL = "https://93.184.216.34/hook"          # a public IP literal: validation needs no DNS


class KeyTests(unittest.TestCase):
    def test_keys_are_random_prefixed_and_only_their_hash_is_derivable(self):
        a, b = api_keys.generate_key(), api_keys.generate_key()
        self.assertNotEqual(a[0], b[0])
        full, prefix, digest = a
        self.assertTrue(full.startswith("ofk_") and len(full) > 40)
        self.assertEqual(prefix, full[:12])
        self.assertEqual(digest, api_keys.hash_key(full))
        self.assertNotIn(full, digest)
        self.assertEqual(len(digest), 64)

    def test_looks_like_key_rejects_obvious_junk(self):
        self.assertFalse(api_keys.looks_like_key("Bearer x"))
        self.assertFalse(api_keys.looks_like_key("ofk_"))
        self.assertFalse(api_keys.looks_like_key("sk_live_" + "a" * 40))
        self.assertTrue(api_keys.looks_like_key(api_keys.generate_key()[0]))


class SignatureTests(unittest.TestCase):
    def test_signature_verifies_and_covers_body_and_timestamp(self):
        body, secret = b'{"a":1}', "whsec_test"          # pragma: allowlist secret
        header = wo.sign(secret, body, 1_700_000_000)
        self.assertTrue(wo.verify_signature(secret, body, header, now=1_700_000_010))
        self.assertFalse(wo.verify_signature(secret, b'{"a":2}', header, now=1_700_000_010))          # body tampered
        self.assertFalse(wo.verify_signature("whsec_other", body, header, now=1_700_000_010))          # wrong secret  # pragma: allowlist secret
        self.assertFalse(wo.verify_signature(secret, body, header, now=1_700_000_000 + 3600))          # replay outside tolerance
        self.assertFalse(wo.verify_signature(secret, body, "garbage", now=1_700_000_010))
        forged = header.replace("t=1700000000", "t=1700003600")
        self.assertFalse(wo.verify_signature(secret, body, forged, now=1_700_003_600))                 # timestamp is part of the MAC


class UrlGuardTests(unittest.TestCase):
    def test_public_https_is_accepted(self):
        self.assertEqual(wo.validate_url(GOOD_URL), GOOD_URL)

    def test_internal_and_malformed_urls_are_refused_outside_development(self):
        with patch.object(get_settings().__class__, "is_development", property(lambda self: False)):
            for bad in ("http://93.184.216.34/x", "https://127.0.0.1/x", "https://10.0.0.5/x", "https://169.254.169.254/latest/meta-data",
                        "https://192.168.1.10/x", "https://[::1]/x", "ftp://93.184.216.34/x", "https://user:pw@93.184.216.34/x", "https:///nohost",
                        "javascript:alert(1)", "https://" + "a" * 600 + ".com"):
                with self.subTest(bad), self.assertRaises(wo.UnsafeUrl):
                    wo.validate_url(bad)

    def test_literal_private_ip_is_refused_without_dns(self):
        with patch.object(get_settings().__class__, "is_development", property(lambda self: False)), self.assertRaises(wo.UnsafeUrl):
            wo.validate_url("https://10.1.2.3/x", resolve=False)


def endpoint(**kw):
    secret = "whsec_unit"          # pragma: allowlist secret
    base = dict(url=GOOD_URL, secret_enc=encrypt_secret(secret), active=True, consecutive_failures=0, disabled_reason=None, events=["customer.created"])
    return NS(**{**base, **kw}), secret


def delivery(**kw):
    eid = uuid.uuid4()
    base = dict(delivery_id=uuid.uuid4(), event_id=eid, event_type="customer.created", payload={"id": str(eid), "type": "customer.created", "data": {}},
                status="pending", attempts=0, next_attempt_at=None, last_status_code=None, last_error=None, delivered_at=None)
    return NS(**{**base, **kw})


class AttemptTests(unittest.IsolatedAsyncioTestCase):
    NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    async def test_success_marks_delivered_and_sends_a_verifiable_signature(self):
        ep, secret = endpoint()
        d, seen = delivery(), {}

        async def poster(url, body, headers):
            seen.update(url=url, body=body, headers=headers)
            return 204, None

        await wo.attempt(d, ep, poster=poster, now=self.NOW)
        self.assertEqual((d.status, d.attempts, d.last_status_code, d.delivered_at), ("delivered", 1, 204, self.NOW))
        self.assertTrue(wo.verify_signature(secret, seen["body"], seen["headers"]["X-OmniFlow-Signature"], now=self.NOW.timestamp()))
        self.assertEqual(seen["headers"]["X-OmniFlow-Event"], "customer.created")
        self.assertEqual(json.loads(seen["body"])["id"], str(d.event_id))

    async def test_failures_back_off_then_give_up(self):
        ep, _ = endpoint()
        d = delivery()

        async def poster(url, body, headers):
            return 500, None

        waits = []
        for i in range(wo.MAX_ATTEMPTS):
            await wo.attempt(d, ep, poster=poster, now=self.NOW)
            waits.append(d.next_attempt_at - self.NOW if d.next_attempt_at else None)
        self.assertEqual(waits[:5], list(wo.BACKOFF))
        self.assertEqual((d.status, d.attempts, d.next_attempt_at, d.last_error), ("failed", wo.MAX_ATTEMPTS, None, "http_500"))
        self.assertEqual(ep.consecutive_failures, wo.MAX_ATTEMPTS)

    async def test_network_error_is_a_failure_with_its_type(self):
        ep, _ = endpoint()
        d = delivery()

        async def poster(url, body, headers):
            return None, "ConnectTimeout"

        await wo.attempt(d, ep, poster=poster, now=self.NOW)
        self.assertEqual((d.status, d.last_error, d.last_status_code), ("pending", "ConnectTimeout", None))

    async def test_success_resets_the_failure_streak(self):
        ep, _ = endpoint(consecutive_failures=7)
        d = delivery()

        async def poster(url, body, headers):
            return 200, None

        await wo.attempt(d, ep, poster=poster, now=self.NOW)
        self.assertEqual(ep.consecutive_failures, 0)

    async def test_endpoint_is_switched_off_after_too_many_failures(self):
        ep, _ = endpoint(consecutive_failures=wo.DISABLE_AFTER_FAILURES - 1)
        d = delivery()

        async def poster(url, body, headers):
            return 503, None

        await wo.attempt(d, ep, poster=poster, now=self.NOW)
        self.assertEqual((ep.active, ep.disabled_reason), (False, "too_many_failures"))

    async def test_unsafe_url_is_never_contacted(self):
        ep, _ = endpoint(url="https://10.0.0.9/x")
        d, called = delivery(), []

        async def poster(url, body, headers):
            called.append(url)
            return 200, None

        with patch.object(get_settings().__class__, "is_development", property(lambda self: False)):
            await wo.attempt(d, ep, poster=poster, now=self.NOW)
        self.assertEqual(called, [])
        self.assertTrue(d.last_error.startswith("unsafe_url"))

    async def test_unreadable_secret_is_a_failure_not_a_crash(self):
        ep, _ = endpoint(secret_enc="not-a-ciphertext")
        d = delivery()
        await wo.attempt(d, ep, poster=lambda *a: None, now=self.NOW)           # poster would blow up if reached
        self.assertEqual(d.last_error, "secret_unreadable")


class FakeEmitSession:
    """Just enough of an AsyncSession for emit(): scalars() → endpoints, add(), flush(), begin_nested()."""
    def __init__(self, endpoints):
        self.endpoints, self.added = endpoints, []

    def begin_nested(self):
        outer = self

        class Ctx:
            async def __aenter__(self_inner):
                return outer

            async def __aexit__(self_inner, *a):
                return False
        return Ctx()

    async def scalars(self, stmt):
        return NS(all=lambda: self.endpoints)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        pass


class EmitTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_subscribed_active_endpoints_get_the_event(self):
        t = uuid.uuid4()
        a, _ = endpoint(endpoint_id=uuid.uuid4(), events=["customer.created", "conversation.handoff"])
        b, _ = endpoint(endpoint_id=uuid.uuid4(), events=["conversation.created"])
        s = FakeEmitSession([a, b])
        self.assertEqual(await wo.emit(s, t, "customer.created", {"customer_id": "c1"}), 1)
        self.assertEqual([r.endpoint_id for r in s.added], [a.endpoint_id])
        self.assertEqual(s.added[0].payload["data"], {"customer_id": "c1"})
        self.assertEqual(s.added[0].payload["tenant_id"], str(t))

    async def test_emit_never_raises(self):
        class Boom(FakeEmitSession):
            async def scalars(self, stmt):
                raise RuntimeError("db down")

        self.assertEqual(await wo.emit(Boom([]), uuid.uuid4(), "customer.created", {}), 0)


@unittest.skipUnless(DB_URL and RLS_URL, "needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL (non-superuser: RLS is the tenant guard)")
class DevelopersDbTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        self.text = text
        self.admin = create_async_engine(DB_URL)
        self.app_engine = create_async_engine(RLS_URL)
        self.Admin = async_sessionmaker(self.admin, expire_on_commit=False)
        self.App = async_sessionmaker(self.app_engine, expire_on_commit=False)
        self.A, self.B = uuid.uuid4(), uuid.uuid4()
        async with self.Admin() as s:
            for i, t in enumerate((self.A, self.B)):
                await s.execute(text("INSERT INTO tenants (tenant_id,business_name,fal_license_number,subscription_tier,status,max_ai_conversations,onboarding_status,created_at,updated_at) "
                                     "VALUES (:t,:n,:f,'starter','active',100,'completed',now(),now())"), {"t": t, "n": f"شركة {i}", "f": f"FAL-{t.hex[:8]}"})
                await s.execute(text("INSERT INTO customers (customer_id,tenant_id,unified_phone,phone_e164,display_name,source,channel_user_ids,is_processing_restricted,is_vip,vcard_state,created_at,updated_at) "
                                     "VALUES (gen_random_uuid(),:t,:p,:p,:n,'manual','{}',false,false,'STATE_NEW',now(),now())"), {"t": t, "p": f"+9665{i}{uuid.uuid4().int % 10**7:07d}", "n": f"عميل {i}"})
            await s.commit()
        p = patch("src.shared.db.session.AsyncSessionFactory", self.App)
        p.start()
        self.addCleanup(p.stop)

    async def asyncTearDown(self):
        async with self.Admin() as s:
            for t in (self.A, self.B):
                for table in ("webhook_deliveries", "webhook_endpoints", "api_usage_log", "api_keys", "audit_logs", "customers"):
                    await s.execute(self.text(f"DELETE FROM {table} WHERE tenant_id=:t"), {"t": t})
                await s.execute(self.text("DELETE FROM tenants WHERE tenant_id=:t"), {"t": t})
            await s.commit()
        await self.admin.dispose()
        await self.app_engine.dispose()

    async def row(self, sql, **p):
        async with self.Admin() as s:
            return (await s.execute(self.text(sql), p)).mappings().all()

    def dev_app(self, tenant, role=None):
        from src.shared.core.enums import TenantUserRole

        app = create_app()
        auth_overrides(app, tenant, self.App, role=role or TenantUserRole.ADMIN)
        return app

    async def make_key(self, tenant, name="k"):
        async with client_for(self.dev_app(tenant)) as c:
            r = await c.post("/api/v1/developers/api-keys", json={"name": name})
            self.assertEqual(r.status_code, 201, r.text)
            return r.json()

    async def test_a_key_is_shown_once_stored_hashed_and_reads_only_its_own_tenant(self):
        made = await self.make_key(self.A)
        key = made["key"]
        stored = (await self.row("SELECT key_hash, prefix FROM api_keys WHERE key_id=:k", k=made["key_id"]))[0]
        self.assertEqual(stored["key_hash"], api_keys.hash_key(key))
        self.assertNotIn(key, json.dumps(dict(stored)))
        async with client_for(create_app()) as c:
            r = await c.get("/api/public/v1/customers", headers={"Authorization": f"Bearer {key}"})
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertEqual([x["name"] for x in body["data"]], ["عميل 0"])                # tenant A's customer only, never tenant B's
            self.assertNotIn("notes", body["data"][0])
            self.assertFalse(body["has_more"])
            one = await c.get(f"/api/public/v1/customers/{body['data'][0]['id']}", headers={"Authorization": f"Bearer {key}"})
            self.assertEqual(one.status_code, 200)
        other = (await self.row("SELECT customer_id FROM customers WHERE tenant_id=:t", t=self.B))[0]["customer_id"]
        async with client_for(create_app()) as c:
            r = await c.get(f"/api/public/v1/customers/{other}", headers={"Authorization": f"Bearer {key}"})
        self.assertEqual(r.status_code, 404)                                                # another tenant's id is indistinguishable from a missing one

    async def test_missing_wrong_and_revoked_keys_are_refused_and_the_api_is_read_only(self):
        made = await self.make_key(self.A)
        key = made["key"]
        async with client_for(create_app()) as c:
            self.assertEqual((await c.get("/api/public/v1/customers")).status_code, 401)
            self.assertEqual((await c.get("/api/public/v1/customers", headers={"Authorization": "Bearer ofk_" + "x" * 43})).status_code, 401)
            self.assertEqual((await c.get("/api/public/v1/customers", headers={"Authorization": "Bearer not-a-key"})).status_code, 401)
            for method in ("post", "put", "patch", "delete"):
                r = await getattr(c, method)("/api/public/v1/customers", headers={"Authorization": f"Bearer {key}"})
                self.assertIn(r.status_code, (401, 405), method)                            # no write handler exists at all
        async with client_for(self.dev_app(self.A)) as c:
            self.assertEqual((await c.delete(f"/api/v1/developers/api-keys/{made['key_id']}")).status_code, 200)
            self.assertEqual((await c.delete(f"/api/v1/developers/api-keys/{made['key_id']}")).status_code, 404)       # already revoked
        async with client_for(create_app()) as c:
            self.assertEqual((await c.get("/api/public/v1/customers", headers={"Authorization": f"Bearer {key}"})).status_code, 401)

    async def test_usage_is_logged_and_a_manager_cannot_manage_another_tenants_keys(self):
        made = await self.make_key(self.A)
        key = made["key"]
        async with client_for(create_app()) as c:
            await c.get("/api/public/v1/customers", headers={"Authorization": f"Bearer {key}"})
            await c.get(f"/api/public/v1/customers/{uuid.uuid4()}", headers={"Authorization": f"Bearer {key}"})
        rows = await self.row("SELECT method, path, status FROM api_usage_log WHERE key_id=:k ORDER BY id", k=made["key_id"])
        self.assertEqual([(r["method"], r["status"]) for r in rows], [("GET", 200), ("GET", 404)])
        self.assertIsNotNone((await self.row("SELECT last_used_at FROM api_keys WHERE key_id=:k", k=made["key_id"]))[0]["last_used_at"])
        async with client_for(self.dev_app(self.B)) as c:
            self.assertEqual((await c.get(f"/api/v1/developers/api-keys/{made['key_id']}/usage")).status_code, 404)
            self.assertEqual((await c.delete(f"/api/v1/developers/api-keys/{made['key_id']}")).status_code, 404)
            ov = (await c.get("/api/v1/developers/overview")).json()
            self.assertEqual(ov["api_keys"], [])
        async with client_for(self.dev_app(self.A)) as c:
            usage = (await c.get(f"/api/v1/developers/api-keys/{made['key_id']}/usage")).json()["items"]
            self.assertEqual(len(usage), 2)
            ov = (await c.get("/api/v1/developers/overview")).json()
            self.assertEqual(ov["usage"]["last_24h"], 2)
            self.assertNotIn("key_hash", json.dumps(ov))
            self.assertNotIn(key, json.dumps(ov))

    async def test_rate_limit_answers_429_and_is_logged(self):
        made = await self.make_key(self.A)

        async def over(*a, **k):
            return 121

        with patch("src.gateway.routers.public_api.redis_mgr.incr_counter", over):
            async with client_for(create_app()) as c:
                r = await c.get("/api/public/v1/customers", headers={"Authorization": f"Bearer {made['key']}"})
        self.assertEqual(r.status_code, 429)
        self.assertIn("Retry-After", r.headers)
        self.assertEqual([x["status"] for x in await self.row("SELECT status FROM api_usage_log WHERE key_id=:k", k=made["key_id"])], [429])

    async def test_only_admins_manage_keys_and_there_is_a_cap(self):
        from src.shared.core.enums import TenantUserRole

        async with client_for(self.dev_app(self.A, TenantUserRole.AGENT)) as c:
            self.assertEqual((await c.post("/api/v1/developers/api-keys", json={"name": "x"})).status_code, 403)
        async with client_for(self.dev_app(self.A)) as c:
            for i in range(api_keys.MAX_ACTIVE_KEYS):
                self.assertEqual((await c.post("/api/v1/developers/api-keys", json={"name": f"k{i}"})).status_code, 201)
            self.assertEqual((await c.post("/api/v1/developers/api-keys", json={"name": "one-too-many"})).status_code, 409)

    async def test_webhook_lifecycle_secret_once_encrypted_signed_test_and_isolation(self):
        async with client_for(self.dev_app(self.A)) as c:
            bad = await c.post("/api/v1/developers/webhooks", json={"url": "https://127.0.0.1/x", "events": ["customer.created"]})
            with patch.object(get_settings().__class__, "is_development", property(lambda self: False)):
                bad = await c.post("/api/v1/developers/webhooks", json={"url": "https://127.0.0.1/x", "events": ["customer.created"]})
            self.assertEqual(bad.status_code, 422)
            self.assertEqual((await c.post("/api/v1/developers/webhooks", json={"url": GOOD_URL, "events": ["nope"]})).status_code, 422)
            made = await c.post("/api/v1/developers/webhooks", json={"url": GOOD_URL, "events": ["customer.created"], "description": "crm"})
            self.assertEqual(made.status_code, 201, made.text)
            ep = made.json()
            secret = ep["secret"]
            stored = (await self.row("SELECT secret_enc FROM webhook_endpoints WHERE endpoint_id=:e", e=ep["endpoint_id"]))[0]["secret_enc"]
            self.assertNotIn(secret, stored)
            self.assertEqual(decrypt_secret(stored), secret)
            listed = (await c.get("/api/v1/developers/overview")).json()["webhooks"][0]
            self.assertNotIn("secret", listed)

            seen = {}

            async def poster(url, body, headers):
                seen.update(body=body, headers=headers)
                return 200, None

            real_attempt = wo.attempt

            async def attempt_with_fake(d, e, **kw):
                return await real_attempt(d, e, poster=poster, **kw)

            with patch("src.gateway.routers.developers.webhooks_out.attempt", attempt_with_fake):
                t = await c.post(f"/api/v1/developers/webhooks/{ep['endpoint_id']}/test")
            self.assertEqual((t.status_code, t.json()["status"]), (200, "delivered"), t.text)
            self.assertTrue(wo.verify_signature(secret, seen["body"], seen["headers"]["X-OmniFlow-Signature"]))
            self.assertEqual(json.loads(seen["body"])["type"], "webhook.test")

            rotated = (await c.post(f"/api/v1/developers/webhooks/{ep['endpoint_id']}/rotate-secret")).json()["secret"]
            self.assertNotEqual(rotated, secret)
            log = (await c.get(f"/api/v1/developers/webhooks/{ep['endpoint_id']}/deliveries")).json()["items"]
            self.assertEqual(len(log), 1)
        async with client_for(self.dev_app(self.B)) as c:
            self.assertEqual((await c.get(f"/api/v1/developers/webhooks/{ep['endpoint_id']}/deliveries")).status_code, 404)
            self.assertEqual((await c.delete(f"/api/v1/developers/webhooks/{ep['endpoint_id']}")).status_code, 404)
            self.assertEqual((await c.post(f"/api/v1/developers/webhooks/{ep['endpoint_id']}/test")).status_code, 404)

    async def test_a_real_event_goes_through_the_outbox_and_is_delivered_with_retry(self):
        from src.shared.db.session import get_system_session, get_tenant_session
        from src.shared.services.customers import create_customer

        async with client_for(self.dev_app(self.A)) as c:
            ep = (await c.post("/api/v1/developers/webhooks", json={"url": GOOD_URL, "events": ["customer.created"]})).json()
        async with client_for(self.dev_app(self.B)) as c:
            await c.post("/api/v1/developers/webhooks", json={"url": GOOD_URL, "events": ["customer.created"]})        # another tenant's endpoint

        phone = f"+9665{uuid.uuid4().int % 10**8:08d}"
        async with get_tenant_session(self.A) as s:
            await create_customer(s, tenant_id=self.A, actor_id=None, phone=phone, source="manual")
        rows = await self.row("SELECT tenant_id, endpoint_id, status, payload FROM webhook_deliveries WHERE event_type='customer.created'")
        mine = [r for r in rows if r["tenant_id"] == self.A]
        self.assertEqual(len(mine), 1)                                                    # tenant B's endpoint received nothing from tenant A's event
        self.assertFalse([r for r in rows if r["tenant_id"] == self.B])
        self.assertEqual((str(mine[0]["endpoint_id"]), mine[0]["status"], mine[0]["payload"]["data"]["phone"]), (ep["endpoint_id"], "pending", phone))

        calls = []

        async def flaky(url, body, headers):
            calls.append(headers["X-OmniFlow-Delivery"])
            return (500, None) if len(calls) == 1 else (200, None)

        async with get_system_session() as s:
            self.assertEqual(await wo.deliver_due(s, poster=flaky), 1)
        st = (await self.row("SELECT status, attempts, next_attempt_at FROM webhook_deliveries WHERE tenant_id=:t", t=self.A))[0]
        self.assertEqual((st["status"], st["attempts"]), ("pending", 1))
        self.assertGreater(st["next_attempt_at"], datetime.now(timezone.utc))
        async with get_system_session() as s:
            self.assertEqual(await wo.deliver_due(s, poster=flaky), 0)                    # not due yet → not retried early
        async with get_system_session() as s:
            self.assertEqual(await wo.deliver_due(s, poster=flaky, now=datetime.now(timezone.utc) + timedelta(minutes=2)), 1)
        st = (await self.row("SELECT status, attempts FROM webhook_deliveries WHERE tenant_id=:t", t=self.A))[0]
        self.assertEqual((st["status"], st["attempts"]), ("delivered", 2))
        self.assertEqual(len(set(calls)), 1)                                              # same delivery id on the retry (receivers can de-duplicate)


if __name__ == "__main__":
    unittest.main()

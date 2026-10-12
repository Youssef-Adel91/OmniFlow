"""Meta Data Deletion Request Callback: signed_request verification, what is removed, the public status lookup, and the public catalogue.

Tested with requests signed locally by this test using a fake app secret — NOT with a real Meta callback.
"""
import base64
import hashlib
import hmac
import json
import os
import unittest
import uuid

import httpx

from src.gateway.main import create_app
from src.gateway.routers import meta_data_deletion as mdd
from src.shared.core.config import get_settings
from tests.test_property_import_flow import client_for

DB_URL = os.environ.get("OMNIFLOW_TEST_DATABASE_URL")
RLS_URL = os.environ.get("OMNIFLOW_TEST_RLS_URL")
SECRET = "unit-test-app-secret"          # pragma: allowlist secret


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def sign(payload: dict, secret: str = SECRET, *, tamper_sig: bool = False) -> str:
    body = b64(json.dumps(payload).encode())
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    if tamper_sig:
        sig = bytes([sig[0] ^ 1]) + sig[1:]
    return f"{b64(sig)}.{body}"


class SignedRequestTests(unittest.TestCase):
    def test_a_valid_request_is_decoded(self):
        p = mdd.parse_signed_request(sign({"algorithm": "HMAC-SHA256", "user_id": "1234567890", "issued_at": 1}), SECRET)
        self.assertEqual(p["user_id"], "1234567890")

    def test_everything_else_is_refused(self):
        good = {"algorithm": "HMAC-SHA256", "user_id": "42"}
        cases = {
            "wrong secret": sign(good, "another-secret"),                              # pragma: allowlist secret
            "tampered signature": sign(good, tamper_sig=True),
            "unsupported algorithm": sign({**good, "algorithm": "none"}),
            "missing algorithm": sign({"user_id": "42"}),
            "missing user id": sign({"algorithm": "HMAC-SHA256"}),
            "empty user id": sign({"algorithm": "HMAC-SHA256", "user_id": "  "}),
            "no dot": "abc", "two dots": "a.b.c", "empty": "", "not base64": "!!!.???",
            "too long": "a" * 9000 + "." + "b",
            "payload not json": f"{b64(b'x')}.{b64(b'not json')}",
            "payload not an object": sign([1, 2, 3]) if False else f"{b64(b'x')}.{b64(b'[1,2]')}",
        }
        for label, value in cases.items():
            with self.subTest(label), self.assertRaises(mdd.InvalidSignedRequest):
                mdd.parse_signed_request(value, SECRET)

    def test_an_unconfigured_secret_refuses_everything(self):
        with self.assertRaises(mdd.InvalidSignedRequest):
            mdd.parse_signed_request(sign({"algorithm": "HMAC-SHA256", "user_id": "1"}, ""), "")

    def test_the_signature_must_cover_the_encoded_payload_exactly(self):
        body = b64(json.dumps({"algorithm": "HMAC-SHA256", "user_id": "7"}).encode())
        sig_over_decoded = hmac.new(SECRET.encode(), base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)), hashlib.sha256).digest()
        with self.assertRaises(mdd.InvalidSignedRequest):                      # signing the DECODED bytes (a common mistake) is not accepted
            mdd.parse_signed_request(f"{b64(sig_over_decoded)}.{body}", SECRET)


@unittest.skipUnless(DB_URL and RLS_URL, "needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL (non-superuser: RLS is the tenant guard)")
class DeletionDbTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from unittest.mock import patch

        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        self.text = text
        self.admin = create_async_engine(DB_URL)
        self.app_engine = create_async_engine(RLS_URL)
        self.Admin = async_sessionmaker(self.admin, expire_on_commit=False)
        self.App = async_sessionmaker(self.app_engine, expire_on_commit=False)
        self.fb_user = f"fb{uuid.uuid4().int % 10**12}"
        self.tenants = [uuid.uuid4(), uuid.uuid4()]
        async with self.Admin() as s:
            for i, t in enumerate(self.tenants):
                await s.execute(text("INSERT INTO tenants (tenant_id,business_name,fal_license_number,subscription_tier,status,max_ai_conversations,onboarding_status,"
                                     "facebook_user_id,instagram_page_id,instagram_page_access_token,instagram_account_id,created_at,updated_at) "
                                     "VALUES (:t,:n,:f,'starter','active',100,'completed',:u,:p,'enc-token',:a,now(),now())"),
                                {"t": t, "n": f"شركة {i}", "f": f"FAL-{t.hex[:8]}", "u": self.fb_user if i == 0 else "someone-else", "p": f"page{t.hex[:6]}", "a": f"ig{t.hex[:6]}"})
            await s.commit()
        for target, name, value in ((get_settings(), "meta_app_secret", SECRET), (get_settings(), "public_base_url", "https://app.example.test/")):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch("src.shared.db.session.AsyncSessionFactory", self.App)
        p.start()
        self.addCleanup(p.stop)

    async def asyncTearDown(self):
        async with self.Admin() as s:
            await s.execute(self.text("DELETE FROM data_deletion_requests WHERE fb_user_hash = ANY(:h)"),
                            {"h": [hashlib.sha256(x.encode()).hexdigest() for x in (self.fb_user, "nobody-here")]})
            for t in self.tenants:
                await s.execute(self.text("DELETE FROM audit_logs WHERE tenant_id=:t"), {"t": t})
                await s.execute(self.text("DELETE FROM tenants WHERE tenant_id=:t"), {"t": t})
            await s.commit()
        await self.admin.dispose()
        await self.app_engine.dispose()

    async def row(self, sql, **p):
        async with self.Admin() as s:
            return (await s.execute(self.text(sql), p)).mappings().all()

    async def test_a_signed_request_removes_the_users_meta_connection_and_returns_a_status_url(self):
        async with client_for(create_app()) as c:
            r = await c.post("/api/v1/meta/data-deletion", data={"signed_request": sign({"algorithm": "HMAC-SHA256", "user_id": self.fb_user})})
            self.assertEqual(r.status_code, 200, r.text)
            out = r.json()
            code = out["confirmation_code"]
            self.assertEqual(out["url"], f"https://app.example.test/ar/data-deletion/status?code={code}")           # PUBLIC_BASE_URL, trailing slash tolerated
            st = await c.get(f"/api/v1/meta/data-deletion/status/{code}")
            self.assertEqual((st.status_code, st.json()["status"]), (200, "completed"))
            self.assertIsNotNone(st.json()["completed_at"])
        mine, other = await self.row("SELECT instagram_page_id, instagram_page_access_token, instagram_account_id, facebook_user_id FROM tenants WHERE tenant_id=:t", t=self.tenants[0]), \
            await self.row("SELECT instagram_page_id, instagram_page_access_token, facebook_user_id FROM tenants WHERE tenant_id=:t", t=self.tenants[1])
        self.assertEqual(dict(mine[0]), {"instagram_page_id": None, "instagram_page_access_token": None, "instagram_account_id": None, "facebook_user_id": None})
        self.assertEqual((other[0]["instagram_page_access_token"], other[0]["facebook_user_id"]), ("enc-token", "someone-else"))       # other people untouched
        audit = await self.row("SELECT action FROM audit_logs WHERE tenant_id=:t AND action='privacy.meta_data_deletion'", t=self.tenants[0])
        self.assertEqual(len(audit), 1)
        req = (await self.row("SELECT fb_user_hash, tenants_affected, status FROM data_deletion_requests WHERE confirmation_code=:c", c=code))[0]
        self.assertEqual((req["tenants_affected"], req["status"]), (1, "completed"))
        self.assertNotIn(self.fb_user, req["fb_user_hash"])                                                    # only a hash of the id is kept

    async def test_a_user_we_hold_nothing_for_gets_an_honest_status(self):
        async with client_for(create_app()) as c:
            r = await c.post("/api/v1/meta/data-deletion", data={"signed_request": sign({"algorithm": "HMAC-SHA256", "user_id": "nobody-here"})})
            code = r.json()["confirmation_code"]
            self.assertEqual((await c.get(f"/api/v1/meta/data-deletion/status/{code}")).json()["status"], "no_data")

    async def test_forged_or_malformed_requests_are_refused_and_store_nothing(self):
        before = (await self.row("SELECT count(*) AS n FROM data_deletion_requests"))[0]["n"]
        async with client_for(create_app()) as c:
            for value in (sign({"algorithm": "HMAC-SHA256", "user_id": self.fb_user}, "wrong"), "garbage", sign({"algorithm": "HMAC-SHA256", "user_id": self.fb_user}, tamper_sig=True)):
                r = await c.post("/api/v1/meta/data-deletion", data={"signed_request": value})
                self.assertEqual((r.status_code, r.json()["detail"]["code"]), (400, "INVALID_SIGNED_REQUEST"))
            self.assertEqual((await c.post("/api/v1/meta/data-deletion")).status_code, 422)                            # missing field
            self.assertEqual((await c.post("/api/v1/meta/data-deletion", json={"signed_request": "x"})).status_code, 422)  # Meta sends a form, not JSON
        self.assertEqual((await self.row("SELECT count(*) AS n FROM data_deletion_requests"))[0]["n"], before)
        self.assertEqual((await self.row("SELECT instagram_page_access_token FROM tenants WHERE tenant_id=:t", t=self.tenants[0]))[0]["instagram_page_access_token"], "enc-token")

    async def test_status_lookup_is_public_but_only_by_a_valid_code(self):
        async with client_for(create_app()) as c:
            self.assertEqual((await c.get("/api/v1/meta/data-deletion/status/ABCDEFGHJKLM")).status_code, 404)
            self.assertEqual((await c.get("/api/v1/meta/data-deletion/status/short")).status_code, 422)
            self.assertEqual((await c.get("/api/v1/meta/data-deletion/status/" + "A" * 40)).status_code, 422)
            self.assertEqual((await c.get("/api/v1/meta/data-deletion/status/ab;drop--table")).status_code, 422)

    async def test_the_public_catalogue_matches_the_seeded_prices_and_exposes_no_secrets(self):
        async with client_for(create_app()) as c:
            r = await c.get("/api/v1/public/catalog")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        plans = {p["code"]: p for p in data["plans"]}
        self.assertEqual([(k, plans[k]["monthly_halalas"], plans[k]["yearly_halalas"]) for k in ("starter", "pro", "business")],
                         [("starter", 29900, 299000), ("pro", 79900, 799000), ("business", 179900, 1799000)])
        self.assertTrue(plans["enterprise"]["contact_sales"])
        self.assertNotIn("economic", plans)
        self.assertEqual([(k["code"], k["replies"], k["price_halalas"]) for k in data["topup_packs"]],
                         [("replies_5k", 5000, 18900), ("replies_20k", 20000, 69900), ("replies_50k", 50000, 149900)])
        self.assertEqual((data["vat_rate_bp"], data["trial_days"]), (1500, get_settings().billing_trial_days))
        text = r.text.lower()
        for needle in ("secret", "token", "password"):
            self.assertNotIn(needle, text)


if __name__ == "__main__":
    unittest.main()

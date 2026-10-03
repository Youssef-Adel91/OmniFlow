"""'Connect with Facebook' OAuth regressions (mocked Graph API, no network or database)."""
import json
import re
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import FastAPI

from src.gateway.routers import facebook_oauth as fo
from src.shared.security.crypto import decrypt_secret, encrypt_secret

CALLBACK = "/api/v1/integrations/facebook/callback"


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def set_raw(self, key, value, ttl=None):
        self.store[key] = value

    async def get_raw(self, key):
        return self.store.get(key)

    async def delete_raw(self, key):
        self.store.pop(key, None)


class FacebookOAuthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tenant_id = uuid.uuid4()
        self.redis = FakeRedis()
        self.saved = []
        self.subscribed = []

        self.app = FastAPI()
        self.app.include_router(fo.router)
        self.app.dependency_overrides[fo.get_current_user] = lambda: SimpleNamespace(tenant_id=self.tenant_id)

        async def save(tenant_id, page_id, token, instagram_account_id=None):
            self.saved.append((tenant_id, page_id, token, instagram_account_id))

        async def graph_post(client, path, params):
            self.subscribed.append(path)
            self.subscribed_params = params
            return {"success": True}

        patches = [
            patch.object(fo, "redis_mgr", self.redis),
            patch.object(fo.settings, "meta_app_id", "123"),
            patch.object(fo.settings, "meta_app_secret", "secret"),
            patch.object(fo.settings, "meta_oauth_redirect_uri", "http://localhost:8000/cb"),
            patch.object(fo, "_save_page_connection", save),
            patch.object(fo, "_graph_post", graph_post),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _graph_get(self, pages, instagram=None):
        instagram = instagram or {}

        async def graph_get(client, path, params):
            if path == "oauth/access_token":
                token = "long" if params.get("grant_type") == "fb_exchange_token" else "short"
                return {"access_token": token}
            if path == "me/accounts":
                return {"data": pages}
            return {"instagram_business_account": instagram[path]} if path in instagram else {}

        return patch.object(fo, "_graph_get", graph_get)

    def _client(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")

    async def test_start_returns_503_when_unconfigured(self):
        with patch.object(fo.settings, "meta_app_secret", ""):
            async with self._client() as client:
                response = await client.get("/api/v1/integrations/facebook/start")
        self.assertEqual(response.status_code, 503)

    async def test_start_builds_dialog_url_and_stores_state(self):
        async with self._client() as client:
            response = await client.get("/api/v1/integrations/facebook/start")
        self.assertEqual(response.status_code, 200)
        url = response.json()["authorize_url"]
        self.assertIn("client_id=123", url)
        self.assertIn("pages_show_list", url)
        self.assertEqual(len(self.redis.store), 1)

    async def test_single_page_connects_subscribes_and_saves(self):
        await fo._store_state("st", self.tenant_id)
        pages = [{"id": "p1", "name": "Shop", "access_token": "page-token"}]
        with self._graph_get(pages, {"p1": {"id": "ig1", "username": "shop_ig"}}):
            async with self._client() as client:
                response = await client.get(CALLBACK, params={"code": "c", "state": "st"})
        self.assertIn('"status": "success"', response.text)
        self.assertIn("shop_ig", response.text)
        self.assertEqual(self.saved, [(self.tenant_id, "p1", "page-token", "ig1")])
        self.assertEqual(self.subscribed, ["p1/subscribed_apps"])
        # "comments" is not a valid Page subscription field (Meta answers 500).
        self.assertEqual(self.subscribed_params["subscribed_fields"], "messages,messaging_postbacks,feed")

    async def test_default_scopes_include_pages_read_engagement(self):
        scopes = fo.get_settings().__class__.model_fields["meta_oauth_scopes"].default.split(",")
        self.assertIn("pages_read_engagement", scopes)

    async def test_state_is_single_use(self):
        await fo._store_state("st", self.tenant_id)
        pages = [{"id": "p1", "name": "Shop", "access_token": "t"}]
        with self._graph_get(pages):
            async with self._client() as client:
                await client.get(CALLBACK, params={"code": "c", "state": "st"})
                replay = await client.get(CALLBACK, params={"code": "c", "state": "st"})
        self.assertIn("expired", replay.text)
        self.assertEqual(len(self.saved), 1)

    async def test_error_paths_never_save(self):
        async with self._client() as client:
            denied = await client.get(CALLBACK, params={"error": "access_denied", "error_description": "nope"})
            missing = await client.get(CALLBACK)
            forged = await client.get(CALLBACK, params={"code": "c", "state": "never-issued"})
        self.assertIn("nope", denied.text)
        self.assertIn("Missing code/state", missing.text)
        self.assertIn("expired", forged.text)
        self.assertEqual(self.saved, [])

    async def test_multiple_pages_need_selection_and_replay_is_rejected(self):
        await fo._store_state("st", self.tenant_id)
        pages = [
            {"id": "p1", "name": "One", "access_token": "t1"},
            {"id": "p2", "name": "Two", "access_token": "t2"},
        ]
        with self._graph_get(pages):
            async with self._client() as client:
                callback = await client.get(CALLBACK, params={"code": "c", "state": "st"})
                self.assertIn("needs_selection", callback.text)
                self.assertEqual(self.saved, [])
                connection_id = re.search(r'"connection_id": "([^"]+)"', callback.text).group(1)

                # Tokens held in Redis while waiting for the pick must be encrypted.
                pending = json.loads(self.redis.store[f"{fo._PENDING_KEY_PREFIX}{connection_id}"])
                stored = {p["page_id"]: p["page_access_token"] for p in pending["pages"]}
                self.assertNotIn(stored["p1"], ("t1", "t2"))
                self.assertEqual(decrypt_secret(stored["p2"]), "t2")

                body = {"connection_id": connection_id, "page_id": "p2"}
                chosen = await client.post("/api/v1/integrations/facebook/select-page", json=body)
                self.assertEqual(chosen.status_code, 200)
                self.assertEqual(self.saved, [(self.tenant_id, "p2", "t2", None)])
                replay = await client.post("/api/v1/integrations/facebook/select-page", json=body)
        self.assertEqual(replay.status_code, 410)

    async def test_select_page_rejects_another_tenants_connection(self):
        await fo._store_state("st", self.tenant_id)
        pages = [
            {"id": "p1", "name": "One", "access_token": "t1"},
            {"id": "p2", "name": "Two", "access_token": "t2"},
        ]
        with self._graph_get(pages):
            async with self._client() as client:
                callback = await client.get(CALLBACK, params={"code": "c", "state": "st"})
                connection_id = re.search(r'"connection_id": "([^"]+)"', callback.text).group(1)
                self.app.dependency_overrides[fo.get_current_user] = lambda: SimpleNamespace(tenant_id=uuid.uuid4())
                response = await client.post(
                    "/api/v1/integrations/facebook/select-page",
                    json={"connection_id": connection_id, "page_id": "p1"},
                )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.saved, [])

    async def test_callback_reports_conflict_in_popup(self):
        await fo._store_state("st", self.tenant_id)
        pages = [{"id": "p1", "name": "Shop", "access_token": "t"}]

        async def conflict(*a, **k):
            raise fo.PageAlreadyConnectedError("already")

        with self._graph_get(pages), patch.object(fo, "_save_page_connection", conflict):
            async with self._client() as client:
                response = await client.get(CALLBACK, params={"code": "c", "state": "st"})
        self.assertIn('"status": "error"', response.text)
        self.assertIn("already", response.text)

    async def test_select_page_returns_409_on_conflict(self):
        await fo._store_state("st", self.tenant_id)
        pages = [
            {"id": "p1", "name": "One", "access_token": "t1"},
            {"id": "p2", "name": "Two", "access_token": "t2"},
        ]

        async def conflict(*a, **k):
            raise fo.PageAlreadyConnectedError("already")

        with self._graph_get(pages):
            async with self._client() as client:
                callback = await client.get(CALLBACK, params={"code": "c", "state": "st"})
                connection_id = re.search(r'"connection_id": "([^"]+)"', callback.text).group(1)
                with patch.object(fo, "_save_page_connection", conflict):
                    response = await client.post(
                        "/api/v1/integrations/facebook/select-page",
                        json={"connection_id": connection_id, "page_id": "p1"},
                    )
        self.assertEqual(response.status_code, 409)


class SavePageConnectionTests(unittest.IsolatedAsyncioTestCase):
    def _session(self, tenant, commit_error=None):
        session = MagicMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = tenant
        session.execute = AsyncMock(return_value=result)
        session.commit = AsyncMock(side_effect=commit_error)
        session.rollback = AsyncMock()
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        return session, factory

    async def test_sets_and_clears_instagram_account_id(self):
        tenant = SimpleNamespace(instagram_page_id=None, instagram_account_id="old", instagram_page_access_token=None)
        session, factory = self._session(tenant)
        with patch.object(fo, "AsyncSessionFactory", factory):
            await fo._save_page_connection(uuid.uuid4(), "p1", "tok", "ig1")
            self.assertEqual(tenant.instagram_account_id, "ig1")
            self.assertEqual(decrypt_secret(tenant.instagram_page_access_token), "tok")
            await fo._save_page_connection(uuid.uuid4(), "p2", "tok", None)
        self.assertIsNone(tenant.instagram_account_id)
        self.assertEqual(tenant.instagram_page_id, "p2")

    async def test_integrity_error_becomes_page_already_connected(self):
        from sqlalchemy.exc import IntegrityError
        tenant = SimpleNamespace(instagram_page_id=None, instagram_account_id=None, instagram_page_access_token=None)
        session, factory = self._session(tenant, IntegrityError("s", {}, Exception("dup")))
        with patch.object(fo, "AsyncSessionFactory", factory):
            with self.assertRaises(fo.PageAlreadyConnectedError):
                await fo._save_page_connection(uuid.uuid4(), "p1", "tok", "ig1")
        session.rollback.assert_awaited()


class FieldEncryptionTests(unittest.TestCase):
    def test_round_trip_and_graceful_failure(self):
        ciphertext = encrypt_secret("EAAB-page-token")
        self.assertNotIn("EAAB", ciphertext)
        self.assertEqual(decrypt_secret(ciphertext), "EAAB-page-token")
        self.assertIsNone(decrypt_secret("legacy-plaintext-token"))
        self.assertIsNone(decrypt_secret(""))


if __name__ == "__main__":
    unittest.main()

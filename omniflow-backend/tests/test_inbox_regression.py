"""GET /conversations 404 regression (null customer names) + error-handler contract."""
import datetime
import unittest
import uuid
from types import SimpleNamespace as NS

import httpx

from src.gateway import dependencies as deps
from src.gateway.main import create_app
from src.gateway.routers.conversations import ConversationOut
from src.shared.core.exceptions import NotFoundError


def _conv(channel, customer):
    return NS(
        conversation_id=uuid.uuid4(), tenant_id=uuid.uuid4(), customer=customer, channel=channel,
        status="ai_active", last_message_at=datetime.datetime.now(datetime.timezone.utc), message_count=3,
    )


def _customer(phone, display_name=None, wa_name=None):
    return NS(unified_phone=phone, display_name=display_name, whatsapp_profile_name=wa_name,
              engagement_score=None, is_vip=False)


class ConversationOutTests(unittest.TestCase):
    def test_instagram_and_messenger_customers_without_names(self):
        for channel in ("instagram", "messenger"):
            out = ConversationOut.from_orm_model(_conv(channel, _customer("17841400000009")))
            self.assertEqual(out.customer_name, "")
            self.assertEqual(out.customer_phone, "17841400000009")
            self.assertEqual(out.channel, channel)

    def test_name_precedence_and_missing_customer(self):
        self.assertEqual(ConversationOut.from_orm_model(_conv("whatsapp", _customer("+966", "Ali", "wa"))).customer_name, "Ali")
        self.assertEqual(ConversationOut.from_orm_model(_conv("whatsapp", _customer("+966", None, "wa"))).customer_name, "wa")
        out = ConversationOut.from_orm_model(_conv("whatsapp", None))
        self.assertEqual((out.customer_name, out.customer_phone), ("", ""))


class FakeRepo:
    def __init__(self, items):
        self.items = items

    async def get_multi(self, **kwargs):
        return self.items, len(self.items)


class InboxEndpointTests(unittest.IsolatedAsyncioTestCase):
    def _app(self, items):
        app = create_app()
        app.dependency_overrides[deps.get_current_user] = lambda: NS(tenant_id=uuid.uuid4())
        app.dependency_overrides[deps.get_conversation_repo] = lambda: FakeRepo(items)

        async def missing():
            raise NotFoundError("conversations with id=x not found")

        async def buggy():
            raise ValueError("real bug")

        app.add_api_route("/__test/missing", missing)
        app.add_api_route("/__test/buggy", buggy)
        return app

    async def _get(self, app, path):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    async def test_list_with_instagram_and_messenger_conversations_returns_200(self):
        items = [_conv("instagram", _customer("17841400000009")), _conv("messenger", _customer("5550001", None, None)),
                 _conv("whatsapp", _customer("+966501234567", "Ali"))]
        response = await self._get(self._app(items), "/api/v1/conversations?limit=50&offset=0&sort_by=recent")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["total"], 3)
        self.assertEqual([i["channel"] for i in body["items"]], ["instagram", "messenger", "whatsapp"])

    async def test_not_found_error_is_404(self):
        response = await self._get(self._app([]), "/__test/missing")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "NOT_FOUND")

    async def test_bare_value_error_is_logged_500_not_404(self):
        response = await self._get(self._app([]), "/__test/buggy")
        self.assertEqual(response.status_code, 500)
        body = response.json()
        self.assertEqual(body["code"], "INTERNAL_SERVER_ERROR")
        self.assertIn("error_id", body)


if __name__ == "__main__":
    unittest.main()

import unittest
import uuid

import httpx

from src.channel_adapters.base import ChannelAdapter, ChannelSendError
from src.channel_adapters.telegram.adapter import TelegramAdapter
from src.shared.core.enums import Channel, MessageType

T = uuid.uuid4()
SECRET = "s3cret-value-for-tests"


def msg(**over):
    m = {"message_id": 7, "date": 1760000000, "chat": {"id": 555, "type": "private"}, "from": {"id": 555, "is_bot": False, "first_name": "سالم", "last_name": "العتيبي"}, "text": "ابغى شقة"}
    m.update(over)
    return {"update_id": 1, "message": m}


class TelegramTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.a = TelegramAdapter(SECRET)

    def test_it_satisfies_the_channel_adapter_contract(self):
        self.assertIsInstance(self.a, ChannelAdapter)

    def test_secret_header_is_checked_in_constant_time_and_fails_closed(self):
        self.assertTrue(self.a.verify({"X-Telegram-Bot-Api-Secret-Token": SECRET}, b"{}"))
        self.assertFalse(self.a.verify({"X-Telegram-Bot-Api-Secret-Token": "wrong"}, b"{}"))
        self.assertFalse(self.a.verify({}, b"{}"))
        self.assertFalse(TelegramAdapter(None).verify({"X-Telegram-Bot-Api-Secret-Token": ""}, b"{}"))        # unconfigured: nothing is trusted, not even an empty header
        self.assertFalse(TelegramAdapter("").verify({"X-Telegram-Bot-Api-Secret-Token": ""}, b"{}"))

    def test_text_message_becomes_a_canonical_event(self):
        [ev] = self.a.parse(msg(), T)
        self.assertEqual((ev.channel, ev.message_type, ev.text_content, ev.platform_conversation_id, ev.customer_display_name),
                         (Channel.TELEGRAM, MessageType.TEXT, "ابغى شقة", "555", "سالم العتيبي"))
        self.assertEqual(ev.platform_message_id, "tg:555:7")

    def test_voice_photo_document_map_to_types_and_stickers_are_ignored(self):
        self.assertEqual(self.a.parse(msg(text=None, voice={"mime_type": "audio/ogg", "duration": 4}), T)[0].message_type, MessageType.AUDIO)
        self.assertEqual(self.a.parse(msg(text=None, photo=[{"file_id": "x"}], caption="الموقع"), T)[0].text_content, "الموقع")
        self.assertEqual(self.a.parse(msg(text=None, document={"mime_type": "application/pdf"}), T)[0].message_type, MessageType.DOCUMENT)
        self.assertEqual(self.a.parse(msg(text=None, sticker={"file_id": "x"}), T), [])

    def test_groups_bots_edits_channel_posts_and_garbage_are_ignored(self):
        self.assertEqual(self.a.parse(msg(chat={"id": -100, "type": "supergroup"}), T), [])
        self.assertEqual(self.a.parse(msg(**{"from": {"id": 9, "is_bot": True}}), T), [])
        self.assertEqual(self.a.parse({"edited_message": msg()["message"]}, T), [])
        self.assertEqual(self.a.parse({"channel_post": msg()["message"]}, T), [])
        for junk in ({}, {"message": "x"}, {"message": {"chat": {"type": "private"}}}, None):
            self.assertEqual(self.a.parse(junk, T), [])

    async def test_send_text_success_and_failures(self):
        seen = []

        def handler(request: httpx.Request):
            seen.append((str(request.url), request.content))
            if "botBAD" in str(request.url):
                return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})
            if "botBLOCKED" in str(request.url):
                return httpx.Response(403, json={"ok": False, "description": "Forbidden: bot was blocked by the user"})
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            self.assertEqual(await self.a.send_text("GOOD", "555", "أهلاً", client=c), "tg:555:42")
            with self.assertRaises(ChannelSendError) as e:
                await self.a.send_text("BAD", "555", "x", client=c)
            self.assertTrue(e.exception.auth)                                                        # dead token → reconnect alarm
            with self.assertRaises(ChannelSendError) as e:
                await self.a.send_text("BLOCKED", "555", "x", client=c)
            self.assertFalse(e.exception.auth)                                                       # the user blocked the bot: not a credential problem
            self.assertNotIn("BAD", str(e.exception))                                                # the token never leaks into messages
            await self.a.send_text("GOOD", "555", "ب" * 5000, client=c)
        self.assertLessEqual(len(__import__("json").loads(seen[-1][1])["text"]), 4096)


if __name__ == "__main__":
    unittest.main()

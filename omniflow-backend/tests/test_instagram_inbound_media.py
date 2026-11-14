"""Instagram / Messenger DMs carrying an image, a voice note, a file, a sticker (checklist item 2: inbound image/audio)."""
import unittest
import uuid

from src.channel_adapters.instagram.router import MetaMessagingEvent, _normalize_messaging_event
from src.shared.core.enums import Channel, MessageType


def ev(message):
    return MetaMessagingEvent.model_validate({"sender": {"id": "u1"}, "recipient": {"id": "p1"}, "timestamp": 1760000000000, "message": {"mid": "mid.1", **message}})


class InboundMediaTests(unittest.TestCase):
    def norm(self, message, channel=Channel.INSTAGRAM):
        return _normalize_messaging_event(ev(message), "u1", channel, uuid.uuid4())

    def test_image_audio_video_and_file_map_to_their_types_with_the_url(self):
        for att, expected in (("image", MessageType.IMAGE), ("audio", MessageType.AUDIO), ("video", MessageType.VIDEO), ("file", MessageType.DOCUMENT)):
            out = self.norm({"attachments": [{"type": att, "payload": {"url": f"https://cdn.example/{att}"}}]})
            self.assertEqual((out.message_type, out.media_url, out.text_content), (expected, f"https://cdn.example/{att}", None), att)

    def test_text_wins_over_attachments_and_echoes_are_dropped(self):
        out = self.norm({"text": "شوف هالصورة", "attachments": [{"type": "image", "payload": {"url": "https://x"}}]})
        self.assertEqual((out.message_type, out.text_content), (MessageType.TEXT, "شوف هالصورة"))
        self.assertIsNone(self.norm({"text": "رد منا", "is_echo": True}))

    def test_messenger_events_are_tagged_messenger(self):
        out = self.norm({"text": "هلا"}, Channel.MESSENGER)
        self.assertEqual(out.channel, Channel.MESSENGER)

    def test_an_empty_message_is_ignored_not_crashing(self):
        self.assertIsNone(self.norm({}))


if __name__ == "__main__":
    unittest.main()

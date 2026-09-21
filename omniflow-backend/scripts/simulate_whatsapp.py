"""
scripts/simulate_whatsapp.py — WhatsApp Webhook Simulator

Sends a realistic Meta-signed webhook payload to the local gateway.
Supports custom message text, contact names, and VIP session seeding.

Usage:
    python scripts/simulate_whatsapp.py
    python scripts/simulate_whatsapp.py --message "أريد رسالة صوتية تشرح لي مميزات شقة النرجس"
    python scripts/simulate_whatsapp.py --vip --message "ما هي أفضل فيلا في حي الملقا؟"
    python scripts/simulate_whatsapp.py --showcase --burst
"""
import hmac
import hashlib
import json
import httpx
import asyncio
import sys
import uuid
from datetime import datetime
import argparse

# Fix Windows cp1252 UnicodeEncodeError for emoji/Arabic output
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    elif sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

# ── Config ────────────────────────────────────────────────────────────────────
WEBHOOK_URL  = "http://localhost:8000/api/v1/webhooks/whatsapp"
REDIS_URL    = "redis://:dev_redis_pass_placeholder@localhost:6379/0"
META_HMAC_SECRET = "dev_meta_hmac_secret_placeholder"

# ── Default showcase messages ──────────────────────────────────────────────────
_SHOWCASE_MESSAGES = [
    "أريد رسالة صوتية تشرح لي مميزات شقة النرجس",
    "ما مميزات الشقق في حي النرجس؟",
    "هل يمكنك إرسال تقرير عن صك العقار رقم 12345؟",
]

DEFAULT_PHONE   = "966555123456"
DEFAULT_PHONE_ID = "1234567890"
DEFAULT_NAME    = "عميل تجريبي"


def _sign(payload_bytes: bytes) -> str:
    return hmac.new(
        META_HMAC_SECRET.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()


def _build_payload(message: str, phone: str, name: str) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "1234567890",
            "changes": [{
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {
                        "display_phone_number": "966500000000",
                        "phone_number_id": DEFAULT_PHONE_ID,
                    },
                    "contacts": [{"profile": {"name": name}, "wa_id": phone}],
                    "messages": [{
                        "from": phone,
                        "id": f"wamid.{uuid.uuid4().hex[:16]}",
                        "timestamp": str(int(datetime.now().timestamp())),
                        "type": "text",
                        "text": {"body": message},
                    }],
                },
                "field": "messages",
            }],
        }],
    }


async def _seed_vip_session(phone: str, tenant_id: str) -> None:
    """
    Pre-seed the Redis session_state for this customer as is_vip=True.
    This makes _should_use_voice_note() return True via Condition 1
    regardless of keywords or tier.
    """
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        key = f"t:{tenant_id}:session:{phone}"
        state = json.dumps({
            "is_vip": True,
            "customer_id": str(uuid.uuid4()),
            "vcard_state": "STATE_CONTACT_SAVED_VERIFIED",
            "is_processing_restricted": False,
            "is_human_active": False,
            "conversation_id": None,
        })
        await r.setex(key, 3600, state)
        await r.aclose()
        print(f"  [VIP] Redis session seeded for phone={phone}, tenant={tenant_id}")
    except Exception as exc:
        print(f"  [VIP] Warning: Could not seed Redis VIP session: {exc}")
        print(f"  [VIP] Keyword trigger ('voice'/'صوت') will be used instead.")


async def simulate_incoming_message(
    message: str,
    name: str = DEFAULT_NAME,
    phone: str = DEFAULT_PHONE,
    vip: bool = False,
    burst: bool = False,
    showcase: bool = False,
) -> None:
    messages = [message]
    if burst:
        messages = _SHOWCASE_MESSAGES

    if vip:
        # Seed VIP state so the first condition in _should_use_voice_note fires
        # Use dev tenant_id from DB (adjust if yours differs)
        tenant_id = "70a4cd1a-9cbe-416e-9f8c-d70da148d91c"
        await _seed_vip_session(phone, tenant_id)

    for msg_text in messages:
        payload = _build_payload(msg_text, phone, name)
        payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        sig = _sign(payload_bytes)

        headers = {
            "Content-Type": "application/json",
            "X-Hub-Signature-256": f"sha256={sig}",
        }

        print(f"\n{'='*60}")
        print(f"SENDING: {msg_text}")
        print(f"  from={phone}  name={name}  vip={vip}")
        print(f"{'='*60}")

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(WEBHOOK_URL, content=payload_bytes, headers=headers)

        status_icon = "OK" if response.status_code == 200 else "FAIL"
        print(f"[{status_icon}] Webhook [{response.status_code}]: {response.text}")

        if burst:
            await asyncio.sleep(1.5)

    print("\n[DONE] Now watch your OutboundDispatcherWorker logs for:")
    print("  outbound_audio_dispatched  (message_type=audio)")
    print("  tts_voice_note_generated   (in LLMInvokerWorker)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulate WhatsApp Webhook — OmniFlow")
    parser.add_argument(
        "--message", "-m",
        default=_SHOWCASE_MESSAGES[0],
        help="Message text to send (default: voice note request)",
    )
    parser.add_argument(
        "--name", "-n",
        default=DEFAULT_NAME,
        help="Contact display name",
    )
    parser.add_argument(
        "--phone", "-p",
        default=DEFAULT_PHONE,
        help="Sender phone number (wa_id format, no +)",
    )
    parser.add_argument(
        "--vip",
        action="store_true",
        help="Seed Redis session as VIP before sending (triggers TTS via Condition 1)",
    )
    parser.add_argument(
        "--showcase",
        action="store_true",
        help="Print extra showcase context",
    )
    parser.add_argument(
        "--burst",
        action="store_true",
        help="Send all showcase messages sequentially",
    )
    args = parser.parse_args()

    asyncio.run(simulate_incoming_message(
        message=args.message,
        name=args.name,
        phone=args.phone,
        vip=args.vip,
        burst=args.burst,
        showcase=args.showcase,
    ))

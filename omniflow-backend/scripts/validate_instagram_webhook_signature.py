"""
Real end-to-end validation of the Instagram/Messenger webhook signature fix.

Before this fix: `channel_adapters/instagram/router.py`'s POST handler had
no X-Hub-Signature-256 check at all -- its own docstring claimed "no HMAC
needed for this adapter", which is wrong. Meta signs every webhook
delivery for an App with the App Secret regardless of product (WhatsApp,
Instagram, Messenger all share one signature scheme), and the WhatsApp
adapter already enforced it (`channel_adapters/whatsapp/security.py`).
Found while investigating a client question about where
META_WEBHOOK_HMAC_SECRET is used in the codebase.

This boots the REAL FastAPI app via its own `create_app()` (not a
reimplementation) and makes real HTTP requests through a real ASGI
transport against the real `/api/v1/webhooks/meta` route, using the real
`settings.meta_webhook_hmac_secret` to compute a genuine HMAC-SHA256
signature -- not a mocked signature-check function.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from src.shared.core.config import get_settings

settings = get_settings()

PAYLOAD = b'{"object":"page","entry":[{"id":"999999999","time":1234567890,"messaging":[]}]}'


def _sign(body: bytes) -> str:
    digest = hmac.new(
        settings.meta_webhook_hmac_secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return f"sha256={digest}"


async def main() -> None:
    if not settings.meta_webhook_hmac_secret:
        raise RuntimeError("META_WEBHOOK_HMAC_SECRET is empty -- nothing to validate")

    from src.gateway.main import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    url = "/api/v1/webhooks/meta"

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. No signature header at all -- must be rejected, not silently accepted.
        r = await client.post(url, content=PAYLOAD)
        assert r.status_code == 403, f"expected 403 with no signature, got {r.status_code}: {r.text}"
        assert r.json()["detail"]["code"] == "INVALID_SIGNATURE"
        print("PASS: missing signature -> 403 INVALID_SIGNATURE")

        # 2. Wrong signature (right shape, wrong value) -- must be rejected.
        r = await client.post(
            url, content=PAYLOAD, headers={"X-Hub-Signature-256": "sha256=" + "0" * 64}
        )
        assert r.status_code == 403, f"expected 403 with bad signature, got {r.status_code}: {r.text}"
        print("PASS: wrong signature -> 403 INVALID_SIGNATURE")

        # 3. Signature computed for a DIFFERENT body than the one actually sent
        #    (tampered payload) -- must be rejected even though the header
        #    itself is well-formed and was validly computed for something.
        real_sig = _sign(PAYLOAD)
        tampered = PAYLOAD.replace(b"999999999", b"111111111")
        r = await client.post(url, content=tampered, headers={"X-Hub-Signature-256": real_sig})
        assert r.status_code == 403, f"expected 403 on tampered body, got {r.status_code}: {r.text}"
        print("PASS: signature for a different body -> 403 INVALID_SIGNATURE")

        # 4. Correctly signed request -- must pass the signature check (200,
        #    not 403). Downstream tenant resolution/Kafka publish happens in
        #    a BackgroundTasks callback and is out of scope for this specific
        #    fix; the handler's own contract is to always return 200 once the
        #    signature is valid, regardless of what happens after.
        r = await client.post(url, content=PAYLOAD, headers={"X-Hub-Signature-256": real_sig})
        assert r.status_code == 200, f"expected 200 with a valid signature, got {r.status_code}: {r.text}"
        assert r.json() == {"status": "ok"}
        print("PASS: correctly signed request -> 200 (reaches normal processing)")

    print("\nALL CHECKS PASSED — Instagram/Messenger webhook now rejects unsigned/tampered requests.")


if __name__ == "__main__":
    asyncio.run(main())

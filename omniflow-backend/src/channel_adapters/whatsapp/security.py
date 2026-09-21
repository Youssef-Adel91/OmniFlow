"""
channel_adapters/whatsapp/security.py — WhatsApp Webhook Signature Verification

Meta signs every POST webhook payload with HMAC-SHA256 using the app's
App Secret. The signature is delivered in the X-Hub-Signature-256 header
as "sha256=<hex_digest>".

Security invariant:
    If the HMAC check fails, return HTTP 403 IMMEDIATELY — before any
    payload deserialization or DB access. This prevents:
      - Spoofed webhook attacks
      - Replay attacks (timing-safe comparison used)
      - Payload tampering

Reference:
    https://developers.facebook.com/docs/messenger-platform/webhooks#validate-payloads
    SRS §5 — Channel Adapters Security
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Annotated

import structlog
from fastapi import Depends, Header, HTTPException, Request, status

from src.shared.core.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

# Pre-encode the secret once at module load — avoids repeated .encode() per request
_HMAC_SECRET: bytes = settings.meta_webhook_hmac_secret.encode("utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# Core verification function
# ══════════════════════════════════════════════════════════════════════════════

def verify_meta_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """
    Verify the X-Hub-Signature-256 header against the raw request body.

    Args:
        raw_body         — Raw bytes of the request body (BEFORE any parsing).
        signature_header — Value of X-Hub-Signature-256 header.
                           Expected format: "sha256=<64-char-hex-string>"

    Returns:
        True if the signature is valid.
        False if the header is missing, malformed, or the HMAC doesn't match.

    CRITICAL: Uses hmac.compare_digest() for timing-safe comparison.
    A simple `==` comparison is vulnerable to timing attacks.
    """
    if not signature_header:
        logger.warning("whatsapp_missing_signature_header")
        return False

    # Header must start with "sha256="
    if not signature_header.startswith("sha256="):
        logger.warning(
            "whatsapp_malformed_signature_header",
            header=signature_header[:20],
        )
        return False

    expected_hex = signature_header.removeprefix("sha256=")

    # Compute HMAC-SHA256 of the raw body using the app secret
    computed = hmac.new(
        key=_HMAC_SECRET,
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    # Timing-safe comparison — both strings must be same length for this to work
    is_valid = hmac.compare_digest(computed, expected_hex)

    if not is_valid:
        logger.warning(
            "whatsapp_signature_mismatch",
            computed_prefix=computed[:8],
            received_prefix=expected_hex[:8],
        )

    return is_valid


# ══════════════════════════════════════════════════════════════════════════════
# FastAPI Dependency
# ══════════════════════════════════════════════════════════════════════════════

async def verify_whatsapp_signature(
    request: Request,
    x_hub_signature_256: Annotated[
        str | None,
        Header(
            alias="X-Hub-Signature-256",
            description=(
                "Meta HMAC-SHA256 webhook signature. "
                "Format: 'sha256=<hex_digest>'. "
                "Required on all POST /webhooks/whatsapp requests."
            ),
        ),
    ] = None,
) -> bytes:
    """
    FastAPI dependency that validates the Meta webhook HMAC signature.

    Returns the raw request body bytes on success (avoids double-reading).
    Raises HTTP 403 on any signature failure.

    Usage:
        @router.post("/webhooks/whatsapp")
        async def whatsapp_webhook(
            raw_body: Annotated[bytes, Depends(verify_whatsapp_signature)],
        ): ...

    Why return raw_body?
        FastAPI reads request.body() once. This dependency reads it and
        passes it to the handler so the handler doesn't need to re-read
        the stream (which would return empty bytes).
    """
    raw_body = await request.body()

    if not verify_meta_signature(raw_body, x_hub_signature_256):
        logger.warning(
            "whatsapp_webhook_rejected",
            remote=request.client.host if request.client else "unknown",
            path=request.url.path,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "INVALID_SIGNATURE",
                "message": (
                    "Webhook signature verification failed. "
                    "Ensure X-Hub-Signature-256 matches the payload HMAC."
                ),
            },
        )

    return raw_body


# Annotated shorthand for use in endpoint signatures
VerifiedWebhookBody = Annotated[bytes, Depends(verify_whatsapp_signature)]

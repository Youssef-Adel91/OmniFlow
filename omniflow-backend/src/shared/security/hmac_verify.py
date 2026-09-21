"""
shared/security/hmac_verify.py — Meta Webhook HMAC-SHA256 Verification

Used to validate inbound webhooks from:
  - Meta WhatsApp Cloud API (X-Hub-Signature-256 header)
  - Moyasar payment callbacks
  - Tap payment callbacks

All unverified requests are rejected with HTTP 401 before any processing.
"""
from __future__ import annotations

import hashlib
import hmac


def verify_hmac_signature(
    payload_bytes: bytes,
    signature_header: str,
    secret: str,
    *,
    prefix: str = "sha256=",
) -> bool:
    """
    Verify an HMAC-SHA256 signature in constant time.

    Args:
        payload_bytes:     The raw request body bytes.
        signature_header:  The full signature string from the header
                           (e.g. "sha256=abc123...").
        secret:            The shared HMAC secret from settings.
        prefix:            Expected algorithm prefix (default "sha256=").

    Returns:
        True if the signature is valid, False otherwise.

    Security:
        Uses `hmac.compare_digest` to prevent timing oracle attacks.
    """
    if not signature_header or not signature_header.startswith(prefix):
        return False

    provided_sig = signature_header[len(prefix):]
    expected_sig = hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(provided_sig, expected_sig)

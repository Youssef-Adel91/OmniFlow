"""
shared/security/crypto.py — Symmetric Field Encryption (Fernet)

Encrypts secrets we store at rest that aren't already covered by another
mechanism -- currently just the long-lived Page Access Token obtained via
the "Connect with Facebook" OAuth flow (see gateway/routers/facebook_oauth.py).

`Settings.field_encryption_key` was declared from the very start of this
project (see .env.example's generation instructions) but, before this
module, nothing in the codebase actually used it -- `Tenant.meta_access_token`
and the other manually-entered Meta credentials are still stored in
plaintext (see IMPLEMENTATION_STATUS.md). This module is deliberately
narrow in scope: it does not retroactively encrypt those existing plaintext
columns, only the new OAuth-derived token.

`field_encryption_key` is an arbitrary operator-supplied string (the
production check in config.py only rejects placeholders, not malformed
Fernet keys), so it's hashed into a valid 32-byte urlsafe-base64 Fernet key
here rather than requiring the env var itself to already be one.
"""
from __future__ import annotations

import base64
import hashlib

import structlog
from cryptography.fernet import Fernet, InvalidToken

from src.shared.core.config import get_settings

logger = structlog.get_logger(__name__)


def _fernet() -> Fernet:
    settings = get_settings()
    digest = hashlib.sha256(settings.field_encryption_key.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret for storage. Raises if field_encryption_key is unusable."""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str | None:
    """
    Decrypt a secret previously produced by encrypt_secret().

    Never raises -- same degrade-gracefully contract as the rest of this
    codebase's credential-loading helpers (see ai_engine/company_context.py).
    Returns None on any failure (wrong key, corrupted/truncated value, or a
    plaintext value left over from before this module existed) so callers
    can fall back the same way they do for a missing credential.
    """
    if not ciphertext:
        return None
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        logger.warning("field_decryption_failed", error=str(exc)[:200])
        return None

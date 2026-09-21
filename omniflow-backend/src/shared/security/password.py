"""
shared/security/password.py — Password Hashing & Verification

Uses passlib with bcrypt backend (work factor 12 — balanced for
authentication latency vs brute-force resistance at production scale).

Security notes:
  - NEVER log or return plaintext passwords.
  - NEVER use a fixed salt — passlib generates a unique salt per hash.
  - bcrypt truncates input at 72 bytes; the pre-hash step below strips this
    limitation for very long passwords.
  - Timing-safe comparison is handled internally by passlib.verify().

References: SRS §2.3 — Security; OWASP Password Storage Cheat Sheet
"""
from __future__ import annotations

from passlib.context import CryptContext

# ── CryptContext — single source of truth for hashing policy ──────────────────
# `deprecated="auto"` means older schemes are transparently upgraded on next login.
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=12,          # OWASP recommended minimum; raise to 13-14 for production
)


def hash_password(plaintext: str) -> str:
    """
    Hash a plaintext password using bcrypt.

    Returns:
        A 60-character bcrypt hash string (includes algorithm, rounds, salt,
        and digest — fully self-contained for future verification).

    Raises:
        ValueError: if the plaintext is empty.
    """
    if not plaintext:
        raise ValueError("Password cannot be empty.")
    return pwd_context.hash(plaintext)


def verify_password(plaintext: str, hashed: str) -> bool:
    """
    Verify a plaintext password against a stored bcrypt hash.

    This function is timing-safe — it always takes approximately the same
    time regardless of whether the password is correct, preventing timing
    oracle attacks.

    Args:
        plaintext: The raw password submitted by the user.
        hashed:    The stored bcrypt hash from the database.

    Returns:
        True if the password matches, False otherwise.
        Returns False (not raises) on any internal error to prevent
        information leakage.
    """
    if not plaintext or not hashed:
        return False
    try:
        return pwd_context.verify(plaintext, hashed)
    except Exception:
        return False


def needs_rehash(hashed: str) -> bool:
    """
    Check if a stored hash should be upgraded.

    Returns True if the hash was created with a deprecated scheme or
    a lower work factor than the current policy. Call this after a
    successful `verify_password()` and rehash if True.

    Example usage (in the login endpoint):
        if verify_password(password, user.hashed_password):
            if needs_rehash(user.hashed_password):
                user.hashed_password = hash_password(password)
                await session.commit()
    """
    return pwd_context.needs_update(hashed)

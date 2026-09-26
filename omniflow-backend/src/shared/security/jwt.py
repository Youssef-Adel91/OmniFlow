"""
shared/security/jwt.py — JWT Token Generation & Validation

Sprint 13 Implementation:
  - HS256 signed tokens via python-jose
  - Access  token: 30-minute TTL (configurable)
  - Refresh token: 7-day TTL (configurable)
  - Claims: sub (user_id), tenant_id, role, email, type
  - Blacklisting via Redis SET for immediate revocation on logout

References: SRS §2.3 — Security; Sprint 13 Auth
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from jose import JWTError, jwt

from src.shared.core.config import get_settings

logger = structlog.get_logger(__name__)

_settings = get_settings()

# ── Token type discriminator ───────────────────────────────────────────────────
_TOKEN_TYPE_ACCESS  = "access"
_TOKEN_TYPE_REFRESH = "refresh"

# ── Redis key prefix for blacklisted JTIs ─────────────────────────────────────
_BLACKLIST_PREFIX = "jwt:blacklist:"


# ══════════════════════════════════════════════════════════════════════════════
# Token Creation
# ══════════════════════════════════════════════════════════════════════════════

def create_access_token(
    *,
    user_id:   str,
    tenant_id: str,
    email:     str,
    role:      str,
    onboarding_status: str,
) -> str:
    """
    Create a short-lived JWT access token.

    Claims embedded (all registered + private):
        sub       — user_id (RFC 7519 subject)
        tenant_id — PostgreSQL RLS partition key
        email     — convenience claim for UI
        role      — admin | agent | auditor
        onboarding_status — tracking tenant onboarding flow
        type      — "access" (discriminator, prevents refresh tokens being used as access)
        jti       — unique token ID for blacklisting
        iat / exp — standard timestamps

    Token is signed with HS256 using jwt_secret_key from settings.
    """
    now = datetime.now(tz=timezone.utc)
    expire = now + timedelta(minutes=_settings.jwt_access_token_expire_minutes)
    jti = str(uuid.uuid4())

    payload: dict[str, Any] = {
        "sub":       user_id,
        "tenant_id": tenant_id,
        "email":     email,
        "role":      role,
        "onboarding_status": onboarding_status,
        "type":      _TOKEN_TYPE_ACCESS,
        "jti":       jti,
        "iat":       now,
        "exp":       expire,
    }

    token = jwt.encode(
        payload,
        _settings.jwt_secret_key,
        algorithm=_settings.jwt_algorithm,
    )
    logger.debug("jwt_access_token_created", user_id=user_id, tenant_id=tenant_id, jti=jti)
    return token


def create_refresh_token(
    *,
    user_id:   str,
    tenant_id: str,
) -> str:
    """
    Create a long-lived JWT refresh token.

    Carries minimal claims (sub + tenant_id only) to limit exposure.
    Must NOT be accepted by endpoints that require an access token —
    the `type` claim discriminator enforces this.
    """
    now = datetime.now(tz=timezone.utc)
    expire = now + timedelta(days=_settings.jwt_refresh_token_expire_days)
    jti = str(uuid.uuid4())

    payload: dict[str, Any] = {
        "sub":       user_id,
        "tenant_id": tenant_id,
        "type":      _TOKEN_TYPE_REFRESH,
        "jti":       jti,
        "iat":       now,
        "exp":       expire,
    }

    return jwt.encode(
        payload,
        _settings.jwt_secret_key,
        algorithm=_settings.jwt_algorithm,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Token Verification
# ══════════════════════════════════════════════════════════════════════════════

def verify_access_token(token: str) -> dict[str, Any]:
    """
    Validate and decode a JWT access token.

    Security checks (in order):
      1. Signature verification (HS256 + jwt_secret_key)
      2. Expiry check (`exp` claim)
      3. Token type discrimination (`type` == "access")
      4. Required claims presence (sub, tenant_id, role)

    Raises:
        JWTError: on any validation failure (expired, tampered, wrong type)

    Returns:
        Decoded payload dict on success.

    Callers must also check the Redis blacklist for revoked tokens;
    use `is_token_blacklisted()` for that.
    """
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            _settings.jwt_secret_key,
            algorithms=[_settings.jwt_algorithm],
        )
    except JWTError as exc:
        logger.warning("jwt_decode_failed", error=str(exc))
        raise

    # Discriminate token type — prevents refresh tokens being used as access tokens
    if payload.get("type") != _TOKEN_TYPE_ACCESS:
        logger.warning("jwt_wrong_type", token_type=payload.get("type"))
        raise JWTError("Invalid token type — expected 'access'")

    # Validate required claims
    for claim in ("sub", "tenant_id", "role"):
        if not payload.get(claim):
            raise JWTError(f"Missing required claim: {claim}")

    return payload


# ── Clerk Authentication ────────────────────────────────────────────────────────
import jwt as pyjwt
from jwt import PyJWKClient

# Cache for PyJWKClients keyed by JWKS URL
_jwk_clients: dict[str, PyJWKClient] = {}


def _clerk_issuer() -> str:
    """Resolve the trusted issuer from server configuration, never from a JWT."""
    import base64
    from urllib.parse import urlsplit

    issuer = _settings.clerk_issuer_url.strip().rstrip("/")
    if not issuer:
        key = _settings.clerk_publishable_key
        if not key.startswith(("pk_test_", "pk_live_")):
            raise JWTError("Configure CLERK_ISSUER_URL or CLERK_PUBLISHABLE_KEY")
        encoded = key.split("_", 2)[2]
        domain = base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode().rstrip("$")
        issuer = f"https://{domain}"
    parsed = urlsplit(issuer)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise JWTError("Invalid configured Clerk issuer")
    return issuer


def verify_clerk_token(token: str) -> dict[str, Any]:
    """
    Validate and decode a Clerk JWT access token using PyJWT.
    
    1. Rejects issuers other than the configured Clerk instance before any I/O.
    2. Fetches keys only from that trusted instance (cached).
    3. Verifies the RSA signature, issuer, expiry and required subject.
    """
    try:
        unverified_payload = pyjwt.decode(token, options={"verify_signature": False})
        
        issuer = _clerk_issuer()
        if unverified_payload.get("iss") != issuer:
            raise JWTError("Unexpected token issuer")
            
        jwks_url = f"{issuer}/.well-known/jwks.json"
        
        if jwks_url not in _jwk_clients:
            _jwk_clients[jwks_url] = PyJWKClient(jwks_url)
            
        jwks_client = _jwk_clients[jwks_url]
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        
        payload = pyjwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=issuer,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
        if not payload.get("sub"):
            raise JWTError("Missing subject")
        if payload.get("azp") and payload["azp"] not in _settings.allowed_origins_list:
            raise JWTError("Unauthorized token origin")
        return payload
    except Exception as e:
        logger.warning("clerk_jwt_decode_failed", error=str(e))
        raise JWTError(f"Invalid Clerk token: {str(e)}")


def decode_clerk_token_unsafe(token: str) -> dict[str, Any] | None:
    """
    Decode a Clerk (RS256) JWT WITHOUT verifying the signature or expiry.

    ⚠️  Use ONLY to read non-security-sensitive claims — currently just
    `jti`/`sid`/`exp` for logout blacklisting. NEVER use the result for an
    authorization decision.

    `decode_token_unsafe()` below cannot be reused for Clerk tokens: it goes
    through python-jose configured for the local HS256 algorithm and rejects
    RS256 tokens.
    """
    try:
        return pyjwt.decode(token, options={"verify_signature": False})
    except Exception as exc:  # malformed token — caller treats as "no claims"
        logger.debug("clerk_jwt_unsafe_decode_failed", error=str(exc)[:200])
        return None


def get_revocation_id(payload: dict[str, Any]) -> str | None:
    """
    Return the claim used as the blacklist key for a token.

    Clerk session tokens carry `sid` (session id); locally-minted legacy
    tokens carry `jti`. Both are checked so a single blacklist works for
    either shape.
    """
    for claim in ("jti", "sid"):
        value = payload.get(claim)
        if value:
            return str(value)
    return None


def decode_token_unsafe(token: str) -> dict[str, Any] | None:
    """
    Decode a JWT WITHOUT signature verification.

    ⚠️  Use ONLY for reading non-security-sensitive fields (e.g. extracting
    `jti` from an already-expired token during logout). Never trust the
    returned payload for authorization decisions.
    """
    try:
        return jwt.decode(
            token,
            _settings.jwt_secret_key,
            algorithms=[_settings.jwt_algorithm],
            options={"verify_exp": False, "verify_signature": False},
        )
    except JWTError:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Redis Blacklist (Token Revocation)
# ══════════════════════════════════════════════════════════════════════════════

async def blacklist_token(jti: str, ttl_seconds: int) -> None:
    """
    Add a token's JTI to the Redis blacklist.

    Called on logout or forced revocation. The TTL is set to the token's
    remaining lifetime so Redis auto-expires the entry cleanly.

    Args:
        jti:         The JWT ID (`jti` claim) to revoke.
        ttl_seconds: Remaining lifetime of the token in seconds.
    """
    try:
        from src.shared.redis_client.client import redis_mgr
        redis = await redis_mgr.get_client()
        await redis.set(
            f"{_BLACKLIST_PREFIX}{jti}",
            "1",
            ex=max(1, ttl_seconds),  # minimum 1s to avoid immediate deletion
        )
        logger.info("jwt_token_blacklisted", jti=jti)
    except Exception as exc:
        # Non-fatal in dev — log loudly but don't crash logout flow
        logger.error("jwt_blacklist_write_failed", jti=jti, error=str(exc))


async def is_token_blacklisted(jti: str) -> bool:
    """
    Check whether a token has been revoked (exists in Redis blacklist).

    Returns True if the token should be rejected.
    Fails OPEN on Redis error in production (rejects the token) to prevent
    bypass via infrastructure failure. In development, fails CLOSED
    (accepts the token) to avoid breaking local dev without Redis.
    """
    try:
        from src.shared.redis_client.client import redis_mgr
        redis = await redis_mgr.get_client()
        result = await redis.get(f"{_BLACKLIST_PREFIX}{jti}")
        return result is not None
    except Exception as exc:
        logger.error("jwt_blacklist_read_failed", jti=jti, error=str(exc))
        # Fail CLOSED in dev, OPEN in production
        return _settings.is_production

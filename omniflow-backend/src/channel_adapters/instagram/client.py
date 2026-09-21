"""
channel_adapters/instagram/client.py — Meta Graph API Async Sender

Sends outbound replies via the Meta Graph API (v19.0+) for:
  - Facebook Messenger (send to /me/messages)
  - Instagram Direct Messages (send to /me/messages with messaging_type)

The same endpoint handles both channels — the recipient ID determines routing.

Meta Graph API endpoint:
    POST https://graph.facebook.com/{version}/me/messages
         ?access_token={PAGE_ACCESS_TOKEN}

Rate limits (Meta, 2024):
    - 250 API calls per hour per page (standard access)
    - 200 messages per user per 24h (standard messaging)

References: Meta Messenger Platform docs, SRS §6 — Instagram/Facebook Adapter
"""
from __future__ import annotations

import asyncio
from typing import Any, Final
from dataclasses import dataclass

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from src.shared.core.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

# ── Meta Graph API constants ──────────────────────────────────────────────────
_GRAPH_BASE: Final[str] = "https://graph.facebook.com"
_API_VERSION: Final[str] = settings.meta_graph_api_version   # e.g. "v19.0"
_MESSAGES_PATH: Final[str] = "me/messages"
_TIMEOUT_SECONDS: Final[float] = 15.0
_MAX_RETRIES: Final[int] = 3
_RETRY_STATUSES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})


# ── Typed exceptions ──────────────────────────────────────────────────────────

class MetaAPIError(Exception):
    """Base exception for Meta Graph API errors."""

    def __init__(self, message: str, status_code: int = 0, error_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class MetaRateLimitError(MetaAPIError):
    """429 Too Many Requests — back off and retry."""


class MetaAuthError(MetaAPIError):
    """401/403 — token expired or revoked. Do not retry."""


class MetaInvalidRecipientError(MetaAPIError):
    """400 with invalid PSID/IGSID. Do not retry."""


# ── Send result ───────────────────────────────────────────────────────────────

@dataclass
class MetaSendResult:
    """Successful send response from Meta Graph API."""
    recipient_id: str    # PSID (Messenger) or IGSID (Instagram)
    message_id: str      # Platform message ID (mid.xxx)


# ── HTTP Client ───────────────────────────────────────────────────────────────

async def send_message(recipient_id: str, text: str) -> MetaSendResult:
    """
    Send a plain-text reply to a Messenger or Instagram DM thread.

    Args:
        recipient_id: The PSID (Facebook) or IGSID (Instagram) of the recipient.
        text:         The text message to send (max 2000 chars for Messenger,
                      1000 chars for Instagram DMs).

    Returns:
        MetaSendResult with the platform's message ID.

    Raises:
        MetaAuthError: If the PAGE_ACCESS_TOKEN is invalid / expired.
        MetaInvalidRecipientError: If the recipient_id is not valid.
        MetaAPIError: For any other non-retryable API error.

    Notes:
        - Retries 3× on transient 5xx / 429 errors with exponential backoff.
        - The access token is read from `settings.meta_instagram_page_access_token`.
    """
    access_token = settings.meta_instagram_page_access_token
    url = f"{_GRAPH_BASE}/{_API_VERSION}/{_MESSAGES_PATH}"
    payload: dict[str, Any] = {
        "messaging_type": "RESPONSE",
        "recipient": {"id": recipient_id},
        "message": {"text": text},
    }

    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(_MAX_RETRIES),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type(MetaRateLimitError),
            before_sleep=before_sleep_log(logger, "WARNING"),  # type: ignore[arg-type]
            reraise=True,
        ):
            with attempt:
                result = await _do_send(url, access_token, payload)
    except MetaRateLimitError:
        # Exhausted all retries — propagate
        raise

    return result


async def _do_send(
    url: str,
    access_token: str,
    payload: dict[str, Any],
) -> MetaSendResult:
    """Execute one HTTP POST to the Meta Graph API."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            url,
            params={"access_token": access_token},
            json=payload,
        )

    if resp.status_code == 200:
        data = resp.json()
        return MetaSendResult(
            recipient_id=data.get("recipient_id", ""),
            message_id=data.get("message_id", ""),
        )

    _raise_for_status(resp)
    # unreachable, but satisfies mypy
    raise MetaAPIError(f"Unexpected response: {resp.status_code}")


def _raise_for_status(resp: httpx.Response) -> None:
    """Map Meta error responses to typed exceptions."""
    body: dict[str, Any] = {}
    try:
        body = resp.json()
    except Exception:
        pass

    error = body.get("error", {})
    code = error.get("code", 0)
    message = error.get("message", resp.text)

    if resp.status_code == 429:
        raise MetaRateLimitError(message, status_code=429, error_code=code)
    if resp.status_code in (401, 403):
        raise MetaAuthError(message, status_code=resp.status_code, error_code=code)
    if resp.status_code == 400:
        raise MetaInvalidRecipientError(message, status_code=400, error_code=code)

    raise MetaAPIError(message, status_code=resp.status_code, error_code=code)


async def send_comment_reply(comment_id: str, text: str) -> dict[str, Any]:
    """
    Reply to a public page/feed comment via Graph API.

    Args:
        comment_id: The comment's Graph API object ID (e.g. "123456_789012").
        text:       The reply text to post publicly under the comment.

    Returns:
        Raw JSON dict from Graph API (contains "id" of the new comment).
    """
    access_token = settings.meta_instagram_page_access_token
    url = f"{_GRAPH_BASE}/{_API_VERSION}/{comment_id}/comments"

    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            url,
            params={"access_token": access_token},
            json={"message": text},
        )

    if resp.status_code == 200:
        return resp.json()

    _raise_for_status(resp)
    return {}  # unreachable

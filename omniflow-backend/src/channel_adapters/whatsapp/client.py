"""
channel_adapters/whatsapp/client.py — WhatsApp Cloud API Async Client

Sends outbound messages via the Meta WhatsApp Cloud API (Graph API v21.0).

Responsibilities:
  - Send text, template, interactive, and media messages
  - Handle rate limits (80 msg/s per phone number ID)
  - Retry on transient errors (5xx, 429) with exponential backoff
  - Log every request with wamid for traceability
  - Raise typed exceptions for unrecoverable errors (4xx auth failures)

Meta Graph API endpoint:
    POST https://graph.facebook.com/{version}/{phone_number_id}/messages
    Authorization: Bearer {access_token}

Rate limits (Meta Cloud API, 2024):
    - 80 messages/second per phone number (shared across all recipients)
    - 1000 unique recipients/24h on trial accounts
    - No limit on number of messages per recipient (Business tier)

References: SRS §5 — WhatsApp Channel Adapter, Meta Cloud API docs
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
_API_VERSION: Final[str] = settings.meta_graph_api_version
_TIMEOUT_SECONDS: Final[float] = 15.0
_MAX_RETRIES: Final[int] = 3
_RETRY_STATUSES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})


# ── Typed exceptions ──────────────────────────────────────────────────────────

class WhatsAppAPIError(Exception):
    """Base exception for WhatsApp API errors."""
    def __init__(self, message: str, status_code: int = 0, error_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code  # Meta's own error code


class WhatsAppRateLimitError(WhatsAppAPIError):
    """429 Too Many Requests — back off and retry."""


class WhatsAppAuthError(WhatsAppAPIError):
    """401/403 — token expired or revoked. Do not retry."""


class WhatsAppInvalidRecipientError(WhatsAppAPIError):
    """400 with code 131026 — phone number not on WhatsApp. Do not retry."""


# ── Send result ───────────────────────────────────────────────────────────────

@dataclass
class SendResult:
    """Successful send response from Meta API."""
    wamid: str          # WhatsApp message ID (e.g., "wamid.xxx")
    phone_number: str
    message_status: str  # "accepted" on success


# ══════════════════════════════════════════════════════════════════════════════
# WhatsAppClient
# ══════════════════════════════════════════════════════════════════════════════

class WhatsAppClient:
    """
    Async Meta WhatsApp Cloud API client.

    Usage (singleton recommended — reuse the httpx client pool):
        client = WhatsAppClient()
        await client.start()
        result = await client.send_text_message(
            phone_number_id="1234567890",
            to="+966501234567",
            text="أهلاً! كيف يمكنني مساعدتك؟",
            access_token="EAAxxxx",
        )
        print(result.wamid)
        await client.stop()
    """

    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None

    async def start(self) -> None:
        """Initialize the shared httpx client with connection pooling."""
        if self._http:
            return
        self._http = httpx.AsyncClient(
            base_url=f"{_GRAPH_BASE}/{_API_VERSION}",
            timeout=httpx.Timeout(
                connect=5.0,
                read=_TIMEOUT_SECONDS,
                write=10.0,
                pool=5.0,
            ),
            limits=httpx.Limits(
                max_connections=50,
                max_keepalive_connections=20,
                keepalive_expiry=30,
            ),
            http2=True,     # Meta Graph API supports HTTP/2
            headers={
                # Deliberately NOT setting Content-Type here — found via a
                # real live send that this httpx version applies a
                # client-level default Content-Type even to multipart
                # requests, overriding the auto-generated
                # "multipart/form-data; boundary=..." that `files=` should
                # produce in upload_media(). httpx sets the correct
                # Content-Type per-request automatically for both
                # `json=` (send_*) and `files=` (upload_media) — a client
                # default only breaks that.
                "User-Agent": f"OmniFlowAI/2.0 (+https://omniflow.ai)",
            },
        )
        logger.info("whatsapp_client_started", api_version=_API_VERSION)

    async def stop(self) -> None:
        """Close the httpx client and release connections."""
        if self._http:
            await self._http.aclose()
            self._http = None
            logger.info("whatsapp_client_stopped")

    @property
    def _client(self) -> httpx.AsyncClient:
        if not self._http:
            raise RuntimeError("WhatsAppClient not started. Call start() first.")
        return self._http

    # ══════════════════════════════════════════════════════════════════════════
    # Public send methods
    # ══════════════════════════════════════════════════════════════════════════

    async def send_text_message(
        self,
        *,
        phone_number_id: str,
        to: str,
        text: str,
        access_token: str,
        preview_url: bool = False,
        reply_to_message_id: str | None = None,
    ) -> SendResult:
        """
        Send a plain text message to a WhatsApp user.

        Args:
            phone_number_id   — Meta phone number ID (from tenant settings)
            to                — Recipient's E.164 phone (e.g., "+966501234567")
            text              — Message body (max 4096 chars, supports emojis)
            access_token      — Tenant's Meta system user access token
            preview_url       — Enable link preview in message
            reply_to_message_id — Optional wamid to reply to a specific message

        Returns:
            SendResult with wamid

        Raises:
            WhatsAppRateLimitError  — 429 after max retries
            WhatsAppAuthError       — 401/403 (token invalid)
            WhatsAppAPIError        — Other unrecoverable errors
        """
        # Normalize recipient — Meta API requires E.164 without '+'
        normalized_to = to.lstrip("+")

        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": normalized_to,
            "type": "text",
            "text": {
                "body": text[:4096],   # hard-cap at Meta's limit
                "preview_url": preview_url,
            },
        }

        if reply_to_message_id:
            payload["context"] = {"message_id": reply_to_message_id}

        return await self._send(
            phone_number_id=phone_number_id,
            payload=payload,
            access_token=access_token,
            recipient=normalized_to,
        )

    async def send_audio_message(
        self,
        *,
        phone_number_id: str,
        to: str,
        audio_url: str,
        access_token: str,
        caption: str | None = None,
    ) -> SendResult:
        """
        Send a WhatsApp Voice Note (audio media message) via a link URL.

        Uses Meta's ``audio`` message type with a ``link`` field, which instructs
        WhatsApp to fetch the audio from the given HTTPS URL.  This avoids
        uploading media to Meta's servers in advance (no Media Upload API call
        required), trading upload time for CDN latency.

        Supported audio formats (Meta Cloud API, 2024):
            audio/aac, audio/mp4, audio/mpeg, audio/amr, audio/ogg (Opus codec)
            Max size: 16 MB

        WhatsApp renders the message as a playable voice note with waveform.

        Args:
            phone_number_id — Meta phone number ID (from tenant settings).
            to              — Recipient's E.164 phone (e.g., \"+966501234567\").
            audio_url       — Public HTTPS URL pointing to the audio file.
                              Must be reachable by Meta's servers (no auth).
            access_token    — Tenant's Meta system user access token.
            caption         — Optional text caption shown below the audio.
                              Note: Meta does NOT support captions for audio
                              messages natively; if set, we send a separate
                              text follow-up (reserved for Sprint 15 UX polish).

        Returns:
            SendResult with wamid of the audio message.
        """
        normalized_to = to.lstrip("+")

        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": normalized_to,
            "type": "audio",
            "audio": {
                "link": audio_url,
            },
        }

        result = await self._send(
            phone_number_id=phone_number_id,
            payload=payload,
            access_token=access_token,
            recipient=normalized_to,
        )

        logger.info(
            "whatsapp_audio_sent",
            wamid=result.wamid,
            recipient=normalized_to,
            audio_url=audio_url,
        )
        return result

    async def upload_media(
        self,
        *,
        phone_number_id: str,
        file_bytes: bytes,
        filename: str,
        mime_type: str,
        access_token: str,
    ) -> str:
        """
        Upload a file to Meta's Media API and return its media ID.

        Meta hosts the file temporarily (media IDs expire after 30 days);
        this lets us send document/image/audio attachments we generated
        ourselves (e.g. a VCard) without needing our own public storage URL.

        POST /{phone_number_id}/media (multipart/form-data), distinct from
        the JSON /{phone_number_id}/messages endpoint every other method
        here uses — Meta requires the file as actual multipart form data.
        """
        url = f"/{phone_number_id}/media"
        headers = {"Authorization": f"Bearer {access_token}"}
        files = {"file": (filename, file_bytes, mime_type)}
        data = {"messaging_product": "whatsapp", "type": mime_type}

        # httpx overrides the client's default application/json Content-Type
        # with the correct multipart boundary automatically when `files=` is
        # passed — no manual header surgery needed.
        response = await self._client.post(url, headers=headers, files=files, data=data)

        if response.status_code != 200:
            self._raise_for_status(response, logger.bind(phone_number_id=phone_number_id))

        media_id = response.json().get("id", "")
        logger.info("whatsapp_media_uploaded", media_id=media_id, filename=filename, mime_type=mime_type)
        return media_id

    async def send_document_message(
        self,
        *,
        phone_number_id: str,
        to: str,
        media_id: str,
        filename: str,
        access_token: str,
        caption: str | None = None,
    ) -> SendResult:
        """Send a document (e.g. a VCard .vcf) by previously-uploaded media ID."""
        normalized_to = to.lstrip("+")

        document: dict[str, Any] = {"id": media_id, "filename": filename}
        if caption:
            document["caption"] = caption[:1024]

        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": normalized_to,
            "type": "document",
            "document": document,
        }

        return await self._send(
            phone_number_id=phone_number_id,
            payload=payload,
            access_token=access_token,
            recipient=normalized_to,
        )

    async def send_template_message(
        self,
        *,
        phone_number_id: str,
        to: str,
        template_name: str,
        language_code: str = "ar",
        components: list[dict[str, Any]] | None = None,
        access_token: str,
    ) -> SendResult:
        """
        Send a pre-approved WhatsApp template message.

        Required for:
          - First outbound message to a user (24h session not yet open)
          - Broadcast / VIP campaigns (Sprint 10)
          - Re-engagement after session expiry
        """
        normalized_to = to.lstrip("+")
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": normalized_to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
            },
        }
        if components:
            payload["template"]["components"] = components

        return await self._send(
            phone_number_id=phone_number_id,
            payload=payload,
            access_token=access_token,
            recipient=normalized_to,
        )

    async def mark_as_read(
        self,
        *,
        phone_number_id: str,
        message_id: str,
        access_token: str,
    ) -> None:
        """
        Send a read receipt for an inbound message.
        Shows blue ticks on the customer's phone.
        Fire-and-forget — failures are logged but not raised.
        """
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        try:
            await self._send(
                phone_number_id=phone_number_id,
                payload=payload,
                access_token=access_token,
                recipient=message_id,
            )
        except Exception as exc:
            logger.warning("whatsapp_read_receipt_failed", message_id=message_id, error=str(exc))

    async def send_typing_indicator(
        self,
        *,
        phone_number_id: str,
        to: str,
        access_token: str,
    ) -> None:
        """
        Send a typing indicator ("..." bubble) before the AI response.
        Improves UX — makes the bot feel more natural.
        Fire-and-forget — failures are ignored.
        """
        # Meta doesn't have a native typing indicator for Cloud API.
        # Workaround: none (this is a placeholder for future WhatsApp Business Features)
        # Some implementations use a brief delay before sending the actual message.
        logger.debug("whatsapp_typing_indicator_placeholder", to=to)

    # ══════════════════════════════════════════════════════════════════════════
    # Core HTTP dispatch — all send methods call this
    # ══════════════════════════════════════════════════════════════════════════

    async def _send(
        self,
        *,
        phone_number_id: str,
        payload: dict[str, Any],
        access_token: str,
        recipient: str,
    ) -> SendResult:
        """
        Execute the POST to Meta Graph API with tenacity retry.

        Retry policy:
            - Retries on: 429, 500, 502, 503, 504, httpx.ConnectError
            - Does NOT retry: 400, 401, 403 (caller error — won't self-heal)
            - Max 3 attempts with exponential backoff (2s, 4s, 8s)

        Meta error code reference:
            130429 — Rate limit hit (message throughput)
            131026 — Recipient not on WhatsApp
            131047 — Re-engagement message outside 24h window (use template)
        """
        url = f"/{phone_number_id}/messages"
        headers = {"Authorization": f"Bearer {access_token}"}

        log = logger.bind(
            phone_number_id=phone_number_id,
            recipient=recipient,
            msg_type=payload.get("type"),
        )

        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type(
                (WhatsAppRateLimitError, httpx.ConnectError, httpx.RemoteProtocolError)
            ),
            stop=stop_after_attempt(_MAX_RETRIES),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            reraise=True,
        ):
            with attempt:
                try:
                    response = await self._client.post(
                        url, json=payload, headers=headers
                    )
                except httpx.TimeoutException as exc:
                    log.error("whatsapp_api_timeout", error=str(exc))
                    raise WhatsAppAPIError(f"Request timed out: {exc}") from exc

                log.debug(
                    "whatsapp_api_response",
                    status=response.status_code,
                    attempt=attempt.retry_state.attempt_number,
                )

                # ── Parse response ────────────────────────────────────────────
                if response.status_code == 200:
                    return self._parse_success(response, recipient)

                self._raise_for_status(response, log)

        # unreachable — tenacity reraises
        raise WhatsAppAPIError("Max retries exceeded")

    @staticmethod
    def _parse_success(response: httpx.Response, recipient: str) -> SendResult:
        """Extract wamid from a successful 200 response."""
        try:
            data = response.json()
            messages = data.get("messages", [{}])
            wamid = messages[0].get("id", "") if messages else ""
            status = messages[0].get("message_status", "accepted") if messages else "accepted"
            logger.info(
                "whatsapp_message_sent",
                wamid=wamid,
                recipient=recipient,
                status=status,
            )
            return SendResult(wamid=wamid, phone_number=recipient, message_status=status)
        except Exception as exc:
            logger.warning("whatsapp_response_parse_warning", error=str(exc))
            return SendResult(wamid="", phone_number=recipient, message_status="accepted")

    @staticmethod
    def _raise_for_status(response: httpx.Response, log: Any) -> None:
        """Raise typed exception based on Meta HTTP status and error code."""
        try:
            body = response.json()
            error_obj = body.get("error", {})
            error_code = error_obj.get("code", 0)
            error_msg = error_obj.get("message", response.text[:200])
            fbtrace_id = error_obj.get("fbtrace_id", "")
        except Exception:
            error_code = 0
            error_msg = response.text[:200]
            fbtrace_id = ""

        log.error(
            "whatsapp_api_error",
            status=response.status_code,
            error_code=error_code,
            error_msg=error_msg,
            fbtrace_id=fbtrace_id,
        )

        if response.status_code == 429 or error_code == 130429:
            raise WhatsAppRateLimitError(
                f"Meta rate limit: {error_msg}",
                status_code=429,
                error_code=error_code,
            )

        if response.status_code in (401, 403):
            raise WhatsAppAuthError(
                f"Meta auth failed (check access token): {error_msg}",
                status_code=response.status_code,
                error_code=error_code,
            )

        if response.status_code == 400 and error_code == 131026:
            raise WhatsAppInvalidRecipientError(
                f"Recipient not on WhatsApp: {error_msg}",
                status_code=400,
                error_code=error_code,
            )

        # All other errors
        raise WhatsAppAPIError(
            f"Meta API error {response.status_code}/{error_code}: {error_msg}",
            status_code=response.status_code,
            error_code=error_code,
        )


# ── Module-level singleton ─────────────────────────────────────────────────────
whatsapp_client = WhatsAppClient()

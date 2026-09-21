"""
shared/services/tts_client.py — Text-to-Speech Service Client (Sprint 14)

Converts AI-generated text responses into WhatsApp-deliverable audio files
using ElevenLabs Multilingual v2.  Includes a Mock Mode that requires zero
external dependencies, making it safe for local development and CI.

Pipeline role:
    LLMInvokerWorker
        └─► TTSClient.synthesize(text)          ← THIS FILE
              ├─► [Mock]  return a static hosted MP3 URL (no API call)
              └─► [Real]  POST to ElevenLabs TTS endpoint
                          → receive audio bytes
                          → upload to S3 public assets bucket
                          → return pre-signed / permanent S3 HTTPS URL

Trigger conditions (controlled by LLMInvokerWorker):
    - Customer is VIP  (is_vip flag from session_state)
    - LLM routing tier is L3 (premium, deep consultation)
    - Customer message contains an explicit voice request keyword
    - Feature flag FEATURE_VOICE_NOTES_ENABLED = true

ElevenLabs API references:
    POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}
    Header: xi-api-key: <ELEVENLABS_API_KEY>
    Body:   {"text": "...", "model_id": "eleven_multilingual_v2", ...}
    Response: audio/mpeg binary stream

Mock Mode:
    Set ELEVENLABS_API_KEY=mock  (or leave it blank) in .env.
    A static, royalty-free MP3 is returned immediately.
    All mock calls are logged at DEBUG level with `tts_mock_mode=True`.

Production Notes:
    - Arabic is fully supported by ElevenLabs Multilingual v2.
    - Recommended Arabic voice: "Omar" (voice_id in settings).
    - Audio format: MP3, 128kbps, mono — optimal for WhatsApp voice notes.
    - WhatsApp accepts: audio/mpeg, audio/ogg, audio/mp4 (max 16MB).
    - ElevenLabs rate limit: 2 concurrent requests on Starter plan.
      Scale with an asyncio.Semaphore (configurable via settings).
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Final

import httpx
import structlog

from src.shared.core.config import get_settings
from src.shared.services.storage_client import storage_client

logger = structlog.get_logger(__name__)
settings = get_settings()

# ── ElevenLabs API constants ──────────────────────────────────────────────────
_ELEVENLABS_BASE_URL: Final[str] = "https://api.elevenlabs.io/v1"
_TTS_ENDPOINT: Final[str] = "/text-to-speech/{voice_id}"
_DEFAULT_MODEL: Final[str] = "eleven_multilingual_v2"
_DEFAULT_VOICE_ID: Final[str] = "pNInz6obpgDQGcFmaJgB"  # ElevenLabs "Adam" (multilingual)
_AUDIO_FORMAT: Final[str] = "mp3_44100_128"
_MAX_CHARS: Final[int] = 2500   # ElevenLabs context window per call
_REQUEST_TIMEOUT: Final[float] = 30.0  # TTS generation can be slow for long texts

# Mock audio URL — publicly hosted royalty-free MP3 for dev / CI use
_MOCK_AUDIO_URL: Final[str] = (
    "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3"
)


# ══════════════════════════════════════════════════════════════════════════════
# TTSClient
# ══════════════════════════════════════════════════════════════════════════════

class TTSClient:
    """
    Async Text-to-Speech client backed by ElevenLabs.

    Automatically switches to Mock Mode when:
        - ELEVENLABS_API_KEY is absent, empty, or set to "mock"

    Thread-safety: safe for concurrent use; a shared httpx.AsyncClient pool
    is created on first call and reused across all requests.

    Concurrency guard: _semaphore limits simultaneous ElevenLabs calls to
    avoid hitting the API's concurrent-request limit.

    Usage:
        url = await tts_client.synthesize("مرحباً، كيف يمكنني مساعدتك؟")
        # Returns an HTTPS URL pointing to audio/mpeg content
    """

    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None
        self._semaphore: asyncio.Semaphore | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def _ensure_client(self) -> None:
        """Lazily initialise httpx client and concurrency semaphore."""
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=_ELEVENLABS_BASE_URL,
                timeout=httpx.Timeout(
                    connect=5.0,
                    read=_REQUEST_TIMEOUT,
                    write=10.0,
                    pool=5.0,
                ),
                headers={
                    "Accept": "audio/mpeg",
                    "Content-Type": "application/json",
                    "User-Agent": "OmniFlowAI/2.0 (TTS)",
                },
            )
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(
                settings.tts_max_concurrent_requests
            )

    async def close(self) -> None:
        """Release the httpx connection pool."""
        if self._http:
            await self._http.aclose()
            self._http = None

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def is_mock(self) -> bool:
        """True when running in Mock Mode (no real API calls)."""
        key = settings.elevenlabs_api_key
        return not key or key.strip().lower() == "mock"

    async def synthesize(
        self,
        text: str,
        *,
        voice_id: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        """
        Convert text to speech and return a public HTTPS URL to the audio.

        Args:
            text      — The text to synthesise (Arabic or English).
                        Truncated to _MAX_CHARS to stay within API limits.
            voice_id  — ElevenLabs voice ID override (uses default if None).
            tenant_id — Tenant UUID string for log correlation.

        Returns:
            A public HTTPS URL pointing to an audio/mpeg resource.
            - Mock Mode: a static hosted MP3 URL (no API call).
            - Real Mode: S3 URL after upload.

        Raises:
            TTSGenerationError — on non-retryable ElevenLabs API failures.
            httpx.TimeoutException — propagated to caller for retry handling.
        """
        text_trimmed = text.strip()[:_MAX_CHARS]

        if self.is_mock:
            return await self._synthesize_mock(text_trimmed, tenant_id=tenant_id)

        return await self._synthesize_real(
            text_trimmed,
            voice_id=voice_id or settings.elevenlabs_voice_id or _DEFAULT_VOICE_ID,
            tenant_id=tenant_id,
        )

    # ── Private: Mock Mode ────────────────────────────────────────────────────

    async def _synthesize_mock(
        self,
        text: str,
        *,
        tenant_id: str | None,
    ) -> str:
        """
        Return a static MP3 URL immediately without any network call.

        Simulates a ~200ms TTS latency so that integration tests can observe
        realistic timing without actually waiting for ElevenLabs.
        """
        await asyncio.sleep(0.2)   # Simulate synthesis latency
        logger.debug(
            "tts_mock_mode",
            chars=len(text),
            tenant_id=tenant_id,
            mock_url=_MOCK_AUDIO_URL,
        )
        return _MOCK_AUDIO_URL

    # ── Private: Real Mode ────────────────────────────────────────────────────

    async def _synthesize_real(
        self,
        text: str,
        *,
        voice_id: str,
        tenant_id: str | None,
    ) -> str:
        """
        Call ElevenLabs TTS API, receive audio bytes, upload to S3/storage,
        and return the public audio URL.

        Concurrency-limited by _semaphore to avoid plan-level rate limits.
        """
        self._ensure_client()
        assert self._semaphore is not None  # guaranteed by _ensure_client
        assert self._http is not None

        synthesis_id = str(uuid.uuid4())[:8]
        log = logger.bind(
            synthesis_id=synthesis_id,
            voice_id=voice_id,
            chars=len(text),
            tenant_id=tenant_id,
        )

        payload = {
            "text": text,
            "model_id": _DEFAULT_MODEL,
            "voice_settings": {
                "stability": 0.55,           # Balanced — not too robotic, not too erratic
                "similarity_boost": 0.75,    # High fidelity to voice sample
                "style": 0.20,               # Light expressiveness for Arabic property tone
                "use_speaker_boost": True,
            },
            "output_format": _AUDIO_FORMAT,
        }

        async with self._semaphore:
            log.info("tts_synthesis_started")
            try:
                response = await self._http.post(
                    _TTS_ENDPOINT.format(voice_id=voice_id),
                    json=payload,
                    headers={"xi-api-key": settings.elevenlabs_api_key},
                )
            except httpx.TimeoutException as exc:
                log.error("tts_api_timeout", error=str(exc))
                raise

            if response.status_code != 200:
                log.error(
                    "tts_api_error",
                    status=response.status_code,
                    body=response.text[:300],
                )
                raise TTSGenerationError(
                    f"ElevenLabs returned {response.status_code}: {response.text[:200]}"
                )

            audio_bytes = response.content
            log.info("tts_synthesis_complete", audio_bytes=len(audio_bytes))

        # ── Upload audio to S3 / local storage ────────────────────────────────
        audio_url = await self._upload_audio(
            audio_bytes=audio_bytes,
            synthesis_id=synthesis_id,
            tenant_id=tenant_id,
        )
        log.info("tts_audio_uploaded", url=audio_url)
        return audio_url

    async def _upload_audio(
        self,
        audio_bytes: bytes,
        synthesis_id: str,
        tenant_id: str | None,
    ) -> str:
        """
        Upload raw audio bytes to MinIO / AWS S3 and return a presigned URL.

        Sprint 16: Replaced placeholder with real StorageClient call.

        Object key: audio/tts/{tenant_id_no_dashes}/{synthesis_id}.mp3

        Returns:
            Presigned HTTPS URL valid for 7 days (MinIO or S3).
            Falls back to static mock URL if MinIO is unreachable or not
            configured — the pipeline continues working regardless.
        """
        filename = storage_client.generate_audio_filename(
            synthesis_id=synthesis_id,
            tenant_id=tenant_id,
        )
        url = await storage_client.upload_audio_bytes(
            file_bytes=audio_bytes,
            filename=filename,
            content_type="audio/mpeg",
        )
        logger.info(
            "tts_audio_stored",
            synthesis_id=synthesis_id,
            tenant_id=tenant_id,
            url=url,
            is_minio=bool(_settings.minio_endpoint),
        )
        return url


# ── Typed exception ────────────────────────────────────────────────────────────

class TTSGenerationError(Exception):
    """Raised when ElevenLabs returns a non-200 response."""


# ── Module-level singleton ─────────────────────────────────────────────────────
tts_client = TTSClient()

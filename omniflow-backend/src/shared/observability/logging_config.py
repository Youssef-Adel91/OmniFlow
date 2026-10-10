"""Structured logging with PII/secret redaction, shared by the gateway, every Kafka worker and Celery.

Before this module structlog was never configured: events printed with the default console renderer and whatever fields
callers passed (phone numbers, message text, tokens) went straight to the log stream — and on to any aggregator.

Rules applied to every event (keys AND free-text values):
  * keys that look like secrets (token, secret, password, authorization, api_key, cookie, signature)   → "[redacted]"
  * message bodies (text, body, content, prompt, answer, message_text …)                               → "[redacted:N chars]"
  * phone / email keys, and any phone-/email-looking substring in other values                          → partially masked
  * bearer tokens and well-known key shapes (sk-…, AIza…, EAA…, tok_…) in free text                  → "[redacted]"
Production renders JSON (one object per line); development keeps the readable console renderer.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import structlog

_SECRET_KEY = re.compile(r"(token|secret|password|passwd|authorization|api[_-]?key|apikey|cookie|signature|credential)", re.I)
_BODY_KEY = re.compile(r"^(text|body|content|prompt|answer|reply|message_text|text_content|ai_text|transcript)$", re.I)
_PHONE_KEY = re.compile(r"(phone|msisdn|wa_id|whatsapp_number)", re.I)
_EMAIL_KEY = re.compile(r"e-?mail", re.I)
_EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_PHONE_RE = re.compile(r"(?<![\w.-])\+?\d[\d\s().-]{7,17}\d(?![\w-])")
_KEYSHAPE_RE = re.compile(r"(Bearer\s+[A-Za-z0-9._~+/=-]{8,}|sk-[A-Za-z0-9_-]{16,}|sk_(?:live|test)_[A-Za-z0-9]{8,}|AIza[0-9A-Za-z_-]{20,}|EAA[A-Za-z0-9]{20,}|tok_[A-Za-z0-9]{8,}|gh[pousr]_[A-Za-z0-9]{20,})")
_ID_KEY = re.compile(r"(^|_)ids?$", re.I)      # tenant_id, event_id, conversation_id …: opaque identifiers, never scrubbed
_MAX_STR = 2000


def mask_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    return f"***{digits[-2:]}" if len(digits) >= 4 else "***"


def mask_email(value: str) -> str:
    return _EMAIL_RE.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", value)


def scrub_text(value: str) -> str:
    value = _KEYSHAPE_RE.sub("[redacted]", value)
    value = _EMAIL_RE.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", value)
    value = _PHONE_RE.sub(lambda m: mask_phone(m.group(0)), value)
    return value if len(value) <= _MAX_STR else value[:_MAX_STR] + "…"


def _clean(key: str, value: Any, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if _SECRET_KEY.search(key):
        return "[redacted]"
    if isinstance(value, str):
        if _ID_KEY.search(key) and len(value) <= 64 and not _SECRET_KEY.search(key):
            return value
        if _BODY_KEY.match(key):
            return f"[redacted:{len(value)} chars]"
        if _PHONE_KEY.search(key):
            return mask_phone(value)
        if _EMAIL_KEY.search(key):
            return mask_email(value)
        return scrub_text(value)
    if depth >= 3:
        return "[nested]"
    if isinstance(value, dict):
        return {str(k): _clean(str(k), v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_clean(key, v, depth + 1) for v in list(value)[:50]]
    return scrub_text(str(value))


def redact_processor(logger: Any, method: str, event_dict: dict) -> dict:
    return {k: (v if k in ("event", "level", "timestamp", "logger") else _clean(k, v)) for k, v in event_dict.items()}


_configured = False


def setup_logging(settings) -> None:
    """Idempotent. Call once per process (gateway lifespan, Kafka worker start, Celery import)."""
    global _configured
    if _configured:
        return
    renderer: Any = structlog.dev.ConsoleRenderer() if settings.is_development else structlog.processors.JSONRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            redact_processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG if settings.is_development else logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
    _configured = True


def setup_process_observability(settings) -> None:
    """Logging redaction + Sentry for a non-gateway process (Kafka workers, Celery). Sentry stays a logged no-op without a DSN."""
    setup_logging(settings)
    from src.shared.observability.tracing import setup_sentry

    setup_sentry(settings)

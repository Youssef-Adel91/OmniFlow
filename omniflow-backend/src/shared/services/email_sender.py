"""Transactional email (Resend HTTP API or SMTP). Disabled — and silently skipped — unless EMAIL_PROVIDER is configured.

Callers must treat email as best-effort: `send()` never raises, returns True only when the provider accepted the message.
Message bodies are never logged.
"""
from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from html import escape

import httpx
import structlog

from src.shared.core.config import get_settings

logger = structlog.get_logger(__name__)
_override = None      # tests inject a fake: async def (to, subject, html, text) -> bool


def set_sender_for_tests(fn) -> None:
    global _override
    _override = fn


def is_configured() -> bool:
    cfg = get_settings()
    p = (cfg.email_provider or "").lower()
    if not cfg.email_from:
        return False
    return (p == "resend" and bool(cfg.resend_api_key)) or (p == "smtp" and bool(cfg.smtp_host))


def render(title: str, body: str, *, link_label: str | None = None, link: str | None = None) -> tuple[str, str]:
    """(html, text) — Arabic, RTL, nothing but escaped plain text and one optional button."""
    button = (f'<p style="margin:24px 0"><a href="{escape(link, quote=True)}" style="background:#C9A84C;color:#0A0F1C;padding:10px 22px;'
              f'border-radius:8px;text-decoration:none;font-weight:bold">{escape(link_label or "فتح")}</a></p>') if link else ""
    html = (f'<!doctype html><html lang="ar" dir="rtl"><body style="font-family:Tahoma,Arial,sans-serif;background:#f6f6f6;padding:24px">'
            f'<div style="max-width:520px;margin:auto;background:#fff;border-radius:12px;padding:28px;color:#111">'
            f'<h2 style="margin:0 0 12px">{escape(title)}</h2><p style="line-height:1.8;white-space:pre-line">{escape(body)}</p>{button}'
            f'<p style="color:#666;font-size:12px;margin-top:28px">OmniFlow — يمكنك تغيير تفضيلات الإشعارات من الإعدادات.</p></div></body></html>')
    text = f"{title}\n\n{body}" + (f"\n\n{link_label or 'الرابط'}: {link}" if link else "")
    return html, text


async def send(to: str, subject: str, html: str, text: str) -> bool:
    if _override is not None:
        return bool(await _override(to, subject, html, text))
    if not is_configured():
        return False
    cfg = get_settings()
    try:
        if cfg.email_provider.lower() == "resend":
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {cfg.resend_api_key}"},
                                 json={"from": cfg.email_from, "to": [to], "subject": subject, "html": html, "text": text})
            ok = r.status_code < 300
        else:
            ok = await asyncio.wait_for(asyncio.to_thread(_smtp_send, to, subject, html, text), timeout=15)
        if not ok:
            logger.warning("email_send_rejected", provider=cfg.email_provider)
        return ok
    except Exception as exc:  # noqa: BLE001 - email is best-effort
        logger.warning("email_send_failed", provider=cfg.email_provider, error=type(exc).__name__)
        return False


def _smtp_send(to: str, subject: str, html: str, text: str) -> bool:
    cfg = get_settings()
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = cfg.email_from, to, subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=10) as s:
        if cfg.smtp_starttls:
            s.starttls()
        if cfg.smtp_user:
            s.login(cfg.smtp_user, cfg.smtp_password)
        s.send_message(msg)
    return True

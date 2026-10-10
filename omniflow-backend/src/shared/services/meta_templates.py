"""Approved WhatsApp message templates, read live from the tenant's WhatsApp Business Account (Graph API).

Broadcasts may only use an APPROVED template (WhatsApp rejects free text outside the 24h window and a number that
keeps trying risks a ban), so the composer lists what Meta says is approved instead of asking for a free-text name.
"""
from __future__ import annotations

import re
from typing import Any

import httpx
import structlog

from src.shared.core.config import get_settings

logger = structlog.get_logger(__name__)
_GRAPH = "https://graph.facebook.com"
_VAR = re.compile(r"\{\{\s*(\d+)\s*\}\}")


class TemplateFetchError(RuntimeError):
    pass


def parse_template(raw: dict[str, Any]) -> dict[str, Any]:
    """Map a Graph `message_templates` item to what the composer needs, flagging layouts we cannot fill yet."""
    body, header_kind, supported, reason = "", None, True, None
    n_vars = 0
    for comp in raw.get("components") or []:
        kind = str(comp.get("type", "")).upper()
        if kind == "BODY":
            body = str(comp.get("text", ""))
            n_vars = max([int(m) for m in _VAR.findall(body)] or [0])
        elif kind == "HEADER":
            header_kind = str(comp.get("format", "TEXT")).upper()
            if header_kind != "TEXT" or _VAR.search(str(comp.get("text", ""))):
                supported, reason = False, "قالب بترويسة وسائط أو بمتغير في الترويسة — غير مدعوم بعد"
        elif kind == "BUTTONS":
            for b in comp.get("buttons") or []:
                if str(b.get("type", "")).upper() == "URL" and _VAR.search(str(b.get("url", ""))):
                    supported, reason = False, "قالب بزر رابط ديناميكي — غير مدعوم بعد"
    return {"name": raw.get("name"), "language": raw.get("language"), "category": raw.get("category"),
            "status": raw.get("status"), "body": body, "variables": n_vars, "supported": supported, "unsupported_reason": reason}


async def list_approved_templates(waba_id: str, access_token: str, *, client: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    cfg = get_settings()
    url = f"{_GRAPH}/{cfg.meta_graph_api_version}/{waba_id}/message_templates"
    params = {"status": "APPROVED", "limit": "100", "fields": "name,language,status,category,components"}
    own = client is None
    client = client or httpx.AsyncClient(timeout=15.0)
    try:
        resp = await client.get(url, params=params, headers={"Authorization": f"Bearer {access_token}"})
    except httpx.HTTPError as exc:
        raise TemplateFetchError(f"تعذّر الاتصال بـ Meta: {type(exc).__name__}") from exc
    finally:
        if own:
            await client.aclose()
    if resp.status_code != 200:
        try:
            msg = resp.json().get("error", {}).get("message", "")
        except Exception:  # noqa: BLE001
            msg = ""
        logger.warning("meta_templates_fetch_failed", status=resp.status_code, error=msg[:200])
        raise TemplateFetchError(f"رفضت Meta الطلب ({resp.status_code}): {msg[:160] or 'تحقق من صلاحيات الرمز'}")
    return [parse_template(t) for t in resp.json().get("data", []) if str(t.get("status", "")).upper() == "APPROVED"]

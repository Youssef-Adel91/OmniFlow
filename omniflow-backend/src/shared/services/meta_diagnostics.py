"""
Read-only diagnostics for a connected Meta Page / Instagram account: is the token valid and when does it expire, which permissions does it
carry, and which webhook fields is the app subscribed to for this Page — compared with what OmniFlow needs.

Used by Settings → Channels ("what is missing") and by scripts/meta_check_subscriptions.py. Nothing here writes to Meta, and nothing
here is exercised against live Meta from this repository (the Graph calls are written from Meta's documentation and tested with mocks).

Why the lists below exist
  * Page webhook fields: Messenger/Instagram DMs (`messages`, `messaging_postbacks`), delivery/read ticks (`message_deliveries`,
    `message_reads`), reactions (`message_reactions`), comments on Page posts (`feed`), @mentions (`mention`).
  * Permissions: the ones the "Connect with Facebook" flow asks for (config.meta_oauth_scopes) — a token that lost one of them (e.g. the
    Page admin unticked it) still looks "connected" but silently fails for that feature. Hiding comments needs pages_manage_engagement.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

from src.shared.core.config import get_settings

logger = structlog.get_logger(__name__)

PAGE_REQUIRED_FIELDS = ("messages", "messaging_postbacks", "message_deliveries", "message_reads", "message_reactions", "feed", "mention")
PAGE_REQUIRED_SCOPES = ("pages_show_list", "pages_messaging", "pages_manage_metadata", "pages_read_engagement", "pages_manage_engagement")
INSTAGRAM_REQUIRED_SCOPES = ("instagram_basic", "instagram_manage_messages", "instagram_manage_comments")
FEATURE_BY_SCOPE = {
    "pages_messaging": "الرد على رسائل ماسنجر",
    "pages_manage_engagement": "الرد على التعليقات وإخفاؤها",
    "pages_read_engagement": "قراءة تعليقات المنشورات",
    "pages_manage_metadata": "تفعيل استقبال الأحداث (webhooks)",
    "pages_show_list": "عرض الصفحات عند الربط",
    "instagram_manage_messages": "الرد على رسائل إنستجرام",
    "instagram_manage_comments": "الرد على تعليقات إنستجرام وإخفاؤها",
    "instagram_basic": "قراءة بيانات حساب إنستجرام",
}
FEATURE_BY_FIELD = {
    "messages": "استقبال الرسائل", "messaging_postbacks": "أزرار الردود السريعة", "message_deliveries": "حالة التسليم",
    "message_reads": "حالة القراءة", "message_reactions": "تفاعلات الرسائل", "feed": "تعليقات المنشورات", "mention": "الإشارات (@mention)",
}


@dataclass
class PageReport:
    reachable: bool = True
    token_valid: bool | None = None            # None = could not be determined (no app credentials / Graph error)
    expires_at: datetime | None = None         # None = never expires (long-lived Page tokens) or unknown
    scopes: list[str] = field(default_factory=list)
    subscribed_fields: list[str] = field(default_factory=list)
    missing_scopes: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    error: str | None = None
    needs_reconnect: bool = False

    @property
    def ok(self) -> bool:
        return self.reachable and self.token_valid is not False and not self.missing_scopes and not self.missing_fields and not self.error

    def issues_ar(self) -> list[str]:
        out: list[str] = []
        if self.needs_reconnect:
            out.append("رمز الوصول لم يعد صالحًا — أعد ربط الصفحة.")
        out += [f"صلاحية ناقصة: {s} ({FEATURE_BY_SCOPE.get(s, s)})." for s in self.missing_scopes]
        out += [f"اشتراك ناقص: {f} ({FEATURE_BY_FIELD.get(f, f)})." for f in self.missing_fields]
        if self.error and not self.needs_reconnect:
            out.append(self.error)
        return out


def _graph() -> str:
    return f"https://graph.facebook.com/{get_settings().meta_graph_api_version}"


async def inspect_page(page_id: str, page_token: str, *, has_instagram: bool, client: httpx.AsyncClient | None = None) -> PageReport:
    """Collect the report with at most three read-only Graph calls. Never raises."""
    cfg = get_settings()
    rep = PageReport()
    own = client is None
    client = client or httpx.AsyncClient(timeout=10.0)
    try:
        # 1) token validity, expiry and scopes (needs the APP token: app_id|app_secret)
        if cfg.meta_app_id and cfg.meta_app_secret:
            r = await client.get(f"{_graph()}/debug_token", params={"input_token": page_token, "access_token": f"{cfg.meta_app_id}|{cfg.meta_app_secret}"})
            if r.status_code == 200:
                d = (r.json() or {}).get("data", {})
                rep.token_valid = bool(d.get("is_valid"))
                exp = d.get("expires_at") or 0
                rep.expires_at = datetime.fromtimestamp(exp, tz=timezone.utc) if exp else None
                rep.scopes = sorted(d.get("scopes") or [])
                if rep.token_valid is False:
                    rep.needs_reconnect = True
            else:
                rep.error = f"تعذّر فحص الرمز (HTTP {r.status_code})."
        # 2) the Page's webhook subscription for this app
        r = await client.get(f"{_graph()}/{page_id}/subscribed_apps", params={"access_token": page_token})
        if r.status_code == 200:
            apps = (r.json() or {}).get("data", [])
            mine = next((a for a in apps if str(a.get("id")) == str(cfg.meta_app_id)), apps[0] if apps and not cfg.meta_app_id else None)
            rep.subscribed_fields = sorted((mine or {}).get("subscribed_fields") or [])
            rep.missing_fields = [f for f in PAGE_REQUIRED_FIELDS if f not in rep.subscribed_fields]
        else:
            _classify_error(rep, r)
    except httpx.HTTPError as exc:
        rep.reachable = False
        rep.error = f"تعذّر الاتصال بـ Meta ({type(exc).__name__})."
    finally:
        if own:
            await client.aclose()
    if rep.scopes:
        needed = list(PAGE_REQUIRED_SCOPES) + (list(INSTAGRAM_REQUIRED_SCOPES) if has_instagram else [])
        rep.missing_scopes = [s for s in needed if s not in rep.scopes]
    return rep


def _classify_error(rep: PageReport, resp: httpx.Response) -> None:
    try:
        err = resp.json().get("error", {})
    except Exception:  # noqa: BLE001
        err = {}
    if err.get("code") in (190, 102) or resp.status_code == 401:
        rep.needs_reconnect, rep.token_valid = True, False
    else:
        rep.error = f"رفضت Meta الطلب ({resp.status_code}): {str(err.get('message', ''))[:160]}"


def report_dict(rep: PageReport) -> dict[str, Any]:
    return {"ok": rep.ok, "reachable": rep.reachable, "token_valid": rep.token_valid, "expires_at": rep.expires_at.isoformat() if rep.expires_at else None,
            "scopes": rep.scopes, "subscribed_fields": rep.subscribed_fields, "missing_scopes": rep.missing_scopes, "missing_fields": rep.missing_fields,
            "needs_reconnect": rep.needs_reconnect, "issues": rep.issues_ar()}

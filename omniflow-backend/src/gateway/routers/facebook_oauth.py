"""
gateway/routers/facebook_oauth.py — "Connect with Facebook" OAuth Flow

Replaces manual credential entry for Facebook Messenger + Instagram with a
real Facebook Login (OAuth 2.0) flow: the tenant clicks "Connect with
Facebook", authorizes once, and this router auto-discovers their Page(s),
the linked Instagram Business Account, exchanges for a long-lived token,
subscribes the app to that Page's webhooks, and stores the result.

WhatsApp is NOT part of this flow (WhatsApp Embedded Signup is a later
phase) -- the existing manual "Developer Mode" form in the onboarding page
remains the only way to connect WhatsApp for now.

Flow:
    1. GET  /start        (Clerk-authed) -> {"authorize_url": "..."}
       Frontend opens authorize_url in a popup window.
    2. User approves on facebook.com; Meta redirects the popup to:
       GET  /callback?code=...&state=...   (no Clerk auth -- this request
       comes straight from Meta, not our frontend's apiClient). `state` is
       validated against a short-lived Redis entry created in step 1 (CSRF
       + ties the callback back to the tenant that started it).
       - Exactly one Page found -> connected immediately, callback page
         posts a "success" message back to the opener window and closes.
       - Multiple Pages found -> callback page posts a "needs_selection"
         message with the page list; the opener shows a picker and calls...
    3. POST /select-page   (Clerk-authed) -> finalizes the chosen Page.

Why a real DB migration wasn't needed: Tenant.instagram_page_id /
instagram_page_access_token already existed (previously only reachable
through the manual TenantOnboardingUpdate schema, which the frontend never
actually exposed a form field for). This flow becomes their first real
writer, storing the Page token Fernet-encrypted (see shared/security/crypto.py)
-- outbound_dispatcher decrypts it on read, falling back to using the raw
value as-is if decryption fails, to stay compatible with any token a
future/manual API caller still sets in plaintext via that same schema field.
"""
from __future__ import annotations

import json
import secrets
import uuid
from typing import Any, Final

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.gateway.dependencies import get_current_user
from src.shared.core.config import get_settings
from src.shared.db.models import Tenant, TenantUser
from src.shared.db.session import AsyncSessionFactory
from src.shared.redis_client.client import redis_mgr
from src.shared.security.crypto import decrypt_secret, encrypt_secret

logger = structlog.get_logger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/v1/integrations/facebook", tags=["Facebook OAuth"])

_GRAPH_BASE: Final[str] = "https://graph.facebook.com"
_API_VERSION: Final[str] = settings.meta_graph_api_version
_TIMEOUT_SECONDS: Final[float] = 20.0
_STATE_TTL_SECONDS: Final[int] = 600       # 10 min to complete the Facebook dialog
_PENDING_SELECTION_TTL_SECONDS: Final[int] = 600  # 10 min to pick a Page afterwards

_STATE_KEY_PREFIX: Final[str] = "fb_oauth_state:"
_PENDING_KEY_PREFIX: Final[str] = "fb_oauth_pending:"


# ══════════════════════════════════════════════════════════════════════════════
# Errors
# ══════════════════════════════════════════════════════════════════════════════

class FacebookOAuthError(Exception):
    """Raised for any Graph API / OAuth failure surfaced to the popup as an error."""


class PageAlreadyConnectedError(FacebookOAuthError):
    """The Page or its Instagram account is already linked to another tenant."""


def _require_configured() -> None:
    if not (settings.meta_app_id and settings.meta_app_secret and settings.meta_oauth_redirect_uri):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Facebook OAuth is not configured on this server "
            "(META_APP_ID / META_APP_SECRET / META_OAUTH_REDIRECT_URI).",
        )


# ══════════════════════════════════════════════════════════════════════════════
# Graph API helpers
# ══════════════════════════════════════════════════════════════════════════════

async def _graph_get(client: httpx.AsyncClient, path: str, params: dict[str, Any]) -> dict[str, Any]:
    resp = await client.get(f"{_GRAPH_BASE}/{_API_VERSION}/{path}", params=params)
    data = resp.json()
    if resp.status_code >= 400:
        err = data.get("error", {}) if isinstance(data, dict) else {}
        raise FacebookOAuthError(err.get("message") or f"Graph API GET {path} failed ({resp.status_code})")
    return data


async def _graph_post(client: httpx.AsyncClient, path: str, params: dict[str, Any]) -> dict[str, Any]:
    resp = await client.post(f"{_GRAPH_BASE}/{_API_VERSION}/{path}", params=params)
    data = resp.json()
    if resp.status_code >= 400:
        err = data.get("error", {}) if isinstance(data, dict) else {}
        raise FacebookOAuthError(err.get("message") or f"Graph API POST {path} failed ({resp.status_code})")
    return data


async def _exchange_code_for_user_token(client: httpx.AsyncClient, code: str) -> str:
    """Authorization code -> short-lived User token."""
    data = await _graph_get(client, "oauth/access_token", {
        "client_id": settings.meta_app_id,
        "client_secret": settings.meta_app_secret,
        "redirect_uri": settings.meta_oauth_redirect_uri,
        "code": code,
    })
    token = data.get("access_token")
    if not token:
        raise FacebookOAuthError("No access_token in Meta's code-exchange response")
    return token


async def _exchange_for_long_lived_token(client: httpx.AsyncClient, short_lived_token: str) -> str:
    """Short-lived (~1-2h) User token -> long-lived (~60 day) User token."""
    data = await _graph_get(client, "oauth/access_token", {
        "grant_type": "fb_exchange_token",
        "client_id": settings.meta_app_id,
        "client_secret": settings.meta_app_secret,
        "fb_exchange_token": short_lived_token,
    })
    token = data.get("access_token")
    if not token:
        raise FacebookOAuthError("No access_token in Meta's token-exchange response")
    return token


async def _list_pages_with_instagram(client: httpx.AsyncClient, long_lived_user_token: str) -> list[dict[str, Any]]:
    """
    GET /me/accounts -> every Page the user administers, each already carrying
    its own Page Access Token (itself long-lived, since it's derived from a
    long-lived User token -- Meta does not expire Page tokens separately as
    long as the granting user remains a Page admin and the User token is valid).

    For each Page, also resolves the linked Instagram Business Account (if
    any) via a second call, and subscribes our app to that Page's webhooks.
    """
    accounts = await _graph_get(client, "me/accounts", {
        "access_token": long_lived_user_token,
        "fields": "id,name,access_token,tasks",
    })
    pages: list[dict[str, Any]] = []
    for page in accounts.get("data", []):
        page_id = page["id"]
        page_token = page["access_token"]

        ig_account_id: str | None = None
        ig_username: str | None = None
        try:
            ig_data = await _graph_get(client, page_id, {
                "fields": "instagram_business_account{id,username}",
                "access_token": page_token,
            })
            ig = ig_data.get("instagram_business_account")
            if ig:
                ig_account_id = ig.get("id")
                ig_username = ig.get("username")
        except FacebookOAuthError as exc:
            # Non-fatal -- a Page with no linked Instagram account is normal,
            # and a transient Graph error here shouldn't block connecting
            # the Page itself for Messenger.
            logger.warning("fb_oauth_ig_lookup_failed", page_id=page_id, error=str(exc)[:200])

        pages.append({
            "page_id": page_id,
            "page_name": page.get("name", ""),
            "page_access_token": page_token,
            "instagram_account_id": ig_account_id,
            "instagram_username": ig_username,
        })
    return pages


async def _subscribe_page_webhooks(client: httpx.AsyncClient, page_id: str, page_token: str) -> None:
    """POST /{page-id}/subscribed_apps -- wires our app to receive this Page's events."""
    await _graph_post(client, f"{page_id}/subscribed_apps", {
        "subscribed_fields": "messages,messaging_postbacks,feed,comments",
        "access_token": page_token,
    })


# ══════════════════════════════════════════════════════════════════════════════
# State / pending-selection helpers (Redis, cache DB)
# ══════════════════════════════════════════════════════════════════════════════

async def _store_state(state: str, tenant_id: uuid.UUID) -> None:
    await redis_mgr.set_raw(
        f"{_STATE_KEY_PREFIX}{state}",
        json.dumps({"tenant_id": str(tenant_id)}),
        ttl=_STATE_TTL_SECONDS,
    )


async def _consume_state(state: str) -> uuid.UUID | None:
    """One-time read: returns the tenant_id the state was issued for, or None."""
    raw = await redis_mgr.get_raw(f"{_STATE_KEY_PREFIX}{state}")
    if raw is None:
        return None
    await redis_mgr.delete_raw(f"{_STATE_KEY_PREFIX}{state}")
    try:
        return uuid.UUID(json.loads(raw)["tenant_id"])
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


async def _store_pending_pages(connection_id: str, tenant_id: uuid.UUID, pages: list[dict[str, Any]]) -> None:
    # Page access tokens are encrypted even in this short-lived holding area --
    # Redis is a shared, longer-lived store, not a request-scoped variable.
    encrypted_pages = [
        {**p, "page_access_token": encrypt_secret(p["page_access_token"])}
        for p in pages
    ]
    await redis_mgr.set_raw(
        f"{_PENDING_KEY_PREFIX}{connection_id}",
        json.dumps({"tenant_id": str(tenant_id), "pages": encrypted_pages}),
        ttl=_PENDING_SELECTION_TTL_SECONDS,
    )


async def _load_pending_pages(connection_id: str) -> dict[str, Any] | None:
    raw = await redis_mgr.get_raw(f"{_PENDING_KEY_PREFIX}{connection_id}")
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def _clear_pending_pages(connection_id: str) -> None:
    await redis_mgr.delete_raw(f"{_PENDING_KEY_PREFIX}{connection_id}")


# ══════════════════════════════════════════════════════════════════════════════
# DB write
# ══════════════════════════════════════════════════════════════════════════════

async def _save_page_connection(
    tenant_id: uuid.UUID,
    page_id: str,
    page_access_token_plaintext: str,
    instagram_account_id: str | None = None,
) -> None:
    """
    Persist the chosen Page for a tenant.

    `instagram_account_id` is stored alongside the Page ID because Meta
    delivers Instagram webhooks with entry.id = the Instagram account ID, not
    the Page ID (see channel_adapters/instagram/router.py's tenant lookup).
    It is overwritten with None when the Page has no linked Instagram account,
    so reconnecting a different Page never leaves a stale account ID behind.
    Both IDs have unique indexes: a Page/account already owned by another
    tenant raises PageAlreadyConnectedError instead of a bare IntegrityError.
    """
    async with AsyncSessionFactory() as session:
        result = await session.execute(select(Tenant).where(Tenant.tenant_id == tenant_id))
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        tenant.instagram_page_id = page_id
        tenant.instagram_account_id = instagram_account_id
        tenant.instagram_page_access_token = encrypt_secret(page_access_token_plaintext)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            logger.warning(
                "fb_oauth_page_already_connected",
                tenant_id=str(tenant_id),
                page_id=page_id,
                instagram_account_id=instagram_account_id,
            )
            raise PageAlreadyConnectedError(
                "هذه الصفحة أو حساب إنستجرام المرتبط بها مربوطة بالفعل بشركة أخرى."
            ) from exc


# ══════════════════════════════════════════════════════════════════════════════
# Popup <-> opener messaging page
# ══════════════════════════════════════════════════════════════════════════════

def _popup_response(payload: dict[str, Any]) -> HTMLResponse:
    """
    A tiny HTML page whose only job is to hand the result back to the window
    that opened the OAuth popup via postMessage, then close itself. Avoids
    needing a hardcoded frontend URL to redirect to (dev's localhost origin
    and the eventual production domain both just work), at the cost of the
    frontend needing a `window.addEventListener("message", ...)` listener
    (see lib/api/facebookOauth.ts).
    """
    body = json.dumps(payload)
    target_origin = json.dumps(str(settings.frontend_url).rstrip("/"))
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Connecting...</title></head>
<body>
<script>
  try {{
    if (window.opener) {{
      window.opener.postMessage({body}, {target_origin});
    }}
  }} finally {{
    window.close();
  }}
</script>
<p>يمكنك إغلاق هذه النافذة.</p>
</body></html>"""
    return HTMLResponse(content=html)


# ══════════════════════════════════════════════════════════════════════════════
# GET /start
# ══════════════════════════════════════════════════════════════════════════════

class StartResponse(BaseModel):
    authorize_url: str


@router.get("/start", response_model=StartResponse)
async def start_facebook_oauth(current_user: TenantUser = Depends(get_current_user)) -> StartResponse:
    """Build the Facebook Login dialog URL for the current tenant."""
    _require_configured()

    state = secrets.token_urlsafe(32)
    await _store_state(state, current_user.tenant_id)

    query = httpx.QueryParams({
        "client_id": settings.meta_app_id,
        "redirect_uri": settings.meta_oauth_redirect_uri,
        "state": state,
        "scope": settings.meta_oauth_scopes,
        "response_type": "code",
    })
    authorize_url = f"https://www.facebook.com/{_API_VERSION}/dialog/oauth?{query}"

    logger.info("fb_oauth_start", tenant_id=str(current_user.tenant_id))
    return StartResponse(authorize_url=authorize_url)


# ══════════════════════════════════════════════════════════════════════════════
# GET /callback
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/callback")
async def facebook_oauth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
) -> HTMLResponse:
    """
    Meta redirects the popup here after the user approves/denies. No Clerk
    auth on this request (it's a top-level browser navigation from
    facebook.com, not our frontend's authenticated apiClient) -- `state`
    is what ties this back to a specific tenant and proves it was really
    initiated by GET /start a moment ago.
    """
    if error:
        logger.info("fb_oauth_denied", error=error, description=error_description)
        return _popup_response({
            "source": "omniflow-fb-oauth", "status": "error",
            "message": error_description or error,
        })

    if not code or not state:
        return _popup_response({
            "source": "omniflow-fb-oauth", "status": "error",
            "message": "Missing code/state from Meta's redirect.",
        })

    tenant_id = await _consume_state(state)
    if tenant_id is None:
        return _popup_response({
            "source": "omniflow-fb-oauth", "status": "error",
            "message": "This connection link expired or was already used. Please try again.",
        })

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            short_lived = await _exchange_code_for_user_token(client, code)
            long_lived = await _exchange_for_long_lived_token(client, short_lived)
            pages = await _list_pages_with_instagram(client, long_lived)

            if not pages:
                return _popup_response({
                    "source": "omniflow-fb-oauth", "status": "error",
                    "message": "لا توجد صفحات فيسبوك مرتبطة بحسابك. أنشئ صفحة على فيسبوك أولاً ثم أعد المحاولة.",
                })

            if len(pages) == 1:
                page = pages[0]
                await _subscribe_page_webhooks(client, page["page_id"], page["page_access_token"])
                await _save_page_connection(
                    tenant_id, page["page_id"], page["page_access_token"], page["instagram_account_id"],
                )
                logger.info(
                    "fb_oauth_connected",
                    tenant_id=str(tenant_id),
                    page_id=page["page_id"],
                    instagram_account_id=page["instagram_account_id"],
                )
                return _popup_response({
                    "source": "omniflow-fb-oauth", "status": "success",
                    "page_name": page["page_name"],
                    "instagram_username": page["instagram_username"],
                })

            # Multiple Pages -- let the user pick, don't guess.
            connection_id = secrets.token_urlsafe(16)
            await _store_pending_pages(connection_id, tenant_id, pages)
            return _popup_response({
                "source": "omniflow-fb-oauth", "status": "needs_selection",
                "connection_id": connection_id,
                "pages": [
                    {"page_id": p["page_id"], "page_name": p["page_name"], "instagram_username": p["instagram_username"]}
                    for p in pages
                ],
            })
    except FacebookOAuthError as exc:
        logger.warning("fb_oauth_graph_error", tenant_id=str(tenant_id), error=str(exc)[:300])
        return _popup_response({
            "source": "omniflow-fb-oauth", "status": "error",
            "message": str(exc)[:300],
        })
    except httpx.HTTPError as exc:
        logger.warning("fb_oauth_http_error", tenant_id=str(tenant_id), error=str(exc)[:300])
        return _popup_response({
            "source": "omniflow-fb-oauth", "status": "error",
            "message": "تعذّر الاتصال بخوادم ميتا. حاول مرة أخرى.",
        })


# ══════════════════════════════════════════════════════════════════════════════
# POST /select-page
# ══════════════════════════════════════════════════════════════════════════════

class SelectPageRequest(BaseModel):
    connection_id: str
    page_id: str


class SelectPageResponse(BaseModel):
    page_id: str
    page_name: str
    instagram_username: str | None = None


@router.post("/select-page", response_model=SelectPageResponse)
async def select_facebook_page(
    payload: SelectPageRequest,
    current_user: TenantUser = Depends(get_current_user),
) -> SelectPageResponse:
    """Finalize the Page choice when GET /callback found more than one."""
    pending = await _load_pending_pages(payload.connection_id)
    if pending is None:
        raise HTTPException(status_code=410, detail="Connection expired or already used. Please reconnect.")
    if pending["tenant_id"] != str(current_user.tenant_id):
        raise HTTPException(status_code=403, detail="This connection belongs to a different tenant.")

    chosen = next((p for p in pending["pages"] if p["page_id"] == payload.page_id), None)
    if chosen is None:
        raise HTTPException(status_code=404, detail="That page ID wasn't in the original list.")

    page_token = decrypt_secret(chosen["page_access_token"])
    if page_token is None:
        raise HTTPException(status_code=500, detail="Could not recover the page token. Please reconnect.")

    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        try:
            await _subscribe_page_webhooks(client, chosen["page_id"], page_token)
        except FacebookOAuthError as exc:
            raise HTTPException(status_code=502, detail=f"Meta rejected the webhook subscription: {exc}") from exc

    try:
        await _save_page_connection(
            current_user.tenant_id, chosen["page_id"], page_token, chosen.get("instagram_account_id"),
        )
    except PageAlreadyConnectedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await _clear_pending_pages(payload.connection_id)

    logger.info(
        "fb_oauth_page_selected",
        tenant_id=str(current_user.tenant_id),
        page_id=chosen["page_id"],
        instagram_account_id=chosen.get("instagram_account_id"),
    )
    return SelectPageResponse(
        page_id=chosen["page_id"],
        page_name=chosen["page_name"],
        instagram_username=chosen.get("instagram_username"),
    )

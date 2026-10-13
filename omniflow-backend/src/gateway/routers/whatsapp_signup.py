"""
gateway/routers/whatsapp_signup.py — WhatsApp Embedded Signup (backend half). OFF by default (`FEATURE_WHATSAPP_EMBEDDED_SIGNUP=false`).

Flow: the dashboard loads the Facebook JS SDK, opens the Embedded Signup popup with `config_id`, and receives (a) a short-lived `code` and
(b) a `WA_EMBEDDED_SIGNUP` message event carrying `waba_id` + `phone_number_id`. It posts those three values here. We then
  1. exchange the code for a business token (server-side, with the app secret),
  2. check the phone number really belongs to that WABA,
  3. register the number with Cloud API and subscribe our app to the WABA's webhooks,
  4. store the token ENCRYPTED (Fernet) together with the ids.
While the flag is off every endpoint answers 404, so the feature is invisible. NOT exercised against live Meta (needs a Tech-Provider-approved app).
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any, Final

import httpx
import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.gateway.dependencies import AuthTenantSession, CurrentUser
from src.shared.core.config import get_settings
from src.shared.core.enums import TenantUserRole
from src.shared.db.models import Tenant
from src.shared.security.crypto import encrypt_secret

logger = structlog.get_logger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/v1/integrations/whatsapp/embedded-signup", tags=["WhatsApp Embedded Signup"])
_GRAPH: Final[str] = "https://graph.facebook.com"


class SignupError(Exception):
    pass


def _off() -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "الميزة غير مفعّلة."})


def registration_pin(phone_number_id: str) -> str:
    """Deterministic 6-digit two-step-verification PIN (HMAC of the number id), so a later re-registration needs no stored secret."""
    digest = hmac.new(settings.meta_app_secret.encode(), f"wa-pin:{phone_number_id}".encode(), hashlib.sha256).digest()
    return f"{int.from_bytes(digest[:4], 'big') % 1_000_000:06d}"


async def _get(client: httpx.AsyncClient, path: str, params: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    r = await client.get(f"{_GRAPH}/{settings.meta_graph_api_version}/{path}", params=params, headers={"Authorization": f"Bearer {token}"} if token else None)
    data = r.json()
    if r.status_code >= 400:
        raise SignupError(((data.get("error") or {}).get("message") if isinstance(data, dict) else None) or f"Graph GET {path} failed ({r.status_code})")
    return data


async def _post(client: httpx.AsyncClient, path: str, body: dict[str, Any], token: str) -> dict[str, Any]:
    r = await client.post(f"{_GRAPH}/{settings.meta_graph_api_version}/{path}", json=body, headers={"Authorization": f"Bearer {token}"})
    data = r.json()
    if r.status_code >= 400:
        raise SignupError(((data.get("error") or {}).get("message") if isinstance(data, dict) else None) or f"Graph POST {path} failed ({r.status_code})")
    return data


async def provision(client: httpx.AsyncClient, *, code: str, waba_id: str, phone_number_id: str) -> dict[str, str]:
    """Steps 1–3 against Meta. Returns {token, display_phone_number}. Raises SignupError with a Meta message on any failure."""
    tok = await _get(client, "oauth/access_token", {"client_id": settings.meta_app_id, "client_secret": settings.meta_app_secret, "code": code})
    token = tok.get("access_token")
    if not token:
        raise SignupError("Meta did not return an access token.")
    numbers = await _get(client, f"{waba_id}/phone_numbers", {"fields": "id,display_phone_number"}, token)
    match = next((n for n in numbers.get("data", []) if str(n.get("id")) == phone_number_id), None)
    if not match:
        raise SignupError("The phone number does not belong to this WhatsApp Business Account.")
    await _post(client, f"{phone_number_id}/register", {"messaging_product": "whatsapp", "pin": registration_pin(phone_number_id)}, token)
    await _post(client, f"{waba_id}/subscribed_apps", {}, token)
    return {"token": token, "display_phone_number": str(match.get("display_phone_number") or "")}


class SignupConfig(BaseModel):
    enabled: bool
    app_id: str | None = None
    config_id: str | None = None
    graph_version: str | None = None


class SignupComplete(BaseModel):
    code: str = Field(..., min_length=4, max_length=2048)
    waba_id: str = Field(..., pattern=r"^\d{5,30}$")
    phone_number_id: str = Field(..., pattern=r"^\d{5,30}$")


@router.get("/config", response_model=SignupConfig, summary="Public SDK settings for the Embedded Signup popup (404 while the feature is off)")
async def signup_config(user: CurrentUser) -> SignupConfig:
    if not settings.feature_whatsapp_embedded_signup:
        raise _off()
    if not (settings.meta_app_id and settings.meta_es_config_id and settings.meta_app_secret):
        return SignupConfig(enabled=False)
    return SignupConfig(enabled=True, app_id=settings.meta_app_id, config_id=settings.meta_es_config_id, graph_version=settings.meta_graph_api_version)


@router.post("/complete", summary="Finish Embedded Signup: exchange the code, register the number, subscribe webhooks, store the token encrypted (admin)")
async def signup_complete(body: SignupComplete, user: CurrentUser, session: AuthTenantSession) -> dict[str, Any]:
    if not settings.feature_whatsapp_embedded_signup:
        raise _off()
    if user.role != TenantUserRole.ADMIN:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "هذا الإجراء للمدير فقط."})
    if not (settings.meta_app_id and settings.meta_app_secret):
        raise HTTPException(status_code=503, detail={"code": "NOT_CONFIGURED", "message": "تطبيق Meta غير مضبوط على الخادم."})
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            result = await provision(client, code=body.code, waba_id=body.waba_id, phone_number_id=body.phone_number_id)
    except (SignupError, httpx.HTTPError) as exc:
        logger.warning("whatsapp_signup_failed", tenant_id=str(user.tenant_id), error=str(exc)[:200])
        raise HTTPException(status_code=502, detail={"code": "META_REJECTED", "message": f"تعذّر إكمال الربط مع Meta: {str(exc)[:160]}"}) from exc

    tenant = await session.scalar(select(Tenant).where(Tenant.tenant_id == user.tenant_id))
    if not tenant:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "المنشأة غير موجودة."})
    taken = await session.scalar(select(Tenant.tenant_id).where(Tenant.whatsapp_phone_number_id == body.phone_number_id, Tenant.tenant_id != user.tenant_id))
    if taken:   # tenants is not under RLS, so this sees other workspaces; two tenants on one number would make webhook routing ambiguous
        raise HTTPException(status_code=409, detail={"code": "NUMBER_ALREADY_CONNECTED", "message": "هذا الرقم مرتبط بمنشأة أخرى."})
    tenant.meta_access_token = encrypt_secret(result["token"])
    tenant.whatsapp_phone_number_id = body.phone_number_id
    tenant.whatsapp_waba_id = body.waba_id
    if result["display_phone_number"]:
        tenant.whatsapp_display_phone_number = result["display_phone_number"]
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail={"code": "NUMBER_ALREADY_CONNECTED", "message": "هذا الرقم مرتبط بمنشأة أخرى."}) from exc
    from src.shared.services import channel_health

    await channel_health.clear(user.tenant_id, "whatsapp")
    logger.info("whatsapp_signup_completed", tenant_id=str(user.tenant_id), waba_id=body.waba_id)
    return {"connected": True, "phone_number_id": body.phone_number_id, "waba_id": body.waba_id, "display_phone_number": result["display_phone_number"] or None}

"""gateway/routers/meta_data_deletion.py — Meta "Data Deletion Request Callback" + the public status lookup + the public pricing catalogue.

    POST /api/v1/meta/data-deletion                 form field `signed_request` (Meta) -> {"url": <status page>, "confirmation_code": <code>}
    GET  /api/v1/meta/data-deletion/status/{code}   public: {"status", "requested_at", "completed_at"}
    GET  /api/v1/public/catalog                     public: plans, top-up packs, add-ons (no secrets)

`signed_request` = base64url(HMAC-SHA256 signature) "." base64url(JSON payload), signed with the Meta app secret. We verify the signature in
constant time, reject any algorithm other than HMAC-SHA256, and then remove everything we hold for that Facebook user: Page / Instagram
connection tokens and ids on every workspace they connected (audited). We keep only a hash of the user id.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from typing import Annotated

import structlog
from fastapi import APIRouter, Form, HTTPException, Path
from sqlalchemy import select, update

from src.shared.core.config import get_settings
from src.shared.db.models import AuditLog, DataDeletionRequest, Plan, PlanAddon, Tenant, TopupPack
from src.shared.db.session import get_system_session

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["Meta data deletion"])
catalog_router = APIRouter(prefix="/api/v1/public", tags=["Public"])

MAX_SIGNED_REQUEST = 8192


class InvalidSignedRequest(ValueError):
    pass


def _b64url(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def parse_signed_request(signed_request: str, app_secret: str) -> dict:
    """Verify and decode Meta's signed_request. Raises InvalidSignedRequest on anything unexpected."""
    if not app_secret:
        raise InvalidSignedRequest("app secret is not configured")
    if not signed_request or len(signed_request) > MAX_SIGNED_REQUEST or signed_request.count(".") != 1:
        raise InvalidSignedRequest("malformed")
    sig_part, payload_part = signed_request.split(".")
    try:
        sig, payload_raw = _b64url(sig_part), _b64url(payload_part)
        payload = json.loads(payload_raw)
    except (ValueError, TypeError) as exc:
        raise InvalidSignedRequest("undecodable") from exc
    if not isinstance(payload, dict) or str(payload.get("algorithm", "")).upper() != "HMAC-SHA256":
        raise InvalidSignedRequest("unsupported algorithm")
    expected = hmac.new(app_secret.encode(), payload_part.encode(), hashlib.sha256).digest()          # Meta signs the ENCODED payload
    if not hmac.compare_digest(sig, expected):
        raise InvalidSignedRequest("bad signature")
    if not str(payload.get("user_id") or "").strip():
        raise InvalidSignedRequest("no user_id")
    return payload


def status_url(code: str) -> str:
    base = (get_settings().public_base_url or "").rstrip("/")
    return f"{base}/ar/data-deletion/status?code={code}" if base else f"/ar/data-deletion/status?code={code}"


async def process_user(session, fb_user_id: str, now: datetime | None = None) -> int:
    """Remove the Meta connection data we hold for this Facebook user. Returns the number of workspaces affected."""
    now = now or datetime.now(timezone.utc)
    tenants = (await session.execute(select(Tenant).where(Tenant.facebook_user_id == fb_user_id).with_for_update())).scalars().all()
    for t in tenants:
        await session.execute(update(Tenant).where(Tenant.tenant_id == t.tenant_id).values(
            instagram_page_id=None, instagram_page_access_token=None, instagram_account_id=None, facebook_user_id=None))
        session.add(AuditLog(tenant_id=t.tenant_id, action="privacy.meta_data_deletion", entity_type="tenant", entity_id=str(t.tenant_id),
                             details={"removed": ["instagram_page_id", "instagram_page_access_token", "instagram_account_id", "facebook_user_id"], "at": now.isoformat()}))
    await session.flush()
    return len(tenants)


@router.post("/api/v1/meta/data-deletion", summary="Meta Data Deletion Request Callback")
async def data_deletion_callback(signed_request: Annotated[str, Form()]) -> dict:
    try:
        payload = parse_signed_request(signed_request, get_settings().meta_app_secret)
    except InvalidSignedRequest as exc:
        logger.warning("meta_data_deletion_rejected", reason=str(exc))
        raise HTTPException(status_code=400, detail={"code": "INVALID_SIGNED_REQUEST", "message": "Invalid signed_request."}) from exc
    fb_user_id = str(payload["user_id"])
    code = secrets.token_urlsafe(18).replace("-", "A").replace("_", "B")[:24]
    async with get_system_session() as session:
        affected = await process_user(session, fb_user_id)
        session.add(DataDeletionRequest(confirmation_code=code, fb_user_hash=hashlib.sha256(fb_user_id.encode()).hexdigest(),
                                        status="completed" if affected else "no_data", tenants_affected=affected, completed_at=datetime.now(timezone.utc)))
    logger.info("meta_data_deletion_processed", affected=affected)
    return {"url": status_url(code), "confirmation_code": code}


@router.get("/api/v1/meta/data-deletion/status/{code}", summary="Public status of a data-deletion request")
async def data_deletion_status(code: Annotated[str, Path(min_length=8, max_length=32, pattern=r"^[A-Za-z0-9]+$")]) -> dict:
    async with get_system_session() as session:
        req = await session.scalar(select(DataDeletionRequest).where(DataDeletionRequest.confirmation_code == code))
    if req is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Unknown confirmation code."})
    return {"status": req.status, "requested_at": req.created_at.isoformat(), "completed_at": req.completed_at.isoformat() if req.completed_at else None}


@catalog_router.get("/catalog", summary="Plans, top-up packs and add-ons (public, read-only)")
async def public_catalog() -> dict:
    cfg = get_settings()
    async with get_system_session() as session:
        plans = (await session.execute(select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order))).scalars().all()
        packs = (await session.execute(select(TopupPack).where(TopupPack.is_active.is_(True)).order_by(TopupPack.sort_order))).scalars().all()
        addons = (await session.execute(select(PlanAddon).where(PlanAddon.is_active.is_(True)))).scalars().all()
    return {
        "currency": "SAR", "vat_rate_bp": cfg.billing_vat_rate_bp, "trial_days": cfg.billing_trial_days,
        "plans": [{"code": p.code, "name_ar": p.name_ar, "description_ar": p.description_ar, "contact_sales": p.contact_sales, "limits": p.limits,
                   "monthly_halalas": p.price_monthly_halalas, "yearly_halalas": p.price_yearly_halalas} for p in plans],
        "topup_packs": [{"code": k.code, "replies": k.replies, "price_halalas": k.price_halalas, "validity_months": k.validity_months} for k in packs],
        "addons": [{"code": a.code, "name_ar": a.name_ar, "monthly_halalas": a.price_monthly_halalas, "available": a.is_available} for a in addons],
    }

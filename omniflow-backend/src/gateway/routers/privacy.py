"""
gateway/routers/privacy.py — PDPL data-subject tools (prefix /api/v1/privacy). Admin (auditor may read the summary / consent log).

    GET  /summary                         counts + the retention statement shown in Settings
    GET  /customers/{id}/consents         consent history (from the audit log)
    GET  /customers/{id}/export           the customer's data as a JSON download (audited)
    POST /customers/{id}/anonymize        { "confirm": "<customer id>" } irreversible PII wipe (audited)
"""
from __future__ import annotations

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Response
from pydantic import BaseModel

from src.gateway.dependencies import AuthTenantSession, CurrentUser
from src.shared.core.enums import TenantUserRole
from src.shared.services import privacy as svc

router = APIRouter(prefix="/api/v1/privacy", tags=["Privacy"])

_NOTE_TAIL = "المحو (إخفاء الهوية) نهائي ولا يمكن التراجع عنه، ويحذف ملفات العميل من التخزين، ويُبقي السجلات المحاسبية وسجل التدقيق."


def retention_note() -> str:
    from src.shared.core.config import get_settings

    cfg = get_settings()
    if cfg.retention_enabled and not cfg.retention_dry_run and cfg.retention_inactive_customer_days > 0:
        return (f"يُخفى تعريف العملاء الذين لم يتفاعلوا لأكثر من {cfg.retention_inactive_customer_days} يومًا تلقائيًا، وتصلك رسالة تنبيه قبل ذلك بـ {cfg.retention_warning_days} يومًا. " + _NOTE_TAIL)
    return "لا يوجد حذف تلقائي مفعّل حاليًا: تُحفظ بيانات العملاء والمحادثات إلى أن تطلب المنشأة المحو أو يطلبه العميل. " + _NOTE_TAIL


def _admin(user) -> None:
    if user.role != TenantUserRole.ADMIN:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "هذا الإجراء متاح لمدير الحساب فقط."})


def _reader(user) -> None:
    if user.role not in (TenantUserRole.ADMIN, TenantUserRole.AUDITOR):
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "متاح للمدير والمدقّق فقط."})


def _err(e: svc.PrivacyError) -> HTTPException:
    return HTTPException(status_code=e.http, detail={"code": e.code, "message": e.message})


@router.get("/summary")
async def summary(user: CurrentUser, session: AuthTenantSession) -> dict:
    _reader(user)
    return {**await svc.summary(session, user.tenant_id), "retention_note": retention_note()}


@router.get("/customers/{customer_id}/consents")
async def consents(user: CurrentUser, session: AuthTenantSession, customer_id: Annotated[uuid.UUID, Path()]) -> dict:
    _reader(user)
    try:
        await svc._customer(session, user.tenant_id, customer_id)
    except svc.PrivacyError as e:
        raise _err(e) from e
    return {"items": await svc.consent_history(session, user.tenant_id, customer_id)}


@router.get("/customers/{customer_id}/export")
async def export(user: CurrentUser, session: AuthTenantSession, customer_id: Annotated[uuid.UUID, Path()]) -> Response:
    _admin(user)
    try:
        data = await svc.export_customer(session, user.tenant_id, customer_id, user.user_id)
    except svc.PrivacyError as e:
        raise _err(e) from e
    return Response(content=json.dumps(data, ensure_ascii=False, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="customer-{customer_id}.json"', "Cache-Control": "private, no-store"})


class AnonymizeBody(BaseModel):
    confirm: str
    reason: str = ""


@router.post("/customers/{customer_id}/anonymize")
async def anonymize(user: CurrentUser, session: AuthTenantSession, customer_id: Annotated[uuid.UUID, Path()], body: AnonymizeBody) -> dict:
    _admin(user)
    if body.confirm.strip() != str(customer_id):
        raise HTTPException(status_code=422, detail={"code": "CONFIRMATION_MISMATCH", "message": "تأكيد غير مطابق. المحو نهائي — أدخل معرّف العميل للتأكيد."})
    try:
        return await svc.anonymize_customer(session, user.tenant_id, customer_id, user.user_id, body.reason)
    except svc.PrivacyError as e:
        raise _err(e) from e

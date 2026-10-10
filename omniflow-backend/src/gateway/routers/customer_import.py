"""
gateway/routers/customer_import.py — bulk customer import (same flow as the property import)

    POST   /api/v1/customers/import/upload          multipart file -> {import_id, detected_columns, suggested_mapping, ...}
    GET    /api/v1/customers/import/template        Excel template (Arabic headers + example)
    GET    /api/v1/customers/import/templates       saved column-mapping templates
    POST   /api/v1/customers/import/templates
    DELETE /api/v1/customers/import/templates/{id}
    POST   /api/v1/customers/import/{id}/validate   dry run (nothing is written)
    POST   /api/v1/customers/import/{id}/commit     queue the background import
    GET    /api/v1/customers/import/{id}            status + progress counters
    POST   /api/v1/customers/import/{id}/cancel
    GET    /api/v1/customers/import/{id}/errors.csv

Authorisation: admin and agent (CustomerWriteUser). Marketing consent is only recorded when the importer
ticks the explicit declaration (`options.consent_confirmed`); the declaration is audit-logged.
Registered BEFORE the customers router so `/import/...` is never read as a customer id.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Response, UploadFile, status

from src.gateway.dependencies import AuthTenantSession, CustomerWriteUser
from src.gateway.routers.property_import import TemplateBody, ValidateBody, _bad_request, _read_capped, _XLSX
from src.shared.core.config import get_settings
from src.shared.services import customer_import as svc
from src.shared.services import property_import as base
from src.shared.services.bulk_import.customer_schema import CUSTOMER_SCHEMA
from src.shared.services.bulk_import.files import ImportFileError

router = APIRouter(prefix="/api/v1/customers/import", tags=["Customer Import"])


@router.post("/upload", status_code=status.HTTP_201_CREATED, summary="Upload a file and get a suggested column mapping")
async def upload(user: CustomerWriteUser, session: AuthTenantSession, file: UploadFile = File(...)) -> dict[str, Any]:
    data = await _read_capped(file, get_settings().import_max_file_mb * 1024 * 1024)
    try:
        return await svc.create_upload(session, tenant_id=user.tenant_id, user_id=getattr(user, "user_id", None),
                                       filename=file.filename or "upload", data=data)
    except ImportFileError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": exc.message}) from exc


@router.get("/template", summary="Excel template with Arabic headers")
async def download_template(user: CustomerWriteUser) -> Response:
    return Response(svc.build_excel_template(), media_type=_XLSX,
                    headers={"Content-Disposition": 'attachment; filename="omniflow-customers-template.xlsx"'})


@router.get("/templates", summary="Saved mapping templates")
async def templates(user: CustomerWriteUser, session: AuthTenantSession) -> dict[str, Any]:
    from sqlalchemy import select

    from src.shared.db.models import ImportMappingTemplate as T

    rows = (await session.execute(select(T).where(T.kind == svc.KIND).order_by(T.name))).scalars().all()
    return {"items": [{"template_id": str(t.template_id), "name": t.name, "mapping": t.mapping} for t in rows]}


@router.post("/templates", status_code=status.HTTP_201_CREATED, summary="Save a mapping template")
async def create_template(body: TemplateBody, user: CustomerWriteUser, session: AuthTenantSession) -> dict[str, Any]:
    from sqlalchemy import select

    from src.shared.db.models import ImportMappingTemplate as T

    name = body.name.strip()[:100]
    clean = {k: v for k, v in body.mapping.items() if v and k in CUSTOMER_SCHEMA.by_name()}
    if not name:
        raise _bad_request(ValueError("اسم القالب مطلوب"))
    tpl = await session.scalar(select(T).where(T.kind == svc.KIND, T.name == name))
    if tpl:
        tpl.mapping = clean
    else:
        tpl = T(tenant_id=user.tenant_id, kind=svc.KIND, name=name, mapping=clean)
        session.add(tpl)
    await session.flush()
    return {"template_id": str(tpl.template_id), "name": tpl.name, "mapping": tpl.mapping}


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a mapping template")
async def remove_template(template_id: uuid.UUID, user: CustomerWriteUser, session: AuthTenantSession) -> Response:
    from sqlalchemy import select

    from src.shared.core.exceptions import NotFoundError
    from src.shared.db.models import ImportMappingTemplate as T

    tpl = await session.scalar(select(T).where(T.template_id == template_id, T.kind == svc.KIND))
    if tpl is None:
        raise NotFoundError("القالب غير موجود")
    await session.delete(tpl)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{import_id}/validate", summary="Dry run: validate every row without writing")
async def validate(import_id: uuid.UUID, body: ValidateBody, user: CustomerWriteUser, session: AuthTenantSession) -> dict[str, Any]:
    job = await base._load_job(session, import_id, (svc.KIND,))
    try:
        summary = await svc.validate_job(session, job, body.mapping, body.options)
        if body.save_template_as:
            await templates_save(session, user.tenant_id, body.save_template_as, job.mapping or {})
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return {"import_id": str(import_id), "status": job.status, **summary, "options": job.options, "mapping": job.mapping}


async def templates_save(session, tenant_id: uuid.UUID, name: str, mapping: dict[str, str]) -> None:
    from sqlalchemy import select

    from src.shared.db.models import ImportMappingTemplate as T

    name = name.strip()[:100]
    if not name:
        return
    clean = {k: v for k, v in mapping.items() if v and k in CUSTOMER_SCHEMA.by_name()}
    tpl = await session.scalar(select(T).where(T.kind == svc.KIND, T.name == name))
    if tpl:
        tpl.mapping = clean
    else:
        session.add(T(tenant_id=tenant_id, kind=svc.KIND, name=name, mapping=clean))
    await session.flush()


@router.post("/{import_id}/commit", status_code=status.HTTP_202_ACCEPTED, summary="Start the background import")
async def commit(import_id: uuid.UUID, background: BackgroundTasks, user: CustomerWriteUser, session: AuthTenantSession) -> dict[str, Any]:
    job = await base._load_job(session, import_id, (svc.KIND,))
    consent = bool((job.options or {}).get("consent_confirmed"))
    try:
        await base.queue_job(session, job)
    except base.ConflictError as exc:
        raise HTTPException(status_code=409, detail={"code": "IMPORT_IN_PROGRESS", "message": str(exc)}) from exc
    except ValueError as exc:
        raise _bad_request(exc) from exc
    if consent:
        counts = (job.summary or {}).get("counts", {})
        await base.write_audit(
            session, tenant_id=user.tenant_id, actor_user_id=getattr(user, "user_id", None),
            action="customer_import.consent_confirmed", entity_type="import_job", entity_id=str(import_id),
            details={"declaration": "importer holds the customers' consent to contact them", "filename": job.filename,
                     "expected_opt_ins": counts.get("will_opt_in", 0), "rows": counts.get("total", 0)})
    background.add_task(base.spawn_by_kind, user.tenant_id, import_id, svc.KIND)
    return base.serialize_job(job)


@router.get("/{import_id}", summary="Import status / progress")
async def progress(import_id: uuid.UUID, user: CustomerWriteUser, session: AuthTenantSession) -> dict[str, Any]:
    return base.serialize_job(await base._load_job(session, import_id, (svc.KIND,)))


@router.post("/{import_id}/cancel", summary="Cancel an import (a running one stops before its next batch)")
async def cancel(import_id: uuid.UUID, user: CustomerWriteUser, session: AuthTenantSession) -> dict[str, Any]:
    job = await base._load_job(session, import_id, (svc.KIND,))
    try:
        await base.cancel_job(session, job)
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return base.serialize_job(job)


@router.get("/{import_id}/errors.csv", summary="Failed / skipped rows with the reason (CSV)")
async def errors_csv(import_id: uuid.UUID, user: CustomerWriteUser, session: AuthTenantSession) -> Response:
    job = await base._load_job(session, import_id, (svc.KIND,))
    return Response(await base.error_report(job), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="customers-import-{import_id}-errors.csv"'})

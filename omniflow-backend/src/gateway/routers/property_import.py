"""
gateway/routers/property_import.py — bulk property import

    POST   /api/v1/properties/import/upload          multipart file -> {import_id, detected_columns, suggested_mapping, ...}
    GET    /api/v1/properties/import/template        downloadable Excel template (Arabic headers + example)
    GET    /api/v1/properties/import/templates       saved column-mapping templates
    POST   /api/v1/properties/import/templates       save/update a mapping template
    DELETE /api/v1/properties/import/templates/{id}
    POST   /api/v1/properties/import/{id}/validate   dry run: counts, 50-row preview, issues (nothing is written)
    POST   /api/v1/properties/import/{id}/commit     queue the background import
    GET    /api/v1/properties/import/{id}            status + progress counters (poll)
    POST   /api/v1/properties/import/{id}/cancel
    GET    /api/v1/properties/import/{id}/errors.csv failed/skipped rows with the reason

Same authorisation as creating a property (PropertyWriteUser); the tenant comes from the verified
JWT and every query runs in the tenant's RLS session. See shared/services/property_import.py.
"""
from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field

from src.gateway.dependencies import AuthTenantSession, PropertyWriteUser
from src.shared.core.config import get_settings
from src.shared.services import property_import as svc
from src.shared.services.bulk_import.files import ImportFileError

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/properties/import", tags=["Property Import"])

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ValidateBody(BaseModel):
    mapping: dict[str, str | None] = Field(..., description="canonical field -> file column header")
    options: dict[str, Any] | None = Field(
        default=None, description='{"on_duplicate": "skip|update|create_new", "defaults": {"property_type":..., "status":..., "city":..., "district":...}}')
    save_template_as: str | None = Field(default=None, max_length=100, description="also save this mapping as a template")


class TemplateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    mapping: dict[str, str | None]


def _bad_request(exc: Exception, code: str = "INVALID_IMPORT") -> HTTPException:
    return HTTPException(status_code=422, detail={"code": code, "message": str(exc)})


async def _read_capped(file: UploadFile, max_bytes: int) -> bytes:
    chunks, size = [], 0
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size > max_bytes:
            raise HTTPException(status_code=413, detail={
                "code": "file_too_large", "message": f"حجم الملف يتجاوز الحد المسموح ({max_bytes // (1024 * 1024)} ميجابايت)."})
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/upload", status_code=status.HTTP_201_CREATED, summary="Upload a file and get a suggested column mapping")
async def upload(user: PropertyWriteUser, session: AuthTenantSession, file: UploadFile = File(...)) -> dict[str, Any]:
    cfg = get_settings()
    data = await _read_capped(file, cfg.import_max_file_mb * 1024 * 1024)
    try:
        return await svc.create_upload(session, tenant_id=user.tenant_id, user_id=getattr(user, "user_id", None),
                                       filename=file.filename or "upload", data=data)
    except ImportFileError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": exc.message}) from exc


@router.get("/template", summary="Excel template with Arabic headers")
async def download_template(user: PropertyWriteUser) -> Response:
    return Response(svc.build_excel_template(), media_type=_XLSX,
                    headers={"Content-Disposition": 'attachment; filename="omniflow-properties-template.xlsx"'})


@router.get("/templates", summary="Saved mapping templates")
async def templates(user: PropertyWriteUser, session: AuthTenantSession) -> dict[str, Any]:
    return {"items": await svc.list_templates(session)}


@router.post("/templates", status_code=status.HTTP_201_CREATED, summary="Save a mapping template")
async def create_template(body: TemplateBody, user: PropertyWriteUser, session: AuthTenantSession) -> dict[str, Any]:
    try:
        return await svc.save_template(session, user.tenant_id, body.name, {k: v for k, v in body.mapping.items() if v})
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a mapping template")
async def remove_template(template_id: uuid.UUID, user: PropertyWriteUser, session: AuthTenantSession) -> Response:
    await svc.delete_template(session, template_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{import_id}/validate", summary="Dry run: validate every row without writing")
async def validate(import_id: uuid.UUID, body: ValidateBody, user: PropertyWriteUser, session: AuthTenantSession) -> dict[str, Any]:
    job = await svc._load_job(session, import_id)
    try:
        summary = await svc.validate_job(session, job, body.mapping, body.options)
        if body.save_template_as:
            await svc.save_template(session, user.tenant_id, body.save_template_as, job.mapping or {})
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return {"import_id": str(import_id), "status": job.status, **summary, "options": job.options, "mapping": job.mapping}


@router.post("/{import_id}/commit", status_code=status.HTTP_202_ACCEPTED, summary="Start the background import")
async def commit(import_id: uuid.UUID, background: BackgroundTasks, user: PropertyWriteUser, session: AuthTenantSession) -> dict[str, Any]:
    job = await svc._load_job(session, import_id)
    try:
        await svc.queue_job(session, job)
    except svc.ConflictError as exc:
        raise HTTPException(status_code=409, detail={"code": "IMPORT_IN_PROGRESS", "message": str(exc)}) from exc
    except ValueError as exc:
        raise _bad_request(exc) from exc
    # Runs after the response (and after the session dependency has committed the `queued` row).
    # run_job also retries its claim briefly, so a slow commit can never strand the job.
    background.add_task(svc.start_job, user.tenant_id, import_id)
    return svc.serialize_job(job)


@router.get("/{import_id}", summary="Import status / progress")
async def progress(import_id: uuid.UUID, user: PropertyWriteUser, session: AuthTenantSession) -> dict[str, Any]:
    return svc.serialize_job(await svc._load_job(session, import_id))


@router.post("/{import_id}/cancel", summary="Cancel an import (a running one stops before its next batch)")
async def cancel(import_id: uuid.UUID, user: PropertyWriteUser, session: AuthTenantSession) -> dict[str, Any]:
    job = await svc._load_job(session, import_id)
    try:
        await svc.cancel_job(session, job)
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return svc.serialize_job(job)


@router.get("/{import_id}/errors.csv", summary="Failed / skipped rows with the reason (CSV)")
async def errors_csv(import_id: uuid.UUID, user: PropertyWriteUser, session: AuthTenantSession) -> Response:
    job = await svc._load_job(session, import_id)
    body = await svc.error_report(job)
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="import-{import_id}-errors.csv"'})

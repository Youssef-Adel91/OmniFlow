"""gateway/routers/einvoice.py — ZATCA e-invoicing operations (prefix /api/v1/einvoice). Off (404) unless EINVOICE_ENABLED.

Staff (SUPPORT_STAFF_EMAILS): unit status, CSR / CSID onboarding steps, document list, manual resubmission.
Tenant admins/auditors: download the signed XML or the PDF/A-3 of their own invoices.
Nothing here ever sends to ZATCA production unless both production switches are on (see src/einvoice/client.py).
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from src.einvoice import pdf as einvoice_pdf
from src.einvoice import service as ez
from src.einvoice.client import EInvoiceError
from src.gateway.dependencies import AuthTenantSession, CurrentUser
from src.gateway.routers.support import StaffUser
from src.shared.core.config import get_settings
from src.shared.core.enums import TenantUserRole
from src.shared.db.models import BillingInvoice, CreditNote, EInvoiceDocument, EInvoiceUnit
from src.shared.db.session import get_system_session


async def _enabled() -> None:
    if not get_settings().einvoice_enabled:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Not found."})


router = APIRouter(prefix="/api/v1/einvoice", tags=["E-Invoice"], dependencies=[Depends(_enabled)])


def _doc_json(d: EInvoiceDocument) -> dict:
    return {"doc_id": str(d.doc_id), "doc_number": d.doc_number, "type_code": d.type_code, "subtype": d.subtype, "icv": d.icv, "status": d.status,
            "attempts": d.attempts, "last_error": d.last_error, "issued_at": d.issued_at.isoformat(), "submitted_at": d.submitted_at.isoformat() if d.submitted_at else None}


@router.get("/status", summary="Staff: unit state and document counts by status")
async def status_(staff: StaffUser) -> dict:
    async with get_system_session() as s:
        units = (await s.execute(select(EInvoiceUnit))).scalars().all()
        counts = dict((await s.execute(select(EInvoiceDocument.status, func.count()).group_by(EInvoiceDocument.status))).all())
    return {"env": get_settings().einvoice_env, "production_unlocked": get_settings().einvoice_env == "production" and get_settings().einvoice_allow_production_submit,
            "units": [{"unit_id": str(u.unit_id), "name": u.name, "env": u.env, "status": u.status, "icv": u.icv_counter, "has_key": bool(u.private_key_enc),
                       "has_certificate": bool(u.certificate_b64)} for u in units], "documents": counts}


class CsrBody(BaseModel):
    org_unit: str = Field(..., max_length=64)
    address: str = Field(..., max_length=200)
    business_category: str = Field(..., max_length=100)
    unit_serial: str = Field(..., max_length=100, description="1-<solution>|2-<model>|3-<unique id>")


@router.post("/onboarding/csr", summary="Staff: generate the key (stored encrypted) and the CSR to paste into the Fatoora portal")
async def make_csr(staff: StaffUser, body: CsrBody) -> dict:
    async with get_system_session() as s:
        unit = await ez.get_or_create_unit(s)
        try:
            csr = await ez.start_onboarding(s, unit, org_unit=body.org_unit, address=body.address, business_category=body.business_category, unit_serial=body.unit_serial)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": "INVALID_CSR_INPUT", "message": str(exc)}) from exc
    return {"csr_pem": csr, "env": unit.env}


class OtpBody(BaseModel):
    otp: str = Field(..., min_length=4, max_length=12)


@router.post("/onboarding/compliance-csid", summary="Staff: exchange the CSR + OTP for a Compliance CSID")
async def compliance_csid(staff: StaffUser, body: OtpBody) -> dict:
    try:
        async with get_system_session() as s:
            unit = await ez.get_or_create_unit(s)
            if not unit.csr_pem:
                raise HTTPException(status_code=409, detail={"code": "NO_CSR", "message": "Generate the CSR first."})
            await ez.request_compliance_csid(s, unit, body.otp)
    except EInvoiceError as exc:
        raise HTTPException(status_code=502, detail={"code": "ZATCA_ERROR", "message": str(exc)}) from exc
    return {"status": "compliance"}


@router.post("/onboarding/production-csid", summary="Staff: exchange the passed compliance request for the Production CSID")
async def production_csid(staff: StaffUser) -> dict:
    try:
        async with get_system_session() as s:
            await ez.request_production_csid(s, await ez.get_or_create_unit(s))
    except EInvoiceError as exc:
        raise HTTPException(status_code=502, detail={"code": "ZATCA_ERROR", "message": str(exc)}) from exc
    return {"status": "production"}


@router.get("/documents", summary="Staff: recent documents (optionally by status)")
async def documents(staff: StaffUser, status: str | None = None, limit: int = 100) -> dict:
    async with get_system_session() as s:
        stmt = select(EInvoiceDocument).order_by(EInvoiceDocument.icv.desc()).limit(min(max(limit, 1), 500))
        if status:
            stmt = stmt.where(EInvoiceDocument.status == status)
        return {"items": [_doc_json(d) for d in (await s.execute(stmt)).scalars()]}


@router.post("/documents/{doc_id}/retry", summary="Staff: put a rejected / dead document back in the queue after the data was fixed")
async def retry(staff: StaffUser, doc_id: Annotated[uuid.UUID, Path()]) -> dict:
    async with get_system_session() as s:
        d = await s.scalar(select(EInvoiceDocument).where(EInvoiceDocument.doc_id == doc_id).with_for_update())
        if d is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Not found."})
        if d.status not in ("dead", "pending"):
            raise HTTPException(status_code=409, detail={"code": "NOT_RETRYABLE", "message": "Only dead or pending documents can be retried (a rejected one needs a corrected new document)."})
        d.status, d.attempts, d.last_error = "pending", 0, None
        d.next_attempt_at = func.now()
    return {"status": "pending"}


async def _own_doc(session, invoice_id: uuid.UUID, user) -> EInvoiceDocument:
    if user.role not in (TenantUserRole.ADMIN, TenantUserRole.AUDITOR):
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "Admins and auditors only."})
    d = await session.scalar(select(EInvoiceDocument).where(EInvoiceDocument.invoice_id == invoice_id))
    if d is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "لا يوجد مستند إلكتروني لهذه الفاتورة."})
    return d


@router.get("/invoices/{invoice_id}/xml", summary="The signed UBL XML of one of your invoices")
async def invoice_xml(user: CurrentUser, session: AuthTenantSession, invoice_id: Annotated[uuid.UUID, Path()]) -> Response:
    d = await _own_doc(session, invoice_id, user)
    return Response(content=d.xml, media_type="application/xml", headers={"Content-Disposition": f'attachment; filename="{d.doc_number}.xml"', "Cache-Control": "private, no-store"})


@router.get("/invoices/{invoice_id}/pdf", summary="PDF/A-3 with the signed XML embedded")
async def invoice_pdf(user: CurrentUser, session: AuthTenantSession, invoice_id: Annotated[uuid.UUID, Path()]) -> Response:
    d = await _own_doc(session, invoice_id, user)
    inv = await session.scalar(select(BillingInvoice).where(BillingInvoice.invoice_id == invoice_id))
    data = einvoice_pdf.build_pdfa3(d, xml=d.xml, title=d.doc_number, number=d.doc_number, subtotal=inv.subtotal_halalas, vat=inv.vat_halalas, total=inv.total_halalas,
                                    vat_rate_bp=inv.vat_rate_bp, description=inv.description, seller=inv.seller or {}, buyer=inv.buyer or {}, qr=d.qr)
    return Response(content=data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{d.doc_number}.pdf"', "Cache-Control": "private, no-store"})

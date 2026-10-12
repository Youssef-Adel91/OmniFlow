"""E-invoice orchestration: issue (build → chain → sign → store), submit (clearance / reporting with retry + dead-letter), onboarding steps.

Everything here is a no-op while EINVOICE_ENABLED is false. All functions take a SYSTEM session (documents are tenant-scoped by RLS, the unit is
system-only). The legal precondition for turning it on in production — the issuing entity being VAT-registered in Saudi Arabia and having completed
ZATCA onboarding — is a business step, not something this code can check.
"""
from __future__ import annotations

import base64
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select

from src.einvoice import certs, signing, ubl
from src.einvoice.client import Credentials, EInvoiceError, ZatcaClient
from src.shared.core.config import get_settings
from src.shared.db.models import BillingInvoice, CreditNote, EInvoiceDocument, EInvoiceUnit, Tenant
from src.shared.security.crypto import decrypt_secret, encrypt_secret

logger = structlog.get_logger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enabled() -> bool:
    return bool(get_settings().einvoice_enabled)


def seller_party() -> ubl.Party:
    c = get_settings()
    return ubl.Party(name=c.billing_seller_name, vat_number=c.billing_seller_tax_number, crn=c.billing_seller_cr_number, street=c.billing_seller_street,
                     building=c.billing_seller_building, district=c.billing_seller_district, city=c.billing_seller_city, postal_zone=c.billing_seller_postal_zone)


def buyer_party(tenant: Tenant | None, snapshot: dict) -> ubl.Party:
    addr = (getattr(tenant, "billing_address", None) or {}) if tenant else {}
    return ubl.Party(name=(snapshot or {}).get("name") or getattr(tenant, "business_name", "") or "", vat_number=getattr(tenant, "vat_number", None) or "",
                     crn=getattr(tenant, "cr_number", None) or "", street=addr.get("street", ""), building=addr.get("building", ""),
                     district=addr.get("district", ""), city=addr.get("city", ""), postal_zone=addr.get("postal_zone", ""))


async def get_or_create_unit(session, *, name: str = "omniflow-main", env: str | None = None) -> EInvoiceUnit:
    env = env or get_settings().einvoice_env
    unit = await session.scalar(select(EInvoiceUnit).where(EInvoiceUnit.env == env, EInvoiceUnit.name == name).with_for_update())
    if unit is None:
        vat = get_settings().billing_seller_tax_number
        unit = EInvoiceUnit(unit_id=uuid.uuid4(), name=name, env=env, status="new", vat_number=vat, icv_counter=0, last_hash=ubl.PIH_FIRST)
        session.add(unit)
        await session.flush()
    return unit


def _signing_material(unit: EInvoiceUnit):
    if not (unit.private_key_enc and unit.certificate_b64):
        raise EInvoiceError("the issuing unit has no key/certificate yet — complete onboarding first")
    return certs.key_from_pem(decrypt_secret(unit.private_key_enc).encode()), base64.b64decode(unit.certificate_b64)


def _credentials(unit: EInvoiceUnit) -> Credentials:
    if not (unit.certificate_b64 and unit.secret_enc):
        raise EInvoiceError("the issuing unit has no CSID credentials yet")
    # ZATCA returns the certificate as base64-of-(base64 DER) in binarySecurityToken; we store the DER and re-wrap here.
    return Credentials(binary_security_token=base64.b64encode(unit.certificate_b64.encode()).decode(), secret=decrypt_secret(unit.secret_enc))


def _qr_fields(inv: ubl.InvoiceData) -> dict:
    t = ubl.totals(inv)
    return dict(seller_name=inv.seller.name, vat_number=inv.seller.vat_number, timestamp=inv.issued_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                total=ubl.money(t["incl"]), vat_total=ubl.money(t["tax"]))


async def _issue(session, *, tenant: Tenant | None, tenant_id: uuid.UUID, number: str, type_code: str, issued_at: datetime, lines: list[ubl.Line],
                 buyer_snapshot: dict, invoice_id=None, credit_note_id=None, reason: str = "", billing_reference: str = "",
                 note: str = "") -> EInvoiceDocument | None:
    if not enabled():
        return None
    existing = await session.scalar(select(EInvoiceDocument).where(
        EInvoiceDocument.invoice_id == invoice_id if invoice_id else EInvoiceDocument.credit_note_id == credit_note_id))
    if existing is not None:
        return existing
    unit = await get_or_create_unit(session)                 # row lock: the chain is serialised here
    key, cert_der = _signing_material(unit)
    buyer = buyer_party(tenant, buyer_snapshot)
    subtype = ubl.SUBTYPE_STANDARD if buyer.vat_number else ubl.SUBTYPE_SIMPLIFIED
    inv = ubl.InvoiceData(number=number, uuid=ubl.new_uuid(), issued_at=issued_at, type_code=type_code, subtype=subtype, seller=seller_party(), buyer=buyer,
                          lines=lines, icv=unit.icv_counter + 1, pih=unit.last_hash, reason=reason, billing_reference=billing_reference, note=note)
    root = ubl.build_xml(inv)                                # raises InvalidInvoice (e.g. no seller VAT number) BEFORE the chain moves
    res = signing.sign_invoice(root, key, cert_der, qr_fields=_qr_fields(inv), simplified=subtype == ubl.SUBTYPE_SIMPLIFIED)
    xml = signing.etree.tostring(root, xml_declaration=True, encoding="UTF-8").decode()
    unit.icv_counter, unit.last_hash = inv.icv, res["invoice_hash"]
    doc = EInvoiceDocument(doc_id=uuid.uuid4(), tenant_id=tenant_id, unit_id=unit.unit_id, invoice_id=invoice_id, credit_note_id=credit_note_id,
                           doc_number=number, type_code=type_code, subtype=subtype, uuid=inv.uuid, icv=inv.icv, pih=inv.pih, invoice_hash=res["invoice_hash"],
                           xml=xml, qr=res["qr"], status="pending", attempts=0, next_attempt_at=issued_at, issued_at=issued_at)
    session.add(doc)
    await session.flush()
    return doc


async def issue_for_invoice(session, invoice: BillingInvoice) -> EInvoiceDocument | None:
    tenant = await session.scalar(select(Tenant).where(Tenant.tenant_id == invoice.tenant_id))
    lines = [ubl.Line(name=invoice.description[:200], quantity=1, unit_price_halalas=invoice.subtotal_halalas, vat_rate_bp=invoice.vat_rate_bp)]
    return await _issue(session, tenant=tenant, tenant_id=invoice.tenant_id, number=invoice.invoice_number, type_code=ubl.TYPE_INVOICE, issued_at=invoice.issued_at,
                        lines=lines, buyer_snapshot=invoice.buyer or {}, invoice_id=invoice.invoice_id)


async def issue_for_credit_note(session, note: CreditNote) -> EInvoiceDocument | None:
    tenant = await session.scalar(select(Tenant).where(Tenant.tenant_id == note.tenant_id))
    original = await session.scalar(select(BillingInvoice).where(BillingInvoice.invoice_id == note.original_invoice_id))
    lines = [ubl.Line(name=("استرداد — " + note.reason)[:200], quantity=1, unit_price_halalas=note.subtotal_halalas, vat_rate_bp=note.vat_rate_bp)]
    return await _issue(session, tenant=tenant, tenant_id=note.tenant_id, number=note.credit_note_number, type_code=ubl.TYPE_CREDIT, issued_at=note.issued_at,
                        lines=lines, buyer_snapshot=note.buyer or {}, credit_note_id=note.credit_note_id, reason=note.reason,
                        billing_reference=original.invoice_number if original else "")


async def issue_safely(session, *, invoice: BillingInvoice | None = None, note: CreditNote | None = None) -> None:
    """Called from billing flows: an e-invoice problem must never undo a payment or a refund. Failures are logged and picked up by ops."""
    if not enabled():
        return
    try:
        async with session.begin_nested():
            if invoice is not None:
                await issue_for_invoice(session, invoice)
            if note is not None:
                await issue_for_credit_note(session, note)
    except Exception as exc:  # noqa: BLE001
        logger.error("einvoice_issue_failed", error=type(exc).__name__, detail=str(exc)[:200])


# ── submission ───────────────────────────────────────────────────────────────────

def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(60 * (2 ** attempts), 6 * 3600))


async def submit_document(session, doc: EInvoiceDocument, now: datetime | None = None, *, client: ZatcaClient | None = None) -> str:
    """Clearance for standard (B2B) documents, reporting for simplified (B2C). Returns the new status."""
    now = now or _now()
    cfg = get_settings()
    unit = await session.scalar(select(EInvoiceUnit).where(EInvoiceUnit.unit_id == doc.unit_id))
    try:
        zc = client or ZatcaClient(env=unit.env)             # raises while production is locked
        creds = _credentials(unit)
        args = dict(invoice_hash=doc.invoice_hash, uuid=doc.uuid, signed_xml=doc.xml.encode())
        res = await (zc.clear(creds, **args) if doc.subtype == ubl.SUBTYPE_STANDARD else zc.report(creds, **args))
    except EInvoiceError as exc:
        doc.attempts += 1
        doc.last_error = str(exc)[:500]
        if exc.status == 400:                                 # ZATCA validated and refused it: a human must fix the data, retrying cannot help
            doc.status, doc.zatca_response = "rejected", exc.body if isinstance(exc.body, dict) else {"body": str(exc.body)[:1000]}
        elif exc.status == 409:                               # already accepted earlier (e.g. response lost): treat as done
            doc.status = "cleared" if doc.subtype == ubl.SUBTYPE_STANDARD else "reported"
            doc.zatca_response, doc.submitted_at = exc.body if isinstance(exc.body, dict) else None, now
        elif doc.attempts >= cfg.einvoice_max_attempts:
            doc.status = "dead"
        else:
            doc.next_attempt_at = now + (_backoff(doc.attempts) if exc.retryable else timedelta(hours=1))
        await session.flush()
        logger.warning("einvoice_submit_failed", doc=str(doc.doc_id), status=doc.status, retryable=exc.retryable, http=exc.status)
        return doc.status
    doc.attempts += 1
    doc.status = "cleared" if doc.subtype == ubl.SUBTYPE_STANDARD else "reported"
    doc.zatca_response, doc.submitted_at, doc.last_error, doc.next_attempt_at = res, now, None, None
    await session.flush()
    return doc.status


async def submit_due(session, now: datetime | None = None, limit: int = 50, unit_id: uuid.UUID | None = None) -> dict[str, int]:
    now = now or _now()
    out: dict[str, int] = {}
    if not enabled():
        return out
    stmt = select(EInvoiceDocument).where(EInvoiceDocument.status == "pending", EInvoiceDocument.next_attempt_at <= now)
    if unit_id is not None:
        stmt = stmt.where(EInvoiceDocument.unit_id == unit_id)
    docs = (await session.execute(stmt.order_by(EInvoiceDocument.icv).limit(limit).with_for_update(skip_locked=True))).scalars().all()
    for d in docs:
        st = await submit_document(session, d, now)
        out[st] = out.get(st, 0) + 1
    return out


# ── onboarding ───────────────────────────────────────────────────────────────────

async def start_onboarding(session, unit: EInvoiceUnit, *, org_unit: str, address: str, business_category: str, unit_serial: str) -> str:
    """Generate the key pair (stored Fernet-encrypted) and the CSR for the Fatoora portal. Returns the PEM CSR (public material)."""
    c = get_settings()
    key = certs.generate_key()
    csr = certs.build_csr(key, env=unit.env, common_name=unit.name, org=c.billing_seller_name, org_unit=org_unit, vat_number=unit.vat_number,
                          unit_serial=unit_serial, address=address, business_category=business_category)
    unit.private_key_enc = encrypt_secret(certs.key_to_pem(key).decode())
    unit.csr_pem, unit.status = csr.decode(), "new"
    await session.flush()
    return csr.decode()


async def request_compliance_csid(session, unit: EInvoiceUnit, otp: str, *, client: ZatcaClient | None = None) -> None:
    zc = client or ZatcaClient(env=unit.env)
    res = await zc.request_compliance_csid(certs.csr_request_body(unit.csr_pem.encode()), otp)
    token = base64.b64decode(res["binarySecurityToken"]).decode()      # ZATCA: base64 of the base64 certificate
    unit.certificate_b64, unit.secret_enc = token, encrypt_secret(res["secret"])
    unit.compliance_request_id, unit.status = str(res.get("requestID")), "compliance"
    await session.flush()


async def request_production_csid(session, unit: EInvoiceUnit, *, client: ZatcaClient | None = None) -> None:
    zc = client or ZatcaClient(env=unit.env)
    res = await zc.request_production_csid(_credentials(unit), unit.compliance_request_id or "")
    unit.certificate_b64, unit.secret_enc = base64.b64decode(res["binarySecurityToken"]).decode(), encrypt_secret(res["secret"])
    unit.status = "production"
    await session.flush()

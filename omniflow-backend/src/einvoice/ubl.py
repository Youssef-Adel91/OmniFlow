"""UBL 2.1 invoice / credit note / debit note XML in ZATCA's profile (reporting:1.0)."""
from __future__ import annotations

import base64
import hashlib
import uuid as uuidlib
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from lxml import etree

NS = {
    None: "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2",
}
CAC, CBC, EXT = "{%s}" % NS["cac"], "{%s}" % NS["cbc"], "{%s}" % NS["ext"]
UBL = "{%s}" % NS[None]

TYPE_INVOICE, TYPE_CREDIT, TYPE_DEBIT = "388", "381", "383"
SUBTYPE_STANDARD, SUBTYPE_SIMPLIFIED = "0100000", "0200000"        # name attribute of InvoiceTypeCode: B2B / B2C
PIH_FIRST = base64.b64encode(hashlib.sha256(b"0").hexdigest().encode()).decode()   # base64(hex(sha256("0"))) -- ZATCA first-invoice previous hash


def amount(halalas: int) -> str:
    sign = "-" if halalas < 0 else ""
    h = abs(int(halalas))
    return f"{sign}{h // 100}.{h % 100:02d}"


def money(d: Decimal) -> str:
    return str(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@dataclass
class Party:
    name: str
    vat_number: str = ""
    crn: str = ""
    street: str = ""
    building: str = ""
    district: str = ""
    city: str = ""
    postal_zone: str = ""
    country: str = "SA"


@dataclass
class Line:
    name: str
    quantity: int
    unit_price_halalas: int            # VAT-exclusive, per unit
    vat_rate_bp: int = 1500


@dataclass
class InvoiceData:
    number: str
    uuid: str
    issued_at: datetime
    type_code: str                      # 388 / 381 / 383
    subtype: str                        # 0100000 standard (B2B) / 0200000 simplified (B2C)
    seller: Party
    buyer: Party
    lines: list[Line]
    icv: int
    pih: str
    currency: str = "SAR"
    reason: str = ""                    # mandatory for credit/debit notes
    billing_reference: str = ""         # number of the invoice a credit/debit note refers to
    delivery_date: str | None = None
    note: str = ""
    tz: str = "Asia/Riyadh"

    def local(self) -> datetime:
        return self.issued_at.astimezone(ZoneInfo(self.tz))


class InvalidInvoice(ValueError):
    pass


def validate(inv: InvoiceData) -> None:
    if not inv.seller.vat_number or len(inv.seller.vat_number) != 15 or not inv.seller.vat_number.isdigit():
        raise InvalidInvoice("الرقم الضريبي للبائع مطلوب (15 رقمًا).")
    if not inv.seller.name:
        raise InvalidInvoice("اسم البائع مطلوب.")
    if inv.subtype == SUBTYPE_STANDARD and (not inv.buyer.vat_number or not inv.buyer.name):
        raise InvalidInvoice("الفاتورة الضريبية (B2B) تتطلب اسم المشتري ورقمه الضريبي.")
    if inv.type_code in (TYPE_CREDIT, TYPE_DEBIT) and (not inv.reason or not inv.billing_reference):
        raise InvalidInvoice("الإشعار الدائن/المدين يتطلب سببًا ورقم الفاتورة الأصلية.")
    if not inv.lines:
        raise InvalidInvoice("الفاتورة بلا بنود.")
    for line in inv.lines:
        if line.quantity <= 0 or line.unit_price_halalas < 0:
            raise InvalidInvoice("كمية أو سعر غير صالح.")
    if inv.type_code not in (TYPE_INVOICE, TYPE_CREDIT, TYPE_DEBIT) or inv.subtype not in (SUBTYPE_STANDARD, SUBTYPE_SIMPLIFIED):
        raise InvalidInvoice("نوع المستند غير صالح.")
    if inv.icv < 1:
        raise InvalidInvoice("عداد الفواتير (ICV) غير صالح.")
    try:
        uuidlib.UUID(inv.uuid)
    except ValueError as exc:
        raise InvalidInvoice("UUID غير صالح.") from exc


def totals(inv: InvoiceData) -> dict:
    """Line-level rounding to the halala, like ZATCA's validator: tax per line, sums of the line figures."""
    ext = Decimal(0)
    tax = Decimal(0)
    line_rows = []
    for ln in inv.lines:
        line_ext = (Decimal(ln.unit_price_halalas) * ln.quantity) / 100
        line_tax = (line_ext * ln.vat_rate_bp / 10000).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        ext += line_ext
        tax += line_tax
        line_rows.append((line_ext, line_tax))
    return {"lines": line_rows, "ext": ext, "tax": tax, "incl": ext + tax}


def _sub(parent, tag, text=None, **attrs):
    el = etree.SubElement(parent, tag, **attrs)
    if text is not None:
        el.text = text
    return el


def _party(parent, wrapper_tag: str, p: Party, crn_scheme: str = "CRN") -> None:
    wrapper = _sub(parent, CAC + wrapper_tag)
    party = _sub(wrapper, CAC + "Party")
    if p.crn:
        ident = _sub(party, CAC + "PartyIdentification")
        _sub(ident, CBC + "ID", p.crn, schemeID=crn_scheme)
    addr = _sub(party, CAC + "PostalAddress")
    _sub(addr, CBC + "StreetName", p.street or "-")
    _sub(addr, CBC + "BuildingNumber", p.building or "0000")
    _sub(addr, CBC + "CitySubdivisionName", p.district or "-")
    _sub(addr, CBC + "CityName", p.city or "-")
    _sub(addr, CBC + "PostalZone", p.postal_zone or "00000")
    _sub(_sub(addr, CAC + "Country"), CBC + "IdentificationCode", p.country)
    if p.vat_number:
        scheme = _sub(party, CAC + "PartyTaxScheme")
        _sub(scheme, CBC + "CompanyID", p.vat_number)
        _sub(_sub(scheme, CAC + "TaxScheme"), CBC + "ID", "VAT")
    _sub(_sub(party, CAC + "PartyLegalEntity"), CBC + "RegistrationName", p.name or "-")


def build_xml(inv: InvoiceData, *, qr: str | None = None, extensions: etree._Element | None = None) -> etree._Element:
    """The unsigned (or, with `extensions`/`qr`, signed) document. Validates the business rules first."""
    validate(inv)
    t = totals(inv)
    cur = inv.currency
    root = etree.Element(UBL + "Invoice", nsmap=NS)
    if extensions is not None:
        root.append(extensions)
    _sub(root, CBC + "ProfileID", "reporting:1.0")
    _sub(root, CBC + "ID", inv.number)
    _sub(root, CBC + "UUID", inv.uuid)
    local = inv.local()
    _sub(root, CBC + "IssueDate", local.strftime("%Y-%m-%d"))
    _sub(root, CBC + "IssueTime", local.strftime("%H:%M:%S"))
    _sub(root, CBC + "InvoiceTypeCode", inv.type_code, name=inv.subtype)
    if inv.note:
        _sub(root, CBC + "Note", inv.note, languageID="ar")
    _sub(root, CBC + "DocumentCurrencyCode", cur)
    _sub(root, CBC + "TaxCurrencyCode", "SAR")
    if inv.billing_reference:
        _sub(_sub(_sub(root, CAC + "BillingReference"), CAC + "InvoiceDocumentReference"), CBC + "ID", inv.billing_reference)
    _sub(_sub(root, CAC + "AdditionalDocumentReference"), CBC + "ID", "ICV")
    root[-1].append(etree.Element(CBC + "UUID"))
    root[-1][-1].text = str(inv.icv)
    pih = _sub(root, CAC + "AdditionalDocumentReference")
    _sub(pih, CBC + "ID", "PIH")
    _sub(_sub(pih, CAC + "Attachment"), CBC + "EmbeddedDocumentBinaryObject", inv.pih, mimeCode="text/plain")
    if qr is not None:
        qref = _sub(root, CAC + "AdditionalDocumentReference")
        _sub(qref, CBC + "ID", "QR")
        _sub(_sub(qref, CAC + "Attachment"), CBC + "EmbeddedDocumentBinaryObject", qr, mimeCode="text/plain")
    sig = _sub(root, CAC + "Signature")
    _sub(sig, CBC + "ID", "urn:oasis:names:specification:ubl:signature:Invoice")
    _sub(sig, CBC + "SignatureMethod", "urn:oasis:names:specification:ubl:dsig:enveloped:xades")
    _party(root, "AccountingSupplierParty", inv.seller)
    _party(root, "AccountingCustomerParty", inv.buyer)
    delivery = _sub(root, CAC + "Delivery")
    day = inv.delivery_date or local.strftime("%Y-%m-%d")
    _sub(delivery, CBC + "ActualDeliveryDate", day)
    _sub(delivery, CBC + "LatestDeliveryDate", day)
    pm = _sub(root, CAC + "PaymentMeans")
    _sub(pm, CBC + "PaymentMeansCode", "10")
    if inv.type_code in (TYPE_CREDIT, TYPE_DEBIT):
        _sub(pm, CBC + "InstructionNote", inv.reason)
    tax_only = _sub(root, CAC + "TaxTotal")
    _sub(tax_only, CBC + "TaxAmount", money(t["tax"]), currencyID=cur)
    tax_full = _sub(root, CAC + "TaxTotal")
    _sub(tax_full, CBC + "TaxAmount", money(t["tax"]), currencyID=cur)
    sub = _sub(tax_full, CAC + "TaxSubtotal")
    _sub(sub, CBC + "TaxableAmount", money(t["ext"]), currencyID=cur)
    _sub(sub, CBC + "TaxAmount", money(t["tax"]), currencyID=cur)
    cat = _sub(sub, CAC + "TaxCategory")
    _sub(cat, CBC + "ID", "S", schemeID="UN/ECE 5305", schemeAgencyID="6")
    _sub(cat, CBC + "Percent", f"{inv.lines[0].vat_rate_bp / 100:.2f}")
    _sub(_sub(cat, CAC + "TaxScheme"), CBC + "ID", "VAT", schemeID="UN/ECE 5153", schemeAgencyID="6")
    lm = _sub(root, CAC + "LegalMonetaryTotal")
    for tag, val in (("LineExtensionAmount", t["ext"]), ("TaxExclusiveAmount", t["ext"]), ("TaxInclusiveAmount", t["incl"]),
                     ("AllowanceTotalAmount", Decimal(0)), ("PrepaidAmount", Decimal(0)), ("PayableAmount", t["incl"])):
        _sub(lm, CBC + tag, money(val), currencyID=cur)
    for i, (ln, (line_ext, line_tax)) in enumerate(zip(inv.lines, t["lines"]), start=1):
        el = _sub(root, CAC + "InvoiceLine")
        _sub(el, CBC + "ID", str(i))
        _sub(el, CBC + "InvoicedQuantity", f"{ln.quantity:.6f}", unitCode="PCE")
        _sub(el, CBC + "LineExtensionAmount", money(line_ext), currencyID=cur)
        lt = _sub(el, CAC + "TaxTotal")
        _sub(lt, CBC + "TaxAmount", money(line_tax), currencyID=cur)
        _sub(lt, CBC + "RoundingAmount", money(line_ext + line_tax), currencyID=cur)
        item = _sub(el, CAC + "Item")
        _sub(item, CBC + "Name", ln.name)
        ctc = _sub(item, CAC + "ClassifiedTaxCategory")
        _sub(ctc, CBC + "ID", "S")
        _sub(ctc, CBC + "Percent", f"{ln.vat_rate_bp / 100:.2f}")
        _sub(_sub(ctc, CAC + "TaxScheme"), CBC + "ID", "VAT")
        _sub(_sub(el, CAC + "Price"), CBC + "PriceAmount", money(Decimal(ln.unit_price_halalas) / 100), currencyID=cur)
    return root


def new_uuid() -> str:
    return str(uuidlib.uuid4())

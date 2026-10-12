"""Human-readable invoice PDF/A-3 with the signed XML embedded as an attachment and the QR printed on the page (ZATCA's "PDF/A-3 with embedded XML").

WeasyPrint writes the PDF/A-3b variant and embeds the XML; the QR is drawn as inline SVG (python `qrcode`, no PIL needed).
Whether ZATCA's validator accepts this exact PDF has not been checked here.
"""
from __future__ import annotations

import io
from html import escape
from pathlib import Path

import qrcode
import qrcode.image.svg
from weasyprint import Attachment, CSS, HTML

from src.shared.services.billing.invoice_pdf import _FONT, _date, money


def qr_svg(payload_b64: str) -> str:
    img = qrcode.make(payload_b64, image_factory=qrcode.image.svg.SvgPathImage, box_size=4, border=1)
    buf = io.BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode()
    return svg[svg.index("<svg"):]


def html(doc, *, title: str, number: str, subtotal: int, vat: int, total: int, vat_rate_bp: int, description: str, seller: dict, buyer: dict,
         qr: str, currency: str = "SAR") -> str:
    e = lambda v: escape(str(v or "—"))  # noqa: E731
    kind = "فاتورة ضريبية" if doc.subtype == "0100000" else "فاتورة ضريبية مبسّطة"
    if doc.type_code == "381":
        kind = "إشعار دائن"
    elif doc.type_code == "383":
        kind = "إشعار مدين"
    return f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>{e(title)}</title></head><body>
<h1>{kind}</h1>
<table class="meta"><tr><td>الرقم</td><td dir="ltr">{e(number)}</td></tr><tr><td>التاريخ</td><td>{_date(doc.issued_at)}</td></tr>
<tr><td>المعرّف (UUID)</td><td dir="ltr" style="font-size:8pt">{e(doc.uuid)}</td></tr><tr><td>العدّاد التسلسلي (ICV)</td><td dir="ltr">{doc.icv}</td></tr></table>
<div class="parties"><div><h2>البائع</h2><p>{e(seller.get('name'))}</p><p>الرقم الضريبي: <span dir="ltr">{e(seller.get('tax_number'))}</span></p></div>
<div><h2>المشتري</h2><p>{e(buyer.get('name'))}</p></div></div>
<table class="lines"><thead><tr><th>البيان</th><th>المبلغ قبل الضريبة</th></tr></thead><tbody><tr><td>{e(description)}</td><td>{money(subtotal, currency)}</td></tr></tbody></table>
<table class="totals"><tr><td>المجموع قبل الضريبة</td><td>{money(subtotal, currency)}</td></tr><tr><td>ضريبة القيمة المضافة ({vat_rate_bp / 100:g}%)</td><td>{money(vat, currency)}</td></tr>
<tr class="grand"><td>الإجمالي</td><td>{money(total, currency)}</td></tr></table>
<div class="qr">{qr_svg(qr)}</div></body></html>"""


def build_pdfa3(doc, *, xml: str, **fields) -> bytes:
    css = CSS(string=f"""
    @font-face {{ font-family: "NotoNaskh"; src: url("{Path(_FONT).as_posix()}"); }}
    @page {{ size: A4; margin: 2cm; }}
    body {{ direction: rtl; text-align: right; font-family: "NotoNaskh"; font-size: 11pt; color: #111; font-feature-settings: "liga" 0, "rlig" 0, "calt" 0, "clig" 0; }}
    h1 {{ text-align: center; font-size: 20pt; margin: 0 0 1em; }} h2 {{ font-size: 12pt; margin: 0 0 .3em; border-bottom: 1px solid #999; }}
    table {{ width: 100%; border-collapse: collapse; margin: .8em 0; }} .meta td {{ padding: 2px 6px; }} .meta td:first-child {{ width: 30%; color: #444; }}
    .parties {{ display: flex; gap: 2em; }} .parties > div {{ flex: 1; }}
    .lines th, .lines td {{ border: 1px solid #888; padding: 6px; }} .lines th {{ background: #eee; }}
    .totals {{ width: 55%; margin-left: auto; margin-right: 0; }} .totals td {{ padding: 4px 6px; border-bottom: 1px solid #ccc; }}
    .totals .grand td {{ font-weight: bold; border-top: 2px solid #111; }} .qr {{ margin-top: 1.5em; text-align: center; }} .qr svg {{ width: 4cm; height: 4cm; }}
    """)
    attachment = Attachment(string=xml.encode(), name=f"{fields['number']}.xml", description="Signed UBL 2.1 invoice (ZATCA)", relationship="Alternative")
    return HTML(string=html(doc, **fields)).write_pdf(stylesheets=[css], pdf_variant="pdf/a-3b", attachments=[attachment])

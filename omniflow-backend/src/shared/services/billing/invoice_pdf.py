"""Arabic invoice PDF (WeasyPrint + the bundled Noto Naskh font — same stack as the report PDFs).

This is a plain subscription invoice with the VAT split out. It is NOT a ZATCA e-invoice: there is no QR/TLV payload, no
cryptographic stamp and no clearance/reporting call. The footer says so; see IMPLEMENTATION_STATUS.md for what full
compliance still needs.
"""
from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from weasyprint import CSS, HTML

from src.shared.core.config import get_settings

_FONT = Path(__file__).resolve().parents[3] / "ai_workers" / "vault_worker" / "assets" / "NotoNaskhArabic-Regular.ttf"
_MISSING_TAX = "غير مضبوط بعد"


def money(halalas: int, currency: str = "SAR") -> str:
    return f"{halalas / 100:,.2f} {'ر.س' if currency == 'SAR' else currency}"


def _date(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.astimezone(ZoneInfo(get_settings().app_timezone)).strftime("%Y-%m-%d")


def invoice_html(inv, *, credit_note: bool = False) -> str:
    seller, buyer = inv.seller or {}, inv.buyer or {}
    rate = f"{inv.vat_rate_bp / 100:g}"
    period = f"{_date(inv.period_start)} — {_date(inv.period_end)}" if inv.period_start and inv.period_end else "—"
    e = lambda v: escape(str(v or "—"))  # noqa: E731
    return f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"></head><body>
<h1>{'إشعار دائن (استرداد)' if credit_note else 'فاتورة اشتراك'}</h1>
<table class="meta">
<tr><td>{'رقم الإشعار' if credit_note else 'رقم الفاتورة'}</td><td dir="ltr">{e(inv.invoice_number)}</td></tr>
<tr><td>تاريخ الإصدار</td><td>{_date(inv.issued_at)}</td></tr>
<tr><td>العملة</td><td>{e(inv.currency)}</td></tr>
{f'<tr><td>السبب</td><td>{e(getattr(inv, "reason", ""))}</td></tr>' if credit_note else ''}
</table>
<div class="parties">
<div><h2>البائع</h2><p>{e(seller.get('name'))}</p><p>الرقم الضريبي: <span dir="ltr">{e(seller.get('tax_number') or _MISSING_TAX)}</span></p>
<p>السجل التجاري: <span dir="ltr">{e(seller.get('cr_number'))}</span></p><p>{e(seller.get('address'))}</p></div>
<div><h2>العميل</h2><p>{e(buyer.get('name'))}</p><p>رخصة فال: <span dir="ltr">{e(buyer.get('fal_license'))}</span></p></div>
</div>
<table class="lines"><thead><tr><th>البيان</th><th>الفترة</th><th>المبلغ قبل الضريبة</th></tr></thead>
<tbody><tr><td>{e(inv.description)}</td><td>{period}</td><td>{money(inv.subtotal_halalas, inv.currency)}</td></tr></tbody></table>
<table class="totals">
<tr><td>المجموع قبل الضريبة</td><td>{money(inv.subtotal_halalas, inv.currency)}</td></tr>
<tr><td>ضريبة القيمة المضافة ({rate}%)</td><td>{money(inv.vat_halalas, inv.currency)}</td></tr>
<tr class="grand"><td>{'إجمالي المبلغ المسترد' if credit_note else 'الإجمالي المستحق'}</td><td>{money(inv.total_halalas, inv.currency)}</td></tr>
</table>
<p class="note">هذه فاتورة إلكترونية عادية بتفصيل ضريبة القيمة المضافة، ولا تتضمن رمز الاستجابة السريعة (QR) ولا الختم الإلكتروني
المطلوب ضمن منظومة الفوترة الإلكترونية لهيئة الزكاة والضريبة والجمارك، ولا تُعد بديلًا عنها.</p>
</body></html>"""


def build_invoice_pdf(inv, *, credit_note: bool = False) -> bytes:
    css = CSS(string=f"""
    @font-face {{ font-family: "NotoNaskh"; src: url("{_FONT.as_posix()}"); }}
    @page {{ size: A4; margin: 2cm; }}
    body {{ direction: rtl; text-align: right; font-family: "NotoNaskh"; font-size: 11pt; color: #111;
            font-feature-settings: "liga" 0, "rlig" 0, "calt" 0, "clig" 0; }}
    h1 {{ text-align: center; font-size: 20pt; margin: 0 0 1em; }}
    h2 {{ font-size: 12pt; margin: 0 0 .3em; border-bottom: 1px solid #999; }}
    table {{ width: 100%; border-collapse: collapse; margin: .8em 0; }}
    .meta td {{ padding: 2px 6px; }} .meta td:first-child {{ width: 30%; color: #444; }}
    .parties {{ display: flex; gap: 2em; }} .parties > div {{ flex: 1; }}
    .lines th, .lines td {{ border: 1px solid #888; padding: 6px; }} .lines th {{ background: #eee; }}
    .totals {{ width: 55%; margin-right: 0; margin-left: auto; }} .totals td {{ padding: 4px 6px; border-bottom: 1px solid #ccc; }}
    .totals .grand td {{ font-weight: bold; border-top: 2px solid #111; }}
    .note {{ margin-top: 1.5em; font-size: 8.5pt; color: #444; }}
    """)
    return HTML(string=invoice_html(inv, credit_note=credit_note)).write_pdf(stylesheets=[css])

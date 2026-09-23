"""
ai_workers/vault_worker/pdf_builder.py — Real PDF authoring for paid customer reports.

Builds a genuine PDF document (not text saved with a ".pdf" extension) from the
data actually available in the system today: tenant/customer identity and the
payment/report metadata carried on the Kafka payment event. Any report-type
content beyond that (REGA-verified deed details, municipal consulting body
text) depends on integrations that are not implemented yet — see
IMPLEMENTATION_STATUS.md. `extra_fields` is where that content will land once
those integrations exist, without changing this module.

Arabic glyphs are positional and the script is right-to-left, so plain UTF-8
text handed to a PDF layout engine renders as disconnected, reversed letters.
`arabic_reshaper` joins the letterforms and `python-bidi` reorders the run for
display; the embedded Noto Naskh Arabic font (SIL OFL, bundled in ./assets)
supplies the actual glyphs, since PDF viewers cannot be relied on to have an
Arabic-capable font installed.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import arabic_reshaper
from bidi.algorithm import get_display
from fpdf import FPDF

_FONT_PATH = Path(__file__).parent / "assets" / "NotoNaskhArabic-Regular.ttf"

REPORT_TITLES = {
    "deed_check_29": "تقرير التحقق من الصك",
    "municipal_consulting_15": "تقرير الاستشارة البلدية",
    "premium_consultation": "الاستشارة المتميزة",
}


def _shape(text: str) -> str:
    """Reshape + bidi-reorder Arabic text for correct rendering by fpdf2's LTR layout."""
    return get_display(arabic_reshaper.reshape(text))


def build_report_pdf(
    *,
    report_type: str,
    tenant_business_name: str,
    customer_display_name: str | None,
    customer_phone: str,
    price_sar: float,
    transaction_reference: str,
    generated_at: datetime,
    extra_fields: dict[str, str] | None = None,
) -> bytes:
    """Render a real, well-formed PDF for a paid customer report. Returns raw PDF bytes."""
    pdf = FPDF(format="A4")
    pdf.add_page()
    pdf.add_font("NotoNaskh", "", str(_FONT_PATH))
    # fpdf2 mis-measures w=0 (auto-width) combined with align="C"/"R", raising
    # "Not enough horizontal space" even on a blank page — pass the usable
    # width explicitly instead of relying on the w=0 shorthand.
    content_width = pdf.w - pdf.l_margin - pdf.r_margin

    title = REPORT_TITLES.get(report_type, report_type)
    pdf.set_font("NotoNaskh", size=18)
    pdf.multi_cell(content_width, 12, _shape(title), align="C")
    pdf.ln(4)

    rows = [
        ("الجهة", tenant_business_name),
        ("العميل", customer_display_name or "غير محدد"),
        ("رقم الجوال", customer_phone),
        ("نوع التقرير", report_type),
        ("المبلغ المدفوع", f"{price_sar:.2f} ريال سعودي"),
        ("رقم العملية", transaction_reference),
        ("تاريخ الإصدار", generated_at.strftime("%Y-%m-%d %H:%M UTC")),
    ]
    if extra_fields:
        rows.extend(extra_fields.items())

    pdf.set_font("NotoNaskh", size=11)
    for label, value in rows:
        pdf.multi_cell(content_width, 9, _shape(f"{label}: {value}"), align="R")

    pdf.ln(6)
    pdf.set_font("NotoNaskh", size=9)
    pdf.multi_cell(
        content_width,
        7,
        _shape(
            "هذا التقرير آلي وتم إصداره بناءً على البيانات المتاحة وقت الدفع. "
            "لا يغني عن التحقق الرسمي من الجهات المختصة."
        ),
        align="R",
    )

    return bytes(pdf.output())


def _demo() -> None:
    """ponytail self-check: a built report is a well-formed, non-trivial PDF."""
    pdf_bytes = build_report_pdf(
        report_type="deed_check_29",
        tenant_business_name="عقارات الاختبار",
        customer_display_name="عميل تجريبي",
        customer_phone="+966500000000",
        price_sar=29.0,
        transaction_reference="txn_demo_123",
        generated_at=datetime(2026, 1, 1, 12, 0),
        extra_fields={"رقم الصك": "123456789"},
    )
    assert pdf_bytes.startswith(b"%PDF-"), "output is not a valid PDF header"
    assert pdf_bytes.rstrip().endswith(b"%%EOF"), "output is not a well-formed PDF trailer"
    assert len(pdf_bytes) > 2000, "output is suspiciously small for a rendered page"
    print(f"pdf_builder self-check passed: {len(pdf_bytes)} bytes")


if __name__ == "__main__":
    _demo()

"""
ai_workers/vault_worker/pdf_builder.py — Real PDF authoring for paid customer reports.

Builds a genuine PDF document (not text saved with a ".pdf" extension) from the
data actually available in the system today: tenant/customer identity and the
payment/report metadata carried on the Kafka payment event. Any report-type
content beyond that (REGA-verified deed details, municipal consulting body
text) depends on integrations that are not implemented yet — see
IMPLEMENTATION_STATUS.md. `extra_fields` is where that content will land once
those integrations exist, without changing this module.

Arabic shaping uses fpdf2's HarfBuzz text-shaping engine (`uharfbuzz`) with
`direction="rtl"`/`script="arab"`, so joining and bidi reordering happen at the
glyph level inside the PDF content stream itself — the output looks correct in
any compliant PDF viewer, unlike pre-joining text with `arabic_reshaper` and
handing a viewer already-shaped presentation-form characters, which some
viewers re-shape a second time and render as disconnected letters. The
embedded Noto Naskh Arabic font (SIL OFL, bundled in ./assets) supplies the
actual glyphs, since PDF viewers cannot be relied on to have an Arabic-capable
font installed.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

_FONT_PATH = Path(__file__).parent / "assets" / "NotoNaskhArabic-Regular.ttf"

REPORT_TITLES = {
    "deed_check_29": "تقرير التحقق من الصك",
    "municipal_consulting_15": "تقرير الاستشارة البلدية",
    "premium_consultation": "الاستشارة المتميزة",
}


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
    pdf.set_text_shaping(use_shaping_engine=True, direction="rtl", script="arab")

    # fpdf2 mis-measures w=0 (auto-width) combined with align="C"/"R", raising
    # "Not enough horizontal space" even on a blank page — pass the usable
    # width explicitly instead of relying on the w=0 shorthand.
    content_width = pdf.w - pdf.l_margin - pdf.r_margin

    def line(text: str, size: int, align: str = "R", extra_gap: float = 0.0) -> None:
        # multi_cell defaults to new_x=XPos.RIGHT, which leaves the cursor at
        # the right edge of whatever was just drawn instead of resetting to
        # the left margin. Every subsequent call then starts further right
        # than the page itself, rendering off-canvas (present in the PDF's
        # text stream, extractable, but invisible) — hence explicit LMARGIN.
        pdf.set_font("NotoNaskh", size=size)
        pdf.multi_cell(
            content_width,
            size * 0.8,
            text,
            align=align,
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        if extra_gap:
            pdf.ln(extra_gap)

    title = REPORT_TITLES.get(report_type, report_type)
    line(title, size=18, align="C", extra_gap=4)

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

    for label, value in rows:
        line(f"{label}: {value}", size=11)

    pdf.ln(6)
    line(
        "هذا التقرير آلي وتم إصداره بناءً على البيانات المتاحة وقت الدفع. "
        "لا يغني عن التحقق الرسمي من الجهات المختصة.",
        size=9,
    )

    return bytes(pdf.output())


def _demo() -> None:
    """ponytail self-check: a built report is a well-formed PDF with every row actually visible."""
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

    import pymupdf

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    page_text = doc[0].get_text()
    # HarfBuzz-shaped glyphs don't round-trip through PyMuPDF's ToUnicode-based
    # text extraction cleanly (a known fpdf2 + complex-script-shaping
    # limitation: visual rendering is correct, copy/paste text is not), so the
    # extraction check only covers the plain-ASCII fields, which do round-trip
    # exactly and prove each row's *value* actually made it onto the page.
    for value in ("deed_check_29", "txn_demo_123", "123456789", "966500000000", "29.00"):
        assert value in page_text, f"row value {value!r} missing from rendered page text"
    # Glyph-level shaping check: rasterize and confirm ink actually appears
    # in the vertical band each row should occupy (catches the off-canvas
    # x-cursor bug even if text happens to still be extractable).
    # Restrict to the top ~45% of the page: this short report (title + 9
    # lines + disclaimer) only occupies that much, so the rest of the page is
    # legitimately blank and must not count against the check.
    pix = doc[0].get_pixmap(dpi=100)
    content_height = int(pix.height * 0.45)
    n_bands = 9
    dark_bands = 0
    for i in range(n_bands):
        y0 = int(content_height * i / n_bands)
        y1 = int(content_height * (i + 1) / n_bands)
        band = pix.samples[y0 * pix.stride : y1 * pix.stride]
        if any(b < 200 for b in band):
            dark_bands += 1
    assert dark_bands >= 7, (
        f"only {dark_bands}/{n_bands} content-area bands have visible ink — "
        "rows are likely overlapping or rendering off-canvas again"
    )
    print(f"pdf_builder self-check passed: {len(pdf_bytes)} bytes, {dark_bands}/{n_bands} bands with ink")


if __name__ == "__main__":
    _demo()

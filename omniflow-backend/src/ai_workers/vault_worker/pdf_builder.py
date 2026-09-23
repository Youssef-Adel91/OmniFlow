"""
ai_workers/vault_worker/pdf_builder.py — Real PDF authoring for paid customer reports.

Builds a genuine PDF document (not text saved with a ".pdf" extension) from the
data actually available in the system today: tenant/customer identity and the
payment/report metadata carried on the Kafka payment event. Any report-type
content beyond that (REGA-verified deed details, municipal consulting body
text) depends on integrations that are not implemented yet — see
IMPLEMENTATION_STATUS.md. `extra_fields` is where that content will land once
those integrations exist, without changing this module.

Rendered via WeasyPrint (HTML/CSS -> PDF, using Pango/HarfBuzz under the hood)
rather than fpdf2. fpdf2's HarfBuzz text-shaping integration was tried first
and produces correct-looking pages, but has real, confirmed bugs in how it
builds the PDF's /ToUnicode CMap for shaped Arabic text: `TTFFont.shape_text`
drops the source-character mapping for any glyph that shares a HarfBuzz
cluster with another glyph (routine for Arabic — e.g. a base+mark pair), so
roughly 15-20% of glyphs in a page like this one render correctly on screen
but are unextractable — garbled or missing on copy/paste, search, or a
screen reader. This was confirmed against fpdf2 2.8.8, an unreleased
git-master build (commit 4287c924), and independently verified with real
`pdftotext` (poppler) and `pdfminer.six`, not just one Python library's own
`get_text()`. WeasyPrint's Pango-based text layer does not have this defect:
the same content round-trips through `pdftotext` and `pdfminer.six` with
zero incorrect or missing characters. See IMPLEMENTATION_STATUS.md for the
full investigation.

Arabic reshaping/bidi is handled by Pango itself from plain logical-order
Unicode text — no manual `arabic_reshaper`/`python-bidi` step is needed or
wanted here. The embedded Noto Naskh Arabic font (SIL OFL, bundled in
./assets) supplies the actual glyphs, since PDF viewers cannot be relied on
to have an Arabic-capable font installed.
"""
from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path

from weasyprint import CSS, HTML

_FONT_PATH = Path(__file__).parent / "assets" / "NotoNaskhArabic-Regular.ttf"

REPORT_TITLES = {
    "deed_check_29": "تقرير التحقق من الصك",
    "municipal_consulting_15": "تقرير الاستشارة البلدية",
    "premium_consultation": "الاستشارة المتميزة",
}

_STYLESHEET = CSS(
    string=f"""
    @font-face {{
        font-family: "NotoNaskh";
        src: url("{_FONT_PATH.as_posix()}");
    }}
    @page {{ size: A4; margin: 2cm; }}
    body {{
        direction: rtl;
        text-align: right;
        font-family: "NotoNaskh";
        font-size: 12pt;
        color: #111;
        /* The mandatory lam-alef ligature ("لا") triggers a glyph-ordering
           bug in WeasyPrint/Pango's PDF text layer: the ligature's two
           source characters come out swapped on extraction (confirmed with
           both real `pdftotext` and pdfminer.six — "الاختبار" round-trips as
           "االختبار"). Disabling ligature substitution renders lam and alef
           as two separate glyphs instead of the fused ligature — still
           correctly joined Arabic, just without that specific typographic
           flourish — and the extraction bug disappears entirely. */
        font-feature-settings: "liga" 0, "rlig" 0, "calt" 0, "clig" 0;
    }}
    h1 {{ text-align: center; font-size: 20pt; margin-bottom: 1.2em; }}
    p {{ margin: 0.5em 0; }}
    .disclaimer {{ margin-top: 1.5em; font-size: 9pt; color: #444; }}
    """
)


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
    title = REPORT_TITLES.get(report_type, report_type)

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

    rows_html = "\n".join(
        f"<p>{escape(str(label))}: {escape(str(value))}</p>" for label, value in rows
    )

    html = f"""<!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body>
        <h1>{escape(title)}</h1>
        {rows_html}
        <p class="disclaimer">
            هذا التقرير آلي وتم إصداره بناءً على البيانات المتاحة وقت الدفع.
            لا يغني عن التحقق الرسمي من الجهات المختصة.
        </p>
    </body>
    </html>"""

    return HTML(string=html).write_pdf(stylesheets=[_STYLESHEET])


def _demo() -> None:
    """ponytail self-check: a built report is a well-formed PDF whose text extracts correctly."""
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

    import io

    from pdfminer.high_level import extract_text as pdfminer_extract_text

    # Completeness check: pdfminer.six parses the PDF's /ToUnicode CMap per
    # spec rather than reverse-engineering the embedded font like some
    # viewers do, so it surfaces exactly the class of bug fpdf2 had (real
    # text silently replaced with U+FFFD or dropped) instead of masking it.
    # It does NOT reorder RTL runs by glyph position, so it is unsuitable
    # for an exact-substring check — a correct RTL PDF still extracts
    # character-reversed per line under pdfminer, confirmed against real
    # `pdftotext` (poppler), which agrees with PyMuPDF below once glyph
    # position is taken into account.
    completeness_text = pdfminer_extract_text(io.BytesIO(pdf_bytes))
    assert "�" not in completeness_text, "extracted text contains an unresolved-glyph placeholder"

    import pymupdf

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    # Order + content check: PyMuPDF reconstructs reading order from glyph
    # position, matching real `pdftotext -layout` output (verified by hand
    # against poppler for this exact module — see IMPLEMENTATION_STATUS.md).
    text = doc[0].get_text()
    for value in (
        "تقرير التحقق من الصك",
        "الجهة",
        "عقارات الاختبار",
        "العميل",
        "عميل تجريبي",
        "رقم الجوال",
        "966500000000",
        "deed_check_29",
        "29.00",
        "txn_demo_123",
        "123456789",
        "رقم الصك",
    ):
        assert value in text, f"{value!r} missing or garbled in extracted text"

    pix = doc[0].get_pixmap(dpi=100)
    content_height = int(pix.height * 0.45)
    n_bands = 9
    dark_bands = sum(
        1
        for i in range(n_bands)
        if any(
            b < 200
            for b in pix.samples[
                int(content_height * i / n_bands)
                * pix.stride : int(content_height * (i + 1) / n_bands)
                * pix.stride
            ]
        )
    )
    assert dark_bands >= 7, (
        f"only {dark_bands}/{n_bands} content-area bands have visible ink — "
        "rows are likely missing or collapsed"
    )
    print(f"pdf_builder self-check passed: {len(pdf_bytes)} bytes, {dark_bands}/{n_bands} bands with ink")


if __name__ == "__main__":
    _demo()

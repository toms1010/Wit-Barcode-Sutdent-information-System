"""The Certificate of Matriculation as a document, not a screen capture.

The dashboard in :mod:`ui.certificate_page` is for working with a record; this
module produces the artefact a registrar actually hands over. The two are drawn
from the same summary objects (:mod:`services.com_summary`), so a figure cannot
be right on screen and wrong on the page.

What makes this a document rather than a form:

* An institutional header -- logo, school name, system name, and the student's
  photograph in a dedicated area -- followed by a thin rule and the document
  title. The logo and the photograph keep their aspect ratio and are never
  stretched or cropped to fill a box.
* Student details as **static label/value text**. No input widgets, no pills, no
  rounded "field" containers: this is the fixed output of an official record.
* A course table whose column headers repeat when the table breaks across pages,
  with a totals row computed from the rows above it.
* Three restrained financial tables -- fee breakdown, discounts and credits,
  payment schedule -- drawn with thin rules rather than heavy web-style cards.
* A footer that states what the document is: a generated copy, with a page count
  and the moment it was produced, and no signature or seal, because this process
  cannot apply one.

Only data the database holds is printed. Institutional details that are not in
the project configuration -- addresses, accreditation text, registrar names -- are
deliberately absent rather than invented.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from config.settings import settings
from services.com_summary import (
    ComSummary,
    FinancialSummary,
    enrollment_status_label,
    format_peso,
    installment_state,
    program_and_year,
    summarize_enrollment,
    summarize_fee_lines,
    summarize_financials,
    to_decimal,
)

logger = logging.getLogger(__name__)

SCHOOL_NAME = "Western Institute of Technology"
SYSTEM_NAME = "Student Information & Matriculation System"
DOCUMENT_TITLE = "CERTIFICATE OF MATRICULATION"

#: Brand palette for the document. The document keeps WIT's own orange, unlike
#: the interface, which uses a softer accent (see ``styles/colors.py``).
_BRAND = colors.HexColor("#AD4D05")
_ACCENT_RULE = colors.HexColor("#EE6B05")
_INK = colors.HexColor("#0F172A")
_MUTED = colors.HexColor("#475569")
_RULE = colors.HexColor("#CBD5E1")
_ZEBRA = colors.HexColor("#F8FAFC")
_TOTAL_FILL = colors.HexColor("#F1F5F9")

PAGE_MARGIN = 16 * mm
CONTENT_WIDTH = A4[0] - 2 * PAGE_MARGIN

#: Fonts tried for the document, in order of preference. The peso sign (U+20B1)
#: is not in the built-in Type-1 fonts ReportLab uses by default -- it renders as
#: a black box -- so the document registers a TrueType face that contains it and
#: falls back to the built-ins, with the currency spelled out, only when no such
#: face is installed.
_DOCUMENT_FONT_CANDIDATES = (
    ("assets/fonts/open-sauce/OpenSans-Regular.ttf", "assets/fonts/open-sauce/OpenSans-Bold.ttf"),
    ("assets/fonts/agrandir/Agrandir-Regular.ttf", "assets/fonts/agrandir/Agrandir-Bold.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf", "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
)

_REGISTERED_FONTS: Optional[tuple[str, str]] = None


def _peso_supported(font_name: str) -> bool:
    """Whether a registered font can actually draw ``₱``."""
    try:
        from reportlab.pdfbase.pdfmetrics import getFont

        face = getFont(font_name).face
        return ord("₱") in getattr(face, "charToGlyph", {})
    except Exception:  # pragma: no cover - depends on the installed font stack
        return False


def _register_document_fonts() -> Optional[tuple[str, str]]:
    """Register a peso-capable sans family, once per process.

    Returns ``(regular, bold)`` font names, or ``None`` when no suitable face is
    installed, in which case the built-in Helvetica is used.
    """
    global _REGISTERED_FONTS
    if _REGISTERED_FONTS is not None:
        return _REGISTERED_FONTS

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for regular_path, bold_path in _DOCUMENT_FONT_CANDIDATES:
        regular = settings.BASE_DIR / regular_path
        bold = settings.BASE_DIR / bold_path
        if not regular.is_file():
            continue
        try:
            pdfmetrics.registerFont(TTFont("WITDocument", str(regular)))
            if bold.is_file():
                pdfmetrics.registerFont(TTFont("WITDocument-Bold", str(bold)))
            else:
                pdfmetrics.registerFont(TTFont("WITDocument-Bold", str(regular)))
        except Exception as exc:
            logger.warning("Could not register document font %s: %s", regular, exc)
            continue
        if not _peso_supported("WITDocument"):
            logger.warning(
                "Font %s has no peso glyph; falling back to the built-in face",
                regular,
            )
            continue
        _REGISTERED_FONTS = ("WITDocument", "WITDocument-Bold")
        return _REGISTERED_FONTS

    return None


def _font_family() -> tuple[str, str, bool]:
    """``(regular, bold, peso_available)`` for the document."""
    family = _register_document_fonts()
    if family:
        return family[0], family[1], True
    return "Helvetica", "Helvetica-Bold", False


def _styles():
    base = getSampleStyleSheet()
    regular, bold, peso = _font_family()

    def style(name, **kwargs):
        kwargs.setdefault("fontName", regular)
        return ParagraphStyle(name, parent=base["Normal"], **kwargs)

    def strong(name, **kwargs):
        kwargs["fontName"] = bold
        return ParagraphStyle(name, parent=base["Normal"], **kwargs)

    return {
        "peso": peso,
        "school": strong("school", fontSize=14, leading=17, textColor=_INK),
        "system": style("system", fontSize=9, leading=12, textColor=_MUTED),
        "title": strong("title", fontSize=13, leading=16, textColor=_BRAND, alignment=TA_CENTER),
        "section": strong("section", fontSize=10, leading=13, textColor=_BRAND),
        "card_heading": strong("cardHeading", fontSize=8.5, leading=11, textColor=_BRAND),
        "label": strong("label", fontSize=9, leading=12, textColor=_MUTED),
        "value": style("value", fontSize=9.5, leading=12.5, textColor=_INK),
        "cell": style("cell", fontSize=8, leading=10, textColor=_INK),
        "cell_bold": strong("cellBold", fontSize=8, leading=10, textColor=_INK),
        "small": style("small", fontSize=7.5, leading=10, textColor=_MUTED),
    }


def _money(amount, styles) -> str:
    """Format an amount for the document, in the symbol its font can draw.

    ``₱`` is dropped for the built-in Type-1 faces, which cannot encode it and
    print a filled box in its place. "PHP 5,618.59" is unambiguous; a black
    square in front of a total is not.
    """
    value = to_decimal(amount)
    if styles.get("peso"):
        return format_peso(value)
    return f"PHP {value:,.2f}"


def _scaled_image(raw_path: Optional[str], max_width, max_height):
    """Return a ReportLab image scaled to fit, or ``None``.

    The stored path is resolved through the project's own asset resolution, and
    the image is fitted by its own aspect ratio -- a photograph is never
    stretched to fill the box, and a missing asset removes the picture instead of
    leaving a broken frame in a document that goes to a registrar.
    """
    if not raw_path:
        return None
    path = settings.resolve_asset(raw_path)
    if path is None:
        logger.warning("Document image missing: %r does not resolve to a file", raw_path)
        return None
    try:
        reader = ImageReader(str(path))
        width, height = reader.getSize()
    except Exception as exc:  # unreadable asset: omit it rather than fail the document
        logger.warning("Document image could not be read (%s): %s", path, exc)
        return None

    scale = min(max_width / width, max_height / height)
    return Image(str(path), width=width * scale, height=height * scale)


def _institutional_header(styles, student, logo_width=26 * mm, photo_size=25 * mm):
    """Logo and school name on the left, the student's photograph on the right."""
    logo = _scaled_image(str(settings.LOGO_PATH), logo_width, logo_width)
    photo = _scaled_image(
        getattr(student, "profile_image_path", None), photo_size, photo_size
    )

    identity = [
        Paragraph(SCHOOL_NAME.upper(), styles["school"]),
        Spacer(1, 1.5 * mm),
        Paragraph(SYSTEM_NAME, styles["system"]),
    ]
    left = Table(
        [[logo]] if logo is not None else [[""]],
        colWidths=[logo_width],
    )
    left.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))

    photo_cell = photo if photo is not None else ""
    gutter = 5 * mm
    identity_width = CONTENT_WIDTH - logo_width - photo_size - 2 * gutter
    header = Table(
        [[left, identity, photo_cell]],
        colWidths=[logo_width, identity_width, photo_size],
    )
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("ALIGN", (2, 0), (2, 0), "RIGHT"),
                ("LINEBELOW", (0, 0), (-1, 0), 0.8, _ACCENT_RULE),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return header


def _profile_block(styles, student, com: ComSummary, enrollment):
    """Static label/value pairs -- the document equivalent of a filled form."""
    term = " • ".join(p for p in (com.academic_year, com.semester) if p) or "Not recorded"
    enrolled_on = ""
    if enrollment is not None and getattr(enrollment, "enrollment_date", None):
        enrolled_on = enrollment.enrollment_date.strftime("%B %d, %Y")

    pairs = [
        ("Student Full Name", student.full_name or "—"),
        ("Program & Year", program_and_year(student) or "—"),
        ("Reference Number", student.reference_number or "—"),
        ("Student ID", student.student_id or "—"),
        ("Enrollment Status", enrollment_status_label(getattr(student, "enrollment_status", ""))),
        ("Academic Term", term),
    ]
    if enrolled_on:
        pairs.append(("Date Enrolled", enrolled_on))

    rows = [
        [Paragraph(label, styles["label"]), Paragraph(value, styles["value"])]
        for label, value in pairs
    ]
    block = Table(rows, colWidths=[38 * mm, CONTENT_WIDTH - 38 * mm])
    block.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 1.6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                # A hairline under each value: the traditional form of a filled
                # document field, without the input box around it.
                ("LINEBELOW", (1, 0), (1, -1), 0.4, _RULE),
            ]
        )
    )
    return block


COURSE_COLUMNS = [
    ("Code", 20 * mm, TA_LEFT),
    ("Course / Subject", 42 * mm, TA_LEFT),
    ("Credits", 15 * mm, TA_CENTER),
    ("Time / Days", 30 * mm, TA_LEFT),
    ("Room", 18 * mm, TA_LEFT),
    ("Periods", 16 * mm, TA_CENTER),
    ("Lab Fee", 21 * mm, TA_RIGHT),
    ("Other Fee", 21 * mm, TA_RIGHT),
]


def _course_table(styles, com: ComSummary):
    """The enrolled subjects, with a totals row and repeating headers."""
    header = [Paragraph(name, styles["cell_bold"]) for name, _, _ in COURSE_COLUMNS]
    data = [header]
    for row in com.rows:
        data.append([
            Paragraph(row.code or "—", styles["cell"]),
            Paragraph(row.subject or "—", styles["cell"]),
            Paragraph(str(row.units), styles["cell"]),
            Paragraph(row.schedule or "—", styles["cell"]),
            Paragraph(row.room or "—", styles["cell"]),
            Paragraph(str(row.period) if row.period else "—", styles["cell"]),
            Paragraph(_money(row.lab_fee, styles), styles["cell"]),
            Paragraph(_money(row.other_fee, styles), styles["cell"]),
        ])

    data.append([
        Paragraph("TOTALS", styles["cell_bold"]),
        Paragraph(f"{com.subject_count} subjects", styles["cell_bold"]),
        Paragraph(str(com.total_units), styles["cell_bold"]),
        "", "",
        Paragraph(str(com.total_periods), styles["cell_bold"]),
        Paragraph(_money(com.total_lab_fee, styles), styles["cell_bold"]),
        Paragraph(_money(com.total_other_fee, styles), styles["cell_bold"]),
    ])

    table = Table(data, colWidths=[width for _, width, _ in COURSE_COLUMNS], repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), _TOTAL_FILL),
        ("LINEBELOW", (0, 0), (-1, 0), 0.9, _ACCENT_RULE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("BACKGROUND", (0, -1), (-1, -1), _TOTAL_FILL),
        ("LINEABOVE", (0, -1), (-1, -1), 0.7, _RULE),
        # Horizontal rules only: vertical lines between every column make a
        # financial table harder to read, not easier.
        ("LINEBELOW", (0, 1), (-1, -2), 0.35, _RULE),
    ]
    for index, (_, _, alignment) in enumerate(COURSE_COLUMNS):
        if alignment == TA_RIGHT:
            style.append(("ALIGN", (index, 1), (index, -1), "RIGHT"))
        elif alignment == TA_CENTER:
            style.append(("ALIGN", (index, 1), (index, -1), "CENTER"))
    if len(data) > 2:
        style.append(("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, _ZEBRA]))
    table.setStyle(TableStyle(style))
    return table


def _money_table(styles, heading: str, lines, total_label: str, total_value, widths, *,
                 with_status: bool = False, statuses=None, empty_message: str = ""):
    """One print-friendly financial table: a title above it, then rows, then a total.

    ``widths`` is passed in rather than derived here because the three cards share
    one page width: a card whose columns were sized from its own content would
    overflow its cell and, with ReportLab, overlap its neighbour.
    """
    headings = ["Item", "Amount (PHP)"] + (["Status"] if with_status else [])

    data = [
        [Paragraph(f"<b>{name}</b>", styles["cell_bold"]) for name in headings]
    ]
    states = list(statuses or [])

    if not lines:
        row = [Paragraph(empty_message or "None on file.", styles["small"]), ""]
        if with_status:
            row.append("")
        data.append(row)
    else:
        for index, (label, amount) in enumerate(lines):
            row = [
                Paragraph(label, styles["cell"]),
                Paragraph(_money(amount, styles), styles["cell"]),
            ]
            if with_status:
                row.append(
                    Paragraph(states[index] if index < len(states) else "—", styles["cell"])
                )
            data.append(row)

    total = [
        Paragraph(f"<b>{total_label}</b>", styles["cell_bold"]),
        Paragraph(f"<b>{_money(total_value, styles)}</b>", styles["cell_bold"]),
    ]
    if with_status:
        total.append(Paragraph("—", styles["cell_bold"]))
    data.append(total)

    table = Table(data, colWidths=list(widths))
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), _TOTAL_FILL),
        ("LINEBELOW", (0, 0), (-1, 0), 0.7, _RULE),
        ("BACKGROUND", (0, -1), (-1, -1), _TOTAL_FILL),
        ("LINEABOVE", (0, -1), (-1, -1), 0.7, _RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.4),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("ALIGN", (1, 1), (1, -1), "RIGHT"),
    ]
    if with_status:
        style.append(("ALIGN", (2, 1), (2, -1), "CENTER"))
    table.setStyle(TableStyle(style))

    title = Paragraph(heading, styles["card_heading"])
    return Table([[title], [table]], colWidths=[sum(widths)])


def _financial_band(styles, com_fees, financial: FinancialSummary):
    """The three financial tables side by side, sized to share the page width."""
    fees = [(line.label, line.amount) for line in com_fees.fees]
    discounts = [(line.label, line.amount) for line in com_fees.discounts]
    installments = [(row.label, row.amount) for row in financial.installments]
    statuses = [installment_state(row)[0] for row in financial.installments]

    # The cards are not equal thirds: C carries an extra status column and the
    # largest peso totals, and an equal split wraps "Down Payment" and
    # "₱28,122.82" onto two lines each. The widths are fixed fractions of the
    # content width rather than derived from each card's content, because
    # ReportLab silently overlaps a nested table that outgrows its cell.
    a_width = CONTENT_WIDTH * 0.30
    b_width = CONTENT_WIDTH * 0.26
    c_width = CONTENT_WIDTH * 0.44
    pair_widths = [a_width * 0.58, a_width * 0.42]
    pair_widths_b = [b_width * 0.58, b_width * 0.42]
    triple_widths = [c_width * 0.42, c_width * 0.32, c_width * 0.26]

    cards = [
        _money_table(
            styles, "A. ASSESSMENT BREAKDOWN", fees,
            "TOTAL ASSESSMENT", com_fees.fees_total, pair_widths,
            empty_message="No assessment on file.",
        ),
        _money_table(
            styles, "B. DISCOUNTS & CREDITS", discounts,
            "TOTAL DISCOUNTS", com_fees.discounts_total, pair_widths_b,
            empty_message="No discount or credit recorded.",
        ),
        _money_table(
            styles, "C. PAYMENT SCHEDULE", installments,
            "TOTAL DUE", financial.installments_total, triple_widths,
            with_status=True, statuses=statuses,
            empty_message="No payment schedule on file.",
        ),
    ]
    band = Table([cards], colWidths=[a_width, b_width, c_width])
    band.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return band


def _balance_table(styles, financial: FinancialSummary):
    """Assessed, discounts, payments and what remains -- stated separately.

    These four are the numbers a parent asks about, and keeping them out of the
    breakdown tables is what stops "total assessment" being read as "amount due".
    """
    rows = [
        ("Total Assessment", _money(financial.total_assessed, styles)),
        ("Total Discounts and Credits", f"-{_money(financial.total_discounts, styles)}"),
        ("Payments Received", f"-{_money(financial.total_payments, styles)}"),
        ("Outstanding Balance", _money(financial.outstanding_balance, styles)),
    ]
    data = [
        [
            Paragraph(f"<b>{label}</b>" if label == "Outstanding Balance" else label,
                      styles["cell_bold"] if label == "Outstanding Balance" else styles["cell"]),
            Paragraph(
                f"<b>{value}</b>" if label == "Outstanding Balance" else value,
                styles["cell_bold"] if label == "Outstanding Balance" else styles["cell"],
            ),
        ]
        for label, value in rows
    ]
    table = Table(data, colWidths=[60 * mm, 30 * mm], hAlign="RIGHT")
    table.setStyle(
        TableStyle(
            [
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 2.6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6),
                ("LINEABOVE", (0, -1), (-1, -1), 0.7, _RULE),
                ("BACKGROUND", (0, -1), (-1, -1), _TOTAL_FILL),
            ]
        )
    )
    return table


def _notice(styles, message: str) -> Table:
    """A restrained band for a caveat that must not be buried in the body."""
    table = Table([[Paragraph(message, styles["small"])]], colWidths=[CONTENT_WIDTH])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FEF3C7")),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#D97706")),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def _page_furniture(generated_at: datetime):
    """Footer drawn on every page: page count, origin, and what this copy is not."""
    label = (
        f"{SCHOOL_NAME} • {SYSTEM_NAME} • generated "
        f"{generated_at.strftime('%B %d, %Y %H:%M')}"
    )
    disclaimer = "Generated record. No signature or seal is applied to this copy."

    def draw(canvas, document):
        canvas.saveState()
        width, _ = A4
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(_MUTED)
        canvas.drawString(PAGE_MARGIN, 12 * mm, label)
        canvas.drawRightString(width - PAGE_MARGIN, 12 * mm, f"Page {canvas.getPageNumber()}")
        canvas.drawString(PAGE_MARGIN, 8.5 * mm, disclaimer)
        canvas.setStrokeColor(_RULE)
        canvas.setLineWidth(0.4)
        canvas.line(PAGE_MARGIN, 14.5 * mm, width - PAGE_MARGIN, 14.5 * mm)
        canvas.restoreState()

    return draw


def build_com_elements(
    student,
    enrollment,
    assessment=None,
    *,
    com: Optional[ComSummary] = None,
    financial: Optional[FinancialSummary] = None,
    generated_at: Optional[datetime] = None,
):
    """The document as a list of ReportLab flowables, for building or testing."""
    styles = _styles()
    generated_at = generated_at or datetime.now()
    com = com if com is not None else summarize_enrollment(enrollment)
    financial = financial if financial is not None else summarize_financials(assessment)
    fee_lines = summarize_fee_lines(assessment)

    elements = [
        _institutional_header(styles, student),
        Spacer(1, 6 * mm),
        Paragraph(DOCUMENT_TITLE, styles["title"]),
        Spacer(1, 1.5 * mm),
    ]

    if not com.available:
        elements.append(
            _notice(
                styles,
                com.empty_reason
                or "This student has no enrollment record, so there is nothing to certify.",
            )
        )
        return elements

    elements.append(_profile_block(styles, student, com, enrollment))
    elements.append(Spacer(1, 4 * mm))

    elements.append(Paragraph("CERTIFICATE OF MATRICULATION — ENROLLED COURSES", styles["section"]))
    elements.append(Spacer(1, 2 * mm))
    elements.append(_course_table(styles, com))
    elements.append(Spacer(1, 5 * mm))

    elements.append(Paragraph("FINANCIAL ASSESSMENT", styles["section"]))
    elements.append(Spacer(1, 2 * mm))
    elements.append(_financial_band(styles, fee_lines, financial))

    # The notice sits with the figures it is about, directly above the balance
    # summary: pushed to the end of the flow it orphaned onto its own second
    # page, where nobody reads it.
    notices = []
    if financial.available and financial.discrepancies:
        notices.append(
            "Stored totals do not agree with each other: "
            + " ".join(financial.discrepancies)
        )
    else:
        from services.com_print_service import official_status

        status = official_status(com, financial)
        if not status.is_official:
            notices.append(status.reason)

    if notices:
        elements.append(Spacer(1, 3 * mm))
        for notice in notices:
            elements.append(_notice(styles, notice))
    elements.append(Spacer(1, 3 * mm))
    elements.append(_balance_table(styles, financial))

    return elements


def build_com_pdf(
    student,
    enrollment,
    assessment=None,
    output_path: Optional[Path] = None,
    *,
    generated_at: Optional[datetime] = None,
) -> Path:
    """Write the Certificate of Matriculation to ``output_path``."""
    generated_at = generated_at or datetime.now()
    if output_path is None:
        reference = "".join(
            c for c in (student.reference_number or "student") if c.isalnum()
        )
        output_path = settings.REPORTS_OUTPUT_DIR / (
            f"certificate_of_matriculation_{reference}_{generated_at:%Y%m%d}.pdf"
        )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=PAGE_MARGIN,
        leftMargin=PAGE_MARGIN,
        topMargin=PAGE_MARGIN,
        bottomMargin=18 * mm,
        title=f"{DOCUMENT_TITLE} - {student.full_name}",
        author=SCHOOL_NAME,
        subject=f"{SYSTEM_NAME} - {student.reference_number}",
    )
    doc.build(
        build_com_elements(
            student, enrollment, assessment, generated_at=generated_at
        ),
        onFirstPage=_page_furniture(generated_at),
        onLaterPages=_page_furniture(generated_at),
    )
    logger.info("Certificate of Matriculation written to %s", output_path)
    return output_path


__all__ = [
    "CONTENT_WIDTH",
    "DOCUMENT_TITLE",
    "SCHOOL_NAME",
    "SYSTEM_NAME",
    "build_com_elements",
    "build_com_pdf",
]
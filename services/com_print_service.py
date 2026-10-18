"""Print and PDF export for the Certificate of Matriculation shown after a scan.

Two output routes, one document:

* :meth:`ComPrintService.print_com` renders a purpose-built, print-only
  document and hands it to the system print dialog. It contains no scanner
  controls, no sidebar and no navigation -- only the verified student, the
  enrolled subjects and the fee summary drawn from the database.
* :meth:`ComPrintService.export_pdf` reuses the project's existing
  :class:`~services.report_service.ReportService` so the file format, fonts and
  output directory stay identical to the Certificate of Matriculation page.

Neither route will invent content. With no enrollment record there is nothing
to certify, and the export is refused; and a document is only labelled
"official" when an enrollment, a finalized assessment and internally consistent
totals are all present.
"""

from __future__ import annotations

import html
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from config.settings import settings
from services.com_summary import (
    ComSummary,
    FinancialSummary,
    FeeLines,
    MoneyLine,
    format_peso,
    summarize_enrollment,
    to_decimal,
)

logger = logging.getLogger(__name__)

#: Assessment states that indicate the fees were reviewed and approved.
OFFICIAL_ASSESSMENT_STATUSES = ("finalized", "paid", "partial")


def _fallback_fee_lines(financial: FinancialSummary) -> FeeLines:
    """Name the fees from the summary when no assessment object is available.

    The print route can be called with only a :class:`FinancialSummary`, which
    carries three named figures rather than the assessment's line items. Those
    three are shown as three lines -- the grouped breakdown simply cannot be
    reconstructed from a summary, and inventing it would be a guess.
    """
    if not financial.available:
        return FeeLines(available=False, empty_reason=financial.empty_reason)
    fees = [
        MoneyLine(label="Tuition Fee", amount=to_decimal(financial.tuition_fee)),
        MoneyLine(label="Miscellaneous Fee", amount=to_decimal(financial.other_fees)),
        MoneyLine(label="Lab Fee", amount=to_decimal(financial.laboratory_fee)),
    ]
    return FeeLines(fees=fees, available=True)


def certifiable(com: ComSummary) -> Optional[str]:
    """Why this record cannot be certified, or ``None`` when it can be.

    The distinction that matters is between *no enrollment record* and *an
    enrollment record with nothing enrolled in it*: the first is a registrar
    problem, the second is a term boundary, and telling an operator the wrong one
    sends them looking for a record that is already there.

    ``ComSummary.empty_reason`` carries exactly that wording, so the same text is
    used here, in the popup and in the document itself.
    """
    if com.available:
        return None
    return com.empty_reason or (
        "This student has no enrollment record, so no Certificate of Matriculation "
        "can be produced."
    )


class ComNotCertifiableError(RuntimeError):
    """Raised when there is not enough authoritative data to print a COM."""


def official_status(com: ComSummary, financial: FinancialSummary) -> "OfficialStatus":
    """Decide whether the printed copy may be called an official COM.

    Every requirement is checked explicitly and the missing ones are named, so
    the operator is told what to fix rather than receiving a certificate that
    looks authoritative and rests on nothing.
    """
    missing: list[str] = []

    if not com.available:
        missing.append("no enrollment record with enrolled subjects")
    if not financial.available:
        missing.append("no assessment record")
    elif financial.status not in OFFICIAL_ASSESSMENT_STATUSES:
        missing.append(f"assessment status is '{financial.status or 'unknown'}'")
    if financial.discrepancies:
        missing.append(f"{len(financial.discrepancies)} unresolved total mismatch(es)")

    return OfficialStatus(
        is_official=not missing,
        missing_requirements=missing,
    )


class OfficialStatus:
    """Result of :func:`official_status`."""

    def __init__(self, is_official: bool, missing_requirements: list[str]):
        self.is_official = is_official
        self.missing_requirements = list(missing_requirements)

    @property
    def banner(self) -> str:
        if self.is_official:
            return "OFFICIAL CERTIFICATE OF MATRICULATION"
        return "UNOFFICIAL PREVIEW - NOT VALID FOR OFFICIAL USE"

    @property
    def reason(self) -> str:
        if self.is_official:
            return ""
        listed = "; ".join(self.missing_requirements)
        return (
            "This copy is not an official Certificate of Matriculation because "
            f"the following are missing or unverified: {listed}."
        )

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"OfficialStatus(is_official={self.is_official}, missing={self.missing_requirements})"


class ComPrintService:
    """Renders and exports the COM for one verified student."""

    def __init__(self, report_service=None):
        if report_service is None:
            from services.report_service import ReportService

            report_service = ReportService()
        self.report_service = report_service

    # -- print -----------------------------------------------------------

    def build_document(self, student, com: ComSummary, financial: FinancialSummary,
                       fee_lines: Optional["FeeLines"] = None):
        """Return a :class:`QTextDocument` laid out for A4 printing."""
        from PySide6.QtGui import QTextDocument

        status = official_status(com, financial)
        document = QTextDocument()
        document.setHtml(
            self._build_html(student, com, financial, status, fee_lines=fee_lines)
        )
        return document

    def print_com(self, student, com: ComSummary, financial: FinancialSummary,
                  parent=None, fee_lines: Optional["FeeLines"] = None) -> bool:
        """Open the system print dialog for the COM. Returns False if cancelled.

        Raises
        ------
        ComNotCertifiableError
            When there is nothing to certify, with the reason the record gives.
        """
        problem = certifiable(com)
        if problem:
            raise ComNotCertifiableError(problem)

        from PySide6.QtPrintSupport import QPrintDialog, QPrinter

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setDocName(f"COM-{student.reference_number}")

        dialog = QPrintDialog(printer, parent)
        dialog.setWindowTitle("Print Certificate of Matriculation")
        if dialog.exec() != QPrintDialog.DialogCode.Accepted:
            return False

        document = self.build_document(student, com, financial, fee_lines=fee_lines)
        document.print_(printer)
        logger.info(
            "COM sent to printer for reference %s", student.reference_number
        )
        return True

    # -- pdf -------------------------------------------------------------

    def export_pdf(
        self,
        student,
        enrollment,
        assessment=None,
        output_path: Optional[Path] = None,
    ) -> Optional[Path]:
        """Write the COM as a PDF using the project's existing report service.

        Certification is decided on the *summary*, not on ``enrollment is None``:
        an enrollment row with nothing enrolled in it produces a document that
        certifies nothing, and returning it would report a success for a document
        that says the student has no subjects. The message comes from the summary
        so it names the real reason -- no enrollment record, or a term with no
        enrolled subjects -- instead of the first whenever either applies.
        """
        problem = certifiable(summarize_enrollment(enrollment))
        if problem:
            raise ComNotCertifiableError(
                f"{problem} No Certificate of Matriculation can be exported."
            )
        return self.report_service.generate_certificate_of_matriculation(
            student, enrollment, assessment, output_path
        )

    def default_output_path(self, student) -> Path:
        from config.settings import settings

        safe_ref = "".join(c for c in (student.reference_number or "student") if c.isalnum())
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return settings.REPORTS_OUTPUT_DIR / f"certificate_{safe_ref}_{stamp}.pdf"

    # -- markup ----------------------------------------------------------

    def _build_html(self, student, com: ComSummary, financial: FinancialSummary,
                    status: OfficialStatus, fee_lines: Optional["FeeLines"] = None) -> str:
        """The printed document.

        This is document formatting, not interface formatting: student details
        are static label/value text with a rule under each value, never an input
        field or a pill, and the three financial blocks are restrained tables
        rather than screen cards. The figures come from the same
        :mod:`services.com_summary` objects the dashboard renders, so the page
        and the printout cannot disagree about a total.

        ``fee_lines`` supplies the per-category breakdown when the caller has the
        assessment to hand. Without it the assessment block falls back to the
        named figures the summary already carries.
        """
        import base64

        from styles.colors import Colors
        from services.com_summary import (
            enrollment_status_label,
            installment_state,
            program_and_year,
            summarize_fee_lines,
        )

        def asset_data_uri(path) -> str:
            if not path:
                return ""
            resolved = settings.resolve_asset(str(path))
            if resolved is None:
                logger.warning("Print document image missing: %s", path)
                return ""
            try:
                payload = base64.b64encode(Path(resolved).read_bytes()).decode("ascii")
            except OSError as exc:
                logger.warning("Print document image unreadable (%s): %s", resolved, exc)
                return ""
            return f"data:image/png;base64,{payload}"

        # -- enrolled courses, with a totals row computed from the rows above --
        rows = []
        for row in com.rows:
            rows.append(
                "<tr>"
                f"<td class='code'>{html.escape(row.code)}</td>"
                f"<td>{html.escape(row.subject)}</td>"
                f"<td class='ctr'>{row.units}</td>"
                f"<td>{html.escape(row.schedule or '—')}</td>"
                f"<td>{html.escape(row.room or '—')}</td>"
                f"<td class='ctr'>{row.period or '—'}</td>"
                f"<td class='num'>{format_peso(row.lab_fee)}</td>"
                f"<td class='num'>{format_peso(row.other_fee)}</td>"
                "</tr>"
            )
        if rows:
            rows.append(
                "<tr class='total'>"
                f"<td>TOTALS</td>"
                f"<td>{com.subject_count} subjects</td>"
                f"<td class='ctr'>{com.total_units}</td>"
                "<td></td><td></td>"
                f"<td class='ctr'>{com.total_periods}</td>"
                f"<td class='num'>{format_peso(com.total_lab_fee)}</td>"
                f"<td class='num'>{format_peso(com.total_other_fee)}</td>"
                "</tr>"
            )

        # -- financial blocks -------------------------------------------------
        lines = fee_lines if fee_lines is not None else _fallback_fee_lines(financial)
        fee_rows_html = "".join(
            f"<tr><td>{html.escape(line.label)}</td>"
            f"<td class='num'>{format_peso(line.amount)}</td></tr>"
            for line in lines.fees
        ) or f"<tr><td class='muted'>{html.escape(lines.empty_reason or 'No assessment on file.')}</td><td class='num'>—</td></tr>"
        discount_rows_html = "".join(
            f"<tr><td>{html.escape(line.label)}</td>"
            f"<td class='num'>{format_peso(line.amount)}</td></tr>"
            for line in lines.discounts
        ) or "<tr><td class='muted'>No discount or credit recorded.</td><td class='num'>—</td></tr>"

        installment_rows_html = "".join(
            "<tr>"
            f"<td>{html.escape(item.label)}</td>"
            f"<td class='num'>{format_peso(item.amount)}</td>"
            f"<td class='ctr'>{html.escape(installment_state(item)[0])}</td>"
            "</tr>"
            for item in financial.installments
        ) or "<tr><td class='muted'>No payment schedule on file.</td><td class='num'>—</td><td class='ctr'>—</td></tr>"

        discrepancies = "".join(
            f"<li>{html.escape(note)}</li>" for note in financial.discrepancies
        )
        warning_block = ""
        if not status.is_official:
            warning_block = (
                f"<div class='warn'><strong>{html.escape(status.banner)}</strong>"
                f"<p>{html.escape(status.reason)}</p></div>"
            )
        if discrepancies:
            warning_block += (
                "<div class='warn'><strong>DATA DISCREPANCIES</strong>"
                f"<ul>{discrepancies}</ul></div>"
            )

        period = " • ".join(p for p in (com.academic_year, com.semester) if p) or "Not recorded"
        logo = asset_data_uri(settings.LOGO_PATH)
        photo = asset_data_uri(getattr(student, "profile_image_path", None))

        logo_cell = (
            f"<img class='logo' src='{logo}' alt='WIT logo'>" if logo else ""
        )
        photo_cell = (
            f"<img class='photo' src='{photo}' alt='Photograph of {html.escape(student.full_name or '')}'>"
            if photo
            else "<div class='photo empty'></div>"
        )

        return f"""
        <html><head><style>
          body {{ font-family: 'DejaVu Sans', Arial, sans-serif; color: {Colors.TEXT_PRIMARY};
                 font-size: 9.5pt; }}
          /* Institutional header: logo and school name on the left, the
             student's photograph in its own area on the right. */
          .masthead {{ display: table; width: 100%; border-bottom: 2px solid {Colors.ORANGE};
                       padding-bottom: 6px; margin-bottom: 14px; }}
          .masthead .brand {{ display: table-cell; vertical-align: middle; }}
          .masthead .photo-cell {{ display: table-cell; vertical-align: middle; text-align: right; width: 92px; }}
          .logo {{ width: 46px; height: auto; }}
          .photo {{ width: 78px; height: 78px; object-fit: contain; }}
          .photo.empty {{ width: 78px; height: 78px; border: 1px solid {Colors.BORDER}; }}
          .school {{ font-size: 14pt; font-weight: bold; color: {Colors.TEXT_PRIMARY}; }}
          .system {{ font-size: 9pt; color: {Colors.TEXT_SECONDARY}; }}
          h1 {{ font-size: 12pt; text-align: center; color: {Colors.DARK_BURNT_ORANGE};
                margin: 0 0 12px 0; letter-spacing: 1px; }}
          h2 {{ font-size: 10pt; color: {Colors.DARK_BURNT_ORANGE}; margin: 16px 0 6px 0; }}
          /* Static label/value document fields: a rule under the value, no box,
             no rounded container, nothing that looks editable. */
          table.profile {{ width: 100%; border-collapse: collapse; }}
          table.profile td {{ padding: 3px 0; vertical-align: top; }}
          table.profile td.k {{ width: 130px; font-weight: bold; color: {Colors.TEXT_SECONDARY}; }}
          table.profile td.v {{ border-bottom: 1px solid {Colors.BORDER_STRONG}; }}
          table.grid {{ width: 100%; border-collapse: collapse; }}
          th {{ background: {Colors.SURFACE_MUTED}; color: {Colors.TEXT_PRIMARY}; text-align: left;
                padding: 5px 6px; font-size: 8.5pt; border-bottom: 2px solid {Colors.ORANGE}; }}
          td {{ border-bottom: 1px solid {Colors.BORDER}; padding: 4px 6px; font-size: 8.5pt;
                vertical-align: top; }}
          td.num, th.num {{ text-align: right; }}
          td.ctr, th.ctr {{ text-align: center; }}
          td.code {{ font-family: 'DejaVu Sans Mono', monospace; }}
          td.muted {{ color: {Colors.TEXT_SECONDARY}; }}
          tr.total td {{ background: {Colors.SURFACE_MUTED}; font-weight: bold;
                         border-top: 1px solid {Colors.BORDER_STRONG}; }}
          /* Three financial tables side by side, kept as tables: a printed
             document does not need card chrome to be readable. */
          table.band {{ width: 100%; border-collapse: separate; border-spacing: 12px 0; }}
          table.band td {{ border: none; padding: 0 0 0 0; vertical-align: top; width: 33%; }}
          table.band table {{ width: 100%; border-collapse: collapse; }}
          table.band td.card-head {{ font-size: 8.5pt; font-weight: bold;
                                     color: {Colors.DARK_BURNT_ORANGE};
                                     border-bottom: 1px solid {Colors.BORDER_STRONG}; padding-bottom: 4px; }}
          table.band th {{ font-size: 8pt; }}
          table.band td {{ font-size: 8pt; padding: 3px 4px; }}
          table.bal {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
          table.bal td {{ padding: 3px 6px; }}
          table.bal td.k {{ text-align: left; }}
          table.bal td.v {{ text-align: right; }}
          table.bal tr.emphasis td {{ font-weight: bold; background: {Colors.SURFACE_MUTED};
                                      border-top: 1px solid {Colors.BORDER_STRONG}; }}
          .warn {{ border: 1px solid {Colors.WARNING_ORANGE}; background: {Colors.WARNING_BG};
                    padding: 6px 8px; margin: 8px 0; font-size: 8.5pt; }}
          .foot {{ color: {Colors.TEXT_SECONDARY}; font-size: 7.5pt; margin-top: 14px;
                   border-top: 1px solid {Colors.BORDER}; padding-top: 6px; }}
        </style></head><body>
          <div class='masthead'>
            <div class='brand'>
              {logo_cell}
              <div class='school'>WESTERN INSTITUTE OF TECHNOLOGY</div>
              <div class='system'>Student Information &amp; Matriculation System</div>
            </div>
            <div class='photo-cell'>{photo_cell}</div>
          </div>

          <h1>CERTIFICATE OF MATRICULATION</h1>

          <h2>STUDENT PROFILE INFORMATION</h2>
          <table class='profile'>
            <tr><td class='k'>Student Full Name</td><td class='v'>{html.escape(student.full_name or '—')}</td></tr>
            <tr><td class='k'>Program &amp; Year</td><td class='v'>{html.escape(program_and_year(student) or '—')}</td></tr>
            <tr><td class='k'>Reference Number</td><td class='v'>{html.escape(student.reference_number or '—')}</td></tr>
            <tr><td class='k'>Student ID</td><td class='v'>{html.escape(student.student_id or '—')}</td></tr>
            <tr><td class='k'>Enrollment Status</td><td class='v'>{html.escape(enrollment_status_label(getattr(student, 'enrollment_status', '')))}</td></tr>
            <tr><td class='k'>Academic Term</td><td class='v'>{html.escape(period)}</td></tr>
            <tr><td class='k'>Barcode Verified</td><td class='v'>{html.escape(student.barcode_payload or '—')}</td></tr>
          </table>

          {warning_block}

          <h2>CERTIFICATE OF MATRICULATION — ENROLLED COURSES</h2>
          <table class='grid'>
            <tr><th>Code</th><th>Course / Subject</th><th class='ctr'>Credits</th>
                <th>Time / Days</th><th>Room</th><th class='ctr'>Periods</th>
                <th class='num'>Lab Fee</th><th class='num'>Other Fee</th></tr>
            {''.join(rows)}
          </table>

          <h2>FINANCIAL ASSESSMENT</h2>
          <table class='band'><tr>
            <td>
              <div class='card-head'>A. ASSESSMENT BREAKDOWN</div>
              <table>
                <tr><th>Fee Description</th><th class='num'>Amount (PHP)</th></tr>
                {fee_rows_html}
                <tr class='total'><td>TOTAL ASSESSMENT</td><td class='num'>{format_peso(lines.fees_total)}</td></tr>
              </table>
            </td>
            <td>
              <div class='card-head'>B. DISCOUNTS &amp; CREDITS</div>
              <table>
                <tr><th>Item</th><th class='num'>Amount (PHP)</th></tr>
                {discount_rows_html}
                <tr class='total'><td>TOTAL DISCOUNTS</td><td class='num'>{format_peso(lines.discounts_total)}</td></tr>
              </table>
            </td>
            <td>
              <div class='card-head'>C. PAYMENT SCHEDULE</div>
              <table>
                <tr><th>Installment</th><th class='num'>Amount</th><th class='ctr'>Status</th></tr>
                {installment_rows_html}
                <tr class='total'><td>TOTAL DUE</td><td class='num'>{format_peso(financial.installments_total)}</td><td class='ctr'>—</td></tr>
              </table>
            </td>
          </tr></table>

          <table class='bal'>
            <tr><td class='k'>Total Assessment</td><td class='v'>{format_peso(financial.total_assessed)}</td></tr>
            <tr><td class='k'>Total Discounts and Credits</td><td class='v'>-{format_peso(financial.total_discounts)}</td></tr>
            <tr><td class='k'>Payments Received</td><td class='v'>-{format_peso(financial.total_payments)}</td></tr>
            <tr class='emphasis'><td class='k'>Outstanding Balance</td><td class='v'>{format_peso(financial.outstanding_balance)}</td></tr>
          </table>

          <p class='foot'>Generated {datetime.now().strftime('%B %d, %Y %H:%M')} from the
          student information system. This is a generated record: no signature or
          seal is applied to it.</p>
        </body></html>
        """


def printable_reference(student) -> str:
    """Reference number reduced to characters that are safe in a filename."""
    return "".join(c for c in (student.reference_number or "student") if c.isalnum())


def today_text(value: Optional[date] = None) -> str:
    return (value or date.today()).strftime("%B %d, %Y")


def unpaid_installment_total(financial: FinancialSummary):
    """Sum of schedule amounts still outstanding. Used by the completion report."""
    return sum(
        (row.amount - row.amount_paid for row in financial.installments),
        to_decimal(0),
    )
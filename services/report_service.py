import csv
from typing import Optional, List, Dict, Any
from decimal import Decimal
from datetime import date, datetime
from pathlib import Path
import logging
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib import colors
from reportlab.lib.units import mm, inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from config.settings import settings
from models.student import Student
from models.enrollment import Enrollment
from models.assessment import Assessment
from models.barcode import Barcode

logger = logging.getLogger(__name__)


def _enum_text(value) -> str:
    """Readable text for an enum member, a plain string, or nothing."""
    if value is None:
        return ""
    return str(getattr(value, "value", value))


class ReportService:
    def __init__(self):
        self.output_dir = settings.REPORTS_OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_certificate_of_matriculation(self, student: Student, enrollment: Enrollment, assessment: Assessment = None, output_path: Path = None) -> Optional[Path]:
        """Write the Certificate of Matriculation as a formal document.

        The layout lives in :mod:`services.com_document`: institutional header
        with the logo and the student's photograph, static label/value details,
        an enrolled-courses table with repeating headers and a totals row, three
        financial tables and a footer. This method only owns the file naming, the
        error contract and the log line, so every caller gets the same document.
        """
        try:
            from services.com_document import build_com_pdf

            return build_com_pdf(student, enrollment, assessment, output_path)
        except Exception as e:
            logger.error(f"Failed to generate certificate of matriculation: {e}")
            return None

    def export_students_csv(self, students: List[Student], output_path: Path) -> Optional[Path]:
        """Write the student list to a CSV file.

        The Students page's "Export Report" button is a real export rather than a
        placeholder message: a registrar filtering to "on leave" and pressing it
        gets exactly the rows on screen, in the same column order, and nothing
        else. CSV (not PDF) because the output is a working list that gets sorted
        and filtered again in a spreadsheet.
        """
        try:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    [
                        "Student ID",
                        "Reference No.",
                        "First Name",
                        "Middle Name",
                        "Last Name",
                        "Program",
                        "Year Level",
                        "Section",
                        "Status",
                        "Date Enrolled",
                    ]
                )
                for student in students:
                    writer.writerow(
                        [
                            student.student_id,
                            student.reference_number,
                            student.first_name,
                            student.middle_name or "",
                            student.last_name,
                            student.program,
                            getattr(student.year_level, "value", student.year_level),
                            student.section or "",
                            getattr(student.enrollment_status, "value", student.enrollment_status),
                            student.date_enrolled.isoformat() if student.date_enrolled else "",
                        ]
                    )
            logger.info("Exported %d student row(s) to %s", len(students), output_path)
            return output_path
        except OSError as exc:
            # Permission denied, read-only mount, full disk: reported as a write
            # failure rather than as "the export produced nothing".
            logger.error("Could not write the student export to %s: %s", output_path, exc)
            return None

    def export_scan_log_csv(self, scans, output_path: Path) -> Optional[Path]:
        """Write the barcode scan log to a CSV file."""
        try:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    ["Scanned At", "Scanned Value", "Student ID", "Student Name",
                     "Reference No.", "Status", "Confidence", "Camera"]
                )
                for scan in scans:
                    confidence = (
                        f"{float(scan.confidence_score):.4f}"
                        if getattr(scan, "confidence_score", None) is not None
                        else ""
                    )
                    writer.writerow(
                        [
                            scan.scanned_at.isoformat(sep=" ") if scan.scanned_at else "",
                            scan.scanned_value,
                            getattr(scan, "student_code", "") or "",
                            getattr(scan, "student_name", "") or "",
                            getattr(scan, "student_reference", "") or "",
                            getattr(scan.scan_status, "value", scan.scan_status),
                            confidence,
                            "" if scan.camera_index is None else scan.camera_index,
                        ]
                    )
            logger.info("Exported %d scan row(s) to %s", len(scans), output_path)
            return output_path
        except OSError as exc:
            logger.error("Could not write the scan log export to %s: %s", output_path, exc)
            return None

    def generate_assessment_report(self, student: Student, assessment, output_path: Path = None,
                                   payments=None) -> Optional[Path]:
        """Write a student's assessment as a PDF: charges, discounts, totals,
        the installment schedule and the payments recorded against it.

        Every figure is read from the assessment record. A student with no
        assessment is refused by the caller rather than printed here as a
        document of zeroes, and a missing schedule or missing payments produces
        a line that says so instead of an invented total.
        """
        try:
            if assessment is None:
                logger.error("Assessment report requested without an assessment record")
                return None

            if output_path is None:
                output_path = self.output_dir / (
                    f"assessment_{student.reference_number}_"
                    f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
                )

            from services.com_summary import format_peso

            doc = SimpleDocTemplate(
                str(output_path), pagesize=A4,
                rightMargin=18 * mm, leftMargin=18 * mm,
                topMargin=18 * mm, bottomMargin=18 * mm,
            )
            styles = getSampleStyleSheet()
            heading = ParagraphStyle(
                "H", parent=styles["Heading1"], fontSize=15,
                alignment=TA_CENTER, textColor=colors.HexColor("#AD4D05"),
            )
            sub = ParagraphStyle(
                "S", parent=styles["Heading2"], fontSize=12,
                alignment=TA_CENTER, textColor=colors.HexColor("#EE6B05"),
            )
            body = ParagraphStyle("B", parent=styles["Normal"], fontSize=9)

            elements = [
                Paragraph("WESTERN INSTITUTE OF TECHNOLOGY", heading),
                Paragraph("Assessment &amp; Fee Statement", sub),
                Spacer(1, 14),
            ]

            info = [
                ["Student:", student.full_name],
                ["Student ID:", student.student_id],
                ["Reference No.:", student.reference_number],
                ["Program:", student.program],
                ["Academic Year:", getattr(assessment, "academic_year", "") or "Not recorded"],
                ["Semester:", _enum_text(getattr(assessment, "semester", "")) or "Not recorded"],
                ["Assessment Date:",
                 assessment.assessment_date.strftime("%B %d, %Y")
                 if getattr(assessment, "assessment_date", None) else "Not recorded"],
            ]
            info_table = Table(info, colWidths=[40 * mm, 130 * mm])
            info_table.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#333333")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
            ]))
            elements.extend([info_table, Spacer(1, 12)])

            elements.append(Paragraph("Assessment Details", body))
            elements.append(Spacer(1, 6))

            # A discount is an item whose *category* is a discount; it is not a
            # separate amount field, so the split is read from the category
            # rather than assumed.
            rows = [["Category / Description", "Fee", "Discount", "Net"]]
            total_fee = Decimal("0")
            total_discount = Decimal("0")
            for item in getattr(assessment, "items", []) or []:
                category = getattr(item, "category", None)
                category_name = (
                    getattr(category, "category_name", None)
                    or getattr(category, "category_code", None)
                    or "Assessment item"
                )
                amount = Decimal(str(item.amount or 0))
                is_discount = bool(getattr(category, "is_discount", False))
                fee = Decimal("0") if is_discount else amount
                discount = amount if is_discount else Decimal("0")
                total_fee += fee
                total_discount += discount
                rows.append([
                    f"{category_name} — {item.description}" if item.description else str(category_name),
                    format_peso(fee) if fee else "—",
                    format_peso(discount) if discount else "—",
                    format_peso(fee - discount),
                ])
            rows.append([
                "TOTALS",
                format_peso(total_fee),
                format_peso(total_discount),
                format_peso(total_fee - total_discount),
            ])

            detail = Table(rows, colWidths=[90 * mm, 27 * mm, 27 * mm, 27 * mm], repeatRows=1)
            detail.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#FFF7ED")),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#FFEDD5")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCA18D")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]))
            elements.extend([detail, Spacer(1, 12)])

            elements.append(Paragraph("Payment Schedule", body))
            elements.append(Spacer(1, 6))
            schedule_rows = [["Due Date", "Amount Due", "Amount Paid", "Status"]]
            schedules = getattr(assessment, "payment_schedules", None) or []
            for schedule in schedules:
                schedule_rows.append([
                    schedule.due_date.strftime("%b %d, %Y") if schedule.due_date else "Not recorded",
                    format_peso(Decimal(str(schedule.amount_due or 0))),
                    format_peso(Decimal(str(schedule.amount_paid or 0))),
                    _enum_text(getattr(schedule, "status", "")) or "Not recorded",
                ])
            if len(schedule_rows) == 1:
                schedule_rows.append(["No payment schedule on file", "—", "—", "—"])
            schedule_table = Table(
                schedule_rows, colWidths=[45 * mm, 40 * mm, 40 * mm, 45 * mm], repeatRows=1
            )
            schedule_table.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#FFF7ED")),
                ("ALIGN", (1, 1), (2, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCA18D")),
            ]))
            elements.extend([schedule_table, Spacer(1, 12)])

            elements.append(Paragraph("Payments Recorded", body))
            elements.append(Spacer(1, 6))
            payment_rows = [["Date", "Amount", "Method", "Reference"]]
            for payment in payments or []:
                payment_rows.append([
                    payment.payment_date.strftime("%b %d, %Y") if payment.payment_date else "Not recorded",
                    format_peso(Decimal(str(payment.amount or 0))),
                    _enum_text(getattr(payment, "payment_method", "")) or "Not recorded",
                    payment.reference_number or "—",
                ])
            if len(payment_rows) == 1:
                payment_rows.append(["No payments recorded", "—", "—", "—"])
            payment_table = Table(
                payment_rows, colWidths=[40 * mm, 40 * mm, 40 * mm, 50 * mm], repeatRows=1
            )
            payment_table.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#FFF7ED")),
                ("ALIGN", (1, 1), (1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCA18D")),
            ]))
            elements.append(payment_table)

            doc.build(elements)
            logger.info("Generated assessment report: %s", output_path)
            return output_path
        except Exception as e:
            logger.error(f"Failed to generate assessment report: {e}")
            return None

    def generate_student_barcode_report(self, student: Student, barcode: Barcode, output_path: Path = None) -> Optional[Path]:
        try:
            if output_path is None:
                output_path = self.output_dir / f"barcode_report_{student.reference_number}_{datetime.now().strftime('%Y%m%d')}.pdf"

            doc = SimpleDocTemplate(str(output_path), pagesize=A4, rightMargin=20*mm, leftMargin=20*mm, topMargin=20*mm, bottomMargin=20*mm)
            elements = []
            styles = getSampleStyleSheet()

            title_style = ParagraphStyle("Title", parent=styles["Heading1"], fontSize=16, alignment=TA_CENTER, textColor=colors.HexColor("#AD4D05"))

            elements.append(Paragraph("WESTERN INSTITUTE OF TECHNOLOGY", title_style))
            elements.append(Paragraph("Student Barcode Report", ParagraphStyle("Sub", parent=styles["Heading2"], fontSize=13, alignment=TA_CENTER, textColor=colors.HexColor("#EE6B05"))))
            elements.append(Spacer(1, 20))

            data = [
                ["Student Name:", student.full_name],
                ["Student ID:", student.student_id],
                ["Reference Number:", student.reference_number],
                ["Program:", student.program],
                ["Year Level:", student.year_level.value if hasattr(student.year_level, 'value') else str(student.year_level)],
                ["Barcode Value:", barcode.barcode_value],
                ["Barcode Type:", barcode.barcode_type.value if hasattr(barcode.barcode_type, 'value') else str(barcode.barcode_type)],
                ["Generated Date:", barcode.created_at.strftime("%B %d, %Y %H:%M") if barcode.created_at else "N/A"],
            ]

            table = Table(data, colWidths=[50*mm, 100*mm])
            table.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#333333")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCA18D")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFFEF7")),
            ]))
            elements.append(table)
            elements.append(Spacer(1, 20))

            if barcode.image_path:
                # Project-root resolution: a stored relative path must not depend
                # on the directory the application was launched from.
                img_path = settings.resolve_asset(barcode.image_path)
                if img_path is not None:
                    elements.append(Paragraph("Barcode Image:", ParagraphStyle("Label", parent=styles["Normal"], fontSize=10, textColor=colors.HexColor("#AD4D05"))))
                    elements.append(Spacer(1, 8))
                    img = Image(str(img_path), width=120*mm, height=40*mm)
                    elements.append(img)

            doc.build(elements)
            logger.info(f"Generated barcode report: {output_path}")
            return output_path

        except Exception as e:
            logger.error(f"Failed to generate barcode report: {e}")
            return None

    def generate_enrollment_summary_report(self, students: List[Student], academic_year: str, semester: str, output_path: Path = None) -> Optional[Path]:
        try:
            if output_path is None:
                output_path = self.output_dir / f"enrollment_summary_{academic_year}_{semester}_{datetime.now().strftime('%Y%m%d')}.pdf"

            doc = SimpleDocTemplate(str(output_path), pagesize=A4, rightMargin=15*mm, leftMargin=15*mm, topMargin=15*mm, bottomMargin=15*mm)
            elements = []
            styles = getSampleStyleSheet()

            elements.append(Paragraph("WESTERN INSTITUTE OF TECHNOLOGY", ParagraphStyle("T", parent=styles["Heading1"], fontSize=16, alignment=TA_CENTER, textColor=colors.HexColor("#AD4D05"))))
            elements.append(Paragraph(f"Enrollment Summary - {academic_year} {semester}", ParagraphStyle("S", parent=styles["Heading2"], fontSize=13, alignment=TA_CENTER, textColor=colors.HexColor("#EE6B05"))))
            elements.append(Spacer(1, 12))

            headers = ["Student ID", "Reference No.", "Name", "Program", "Year Level", "Status", "Total Credits"]
            data = [headers]

            for student in students:
                enrollments = [e for e in getattr(student, 'enrollments', []) 
                              if e.academic_year == academic_year and str(e.semester.value if hasattr(e.semester, 'value') else e.semester) == semester]
                total_credits = sum(e.total_credits for e in enrollments)
                status = enrollments[0].status.value if enrollments and hasattr(enrollments[0].status, 'value') else "N/A"
                
                data.append([
                    student.student_id,
                    student.reference_number,
                    student.full_name,
                    student.program,
                    student.year_level.value if hasattr(student.year_level, 'value') else str(student.year_level),
                    status,
                    str(total_credits),
                ])

            col_widths = [25*mm, 30*mm, 45*mm, 35*mm, 25*mm, 25*mm, 20*mm]
            table = Table(data, colWidths=col_widths, repeatRows=1)
            table.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EE6B05")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#333333")),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCA18D")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FFFEF7")]),
            ]))
            elements.append(table)

            doc.build(elements)
            logger.info(f"Generated enrollment summary: {output_path}")
            return output_path

        except Exception as e:
            logger.error(f"Failed to generate enrollment summary: {e}")
            return None
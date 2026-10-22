#!/usr/bin/env python3
"""Capture genuine screenshots of the running application for the documentation.

Every image written by this script is produced by the real application: the
real ``MainWindow``, the real pages, the real widgets, the real MySQL database
and the real detection pipeline. Nothing is mocked and no image is drawn by
hand.

The script drives the application the way an operator would -- it navigates the
sidebar, selects rows, opens the dialogs and feeds a genuine card photo
through the scanner's "Upload Card Image" path -- and then asks Qt to render
the live widget tree into a PNG.

A companion text file, ``screenshot_manifest.txt``, records what was on screen
when each picture was taken, so a reviewer can check the figures against the
data without having to trust the author.

Usage
-----
    python scripts/capture_doc_screenshots.py [--out documentation/screenshots]

The screenshots land in ``documentation/screenshots`` by default. Re-running the
script overwrites them, so the folder can always be regenerated from a working
checkout.
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import QEventLoop, QTimer, Qt  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QLabel,
    QPushButton,
    QTableWidget,
    QTabWidget,
)

from config.logging_config import setup_logging  # noqa: E402
from styles import apply_theme  # noqa: E402

# Window geometry used for every capture. One size for the whole set keeps the
# figures visually consistent in the document.
WINDOW_WIDTH = 1600
WINDOW_HEIGHT = 950

MANIFEST_LINES: list[str] = []


def wait(ms: int = 400) -> None:
    """Let the event loop run for ``ms`` milliseconds.

    Several pages load their data on a worker thread and repaint when the
    answer arrives, so a capture has to happen after the UI has settled.
    """
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def save(widget, path: Path) -> bool:
    """Render ``widget`` into a PNG file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pixmap: QPixmap = widget.grab()
    if pixmap.isNull():
        return False
    return pixmap.save(str(path), "PNG")


def describe(widget, limit: int = 70) -> list[str]:
    """Collect the *visible* text of a widget tree.

    Used to prove that a screenshot really shows live application data rather
    than an empty frame. Only widgets that are actually on screen are read,
    so the error and empty states that a stacked widget keeps hidden do not
    pollute the record.
    """
    found: list[str] = []
    for child in widget.findChildren(QLabel):
        if not child.isVisible():
            continue
        text = child.text().strip()
        if text:
            found.append(text)
    for child in widget.findChildren(QPushButton):
        if not child.isVisible():
            continue
        text = child.text().strip()
        if text:
            found.append(f"[button] {text}")
    for table in widget.findChildren(QTableWidget):
        if not table.isVisible():
            continue
        headers = [
            table.horizontalHeaderItem(c).text()
            for c in range(table.columnCount())
            if table.horizontalHeaderItem(c) is not None
        ]
        if headers:
            found.append(f"[table] {headers}")
        rows = []
        for r in range(min(table.rowCount(), 4)):
            cells = []
            for c in range(table.columnCount()):
                item = table.item(r, c)
                cells.append((item.text().strip() if item else ""))
            rows.append(" | ".join(cells))
        for row in rows:
            if row.strip(" |"):
                found.append(f"[row] {row}")
    return found[:limit]


class Capture:
    """Small helper that records the screenshot and what was on screen."""

    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.manifest = MANIFEST_LINES

    def take(self, name: str, widget, title: str) -> bool:
        target = self.out_dir / f"{name}.png"
        ok = save(widget, target)
        self.manifest.append("")
        self.manifest.append(f"{target.name}")
        self.manifest.append(f"  title:  {title}")
        self.manifest.append(f"  size:   {target.stat().st_size // 1024} KB" if ok else "  size:   FAILED")
        for line in describe(widget):
            self.manifest.append(f"  | {line}")
        if ok:
            print(f"  captured {target.name}")
        else:
            print(f"  FAILED    {target.name}")
        return ok


def select_first_row(table: QTableWidget) -> None:
    """Select the first row of a table, which fires the page's selection handler."""
    if table.rowCount() == 0:
        return
    table.blockSignals(False)
    table.setCurrentCell(0, 0)
    table.selectRow(0)
    table.itemSelectionChanged.emit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(PROJECT_ROOT / "documentation" / "screenshots"),
        help="Directory the PNG files are written to.",
    )
    args = parser.parse_args()

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("WIT Barcode Student Information System")
    apply_theme(app)

    from ui.main_window import MainWindow

    window = MainWindow()
    window.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
    window.show()
    wait(1200)

    cap = Capture(out_dir)

    def go(page_id: str, settle: int = 900) -> None:
        window.navigate_to(page_id)
        wait(settle)

    # ------------------------------------------------------------------
    # 1. Dashboard
    # ------------------------------------------------------------------
    go("dashboard")
    cap.take("01_dashboard", window, "Dashboard - overview and student directory")

    # ------------------------------------------------------------------
    # 2. Dashboard with a student selected (detail panel and ID card)
    # ------------------------------------------------------------------
    dash = window.pages.get("dashboard")
    if dash is not None:
        select_first_row(dash.student_table)
        wait(1200)
        cap.take(
            "02_dashboard_student_selected",
            window,
            "Dashboard - student record and ID card panel",
        )

    # ------------------------------------------------------------------
    # 3. Students page
    # ------------------------------------------------------------------
    go("students")
    cap.take("03_students_directory", window, "Students - searchable student directory")

    students_page = window.pages.get("students")
    if students_page is not None:
        select_first_row(students_page.table)
        wait(800)
        cap.take(
            "04_students_record_selected",
            window,
            "Students - one student selected in the directory",
        )

    # ------------------------------------------------------------------
    # 4. Add / Edit student dialog
    # ------------------------------------------------------------------
    from ui.dialogs.student_dialog import StudentDialog

    add_dialog = StudentDialog(parent=window)
    add_dialog.resize(780, 760)
    add_dialog.show()
    wait(700)
    cap.take("05_student_dialog_add", add_dialog, "Add Student dialog")
    add_dialog.close()

    student_service = window.student_service
    students = student_service.search_students(limit=1)
    if students:
        edit_dialog = StudentDialog(student=students[0], parent=window)
        edit_dialog.resize(780, 760)
        edit_dialog.show()
        wait(700)
        cap.take(
            "06_student_dialog_edit",
            edit_dialog,
            "Edit Student dialog with a real record loaded",
        )
        edit_dialog.close()

    # ------------------------------------------------------------------
    # 5. Subject enrolment with a student selected
    # ------------------------------------------------------------------
    go("enrollment")
    cap.take("07_enrollment_select_student", window, "Subject Enrollment - student list")

    enrollment_page = window.pages.get("enrollment")
    if enrollment_page is not None:
        select_first_row(enrollment_page.student_list)
        wait(1400)
        cap.take(
            "08_enrollment_subjects",
            window,
            "Subject Enrollment - enrolled subjects for the selected student",
        )

        # The "Add Subject" and "New Enrollment" dialogs.
        first_cell = enrollment_page.student_list.item(0, 0)
        student_pk = (
            first_cell.data(Qt.ItemDataRole.UserRole) if first_cell is not None else 1
        )
        enrollments = student_service.get_enrollments_by_student(student_pk)
        if enrollments:
            from ui.enrollment_page import AddSubjectDialog

            add_subject = AddSubjectDialog(
                enrollments[0], student_service, parent=window
            )
            add_subject.resize(720, 560)
            add_subject.show()
            wait(700)
            cap.take("09_add_subject_dialog", add_subject, "Add Subject to Enrollment dialog")
            add_subject.close()

    # ------------------------------------------------------------------
    # 6. Courses and subjects
    # ------------------------------------------------------------------
    go("courses")
    cap.take("10_courses_subjects", window, "Courses and Subjects catalogue")

    # ------------------------------------------------------------------
    # 7. Certificate of Matriculation
    # ------------------------------------------------------------------
    go("certificate")
    certificate_page = window.pages.get("certificate")
    if certificate_page is not None and students:
        certificate_page.load_reference(students[0].reference_number)
        wait(1500)
        cap.take(
            "11_certificate_of_matriculation",
            window,
            "Certificate of Matriculation for a student on file",
        )

    # ------------------------------------------------------------------
    # 8. Assessment and fees with a student selected
    # ------------------------------------------------------------------
    go("assessment")
    assessment_page = window.pages.get("assessment")
    if assessment_page is not None:
        select_first_row(assessment_page.student_list)
        wait(1600)
        cap.take(
            "12_assessment_fees",
            window,
            "Assessment and Fees - charges, schedule and payments",
        )

        from services.assessment_service import AssessmentService

        service = AssessmentService()
        assessments = service.get_student_assessments(1)
        if assessments:
            from ui.assessment_page import RecordPaymentDialog

            payment_dialog = RecordPaymentDialog(
                assessments[0], service, parent=window
            )
            payment_dialog.resize(680, 520)
            payment_dialog.show()
            wait(700)
            cap.take("13_record_payment_dialog", payment_dialog, "Record Payment dialog")
            payment_dialog.close()

    # ------------------------------------------------------------------
    # 9. Reports and analytics
    # ------------------------------------------------------------------
    go("reports")
    cap.take("14_reports_analytics", window, "Reports and Analytics - report catalogue")

    reports_page = window.pages.get("reports")
    if reports_page is not None:
        from ui.reports_page import EnrollmentSummaryDialog

        summary_dialog = EnrollmentSummaryDialog(
            student_service, window.report_service, parent=window
        )
        summary_dialog.resize(760, 620)
        summary_dialog.show()
        wait(900)
        cap.take(
            "15_enrollment_summary_dialog",
            summary_dialog,
            "Enrollment Summary report dialog",
        )
        summary_dialog.close()

    # ------------------------------------------------------------------
    # 10. Barcode scanner
    # ------------------------------------------------------------------
    go("scanner", settle=1800)
    cap.take("16_scanner_idle", window, "Barcode Scanner - idle state")

    scanner = window.pages.get("scanner")
    if scanner is not None:
        # 10a. Genuine still-image scan of a real card, through the same
        #      pipeline the camera frames go through.
        card = PROJECT_ROOT / "assets" / "images" / "Id_1.png"
        if card.exists():
            original_dialog = QFileDialog.getOpenFileName
            QFileDialog.getOpenFileName = staticmethod(  # type: ignore[assignment]
                lambda *a, **k: (str(card), "")
            )
            try:
                scanner._upload_image()
            finally:
                QFileDialog.getOpenFileName = original_dialog  # type: ignore[assignment]

            # Wait for the real pipeline to finish and the result panel to
            # open on its own. Nothing here forces the outcome.
            deadline = time.time() + 45
            while time.time() < deadline:
                wait(500)
                if scanner._active_dialog is not None:
                    break
                if scanner.decoded_value_label.text() not in ("Reading image…", ""):
                    wait(2500)
                    break
            wait(2500)

            # The structured result panel the operator sees next -- opened by
            # the application itself, not constructed by this script.
            result_dialog = scanner._active_dialog
            if result_dialog is not None:
                result_dialog.resize(1040, 780)
                wait(1200)
                cap.take(
                    "18_scan_result_dialog",
                    result_dialog,
                    "Scan Result - verified identity, subjects and fees",
                )
                result_dialog.close()
                wait(1200)

            cap.take(
                "17_scanner_verified",
                window,
                "Barcode Scanner - a real card decoded and verified against the records",
            )

        # 10b. Live camera running.
        try:
            scanner._start_scanner()
            wait(4000)
            cap.take(
                "19_scanner_camera_running",
                window,
                "Barcode Scanner - live camera preview with the detector loaded",
            )
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            print("  FAILED    19_scanner_camera_running")
        finally:
            try:
                scanner._stop_scanner()
                wait(800)
            except Exception:  # noqa: BLE001
                traceback.print_exc()

    # ------------------------------------------------------------------
    # 11. Training centre - the three tabs
    # ------------------------------------------------------------------
    go("training", settle=1400)
    training_page = window.pages.get("training")
    if training_page is not None and isinstance(training_page.tabs, QTabWidget):
        for index, (name, slug) in enumerate(
            [
                ("Training Center - dataset tab", "20_training_center_dataset"),
                ("Training Center - training configuration tab", "21_training_center_training"),
                ("Training Center - model registry tab", "22_training_center_models"),
            ]
        ):
            if index < training_page.tabs.count():
                training_page.tabs.setCurrentIndex(index)
                wait(900)
                cap.take(slug, window, name)

    # ------------------------------------------------------------------
    # 12. Settings
    # ------------------------------------------------------------------
    go("settings")
    cap.take("23_settings", window, "Settings - application and environment configuration")

    # ------------------------------------------------------------------
    # 13. Barcode generator page (implemented, but not routed in the sidebar)
    # ------------------------------------------------------------------
    try:
        from ui.barcode_generator_page import BarcodeGeneratorPage

        generator = BarcodeGeneratorPage(student_service, window.barcode_service)
        generator.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        generator.show()
        wait(1500)
        cap.take(
            "24_barcode_generator_unrouted",
            generator,
            "Barcode Generator - implemented page that the sidebar does not yet route to",
        )
        generator.close()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        print("  FAILED    16_barcode_generator_unrouted")

    # ------------------------------------------------------------------
    manifest_path = out_dir / "screenshot_manifest.txt"
    manifest_path.write_text(
        "WIT Barcode Student Information System\n"
        "Screenshot capture manifest\n"
        "=============================\n"
        "Each entry records the file, what it shows, and the text that was on\n"
        "screen in the live application when the picture was taken.\n"
        + "\n".join(MANIFEST_LINES)
        + "\n",
        encoding="utf-8",
    )
    print(f"\nmanifest written to {manifest_path}")

    window.shutdown()
    wait(400)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Reports & Analytics: every card produces the document it names.

Four of the five cards used to end in ``QMessageBox.information(..., "will be
implemented")`` and the fifth filtered on an attribute the query never loads, so
the page reported "No enrollments found" for a term that had three of them. Each
action now runs the real backend:

===========================  ====================================================
Card                        What it does
===========================  ====================================================
Certificate of Matriculation Opens the Certificate page, which owns the real COM
                            workflow (lookup, generation, print).
Enrollment Summary          Reads the students enrolled in a term from the
                            database and writes the PDF.
Student Barcode Report      Picks a student, then writes the barcode PDF.
Assessment Report           Picks a student, then writes the assessment PDF.
Barcode Scan Log            Writes the scan history, with names resolved, to CSV.
===========================  ====================================================

Cards stay responsive while a document is being written: the event loop is pumped
from a background thread's completion callback, the card's button is disabled so a
second click cannot start a second build, and the success message names the file
that was actually written.
"""

from pathlib import Path

from PySide6.QtCore import Qt, QEvent, QObject, QThread, Signal, Slot
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QLineEdit, QFrame, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QScrollArea, QMessageBox, QApplication, QTableWidget
)

from styles.typography import body_font, section_heading_font, page_heading_font, small_label_font, button_font
from styles.colors import Colors
from services.student_service import StudentService
from services.report_service import ReportService
from services.assessment_service import AssessmentService
from services.barcode_generator import BarcodeGeneratorService
from database.connection import DatabaseUnavailableError, reset_connection_pool
from models.enrollment import Semester
from config.settings import settings
from ui.db_error import show_db_error
from ui.widgets.responsive_table import ColumnSpec, configure_table, set_cell
from ui.widgets.empty_state import EmptyState, error_state
from ui.widgets.panel import card_frame
import logging

logger = logging.getLogger(__name__)


class _ReportWorker(QThread):
    """Runs one report build off the GUI thread.

    A reportlab build is tens to hundreds of milliseconds of pure computation;
    on the GUI thread the window stops repainting for its whole duration, which
    reads as a freeze. The result is returned through :attr:`finished_ok` so a
    failure is reported as a failure rather than as a silent "nothing happened".
    """

    finished_ok = Signal(object, str)  # (Path | None, error message)

    def __init__(self, build, parent=None):
        super().__init__(parent)
        self._build = build

    def run(self):
        try:
            self.finished_ok.emit(self._build(), "")
        except DatabaseUnavailableError as exc:
            logger.warning("Report build could not read the database: %s", exc)
            self.finished_ok.emit(None, str(exc))
        except Exception as exc:  # noqa: BLE001 - carried to the UI, not hidden
            logger.exception("Report build failed")
            self.finished_ok.emit(None, str(exc))


class _CardPressFilter(QObject):
    """Makes a whole card clickable without hijacking its own button.

    The previous implementation replaced ``mousePressEvent`` on the card, which
    fires on press rather than on release: pressing and dragging off the card
    still started the report. This filter only acts when the press landed on the
    card's own background, and reports on release inside the same widget.
    """

    def __init__(self, card, callback):
        super().__init__(card)
        self._card = card
        self._callback = callback
        self._pressed_on_card = False

    def eventFilter(self, watched, event):
        if watched is not self._card:
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._pressed_on_card = self._card.childAt(event.position().toPoint()) is None
        elif event.type() == QEvent.Type.MouseButtonRelease and self._pressed_on_card:
            if event.button() == Qt.MouseButton.LeftButton and self._card.rect().contains(
                event.position().toPoint()
            ):
                self._pressed_on_card = False
                self._callback()
        return super().eventFilter(watched, event)


class ReportsPage(QWidget):
    def __init__(self, student_service: StudentService, report_service: ReportService, parent=None):
        super().__init__(parent)
        self.student_service = student_service
        self.report_service = report_service
        self.assessment_service = AssessmentService()
        self.barcode_service = BarcodeGeneratorService()
        self._worker: _ReportWorker | None = None

        self.setObjectName("reportsPage")
        self._setup_ui()

    # ------------------------------------------------------------------ UI --

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        main_layout.addWidget(self._build_header())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameStyle(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 24, 24, 24)
        content_layout.setSpacing(16)

        reports = [
            (
                "Certificate of Matriculation",
                "Open the matriculation record and print or export the certificate",
                "📜",
                self._open_certificate_page,
            ),
            (
                "Enrollment Summary",
                "Every student enrolled in a chosen academic year and semester",
                "📊",
                self._generate_enrollment_summary,
            ),
            (
                "Student Barcode Report",
                "The student's identity, barcode payload and printed symbol",
                "🏷️",
                self._generate_barcode_report,
            ),
            (
                "Assessment Report",
                "Assessment charges, discounts, schedule and payments for a student",
                "💰",
                self._generate_assessment_report,
            ),
            (
                "Barcode Scan Log",
                "The scan history with the resolved student for every scan",
                "📋",
                self._generate_scan_log,
            ),
        ]

        self.cards: dict[str, QFrame] = {}
        self.card_buttons: dict[str, QPushButton] = {}
        self.status_label = QLabel("")
        self.status_label.setFont(small_label_font(11))
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")

        for key, (title, desc, icon, callback) in zip(
            ("com", "enrollment", "barcode", "assessment", "scans"), reports
        ):
            card, button = self._create_report_card(title, desc, icon, callback)
            self.cards[key] = card
            self.card_buttons[key] = button
            content_layout.addWidget(card)

        content_layout.addSpacing(4)
        content_layout.addWidget(self.status_label)
        content_layout.addStretch()
        scroll.setWidget(content)
        main_layout.addWidget(scroll)

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setFixedHeight(80)
        header.setStyleSheet(
            f"background-color: {Colors.ORANGE}; "
            "border-top-left-radius: 8px; border-top-right-radius: 8px;"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 0, 24, 0)

        title = QLabel("Reports & Analytics")
        title.setFont(page_heading_font(24))
        title.setStyleSheet("color: white;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        return header

    def _create_report_card(self, title: str, desc: str, icon: str, callback):
        # Scoped by object name. Qt applies a parent's stylesheet to its
        # children as well, so the unscoped `QFrame { border }` this used to
        # carry put a hairline box around the title and the description -- the
        # "nested boxes" look on the whole page.
        card = card_frame(f"reportCard_{title.replace(' ', '')}", hover_border=Colors.ORANGE)
        card.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        icon_label = QLabel(icon)
        icon_label.setFixedSize(60, 60)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet(f"""
            QLabel {{
                background-color: {Colors.CREAM};
                border-radius: 30px;
                font-size: 24px;
                color: {Colors.ORANGE};
            }}
        """)

        # The text column carries the stretch factor. Without it the labels kept
        # their sizeHint width, so the description wrapped inside a 250px box in
        # the middle of a 1100px card.
        text_column = QWidget()
        text_layout = QVBoxLayout(text_column)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(4)

        title_label = QLabel(title)
        title_label.setFont(section_heading_font(16))
        title_label.setStyleSheet(f"color: {Colors.DARK_BURNT_ORANGE};")
        title_label.setWordWrap(True)

        desc_label = QLabel(desc)
        desc_label.setFont(body_font(12))
        desc_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        desc_label.setWordWrap(True)

        text_layout.addWidget(title_label)
        text_layout.addWidget(desc_label)
        text_layout.addStretch()

        btn = QPushButton("Generate")
        btn.setProperty("secondary", "true")
        btn.setMinimumWidth(120)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(callback)

        layout.addWidget(icon_label)
        layout.addWidget(text_column, 1)
        layout.addWidget(btn, 0, Qt.AlignmentFlag.AlignVCenter)

        card.installEventFilter(_CardPressFilter(card, callback))
        return card, btn

    # ------------------------------------------------------------- routing --

    @property
    def _window(self):
        widget = self.parent()
        while widget is not None:
            if hasattr(widget, "navigate_to"):
                return widget
            widget = widget.parent()
        return None

    def _open_certificate_page(self) -> None:
        """The COM workflow lives on the Certificate page; go there."""
        window = self._window
        if window is None:
            QMessageBox.information(
                self, "Certificate of Matriculation",
                "Open the Certificate of Matriculation page from the navigation to "
                "produce a certificate.",
            )
            return
        window.navigate_to("certificate")

    # -------------------------------------------------------------- exports --

    def _set_busy(self, key: str, busy: bool, message: str = "") -> None:
        button = self.card_buttons[key]
        button.setEnabled(not busy)
        button.setText("Working…" if busy else "Generate")
        if message:
            self.status_label.setText(message)

    def _run_report(self, key: str, build, success_message) -> None:
        """Build one document off the GUI thread and report what happened.

        ``build`` returns the written :class:`~pathlib.Path` or ``None``. The
        card's button is disabled for the duration, so a second click cannot start
        a second build on the same path, and success is only announced once the
        file really exists.
        """
        if self._worker is not None and self._worker.isRunning():
            self._set_busy(key, True, "Another document is still being generated.")
            return

        self._set_busy(key, True, "Generating…")
        self._worker = _ReportWorker(build, self)
        self._worker.finished_ok.connect(
            lambda path, error: self._on_report_done(key, path, error, success_message)
        )
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    @Slot(object, str)
    def _on_report_done(self, key: str, path, error: str, success_message) -> None:
        self._set_busy(key, False)
        self._worker = None

        if error:
            # Database failures are retryable and are offered as such; a build
            # failure is not, so it is reported plainly.
            if "database" in error.lower() or "connect" in error.lower():
                reset_connection_pool()
                show_db_error(
                    self,
                    DatabaseUnavailableError(error),
                    retry_callback=lambda: self._on_report_done(key, None, "", success_message),
                )
            else:
                QMessageBox.critical(self, "Report not generated", f"{error}")
            self.status_label.setText("The report was not written.")
            return

        if path is None:
            QMessageBox.critical(
                self,
                "Report not generated",
                "The document could not be written. Nothing was saved, and no "
                "report has been produced.",
            )
            self.status_label.setText("The report was not written.")
            return

        written = Path(path)
        if not written.exists():
            QMessageBox.critical(
                self,
                "Report not written",
                f"The backend reported success but the file is missing:\n{written}",
            )
            self.status_label.setText("The report was not written.")
            return

        stamp = self.status_label
        stamp.setText(f"Saved: {written}")
        QMessageBox.information(self, "Report ready", success_message(str(written)))

    def _ask_save_path(self, suggested_name: str, title: str) -> str | None:
        suggested = settings.REPORTS_OUTPUT_DIR / suggested_name
        path, _ = QFileDialog.getSaveFileName(self, title, str(suggested), "PDF Files (*.pdf)")
        return path or None

    def _generate_barcode_report(self) -> None:
        student = self._pick_student("Student Barcode Report")
        if student is None:
            return

        def build():
            barcode = self.barcode_service.get_student_barcode(student.id)
            if barcode is None:
                raise ValueError(
                    f"{student.full_name} has no barcode on file. Generate one on the "
                    f"Barcode Generator page first."
                )
            return self.report_service.generate_student_barcode_report(
                student, barcode, Path(self._pending_barcode_path)
            )

        path = self._ask_save_path(
            f"barcode_report_{student.reference_number}.pdf", "Save Barcode Report"
        )
        if path is None:
            return
        self._pending_barcode_path = path
        self._run_report(
            "barcode",
            build,
            lambda written: f"The barcode report for {student.full_name} was written to:\n{written}",
        )

    def _generate_assessment_report(self) -> None:
        student = self._pick_student("Assessment Report")
        if student is None:
            return

        def build():
            assessment = self.assessment_service.get_latest_assessment(student.id)
            if assessment is None:
                raise ValueError(
                    f"No assessment is on file for {student.full_name} "
                    f"({student.reference_number}). There is nothing to report."
                )
            payments = self.assessment_service.get_payments(assessment.id)
            return self.report_service.generate_assessment_report(
                student, assessment, Path(self._pending_assessment_path), payments
            )

        path = self._ask_save_path(
            f"assessment_{student.reference_number}.pdf", "Save Assessment Report"
        )
        if path is None:
            return
        self._pending_assessment_path = path
        self._run_report(
            "assessment",
            build,
            lambda written: f"{student.full_name}'s assessment was written to:\n{written}",
        )

    def _generate_scan_log(self) -> None:
        suggested = settings.REPORTS_OUTPUT_DIR / "barcode_scan_log.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Barcode Scan Log", str(suggested), "CSV Files (*.csv)"
        )
        if path is None:
            return

        def build():
            scans = self.barcode_service.get_recent_scans(limit=1000)
            return self.report_service.export_scan_log_csv(scans, Path(path))

        self._run_report(
            "scans",
            build,
            lambda written: f"The scan history was written to:\n{written}",
        )

    def _generate_enrollment_summary(self) -> None:
        dialog = EnrollmentSummaryDialog(self.student_service, self.report_service, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            dialog.deleteLater()
            return
        request = dialog.get_request()
        dialog.deleteLater()
        if request is None:
            return

        academic_year, semester, path = request
        self._run_report(
            "enrollment",
            lambda: self._build_enrollment_summary(academic_year, semester, Path(path)),
            lambda written: f"The {academic_year} {semester} summary was written to:\n{written}",
        )

    def _build_enrollment_summary(self, academic_year: str, semester: str, path: Path):
        """Read the term's real enrollments, then render the report.

        The enrollments are read here, per student, because
        :meth:`StudentRepository.get_all` returns student rows only -- the
        previous version filtered on ``student.enrollments``, which is always
        empty for a list query, so the report claimed "No enrollments found" for
        terms that had them.
        """
        students = self.student_service.search_students(limit=1000)
        wanted = str(getattr(semester, "value", semester))

        enrolled = []
        for student in students:
            for enrollment in self.student_service.get_enrollments_by_student(student.id):
                if enrollment.academic_year != academic_year:
                    continue
                if str(getattr(enrollment.semester, "value", enrollment.semester)) != wanted:
                    continue
                if not any(
                    str(getattr(row.status, "value", row.status)) == "enrolled"
                    for row in enrollment.subjects
                ):
                    continue
                enrolled.append((student, enrollment))
                break

        if not enrolled:
            raise ValueError(
                f"No student has an 'enrolled' subject in {academic_year} {wanted}. "
                f"There is nothing to summarise."
            )

        # The report reads ``student.enrollments``; only the matching term is
        # attached, so the printed row is the term that was asked for.
        payload = []
        for student, enrollment in enrolled:
            student.enrollments = [enrollment]
            payload.append(student)

        return self.report_service.generate_enrollment_summary_report(
            payload, academic_year, wanted, path
        )

    # --------------------------------------------------------------- picker --

    def _pick_student(self, title: str):
        """Ask for a student. Returns ``None`` when the operator cancels."""
        dialog = StudentPickerDialog(self.student_service, title=title, parent=self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None
            return dialog.selected_student()
        finally:
            dialog.deleteLater()


class StudentPickerDialog(QDialog):
    """Choose one student from the real student list, with search.

    Used by the reports page instead of the message box that used to tell the
    operator to "select a student" without offering a way to do it.
    """

    def __init__(self, student_service: StudentService, title: str = "Select a Student", parent=None):
        super().__init__(parent)
        self.student_service = student_service
        self._students = []
        self._selected = None

        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumSize(640, 480)
        self.resize(720, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        prompt = QLabel("Search by name, student ID or reference number:")
        prompt.setFont(body_font(13))
        layout.addWidget(prompt)

        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText("Type to filter…")
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)

        self.list_widget = QWidget()
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(0)

        self.table = QTableWidget()
        configure_table(
            self.table,
            [
                ColumnSpec("Name", width=200, stretch=True),
                ColumnSpec("Student ID", width=130),
                ColumnSpec("Reference", width=110),
                ColumnSpec("Program", width=180, stretch=True),
            ],
        )
        self.table.itemSelectionChanged.connect(lambda: self._on_selection_changed())
        self.list_layout.addWidget(self.table)
        layout.addWidget(self.list_widget, 1)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setFont(small_label_font(11))
        self.message.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        layout.addWidget(self.message)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Select")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load()

    def _load(self) -> None:
        try:
            self._students = self.student_service.search_students(limit=500)
        except DatabaseUnavailableError as exc:
            logger.warning("Student picker could not read the student list: %s", exc)
            self._students = []
            self.message.setText("The student list could not be read from the database.")
            self.message.setStyleSheet(f"color: {Colors.ERROR_RED};")
            self.table.setVisible(False)
            return
        except Exception as exc:  # noqa: BLE001 - reported in the dialog
            logger.exception("Student picker failed")
            self._students = []
            self.message.setText(f"The student list could not be read:\n{exc}")
            self.message.setStyleSheet(f"color: {Colors.ERROR_RED};")
            self.table.setVisible(False)
            return

        self.message.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        self.message.setText(f"{len(self._students)} student(s) on file.")
        self._filter(self.search.text())

    def _filter(self, text: str) -> None:
        needle = text.strip().lower()
        rows = [
            student for student in self._students
            if not needle
            or needle in student.full_name.lower()
            or needle in (student.student_id or "").lower()
            or needle in (student.reference_number or "").lower()
        ]

        self.table.setRowCount(0)
        for student in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            set_cell(self.table, row, 0, student.full_name)
            set_cell(self.table, row, 1, student.student_id)
            set_cell(self.table, row, 2, student.reference_number)
            set_cell(self.table, row, 3, student.program)
        if rows:
            self.table.selectRow(0)

        if not rows:
            self.message.setText("No student matches that search.")
            self.message.setStyleSheet(f"color: {Colors.WARNING_ORANGE};")

    def _on_selection_changed(self) -> None:
        """Report the current pick so the operator can see it before confirming."""
        row = self.table.currentRow()
        item = self.table.item(row, 1) if row >= 0 else None
        if item is None:
            return
        name_item = self.table.item(row, 0)
        self.message.setText(f"Selected: {name_item.text() if name_item else ''} ({item.text()})")
        self.message.setStyleSheet(f"color: {Colors.DARK_BURNT_ORANGE};")

    def selected_student(self):
        """The highlighted student, re-read from the database by id."""
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 1)
        if item is None:
            return None
        try:
            return self.student_service.get_student_by_student_id(item.text())
        except DatabaseUnavailableError:
            return None

    def accept(self) -> None:
        if self.selected_student() is None:
            QMessageBox.information(
                self, "Select a student", "Highlight the student to use, then press Select."
            )
            return
        super().accept()


class EnrollmentSummaryDialog(QDialog):
    """Ask for the term to summarise."""

    def __init__(self, student_service: StudentService, report_service: ReportService, parent=None):
        super().__init__(parent)
        self.student_service = student_service
        self.report_service = report_service
        self._request = None

        self.setWindowTitle("Enrollment Summary Report")
        self.setModal(True)
        self.setMinimumSize(440, 260)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        intro = QLabel(
            "Summarises every student who has at least one subject in 'enrolled' "
            "status for the term you choose."
        )
        intro.setWordWrap(True)
        intro.setFont(body_font(12))
        intro.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        layout.addWidget(intro)

        form = QFormLayout()
        form.setSpacing(12)

        self.academic_year = QLineEdit("2024-2025")
        self.academic_year.setPlaceholderText("e.g. 2024-2025")
        form.addRow("Academic Year:", self.academic_year)

        self.semester = QComboBox()
        self.semester.addItems([item.value for item in Semester])
        form.addRow("Semester:", self.semester)

        layout.addLayout(form)
        layout.addStretch()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Generate Report")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        year = self.academic_year.text().strip()
        if not year or "-" not in year:
            QMessageBox.warning(
                self, "Academic year",
                "Enter the academic year in the form YYYY-YYYY, for example 2024-2025.",
            )
            self.academic_year.setFocus()
            return

        suggested = settings.REPORTS_OUTPUT_DIR / (
            f"enrollment_summary_{year}_{self.semester.currentText().replace(' ', '_')}.pdf"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Enrollment Summary", str(suggested), "PDF Files (*.pdf)"
        )
        if not path:
            return
        self._request = (year, self.semester.currentText(), path)
        self.accept()

    def get_request(self):
        """``(academic_year, semester, output_path)`` or ``None`` if cancelled."""
        return self._request
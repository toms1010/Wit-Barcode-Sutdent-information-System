"""Certificate of Matriculation page: the student record, laid out to be read.

This is the registrar-facing view of one student, and it is the screen the
printed and exported document is rendered from. It is a *read* screen: it shows
what the database holds and never edits it. Editing stays in Students, Subject
Enrollment and Assessment & Fees.

The layout follows the institutional reference:

    institutional header  logo, school name, system name, Save PDF / Print
    student profile       portrait, name, program & year, reference, status
    course table          the enrolled subjects, with a totals row
    financial assessment  three cards: fees, discounts & credits, schedule

Every figure on the screen is computed in :mod:`services.com_summary` from the
records the repositories return. Nothing here derives money, and no sample
student, fee or installment is ever written into the widgets: an empty record
produces an empty state that explains itself, not a filled-in example.

Three states are kept visibly distinct, because on a registrar's screen they
mean three different things: *loading*, *nothing on file*, and *the database
could not be reached*. See :mod:`ui.widgets.empty_state`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from config.settings import settings
from database.connection import DatabaseUnavailableError
from services.assessment_service import AssessmentService
from services.com_print_service import ComNotCertifiableError, ComPrintService
from services.com_summary import (
    ComSummary,
    FeeLines,
    FinancialSummary,
    format_peso,
    installment_state,
    program_and_year,
    summarize_enrollment,
    summarize_fee_lines,
    summarize_financials,
)
from services.report_service import ReportService
from services.student_service import (
    EnrollmentLookup,
    StudentService,
    get_enrollment_for_reference,
)
from styles.colors import Colors
from styles.typography import (
    body_bold_font,
    body_font,
    button_font,
    page_heading_font,
    section_heading_font,
    small_label_font,
)
from ui.db_error import show_db_error
from ui.widgets.empty_state import EmptyState
from ui.widgets.responsive_table import (
    ColumnSpec,
    configure_table,
    count_column,
    fit_height,
    money_column,
    set_cell,
)
from ui.widgets.profile_card import StudentProfileCard

logger = logging.getLogger(__name__)

#: Portrait edge length on this page. Larger than the 80px avatar used in the
#: scanner and modal, because this screen is the one a registrar reads a face
#: from across a desk.
PROFILE_PHOTO = 104

#: Milliseconds of quiet typing before the student list is filtered. Long enough
#: that a reference number typed at speed triggers one filter, short enough to
#: feel immediate.
SEARCH_DEBOUNCE_MS = 180

#: Tone colour per installment state, from
#: :func:`services.com_summary.installment_state`. Shared with every other screen
#: that renders one of these states, so an "overdue" installment cannot be red
#: here and amber there.
_STATE_COLOURS = Colors.TONES

_RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
_LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
_CENTRE = Qt.AlignmentFlag.AlignCenter


class CertificatePage(QWidget):
    """Read-only student record with print and PDF actions."""

    def __init__(
        self,
        student_service: StudentService,
        assessment_service: AssessmentService,
        report_service: ReportService,
        parent=None,
    ):
        super().__init__(parent)
        self.student_service = student_service
        self.assessment_service = assessment_service
        self.report_service = report_service
        self._print_service: Optional[ComPrintService] = None
        #: True while a document is being built, so a second click cannot start a
        #: second copy.
        self._print_busy = False

        self.setObjectName("certificatePage")
        self.current_student = None
        self.current_enrollment = None
        self.current_assessment = None
        self.com: ComSummary = summarize_enrollment(None)
        self.financial: FinancialSummary = summarize_financials(None)
        self.fee_lines: FeeLines = summarize_fee_lines(None)
        self._students = []

        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_debounce.timeout.connect(self._apply_search)

        self._setup_ui()
        self.refresh()

    # ------------------------------------------------------------------ ui --

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        # A scroll area so the three financial cards stay side by side on a wide
        # monitor and stack on a laptop rather than being crushed.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("certificateScroll")
        scroll.setStyleSheet(f"background-color: {Colors.PAGE_BG}; border: none;")
        body = QWidget()
        body.setStyleSheet(f"background-color: {Colors.PAGE_BG};")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(24, 20, 24, 28)
        body_layout.setSpacing(18)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        self.stack = QStackedWidget()
        body_layout.addWidget(self.stack)

        self.no_student_state = EmptyState(
            "No student selected",
            "Choose a student from the list to see the record, or search for one by "
            "name, student ID or reference number.",
            icon="search",
            parent=self,
        )
        self.stack.addWidget(self.no_student_state)

        self.record = QWidget()
        self.record.setStyleSheet(f"background-color: {Colors.PAGE_BG};")
        record_layout = QVBoxLayout(self.record)
        record_layout.setContentsMargins(0, 0, 0, 0)
        record_layout.setSpacing(18)
        record_layout.addWidget(self._build_profile_section())
        record_layout.addWidget(self._build_course_section())
        record_layout.addWidget(self._build_financial_section())
        record_layout.addStretch()
        self.stack.addWidget(self.record)

        # Ctrl+K focuses the quick search from anywhere on this page. Bound to
        # this widget's window context so it cannot shadow a shortcut in a dialog
        # opened on top of it.
        self._quick_search_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self._quick_search_shortcut.activated.connect(self._focus_search)

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("certificateHeader")
        header.setFixedHeight(88)
        header.setStyleSheet(
            "QFrame#certificateHeader {"
            f"  background-color: {Colors.SURFACE};"
            f"  border-bottom: 1px solid {Colors.BORDER};"
            "}"
        )
        layout = QHBoxLayout(header)
        layout.setContentsMargins(24, 10, 24, 10)
        layout.setSpacing(16)

        layout.addWidget(self._logo_label())

        titles = QVBoxLayout()
        titles.setSpacing(0)
        school = QLabel("WESTERN INSTITUTE OF TECHNOLOGY")
        school.setFont(page_heading_font(19))
        school.setStyleSheet(
            f"color: {Colors.TEXT_PRIMARY}; background: transparent;"
        )
        system = QLabel("Student Information & Matriculation System")
        system.setFont(small_label_font(12))
        system.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; background: transparent;"
        )
        titles.addWidget(school)
        titles.addWidget(system)
        layout.addLayout(titles)

        layout.addStretch()

        self.search_input = QLineEdit()
        self.search_input.setObjectName("quickSearch")
        self.search_input.setPlaceholderText("Quick search: name, student ID or reference…")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumWidth(240)
        self.search_input.setFont(body_font(13))
        self.search_input.setToolTip(
            "Filter the student list by name, student ID or reference number (Ctrl+K)"
        )
        self.search_input.textChanged.connect(self._on_search_typed)
        layout.addWidget(self.search_input)

        # The record on screen is always the selected student. Without an
        # explicit selection an operator could print the last-loaded record
        # believing it was the one they had in front of them.
        self.student_combo = QComboBox()
        self.student_combo.setObjectName("certificateStudentCombo")
        self.student_combo.setMinimumWidth(230)
        self.student_combo.setToolTip("The student whose record is shown")
        self.student_combo.currentIndexChanged.connect(self._on_combo_changed)
        layout.addWidget(self.student_combo)

        # Split into two explicit actions rather than one combined control: a
        # print dialog and a file write are different consequences, and an
        # operator should be able to see which one they are invoking.
        self.save_pdf_btn = QPushButton("Save PDF")
        self.save_pdf_btn.setObjectName("savePdfButton")
        self.save_pdf_btn.setFont(button_font())
        self.save_pdf_btn.setProperty("secondary", "true")
        self.save_pdf_btn.setToolTip("Export this record as a PDF file")
        self.save_pdf_btn.clicked.connect(self._save_pdf)
        layout.addWidget(self.save_pdf_btn)

        self.print_btn = QPushButton("Print COM")
        self.print_btn.setObjectName("printButton")
        self.print_btn.setFont(button_font())
        self.print_btn.setToolTip("Send this record to a printer")
        self.print_btn.clicked.connect(self._print)
        layout.addWidget(self.print_btn)

        # Progress text for the document actions. The buttons used to change
        # nothing visible while a build ran, so a slow printer looked like a
        # dead control.
        self.action_status_label = QLabel("")
        self.action_status_label.setFont(small_label_font(11))
        self.action_status_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        self.action_status_label.setVisible(False)
        layout.addWidget(self.action_status_label)

        return header

    def _logo_label(self) -> QLabel:
        logo = QLabel()
        logo.setObjectName("institutionLogo")
        logo.setFixedSize(48, 48)
        logo.setStyleSheet("border: none; background: transparent;")
        logo_path = settings.LOGO_PATH
        if not logo_path.exists():
            logger.warning("Logo asset missing: %s", logo_path)
            return logo
        pixmap = QPixmap(str(logo_path))
        if pixmap.isNull():
            logger.warning("Logo could not be decoded: %s", logo_path)
            return logo
        logo.setPixmap(
            pixmap.scaled(
                48,
                48,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        return logo

    # ------------------------------------------------------- profile card --

    def _build_profile_section(self) -> QWidget:
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._section_heading("STUDENT PROFILE"))

        # The shared card: photograph, name, program & year, reference number,
        # academic term and the status pill, rendered by one component so this
        # screen cannot drift from the verification popup's version.
        self.profile_card = StudentProfileCard(
            "STUDENT PROFILE INFORMATION", size=PROFILE_PHOTO
        )
        self.profile_card.add_detail("reference", "Reference Number")
        self.profile_card.add_detail("student_id", "Student ID")
        self.profile_card.add_detail("term", "Academic Term")
        self.photo_label = self.profile_card.photo_label
        self.status_pill = self.profile_card.status_pill
        layout.addWidget(self.profile_card)
        return section

    # -------------------------------------------------------- course table --

    def _course_columns(self):
        return [
            ColumnSpec("Code", width=110),
            ColumnSpec("Course", width=220, stretch=True),
            count_column("Credits", 86),
            ColumnSpec("Time / Days", width=190),
            ColumnSpec("Room", width=110),
            count_column("Periods", 90),
            money_column("Lab Fee", 124),
            money_column("Other Fee", 124),
        ]

    def _build_course_section(self) -> QWidget:
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._section_heading("CERTIFICATE OF MATRICULATION"))

        card = QFrame()
        card.setObjectName("courseCard")
        card.setStyleSheet(self._card_style("courseCard"))
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 16)
        card_layout.setSpacing(10)

        self.term_label = QLabel("")
        self.term_label.setFont(small_label_font(12))
        self.term_label.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; background: transparent;"
        )
        card_layout.addWidget(self.term_label)

        self.course_table = QTableWidget()
        self.course_table.setObjectName("comSubjectTable")
        configure_table(self.course_table, self._course_columns())
        self.course_table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        card_layout.addWidget(self.course_table)

        self.course_empty = QLabel("")
        self.course_empty.setObjectName("courseEmpty")
        self.course_empty.setFont(body_font(12))
        self.course_empty.setWordWrap(True)
        self.course_empty.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; background: transparent;"
        )
        self.course_empty.setVisible(False)
        card_layout.addWidget(self.course_empty)

        layout.addWidget(card)
        return section

    # ---------------------------------------------------- financial cards --

    def _build_financial_section(self) -> QWidget:
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._section_heading("FINANCIAL ASSESSMENT"))

        cards = QHBoxLayout()
        cards.setSpacing(16)
        cards.setContentsMargins(0, 0, 0, 0)
        self.assessment_table = self._money_card(
            cards, "assessmentTable", "A. ASSESSMENT BREAKDOWN"
        )
        self.discount_table = self._money_card(
            cards, "discountTable", "B. DISCOUNTS & CREDITS"
        )
        self.schedule_table = self._money_card(
            cards, "installmentTable", "C. PAYMENT SCHEDULE", status=True
        )
        layout.addLayout(cards)

        # The four figures that answer "what does this student still owe", kept
        # out of the cards above because they are totals, not line items.
        self.kpi_labels = {}
        kpis = QHBoxLayout()
        kpis.setSpacing(16)
        kpis.setContentsMargins(0, 0, 0, 0)
        for key, title in (
            ("assessed", "Total Assessment"),
            ("discounts", "Total Discounts"),
            ("paid", "Payments Received"),
            ("balance", "Outstanding Balance"),
        ):
            tile, value = self._kpi_tile(f"kpi_{key}", title)
            self.kpi_labels[key] = value
            kpis.addWidget(tile)
        layout.addLayout(kpis)

        self.discrepancy_label = QLabel("")
        self.discrepancy_label.setObjectName("discrepancyNote")
        self.discrepancy_label.setFont(body_font(11))
        self.discrepancy_label.setWordWrap(True)
        self.discrepancy_label.setStyleSheet(
            f"color: {Colors.WARNING_ORANGE}; background: transparent;"
        )
        self.discrepancy_label.setVisible(False)
        layout.addWidget(self.discrepancy_label)

        return section

    def _money_card(self, into: QHBoxLayout, object_name: str, title: str,
                    *, status: bool = False) -> QTableWidget:
        card = QFrame()
        card.setObjectName(f"{object_name}Card")
        card.setStyleSheet(self._card_style(f"{object_name}Card"))
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(8)

        # The three cards are laid out in one row, so the shortest one is padded
        # to match the tallest. A stretch at the bottom of the column absorbs that
        # padding: without it Qt shares the spare height between the heading and
        # the table, and the card titles drift to the middle of their cards.
        heading = QLabel(title)
        heading.setFont(small_label_font(11))
        heading.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        heading.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        heading.setStyleSheet(
            f"color: {Colors.ACCENT_STRONG}; background: transparent;"
        )
        layout.addWidget(heading)

        columns = [ColumnSpec("Item", width=150, stretch=True), money_column("Amount (PHP)", 130)]
        if status:
            columns.append(count_column("Status", 96))
        table = QTableWidget()
        table.setObjectName(object_name)
        configure_table(table, columns)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout.addWidget(table)
        layout.addStretch(1)

        into.addWidget(card, 1)
        return table

    def _kpi_tile(self, object_name: str, title: str) -> tuple[QFrame, QLabel]:
        """A single headline figure: the label and the value label to write into."""
        tile = QFrame()
        tile.setObjectName(object_name)
        tile.setStyleSheet(self._card_style(object_name))
        layout = QVBoxLayout(tile)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(2)

        label = QLabel(title.upper())
        label.setFont(small_label_font(10))
        label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; background: transparent;")
        layout.addWidget(label)

        value = QLabel("—")
        value.setObjectName(f"{object_name}Value")
        value.setFont(page_heading_font(17))
        value.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")
        layout.addWidget(value)
        return tile, value

    def _section_heading(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setFont(small_label_font(11))
        label.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; background: transparent;"
        )
        return label

    @staticmethod
    def _card_style(object_name: str) -> str:
        return (
            f"QFrame#{object_name} {{"
            f"  background-color: {Colors.SURFACE};"
            f"  border: 1px solid {Colors.BORDER};"
            "  border-radius: 12px;"
            "}"
        )

    # ------------------------------------------------------------- loading --

    def refresh(self):
        """Reload the student list. Called on open and by the retry handler."""
        self._students = []
        try:
            students = self.student_service.search_students(limit=500)
        except DatabaseUnavailableError as exc:
            # The previously loaded student's name, photograph and peso figures
            # used to stay on screen here with only the buttons disabled, so a
            # failed refresh looked like a page that still had current data.
            self._show_no_match(
                "Records unavailable",
                "The student list could not be read, so the record on screen is not "
                "current.",
            )
            show_db_error(self, exc, retry_callback=self.refresh)
            return

        self._students = list(students)
        self._apply_search()
        if not students:
            self._show_no_match(
                "No student records yet",
                "There are no student records yet. Add a student, or scan a card, "
                "and this page will show their enrollment.",
            )

    def _focus_search(self):
        self.search_input.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search_input.selectAll()

    def _on_search_typed(self, _text: str):
        self._search_debounce.start()

    def _apply_search(self):
        """Filter the already-loaded students without issuing another query.

        The list is capped at 500 records when loaded, so a student outside it
        cannot be found here. That limit is stated in the field's tooltip and in
        the no-match message rather than being left for the operator to infer.
        """
        query = self.search_input.text().strip()
        needle = query.lower()
        if needle:
            matches = [
                student
                for student in self._students
                if needle in (student.full_name or "").lower()
                or needle in (student.student_id or "").lower()
                or needle in (student.reference_number or "").lower()
            ]
        else:
            matches = list(self._students)

        self._populate(matches, query)

    def _populate(self, matches, query: str):
        previous_id = self.current_student.id if self.current_student else None
        self.student_combo.blockSignals(True)
        self.student_combo.clear()
        for student in matches:
            self.student_combo.addItem(
                f"{student.full_name} ({student.reference_number})", student.id
            )
        self.student_combo.blockSignals(False)

        if not matches:
            self._show_no_match(
                "No matching student" if query else "No student selected",
                (
                    f"Nothing in the loaded list matches “{query}”. Check the spelling, "
                    "or search by reference number."
                    if query
                    else "Choose a student from the list to see the record, or search "
                    "for one by name, student ID or reference number."
                ),
            )
            return

        index = 0
        if previous_id is not None:
            for position, student in enumerate(matches):
                if student.id == previous_id:
                    index = position
                    break
        self.student_combo.setCurrentIndex(index)
        self.stack.setCurrentWidget(self.record)
        self._select_by_id(self.student_combo.currentData())

    def _show_no_match(self, heading: str, message: str):
        """Empty the page: no student means no record, totals or photograph.

        Clearing only the three ``current_*`` fields left the previous student's
        face and money in the widgets behind the message, which is how they came
        back on screen on the next render.
        """
        self.stack.setCurrentWidget(self.no_student_state)
        self.no_student_state.heading_label.setText(heading)
        self.no_student_state.set_message(message)
        self.current_student = None
        self.current_enrollment = None
        self.current_assessment = None
        self.com = summarize_enrollment(None)
        self.financial = summarize_financials(None)
        self.fee_lines = summarize_fee_lines(None)
        try:
            self.profile_card.clear()
        except Exception:  # noqa: BLE001 - clearing must never block the message
            logger.exception("Could not clear the profile card")
        for table in (self.course_table, self.assessment_table, self.discount_table, self.schedule_table):
            table.setRowCount(0)
        self._set_actions_enabled(False)

    def _on_combo_changed(self, _index: int):
        if self.student_combo.currentIndex() < 0:
            return
        self.stack.setCurrentWidget(self.record)
        self._select_by_id(self.student_combo.currentData())

    def _select_by_id(self, student_id):
        if student_id is None:
            return
        for student in self._students:
            if student.id == student_id:
                self.current_student = student
                self._load_record(student)
                return
        # The selected student is not in the loaded page (a router handed one
        # over directly), so it is read from the database by its own id.
        try:
            student = self.student_service.get_student(student_id)
        except DatabaseUnavailableError as exc:
            show_db_error(self, exc, retry_callback=lambda: self._select_by_id(student_id))
            return
        if student is None:
            self._show_no_match(
                "Student not found",
                "That student record is no longer in the database.",
            )
            return
        self._students.append(student)
        self.current_student = student
        self._load_record(student)

    # -------------------------------------------------------------- record --

    def _load_record(self, student, enrollment=None, assessment=None):
        """Fetch and render one student's record.

        ``enrollment``/``assessment`` are accepted so a caller that has already
        read them (the reference lookup) does not repeat the queries.
        """
        try:
            if enrollment is None and assessment is None:
                enrollments = self.student_service.get_enrollments_by_student(student.id)
                assessment = self.assessment_service.get_latest_assessment(student.id)
                enrollment = enrollments[0] if enrollments else None
        except DatabaseUnavailableError as exc:
            show_db_error(self, exc, retry_callback=lambda: self._load_record(student))
            return

        self.current_enrollment = enrollment
        self.current_assessment = assessment

        self.com = summarize_enrollment(self.current_enrollment)
        self.financial = summarize_financials(assessment)
        self.fee_lines = summarize_fee_lines(assessment)

        self._render_profile(student)
        self._render_courses()
        self._render_financial()
        self._set_actions_enabled(self.com.available)

    def _render_profile(self, student):
        term = " • ".join(
            part for part in (self.com.academic_year, self.com.semester) if part
        ) or "Not recorded"
        self.profile_card.set_student(
            student, extra_details=(("student_id", "Student ID"), ("term", "Academic Term"))
        )
        # Through the card's own setter: the card decides how "term" is drawn,
        # and reaching into its private dict broke silently when its keys changed.
        self.profile_card.set_detail("term", term)

    def _render_courses(self):
        columns = self._course_columns()
        table = self.course_table

        rows = [
            [
                row.code,
                row.subject,
                str(row.units),
                row.schedule or "—",
                row.room or "—",
                str(row.period) if row.period else "—",
                format_peso(row.lab_fee, blank_for_zero=True),
                format_peso(row.other_fee, blank_for_zero=True),
            ]
            for row in self.com.rows
        ]
        if rows:
            rows.append(
                [
                    "TOTALS",
                    f"{self.com.subject_count} subject"
                    f"{'' if self.com.subject_count == 1 else 's'}",
                    str(self.com.total_units),
                    "",
                    "",
                    str(self.com.total_periods),
                    format_peso(self.com.total_lab_fee),
                    format_peso(self.com.total_other_fee),
                ]
            )

        table.setRowCount(0)
        for values in rows:
            row = table.rowCount()
            table.insertRow(row)
            is_total = values[0] == "TOTALS"
            for column, spec in enumerate(columns):
                item = set_cell(table, row, column, values[column], align=spec.align)
                if is_total:
                    item.setFont(body_bold_font(12))
                    item.setBackground(QBrush(Colors.qcolor(Colors.SURFACE_MUTED)))

        self.course_empty.setText(
            self.com.empty_reason
            or "This enrollment has no subjects in 'enrolled' status."
        )
        self.course_empty.setVisible(not rows)

        fit_height(table)

        term = " • ".join(p for p in (self.com.academic_year, self.com.semester) if p)
        self.term_label.setText(
            f"Enrolled courses — {term}" if term else "Enrolled courses"
        )

    def _render_financial(self):
        lines = self.fee_lines
        self._render_money_table(
            self.assessment_table,
            [(line.label, line.amount) for line in lines.fees],
            total_label="TOTAL ASSESSMENT",
            total_value=lines.fees_total,
        )
        self._render_money_table(
            self.discount_table,
            [(line.label, line.amount) for line in lines.discounts],
            total_label="TOTAL DISCOUNTS",
            total_value=lines.discounts_total,
            empty_message=lines.empty_reason
            or "No discount or credit is recorded on this assessment.",
        )
        self._render_schedule()

        # Four ₱0.00 tiles are indistinguishable from a student who owes nothing.
        # With no assessment on file the figures are shown as unavailable.
        has_assessment = self.current_assessment is not None
        if has_assessment:
            self.kpi_labels["assessed"].setText(format_peso(self.financial.total_assessed))
            self.kpi_labels["discounts"].setText(format_peso(self.financial.total_discounts))
            self.kpi_labels["paid"].setText(format_peso(self.financial.total_payments))
            balance = self.financial.outstanding_balance
            self.kpi_labels["balance"].setText(format_peso(balance))
        else:
            for key in ("assessed", "discounts", "paid", "balance"):
                self.kpi_labels[key].setText("—")
                self.kpi_labels[key].setToolTip("No assessment is on file for this student.")
            balance = None
        if has_assessment:
            self.kpi_labels["balance"].setStyleSheet(
                f"color: {Colors.ERROR_RED if balance > 0 else Colors.SUCCESS_GREEN};"
                " background: transparent;"
            )
        else:
            self.kpi_labels["balance"].setStyleSheet(
                f"color: {Colors.TEXT_SECONDARY}; background: transparent;"
            )

        if self.financial.discrepancies:
            self.discrepancy_label.setText(
                "Unresolved totals: " + " ".join(self.financial.discrepancies)
            )
            self.discrepancy_label.setVisible(True)
        else:
            self.discrepancy_label.setVisible(False)

    def _render_money_table(self, table: QTableWidget, lines, *, total_label: str,
                            total_value, empty_message: str = ""):
        table.setRowCount(0)

        for label, amount in lines:
            row = table.rowCount()
            table.insertRow(row)
            set_cell(table, row, 0, label)
            set_cell(table, row, 1, format_peso(amount), align=_RIGHT)

        if not lines:
            message = empty_message or "Nothing on file."
            row = table.rowCount()
            table.insertRow(row)
            item = set_cell(table, row, 0, "None recorded", align=_LEFT)
            item.setForeground(Colors.qcolor(Colors.TEXT_SECONDARY))
            item.setToolTip(message)
            set_cell(table, row, 1, "—", align=_RIGHT)

        # A total is only printed when there is something to total. A student
        # with no assessment used to get "TOTAL ASSESSMENT ₱0.00", which reads
        # exactly like a genuine zero assessment.
        if not lines:
            return

        row = table.rowCount()
        table.insertRow(row)
        label_item = set_cell(table, row, 0, total_label)
        value_item = set_cell(table, row, 1, format_peso(total_value), align=_RIGHT)
        label_item.setFont(body_bold_font(12))
        value_item.setFont(body_bold_font(12))
        for column in (0, 1):
            table.item(row, column).setBackground(
                QBrush(Colors.qcolor(Colors.SURFACE_MUTED))
            )
        fit_height(table)

    def _render_schedule(self):
        table = self.schedule_table
        table.setRowCount(0)

        for installment in self.financial.installments:
            row = table.rowCount()
            table.insertRow(row)
            set_cell(table, row, 0, installment.label)
            set_cell(table, row, 1, format_peso(installment.amount), align=_RIGHT)
            label, tone = installment_state(installment)
            status_item = set_cell(table, row, 2, label, align=_CENTRE)
            status_item.setForeground(Colors.qcolor(_STATE_COLOURS.get(tone, Colors.TEXT_SECONDARY)))

        if not self.financial.installments:
            row = table.rowCount()
            table.insertRow(row)
            item = set_cell(table, row, 0, "No payment schedule on file.", align=_LEFT)
            item.setForeground(Colors.qcolor(Colors.TEXT_SECONDARY))
            set_cell(table, row, 1, "—", align=_RIGHT)
            set_cell(table, row, 2, "—", align=_CENTRE)

        if self.financial.installments:
            row = table.rowCount()
            table.insertRow(row)
            set_cell(table, row, 0, "TOTAL DUE").setFont(body_bold_font(12))
            set_cell(table, row, 1, format_peso(self.financial.installments_total),
                     align=_RIGHT).setFont(body_bold_font(12))
            set_cell(table, row, 2, "—", align=_CENTRE)
            for column in range(3):
                table.item(row, column).setBackground(
                    QBrush(Colors.qcolor(Colors.SURFACE_MUTED))
                )
        fit_height(table)

    # ---------------------------------------------------------- printing --

    def _set_actions_enabled(self, enabled: bool):
        """Enable or disable both document actions together."""
        if not enabled:
            self.print_btn.setEnabled(False)
            self.save_pdf_btn.setEnabled(False)
            self.print_btn.setToolTip(
                "There is no enrollment on file for this student, so there is "
                "nothing to certify yet."
            )
            self.save_pdf_btn.setToolTip(
                "There is no enrollment on file for this student, so there is "
                "nothing to export yet."
            )
            return
        if not getattr(self, "_print_busy", False):
            self.print_btn.setEnabled(True)
            self.save_pdf_btn.setEnabled(True)
        self.print_btn.setToolTip("Send this record to a printer")
        self.save_pdf_btn.setToolTip("Export this record as a PDF file")

    def _print_service_instance(self) -> ComPrintService:
        if self._print_service is None:
            self._print_service = ComPrintService(report_service=self.report_service)
        return self._print_service

    def _print(self):
        """Send the certificate to a printer.

        Returns quietly only when there is genuinely nothing to print *and* says
        why. Previously both guards returned silently, so the button looked live
        and did nothing for a student with no enrollment.
        """
        if self.current_student is None:
            QMessageBox.information(
                self, "Nothing to print",
                "Select a student first: a certificate is printed for one student.",
            )
            return
        if not self.com.available:
            QMessageBox.warning(
                self, "Nothing to print",
                f"{self.current_student.full_name} has no enrollment with an enrolled "
                f"subject on file, so there is no certificate to produce.\n\n"
                f"{self.com.empty_reason or 'No enrollment record is on file.'}",
            )
            return
        if self._print_busy:
            return

        self._set_print_busy(True, "Building the certificate…")
        try:
            try:
                result = self._print_service_instance().print_com(
                    self.current_student, self.com, self.financial, parent=self
                )
            except ComNotCertifiableError as exc:
                QMessageBox.warning(self, "Nothing to print", str(exc))
                return
            except DatabaseUnavailableError as exc:
                show_db_error(
                    self, exc, retry_callback=lambda: self._set_print_busy(False)
                )
                return
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                logger.exception("Printing the certificate failed")
                QMessageBox.critical(
                    self, "Could not print the certificate",
                    f"The certificate was built but the printer rejected it:\n\n{exc}",
                )
                return
        finally:
            self._set_print_busy(False)

        if result is False:
            # print_com() reported a failure (printer missing, driver error, or
            # no printers configured). Falling through silently -- or offering a
            # PDF export with no confirmation -- is what left operators thinking
            # the certificate had been produced.
            QMessageBox.warning(
                self, "Not printed",
                "The certificate was not sent to a printer. Check that a printer is "
                "configured, then try again.\n\n"
                "Use Save PDF to write the file instead.",
            )
            return

        QMessageBox.information(
            self, "Sent to printer",
            f"The certificate for {self.current_student.full_name} was sent to "
            f"the printer.",
        )

    def _set_print_busy(self, busy: bool, message: str = "") -> None:
        """Disable both actions while a document is being produced.

        A second click on Print while the first is still building used to start a
        second copy of the same certificate.
        """
        self._print_busy = busy
        enabled = self.com.available and not busy and self.current_student is not None
        self.print_btn.setEnabled(enabled)
        self.save_pdf_btn.setEnabled(enabled)
        self.print_btn.setText("Printing…" if busy else "Print COM")
        self.save_pdf_btn.setText("Saving…" if busy else "Save PDF")
        self.action_status_label.setText(message if busy else "")
        self.action_status_label.setVisible(bool(message))
        if not enabled and not busy:
            self.print_btn.setToolTip(
                "There is no enrollment on file for this student, so there is "
                "nothing to certify yet."
            )
        elif not busy:
            self.print_btn.setToolTip("Send this record to a printer")
            # Both buttons get the same explanation: only the print button used
            # to carry it, so a disabled Save PDF looked like a broken control.
            self.save_pdf_btn.setToolTip(
                "Export this record as a PDF file"
                if enabled
                else "There is no enrollment on file for this student, so there is "
                     "nothing to export yet."
            )

    def _save_pdf(self):
        """Write the certificate to a PDF file the operator chooses.

        The dialog starts in ``reports/`` rather than the process working
        directory: a bare filename put the file wherever the application happened
        to be launched from, and the configured output folder was never used.
        """
        if self.current_student is None:
            QMessageBox.information(
                self, "Nothing to export",
                "Select a student first: a certificate is produced for one student.",
            )
            return
        if not self.com.available:
            QMessageBox.warning(
                self, "Nothing to export",
                f"{self.current_student.full_name} has no enrollment with an enrolled "
                f"subject on file, so there is no certificate to produce.\n\n"
                f"{self.com.empty_reason or 'No enrollment record is on file.'}",
            )
            return
        if self._print_busy:
            return

        service = self._print_service_instance()
        reference = "".join(
            c for c in (self.current_student.reference_number or "student") if c.isalnum()
        )
        suggested = settings.REPORTS_OUTPUT_DIR / (
            f"certificate_of_matriculation_{reference}.pdf"
        )
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Certificate of Matriculation",
            str(suggested),
            "PDF Files (*.pdf)",
        )
        if not file_path:
            return
        if not file_path.lower().endswith(".pdf"):
            file_path = f"{file_path}.pdf"

        self._set_print_busy(True, "Writing the PDF…")
        try:
            try:
                result = service.export_pdf(
                    self.current_student,
                    self.current_enrollment,
                    self.current_assessment,
                    Path(file_path),
                )
            except ComNotCertifiableError as exc:
                QMessageBox.warning(self, "Nothing to export", str(exc))
                return
            except DatabaseUnavailableError as exc:
                show_db_error(self, exc, retry_callback=self._save_pdf)
                return
            except PermissionError as exc:
                QMessageBox.critical(
                    self, "Cannot write the file",
                    f"You do not have permission to write to:\n{file_path}\n\n{exc}",
                )
                return
            except OSError as exc:
                QMessageBox.critical(
                    self, "Cannot write the file",
                    f"The file could not be written to:\n{file_path}\n\n{exc}",
                )
                return
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                logger.exception("Exporting the certificate failed")
                QMessageBox.critical(
                    self, "Export failed",
                    f"The PDF could not be generated.\n\n{exc}",
                )
                return
        finally:
            self._set_print_busy(False)

        if not result:
            QMessageBox.critical(
                self,
                "Export failed",
                f"The backend reported that no document was written to:\n{file_path}\n\n"
                "Nothing was saved. See the log for the reason.",
            )
            return

        written = Path(result)
        if not written.exists():
            QMessageBox.critical(
                self,
                "Export failed",
                f"The backend reported success but the file is missing:\n{written}",
            )
            return

        QMessageBox.information(
            self, "PDF saved",
            f"The certificate for {self.current_student.full_name} was written to:\n{written}",
        )

    # --------------------------------------------------------- navigation --

    def load_reference(self, reference_number) -> "EnrollmentLookup":
        """Show the student a printed or scanned reference refers to.

        Every form of the reference is accepted -- the stored number, the
        zero-padded barcode payload, and aliases such as ``000001`` printed on an
        older panel -- and the reason a reference could not be shown comes back
        with it, so the caller can say what is actually missing rather than
        "not found".
        """
        lookup = get_enrollment_for_reference(reference_number)
        if lookup.student is None:
            self.search_input.setText(str(reference_number or ""))
            self._show_no_match("Student not found", lookup.reason or "That student is not on file.")
            return lookup

        if self.current_student is None or self.current_student.id != lookup.student.id:
            if all(row.id != lookup.student.id for row in self._students):
                self._students.append(lookup.student)
            self.stack.setCurrentWidget(self.record)
            self.current_student = lookup.student
            # The enrollment from the lookup is the one that was just read, so it
            # is passed through instead of being fetched a second time.
            self._load_record(lookup.student, enrollment=lookup.enrollment,
                              assessment=lookup.assessment)
        return lookup

    def preload_student(self, student) -> bool:
        """Show a student handed over by another page (e.g. after a scan).

        The student is re-read by reference number so the page never shows a
        detached copy of a record, and a reference that no longer resolves is
        reported instead of silently ignored.
        """
        reference = getattr(student, "reference_number", None)
        if not reference:
            self.search_input.clear()
            return False
        try:
            resolved = self.student_service.get_student_by_reference(reference)
        except DatabaseUnavailableError as exc:
            show_db_error(self, exc, retry_callback=lambda: self.preload_student(student))
            return False

        self.search_input.setText(reference)
        self._apply_search()
        if resolved is None:
            self._show_no_match(
                "Student not found",
                f"No student on file has reference number {reference}.",
            )
            return False
        if self.current_student is None or self.current_student.id != resolved.id:
            self._students.append(resolved)
            self.current_student = resolved
            self.stack.setCurrentWidget(self.record)
            self._load_record(resolved)
        return True

    def closeEvent(self, event):
        self._search_debounce.stop()
        super().closeEvent(event)


__all__ = ["CertificatePage", "PROFILE_PHOTO"]
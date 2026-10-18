"""Post-scan result modal: structured student profile, COM and financial tabs.

Replaces the previous flat listing with three deliberate regions:

1. a student header carrying the verified identity (name, reference number,
   program, year level and a verified indicator),
2. an **Enrolled Subjects (COM)** tab whose column count, unit total, subject
   count and lab-fee total are computed from the rows actually shown, and
3. a **Financial Summary** tab with the four KPI figures and the installment
   schedule, each clearly separated into assessed / paid / outstanding.

Every figure comes from the student record handed in by
:class:`~services.student_service.StudentService`; nothing about the student is
hardcoded here. When a record is missing the corresponding tab says so instead
of rendering an empty table that reads like "zero pesos owed".

The modal owns no navigation. It emits signals; the scanner page decides what to
do with them, which keeps the existing page routing and permission rules in
one place.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Optional

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from models.assessment import Assessment
from models.barcode import Barcode
from models.enrollment import Enrollment
from models.student import Student
from services.com_summary import (
    ComSummary,
    FinancialSummary,
    format_peso,
    summarize_enrollment,
    summarize_fee_lines,
    summarize_financials,
)
from services.com_print_service import (
    ComNotCertifiableError,
    ComPrintService,
    certifiable,
    official_status,
)
from styles.colors import Colors
from ui.widgets.profile_card import StudentProfileCard
from ui.widgets.profile_image import apply_portrait, identifier_conflict
from ui.widgets.flow_row import FlowRow
from ui.widgets.responsive_table import (
    ColumnSpec,
    configure_table,
    count_column,
    fit_height,
    money_column,
    set_cell,
)
from styles.typography import (
    body_bold_font,
    body_font,
    button_font,
    get_agrandir_family,
    monospace_font,
    page_heading_font,
    section_heading_font,
    small_label_font,
)

logger = logging.getLogger(__name__)


class KpiCard(QFrame):
    """A single labelled figure. Compact enough for four across a dialog."""

    #: Narrowest a card may be drawn. The four of them plus their gaps used to
    #: demand 690 px, which became the whole dialog body's minimum width: the
    #: Financial Summary tab is built even while the Enrolled Subjects tab is the
    #: one on screen, so its minimum leaked into the enclosing scroll area and put
    #: a horizontal scrollbar under a panel that has no wide content at all. The
    #: titles and the peso values both fit inside this; the footnotes below them
    #: already word-wrap.
    MIN_WIDTH = 150

    def __init__(self, title: str, value: str, tone: str = Colors.DARK_BURNT_ORANGE,
                 footnote: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("kpiCard")
        self.setMinimumWidth(self.MIN_WIDTH)
        self.setStyleSheet(
            f"""
            QFrame#kpiCard {{
                background-color: {Colors.CARD_BG};
                border: 1px solid {Colors.CARD_BORDER};
                border-left: 4px solid {tone};
                border-radius: 8px;
            }}
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)

        self.value_label = QLabel(value)
        self.value_label.setFont(body_bold_font(18))
        self.value_label.setStyleSheet(f"color: {tone};")
        self.value_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        title_label = QLabel(title)
        title_label.setFont(small_label_font(10))
        title_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        title_label.setWordWrap(True)

        layout.addWidget(self.value_label)
        layout.addWidget(title_label)

        if footnote:
            note = QLabel(footnote)
            note.setFont(small_label_font(9))
            note.setStyleSheet(f"color: {Colors.LIGHT_TEXT};")
            note.setWordWrap(True)
            layout.addWidget(note)

    def update_value(self, value: str, footnote: str = "") -> None:
        self.value_label.setText(value)
        if footnote:
            self.value_label.setToolTip(footnote)


class ScanResultDialog(QDialog):
    """Structured detail panel shown after a successful scan.

    Parameters
    ----------
    student:
        The verified student. Required for the success presentation.
    enrollment:
        Latest enrollment, when one exists. ``None`` renders an explicit
        no-data state instead of an empty table.
    assessment:
        Latest assessment, when one exists.
    com_summary / financial_summary:
        Pre-computed aggregates. Left ``None`` they are derived from
        ``enrollment`` / ``assessment``.
    """

    #: Emitted with the verified student when the operator asks to continue to
    #: the fees module. The scanner page performs the navigation.
    assessment_requested = Signal(object)
    #: Emitted with the verified student when the operator asks for the record.
    profile_requested = Signal(object)
    #: Emitted when the operator finishes with this scan and wants the camera.
    scan_next_requested = Signal()
    #: Emitted with the destination path after a successful PDF export.
    exported = Signal(str)

    def __init__(
        self,
        student: Student = None,
        enrollment: Enrollment = None,
        assessment: Assessment = None,
        barcode: Barcode = None,
        scanned_value: str = "",
        success: bool = True,
        com_summary: Optional[ComSummary] = None,
        financial_summary: Optional[FinancialSummary] = None,
        print_service: Optional[ComPrintService] = None,
        loading: bool = False,
        load_error: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.student = student
        self.enrollment = enrollment
        self.assessment = assessment
        self.barcode = barcode
        self.scanned_value = scanned_value
        self.success = success
        #: True while enrollment/assessment records are still being fetched.
        #: The panel shows a loading state instead of pretending to know the
        #: answer; :meth:`apply_records` replaces it once the lookup lands.
        self.loading = loading
        self.load_error = load_error

        self.com_summary = com_summary or summarize_enrollment(enrollment)
        self.financial_summary = financial_summary or summarize_financials(assessment)
        # The same grouped breakdown the certificate page shows, so the
        # printed assessment cannot disagree with the screen it was printed
        # from.
        self.fee_lines = summarize_fee_lines(assessment)
        self.official = official_status(self.com_summary, self.financial_summary)
        self._print_service = print_service
        self._close_button: Optional[QPushButton] = None
        #: The widget currently holding the student card and the tabs, so the
        #: body can be swapped without rebuilding the header or the actions.
        self._body: Optional[QWidget] = None

        self.setWindowTitle("Scan Result")
        self.setModal(True)
        # A hard 560x560 floor made the panel unusable on a small window even
        # though its body already lives in a resizable QScrollArea. Keep only a
        # modest floor so the dialog cannot collapse, and clamp the preferred
        # size to the screen actually available.
        self.setMinimumSize(360, 360)
        self._height_fitted = False
        self._resize_to_screen()
        self.setObjectName("scanResultDialog")

        self._setup_ui()

    def _resize_to_screen(self) -> None:
        """Prefer 760x720, but never larger than the available screen."""
        width, height = 760, 720
        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            # Leave a margin so the title bar and window frame stay on screen.
            width = min(width, int(available.width() * 0.92))
            height = min(height, int(available.height() * 0.92))
        self.resize(max(width, self.minimumSize().width()),
                    max(height, self.minimumSize().height()))

    def _fit_height_to_content(self) -> None:
        """Ask the screen for the height this record needs, within reason.

        The height comes from the scroll area's *content*, not from the dialog's
        layout: the QScrollArea reports its own (small) size hint however much
        its content has grown, so measuring the dialog would always conclude that
        nothing needs to be bigger.
        """
        scroll = self.findChild(QScrollArea)
        if scroll is None or scroll.widget() is None:
            return
        needed = scroll.widget().sizeHint().height() + self.sizeHint().height()
        ceiling = self.height()
        screen = QApplication.primaryScreen()
        if screen is not None:
            ceiling = int(screen.availableGeometry().height() * 0.92)
        target = min(max(needed, self.minimumSize().height()), ceiling)
        if target > self.height():
            self.resize(self.width(), target)

    def showEvent(self, event):
        """Focus the close button, and grow once so the totals row is on screen.

        ``setFocus`` during construction is discarded because the widget is not
        visible yet, which left a keyboard operator with no focused control.

        The resize runs once, after the first layout pass. The subjects table
        sizes itself to its rows, so the dialog can ask for exactly the height it
        needs -- up to the screen -- instead of leaving the totals row below the
        fold of a scroll area the operator has to discover.
        """
        super().showEvent(event)
        if self._close_button is not None and not self._close_button.hasFocus():
            self._close_button.setFocus(Qt.FocusReason.TabFocusReason)

        if not self._height_fitted:
            self._height_fitted = True
            QTimer.singleShot(0, self._fit_height_to_content)

    # -- construction ----------------------------------------------------

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        main_layout.addWidget(self._build_header())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameStyle(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(16)

        if self.success and self.student:
            content_layout.addWidget(self._build_student_card())
            self._body = self._build_body()
            content_layout.addWidget(self._body)
        else:
            content_layout.addWidget(self._build_not_found())

        scroll.setWidget(content)
        main_layout.addWidget(scroll, 1)
        main_layout.addWidget(self._build_actions())

        # Keyboard: Escape closes, and focus starts on the close button rather
        # than nowhere, so a keyboard operator is never stranded.
        self._close_button.setFocus(Qt.FocusReason.TabFocusReason)

    def _build_body(self) -> QWidget:
        """The COM / Financial tabs, or the loading or failure state."""
        if self.loading:
            return self._build_loading()
        return self._build_tabs()

    def _build_loading(self) -> QWidget:
        """Placeholder shown while enrollment/assessment records are fetched.

        Deliberately says nothing about subjects or money: showing an empty
        table would read as "no subjects", which is a different and wrong claim
        while the lookup is still in flight.
        """
        panel = QFrame()
        panel.setObjectName("loadingPanel")
        panel.setStyleSheet(
            f"""
            QFrame#loadingPanel {{
                background-color: {Colors.WHITE};
                border: 1px solid {Colors.CARD_BORDER};
                border-radius: 8px;
            }}
            """
        )
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(8)

        spinner = QLabel()
        spinner.setObjectName("loadingSpinner")
        spinner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spinner.setText("Loading enrolled subjects and fees…")
        spinner.setFont(body_bold_font(12))
        spinner.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        spinner.setAccessibleName("Loading student records")
        layout.addWidget(spinner)

        hint = QLabel("The student's identity is already verified.")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setFont(small_label_font(10))
        hint.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        layout.addWidget(hint)

        # Animate only while genuinely loading, so the panel stops moving once
        # the records land or the lookup fails.
        self._loading_spinner = spinner
        self._loading_timer = QTimer(self)
        self._loading_timer.setInterval(700)
        self._loading_timer.timeout.connect(self._pulse_loading_text)
        self._loading_phase = 0
        self._loading_timer.start()
        return panel

    def _pulse_loading_text(self) -> None:
        """Simple text-based activity indicator (no extra asset needed)."""
        self._loading_phase = (self._loading_phase + 1) % 4
        dots = "." * self._loading_phase
        self._loading_spinner.setText(f"Loading enrolled subjects and fees{dots}")

    def _stop_loading_animation(self) -> None:
        if getattr(self, "_loading_timer", None) is not None:
            self._loading_timer.stop()

    def apply_records(
        self,
        enrollment: Optional[Enrollment] = None,
        assessment: Optional[Assessment] = None,
        error: str = "",
    ) -> None:
        """Swap the loading state for the real records once the lookup lands.

        Called from the GUI thread with whatever the background lookup produced.
        ``error`` is a human-readable failure; it is shown as a failure, never
        as an empty result, so a database outage cannot be misread as a student
        with no subjects.
        """
        self._stop_loading_animation()
        self.loading = False
        self.enrollment = enrollment
        self.assessment = assessment
        self.load_error = error

        self.com_summary = summarize_enrollment(enrollment)
        self.financial_summary = summarize_financials(assessment)
        self.fee_lines = summarize_fee_lines(assessment)
        if error:
            self.com_summary = replace(
                self.com_summary, empty_reason=error
            )
            self.financial_summary = replace(
                self.financial_summary, empty_reason=error
            )
        self.official = official_status(self.com_summary, self.financial_summary)
        if getattr(self, "export_btn", None) is not None:
            self._sync_export_button()

        if self._body is None:
            return

        parent_layout = self._body.parentWidget().layout()
        if parent_layout is None:
            return
        index = parent_layout.indexOf(self._body)
        parent_layout.removeWidget(self._body)
        # removeWidget only unhooks the layout; the widget keeps its geometry and
        # stays visible as a child, so it would keep painting the stale loading
        # state until the deferred delete ran. Detach it from the parent first so
        # it is off screen the instant it is swapped out.
        self._body.setParent(None)
        self._body.deleteLater()

        self._body = self._build_tabs() if not error else self._build_load_error(error)
        parent_layout.insertWidget(index, self._body)

    def _build_load_error(self, message: str) -> QWidget:
        panel = QFrame()
        panel.setObjectName("loadErrorPanel")
        panel.setStyleSheet(
            f"""
            QFrame#loadErrorPanel {{
                background-color: {Colors.WHITE};
                border: 1px solid {Colors.ERROR_RED};
                border-radius: 8px;
            }}
            """
        )
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(8)

        title = QLabel("Could not load subjects and fees")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(body_bold_font(12))
        title.setStyleSheet(f"color: {Colors.ERROR_RED};")
        title.setAccessibleName("Could not load subjects and fees")
        layout.addWidget(title)

        detail = QLabel(message)
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail.setWordWrap(True)
        detail.setFont(small_label_font(10))
        detail.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        layout.addWidget(detail)

        note = QLabel(
            "The verified identity above is unaffected. Close this panel and "
            "scan again to retry."
        )
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        note.setWordWrap(True)
        note.setFont(small_label_font(10))
        note.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        layout.addWidget(note)
        return panel

    def _build_header(self) -> QFrame:
        """Verified banner: a neutral bar carrying one high-contrast status chip.

        The status is carried by a word and a glyph as well as by colour -- a
        saturated green or red block behind white text was both harsh and, at
        this contrast, hard to read; a pale tint with dark text is legible and
        leaves the institution's orange free for the interface accents.
        """
        header = QFrame()
        header.setObjectName("verificationBanner")
        header.setMinimumHeight(84)
        # Scoped by object name on purpose: QLabel inherits QFrame, so an
        # unscoped `QFrame { border-bottom }` rule also draws a rule under every
        # label in the banner -- a line straight through the title.
        header.setStyleSheet(
            f"""
            QFrame#verificationBanner {{
                background-color: {Colors.SURFACE};
                border-bottom: 1px solid {Colors.BORDER};
            }}
            """
        )
        # A widget holds one layout: the row is built without a parent and then
        # nested in the column, rather than installing two layouts on the header
        # (the second call silently replaces the first, which cost this banner
        # its status line).
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)

        if self.success:
            title = QLabel("Student Verified")
            chip_text, chip_fg, chip_bg = "✓  VERIFIED", "#166534", Colors.SUCCESS_BG
        else:
            title = QLabel("Barcode Not Recognised")
            chip_text, chip_fg, chip_bg = "✕  NOT FOUND", "#991B1B", Colors.ERROR_BG

        title.setFont(page_heading_font(19))
        title.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")
        row.addWidget(title)
        row.addStretch()

        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(24, 10, 24, 10)
        header_layout.setSpacing(2)
        header_layout.addLayout(row)

        payload = self.scanned_value or getattr(self.student, "barcode_payload", "") or ""
        subtitle = QLabel(
            f"Status: {'Match Found' if self.success else 'No match'}  •  "
            f"Barcode: {payload}" if payload else
            f"Status: {'Match Found' if self.success else 'No match'}"
        )
        subtitle.setFont(small_label_font(11))
        subtitle.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; background: transparent;"
        )
        header_layout.addWidget(subtitle)

        if self.success:
            # The badge reports whether the identity is confirmed. A record whose
            # barcode payload and reference number disagree is shown, but never
            # labelled VERIFIED.
            conflict = identifier_conflict(self.student)
            if conflict:
                chip_text = "⚠  UNRESOLVED RECORD"
                chip_fg = Colors.WARNING_ORANGE
                chip_bg = Colors.WARNING_BG
                self._status_accessible = f"Unresolved record. {conflict}"
            else:
                self._status_accessible = "Student verified"
        else:
            self._status_accessible = "Barcode not recognised"

        chip = QLabel(chip_text)
        chip.setFont(small_label_font(11))
        chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chip.setStyleSheet(
            f"""
            QLabel {{
                background-color: {chip_bg};
                color: {chip_fg};
                border-radius: 11px;
                padding: 5px 14px;
                font-weight: 600;
            }}
            """
        )
        chip.setAccessibleName(self._status_accessible)
        row.addWidget(chip)

        return header

    def _build_student_card(self) -> QFrame:
        """The brief profile card, rendered by the shared component.

        The same :class:`~ui.widgets.profile_card.StudentProfileCard` the
        certificate page uses, with the two extra rows this surface needs (the
        scanned payload and the student ID). Building it here as well is what
        let the two drift apart in the first place.
        """
        self.profile_card = StudentProfileCard(size=84, show_status=False)
        self.profile_card.add_detail("program", "Program & Year")
        self.profile_card.add_detail("reference", "Reference Number")
        self.profile_card.add_detail("student_id", "Student ID")
        self.profile_card.add_detail("barcode", "Barcode")

        if self.student is not None:
            self.profile_card.set_student(
                self.student,
                extra_details=(
                    ("student_id", "Student ID"),
                    ("barcode", "Barcode"),
                ),
            )
            self.profile_card._value_labels["reference"].setText(
                f"{self.student.reference_number or '—'}"
            )
        return self.profile_card

    def _build_tabs(self) -> QTabWidget:
        tabs = QTabWidget()
        tabs.setAccessibleName("Student details")
        # Active tab: white surface, dark text, an orange rule marking the
        # selection. Inactive tabs stay neutral rather than pale-filled, so
        # "which tab am I on" does not depend on a subtle fill difference.
        tabs.setStyleSheet(
            f"""
            QTabWidget::pane {{ border: 1px solid {Colors.BORDER};
                                border-radius: 8px; background: {Colors.SURFACE}; }}
            QTabBar::tab {{ background: {Colors.SURFACE_MUTED};
                            color: {Colors.TEXT_SECONDARY};
                            padding: 10px 20px;
                            border: 1px solid {Colors.BORDER};
                            border-bottom: 2px solid {Colors.BORDER};
                            border-top-left-radius: 6px; border-top-right-radius: 6px;
                            font-weight: 600; }}
            QTabBar::tab:hover {{ background: {Colors.ACCENT_PALE};
                                  color: {Colors.TEXT_PRIMARY}; }}
            QTabBar::tab:focus {{ border: 2px solid {Colors.ACCENT}; }}
            QTabBar::tab:selected {{ background: {Colors.SURFACE};
                                     color: {Colors.TEXT_PRIMARY};
                                     border-bottom: 3px solid {Colors.ACCENT}; }}
            """
        )
        count = self.com_summary.subject_count
        tabs.addTab(self._build_com_tab(), f"Enrolled Subjects ({count})")
        tabs.addTab(self._build_financial_tab(), "Financial Summary")
        return tabs

    # -- tab 1: enrolled subjects ---------------------------------------

    def _build_com_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        summary = self.com_summary
        period = "  •  ".join(p for p in (summary.academic_year, summary.semester) if p)
        meta = QLabel(period or "No academic term on record")
        meta.setFont(small_label_font(11))
        meta.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        layout.addWidget(meta)

        if not summary.available:
            layout.addWidget(self._empty_state(summary.empty_reason or "No data."))
            return tab

        # The table sits on a light surface with its own padding, inside the
        # tab's own container: the earlier dark wrapper around it read as a
        # second application window inside the dialog.
        frame = QFrame()
        frame.setObjectName("comTableFrame")
        frame.setStyleSheet(
            f"QFrame#comTableFrame {{ background-color: {Colors.SURFACE};"
            f" border: 1px solid {Colors.BORDER}; border-radius: 10px; }}"
        )
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(12, 12, 12, 12)
        frame_layout.setSpacing(0)

        table = QTableWidget(summary.subject_count + 1, 6)
        table.setObjectName("comSubjectTable")
        # Shared sizing rules: the subject column absorbs the spare width, the
        # rest fit their content, and a long list scrolls instead of squashing
        # every cell into an ellipsis.
        configure_table(
            table,
            [
                ColumnSpec("Code", width=110),
                ColumnSpec("Subject", width=170, stretch=True),
                count_column("Units", 80),
                ColumnSpec("Schedule", width=180),
                ColumnSpec("Room", width=100),
                money_column("Lab Fee", 120),
            ],
        )

        for row_index, row in enumerate(summary.rows):
            set_cell(table, row_index, 0, row.code, align=Qt.AlignmentFlag.AlignLeft)
            set_cell(table, row_index, 1, row.subject, align=Qt.AlignmentFlag.AlignLeft)
            set_cell(table, row_index, 2, str(row.units), align=Qt.AlignmentFlag.AlignCenter)
            set_cell(table, row_index, 3, row.schedule or "—", align=Qt.AlignmentFlag.AlignLeft)
            set_cell(table, row_index, 4, row.room or "—", align=Qt.AlignmentFlag.AlignLeft)
            # A subject with no laboratory fee shows "—", not ₱0.00: on an
            # official-looking panel a peso figure reads as a recorded charge.
            fee_cell = set_cell(
                table, row_index, 5, format_peso(row.lab_fee, blank_for_zero=True),
                align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )
            if not row.lab_fee:
                fee_cell.setToolTip("No laboratory fee recorded for this subject.")

        # The table was built with subject_count + 1 rows: the last one is
        # reserved for the totals. It has to be addressed by that reserved index
        # rather than by rowCount(), because QTableWidget.setItem() silently
        # discards an item whose row is outside the current range -- the totals
        # row would come out blank and the COM would print no totals at all.
        total_row = summary.subject_count
        totals = [
            ("Total", Qt.AlignmentFlag.AlignLeft),
            (f"{summary.subject_count} Subject{'s' if summary.subject_count != 1 else ''}",
             Qt.AlignmentFlag.AlignLeft),
            (str(summary.total_units), Qt.AlignmentFlag.AlignCenter),
            ("", Qt.AlignmentFlag.AlignLeft),
            ("", Qt.AlignmentFlag.AlignLeft),
            (format_peso(summary.total_lab_fee), Qt.AlignmentFlag.AlignRight),
        ]
        for column, (text, alignment) in enumerate(totals):
            item = set_cell(
                table, total_row, column, text,
                align=alignment | Qt.AlignmentFlag.AlignVCenter,
            )
            # Dark, semibold text on a pale orange band: the earlier white-on-
            # cream treatment was the least readable row on the screen.
            item.setFont(body_bold_font(12))
            item.setBackground(Colors.qcolor(Colors.ACCENT_PALE))

        # Fits the table to its rows (capped, so a long list still scrolls) so
        # the totals row is not pushed below the fold of a short dialog.
        fit_height(table, max_visible_rows=14)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        frame_layout.addWidget(table)
        layout.addWidget(frame, 1)
        layout.addStretch()

        caption = QLabel(
            f"{summary.subject_count} subject(s)   •   {summary.total_units} units   •   "
            f"laboratory fees {format_peso(summary.total_lab_fee)}   "
            "(totals computed from the rows above)"
        )
        caption.setFont(small_label_font(10))
        caption.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        caption.setWordWrap(True)
        layout.addWidget(caption)
        return tab

    # -- tab 2: financial summary ---------------------------------------

    def _build_financial_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        summary = self.financial_summary
        if not summary.available:
            layout.addWidget(self._empty_state(summary.empty_reason or "No assessment."))
            return tab

        period = "  •  ".join(p for p in (summary.academic_year, summary.semester) if p)
        meta = QLabel(f"{period}   •   status: {summary.status or 'unknown'}")
        meta.setFont(small_label_font(11))
        meta.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        layout.addWidget(meta)

        # A FlowRow rather than a fixed grid: the four cards fill the row on a
        # wide panel and fold onto two rows on a narrow one, instead of forcing
        # the whole dialog body -- including the Enrolled Subjects tab that is
        # usually the one on screen -- to be at least four cards wide.
        cards = FlowRow(spacing=10, stretch=True)
        cards.addWidget(
            KpiCard("Tuition Fee", format_peso(summary.tuition_fee), Colors.ORANGE)
        )
        cards.addWidget(
            KpiCard(
                "Laboratory Fee",
                format_peso(summary.laboratory_fee),
                Colors.CORAL_ORANGE,
                "From the enrolled subjects",
            )
        )
        cards.addWidget(
            KpiCard(
                "Total Assessed Fees",
                format_peso(summary.total_assessed),
                Colors.DARK_BURNT_ORANGE,
                "Tuition + laboratory + other fees",
            )
        )
        cards.addWidget(
            KpiCard(
                "Balance Due",
                format_peso(summary.outstanding_balance),
                Colors.ERROR_RED if summary.outstanding_balance > 0 else Colors.SUCCESS_GREEN,
                f"After {format_peso(summary.total_payments)} received",
            )
        )
        layout.addWidget(cards)

        if summary.other_fees != 0:
            other = QLabel(f"Includes other fees of {format_peso(summary.other_fees)}.")
            other.setFont(small_label_font(10))
            other.setStyleSheet(f"color: {Colors.LIGHT_TEXT};")
            layout.addWidget(other)

        received = QLabel(
            f"Assessed {format_peso(summary.total_assessed)}"
            f"  −  discounts {format_peso(summary.total_discounts)}"
            f"  −  payments received {format_peso(summary.total_payments)}"
            f"  =  balance {format_peso(summary.outstanding_balance)}"
        )
        received.setFont(small_label_font(10))
        received.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        received.setWordWrap(True)
        layout.addWidget(received)

        heading = QLabel("Installment Schedule")
        heading.setFont(section_heading_font(13))
        heading.setStyleSheet(f"color: {Colors.DARK_TEXT};")
        layout.addWidget(heading)

        table = QTableWidget(max(1, len(summary.installments)), 3)
        table.setObjectName("installmentTable")
        table.setHorizontalHeaderLabels(["Payment Stage", "Amount Due", "Amount Paid"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)

        if not summary.installments:
            table.setItem(0, 0, QTableWidgetItem("No installment schedule on record"))
            for column in (1, 2):
                table.setItem(0, column, QTableWidgetItem("—"))
        else:
            for index, item in enumerate(summary.installments):
                table.setItem(index, 0, QTableWidgetItem(item.label))
                due = QTableWidgetItem(format_peso(item.amount))
                due.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                table.setItem(index, 1, due)
                paid = QTableWidgetItem(format_peso(item.amount_paid))
                paid.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                table.setItem(index, 2, paid)

        table.setMaximumHeight(min(240, 40 + 28 * max(1, table.rowCount())))
        layout.addWidget(table)

        schedule_total = summary.installments_total
        total_line = QLabel(f"Schedule total {format_peso(schedule_total)}")
        total_line.setFont(small_label_font(10))
        total_line.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        layout.addWidget(total_line)

        for note in summary.discrepancies:
            warning = QLabel(f"⚠  {note}")
            warning.setFont(small_label_font(10))
            warning.setStyleSheet(f"color: {Colors.ERROR_RED};")
            warning.setWordWrap(True)
            warning.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            layout.addWidget(warning)

        layout.addStretch()
        return tab

    # -- states ----------------------------------------------------------

    def _empty_state(self, message: str) -> QWidget:
        frame = QFrame()
        # Scoped by object name: an unscoped `QFrame { border }` rule is applied
        # by Qt to the frame's own labels too, drawing a dashed box around the
        # heading and the message inside an already-dashed box.
        frame.setObjectName("comEmptyState")
        frame.setStyleSheet(
            f"""
            QFrame#comEmptyState {{
                background-color: {Colors.CARD_BG};
                border: 1px dashed {Colors.CARD_BORDER};
                border-radius: 12px;
            }}
            """
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(8)

        heading = QLabel("No data on record")
        heading.setFont(body_bold_font(14))
        heading.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)

        body = QLabel(message)
        body.setFont(body_font(12))
        body.setStyleSheet(f"color: {Colors.LIGHT_TEXT};")
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setWordWrap(True)

        layout.addWidget(heading)
        layout.addWidget(body)
        return frame

    def _build_not_found(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("comNotFoundState")
        frame.setStyleSheet(
            f"""
            QFrame#comEmptyState {{
                background-color: {Colors.CARD_BG};
                border: 1px dashed {Colors.CARD_BORDER};
                border-radius: 12px;
            }}
            """
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(40, 48, 40, 48)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(12)

        heading = QLabel("No student found")
        heading.setFont(page_heading_font(18))
        heading.setStyleSheet(f"color: {Colors.ERROR_RED};")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)

        value = QLabel(self.scanned_value or "—")
        value.setFont(monospace_font(20))
        value.setStyleSheet(f"color: {Colors.DARK_TEXT};")
        value.setAlignment(Qt.AlignmentFlag.AlignCenter)

        hint = QLabel(
            "The symbol decoded but no student record matches it. Nothing has been "
            "recorded as a successful scan."
        )
        hint.setFont(body_font(12))
        hint.setStyleSheet(f"color: {Colors.LIGHT_TEXT};")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)

        layout.addWidget(heading)
        layout.addWidget(value)
        layout.addWidget(hint)
        return frame

    # -- actions ---------------------------------------------------------

    def _build_actions(self) -> QFrame:
        """Bottom action bar.

        The five actions keep their full labels and are laid out in a
        :class:`~ui.widgets.flow_row.FlowRow`, so a narrow window wraps them onto
        a second line instead of clipping "Proceed to Assessment" or forcing the
        dialog wider than the screen it has to fit on.
        """
        frame = QFrame()
        frame.setObjectName("actionBar")
        frame.setStyleSheet(
            f"""
            QFrame#actionBar {{
                background-color: {Colors.SURFACE};
                border-top: 1px solid {Colors.BORDER};
            }}
            """
        )
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(20, 12, 20, 12)
        outer.setSpacing(0)

        row = FlowRow(spacing=10)
        outer.addWidget(row)

        self._close_button = QPushButton("Close")
        self._close_button.setProperty("secondary", "true")
        self._close_button.setFont(button_font())
        self._close_button.setToolTip("Close this panel (Esc)")
        self._close_button.setAccessibleName("Close scan result panel")
        self._close_button.clicked.connect(self.reject)
        self._close_button.setAutoDefault(True)
        self._close_button.setDefault(True)

        if not (self.success and self.student):
            row.addStretch()
            row.addWidget(self._close_button)
            self._action_row = row
            return frame

        self.next_btn = QPushButton("Scan Next Student")
        self.next_btn.setFont(button_font())
        self.next_btn.setToolTip("Close this panel and resume scanning")
        self.next_btn.setAccessibleName("Scan next student")
        self.next_btn.clicked.connect(self._on_scan_next)

        self.profile_btn = QPushButton("View Full Profile")
        self.profile_btn.setProperty("secondary", "true")
        self.profile_btn.setFont(button_font())
        self.profile_btn.setToolTip("Open this student in Students management")
        self.profile_btn.setAccessibleName("View full student profile")
        self.profile_btn.clicked.connect(lambda: self.profile_requested.emit(self.student))

        self.assessment_btn = QPushButton("Proceed to Assessment")
        self.assessment_btn.setProperty("secondary", "true")
        self.assessment_btn.setFont(button_font())
        self.assessment_btn.setToolTip("Open Assessment & Fees for this student")
        self.assessment_btn.setAccessibleName("Proceed to assessment and fees")
        self.assessment_btn.clicked.connect(lambda: self.assessment_requested.emit(self.student))

        self.export_btn = QPushButton("Print COM")
        self.export_btn.setFont(button_font())
        self.export_btn.setAccessibleName("Print or export certificate of matriculation")
        self.export_btn.clicked.connect(self._on_print_or_export)
        self._sync_export_button()

        # Order is fixed by the layout reference: scan on, then the two
        # navigation actions, then the document, then closing.
        for button in (
            self.next_btn,
            self.profile_btn,
            self.assessment_btn,
            self.export_btn,
            self._close_button,
        ):
            row.addWidget(button)
        self._action_row = row
        return frame

    def _on_scan_next(self) -> None:
        self.scan_next_requested.emit()
        self.accept()

    def _sync_export_button(self) -> None:
        """Enable Print COM only when there is something it can actually print.

        Offering the action during the lookup and refusing it afterwards was the
        other half of the misleading-message bug: the button looked available and
        then reported that the student had no enrollment.
        """
        problem = self._certifiable_problem()
        self.export_btn.setEnabled(not problem)
        self.export_btn.setToolTip(
            problem
            or "Print the Certificate of Matriculation, or export it as a PDF"
        )

    def _certifiable_problem(self) -> str:
        """Why Print COM is unavailable right now -- or ``""`` when it is.

        Three states look alike in the code and mean three different things to an
        operator, and conflating them is what produced a student with seven
        enrolled subjects being told they had no enrollment record:

        * the records are still being fetched -- nothing is wrong, the answer is
          not back yet;
        * the fetch failed -- a database problem, and the COM is unknown, not
          empty;
        * the records arrived and there is genuinely nothing to certify.

        Each returns the sentence that belongs to it, so the popup can either
        disable the action or explain itself instead of guessing.
        """
        if self.loading:
            return (
                "The enrollment and assessment records for this student are still "
                "being loaded. Print COM becomes available as soon as they arrive."
            )
        if self.load_error:
            return (
                f"This student's records could not be loaded, so no Certificate of "
                f"Matriculation can be produced: {self.load_error}"
            )
        # "" when the document can be produced, the sentence that says why not
        # otherwise -- never None, so callers can always use it as text.
        return certifiable(self.com_summary) or ""

    def _on_print_or_export(self) -> None:
        """Print straight away; offer PDF export when there is no printer."""
        from PySide6.QtPrintSupport import QPrinterInfo

        problem = self._certifiable_problem()
        if problem:
            # Said plainly, and nothing is written: a failed export must never
            # report that a document was generated.
            logger.info("COM not produced for %s: %s",
                        getattr(self.student, "student_id", "?"), problem)
            self._show_not_certifiable(problem)
            return

        service = self._print_service
        try:
            if service is None:
                service = ComPrintService()
        except Exception as exc:  # reportlab/Qt unavailable
            logger.warning("Report service unavailable: %s", exc)
            service = None

        printers = QPrinterInfo.availablePrinterNames()
        if service is not None and printers:
            try:
                if service.print_com(
                    self.student,
                    self.com_summary,
                    self.financial_summary,
                    parent=self,
                    fee_lines=self.fee_lines,
                ):
                    return
            except ComNotCertifiableError as exc:
                self._show_not_certifiable(str(exc))
                return

        self._export_pdf(service)

    def _export_pdf(self, service) -> None:
        from PySide6.QtWidgets import QMessageBox

        if service is None:
            QMessageBox.warning(
                self,
                "Export unavailable",
                "PDF generation is unavailable in this environment.",
            )
            return
        try:
            path = service.export_pdf(
                self.student,
                self.enrollment,
                self.assessment,
                service.default_output_path(self.student),
            )
        except ComNotCertifiableError as exc:
            self._show_not_certifiable(str(exc))
            return
        except Exception as exc:
            logger.exception("COM export failed")
            QMessageBox.critical(
                self, "Export failed", f"The COM could not be exported:\n{exc}"
            )
            return

        if path is None:
            QMessageBox.critical(
                self,
                "Export failed",
                "The COM could not be written. See the application log for details.",
            )
            return

        self.exported.emit(str(path))
        QMessageBox.information(
            self,
            "COM exported",
            f"Certificate saved to:\n{path}\n\n"
            f"{self.official.banner}\n{self.official.reason}".strip(),
        )

    def _show_not_certifiable(self, message: str) -> None:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.warning(
            self,
            "Cannot produce a COM",
            f"{message}\n\nNo document was generated and nothing was recorded.",
        )

    # -- misc ------------------------------------------------------------

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        """Stop the loading animation before the dialog goes away.

        The loading pulse is a QTimer parented to this dialog. Closing the panel
        while the records are still in flight left it running against widgets
        that are about to be destroyed, which the platform is entitled to treat
        as undefined behaviour -- it showed up as an intermittent crash of the
        test runner rather than as a visible fault.
        """
        self._stop_loading_animation()
        super().closeEvent(event)

    def _load_student_photo(self, avatar_label: QLabel) -> None:
        """Show the verified student's portrait, or a neutral placeholder.

        The picture follows the verified record: ``students.profile_image_path``
        names the file, and :mod:`ui.widgets.profile_image` resolves it against
        the configured assets directory. Nothing here matches on name, order or
        filename, so Javy's and Ronna's photographs cannot be swapped, and a
        missing file degrades to a neutral initials disc with a logged warning
        rather than to the previous student's face.
        """
        apply_portrait(avatar_label, self.student)
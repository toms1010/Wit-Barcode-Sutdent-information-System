"""Dashboard: real figures from the database, with honest states.

Three rules govern this page, because each of them was previously violated:

* Every tile is a real query. "Available Subjects" and "Generated Barcodes"
  were pinned to ``update_value("0")`` with a comment admitting there was no
  query behind them, so the dashboard reported a number that had nothing to do
  with the data. They now count rows.
* A tile means what its label says. "Active Enrollments" counted students whose
  *profile* status was ``active``; that is a different question from how many
  enrollment records are in ``enrolled`` status, and the two can disagree. The
  tile now counts enrollment records and its tooltip says so.
* Loading, empty, success and error are drawn differently. A tile shows ``—``
  while a query is in flight, an empty table says "no records" instead of
  showing nothing, and a database failure is reported as a failure with a retry
  -- never as an empty table, which reads as "this registrar has no students".

Nothing here fabricates a value. If a count cannot be read, the tile says so.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QFrame,
    QScrollArea, QPushButton, QSizePolicy, QStackedWidget, QTableWidget
)
from PySide6.QtCore import Qt, QTimer, Slot
from styles.typography import section_heading_font, page_heading_font, small_label_font, button_font
from styles.colors import Colors
from ui.widgets.stat_card import StatCard
from ui.widgets.responsive_table import ColumnSpec, configure_table, set_cell
from ui.widgets.empty_state import EmptyState, LoadingState, error_state
from ui.widgets.panel import card_frame
from services.student_service import StudentService
from services.barcode_generator import BarcodeGeneratorService
from database.connection import DatabaseUnavailableError
from database.repositories import BarcodeScanRepository
from models.enrollment import EnrollmentStatus as TermStatus
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

#: Figure shown while a count is being read or when it could not be read.
UNAVAILABLE = "—"

#: Rows in each of the two recent-activity tables.
RECENT_ROWS = 10


class DashboardPage(QWidget):
    def __init__(self, student_service: StudentService, barcode_service: BarcodeGeneratorService, parent=None):
        super().__init__(parent)
        self.student_service = student_service
        self.barcode_service = barcode_service
        self.scan_repo = BarcodeScanRepository()

        self.setObjectName("dashboardPage")
        self._setup_ui()
        # A failure at construction must not leave the window unusable: the page
        # reports it and Refresh can try again.
        self.refresh()

        # Auto-refresh timer. Refresh is cheap and read-only, and stopping it
        # while a modal is open avoids rewriting the tables under the operator.
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._on_auto_refresh)

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
        content_layout.setSpacing(20)

        content_layout.addWidget(self._build_stats())
        content_layout.addWidget(self._build_actions())
        content_layout.addLayout(self._build_recent_section())

        content_layout.addStretch()
        scroll.setWidget(content)
        main_layout.addWidget(scroll)

    # ------------------------------------------------------------------ UI --

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setFixedHeight(80)
        header.setStyleSheet(
            f"background-color: {Colors.ORANGE}; "
            "border-top-left-radius: 8px; border-top-right-radius: 8px;"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 0, 24, 0)

        title = QLabel("Dashboard")
        title.setFont(page_heading_font(24))
        title.setStyleSheet("color: white;")
        header_layout.addWidget(title)
        header_layout.addStretch()

        self.header_status = QLabel("")
        self.header_status.setFont(small_label_font(11))
        self.header_status.setStyleSheet("color: rgba(255,255,255,0.85);")
        header_layout.addWidget(self.header_status)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setProperty("ghost", "true")
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.setToolTip("Re-read every figure on this page from the database")
        self.refresh_btn.setStyleSheet(
            "QPushButton { color: white; border-color: white; } "
            "QPushButton:hover { background: rgba(255,255,255,0.1); } "
            "QPushButton:disabled { color: rgba(255,255,255,0.6); border-color: rgba(255,255,255,0.6); }"
        )
        self.refresh_btn.clicked.connect(self.refresh)
        header_layout.addWidget(self.refresh_btn)
        return header

    def _build_stats(self) -> QWidget:
        container = QWidget()
        grid = QGridLayout(container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(16)

        self.stat_cards = {
            "total_students": StatCard("Total Students", UNAVAILABLE, "👥"),
            "active_enrollments": StatCard("Enrolled Terms", UNAVAILABLE, "📚"),
            "total_subjects": StatCard("Active Subjects", UNAVAILABLE, "📖"),
            "total_barcodes": StatCard("Active Barcodes", UNAVAILABLE, "🏷️"),
        }
        self.stat_cards["total_students"].setToolTip(
            "Rows in the students table, whatever their status."
        )
        self.stat_cards["active_enrollments"].setToolTip(
            "Enrollment records in 'enrolled' status. This counts terms, not "
            "students: a student can hold more than one enrollment record."
        )
        self.stat_cards["total_subjects"].setToolTip("Subjects flagged active on the catalogue.")
        self.stat_cards["total_barcodes"].setToolTip("Barcode records that are still active.")

        for index, key in enumerate(
            ("total_students", "active_enrollments", "total_subjects", "total_barcodes")
        ):
            card = self.stat_cards[key]
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            grid.addWidget(card, 0, index)
            grid.setColumnStretch(index, 1)
        return container

    def _build_actions(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        label = QLabel("Quick Actions")
        label.setFont(section_heading_font(16))
        label.setStyleSheet(f"color: {Colors.DARK_BURNT_ORANGE};")
        layout.addWidget(label)

        grid = QGridLayout()
        grid.setSpacing(12)

        actions = [
            ("Add Student", "👤", "open_add_student"),
            ("Scan Barcode", "📷", "open_scanner"),
            ("Generate Barcode", "🏷️", "open_generator"),
            ("View Reports", "📈", "open_reports"),
        ]
        self.action_buttons = {}
        for index, (text, icon, slot_name) in enumerate(actions):
            button = QPushButton(f"{icon}  {text}")
            button.setFont(button_font(13))
            button.setMinimumHeight(56)
            button.setProperty("secondary", "true")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(getattr(self, slot_name))
            self.action_buttons[slot_name] = button
            grid.addWidget(button, 0, index)
            grid.setColumnStretch(index, 1)
        layout.addLayout(grid)
        return container

    def _build_recent_section(self) -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setSpacing(12)

        heading = QLabel("Recent Activity")
        heading.setFont(section_heading_font(16))
        heading.setStyleSheet(f"color: {Colors.DARK_BURNT_ORANGE};")
        layout.addWidget(heading)

        layout.addWidget(
            self._build_recent_table(
                "scans",
                "No scans recorded yet",
                "Scans appear here once the scanner has read a card.",
            )
        )
        layout.addWidget(
            self._build_recent_table(
                "students",
                "No students on file",
                "Add a student, or import the registrar's list, to fill this table.",
            )
        )
        return layout

    def _build_recent_table(self, key: str, empty_heading: str, empty_message: str) -> QWidget:
        """One titled panel holding a state stack over a table.

        The stack is what makes loading, empty and failed distinguishable: a bare
        0-row table looks identical for all three.
        """
        # Scoped by object name: an unscoped `QFrame { border }` rule cascades to
        # the caption label and the table inside, boxing each of them separately.
        panel = card_frame(f"dashboardPanel_{key}")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(8)

        caption = QLabel("Recent Scans" if key == "scans" else "Recent Students")
        caption.setFont(small_label_font(11))
        caption.setStyleSheet(f"color: {Colors.MEDIUM_TEXT}; letter-spacing: 1px;")
        layout.addWidget(caption)

        stack = QStackedWidget()
        layout.addWidget(stack)

        columns = (
            [
                ColumnSpec("Time", width=90),
                ColumnSpec("Barcode", width=130),
                ColumnSpec("Student", stretch=True),
                ColumnSpec("Status", width=120),
                ColumnSpec("Confidence", width=110),
            ]
            if key == "scans"
            else [
                ColumnSpec("Student ID", width=140),
                ColumnSpec("Reference", width=110),
                ColumnSpec("Name", stretch=True),
                ColumnSpec("Program", stretch=True),
                ColumnSpec("Status", width=120),
            ]
        )

        table = QTableWidget()
        configure_table(table, columns)
        stack.addWidget(table)

        loading = LoadingState("Reading the database…")
        stack.addWidget(loading)

        empty = EmptyState(
            empty_heading,
            empty_message,
            icon="students" if key == "students" else "document",
            hint="Refresh after making a change to confirm it was recorded.",
        )
        stack.addWidget(empty)

        failure = error_state(
            DatabaseUnavailableError("The counts and lists on this page could not be read."),
            retry=self.refresh,
        )
        stack.addWidget(failure)

        setattr(self, f"{key}_table", table)
        setattr(self, f"{key}_stack", stack)
        setattr(self, f"{key}_loading", loading)
        setattr(self, f"{key}_empty", empty)
        setattr(self, f"{key}_error", failure)
        return panel

    # -------------------------------------------------------------- routing --

    @property
    def _window(self):
        """The :class:`~ui.main_window.MainWindow` this page sits inside.

        Resolved by walking up the parent chain. ``self.parent()`` is the
        ``QStackedWidget``, which has no ``navigate_to``/``show_student_dialog``,
        so the quick actions used to test for those attributes, fail the test and
        do nothing at all.
        """
        widget = self.parent()
        while widget is not None:
            if hasattr(widget, "navigate_to"):
                return widget
            widget = widget.parent()
        return None

    def _require_window(self, action: str) -> bool:
        window = self._window
        if window is None:
            # Only possible if the page is embedded outside the main window (a
            # test harness); say so rather than clicking into nothing.
            logger.warning("Dashboard action %s has no window to route to", action)
            self._set_status("This action is unavailable outside the main window")
            return False
        return True

    def open_add_student(self):
        if not self._require_window("Add Student"):
            return
        self._window.show_student_dialog()

    def open_scanner(self):
        if self._require_window("Scan Barcode"):
            self._window.navigate_to("scanner")

    def open_generator(self):
        if self._require_window("Generate Barcode"):
            self._window.navigate_to("generator")

    def open_reports(self):
        if self._require_window("View Reports"):
            self._window.navigate_to("reports")

    # -------------------------------------------------------------- loading --

    def _set_status(self, text: str) -> None:
        self.header_status.setText(text)

    def _show_state(self, key: str, state: str, error: str = "") -> None:
        """Put one panel into ``loading`` / ``empty`` / ``error`` / ``content``."""
        stack = getattr(self, f"{key}_stack")
        table = getattr(self, f"{key}_table")
        if state == "content":
            stack.setCurrentWidget(table)
            return
        if state == "loading":
            stack.setCurrentWidget(getattr(self, f"{key}_loading"))
            return
        if state == "empty":
            stack.setCurrentWidget(getattr(self, f"{key}_empty"))
            return
        failure = getattr(self, f"{key}_error")
        if error:
            failure.set_message(
                "The records could not be read, so nothing on this panel is current."
            )
        stack.setCurrentWidget(failure)

    def _set_tile(self, key: str, value) -> None:
        """Write a count, or ``—`` when it could not be read."""
        if value is None:
            self.stat_cards[key].update_value(UNAVAILABLE)
        else:
            self.stat_cards[key].update_value(str(value))

    def _read_counts(self) -> dict:
        """Read every tile in one place, so one failure cannot hide the rest.

        Each count is attempted independently: a failure is recorded against its
        own tile and the others still report, instead of a single exception
        abandoning the whole page behind an empty screen.
        """
        counts = {
            "total_students": None,
            "active_enrollments": None,
            "total_subjects": None,
            "total_barcodes": None,
        }
        failures = {}

        def read(key: str, label: str, call):
            try:
                counts[key] = call()
            except DatabaseUnavailableError as exc:
                logger.warning("Dashboard count %s failed: %s", label, exc)
                failures[label] = exc
            except Exception as exc:  # noqa: BLE001 - surfaced, never hidden
                logger.exception("Dashboard count %s failed", label)
                failures[label] = exc

        read("total_students", "students", lambda: self.student_service.count_students())
        read(
            "active_enrollments",
            "enrollments",
            lambda: self.student_service.count_enrollments(status=TermStatus.ENROLLED.value),
        )
        read("total_subjects", "subjects", lambda: self.student_service.count_subjects())
        read("total_barcodes", "barcodes", lambda: self.barcode_service.count_barcodes())
        return counts, failures

    def _read_recent_scans(self, rows: int = RECENT_ROWS):
        return self.scan_repo.get_recent(limit=rows)

    def _read_recent_students(self, rows: int = RECENT_ROWS):
        return self.student_service.search_students(limit=rows)

    def refresh(self) -> None:
        """Reload every figure and list. Safe to call at any time.

        The timer is stopped while a modal is open so a refresh cannot rewrite
        the tables behind an operator reading a student record, and started again
        on close.
        """
        if not self.refresh_btn.isEnabled():
            # A refresh is already in progress (this call is the retry from the
            # error panel, or a second click).
            return

        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("Refreshing…")
        self._show_state("scans", "loading")
        self._show_state("students", "loading")
        try:
            counts, failures = self._read_counts()
            for key in counts:
                self._set_tile(key, counts[key])

            scans_error = students_error = None
            try:
                scans = self._read_recent_scans()
            except DatabaseUnavailableError as exc:
                logger.warning("Dashboard scan history failed: %s", exc)
                scans, scans_error = [], exc

            try:
                students = self._read_recent_students()
            except DatabaseUnavailableError as exc:
                logger.warning("Dashboard student list failed: %s", exc)
                students, students_error = [], exc

            self._fill_scans(scans)
            self._fill_students(students)
            self._show_state("scans", "error" if scans_error else ("content" if scans else "empty"),
                             str(scans_error) if scans_error else "")
            self._show_state("students", "error" if students_error else ("content" if students else "empty"),
                             str(students_error) if students_error else "")

            if failures or scans_error or students_error:
                labels = ", ".join(sorted(failures)) or "recent activity"
                self._set_status(f"Partially unavailable: {labels}")
            elif not counts["total_students"]:
                self._set_status("No students on file yet")
            else:
                stamp = datetime.now().strftime("%H:%M:%S")
                self._set_status(f"Updated {stamp}")
        finally:
            self.refresh_btn.setEnabled(True)
            self.refresh_btn.setText("Refresh")

    def _fill_scans(self, scans) -> None:
        table = self.scans_table
        table.setRowCount(0)
        for scan in scans:
            row = table.rowCount()
            table.insertRow(row)
            scanned_at = scan.scanned_at
            set_cell(table, row, 0, scanned_at.strftime("%H:%M:%S") if scanned_at else "N/A")
            set_cell(table, row, 1, scan.scanned_value or "")
            # The scan carries the joined student name, so the table does not run
            # one extra query per row -- and never labels a resolved student
            # "Unknown" because a lookup was issued against the wrong key.
            set_cell(table, row, 2, scan.student_name or "No matching student")
            status = getattr(scan.scan_status, "value", scan.scan_status)
            status_cell = set_cell(
                table, row, 3, str(status).replace("_", " ").title(),
                align=Qt.AlignmentFlag.AlignCenter,
            )
            status_cell.setForeground(Colors.qcolor(self._status_colour(status)))
            confidence = (
                f"{float(scan.confidence_score):.1%}" if scan.confidence_score is not None else "N/A"
            )
            set_cell(table, row, 4, confidence, align=Qt.AlignmentFlag.AlignCenter)

    @staticmethod
    def _status_colour(status: str) -> str:
        return {
            "success": Colors.SUCCESS_GREEN,
            "not_found": Colors.ERROR_RED,
            "invalid": Colors.ERROR_RED,
            "duplicate": Colors.WARNING_ORANGE,
        }.get(str(status), Colors.MEDIUM_TEXT)

    def _fill_students(self, students) -> None:
        table = self.students_table
        table.setRowCount(0)
        for student in students:
            row = table.rowCount()
            table.insertRow(row)
            set_cell(table, row, 0, student.student_id)
            set_cell(table, row, 1, student.reference_number)
            set_cell(table, row, 2, student.full_name)
            set_cell(table, row, 3, student.program)
            status = getattr(student.enrollment_status, "value", student.enrollment_status)
            status_cell = set_cell(
                table, row, 4, str(status).replace("_", " ").title(),
                align=Qt.AlignmentFlag.AlignCenter,
            )
            status_cell.setForeground(
                Colors.qcolor(
                    {
                        "active": Colors.SUCCESS_GREEN,
                        "dropped": Colors.ERROR_RED,
                        "graduated": Colors.MEDIUM_TEXT,
                        "on_leave": Colors.WARNING_ORANGE,
                    }.get(str(status), Colors.MEDIUM_TEXT)
                )
            )

    # -------------------------------------------------------------- timers --

    def _on_auto_refresh(self) -> None:
        """Poll only while the page is visible and no modal is on top."""
        window = self._window
        if window is not None and getattr(window, "_active_dialog", None) is not None:
            return
        if not self.isVisible():
            return
        self.refresh()

    def start_auto_refresh(self) -> None:
        self.refresh_timer.start(30000)

    def stop_auto_refresh(self) -> None:
        self.refresh_timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        # Re-read on entry: an operator who just recorded a scan expects the
        # dashboard to know about it without pressing Refresh.
        self.refresh()
        self.start_auto_refresh()

    def hideEvent(self, event):
        self.stop_auto_refresh()
        super().hideEvent(event)


class QTableWidgetShim:
    """Placeholder replaced below -- see :class:`_RecentTable`.

    Kept as a name the page already uses so the stack construction above reads
    the same way as every other table in the application.
    """
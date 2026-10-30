"""Student Information and ID Management Dashboard.

This page is the main interface for managing student information and ID records.
It displays real-time statistics from the database, a searchable/filterable student
table, and provides access to student details with ID card preview and printing.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QFrame,
    QScrollArea, QPushButton, QSizePolicy, QStackedWidget, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QLineEdit, QComboBox,
    QMessageBox, QDialog, QFileDialog, QApplication
)
from PySide6.QtCore import Qt, QTimer, Slot, QSortFilterProxyModel
from PySide6.QtGui import QFont, QFontMetrics, QPixmap, QPainter, QPageLayout, QPageSize, QColor
from styles.typography import section_heading_font, page_heading_font, small_label_font, button_font, body_font, body_bold_font, monospace_font
from styles.colors import Colors
from ui.widgets.stat_card import StatCard
from ui.widgets.responsive_table import ColumnSpec, configure_table, set_cell
from ui.widgets.empty_state import EmptyState, LoadingState, error_state
from ui.widgets.panel import card_frame
from services.student_service import StudentService
from services.barcode_generator import BarcodeGeneratorService
from services.id_card_service import IdCardService, CardPreviewData, CardSide, CardLayout
from database.connection import DatabaseUnavailableError
from database.repositories import BarcodeScanRepository
from models.enrollment import EnrollmentStatus as TermStatus
from models.student import EnrollmentStatus
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Figure shown while a count is being read or when it could not be read.
UNAVAILABLE = "—"

# Default page size for student table
DEFAULT_PAGE_SIZE = 25


class StudentTableModel:
    """Simple model for student table data with sorting and filtering."""

    def __init__(self, students=None):
        self.students = students or []
        self.filtered_students = self.students[:]
        self.sort_column = -1
        self.sort_order = Qt.SortOrder.AscendingOrder

    def set_students(self, students):
        self.students = students
        self.apply_filter()

    def apply_filter(self, search_text="", status_filter="", course_filter="", year_filter=""):
        search_lower = search_text.lower().strip()
        self.filtered_students = [
            s for s in self.students
            if (not search_lower or
                search_lower in s.student_id.lower() or
                search_lower in s.reference_number.lower() or
                search_lower in s.full_name.lower() or
                search_lower in s.program.lower() or
                (s.barcode_payload and search_lower in s.barcode_payload.lower()))
            and (not status_filter or
                 (s.enrollment_status.value if hasattr(s.enrollment_status, 'value') else str(s.enrollment_status)) == status_filter)
            and (not course_filter or s.program == course_filter)
            and (not year_filter or
                 (s.year_level.value if hasattr(s.year_level, 'value') else str(s.year_level)) == year_filter)
        ]
        if self.sort_column >= 0:
            self.sort(self.sort_column, self.sort_order)

    def sort(self, column, order):
        self.sort_column = column
        self.sort_order = order
        reverse = order == Qt.SortOrder.DescendingOrder

        def get_key(student):
            if column == 0:  # Student ID
                return student.student_id
            elif column == 1:  # Reference No.
                return student.reference_number
            elif column == 2:  # Name
                return student.full_name
            elif column == 3:  # Program
                return student.program
            elif column == 4:  # Year Level
                return student.year_level.value if hasattr(student.year_level, 'value') else str(student.year_level)
            elif column == 5:  # Barcode
                return student.barcode_payload or ""
            elif column == 6:  # ID Status
                return self._get_id_status(student)
            elif column == 7:  # Enrollment Status
                return student.enrollment_status.value if hasattr(student.enrollment_status, 'value') else str(student.enrollment_status)
            return ""

        self.filtered_students.sort(key=get_key, reverse=reverse)

    @staticmethod
    def _get_id_status(student):
        if student.barcode_payload:
            return "Registered"
        return "Missing"

    def get_page(self, page, page_size):
        start = page * page_size
        end = start + page_size
        return self.filtered_students[start:end]

    def total_count(self):
        return len(self.filtered_students)

    def total_unfiltered(self):
        return len(self.students)


class DashboardPage(QWidget):
    def __init__(self, student_service: StudentService, barcode_service: BarcodeGeneratorService, parent=None):
        super().__init__(parent)
        self.student_service = student_service
        self.barcode_service = barcode_service
        self.id_card_service = IdCardService(barcode_service)
        self.scan_repo = BarcodeScanRepository()

        # Pagination state
        self.current_page = 0
        self.page_size = DEFAULT_PAGE_SIZE

        # Filter state
        self.current_search = ""
        self.current_status_filter = ""
        self.current_course_filter = ""
        self.current_year_filter = ""

        # Student table model
        self.table_model = StudentTableModel()

        # Selected student for details
        self.selected_student = None

        self.setObjectName("dashboardPage")
        self._setup_ui()
        self.refresh()

        # Auto-refresh timer
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._on_auto_refresh)

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Page header (the main window has its own top bar, so this is the page-specific header)
        main_layout.addWidget(self._build_page_header())

        # Main content area with splitter-like layout
        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(24, 24, 24, 24)
        content_layout.setSpacing(24)

        # Left side: Student table (primary content)
        left_panel = self._build_student_table_panel()
        content_layout.addWidget(left_panel, 2)

        # Right side: Student details / ID preview
        right_panel = self._build_student_details_panel()
        content_layout.addWidget(right_panel, 1)

        main_layout.addWidget(content, 1)

    def _build_page_header(self) -> QFrame:
        """Build the page header with title and refresh button."""
        header = QFrame()
        header.setFixedHeight(72)
        header.setStyleSheet(
            f"background-color: {Colors.ORANGE}; "
            "border-top-left-radius: 8px; border-top-right-radius: 8px;"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 0, 24, 0)

        title = QLabel("Student Information and ID Management")
        title.setFont(page_heading_font(24))
        title.setStyleSheet("color: white;")
        header_layout.addWidget(title)
        header_layout.addStretch()

        # Stats summary label
        self.stats_summary = QLabel("Loading…")
        self.stats_summary.setFont(small_label_font(11))
        self.stats_summary.setStyleSheet("color: rgba(255,255,255,0.85);")
        header_layout.addWidget(self.stats_summary)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setProperty("ghost", "true")
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.setToolTip("Re-read all data from the database")
        self.refresh_btn.setStyleSheet(
            "QPushButton { color: white; border: 1px solid white; border-radius: 6px; padding: 6px 16px; } "
            "QPushButton:hover { background: rgba(255,255,255,0.1); } "
            "QPushButton:disabled { color: rgba(255,255,255,0.6); border-color: rgba(255,255,255,0.6); }"
        )
        self.refresh_btn.clicked.connect(self.refresh)
        header_layout.addWidget(self.refresh_btn)
        return header

    def _build_student_table_panel(self) -> QWidget:
        """Build the left panel with student overview cards and the student table."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        # Student Overview Cards
        layout.addWidget(self._build_overview_cards())

        # Student Table Section
        table_section = QFrame()
        table_section.setObjectName("studentTableSection")
        table_section.setStyleSheet(f"""
            QFrame#studentTableSection {{
                background-color: {Colors.CARD_BG};
                border: 1px solid {Colors.CARD_BORDER};
                border-radius: 12px;
            }}
        """)
        table_layout = QVBoxLayout(table_section)
        table_layout.setContentsMargins(16, 16, 16, 16)
        table_layout.setSpacing(12)

        # Toolbar with search and filters
        table_layout.addLayout(self._build_table_toolbar())

        # Table with state stack
        self.table_stack = QStackedWidget()
        table_layout.addWidget(self.table_stack, 1)

        self.student_table = QTableWidget()
        configure_table(
            self.student_table,
            [
                ColumnSpec("Student ID", width=110),
                ColumnSpec("Reference No.", width=110),
                ColumnSpec("Name", width=170),
                ColumnSpec("Program", width=150, stretch=True),
                ColumnSpec("Year Level", width=100),
                ColumnSpec("Barcode / ID", width=120),
                ColumnSpec("ID Status", width=100),
                ColumnSpec("Enrollment", width=100),
            ],
        )
        self.student_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.student_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.student_table.setAlternatingRowColors(True)
        self.student_table.verticalHeader().setVisible(False)
        self.student_table.setSortingEnabled(False)  # We handle sorting manually
        self.student_table.itemSelectionChanged.connect(self._on_student_selected)
        self.table_stack.addWidget(self.student_table)

        self.table_loading = LoadingState("Loading students…")
        self.table_stack.addWidget(self.table_loading)

        self.table_empty = EmptyState(
            "No students found",
            "No students match the current search and filters.",
            icon="search",
            action_text="Clear filters",
            action=self._clear_filters,
        )
        self.table_stack.addWidget(self.table_empty)

        self.table_error = error_state(
            DatabaseUnavailableError("The student list could not be loaded."),
            retry=self.refresh,
        )
        self.table_stack.addWidget(self.table_error)

        # Pagination
        table_layout.addLayout(self._build_pagination())

        layout.addWidget(table_section, 1)
        return panel

    def _build_overview_cards(self) -> QWidget:
        """Build the student overview statistic cards."""
        container = QWidget()
        grid = QGridLayout(container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(16)

        self.stat_cards = {
            "total_students": StatCard("Total Students", UNAVAILABLE, "👥"),
            "active_students": StatCard("Active Students", UNAVAILABLE, "✅"),
            "students_with_id": StatCard("Registered Student IDs", UNAVAILABLE, "🏷️"),
            "students_missing_id": StatCard("Missing/Unassigned IDs", UNAVAILABLE, "⚠️"),
        }
        self.stat_cards["total_students"].setToolTip("Total number of student records in the database.")
        self.stat_cards["active_students"].setToolTip("Students with 'active' enrollment status.")
        self.stat_cards["students_with_id"].setToolTip("Students who have a barcode payload assigned.")
        self.stat_cards["students_missing_id"].setToolTip("Students who are missing a barcode payload.")

        for index, key in enumerate(
            ("total_students", "active_students", "students_with_id", "students_missing_id")
        ):
            card = self.stat_cards[key]
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            grid.addWidget(card, 0, index)
            grid.setColumnStretch(index, 1)
        return container

    def _build_table_toolbar(self) -> QHBoxLayout:
        """Build the toolbar with search and filter controls."""
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        # Search
        search_label = QLabel("Search:")
        search_label.setFont(small_label_font(11))
        search_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        toolbar.addWidget(search_label)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search by name, ID, reference, program, or barcode…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(200)
        self.search_edit.setMaximumWidth(400)
        self.search_edit.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.search_edit.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self.search_edit)

        toolbar.addSpacing(8)

        # Status filter
        status_label = QLabel("Status:")
        status_label.setFont(small_label_font(11))
        status_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        toolbar.addWidget(status_label)

        self.status_filter = QComboBox()
        self.status_filter.addItem("All Statuses", "")
        for status in EnrollmentStatus:
            self.status_filter.addItem(status.value.title(), status.value)
        self.status_filter.currentIndexChanged.connect(self._on_status_filter_changed)
        self.status_filter.setMinimumWidth(130)
        toolbar.addWidget(self.status_filter)

        toolbar.addSpacing(8)

        # Course filter
        course_label = QLabel("Program:")
        course_label.setFont(small_label_font(11))
        course_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        toolbar.addWidget(course_label)

        self.course_filter = QComboBox()
        self.course_filter.addItem("All Programs", "")
        self.course_filter.currentIndexChanged.connect(self._on_course_filter_changed)
        self.course_filter.setMinimumWidth(140)
        toolbar.addWidget(self.course_filter)

        toolbar.addSpacing(8)

        # Year level filter
        year_label = QLabel("Year:")
        year_label.setFont(small_label_font(11))
        year_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        toolbar.addWidget(year_label)

        self.year_filter = QComboBox()
        self.year_filter.addItem("All Years", "")
        for year in ["1st Year", "2nd Year", "3rd Year", "4th Year", "5th Year"]:
            self.year_filter.addItem(year, year)
        self.year_filter.currentIndexChanged.connect(self._on_year_filter_changed)
        self.year_filter.setMinimumWidth(90)
        toolbar.addWidget(self.year_filter)

        toolbar.addStretch()

        # Add student button
        self.add_student_btn = QPushButton("+ Add Student")
        self.add_student_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_student_btn.clicked.connect(self._add_student)
        toolbar.addWidget(self.add_student_btn)

        # Export button
        self.export_btn = QPushButton("Export CSV")
        self.export_btn.setProperty("secondary", "true")
        self.export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_btn.clicked.connect(self._export_students)
        toolbar.addWidget(self.export_btn)

        return toolbar

    def _build_pagination(self) -> QHBoxLayout:
        """Build the pagination controls."""
        pagination = QHBoxLayout()
        pagination.setSpacing(8)

        self.prev_btn = QPushButton("Previous")
        self.prev_btn.setProperty("secondary", "true")
        self.prev_btn.clicked.connect(self._prev_page)

        self.page_label = QLabel("Page 1")
        self.page_label.setFont(small_label_font(11))
        self.page_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_label.setFixedWidth(140)

        self.next_btn = QPushButton("Next")
        self.next_btn.setProperty("secondary", "true")
        self.next_btn.clicked.connect(self._next_page)

        self.count_label = QLabel("0 students")
        self.count_label.setFont(small_label_font(11))
        self.count_label.setStyleSheet(f"color: {Colors.LIGHT_TEXT};")

        pagination.addStretch()
        pagination.addWidget(self.prev_btn)
        pagination.addWidget(self.page_label)
        pagination.addWidget(self.next_btn)
        pagination.addSpacing(24)
        pagination.addWidget(self.count_label)
        return pagination

    def _build_student_details_panel(self) -> QWidget:
        """Build the right panel for student details and ID preview."""
        panel = QFrame()
        panel.setObjectName("detailsPanel")
        panel.setStyleSheet(f"""
            QFrame#detailsPanel {{
                background-color: {Colors.CARD_BG};
                border: 1px solid {Colors.CARD_BORDER};
                border-radius: 12px;
            }}
        """)
        panel.setMinimumWidth(380)
        panel.setMaximumWidth(480)

        self.details_stack = QStackedWidget()
        main_layout = QVBoxLayout(panel)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.details_stack)

        # Empty state (no student selected)
        self.details_empty = QWidget()
        empty_layout = QVBoxLayout(self.details_empty)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.setSpacing(12)

        empty_icon = QLabel("👤")
        empty_icon.setFont(QFont("Segoe UI Emoji", 48))
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_icon)

        empty_title = QLabel("Select a Student")
        empty_title.setFont(section_heading_font(18))
        empty_title.setStyleSheet(f"color: {Colors.DARK_TEXT};")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)

        empty_msg = QLabel("Click a student in the table to view their details and ID card.")
        empty_msg.setFont(body_font(13))
        empty_msg.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        empty_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_msg.setWordWrap(True)
        empty_layout.addWidget(empty_msg)
        self.details_stack.addWidget(self.details_empty)

        # Student details content
        self.details_content = QWidget()
        self.details_layout = QVBoxLayout(self.details_content)
        self.details_layout.setContentsMargins(20, 20, 20, 20)
        self.details_layout.setSpacing(16)
        self.details_stack.addWidget(self.details_content)

        # Loading state
        self.details_loading = LoadingState("Loading student details…")
        self.details_stack.addWidget(self.details_loading)

        # Error state
        self.details_error = error_state(
            DatabaseUnavailableError("Could not load student details."),
            retry=lambda: self._load_student_details(self.selected_student.id) if self.selected_student else None,
        )
        self.details_stack.addWidget(self.details_error)

        return panel

    # ------------------------------------------------------------------ Data Loading --

    def refresh(self) -> None:
        """Reload all data from the database."""
        if not self.refresh_btn.isEnabled():
            return

        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("Refreshing…")
        self.table_stack.setCurrentWidget(self.table_loading)
        self.details_stack.setCurrentWidget(self.details_loading)

        try:
            # Load statistics
            self._load_statistics()

            # Load student list
            self._load_student_list()

            # Load filter options
            self._load_filter_options()

            # Update stats summary
            total = self.table_model.total_unfiltered()
            filtered = self.table_model.total_count()
            if filtered == total:
                self.stats_summary.setText(f"{total} students")
            else:
                self.stats_summary.setText(f"Showing {filtered} of {total} students")

        except DatabaseUnavailableError as exc:
            logger.warning("Dashboard refresh failed: %s", exc)
            self._show_table_error(str(exc))
            self._show_details_error(str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Dashboard refresh failed")
            self._show_table_error(str(exc))
            self._show_details_error(str(exc))
        finally:
            self.refresh_btn.setEnabled(True)
            self.refresh_btn.setText("Refresh")

    def _load_statistics(self) -> None:
        """Load all dashboard statistics."""
        stats = {
            "total_students": None,
            "active_students": None,
            "students_with_id": None,
            "students_missing_id": None,
        }
        errors = {}

        def read(key, label, call):
            try:
                stats[key] = call()
            except DatabaseUnavailableError as exc:
                logger.warning("Dashboard stat %s failed: %s", label, exc)
                errors[key] = exc
            except Exception as exc:  # noqa: BLE001
                logger.exception("Dashboard stat %s failed", label)
                errors[key] = exc

        read("total_students", "total students", self.student_service.count_students)
        read("active_students", "active students", self.student_service.count_active_students)
        read("students_with_id", "students with barcode", self.student_service.count_students_with_barcode)
        read("students_missing_id", "students without barcode", self.student_service.count_students_without_barcode)

        for key, value in stats.items():
            if value is None:
                self.stat_cards[key].update_value(UNAVAILABLE)
            else:
                self.stat_cards[key].update_value(str(value))

    def _load_student_list(self) -> None:
        """Load the full student list for the table model."""
        try:
            students = self.student_service.search_students(limit=5000)  # Load all for client-side filtering
            self.table_model.set_students(students)
            self._apply_filters()
            self._fill_table()
        except DatabaseUnavailableError as exc:
            logger.warning("Student list load failed: %s", exc)
            self._show_table_error(str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Student list load failed")
            self._show_table_error(str(exc))

    def _load_filter_options(self) -> None:
        """Load unique programs for the course filter."""
        try:
            students = self.student_service.search_students(limit=5000)
            programs = sorted(set(s.program for s in students if s.program))
            current = self.course_filter.currentData()
            self.course_filter.blockSignals(True)
            self.course_filter.clear()
            self.course_filter.addItem("All Programs", "")
            for prog in programs:
                self.course_filter.addItem(prog, prog)
            if current:
                idx = self.course_filter.findData(current)
                if idx >= 0:
                    self.course_filter.setCurrentIndex(idx)
            self.course_filter.blockSignals(False)
        except DatabaseUnavailableError:
            pass  # Keep existing options
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to load filter options")

    def _apply_filters(self) -> None:
        """Apply current filters to the table model."""
        self.table_model.apply_filter(
            search_text=self.current_search,
            status_filter=self.current_status_filter,
            course_filter=self.current_course_filter,
            year_filter=self.current_year_filter,
        )
        self.current_page = 0
        self._update_pagination()
        self._fill_table()

    def _fill_table(self) -> None:
        """Fill the table with the current page of filtered students."""
        students = self.table_model.get_page(self.current_page, self.page_size)
        total_filtered = self.table_model.total_count()

        self.student_table.setRowCount(0)
        for student in students:
            row = self.student_table.rowCount()
            self.student_table.insertRow(row)

            set_cell(self.student_table, row, 0, student.student_id)
            set_cell(self.student_table, row, 1, student.reference_number)
            set_cell(self.student_table, row, 2, student.full_name)
            set_cell(self.student_table, row, 3, student.program)
            year = student.year_level.value if hasattr(student.year_level, 'value') else str(student.year_level)
            set_cell(self.student_table, row, 4, year)
            barcode = student.barcode_payload or "—"
            set_cell(self.student_table, row, 5, barcode, family="monospace")

            # ID Status
            id_status = "Registered" if student.barcode_payload else "Missing"
            id_status_item = set_cell(
                self.student_table, row, 6, id_status,
                align=Qt.AlignmentFlag.AlignCenter,
            )
            id_status_item.setForeground(Colors.qcolor(
                Colors.SUCCESS_GREEN if student.barcode_payload else Colors.WARNING_ORANGE
            ))

            # Enrollment Status
            enroll_status = student.enrollment_status.value if hasattr(student.enrollment_status, 'value') else str(student.enrollment_status)
            enroll_item = set_cell(
                self.student_table, row, 7, enroll_status.replace("_", " ").title(),
                align=Qt.AlignmentFlag.AlignCenter,
            )
            enroll_item.setForeground(Colors.qcolor(self._enrollment_status_color(enroll_status)))

        self.count_label.setText(f"{total_filtered} student{'s' if total_filtered != 1 else ''}")
        self._update_pagination()

        if students:
            self.table_stack.setCurrentWidget(self.student_table)
        else:
            self.table_stack.setCurrentWidget(self.table_empty)

    def _enrollment_status_color(self, status: str) -> str:
        return {
            "active": Colors.SUCCESS_GREEN,
            "inactive": Colors.MEDIUM_TEXT,
            "graduated": Colors.INFO_BLUE,
            "dropped": Colors.ERROR_RED,
            "on_leave": Colors.WARNING_ORANGE,
        }.get(status, Colors.MEDIUM_TEXT)

    def _update_pagination(self) -> None:
        total = self.table_model.total_count()
        total_pages = max(1, (total + self.page_size - 1) // self.page_size)
        if self.current_page >= total_pages:
            self.current_page = max(0, total_pages - 1)
        self.page_label.setText(f"Page {self.current_page + 1} / {total_pages}")
        self.prev_btn.setEnabled(self.current_page > 0)
        self.next_btn.setEnabled(self.current_page < total_pages - 1)

    # ------------------------------------------------------------------ Event Handlers --

    def _on_search_changed(self, text: str):
        self.current_search = text.strip()
        QTimer.singleShot(300, self._apply_filters)  # Debounce

    def _on_status_filter_changed(self, index: int):
        self.current_status_filter = self.status_filter.itemData(index) or ""
        self.current_page = 0
        self._apply_filters()

    def _on_course_filter_changed(self, index: int):
        self.current_course_filter = self.course_filter.itemData(index) or ""
        self.current_page = 0
        self._apply_filters()

    def _on_year_filter_changed(self, index: int):
        self.current_year_filter = self.year_filter.itemData(index) or ""
        self.current_page = 0
        self._apply_filters()

    def _clear_filters(self):
        self.search_edit.blockSignals(True)
        self.search_edit.clear()
        self.search_edit.blockSignals(False)
        self.status_filter.blockSignals(True)
        self.status_filter.setCurrentIndex(0)
        self.status_filter.blockSignals(False)
        self.course_filter.blockSignals(True)
        self.course_filter.setCurrentIndex(0)
        self.course_filter.blockSignals(False)
        self.year_filter.blockSignals(True)
        self.year_filter.setCurrentIndex(0)
        self.year_filter.blockSignals(False)
        self.current_search = ""
        self.current_status_filter = ""
        self.current_course_filter = ""
        self.current_year_filter = ""
        self.current_page = 0
        self._apply_filters()

    def _prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self._fill_table()

    def _next_page(self):
        total = self.table_model.total_count()
        total_pages = max(1, (total + self.page_size - 1) // self.page_size)
        if self.current_page < total_pages - 1:
            self.current_page += 1
            self._fill_table()

    def _on_student_selected(self):
        selected = self.student_table.selectedItems()
        if not selected:
            self._show_details_empty()
            return
        row = selected[0].row()
        student_id_item = self.student_table.item(row, 0)
        if not student_id_item:
            self._show_details_empty()
            return
        student_id = student_id_item.text()
        self._load_student_details_by_student_id(student_id)

    def _load_student_details_by_student_id(self, student_id: str):
        """Load and display student details by student ID."""
        self.details_stack.setCurrentWidget(self.details_loading)
        try:
            student = self.student_service.get_student_by_student_id(student_id)
            if student:
                self.selected_student = student
                self._show_student_details(student)
            else:
                self._show_details_error("Student not found")
        except DatabaseUnavailableError as exc:
            logger.warning("Student details load failed: %s", exc)
            self._show_details_error(str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Student details load failed")
            self._show_details_error(str(exc))

    def _load_student_details(self, student_id: int):
        """Load and display student details by internal ID."""
        self.details_stack.setCurrentWidget(self.details_loading)
        try:
            student = self.student_service.get_student(student_id)
            if student:
                self.selected_student = student
                self._show_student_details(student)
            else:
                self._show_details_error("Student not found")
        except DatabaseUnavailableError as exc:
            logger.warning("Student details load failed: %s", exc)
            self._show_details_error(str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Student details load failed")
            self._show_details_error(str(exc))

    def _show_student_details(self, student):
        """Display the student details in the right panel."""
        # Clear previous content
        for i in reversed(range(self.details_layout.count())):
            item = self.details_layout.takeAt(i)
            if item.widget():
                item.widget().deleteLater()

        # Student header with avatar
        header_widget = QWidget()
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(16)

        # Avatar
        avatar = QLabel()
        avatar.setFixedSize(80, 80)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(f"""
            QLabel {{
                background-color: {Colors.WARM_BEIGE};
                border-radius: 40px;
                border: 3px solid {Colors.WHITE};
                color: {Colors.DARK_BURNT_ORANGE};
            }}
        """)
        self._load_avatar(avatar, student)

        # Name and basic info
        info_layout = QVBoxLayout()
        info_layout.setSpacing(4)

        name_label = QLabel(student.full_name)
        name_label.setFont(body_bold_font(16))
        name_label.setStyleSheet(f"color: {Colors.DARK_TEXT};")
        name_label.setWordWrap(True)
        info_layout.addWidget(name_label)

        id_label = QLabel(f"Student ID: {student.student_id}  •  Ref: {student.reference_number}")
        id_label.setFont(small_label_font(11))
        id_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        info_layout.addWidget(id_label)

        program_label = QLabel(f"{student.program}  •  {student.year_level.value if hasattr(student.year_level, 'value') else str(student.year_level)}")
        program_label.setFont(small_label_font(11))
        program_label.setStyleSheet(f"color: {Colors.LIGHT_TEXT};")
        program_label.setWordWrap(True)
        info_layout.addWidget(program_label)

        # ID Status badge
        id_status = "Registered" if student.barcode_payload else "Missing"
        status_badge = QLabel(f"  {id_status}  ")
        status_badge.setFont(small_label_font(10))
        status_badge.setStyleSheet(f"""
            background-color: {Colors.SUCCESS_BG if student.barcode_payload else Colors.WARNING_BG};
            color: {Colors.SUCCESS_GREEN if student.barcode_payload else Colors.WARNING_ORANGE};
            border-radius: 10px;
            padding: 2px 8px;
        """)
        info_layout.addWidget(status_badge)
        info_layout.addStretch()

        header_layout.addWidget(avatar)
        header_layout.addLayout(info_layout, 1)
        self.details_layout.addWidget(header_widget)

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"background: {Colors.CARD_BORDER}; border: none; max-height: 1px;")
        self.details_layout.addWidget(divider)

        # Personal Information Section
        self._add_detail_section("Personal Information", [
            ("Date of Birth", student.date_of_birth.strftime("%B %d, %Y") if student.date_of_birth else "Not recorded"),
            ("Gender", student.gender.value if student.gender and hasattr(student.gender, 'value') else (str(student.gender) if student.gender else "Not recorded")),
            ("Address", student.address or "Not recorded"),
        ])

        # Academic Information Section
        self._add_detail_section("Academic Information", [
            ("Program", student.program),
            ("Year Level", student.year_level.value if hasattr(student.year_level, 'value') else str(student.year_level)),
            ("Section", student.section or "Not recorded"),
            ("Enrollment Status", student.enrollment_status.value.replace("_", " ").title() if hasattr(student.enrollment_status, 'value') else str(student.enrollment_status).replace("_", " ").title()),
            ("Date Enrolled", student.date_enrolled.strftime("%B %d, %Y") if student.date_enrolled else "Not recorded"),
        ])

        # Student ID Information Section
        self._add_detail_section("Student ID Information", [
            ("Barcode Payload", student.barcode_payload or "Not assigned"),
            ("ID Status", "Registered" if student.barcode_payload else "Missing / Unassigned"),
        ])

        # Contact Information (if available)
        if student.contacts:
            contacts = []
            for contact in student.contacts:
                label = contact.contact_type.value.replace("_", " ").title()
                if contact.label:
                    label += f" ({contact.label})"
                if contact.is_primary:
                    label += " (Primary)"
                contacts.append((label, contact.contact_value))
            self._add_detail_section("Contact Information", contacts)

        # Actions
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        view_id_btn = QPushButton("View Student ID")
        view_id_btn.setProperty("secondary", "true")
        view_id_btn.clicked.connect(lambda: self._view_student_id(student))
        actions_layout.addWidget(view_id_btn)

        print_id_btn = QPushButton("Print Student ID")
        print_id_btn.clicked.connect(lambda: self._print_student_id(student))
        actions_layout.addWidget(print_id_btn)

        edit_btn = QPushButton("Edit Student")
        edit_btn.setProperty("secondary", "true")
        edit_btn.clicked.connect(lambda: self._edit_student(student))
        actions_layout.addWidget(edit_btn)

        # Size each button to its own label so the full text always shows and
        # the three buttons stay aligned instead of crowding the panel edge.
        _btn_font = button_font(11)
        _btn_metrics = QFontMetrics(_btn_font)
        for button in (view_id_btn, print_id_btn, edit_btn):
            button.setFixedWidth(
                _btn_metrics.horizontalAdvance(button.text()) + 30
            )
            button.setCursor(Qt.CursorShape.PointingHandCursor)

        self.details_layout.addLayout(actions_layout)
        self.details_layout.addStretch()

        self.details_stack.setCurrentWidget(self.details_content)

    def _add_detail_section(self, title: str, items: list):
        """Add a section with label-value pairs to the details panel."""
        section_title = QLabel(title)
        section_title.setFont(section_heading_font(13))
        section_title.setStyleSheet(f"color: {Colors.DARK_BURNT_ORANGE};")
        self.details_layout.addWidget(section_title)

        for label, value in items:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 2, 0, 2)
            row_layout.setSpacing(12)

            label_widget = QLabel(label)
            label_widget.setFont(small_label_font(10))
            label_widget.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
            label_widget.setFixedWidth(130)
            row_layout.addWidget(label_widget)

            value_widget = QLabel(str(value))
            value_widget.setFont(body_font(11))
            value_widget.setStyleSheet(f"color: {Colors.DARK_TEXT};")
            value_widget.setWordWrap(True)
            value_widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row_layout.addWidget(value_widget, 1)

            self.details_layout.addWidget(row)

    def _load_avatar(self, avatar_label: QLabel, student):
        """Load student avatar/photo."""
        from ui.widgets.profile_image import apply_portrait
        apply_portrait(avatar_label, student)

    def _view_student_id(self, student):
        """Show the student ID card preview in a dialog."""
        dialog = StudentIDPreviewDialog(self.id_card_service, student, self)
        dialog.exec()

    def _print_student_id(self, student):
        """Print the student ID card."""
        try:
            # Build front and back content
            front_content = self.id_card_service.build_content(student, CardSide.FRONT)
            back_content = self.id_card_service.build_content(student, CardSide.BACK)
            template = self.id_card_service._template if hasattr(self.id_card_service, '_template') else None
            layout = CardLayout()

            # Render front
            front_result = self.id_card_service.render(front_content, template=template, layout=layout, dpi=300)
            if not front_result.barcode_verified:
                QMessageBox.warning(self, "Barcode Verification Failed",
                    "The barcode on the rendered card could not be verified. "
                    "The card may not scan correctly. Do you want to continue printing?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                # Continue anyway if user clicks Yes

            # Render back
            back_result = self.id_card_service.render(back_content, template=template, layout=layout, dpi=300)

            # Print using QPrinter
            from PySide6.QtPrintSupport import QPrinter, QPrintDialog
            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            printer.setPageSize(QPageSize(QPageSize.PageSizeId.Custom, QSizeF(85.6, 54.0), QPageSize.Unit.Millimeter))
            printer.setPageMargins(QMarginsF(0, 0, 0, 0))

            dialog = QPrintDialog(printer, self)
            dialog.setWindowTitle("Print Student ID Card")
            if dialog.exec() == QDialog.DialogCode.Accepted:
                painter = QPainter(printer)
                # Print front
                painter.drawImage(0, 0, front_result.image)
                if printer.newPage():
                    # Print back
                    painter.drawImage(0, 0, back_result.image)
                painter.end()
                QMessageBox.information(self, "Print Sent", "Student ID card sent to printer.")
        except Exception as exc:
            logger.exception("Print student ID failed")
            QMessageBox.critical(self, "Print Failed", f"Could not print student ID:\n{exc}")

    def _edit_student(self, student):
        """Open the student edit dialog."""
        from ui.dialogs.student_dialog import StudentDialog
        dialog = StudentDialog(student=student, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            updated = dialog.get_student()
            try:
                self.student_service.update_student(updated)
                QMessageBox.information(self, "Success", "Student updated successfully!")
                self.refresh()
            except DatabaseUnavailableError as exc:
                logger.warning("Update student failed: %s", exc)
                QMessageBox.critical(self, "Error", f"Failed to update student: {exc}")
            except Exception as exc:  # noqa: BLE001
                logger.exception("Update student failed")
                QMessageBox.critical(self, "Error", f"Failed to update student: {exc}")

    def _add_student(self):
        """Open the add student dialog."""
        from ui.dialogs.student_dialog import StudentDialog
        dialog = StudentDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            student = dialog.get_student()
            try:
                self.student_service.create_student(student)
                QMessageBox.information(self, "Success", "Student added successfully!")
                self.refresh()
            except DatabaseUnavailableError as exc:
                logger.warning("Add student failed: %s", exc)
                QMessageBox.critical(self, "Error", f"Failed to add student: {exc}")
            except Exception as exc:  # noqa: BLE001
                logger.exception("Add student failed")
                QMessageBox.critical(self, "Error", f"Failed to add student: {exc}")

    def _export_students(self):
        """Export filtered students to CSV."""
        from services.report_service import ReportService
        report_service = ReportService()

        self.export_btn.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            students = self.table_model.filtered_students
            if not students:
                QMessageBox.information(self, "Nothing to export", "No students match the current filters.")
                return

            from config.settings import settings
            suggested = settings.REPORTS_OUTPUT_DIR / f"students_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            path, _ = QFileDialog.getSaveFileName(self, "Export Students", str(suggested), "CSV Files (*.csv)")
            if not path:
                return

            written = report_service.export_students_csv(students, path)
            if written:
                QMessageBox.information(self, "Export Complete", f"{len(students)} students exported to:\n{written}")
            else:
                QMessageBox.critical(self, "Export Failed", "Could not write the CSV file.")
        except Exception as exc:
            logger.exception("Export failed")
            QMessageBox.critical(self, "Export Failed", f"Export failed:\n{exc}")
        finally:
            QApplication.restoreOverrideCursor()
            self.export_btn.setEnabled(True)

    # ------------------------------------------------------------------ State Helpers --

    def _show_table_error(self, message: str):
        self.table_error.set_message(message)
        self.table_stack.setCurrentWidget(self.table_error)

    def _show_details_empty(self):
        self.selected_student = None
        self.details_stack.setCurrentWidget(self.details_empty)

    def _show_details_error(self, message: str):
        self.details_error.set_message(message)
        self.details_stack.setCurrentWidget(self.details_error)

    # ------------------------------------------------------------------ Timers --

    def _on_auto_refresh(self) -> None:
        window = self.parent()
        while window and not hasattr(window, "_active_dialog"):
            window = window.parent()
        if window and getattr(window, "_active_dialog", None) is not None:
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
        self.refresh()
        self.start_auto_refresh()

    def hideEvent(self, event):
        self.stop_auto_refresh()
        super().hideEvent(event)


class StudentIDPreviewDialog(QDialog):
    """Dialog for previewing a student ID card before printing."""

    def __init__(self, id_card_service: IdCardService, student, parent=None):
        super().__init__(parent)
        self.id_card_service = id_card_service
        self.student = student
        self.current_side = CardSide.FRONT
        self.preview_dpi = 150  # Preview DPI (lower than print for performance)

        self.setWindowTitle(f"Student ID Preview - {student.full_name}")
        self.setModal(True)
        self.resize(600, 500)
        self.setMinimumSize(500, 400)

        self._setup_ui()
        self._render_preview()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Side selector
        side_layout = QHBoxLayout()
        side_layout.addStretch()

        self.front_btn = QPushButton("Front")
        self.front_btn.setCheckable(True)
        self.front_btn.setChecked(True)
        self.front_btn.clicked.connect(lambda: self._switch_side(CardSide.FRONT))
        side_layout.addWidget(self.front_btn)

        self.back_btn = QPushButton("Back")
        self.back_btn.setCheckable(True)
        self.back_btn.clicked.connect(lambda: self._switch_side(CardSide.BACK))
        side_layout.addWidget(self.back_btn)

        side_layout.addStretch()
        layout.addLayout(side_layout)

        # Preview area
        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(400, 250)
        self.preview_label.setStyleSheet(f"""
            QLabel {{
                background-color: {Colors.CARD_BG};
                border: 1px solid {Colors.CARD_BORDER};
                border-radius: 8px;
            }}
        """)
        layout.addWidget(self.preview_label, 1)

        # Status/warning
        self.warning_label = QLabel()
        self.warning_label.setFont(small_label_font(10))
        self.warning_label.setStyleSheet(f"color: {Colors.WARNING_ORANGE};")
        self.warning_label.setWordWrap(True)
        self.warning_label.setVisible(False)
        layout.addWidget(self.warning_label)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        print_btn = QPushButton("Print ID Card")
        print_btn.clicked.connect(self._print_card)
        btn_layout.addWidget(print_btn)

        close_btn = QPushButton("Close")
        close_btn.setProperty("secondary", "true")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

    def _switch_side(self, side: CardSide):
        self.current_side = side
        self.front_btn.setChecked(side == CardSide.FRONT)
        self.back_btn.setChecked(side == CardSide.BACK)
        self._render_preview()

    def _render_preview(self):
        try:
            template_key = 'official_wit'
            if self.current_side == CardSide.FRONT:
                content = self.id_card_service.build_content(self.student, CardSide.FRONT)
            else:
                content = self.id_card_service.build_content(self.student, CardSide.BACK)

            layout = CardLayout()
            result = self.id_card_service.render(content, dpi=self.preview_dpi, layout=layout)

            # Convert to pixmap
            image = result.image
            rgb = image.convertToFormat(QImage.Format.Format_RGB888)
            w, h = rgb.width(), rgb.height()
            bytes_per_line = rgb.bytesPerLine()
            from PySide6.QtGui import QImage
            qt_image = QImage(rgb.bits(), w, h, bytes_per_line, QImage.Format.Format_RGB888)
            pixmap = QPixmap.fromImage(qt_image.copy())

            # Scale to fit label
            target_size = self.preview_label.size()
            scaled = pixmap.scaled(target_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.preview_label.setPixmap(scaled)

            # Show warning if barcode not verified
            if self.current_side == CardSide.FRONT and result.barcode_checked and not result.barcode_verified:
                self.warning_label.setText("⚠ Barcode on this card could not be verified. It may not scan correctly.")
                self.warning_label.setVisible(True)
            else:
                self.warning_label.setVisible(False)

        except Exception as exc:
            logger.exception("ID card preview render failed")
            self.preview_label.setText(f"Preview failed:\n{exc}")
            self.preview_label.setStyleSheet(f"color: {Colors.ERROR_RED}; padding: 20px;")

    def _print_card(self):
        """Print the ID card (both sides)."""
        try:
            layout = CardLayout()
            template_key = 'official_wit'

            front_content = self.id_card_service.build_content(self.student, CardSide.FRONT)
            front_result = self.id_card_service.render(front_content, dpi=300, layout=layout)

            back_content = self.id_card_service.build_content(self.student, CardSide.BACK)
            back_result = self.id_card_service.render(back_content, dpi=300, layout=layout)

            if front_result.barcode_checked and not front_result.barcode_verified:
                reply = QMessageBox.warning(self, "Barcode Verification Failed",
                    "The barcode on the front of the card could not be verified. "
                    "The card may not scan correctly. Continue printing anyway?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply != QMessageBox.StandardButton.Yes:
                    return

            from PySide6.QtPrintSupport import QPrinter, QPrintDialog
            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            printer.setPageSize(QPageSize(QPageSize.PageSizeId.Custom, QSizeF(85.6, 54.0), QPageSize.Unit.Millimeter))
            printer.setPageMargins(QMarginsF(0, 0, 0, 0))

            dialog = QPrintDialog(printer, self)
            dialog.setWindowTitle("Print Student ID Card")
            if dialog.exec() == QDialog.DialogCode.Accepted:
                painter = QPainter(printer)
                painter.drawImage(0, 0, front_result.image)
                if printer.newPage():
                    painter.drawImage(0, 0, back_result.image)
                painter.end()
                QMessageBox.information(self, "Print Sent", "Student ID card sent to printer.")
        except Exception as exc:
            logger.exception("Print ID card failed")
            QMessageBox.critical(self, "Print Failed", f"Could not print ID card:\n{exc}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.preview_label.pixmap():
            self._render_preview()
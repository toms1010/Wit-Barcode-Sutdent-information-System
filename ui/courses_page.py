"""Courses & Subjects page.

Lists courses and their subjects straight from the database. No sample or
placeholder rows are generated; when the database is unreachable the page shows
an error with a retry rather than an empty table.
"""

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QPushButton, QMessageBox, QStackedWidget,
)

from database.connection import DatabaseUnavailableError
from services.student_service import StudentService
from styles.colors import Colors
from styles.typography import (
    page_heading_font, small_label_font, body_font, button_font,
)
from ui.db_error import show_db_error
from ui.widgets.responsive_table import ColumnSpec, configure_table, count_column, money_column
from ui.widgets.empty_state import EmptyState, LoadingState, error_state

logger = logging.getLogger(__name__)


class CoursesPage(QWidget):
    def __init__(self, student_service: StudentService, parent=None):
        super().__init__(parent)
        self.student_service = student_service
        self.setObjectName("coursesPage")
        self._setup_ui()
        self.refresh()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        header = QFrame()
        header.setFixedHeight(80)
        header.setStyleSheet(
            f"background-color: {Colors.ORANGE};"
            "border-top-left-radius: 8px; border-top-right-radius: 8px;"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 0, 24, 0)

        title = QLabel("Courses & Subjects")
        title.setFont(page_heading_font(24))
        title.setStyleSheet("color: white;")
        header_layout.addWidget(title)
        header_layout.addStretch()

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setFont(button_font())
        refresh_btn.setStyleSheet(
            "QPushButton { color: white; background: rgba(255,255,255,0.1);"
            "border: 1px solid white; }"
            "QPushButton:hover { background: rgba(255,255,255,0.2); }"
        )
        refresh_btn.clicked.connect(self.refresh)
        header_layout.addWidget(refresh_btn)

        main_layout.addWidget(header)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(24, 24, 24, 24)
        body_layout.setSpacing(16)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Course:"))

        self.course_combo = QComboBox()
        self.course_combo.setMinimumWidth(320)
        self.course_combo.currentIndexChanged.connect(self._load_subjects)
        controls.addWidget(self.course_combo)
        controls.addStretch()

        self.status_label = QLabel("")
        self.status_label.setFont(small_label_font(11))
        self.status_label.setStyleSheet(f"color: {Colors.LIGHT_TEXT};")
        controls.addWidget(self.status_label)

        body_layout.addLayout(controls)

        # A state stack, so "this course has no subjects", "nothing has been
        # loaded yet" and "the database is unreachable" are three different
        # things on screen rather than three identical empty tables.
        self.stack = QStackedWidget()
        body_layout.addWidget(self.stack, 1)

        self.table = QTableWidget()
        configure_table(
            self.table,
            [
                ColumnSpec("Code", width=120),
                ColumnSpec("Subject", width=220, stretch=True),
                count_column("Credits", 90),
                count_column("Lecture Hrs", 110),
                count_column("Lab Hrs", 90),
                ColumnSpec("Department", width=180, stretch=True),
            ],
        )
        self.stack.addWidget(self.table)

        self.loading_state = LoadingState("Reading the catalogue…")
        self.stack.addWidget(self.loading_state)

        self.empty_state = EmptyState(
            "Nothing to show yet",
            "Subjects appear here once a course is selected.",
            icon="courses",
            action_text="Reload",
            action=self.refresh,
            parent=self,
        )
        self.stack.addWidget(self.empty_state)

        self.error_view = error_state(
            DatabaseUnavailableError("The course catalogue could not be read."),
            retry=self.refresh,
        )
        self.stack.addWidget(self.error_view)

        main_layout.addWidget(body)

    def _show(self, state: str) -> None:
        """Switch between the loading, content, empty and failed views."""
        self.stack.setCurrentWidget(
            {
                "content": self.table,
                "loading": self.loading_state,
                "empty": self.empty_state,
                "error": self.error_view,
            }[state]
        )

    def refresh(self):
        """Reload the course list from the database."""
        self._show("loading")
        self.status_label.setText("Reading…")
        self.course_combo.blockSignals(True)
        self.course_combo.clear()
        try:
            courses = self.student_service.get_courses()
        except DatabaseUnavailableError as exc:
            self.course_combo.blockSignals(False)
            self.table.setRowCount(0)
            self.status_label.setText("Course list unavailable")
            self._show("error")
            show_db_error(self, exc, retry_callback=self.refresh)
            return
        except Exception as exc:  # noqa: BLE001 - reported on screen
            self.course_combo.blockSignals(False)
            logger.exception("Course list could not be read")
            self.status_label.setText(f"Course list unavailable: {exc}")
            self._show("error")
            return

        for course in courses:
            self.course_combo.addItem(
                f"{course.course_code} — {course.course_name}", course.id
            )
        self.course_combo.blockSignals(False)

        if not courses:
            self.status_label.setText("No courses recorded")
            self.table.setRowCount(0)
            self.empty_state.heading_label.setText("No courses on file")
            self.empty_state.set_message(
                "Nothing has been recorded in the course catalogue yet."
            )
            self._show("empty")
            return

        self.status_label.setText(f"{len(courses)} course(s)")
        self._load_subjects()

    def _load_subjects(self):
        course_id = self.course_combo.currentData()
        self.table.setRowCount(0)
        if course_id is None:
            self._show("empty")
            return

        self._show("loading")
        try:
            subjects = self.student_service.get_subjects_by_course(course_id)
        except DatabaseUnavailableError as exc:
            self.status_label.setText("Subject list unavailable")
            self._show("error")
            show_db_error(self, exc, retry_callback=self._load_subjects)
            return
        except Exception as exc:  # noqa: BLE001 - reported on screen
            logger.exception("Subject list could not be read")
            self.status_label.setText(f"Subject list unavailable: {exc}")
            self._show("error")
            return

        for subject in subjects:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(subject.subject_code))
            self.table.setItem(row, 1, QTableWidgetItem(subject.subject_name))
            self.table.setItem(row, 2, QTableWidgetItem(str(subject.credits)))
            self.table.setItem(row, 3, QTableWidgetItem(str(subject.lecture_hours)))
            self.table.setItem(row, 4, QTableWidgetItem(str(subject.lab_hours)))
            self.table.setItem(row, 5, QTableWidgetItem(subject.course.department
                                                          if subject.course else ""))

        if not subjects:
            self.empty_state.heading_label.setText("No subjects in this course")
            self.empty_state.set_message(
                "This course is recorded but no subject is filed under it yet."
            )
            self.status_label.setText("0 subject(s)")
            self._show("empty")
            return

        self.status_label.setText(f"{len(subjects)} subject(s)")
        self._show("content")

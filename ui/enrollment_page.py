from PySide6.QtWidgets import (
    QScrollArea,
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QFrame, QComboBox, QMessageBox, QDialog, QFormLayout, QDateEdit,
    QSpinBox, QDoubleSpinBox, QTabWidget, QDialogButtonBox, QApplication,
    QSizePolicy
)
from PySide6.QtCore import Qt, QDate
from styles.typography import body_font, body_bold_font, page_heading_font, small_label_font, button_font
from styles.colors import Colors
from database.connection import DatabaseUnavailableError
from services.student_service import StudentService
from services.com_summary import format_peso, format_schedule
from ui.formatting import NOT_RECORDED, format_date, format_datetime
from ui.db_error import show_db_error
from ui.widgets.empty_state import EmptyState
from ui.widgets.profile_image import apply_portrait
from ui.widgets.responsive_table import (
    ColumnSpec, configure_table, count_column, money_column, fit_height, set_cell,
)
from ui.widgets.panel import card_frame
from config.settings import settings
from models.enrollment import Enrollment, EnrollmentSubject, Subject, Semester, EnrollmentStatus, SubjectStatus
from models.student import Student
from dataclasses import replace
import logging

logger = logging.getLogger(__name__)

def _subject_status_colour(status: str) -> str:
    return {
        SubjectStatus.ENROLLED.value: Colors.SUCCESS_GREEN,
        SubjectStatus.DROPPED.value: Colors.ERROR_RED,
        SubjectStatus.COMPLETED.value: Colors.INFO_BLUE,
    }.get(str(status), Colors.MEDIUM_TEXT)


class EnrollmentPage(QWidget):
    def __init__(self, student_service: StudentService, parent=None):
        super().__init__(parent)
        self.student_service = student_service
        self.current_student: Student = None
        self.current_enrollment: Enrollment = None
        #: True while a write is in flight, so a second click cannot double it.
        self._busy = False
        
        self.setObjectName("enrollmentPage")
        self._setup_ui()
    
    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Header
        header = QFrame()
        header.setFixedHeight(80)
        header.setStyleSheet(f"background-color: {Colors.ORANGE}; border-top-left-radius: 8px; border-top-right-radius: 8px;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 0, 24, 0)
        
        title = QLabel("Subject Enrollment")
        title.setFont(page_heading_font(24))
        title.setStyleSheet("color: white;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        
        main_layout.addWidget(header)
        
        # Content
        #
        # Stacked, not side by side. With the picker in a left column the term's
        # subject table had about 700px for eight columns and scrolled sideways
        # with a column cut in half; the picker needs a fraction of the width and
        # the table needs all of it.
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 24, 24, 24)
        content_layout.setSpacing(16)
        
        # Left panel - Student selection
        left_panel = card_frame("enrollmentLeftPanel")
        # A strip across the top: wide enough for the four columns it holds, and
        # short enough that the term's subjects below it stay the main event.
        left_panel.setFixedHeight(232)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(16, 16, 16, 16)
        left_layout.setSpacing(16)
        
        # Student search
        search_layout = QVBoxLayout()
        search_layout.setSpacing(8)

        heading = QLabel("Select Student")
        heading.setFont(body_bold_font(13))
        search_layout.addWidget(heading)
        
        self.student_search = QLineEdit()
        self.student_search.setPlaceholderText("Search by name, ID, or reference...")
        self.student_search.setClearButtonEnabled(True)
        self.student_search.textChanged.connect(self._filter_students)
        search_layout.addWidget(self.student_search)
        
        self.student_list = QTableWidget()
        configure_table(
            self.student_list,
            [
                ColumnSpec("Student ID", width=112),
                ColumnSpec("Reference", width=92),
                ColumnSpec("Name", width=150, stretch=True),
                ColumnSpec("Program", width=130, stretch=True),
            ],
        )
        self.student_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.student_list.itemSelectionChanged.connect(self._on_student_selected)
        
        self._filter_hint = QLabel("")
        self._filter_hint.setFont(small_label_font(11))
        self._filter_hint.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        self._filter_hint.setWordWrap(True)
        self._filter_hint.setVisible(False)
        search_layout.addWidget(self._filter_hint)

        self.student_list.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        search_layout.addWidget(self.student_list)
        left_layout.addLayout(search_layout)

        content_layout.addWidget(left_panel, 0)
        
        # Right panel - Enrollment details
        right_panel = card_frame("enrollmentRightPanel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(20, 20, 20, 20)
        right_layout.setSpacing(20)
        
        # No selection state. It explains what the page is for rather than
        # sitting as one grey line; the action appears once a student is chosen,
        # because creating an enrollment needs one.
        self.empty_state = EmptyState(
            "No student selected",
            "Choose a student from the list to see and manage the subjects they "
            "are enrolled in.",
            icon="students",
            parent=self,
        )
        right_layout.addWidget(self.empty_state, 1)
        
        # Enrollment content (hidden initially)
        self.enrollment_widget = QWidget()
        self.enrollment_widget.setVisible(False)
        enroll_layout = QVBoxLayout(self.enrollment_widget)
        enroll_layout.setContentsMargins(0, 0, 0, 0)
        enroll_layout.setSpacing(16)
        
        # Student info header
        self.student_info_header = QFrame()
        # Scoped by object name, and compact: a 150px header pushed the term's
        # subjects below the fold on a 900px-tall window.
        self.student_info_header.setObjectName("enrollmentStudentHeader")
        self.student_info_header.setStyleSheet(f"""
            QFrame#enrollmentStudentHeader {{
                background-color: {Colors.CREAM};
                border-radius: 8px;
            }}
        """)
        info_layout = QHBoxLayout(self.student_info_header)
        info_layout.setContentsMargins(16, 10, 16, 10)
        info_layout.setSpacing(12)
        
        # The photograph comes from students.profile_image_path through the
        # shared loader, not from an initials disc.
        self.student_avatar = QLabel()
        self.student_avatar.setObjectName("studentPortrait")
        self.student_avatar.setFixedSize(44, 44)
        self.student_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.student_avatar.setStyleSheet(
            f"border: 1px solid {Colors.BORDER}; border-radius: 10px;"
            f"background-color: {Colors.SURFACE_MUTED};"
        )
        
        self.student_details = QVBoxLayout()
        self.student_name_label = QLabel()
        self.student_name_label.setFont(body_bold_font(16))
        self.student_details.addWidget(self.student_name_label)
        
        self.student_meta_label = QLabel()
        self.student_meta_label.setFont(small_label_font(11))
        self.student_meta_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        self.student_details.addWidget(self.student_meta_label)
        
        info_layout.addWidget(self.student_avatar)
        info_layout.addLayout(self.student_details)
        info_layout.addStretch()
        
        enroll_layout.addWidget(self.student_info_header)
        
        # Tabs for enrollments
        self.enrollment_tabs = QTabWidget()
        # Enough for a caption row, a few subjects and the Add Subject button.
        # Without a floor the pane collapses to a strip when the window is short.
        self.enrollment_tabs.setMinimumHeight(240)
        self.enrollment_tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border: 1px solid {Colors.CARD_BORDER}; border-radius: 8px; background: white; }}
            QTabBar::tab {{ background: {Colors.CREAM}; padding: 10px 20px; border: 1px solid {Colors.CARD_BORDER}; border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px; }}
            QTabBar::tab:selected {{ background: white; color: {Colors.ORANGE}; border-color: {Colors.CARD_BORDER}; border-bottom: 1px solid white; }}
        """)
        enroll_layout.addWidget(self.enrollment_tabs)
        
        # New enrollment button
        self.new_enroll_btn = QPushButton("+ New Enrollment")
        self.new_enroll_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_enroll_btn.setToolTip("Record a new enrollment term for this student")
        self.new_enroll_btn.clicked.connect(self._create_new_enrollment)
        enroll_layout.addWidget(self.new_enroll_btn)
        
        self.status_bar_message = QLabel("")
        self.status_bar_message.setFont(small_label_font(11))
        self.status_bar_message.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        self.status_bar_message.setWordWrap(True)
        self.status_bar_message.setVisible(False)

        # The details side scrolls vertically. A term table, a student header
        # and an action row do not fit a short window, and without this the tab
        # pane was squeezed until the subject table had no height at all.
        details_holder = QWidget()
        details_layout = QVBoxLayout(details_holder)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(16)
        details_layout.addWidget(self.status_bar_message)
        details_layout.addWidget(self.enrollment_widget, 1)

        details_scroll = QScrollArea()
        details_scroll.setWidgetResizable(True)
        details_scroll.setFrameStyle(QFrame.Shape.NoFrame)
        details_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        details_scroll.setWidget(details_holder)
        right_layout.addWidget(details_scroll, 1)

        content_layout.addWidget(right_panel, 1)
        main_layout.addWidget(content)
        
        self._load_student_list()
    
    def _load_student_list(self):
        try:
            students = self.student_service.search_students(limit=500)
        except DatabaseUnavailableError as exc:
            self.student_list.setRowCount(0)
            show_db_error(self, exc, retry_callback=self._load_student_list)
            return
        self.student_list.setRowCount(0)
        for student in students:
            row = self.student_list.rowCount()
            self.student_list.insertRow(row)
            for column, value in enumerate(
                (student.student_id, student.reference_number, student.full_name, student.program)
            ):
                item = set_cell(self.student_list, row, column, value)
                # The internal id rides on every cell, so selecting any column
                # resolves the same student.
                item.setData(Qt.ItemDataRole.UserRole, student.id)
        # Minimum, not a fixed height: the strip's spare height belongs to the
        # table, and a fixed height left a blank band under three rows.
        self.student_list.setMinimumHeight(120)
    
    def _filter_students(self, text: str):
        """Hide non-matching rows and keep the panel honest about them.

        Filtering used to hide rows only: a student selected before the search
        stayed selected while invisible, so the page went on showing (and
        offering to add subjects to) someone the list no longer showed.
        """
        needle = text.lower().strip()
        visible_rows = 0
        selected_still_visible = False
        selected_id = self.current_student.id if self.current_student else None

        for row in range(self.student_list.rowCount()):
            match = False
            for col in range(4):
                item = self.student_list.item(row, col)
                if item and needle in item.text().lower():
                    match = True
                    break
            self.student_list.setRowHidden(row, not match)
            if match:
                visible_rows += 1
                item = self.student_list.item(row, 0)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) == selected_id:
                    selected_still_visible = True

        if selected_still_visible:
            self._filter_hint.setText("" if not needle else f"{visible_rows} match the search")
            self._filter_hint.setVisible(bool(needle))
            return

        if selected_id is not None:
            # The selection is no longer part of what the list shows: drop it
            # rather than leave another student's record on screen.
            self.student_list.clearSelection()
            self._clear_selection()

        if needle and visible_rows == 0:
            self._filter_hint.setText(f"No student matches “{text.strip()}”")
            self._filter_hint.setStyleSheet(f"color: {Colors.WARNING_ORANGE};")
            self._filter_hint.setVisible(True)
        else:
            self._filter_hint.setVisible(bool(needle))
            if needle:
                self._filter_hint.setText(f"{visible_rows} match the search")
                self._filter_hint.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
    
    def _on_student_selected(self):
        selected = self.student_list.selectedItems()
        if not selected:
            self._clear_selection()
            return

        student_id = selected[0].data(Qt.ItemDataRole.UserRole)
        try:
            student = self.student_service.get_student(student_id)
        except DatabaseUnavailableError as exc:
            # The lookup is re-read rather than trusted from the table, so a
            # failure must not leave the previous student's record on screen.
            logger.warning("Could not read student %s: %s", student_id, exc)
            self.student_list.clearSelection()
            self._clear_selection()
            show_db_error(self, exc, retry_callback=self._load_enrollment_panel)
            return
        except Exception as exc:  # noqa: BLE001 - reported to the operator
            logger.exception("Could not read student %s", student_id)
            QMessageBox.critical(self, "Could not open that student", str(exc))
            self.student_list.clearSelection()
            self._clear_selection()
            return

        if student is None:
            # The row was deleted by someone else; the table is now stale.
            QMessageBox.warning(
                self, "Student not found",
                "That student is no longer on file. The list has been refreshed.",
            )
            self.student_list.clearSelection()
            self._clear_selection()
            self._load_student_list()
            return

        self.current_student = student
        self._update_student_header()
        self._load_enrollment_panel()

    def _clear_selection(self) -> None:
        """Return the page to its no-selection state.

        Every student-specific value is reset together. Leaving the previous
        student's name, tabs or subjects behind is how one student's record ends
        up displayed under another student's name.
        """
        self.current_student = None
        self.current_enrollment = None
        self.enrollment_tabs.clear()
        self.student_name_label.clear()
        self.student_meta_label.clear()
        self.student_avatar.clear()
        self.student_avatar.setText("")
        self.enrollment_widget.setVisible(False)
        self.empty_state.setVisible(True)

    def _load_enrollment_panel(self) -> None:
        """Read this student's enrollments and show them, or explain the gap."""
        if self.current_student is None:
            return
        try:
            self._load_enrollments()
        except DatabaseUnavailableError as exc:
            logger.warning("Enrollments for student %s failed: %s", self.current_student.id, exc)
            self.enrollment_tabs.clear()
            self.enrollment_widget.setVisible(True)
            self.empty_state.setVisible(False)
            self._show_panel_message(
                "Enrollments unavailable",
                f"{self.current_student.full_name}'s enrollment records could not be "
                f"read, so the subjects below are not current.",
            )
            show_db_error(self, exc, retry_callback=self._load_enrollment_panel)
            return
        except Exception as exc:  # noqa: BLE001 - reported to the operator
            logger.exception("Enrollments for student %s failed", self.current_student.id)
            self.enrollment_tabs.clear()
            self.enrollment_widget.setVisible(True)
            self.empty_state.setVisible(False)
            self._show_panel_message(
                "Enrollments unavailable", f"The enrollment records could not be read:\n{exc}"
            )
            return

        self.empty_state.setVisible(False)
        self.enrollment_widget.setVisible(True)

    def _show_panel_message(self, heading: str, message: str) -> None:
        """Replace the tab area with one explanatory panel."""
        self.enrollment_tabs.clear()
        panel = EmptyState(heading, message, icon="enrollment", parent=self)
        self.enrollment_tabs.addTab(panel, "Unavailable")
    
    def _update_student_header(self):
        if not self.current_student:
            return
        
        apply_portrait(self.student_avatar, self.current_student, 44)
        self.student_name_label.setText(self.current_student.full_name)
        self.student_meta_label.setText(
            f"{self.current_student.student_id} • {self.current_student.reference_number} • "
            f"{self.current_student.program} • {self.current_student.year_level.value if hasattr(self.current_student.year_level, 'value') else self.current_student.year_level}"
        )
    
    def _load_enrollments(self):
        enrollments = self.student_service.get_enrollments_by_student(self.current_student.id)

        self.enrollment_tabs.clear()
        
        for enrollment in enrollments:
            tab = self._create_enrollment_tab(enrollment)
            semester_label = f"{enrollment.academic_year} - {enrollment.semester.value if hasattr(enrollment.semester, 'value') else enrollment.semester}"
            self.enrollment_tabs.addTab(tab, semester_label)
        
        if not enrollments:
            # An empty tab whose action is the button that actually creates the
            # record, rather than a sentence telling the user to find it.
            empty_tab = EmptyState(
                "No enrollments recorded",
                f"{self.current_student.full_name} has no enrollment on file for any "
                "term yet, so there are no subjects to manage.",
                icon="enrollment",
                action_text="Create enrollment",
                action=self._create_new_enrollment,
                parent=self,
            )
            self.enrollment_tabs.addTab(empty_tab, "No Enrollments")
    
    def _create_enrollment_tab(self, enrollment: Enrollment) -> QWidget:
        """One tab per term.

        The content is wrapped in a scroll area: the pane's height is whatever is
        left after the student header and the tabs, and with a fixed-height
        subject table the Add Subject button was pushed out of the pane and drew
        on top of the last row instead of below it.
        """
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameStyle(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        outer.addWidget(scroll)

        panel = QWidget()
        scroll.setWidget(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        # Enrollment header
        header_layout = QHBoxLayout()
        status_text = getattr(enrollment.status, "value", enrollment.status)
        enrolled = [
            row for row in enrollment.subjects
            if getattr(row.status, "value", row.status) == SubjectStatus.ENROLLED.value
        ]
        header_layout.addWidget(self._caption(
            f"Status: {status_text} • {len(enrolled)} of {len(enrollment.subjects)} subject(s) enrolled"
        ))
        header_layout.addWidget(self._caption(f"Enrolled: {format_date(enrollment.enrollment_date)}"))
        header_layout.addWidget(self._caption(f"Total Credits: {enrollment.total_credits}"))
        header_layout.addStretch()

        # Wired to a real edit. It used to be created and never connected, so a
        # button on every enrollment did nothing at all.
        edit_enroll_btn = QPushButton("Edit Enrollment")
        edit_enroll_btn.setProperty("secondary", "true")
        edit_enroll_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        edit_enroll_btn.setMinimumWidth(
            edit_enroll_btn.fontMetrics().horizontalAdvance("Edit Enrollment") + 28
        )
        edit_enroll_btn.setToolTip(
            f"Change the term, date or status of {enrollment.academic_year} "
            f"{getattr(enrollment.semester, 'value', enrollment.semester)}"
        )
        edit_enroll_btn.clicked.connect(lambda: self._edit_enrollment(enrollment))
        header_layout.addWidget(edit_enroll_btn)
        layout.addLayout(header_layout)

        # Subjects table
        table = QTableWidget()
        configure_table(
            table,
            # Code and Section are one column and the two fees are one, so the
            # ten columns that used to be squeezed into a half-width panel
            # become eight that fit. Nothing is dropped: both fee values and the
            # section are still on the row.
            [
                ColumnSpec("Code / Section", width=118),
                ColumnSpec("Subject", width=150, stretch=True),
                count_column("Credits", width=70),
                ColumnSpec("Time / Days", width=120, stretch=True),
                ColumnSpec("Room", width=74),
                count_column("Period", width=68),
                ColumnSpec("Status", width=84),
                money_column("Fees", width=124),
            ],
        )

        for es in enrollment.subjects:
            row = table.rowCount()
            table.insertRow(row)
            subject = es.subject
            # "Not recorded" rather than a zero or an "N/A": a missing credit
            # count or a fee that was never entered is not the same statement as
            # 0 credits or a free subject.
            code = subject.subject_code if subject else NOT_RECORDED
            code_cell = set_cell(
                table, row, 0,
                f"{code} • {es.section}" if es.section else code,
            )
            if es.section:
                code_cell.setToolTip(f"{code} — section {es.section}")
            set_cell(table, row, 1, subject.subject_name if subject else NOT_RECORDED)
            set_cell(
                table, row, 2,
                str(subject.credits) if subject else NOT_RECORDED,
                align=Qt.AlignmentFlag.AlignCenter,
            )
            set_cell(table, row, 3, format_schedule(es.schedule_time, es.schedule_days))
            set_cell(table, row, 4, es.room or NOT_RECORDED)
            set_cell(
                table, row, 5, str(es.period) if es.period else NOT_RECORDED,
                align=Qt.AlignmentFlag.AlignCenter,
            )
            subject_status = getattr(es.status, "value", es.status)
            status_cell = set_cell(
                table, row, 6, str(subject_status).title(),
                align=Qt.AlignmentFlag.AlignCenter,
            )
            status_cell.setForeground(Colors.qcolor(_subject_status_colour(subject_status)))
            fees = " / ".join(
                format_peso(value, blank_for_zero=True)
                for value in (es.lab_fee, es.other_fee)
                if value not in (None, 0)
            )
            fees_cell = set_cell(
                table, row, 7, fees or NOT_RECORDED,
                align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )
            fees_cell.setToolTip(
                f"Lab fee: {format_peso(es.lab_fee)}\n"
                f"Other fee: {format_peso(es.other_fee)}"
            )

        # Expanding with a modest floor: the table takes the room the tab has,
        # scrolls when the term is long, and never collapses to nothing.
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        table.setMinimumHeight(150)
        layout.addWidget(table, 1)

        if not enrollment.subjects:
            # An enrollment with no subject rows is a real state: the record
            # exists but nothing has been added to it yet.
            layout.addWidget(EmptyState(
                "No subjects on this enrollment",
                "Use Add Subject to record the subjects for this term. They are what "
                "a Certificate of Matriculation is produced from.",
                icon="subjects",
                parent=self,
            ))

        layout.addStretch(1)

        # Actions, pinned below the scroll area so they stay reachable however
        # long the subject list is or how short the pane becomes.
        actions_layout = QHBoxLayout()
        actions_layout.setContentsMargins(16, 0, 16, 12)
        add_subject_btn = QPushButton("+ Add Subject")
        add_subject_btn.clicked.connect(lambda: self._add_subject(enrollment))
        actions_layout.addWidget(add_subject_btn)
        actions_layout.addStretch()
        outer.addLayout(actions_layout)

        self.current_enrollment = enrollment
        return tab

    @staticmethod
    def _caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setFont(small_label_font(11))
        label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        return label

    def _edit_enrollment(self, enrollment: Enrollment) -> None:
        dialog = EditEnrollmentDialog(enrollment, parent=self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            updated = dialog.get_enrollment()
        finally:
            dialog.deleteLater()

        self._set_busy(True, "Saving the enrollment…")
        try:
            try:
                saved = self.student_service.update_enrollment(updated)
            except DatabaseUnavailableError as exc:
                logger.warning("Edit enrollment failed: %s", exc)
                show_db_error(self, exc, retry_callback=lambda: self._edit_enrollment(enrollment))
                return
            except Exception as exc:  # noqa: BLE001 - reported to the operator
                logger.exception("Edit enrollment failed")
                QMessageBox.critical(self, "Could not save the enrollment", str(exc))
                return

            if not saved:
                QMessageBox.warning(
                    self, "Not saved",
                    "The database reported that nothing was changed.",
                )
        finally:
            self._set_busy(False)

        self._load_enrollment_panel()

    def _set_busy(self, busy: bool, message: str = "") -> None:
        """Guard against a second write while one is in flight.

        The tab buttons are disabled as well as the cursor being changed, so a
        second click cannot start a second save on the same enrollment.
        """
        self._busy = busy
        self.new_enroll_btn.setEnabled(not busy)
        self.status_bar_message.setText(message if busy else "")
        self.status_bar_message.setVisible(bool(message))
        if busy:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        else:
            QApplication.restoreOverrideCursor()

    def _create_new_enrollment(self):
        if self.current_student is None:
            # Reachable only through the empty state, which is shown when no
            # student is selected; the dialog needs a student to attach to.
            QMessageBox.information(
                self, "No student selected",
                "Choose a student from the list first: an enrollment belongs to a student.",
            )
            return

        dialog = NewEnrollmentDialog(self.current_student, self.student_service, parent=self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            enrollment = dialog.get_enrollment()
        finally:
            dialog.deleteLater()

        self._set_busy(True, "Creating the enrollment…")
        try:
            try:
                enrollment_id = self.student_service.create_enrollment(enrollment)
            except DatabaseUnavailableError as exc:
                logger.warning("Create enrollment failed: %s", exc)
                show_db_error(self, exc, retry_callback=self._create_new_enrollment)
                return
            except Exception as exc:  # noqa: BLE001 - reported to the operator
                logger.exception("Create enrollment failed")
                QMessageBox.critical(
                    self, "Could not create the enrollment",
                    f"No enrollment was recorded.\n\n{exc}",
                )
                return
        finally:
            self._set_busy(False)

        if not enrollment_id:
            QMessageBox.warning(
                self, "Not created",
                "The database reported that no enrollment was written.",
            )
            return

        QMessageBox.information(
            self, "Enrollment created",
            f"{self.current_student.full_name} is enrolled for "
            f"{enrollment.academic_year} {getattr(enrollment.semester, 'value', enrollment.semester)}.",
        )
        self._load_enrollment_panel()

    def _add_subject(self, enrollment: Enrollment):
        dialog = AddSubjectDialog(enrollment, self.student_service, parent=self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            es = dialog.get_enrollment_subject()
        finally:
            dialog.deleteLater()

        self._set_busy(True, "Adding the subject…")
        try:
            try:
                es_id = self.student_service.add_enrollment_subject(es)
            except DatabaseUnavailableError as exc:
                logger.warning("Add subject failed: %s", exc)
                show_db_error(self, exc, retry_callback=lambda: self._add_subject(enrollment))
                return
            except Exception as exc:  # noqa: BLE001 - reported to the operator
                logger.exception("Add subject failed")
                QMessageBox.critical(
                    self, "Could not add the subject",
                    f"The subject was not added to the enrollment.\n\n{exc}",
                )
                return
        finally:
            self._set_busy(False)

        if not es_id:
            QMessageBox.warning(
                self, "Not added",
                "The database reported that the subject was not added.",
            )
            return

        QMessageBox.information(self, "Subject added", "The subject is on the enrollment.")
        self._load_enrollment_panel()


class EditEnrollmentDialog(QDialog):
    """Edit the term, date and status of an existing enrollment.

    Reached from the enrollment tab's Edit Enrollment button, which previously
    existed on every tab and was connected to nothing.
    """

    def __init__(self, enrollment: Enrollment, parent=None):
        super().__init__(parent)
        self.enrollment = enrollment
        self.setWindowTitle("Edit Enrollment")
        self.setModal(True)
        self.setMinimumSize(420, 320)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        form = QFormLayout()
        form.setSpacing(12)

        self.academic_year = QLineEdit(self.enrollment.academic_year)
        self.academic_year.setPlaceholderText("e.g., 2024-2025")
        form.addRow("Academic Year:", self.academic_year)

        self.semester = QComboBox()
        self.semester.addItems([item.value for item in Semester])
        current = getattr(self.enrollment.semester, "value", self.enrollment.semester)
        index = self.semester.findText(str(current))
        self.semester.setCurrentIndex(index if index >= 0 else 0)
        form.addRow("Semester:", self.semester)

        self.enrollment_date = QDateEdit()
        self.enrollment_date.setCalendarPopup(True)
        self.enrollment_date.setDisplayFormat("yyyy-MM-dd")
        self.enrollment_date.setDate(
            QDate(self.enrollment.enrollment_date)
            if self.enrollment.enrollment_date
            else QDate.currentDate()
        )
        form.addRow("Enrollment Date:", self.enrollment_date)

        self.status = QComboBox()
        self.status.addItems([item.value for item in EnrollmentStatus])
        current_status = getattr(self.enrollment.status, "value", self.enrollment.status)
        status_index = self.status.findText(str(current_status))
        self.status.setCurrentIndex(status_index if status_index >= 0 else 0)
        form.addRow("Status:", self.status)

        layout.addLayout(form)

        note = QLabel(
            "Subjects already on this enrollment are not changed. Removing a "
            "subject is a separate action on the subject row."
        )
        note.setWordWrap(True)
        note.setFont(small_label_font(11))
        note.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        layout.addWidget(note)
        layout.addStretch()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Save Changes")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        year = self.academic_year.text().strip()
        if not year or "-" not in year:
            QMessageBox.warning(
                self, "Academic year",
                "Enter the academic year as YYYY-YYYY, for example 2024-2025.",
            )
            self.academic_year.setFocus()
            return
        self.accept()

    def get_enrollment(self) -> Enrollment:
        """A copy of the record with the edited fields replaced."""
        edited = replace(self.enrollment)
        edited.academic_year = self.academic_year.text().strip()
        edited.semester = Semester(self.semester.currentText())
        edited.enrollment_date = self.enrollment_date.date().toPython()
        edited.status = EnrollmentStatus(self.status.currentText())
        return edited


class NewEnrollmentDialog(QDialog):
    def __init__(self, student: Student, student_service: StudentService, parent=None):
        super().__init__(parent)
        self.student = student
        self.student_service = student_service
        self.setWindowTitle("New Enrollment")
        self.setModal(True)
        self.setMinimumSize(400, 350)
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        
        form = QFormLayout()
        form.setSpacing(12)
        
        # Defaulted from the settings rather than hardcoded, and validated on
        # accept: a registrar who typed a term that does not exist should be
        # told, not stored.
        self.academic_year = QLineEdit()
        self.academic_year.setPlaceholderText("e.g., 2024-2025")
        self.academic_year.setText(settings.DEFAULT_ACADEMIC_YEAR)
        form.addRow("Academic Year:", self.academic_year)
        
        self.semester = QComboBox()
        self.semester.addItems([s.value for s in Semester])
        form.addRow("Semester:*", self.semester)
        
        self.enrollment_date = QDateEdit()
        self.enrollment_date.setCalendarPopup(True)
        self.enrollment_date.setDate(QDate.currentDate())
        self.enrollment_date.setDisplayFormat("yyyy-MM-dd")
        form.addRow("Enrollment Date:*", self.enrollment_date)
        
        self.status = QComboBox()
        self.status.addItems([s.value for s in EnrollmentStatus])
        form.addRow("Status:*", self.status)
        
        layout.addLayout(form)
        layout.addStretch()
        
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("Create Enrollment")
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _on_accept(self) -> None:
        year = self.academic_year.text().strip()
        if not year or "-" not in year:
            QMessageBox.warning(
                self, "Academic year",
                "Enter the academic year as YYYY-YYYY, for example 2024-2025.",
            )
            self.academic_year.setFocus()
            return
        self.accept()

    def get_enrollment(self) -> Enrollment:
        return Enrollment(
            student_id=self.student.id,
            academic_year=self.academic_year.text().strip(),
            semester=Semester(self.semester.currentText()),
            enrollment_date=self.enrollment_date.date().toPython(),
            status=EnrollmentStatus(self.status.currentText()),
            # 0 because the term has no subjects yet; the credit total is
            # recalculated by the service whenever a subject is added or
            # removed, so it is never left stale.
            total_credits=0,
        )


class AddSubjectDialog(QDialog):
    def __init__(self, enrollment: Enrollment, student_service: StudentService, parent=None):
        super().__init__(parent)
        self.enrollment = enrollment
        self.student_service = student_service
        self.setWindowTitle("Add Subject to Enrollment")
        self.setModal(True)
        self.setMinimumSize(500, 500)
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        
        form = QFormLayout()
        form.setSpacing(12)
        
        self.subject_combo = QComboBox()
        # Loaded once and kept: `get_enrollment_subject` used to query the
        # catalogue a second time and then scan it in Python to recover the
        # object the combo already held as currentData().
        try:
            self._subjects = self.student_service.get_all_subjects()
        except DatabaseUnavailableError as exc:
            logger.warning("Subject catalogue unavailable: %s", exc)
            self._subjects = []
            self.subject_combo.setEnabled(False)
            catalogue_error = QLabel(
                "The subject catalogue could not be read, so no subject can be added."
            )
            catalogue_error.setWordWrap(True)
            catalogue_error.setStyleSheet(f"color: {Colors.ERROR_RED};")
            form.addRow("", catalogue_error)
        except Exception as exc:  # noqa: BLE001 - reported in the dialog
            logger.exception("Subject catalogue unavailable")
            self._subjects = []
            self.subject_combo.setEnabled(False)
            catalogue_error = QLabel(f"The subject catalogue could not be read:\n{exc}")
            catalogue_error.setWordWrap(True)
            catalogue_error.setStyleSheet(f"color: {Colors.ERROR_RED};")
            form.addRow("", catalogue_error)

        for subj in self._subjects:
            self.subject_combo.addItem(f"{subj.subject_code} - {subj.subject_name}", subj.id)
        form.addRow("Subject:", self.subject_combo)
        
        self.section = QLineEdit()
        self.section.setPlaceholderText("e.g., EP1")
        form.addRow("Section:", self.section)
        
        self.schedule_time = QLineEdit()
        self.schedule_time.setPlaceholderText("e.g., 7-8 PM")
        form.addRow("Time:", self.schedule_time)
        
        self.schedule_days = QLineEdit()
        self.schedule_days.setPlaceholderText("e.g., Saturday")
        form.addRow("Days:", self.schedule_days)
        
        self.room = QLineEdit()
        self.room.setPlaceholderText("e.g., CB 24")
        form.addRow("Room:", self.room)
        
        self.period = QSpinBox()
        self.period.setRange(0, 20)
        form.addRow("Period:", self.period)
        
        # Two decimals, because the certificate and the assessment both carry
        # peso-and-centavo amounts; an integer spin box made ₱1,755.81
        # impossible to enter. The ceiling is raised well past any fee WIT
        # charges so the limit cannot silently truncate a real figure.
        self.lab_fee = QDoubleSpinBox()
        self.lab_fee.setRange(0, 10_000_000)
        self.lab_fee.setDecimals(2)
        self.lab_fee.setPrefix("₱ ")
        self.lab_fee.setSingleStep(50)
        form.addRow("Lab Fee:", self.lab_fee)

        self.other_fee = QDoubleSpinBox()
        self.other_fee.setRange(0, 10_000_000)
        self.other_fee.setDecimals(2)
        self.other_fee.setPrefix("₱ ")
        self.other_fee.setSingleStep(50)
        form.addRow("Other Fee:", self.other_fee)
        
        layout.addLayout(form)
        layout.addStretch()
        
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)

        # Standard dialog labels: "Save Changes" states what confirming does,
        # where a bare "OK" only confirms that *something* happens. The button is
        # renamed rather than the role, so `accepted` still fires exactly as
        # before.
        confirm = btn_box.button(QDialogButtonBox.StandardButton.Ok)
        if confirm is not None:
            confirm.setText("Save Changes")
        layout.addWidget(btn_box)
    
    def get_enrollment_subject(self) -> EnrollmentSubject:
        subject_id = self.subject_combo.currentData()
        subject = next((s for s in self._subjects if s.id == subject_id), None)
        if subject is None:
            raise ValueError("No subject is selected.")
        
        return EnrollmentSubject(
            enrollment_id=self.enrollment.id,
            subject_id=subject_id,
            section=self.section.text().strip() or None,
            schedule_time=self.schedule_time.text().strip() or None,
            schedule_days=self.schedule_days.text().strip() or None,
            room=self.room.text().strip() or None,
            period=self.period.value() if self.period.value() > 0 else None,
            # 0 stays 0: "no fee for this subject" and "the fee was waived"
            # are different facts, and coercing both to None erases the first.
            lab_fee=self.lab_fee.value(),
            other_fee=self.other_fee.value(),
            status=SubjectStatus.ENROLLED,
            subject=subject,
        )
from pathlib import Path
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QFrame, QComboBox, QMessageBox, QDialog, QFileDialog, QStackedWidget,
    QApplication
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics
from styles.typography import get_open_sauce_family, get_agrandir_family, body_font, body_bold_font, section_heading_font, page_heading_font, small_label_font, button_font
from styles.colors import Colors
from services.student_service import StudentService
from services.report_service import ReportService
from ui.widgets.responsive_table import ColumnSpec, configure_table, count_column, money_column, set_cell
from ui.widgets.empty_state import EmptyState, LoadingState, error_state
from ui.dialogs.student_dialog import StudentDialog
from ui.dialogs.confirm_dialog import ConfirmDialog
from ui.db_error import show_db_error
from database.connection import DatabaseUnavailableError, reset_connection_pool
from models.student import EnrollmentStatus
from config.settings import settings
import logging

logger = logging.getLogger(__name__)

#: Padding added to each row-action button's measured label width, covering the
#: stylesheet's horizontal padding (``padding: 8px 16px``) and the cell margins.
ACTION_BUTTON_PADDING = 34


def actions_column_width() -> int:
    """Width the row-actions column needs for its three labels.

    Measured from the button font rather than hardcoded: a fixed 160px squeezed
    "View Full Profile" into "View F..." and clipped "Delete" in half, and any
    other fixed number breaks again the moment the font changes (a different
    fallback family, 125% display scaling).
    """
    metrics = QFontMetrics(button_font(12))
    labels = ("View Full Profile", "Edit", "Delete")
    return sum(
        metrics.horizontalAdvance(label) + ACTION_BUTTON_PADDING for label in labels
    ) + 2 * 6  # gaps between the buttons


def _status_colour(status: str) -> str:
    """Colour for a student's enrollment status, by the stored value."""
    return {
        "active": Colors.SUCCESS_GREEN,
        "on_leave": Colors.WARNING_ORANGE,
        "dropped": Colors.ERROR_RED,
        "graduated": Colors.MEDIUM_TEXT,
    }.get(str(status), Colors.MEDIUM_TEXT)


class StudentsPage(QWidget):
    def __init__(self, student_service: StudentService, parent=None):
        super().__init__(parent)
        self.student_service = student_service
        self.report_service = ReportService()
        self.current_page = 0
        self.page_size = 25
        self.current_search = ""
        self.current_status = ""

        self.setObjectName("studentsPage")
        self._setup_ui()
        self.refresh()
    
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
        
        title = QLabel("Students")
        title.setFont(page_heading_font(24))
        title.setStyleSheet("color: white;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        
        add_btn = QPushButton("+ Add Student")
        add_btn.setStyleSheet("QPushButton { color: white; background: rgba(255,255,255,0.1); border: 1px solid white; } QPushButton:hover { background: rgba(255,255,255,0.2); }")
        add_btn.clicked.connect(self._add_student)
        header_layout.addWidget(add_btn)
        
        main_layout.addWidget(header)
        
        # Toolbar
        toolbar = QFrame()
        toolbar.setStyleSheet(f"background-color: {Colors.WHITE}; border-bottom: 1px solid {Colors.CARD_BORDER};")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(16, 12, 16, 12)
        toolbar_layout.setSpacing(12)
        
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search students...")
        self.search_edit.setFixedWidth(300)
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_search_changed)
        
        self.status_filter = QComboBox()
        self.status_filter.addItem("All Statuses", "")
        for status in EnrollmentStatus:
            self.status_filter.addItem(status.value.title(), status.value)
        self.status_filter.currentIndexChanged.connect(self._on_status_changed)
        
        self.export_btn = QPushButton("Export Report")
        self.export_btn.setProperty("secondary", "true")
        self.export_btn.setToolTip("Write the students currently listed to a CSV file")
        self.export_btn.clicked.connect(self._export_students)
        
        toolbar_layout.addWidget(QLabel("Search:"))
        toolbar_layout.addWidget(self.search_edit)
        toolbar_layout.addSpacing(16)
        toolbar_layout.addWidget(QLabel("Status:"))
        toolbar_layout.addWidget(self.status_filter)
        toolbar_layout.addStretch()
        toolbar_layout.addWidget(self.export_btn)
        
        main_layout.addWidget(toolbar)
        
        # Table
        # The table sits in a stack over loading/empty/error states so "no
        # matches" and "the database is unreachable" cannot look the same.
        self.list_stack = QStackedWidget()
        main_layout.addWidget(self.list_stack, 1)

        self.table = QTableWidget()
        # Name and Program size to their content and take the remaining space;
        # identifiers get a floor derived from their header. Nothing here is
        # "Stretch", which is what clipped "Student ID" into "tudent II" and
        # "Ronna Mae D. Alemania" into "Ronn...".
        configure_table(
            self.table,
            [
                # Name sizes to its content -- "Steven Anthony M. Oquiana" is
                # what identifies the row, so it is never the column that gives
                # up space. Program absorbs what is left over.
                ColumnSpec("Student ID", width=110),
                ColumnSpec("Reference No.", width=110),
                ColumnSpec("Name", width=170),
                ColumnSpec("Program", width=150, stretch=True),
                ColumnSpec("Year Level", width=100),
                ColumnSpec("Section", width=84),
                ColumnSpec("Status", width=100),
                # The action column is sized from the buttons it holds (see
                # ACTIONS_COLUMN), not from a guess: at 160px the three buttons
                # were squeezed until "View Full Profile" was unreadable and
                # "Delete" was clipped in half.
                ColumnSpec("Actions", width=actions_column_width()),
            ],
        )
        self.list_stack.addWidget(self.table)

        self.loading_state = LoadingState("Loading students…")
        self.list_stack.addWidget(self.loading_state)

        self.empty_state = EmptyState(
            "No students to show",
            "Nothing here matches the current search and status filter.",
            icon="search",
            action_text="Clear filters",
            action=self._clear_filters,
        )
        self.list_stack.addWidget(self.empty_state)

        self.error_view = error_state(
            DatabaseUnavailableError("The student list could not be read."),
            retry=self.refresh,
        )
        self.list_stack.addWidget(self.error_view)
        
        # Pagination
        pagination = QFrame()
        pagination.setStyleSheet(f"background-color: {Colors.WHITE}; border-top: 1px solid {Colors.CARD_BORDER};")
        pag_layout = QHBoxLayout(pagination)
        pag_layout.setContentsMargins(16, 12, 16, 12)
        pag_layout.setSpacing(8)
        
        self.prev_btn = QPushButton("Previous")
        self.prev_btn.setProperty("secondary", "true")
        self.prev_btn.clicked.connect(self._prev_page)
        
        self.page_label = QLabel("Page 1")
        self.page_label.setFont(small_label_font(11))
        self.page_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_label.setFixedWidth(120)
        
        self.next_btn = QPushButton("Next")
        self.next_btn.setProperty("secondary", "true")
        self.next_btn.clicked.connect(self._next_page)
        
        self.count_label = QLabel("0 students")
        self.count_label.setFont(small_label_font(11))
        self.count_label.setStyleSheet(f"color: {Colors.LIGHT_TEXT};")
        
        pag_layout.addStretch()
        pag_layout.addWidget(self.prev_btn)
        pag_layout.addWidget(self.page_label)
        pag_layout.addWidget(self.next_btn)
        pag_layout.addSpacing(24)
        pag_layout.addWidget(self.count_label)
        
        main_layout.addWidget(pagination)
    
    def apply_search(self, query: str) -> None:
        """Filter this page to ``query``.

        Called by the window's quick search. The text is set with signals blocked
        and the refresh invoked directly, so one action produces one query rather
        than the query from ``textChanged`` plus another from here.
        """
        text = query.strip()
        self.search_edit.blockSignals(True)
        self.search_edit.setText(text)
        self.search_edit.blockSignals(False)
        self.current_search = text
        self.current_page = 0
        self.refresh()

    def _on_search_changed(self, text: str):
        self.current_search = text.strip()
        self.current_page = 0
        QTimer.singleShot(300, self.refresh)  # Debounce
    
    def _on_status_changed(self, index: int):
        self.current_status = self.status_filter.itemData(index) or ""
        self.current_page = 0
        self.refresh()
    
    def _clear_filters(self) -> None:
        """Reset search and status, then re-read the list."""
        self.search_edit.blockSignals(True)
        self.search_edit.clear()
        self.search_edit.blockSignals(False)
        self.status_filter.blockSignals(True)
        self.status_filter.setCurrentIndex(0)
        self.status_filter.blockSignals(False)
        self.current_search = ""
        self.current_status = ""
        self.current_page = 0
        self.refresh()

    def refresh(self):
        """Re-read the filtered page.

        A database failure is shown as a failure with a retry and never as an
        empty table: "no students match" and "the registrar's records could not
        be read" are different facts, and an empty table states the first one
        whatever actually happened.
        """
        if not self.table.isEnabled():
            return

        self.list_stack.setCurrentWidget(self.loading_state)
        self.table.setEnabled(False)
        try:
            total = self.student_service.count_students(self.current_search, self.current_status)
            students = self.student_service.search_students(
                query=self.current_search,
                status=self.current_status,
                limit=self.page_size,
                offset=self.current_page * self.page_size,
            )
        except DatabaseUnavailableError as exc:
            logger.warning("Students page could not read the student list: %s", exc)
            self.count_label.setText("Unavailable")
            self.list_stack.setCurrentWidget(self.error_view)
            self._set_pagination(0)
            return
        finally:
            self.table.setEnabled(True)

        self.count_label.setText(f"{total} student{'s' if total != 1 else ''}")
        self._fill_rows(students)
        self._set_pagination(total)

        if students:
            self.list_stack.setCurrentWidget(self.table)
        else:
            self.list_stack.setCurrentWidget(self.empty_state)

    def _set_pagination(self, total: int) -> None:
        total_pages = max(1, (total + self.page_size - 1) // self.page_size)
        # A filter that no longer matches this page would otherwise leave the
        # operator on an empty page 3 with no way back except the filter box.
        if self.current_page >= total_pages:
            self.current_page = max(0, total_pages - 1)
        self.page_label.setText(f"Page {self.current_page + 1} / {total_pages}")
        self.prev_btn.setEnabled(self.current_page > 0)
        self.next_btn.setEnabled(self.current_page < total_pages - 1)

    def _fill_rows(self, students) -> None:
        table = self.table
        metrics = table.fontMetrics()
        table.setRowCount(0)
        for student in students:
            row = table.rowCount()
            table.insertRow(row)

            set_cell(table, row, 0, student.student_id)
            set_cell(table, row, 1, student.reference_number)
            set_cell(table, row, 2, student.full_name)
            set_cell(table, row, 3, student.program)
            set_cell(table, row, 4, getattr(student.year_level, "value", student.year_level))
            set_cell(table, row, 5, student.section or "Not recorded")

            status = getattr(student.enrollment_status, "value", student.enrollment_status)
            status_item = set_cell(
                table, row, 6, str(status).replace("_", " ").title(),
                align=Qt.AlignmentFlag.AlignCenter,
            )
            status_item.setForeground(Colors.qcolor(_status_colour(status)))

            holder = self._row_actions(student)
            table.setCellWidget(row, 7, holder)

        # Rows must be tall enough for the buttons they contain. Without this the
        # row keeps the text-only height from `configure_table` and the buttons
        # are cut off through the middle of their labels.
        needed = metrics.height() + 18
        for row in range(table.rowCount()):
            holder = table.cellWidget(row, 7)
            if holder is not None:
                needed = max(needed, holder.sizeHint().height() + 6)
        table.verticalHeader().setDefaultSectionSize(needed)

    def _row_actions(self, student) -> QWidget:
        """The per-row View / Edit / Delete buttons, sized to their labels."""
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)

        view_btn = QPushButton("View Full Profile")
        view_btn.setProperty("secondary", "true")
        view_btn.setFont(button_font(12))
        view_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        view_btn.setToolTip(f"Open {student.full_name}'s record (read and edit)")
        view_btn.clicked.connect(lambda checked, sid=student.id: self._view_student(sid))

        edit_btn = QPushButton("Edit")
        edit_btn.setProperty("secondary", "true")
        edit_btn.setFont(button_font(12))
        edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        edit_btn.clicked.connect(lambda checked, sid=student.id: self._edit_student(sid))

        delete_btn = QPushButton("Delete")
        delete_btn.setProperty("danger", "true")
        delete_btn.setFont(button_font(12))
        delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        delete_btn.setToolTip(f"Delete {student.full_name} and every record attached to them")
        delete_btn.clicked.connect(lambda checked, sid=student.id: self._delete_student(sid))

        for button in (view_btn, edit_btn, delete_btn):
            # Width from the label's own metrics, matching actions_column_width()
            # so the column and its buttons always agree; height from the
            # stylesheet's own padding, so the label is never clipped top or
            # bottom inside the cell.
            button.setFixedWidth(
                button.fontMetrics().horizontalAdvance(button.text())
                + ACTION_BUTTON_PADDING
            )
            button.setFixedHeight(button.sizeHint().height())

        layout.addWidget(view_btn)
        layout.addWidget(edit_btn)
        layout.addWidget(delete_btn)
        layout.addStretch()
        return holder

    def preload_student(self, student) -> bool:
        """Bring a student handed over by another page (e.g. after a scan) into view.

        The record is re-read from the database by reference number, so the page
        shows authoritative data rather than whatever object the caller passed.
        The student is selected but *not* opened for editing: this page offers no
        role check of its own, and opening an editor implicitly is not the same
        as being asked to.
        """
        if student is None:
            return False
        reference = getattr(student, "reference_number", None)
        if not reference:
            logger.warning("Navigation context carried no reference number")
            return False

        try:
            fresh = self.student_service.get_student_by_reference(reference)
        except DatabaseUnavailableError as exc:
            logger.warning("Could not re-verify student %s: %s", reference, exc)
            self.count_label.setText("Could not verify the selected student")
            self.list_stack.setCurrentWidget(self.error_view)
            show_db_error(self, exc, retry_callback=lambda: self.preload_student(student))
            return False
        except Exception as exc:  # noqa: BLE001 - reported to the operator
            logger.exception("Could not re-verify student %s", reference)
            self.count_label.setText("Could not verify the selected student")
            QMessageBox.warning(
                self, "Could not open that student",
                f"Reference {reference} could not be verified:\n\n{exc}",
            )
            return False

        if fresh is None:
            logger.warning("Reference %s did not resolve on this page", reference)
            self.count_label.setText(f"Reference {reference} was not found")
            return False

        self.current_search = ""
        self.current_status = ""
        self.search_edit.blockSignals(True)
        self.search_edit.clear()
        self.search_edit.blockSignals(False)
        self.status_filter.blockSignals(True)
        self.status_filter.setCurrentIndex(0)
        self.status_filter.blockSignals(False)
        self.current_page = 0
        self.refresh()

        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)  # Reference No. column
            if item is not None and item.text() == reference:
                self.table.blockSignals(True)
                try:
                    self.table.selectRow(row)
                    self.table.scrollToItem(item)
                finally:
                    self.table.blockSignals(False)
                return True
        return False

    def _prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self.refresh()
    
    def _next_page(self):
        self.current_page += 1
        self.refresh()
    
    def _add_student(self):
        dialog = StudentDialog(parent=self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            student = dialog.get_student()
            self.student_service.create_student(student)
        except DatabaseUnavailableError as exc:
            logger.warning("Add student failed: %s", exc)
            show_db_error(self, exc, retry_callback=self._add_student)
            return
        except Exception as exc:  # noqa: BLE001 - reported to the operator
            logger.exception("Add student failed")
            QMessageBox.critical(
                self, "Could not add the student",
                f"The record was not saved.\n\n{exc}",
            )
            return
        finally:
            dialog.deleteLater()

        QMessageBox.information(
            self, "Student added", f"{student.full_name} was added to the student list."
        )
        self.refresh()

    def _view_student(self, student_id: int):
        try:
            student = self.student_service.get_student(student_id)
        except DatabaseUnavailableError as exc:
            logger.warning("Could not open student %s: %s", student_id, exc)
            show_db_error(self, exc, retry_callback=lambda: self._view_student(student_id))
            return

        if student is None:
            # The row was deleted by someone else, or the id came from a stale
            # table. Say which, instead of closing silently.
            QMessageBox.warning(
                self, "Student not found",
                "That student is no longer on file. The list has been refreshed.",
            )
            self.refresh()
            return

        dialog = StudentDialog(student=student, parent=self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            updated = dialog.get_student()
            self.student_service.update_student(updated)
        except DatabaseUnavailableError as exc:
            logger.warning("Update student failed: %s", exc)
            show_db_error(self, exc, retry_callback=lambda: self._view_student(student_id))
            return
        except Exception as exc:  # noqa: BLE001 - reported to the operator
            logger.exception("Update student failed")
            QMessageBox.critical(
                self, "Could not save the changes",
                f"{student.full_name}'s record was left unchanged.\n\n{exc}",
            )
            return
        finally:
            dialog.deleteLater()

        QMessageBox.information(
            self, "Student updated", f"{updated.full_name}'s record was saved."
        )
        self.refresh()

    def _edit_student(self, student_id: int):
        self._view_student(student_id)

    def _delete_student(self, student_id: int):
        try:
            student = self.student_service.get_student(student_id)
        except DatabaseUnavailableError as exc:
            show_db_error(self, exc, retry_callback=lambda: self._delete_student(student_id))
            return

        if student is None:
            QMessageBox.warning(
                self, "Student not found",
                "That student is no longer on file, so there is nothing to delete.",
            )
            self.refresh()
            return

        dialog = ConfirmDialog(
            "Delete Student",
            f"Delete {student.full_name} ({student.student_id})?\n\n"
            "Their enrollments, assessments and scan history are removed with them. "
            "This cannot be undone.",
            "Delete", "Cancel",
            danger=True,
            parent=self,
        )
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
        finally:
            dialog.deleteLater()

        try:
            removed = self.student_service.delete_student(student_id)
        except DatabaseUnavailableError as exc:
            logger.warning("Delete student failed: %s", exc)
            show_db_error(self, exc, retry_callback=lambda: self._delete_student(student_id))
            return
        except Exception as exc:  # noqa: BLE001 - reported to the operator
            logger.exception("Delete student failed")
            QMessageBox.critical(
                self, "Could not delete the student",
                f"{student.full_name} is still on file.\n\n{exc}",
            )
            return

        if not removed:
            QMessageBox.warning(
                self, "Not deleted",
                "The database reported that nothing was removed. The list has been "
                "refreshed so it shows what is actually on file.",
            )
        self.refresh()

    def _export_students(self):
        """Write the rows currently on screen to a CSV file.

        The export follows the filters: an operator who has searched "Caporal"
        or filtered to one status gets that list, which is the only reason to
        press the button after filtering.
        """
        self.export_btn.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            try:
                students = self.student_service.search_students(
                    query=self.current_search,
                    status=self.current_status,
                    limit=max(self.page_size, 1000),
                )
            except DatabaseUnavailableError as exc:
                logger.warning("Export failed: %s", exc)
                show_db_error(self, exc, retry_callback=self._export_students)
                return
            except Exception as exc:  # noqa: BLE001 - reported to the operator
                logger.exception("Export failed")
                QMessageBox.critical(self, "Export failed", f"The list could not be read.\n\n{exc}")
                return

            if not students:
                QMessageBox.information(
                    self, "Nothing to export",
                    "No students match the current search and status filter.",
                )
                return

            suggested = settings.REPORTS_OUTPUT_DIR / (
                f"students_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            )
            path, _ = QFileDialog.getSaveFileName(
                self, "Export Students", str(suggested), "CSV Files (*.csv)"
            )
            if not path:
                return

            written = self.report_service.export_students_csv(students, Path(path))
            if written is None:
                QMessageBox.critical(
                    self, "Export failed",
                    f"The file could not be written to:\n{path}\n\n"
                    "Check that the folder exists and that you have permission to write to it.",
                )
                return
        finally:
            QApplication.restoreOverrideCursor()
            self.export_btn.setEnabled(True)

        QMessageBox.information(
            self, "Export complete",
            f"{len(students)} student row(s) written to:\n{written}",
        )

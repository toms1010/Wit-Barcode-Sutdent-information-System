from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QFrame, QComboBox, QMessageBox, QDialog, QFormLayout, QDateEdit,
    QDoubleSpinBox, QTabWidget, QGroupBox, QScrollArea, QSpinBox, QDialogButtonBox
)
from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QScrollArea, QSizePolicy, QApplication
from decimal import Decimal
from styles.typography import get_open_sauce_family, get_agrandir_family, body_font, body_bold_font, section_heading_font, page_heading_font, small_label_font, button_font
from styles.colors import Colors
from database.connection import DatabaseUnavailableError
from services.student_service import StudentService
from services.com_summary import (
    InstallmentRow, format_peso, installment_state,
)

from ui.formatting import NOT_RECORDED, format_date
from ui.widgets.panel import card_frame
from ui.widgets.responsive_table import set_cell
from config.settings import settings
from ui.db_error import show_db_error
from ui.widgets.empty_state import EmptyState
from ui.widgets.profile_image import apply_portrait
from ui.widgets.responsive_table import ColumnSpec, configure_table, count_column, money_column
from services.assessment_service import AssessmentService
from models.assessment import Assessment, AssessmentItem, AssessmentCategory, Payment, PaymentSchedule, AssessmentStatus, PaymentMethod, PaymentScheduleType, PaymentScheduleStatus
from models.student import Student
from models.enrollment import Semester
from dataclasses import replace
import logging

logger = logging.getLogger(__name__)


class AssessmentPage(QWidget):
    def __init__(self, student_service: StudentService, assessment_service: AssessmentService, parent=None):
        super().__init__(parent)
        self.student_service = student_service
        self.assessment_service = assessment_service
        self.current_student: Student = None
        self.current_assessment: Assessment = None
        
        self.setObjectName("assessmentPage")
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
        
        title = QLabel("Assessment & Fees")
        title.setFont(page_heading_font(24))
        title.setStyleSheet("color: white;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        
        main_layout.addWidget(header)
        
        # Content
        #
        # Stacked, not side by side. The picker needs a fraction of the width and
        # the assessment tables need all of it; in a left column they had about
        # 700px for six money columns and scrolled with a column cut in half.
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 24, 24, 24)
        content_layout.setSpacing(16)
        
        # Left panel - Student selection
        left_panel = card_frame("assessmentLeftPanel")
        left_panel.setFixedHeight(232)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(16, 16, 16, 16)
        left_layout.setSpacing(16)
        
        search_layout = QVBoxLayout()
        search_layout.setSpacing(8)

        picker_heading = QLabel("Select Student")
        picker_heading.setFont(body_bold_font(13))
        search_layout.addWidget(picker_heading)
        
        self.student_search = QLineEdit()
        self.student_search.setPlaceholderText("Search by name, ID, or reference...")
        self.student_search.setClearButtonEnabled(True)
        self.student_search.textChanged.connect(self._filter_students)
        search_layout.addWidget(self.student_search)
        
        self.student_list = QTableWidget()
        configure_table(
            self.student_list,
            [
                ColumnSpec("Student ID", width=125),
                ColumnSpec("Reference", width=100),
                ColumnSpec("Name", width=170, stretch=True),
                ColumnSpec("Program", width=150, stretch=True),
            ],
        )
        self.student_list.setAlternatingRowColors(True)
        self.student_list.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.student_list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.student_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.student_list.verticalHeader().setVisible(False)
        self.student_list.itemSelectionChanged.connect(self._on_student_selected)
        
        self.student_list.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        search_layout.addWidget(self.student_list)
        left_layout.addLayout(search_layout)
        
        content_layout.addWidget(left_panel, 0)
        
        # Right panel - Assessment details
        right_panel = card_frame("assessmentRightPanel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(20, 20, 20, 20)
        right_layout.setSpacing(20)
        
        # No selection state
        self.empty_state = EmptyState(
            "No student selected",
            "Choose a student to see the fees assessed, the discounts applied and "
            "what has been paid.",
            icon="assessment",
            parent=self,
        )
        right_layout.addWidget(self.empty_state, 1)
        
        # Assessment content
        self.assessment_widget = QWidget()
        self.assessment_widget.setVisible(False)
        assess_layout = QVBoxLayout(self.assessment_widget)
        assess_layout.setContentsMargins(0, 0, 0, 0)
        assess_layout.setSpacing(16)
        
        # Student info header
        self.student_info_header = QFrame()
        # Scoped by object name, and compact: a 150px header pushed the fee
        # tables below the fold on a 900px-tall window.
        self.student_info_header.setObjectName("assessmentStudentHeader")
        self.student_info_header.setStyleSheet(f"""
            QFrame#assessmentStudentHeader {{
                background-color: {Colors.CREAM};
                border-radius: 8px;
            }}
        """)
        info_layout = QHBoxLayout(self.student_info_header)
        info_layout.setContentsMargins(16, 10, 16, 10)
        info_layout.setSpacing(12)
        
        # The photograph comes from students.profile_image_path through the
        # shared loader; it was an orange disc with the student's initial, which
        # looked like a photograph and was not one.
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
        
        assess_layout.addWidget(self.student_info_header)
        
        # Summary cards
        self.summary_layout = QHBoxLayout()
        self.summary_layout.setSpacing(12)
        self.summary_cards = {}
        assess_layout.addLayout(self.summary_layout)
        
        # Tabs
        self.assessment_tabs = QTabWidget()
        self.assessment_tabs.setMinimumHeight(260)
        self.assessment_tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border: 1px solid {Colors.CARD_BORDER}; border-radius: 8px; background: white; }}
            QTabBar::tab {{ background: {Colors.CREAM}; padding: 10px 20px; border: 1px solid {Colors.CARD_BORDER}; border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px; }}
            QTabBar::tab:selected {{ background: white; color: {Colors.ORANGE}; border-color: {Colors.CARD_BORDER}; border-bottom: 1px solid white; }}
        """)
        assess_layout.addWidget(self.assessment_tabs)
        
        # New assessment button
        new_assess_btn = QPushButton("+ New Assessment")
        new_assess_btn.clicked.connect(self._create_new_assessment)
        assess_layout.addWidget(new_assess_btn)
        
        # The details side scrolls vertically: fee tables, a student header and
        # an action row do not fit a short window, and without this the tab pane
        # was squeezed until the tables had no height.
        details_holder = QWidget()
        details_layout = QVBoxLayout(details_holder)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(16)
        details_layout.addWidget(self.assessment_widget, 1)

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
            self.student_list.setItem(row, 0, QTableWidgetItem(student.student_id))
            self.student_list.setItem(row, 1, QTableWidgetItem(student.reference_number))
            self.student_list.setItem(row, 2, QTableWidgetItem(student.full_name))
            self.student_list.setItem(row, 3, QTableWidgetItem(student.program))
            for col in range(4):
                self.student_list.item(row, col).setData(Qt.ItemDataRole.UserRole, student.id)
    
    def _filter_students(self, text: str):
        text = text.lower().strip()
        for row in range(self.student_list.rowCount()):
            match = False
            for col in range(4):
                item = self.student_list.item(row, col)
                if item and text in item.text().lower():
                    match = True
                    break
            self.student_list.setRowHidden(row, not match)
    
    def _on_student_selected(self):
        selected = self.student_list.selectedItems()
        if not selected:
            self._clear_selection()
            return

        student_id = selected[0].data(Qt.ItemDataRole.UserRole)
        try:
            student = self.student_service.get_student(student_id)
        except DatabaseUnavailableError as exc:
            logger.warning("Could not read student %s: %s", student_id, exc)
            self.student_list.clearSelection()
            self._clear_selection()
            show_db_error(self, exc, retry_callback=self._load_assessment_panel)
            return
        except Exception as exc:  # noqa: BLE001 - reported to the operator
            logger.exception("Could not read student %s", student_id)
            QMessageBox.critical(self, "Could not open that student", str(exc))
            self.student_list.clearSelection()
            self._clear_selection()
            return

        if student is None:
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
        self._load_assessment_panel()

    def _clear_selection(self) -> None:
        """Return the page to its no-selection state.

        Every student-specific value is reset together. The previous student's
        name, tabs and peso totals staying on screen under a new selection is
        how one student's financial record ends up attributed to another.
        """
        self.current_student = None
        self.current_assessment = None
        self.assessment_tabs.clear()
        self._clear_summary_cards()
        self.student_name_label.clear()
        self.student_meta_label.clear()
        self.student_avatar.clear()
        self.student_avatar.setText("")
        self.assessment_widget.setVisible(False)
        self.empty_state.setVisible(True)

    def _load_assessment_panel(self) -> None:
        """Read this student's assessments, or explain the gap."""
        if self.current_student is None:
            return
        try:
            self._load_assessments()
        except DatabaseUnavailableError as exc:
            logger.warning("Assessments for student %s failed: %s", self.current_student.id, exc)
            self.assessment_tabs.clear()
            self._clear_summary_cards()
            self._show_panel_message(
                "Assessment unavailable",
                f"{self.current_student.full_name}'s assessment could not be read, so "
                f"nothing below is current.",
            )
            show_db_error(self, exc, retry_callback=self._load_assessment_panel)
            return
        except Exception as exc:  # noqa: BLE001 - reported to the operator
            logger.exception("Assessments for student %s failed", self.current_student.id)
            self.assessment_tabs.clear()
            self._clear_summary_cards()
            self._show_panel_message(
                "Assessment unavailable", f"The assessment records could not be read:\n{exc}"
            )
            return

        self.empty_state.setVisible(False)
        self.assessment_widget.setVisible(True)

    def _show_panel_message(self, heading: str, message: str) -> None:
        """Replace the tab area with one explanatory panel."""
        self.assessment_tabs.clear()
        panel = EmptyState(heading, message, icon="assessment", parent=self)
        self.assessment_tabs.addTab(panel, "Unavailable")

    def preload_student(self, student) -> bool:
        """Select a student handed over by another page (e.g. after a scan).

        The record is re-read from the database by reference number rather than
        trusting the object that was passed in: the scanner's copy may be stale,
        and this page is about to show financial information. A reference number
        that no longer resolves leaves the page in its empty state and says so,
        rather than displaying amounts for a record that does not exist.
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
            # The message used to be written into a hidden label, so after a
            # failed re-verification the previous student's assessment stayed on
            # screen with no visible explanation.
            self._clear_selection()
            self.empty_state.set_message(
                f"Reference {reference} could not be verified: the student records "
                f"are unavailable, so nothing on this page is current."
            )
            self.empty_state.setVisible(True)
            self.assessment_widget.setVisible(False)
            show_db_error(self, exc, retry_callback=lambda: self.preload_student(student))
            return False

        if fresh is None:
            logger.warning("Reference %s did not resolve on this page", reference)
            self.empty_state.setText(
                f"Reference {reference} could not be found in the student records."
            )
            self.empty_state.setVisible(True)
            self.assessment_widget.setVisible(False)
            return False

        self._select_student_in_list(fresh.id)
        if self.current_student is None or self.current_student.id != fresh.id:
            # The list may not contain the student (filtered or paginated out);
            # load the record directly so the page still shows the right person.
            self.current_student = fresh
            self._load_assessments()
            self._update_student_header()

        self.empty_state.setText("Select a student to view assessment")
        self.empty_state.setVisible(False)
        self.assessment_widget.setVisible(True)
        return True

    def _select_student_in_list(self, student_id) -> bool:
        """Highlight the row for ``student_id`` in the picker table."""
        for row in range(self.student_list.rowCount()):
            item = self.student_list.item(row, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == student_id:
                self.student_list.blockSignals(True)
                try:
                    self.student_list.selectRow(row)
                    self.student_list.scrollToItem(item)
                finally:
                    self.student_list.blockSignals(False)
                # Load through the normal selection path so every dependent
                # widget is populated exactly as if the row had been clicked.
                self._on_student_selected()
                return True
        return False
    
    def _update_student_header(self):
        if not self.current_student:
            return
        
        apply_portrait(self.student_avatar, self.current_student, 44)
        self.student_name_label.setText(self.current_student.full_name)
        self.student_meta_label.setText(
            f"{self.current_student.student_id} • {self.current_student.reference_number} • "
            f"{self.current_student.program} • {self.current_student.year_level.value if hasattr(self.current_student.year_level, 'value') else self.current_student.year_level}"
        )
    
    def _load_assessments(self):
        assessments = self.assessment_service.get_student_assessments(self.current_student.id)

        self.assessment_tabs.clear()
        # Reset first: this used to keep the previous student's assessment
        # object, which is what the summary cards were still showing.
        self.current_assessment = None

        if assessments:
            for assessment in assessments:
                tab = self._create_assessment_tab(assessment)
                tab_label = f"{assessment.academic_year} - {assessment.semester} ({assessment.status.value if hasattr(assessment.status, 'value') else assessment.status})"
                self.assessment_tabs.addTab(tab, tab_label)
            
            # Show latest assessment summary
            self.current_assessment = assessments[0]
            self._update_summary_cards(self.current_assessment)
        else:
            # The same EmptyState component the rest of the application uses, so
            # "no assessment" looks like a state rather than a stray sentence.
            empty_tab = EmptyState(
                "No assessment on file",
                f"{self.current_student.full_name} has no assessment recorded for any "
                "term. Use New Assessment to create one.",
                icon="assessment",
                action_text="New Assessment",
                action=self._create_new_assessment,
                parent=self,
            )
            self.assessment_tabs.addTab(empty_tab, "No Assessments")
            self._clear_summary_cards()
    
    def _update_summary_cards(self, assessment: Assessment):
        # Clear existing
        for card in self.summary_cards.values():
            card.deleteLater()
        self.summary_cards.clear()
        
        from ui.widgets.stat_card import StatCard
        
        # format_peso, not an inline f-string: this is the same helper the
        # certificate and the exported PDF use, so a figure cannot read one way
        # on screen and another way on paper.
        self.summary_cards["assessed"] = StatCard("Total Assessed", format_peso(assessment.total_assessed), "💰")
        self.summary_cards["discounts"] = StatCard("Discounts", format_peso(assessment.total_discounts), "🏷️")
        self.summary_cards["payments"] = StatCard("Payments", format_peso(assessment.total_payments), "💳")
        self.summary_cards["balance"] = StatCard(
            "Outstanding", format_peso(assessment.outstanding_balance), "⚖️"
        )
        
        self.summary_layout.addWidget(self.summary_cards["assessed"])
        self.summary_layout.addWidget(self.summary_cards["discounts"])
        self.summary_layout.addWidget(self.summary_cards["payments"])
        self.summary_layout.addWidget(self.summary_cards["balance"])
    
    def _clear_summary_cards(self):
        for card in self.summary_cards.values():
            card.deleteLater()
        self.summary_cards.clear()
    
    def _create_assessment_tab(self, assessment: Assessment) -> QWidget:
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

        # Assessment header
        header_layout = QHBoxLayout()
        status = getattr(assessment.status, "value", assessment.status)
        header_layout.addWidget(self._caption(f"Status: {status}"))
        header_layout.addWidget(self._caption(f"Dated: {format_date(assessment.assessment_date)}"))
        header_layout.addWidget(self._caption(f"Term: {assessment.academic_year} • {assessment.semester}"))
        header_layout.addStretch()

        # Wired to a real edit. It used to be created on every tab and connected
        # to nothing, so the button did nothing at all.
        edit_btn = QPushButton("Edit Assessment")
        edit_btn.setProperty("secondary", "true")
        edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        edit_btn.setMinimumWidth(
            edit_btn.fontMetrics().horizontalAdvance("Edit Assessment") + 28
        )
        edit_btn.setToolTip(
            f"Change the term, date or status of the {assessment.academic_year} assessment"
        )
        edit_btn.clicked.connect(lambda: self._edit_assessment(assessment))
        header_layout.addWidget(edit_btn)
        layout.addLayout(header_layout)

        # Items table
        table = QTableWidget()
        configure_table(
            table,
            [
                ColumnSpec("Category", width=180),
                ColumnSpec("Description", width=220, stretch=True),
                ColumnSpec("Type", width=110),
                ColumnSpec("Verified", width=110),
                money_column("Amount", 140),
            ],
        )

        fee_total = Decimal("0")
        discount_total = Decimal("0")
        for item in assessment.items:
            row = table.rowCount()
            table.insertRow(row)
            category = item.category
            cat_name = (
                getattr(category, "category_name", None)
                or getattr(category, "category_code", None)
                or NOT_RECORDED
            )
            if category is not None and getattr(category, "is_discount", False):
                cat_type = "Discount"
                discount_total += Decimal(str(item.amount or 0))
            elif category is not None and getattr(category, "is_fee", False):
                cat_type = "Fee"
                fee_total += Decimal(str(item.amount or 0))
            else:
                cat_type = "Other"
                fee_total += Decimal(str(item.amount or 0))

            set_cell(table, row, 0, cat_name)
            set_cell(table, row, 1, item.description or NOT_RECORDED)
            type_cell = set_cell(
                table, row, 2, cat_type, align=Qt.AlignmentFlag.AlignCenter
            )
            # A discount reduces what is owed; showing it like a fee in the same
            # column and the same colour made the two indistinguishable.
            if cat_type == "Discount":
                type_cell.setForeground(Colors.qcolor(Colors.SUCCESS_GREEN))
            verified_cell = set_cell(
                table, row, 3, "Verified" if item.is_verified else "Unverified",
                align=Qt.AlignmentFlag.AlignCenter,
            )
            verified_cell.setForeground(Colors.qcolor(
                Colors.SUCCESS_GREEN if item.is_verified else Colors.WARNING_ORANGE
            ))
            set_cell(
                table, row, 4, format_peso(item.amount),
                align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )

        if not assessment.items:
            layout.addWidget(EmptyState(
                "No assessment items",
                "This assessment has no charges recorded against it, so there is "
                "nothing to certify or bill yet.",
                icon="assessment",
                parent=self,
            ))

        # Totals row, from the items on screen so the printed sum matches them.
        total_row = table.rowCount()
        table.insertRow(total_row)
        totals = [
            "TOTALS",
            f"{len(assessment.items)} item(s)",
            f"Fees {format_peso(fee_total)}",
            f"Discounts −{format_peso(discount_total)}" if discount_total else "No discounts",
            format_peso(fee_total - discount_total),
        ]
        for column, text in enumerate(totals):
            cell = set_cell(
                table, total_row, column, text,
                align=(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    if column == 4 else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                ),
            )
            cell.setFont(QFont(get_open_sauce_family(), 12, QFont.Weight.Bold))
            cell.setBackground(Colors.qcolor(Colors.ACCENT_SUBTLE))

        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        table.setMinimumHeight(160)
        layout.addWidget(table, 1)

        # Payment schedule
        layout.addWidget(self._schedule_panel(assessment))

        # Payments
        layout.addWidget(self._payments_panel(assessment))

        layout.addStretch(1)

        # Actions stay pinned below the scroll area.
        actions = QHBoxLayout()
        actions.setContentsMargins(16, 0, 16, 12)
        record_pay_btn = QPushButton("Record Payment")
        record_pay_btn.clicked.connect(lambda: self._record_payment(assessment))
        actions.addWidget(record_pay_btn)
        actions.addStretch()
        outer.addLayout(actions)

        return tab

    @staticmethod
    def _caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setFont(small_label_font(11))
        label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        return label

    def _schedule_panel(self, assessment: Assessment) -> QWidget:
        """The installment schedule, or an explanation when there is none."""
        group = QGroupBox("Payment Schedule")
        group.setFont(section_heading_font(14))
        schedule_layout = QVBoxLayout(group)

        schedules = assessment.payment_schedules or []
        if not schedules:
            empty = EmptyState(
                "No payment schedule on file",
                f"This assessment has no installments recorded for "
                f"{self.current_student.full_name if self.current_student else 'this student'}.",
                icon="document",
                parent=self,
            )
            schedule_layout.addWidget(empty)
            return group

        schedule_table = QTableWidget()
        configure_table(
            schedule_table,
            [
                ColumnSpec("Installment", width=160),
                ColumnSpec("Due Date", width=140),
                money_column("Amount Due", 150),
                money_column("Amount Paid", 150),
                ColumnSpec("Status", width=130),
            ],
        )

        for row_index, entry in enumerate(schedules):
            row = schedule_table.rowCount()
            schedule_table.insertRow(row)
            kind = str(getattr(entry.schedule_type, "value", entry.schedule_type)).replace("_", " ").title()
            set_cell(schedule_table, row, 0, kind)
            set_cell(schedule_table, row, 1, format_date(entry.due_date))
            set_cell(schedule_table, row, 2, format_peso(entry.amount_due),
                     align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            paid = Decimal(str(entry.amount_paid or 0))
            set_cell(schedule_table, row, 3, format_peso(paid),
                     align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            # The same state helper the certificate uses, so an installment is
            # called the same thing in both places. Tones come from the same
            # table rather than from string comparisons re-implemented here.
            state_label, tone = installment_state(
                InstallmentRow(
                    label=kind,
                    amount=Decimal(str(entry.amount_due or 0)),
                    amount_paid=paid,
                    due_date=entry.due_date,
                    status=getattr(entry.status, "value", entry.status),
                )
            )
            status_cell = set_cell(
                schedule_table, row, 4, state_label,
                align=Qt.AlignmentFlag.AlignCenter,
            )
            status_cell.setForeground(Colors.qcolor(Colors.TONES.get(tone, Colors.MEDIUM_TEXT)))

        schedule_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        schedule_table.setMinimumHeight(120)
        schedule_layout.addWidget(schedule_table)
        return group

    def _payments_panel(self, assessment: Assessment) -> QWidget:
        """Payments recorded against the assessment.

        The panel is always built -- with an empty state when nothing has been
        paid -- because the Record Payment action used to live inside the
        ``if payments:`` branch, so an assessment with no payments yet had no way
        to record the first one.
        """
        group = QGroupBox("Payment History")
        group.setFont(section_heading_font(14))
        pay_layout = QVBoxLayout(group)

        try:
            payments = self.assessment_service.get_payments(assessment.id)
        except DatabaseUnavailableError as exc:
            logger.warning("Payments for assessment %s failed: %s", assessment.id, exc)
            failed = EmptyState(
                "Payments unavailable",
                f"The payments recorded against this assessment could not be read, so "
                f"the figures below are not current.",
                icon="error",
                parent=self,
            )
            pay_layout.addWidget(failed)
            return group
        except Exception as exc:  # noqa: BLE001 - reported on screen
            logger.exception("Payments for assessment %s failed", assessment.id)
            failed = EmptyState(
                "Payments unavailable", f"The payment history could not be read:\n{exc}",
                icon="error",
                parent=self,
            )
            pay_layout.addWidget(failed)
            return group

        if not payments:
            empty = EmptyState(
                "No payments recorded",
                "Nothing has been paid against this assessment yet. Use Record Payment "
                "to enter the first one.",
                icon="document",
                parent=self,
            )
            pay_layout.addWidget(empty)
            return group

        pay_table = QTableWidget()
        configure_table(
            pay_table,
            [
                ColumnSpec("Date", width=140),
                money_column("Amount", 150),
                ColumnSpec("Method", width=140),
                ColumnSpec("Reference", width=180, stretch=True),
                ColumnSpec("Status", width=130),
            ],
        )

        for payment in payments:
            row = pay_table.rowCount()
            pay_table.insertRow(row)
            set_cell(pay_table, row, 0, format_date(payment.payment_date))
            set_cell(pay_table, row, 1, format_peso(payment.amount),
                     align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            set_cell(
                pay_table, row, 2,
                str(getattr(payment.payment_method, "value", payment.payment_method)).title(),
            )
            set_cell(pay_table, row, 3, payment.reference_number or NOT_RECORDED)
            status_cell = set_cell(
                pay_table, row, 4, "Voided" if payment.is_voided else "Active",
                align=Qt.AlignmentFlag.AlignCenter,
            )
            status_cell.setForeground(Colors.qcolor(
                Colors.ERROR_RED if payment.is_voided else Colors.SUCCESS_GREEN
            ))

        pay_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        pay_table.setMinimumHeight(120)
        pay_layout.addWidget(pay_table)
        return group

    def _edit_assessment(self, assessment: Assessment) -> None:
        dialog = EditAssessmentDialog(assessment, parent=self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            updated = dialog.get_assessment()
        finally:
            dialog.deleteLater()

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            try:
                saved = self.assessment_service.update_assessment(
                    updated, updated_by=settings.DEFAULT_USER_ID
                )
            except DatabaseUnavailableError as exc:
                logger.warning("Edit assessment failed: %s", exc)
                show_db_error(self, exc, retry_callback=lambda: self._edit_assessment(assessment))
                return
            except Exception as exc:  # noqa: BLE001 - reported to the operator
                logger.exception("Edit assessment failed")
                QMessageBox.critical(self, "Could not save the assessment", str(exc))
                return
        finally:
            QApplication.restoreOverrideCursor()

        if not saved:
            QMessageBox.warning(
                self, "Not saved", "The database reported that nothing was changed."
            )
        self._load_assessment_panel()

    def _create_new_assessment(self):
        if self.current_student is None:
            QMessageBox.information(
                self, "No student selected",
                "Choose a student first: an assessment belongs to a student.",
            )
            return

        dialog = NewAssessmentDialog(self.current_student, self.assessment_service, parent=self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            assessment = dialog.get_assessment()
        finally:
            dialog.deleteLater()

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            try:
                # settings.DEFAULT_USER_ID, not a literal 1: the acting user is
                # configuration, and burying it in the widget meant the audit
                # columns could only ever say "1".
                assess_id = self.assessment_service.create_assessment(
                    assessment, created_by=settings.DEFAULT_USER_ID
                )
            except DatabaseUnavailableError as exc:
                logger.warning("Create assessment failed: %s", exc)
                show_db_error(self, exc, retry_callback=self._create_new_assessment)
                return
            except Exception as exc:  # noqa: BLE001 - reported to the operator
                logger.exception("Create assessment failed")
                QMessageBox.critical(
                    self, "Could not create the assessment",
                    f"No assessment was recorded.\n\n{exc}",
                )
                return
        finally:
            QApplication.restoreOverrideCursor()

        if not assess_id:
            QMessageBox.warning(
                self, "Not created", "The database reported that no assessment was written."
            )
            return

        QMessageBox.information(
            self, "Assessment created",
            f"An assessment for {assessment.academic_year} {assessment.semester} was "
            f"recorded for {self.current_student.full_name}.",
        )
        self._load_assessment_panel()
    
    def _record_payment(self, assessment: Assessment):
        dialog = RecordPaymentDialog(assessment, self.assessment_service, parent=self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            payment = dialog.get_payment()
        finally:
            dialog.deleteLater()

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            try:
                payment_id = self.assessment_service.record_payment(
                    payment, received_by=settings.DEFAULT_USER_ID
                )
            except DatabaseUnavailableError as exc:
                logger.warning("Record payment failed: %s", exc)
                show_db_error(self, exc, retry_callback=lambda: self._record_payment(assessment))
                return
            except Exception as exc:  # noqa: BLE001 - reported to the operator
                logger.exception("Record payment failed")
                QMessageBox.critical(
                    self, "Could not record the payment",
                    f"No payment was recorded against this assessment.\n\n{exc}",
                )
                return
        finally:
            QApplication.restoreOverrideCursor()

        if not payment_id:
            QMessageBox.warning(
                self, "Not recorded", "The database reported that no payment was written."
            )
            return

        QMessageBox.information(
            self, "Payment recorded",
            f"{format_peso(payment.amount)} was recorded on "
            f"{format_date(payment.payment_date)}.",
        )
        self._load_assessment_panel()


class EditAssessmentDialog(QDialog):
    """Edit the term, date and status of an existing assessment.

    Reached from the assessment tab's Edit Assessment button, which previously
    existed on every tab and was connected to nothing.
    """

    def __init__(self, assessment: Assessment, parent=None):
        super().__init__(parent)
        self.assessment = assessment
        self.setWindowTitle("Edit Assessment")
        self.setModal(True)
        self.setMinimumSize(420, 320)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        form = QFormLayout()
        form.setSpacing(12)

        self.academic_year = QLineEdit(self.assessment.academic_year)
        self.academic_year.setPlaceholderText("e.g., 2024-2025")
        form.addRow("Academic Year:", self.academic_year)

        self.semester = QComboBox()
        self.semester.addItems([item.value for item in Semester])
        index = self.semester.findText(str(self.assessment.semester))
        self.semester.setCurrentIndex(index if index >= 0 else 0)
        form.addRow("Semester:", self.semester)

        self.assessment_date = QDateEdit()
        self.assessment_date.setCalendarPopup(True)
        self.assessment_date.setDisplayFormat("yyyy-MM-dd")
        self.assessment_date.setDate(
            QDate(self.assessment.assessment_date)
            if self.assessment.assessment_date
            else QDate.currentDate()
        )
        form.addRow("Assessment Date:", self.assessment_date)

        self.status = QComboBox()
        self.status.addItems([item.value for item in AssessmentStatus])
        status_index = self.status.findText(
            str(getattr(self.assessment.status, "value", self.assessment.status))
        )
        self.status.setCurrentIndex(status_index if status_index >= 0 else 0)
        form.addRow("Status:", self.status)

        layout.addLayout(form)

        note = QLabel(
            "The assessment items, schedule and payments are not changed here. They "
            "stay attached to this record."
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

    def get_assessment(self) -> Assessment:
        """A copy of the record with the edited header fields replaced."""
        edited = replace(self.assessment)
        edited.academic_year = self.academic_year.text().strip()
        edited.semester = self.semester.currentText()
        edited.assessment_date = self.assessment_date.date().toPython()
        edited.status = AssessmentStatus(self.status.currentText())
        return edited


class NewAssessmentDialog(QDialog):
    def __init__(self, student: Student, assessment_service: AssessmentService, parent=None):
        super().__init__(parent)
        self.student = student
        self.assessment_service = assessment_service
        self.setWindowTitle("New Assessment")
        self.setModal(True)
        self.setMinimumSize(500, 600)
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        
        form = QFormLayout()
        form.setSpacing(12)
        
        self.academic_year = QLineEdit(settings.DEFAULT_ACADEMIC_YEAR)
        self.academic_year.setPlaceholderText("e.g., 2024-2025")
        form.addRow("Academic Year:", self.academic_year)

        # The same vocabulary as models.enrollment.Semester. The hardcoded list
        # here and the enum used by the enrollment page could drift apart.
        self.semester = QComboBox()
        self.semester.addItems([item.value for item in Semester])
        index = self.semester.findText(settings.DEFAULT_SEMESTER)
        self.semester.setCurrentIndex(index if index >= 0 else 0)
        form.addRow("Semester:", self.semester)
        
        self.assessment_date = QDateEdit()
        self.assessment_date.setCalendarPopup(True)
        self.assessment_date.setDate(QDate.currentDate())
        self.assessment_date.setDisplayFormat("yyyy-MM-dd")
        form.addRow("Assessment Date:*", self.assessment_date)
        
        self.status = QComboBox()
        self.status.addItems([s.value for s in AssessmentStatus])
        form.addRow("Status:", self.status)

        layout.addLayout(form)

        # Categories
        layout.addWidget(QLabel("Assessment Items"))
        try:
            categories = self.assessment_service.get_categories()
        except DatabaseUnavailableError as exc:
            logger.warning("Assessment categories unavailable: %s", exc)
            categories = []
            note = QLabel(
                "The assessment categories could not be read, so no amounts can be "
                "entered. Close this dialog and try again."
            )
            note.setWordWrap(True)
            note.setStyleSheet(f"color: {Colors.ERROR_RED};")
            layout.addWidget(note)
        except Exception as exc:  # noqa: BLE001 - reported in the dialog
            logger.exception("Assessment categories unavailable")
            categories = []
            note = QLabel(f"The assessment categories could not be read:\n{exc}")
            note.setWordWrap(True)
            note.setStyleSheet(f"color: {Colors.ERROR_RED};")
            layout.addWidget(note)
        
        self.category_spinboxes = {}
        for cat in categories:
            if cat.is_fee or cat.is_discount:
                hbox = QHBoxLayout()
                label = QLabel(cat.category_name)
                label.setFont(body_font(12))
                label.setMinimumWidth(200)
                
                spinbox = QDoubleSpinBox()
                # A ceiling high enough that no real fee reaches it: the old
                # 1,000,000 cap silently truncated anything larger.
                spinbox.setRange(0, 100_000_000)
                spinbox.setPrefix("₱")
                spinbox.setDecimals(2)
                spinbox.setSingleStep(100)
                spinbox.setValue(0)
                
                hbox.addWidget(label)
                hbox.addWidget(spinbox)
                hbox.addStretch()
                layout.addLayout(hbox)
                
                self.category_spinboxes[cat.id] = (spinbox, cat)
        
        layout.addStretch()
        
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("Create Assessment")
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

    def get_assessment(self) -> Assessment:
        items = []
        for cat_id, (spinbox, cat) in self.category_spinboxes.items():
            amount = Decimal(str(spinbox.value()))
            if amount > 0:
                items.append(AssessmentItem(
                    assessment_id=0,
                    category_id=cat_id,
                    description=cat.category_name,
                    amount=amount,
                    is_verified=False,
                ))
        
        return Assessment(
            student_id=self.student.id,
            academic_year=self.academic_year.text().strip(),
            semester=self.semester.currentText(),
            assessment_date=self.assessment_date.date().toPython(),
            status=AssessmentStatus(self.status.currentText()),
            items=items,
        )


class RecordPaymentDialog(QDialog):
    def __init__(self, assessment: Assessment, assessment_service: AssessmentService, parent=None):
        super().__init__(parent)
        self.assessment = assessment
        self.assessment_service = assessment_service
        self.setWindowTitle("Record Payment")
        self.setModal(True)
        self.setMinimumSize(400, 400)
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        
        form = QFormLayout()
        form.setSpacing(12)
        
        self.amount = QDoubleSpinBox()
        self.amount.setRange(0.01, 100_000_000)
        self.amount.setPrefix("₱ ")
        self.amount.setDecimals(2)
        self.amount.setSingleStep(100)
        # Prefill with what is outstanding, but never below the floor: a fully
        # paid assessment would otherwise prefill to 0.01 and offer a payment
        # that has nothing behind it.
        outstanding = float(self.assessment.outstanding_balance or 0)
        self.amount.setValue(outstanding if outstanding > 0.01 else 0.01)
        if outstanding <= 0:
            warning = QLabel(
                "This assessment shows no outstanding balance. A payment recorded here "
                "will be an overpayment."
            )
            warning.setWordWrap(True)
            warning.setStyleSheet(f"color: {Colors.WARNING_ORANGE};")
            form.addRow("", warning)
        form.addRow("Amount:", self.amount)
        
        self.payment_date = QDateEdit()
        self.payment_date.setCalendarPopup(True)
        self.payment_date.setDate(QDate.currentDate())
        self.payment_date.setDisplayFormat("yyyy-MM-dd")
        form.addRow("Date:", self.payment_date)
        
        self.payment_method = QComboBox()
        self.payment_method.addItems([m.value.title() for m in PaymentMethod])
        form.addRow("Method:", self.payment_method)
        
        self.reference_number = QLineEdit()
        self.reference_number.setPlaceholderText("Optional")
        form.addRow("Reference:", self.reference_number)
        
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("Optional notes")
        form.addRow("Notes:", self.notes)
        
        layout.addLayout(form)
        layout.addStretch()
        
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("Record Payment")
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def get_payment(self) -> Payment:
        return Payment(
            assessment_id=self.assessment.id,
            payment_date=self.payment_date.date().toPython(),
            amount=Decimal(str(self.amount.value())),
            payment_method=PaymentMethod(self.payment_method.currentText().lower()),
            reference_number=self.reference_number.text().strip() or None,
            notes=self.notes.text().strip() or None,
        )
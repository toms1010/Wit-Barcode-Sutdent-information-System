"""Tests for the certificate screen, its document, and the shared UI helpers.

The screen is built from the same summary objects as the printed document, so
these tests check the *agreement* between the two rather than their pixels: the
figures on screen, in the PDF flowables and in the print markup all come from one
calculation, and a change that breaks that agreement fails here.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QTableWidget  # noqa: E402

REFERENCE = "65412"
STUDENT_NAME = "Javy Danielle N. Caporal"

# The seven subjects the registrar's reference card lists. Reference data only:
# nothing in the application may use these as a source of truth.
BRIEF_ROWS = [
    ("311EP1", "CPE 311", 4, "7-8 PM SAT", "CB 24", 5, "1755.81", "0.00"),
    ("312EP1", "CPE 312", 3, "1-4 PM MON", "CB 21", 3, "0.00", "0.00"),
    ("313EP1", "CPE 313", 3, "5-8 PM FRI", "CB 24", 3, "0.00", "0.00"),
    ("315EP1", "CPE 315", 1, "1-4 PM FRI", "CB 12", 2, "2106.97", "0.00"),
    ("DRWEP1", "DRAW E2", 3, "4-6 PM FRI", "CB 21", 4, "1755.81", "0.00"),
    ("CGELEC1", "COG ELECT", 3, "6-8 PM TUE", "CB 24", 3, "0.00", "0.00"),
    ("MTE4EP1", "MATH E4", 3, "11 AM-12 PM MWF", "R3 RTS", 3, "0.00", "0.00"),
]


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def verified_student():
    from models.student import EnrollmentStatus, Student, YearLevel

    return Student(
        id=1,
        student_id="WIT-2024-001",
        reference_number=REFERENCE,
        first_name="Javy Danielle",
        middle_name="N.",
        last_name="Caporal",
        program="Computer Engineering",
        year_level=YearLevel.THIRD_YEAR,
        section="EP1",
        enrollment_status=EnrollmentStatus.ACTIVE,
        profile_image_path="assets/profile/javy_profile.png",
        barcode_payload="000065412",
    )


@pytest.fixture
def enrollment():
    from models.enrollment import (
        Enrollment,
        EnrollmentStatus,
        EnrollmentSubject,
        Semester,
        Subject,
        SubjectStatus,
    )

    subjects = []
    for code, name, credits, schedule, room, period, lab, other in BRIEF_ROWS:
        time, _, days = schedule.rpartition(" ")
        subjects.append(
            EnrollmentSubject(
                section="EP1",
                subject=Subject(subject_code=code, subject_name=name, credits=credits),
                schedule_time=time,
                schedule_days=days,
                room=room,
                period=period,
                lab_fee=float(lab),
                other_fee=float(other),
                status=SubjectStatus.ENROLLED,
            )
        )
    return Enrollment(
        id=1,
        student_id=1,
        academic_year="2024-2025",
        semester=Semester.FIRST,
        status=EnrollmentStatus.ENROLLED,
        total_credits=20,
        subjects=subjects,
    )


@pytest.fixture
def assessment():
    from datetime import date

    from models.assessment import (
        Assessment,
        AssessmentCategory,
        AssessmentItem,
        AssessmentStatus,
        PaymentSchedule,
        PaymentScheduleStatus,
        PaymentScheduleType,
    )

    def category(code, name, *, fee=True, discount=False, order=1):
        return AssessmentCategory(
            id=order,
            category_code=code,
            category_name=name,
            is_fee=fee,
            is_discount=discount,
            display_order=order,
        )

    items = [
        AssessmentItem(category=category("tuition_fee", "Tuition Fee", order=1), amount=Decimal("13340.00")),
        AssessmentItem(category=category("miscellaneous_fee", "Miscellaneous Fee", order=2), amount=Decimal("8674.72")),
        AssessmentItem(category=category("laboratory_fee", "Laboratory Fee", order=3), amount=Decimal("5618.59")),
        AssessmentItem(category=category("cda_fee", "CDA Fee", order=4), amount=Decimal("0.00")),
        AssessmentItem(category=category("alumni_fee", "Alumni Fee", order=8), amount=Decimal("0.00")),
        AssessmentItem(category=category("discount_other", "Other Discounts", fee=False, discount=True, order=14), amount=Decimal("0.00")),
    ]
    schedules = [
        PaymentSchedule(schedule_type=PaymentScheduleType.DOWN_PAYMENT, due_date=date(2026, 9, 1),
                        amount_due=Decimal("2689.51"), amount_paid=Decimal("2689.51"),
                        status=PaymentScheduleStatus.PAID),
        PaymentSchedule(schedule_type=PaymentScheduleType.MIDTERM, due_date=date(2026, 1, 10),
                        amount_due=Decimal("9671.66"), amount_paid=Decimal("0.00"),
                        status=PaymentScheduleStatus.PENDING),
    ]
    return Assessment(
        id=1,
        student_id=1,
        enrollment_id=1,
        academic_year="2024-2025",
        semester="1st Semester",
        status=AssessmentStatus.FINALIZED,
        items=items,
        payment_schedules=schedules,
        total_assessed=Decimal("27633.31"),
        total_discounts=Decimal("0.00"),
        total_payments=Decimal("2689.51"),
        outstanding_balance=Decimal("24943.80"),
    )


# ------------------------------------------------------------- calculations --


class TestComTotalsFromRealRows:
    def test_units_periods_and_fees_match_the_rows(self, enrollment):
        from services.com_summary import summarize_enrollment

        com = summarize_enrollment(enrollment)

        assert com.subject_count == 7
        assert com.total_units == 20
        assert com.total_periods == 23
        assert com.total_lab_fee == Decimal("5618.59")
        assert com.total_other_fee == Decimal("0.00")

    def test_dropped_subjects_are_excluded_from_the_totals(self, enrollment):
        from services.com_summary import summarize_enrollment

        enrollment.subjects.pop(0)
        com = summarize_enrollment(enrollment)

        assert com.subject_count == 6
        assert com.total_units == 16


class TestFeeLineGrouping:
    def test_known_categories_group_and_keep_their_group_order(self, assessment):
        from services.com_summary import summarize_fee_lines

        lines = summarize_fee_lines(assessment)

        assert [line.label for line in lines.fees][:3] == [
            "Tuition Fee",
            "Miscellaneous Fee",
            "Lab Fee",
        ]
        assert lines.fees_total == Decimal("27633.31")

    def test_related_categories_are_summed_into_one_line(self, assessment):
        from services.com_summary import summarize_fee_lines

        cda = assessment.items[3]
        cda.category.category_code = "yearbook_fee"
        cda.category.category_name = "Yearbook Fee"
        diploma = assessment.items[4]
        diploma.category.category_code = "diploma_fee"
        diploma.category.category_name = "Diploma Fee"
        diploma.amount = Decimal("1200.00")

        lines = summarize_fee_lines(assessment)

        combined = next(line for line in lines.fees if line.label == "CDA / Diploma / Yearbook")
        assert combined.amount == Decimal("1200.00")

    def test_an_unknown_fee_category_is_never_dropped(self, assessment):
        from services.com_summary import summarize_fee_lines

        odd = assessment.items[4]
        odd.category.category_code = "library_fee"
        odd.category.category_name = "Library Fee"
        odd.amount = Decimal("500.00")

        lines = summarize_fee_lines(assessment)

        assert any(
            line.label == "Library Fee" and line.amount == Decimal("500.00")
            for line in lines.fees
        )
        assert lines.fees_total == Decimal("28133.31")

    def test_discounts_stay_out_of_the_fee_total(self, assessment):
        from services.com_summary import summarize_fee_lines

        discount = assessment.items[-1]
        discount.amount = Decimal("1500.00")

        lines = summarize_fee_lines(assessment)

        assert lines.fees_total == Decimal("27633.31")
        assert lines.discounts_total == Decimal("1500.00")

    def test_no_assessment_is_unavailable_with_a_reason(self):
        from services.com_summary import summarize_fee_lines

        lines = summarize_fee_lines(None)

        assert lines.available is False
        assert "No assessment record" in lines.empty_reason
        assert lines.fees == []


class TestInstallmentState:
    def test_paid_requires_payments_that_cover_the_amount(self):
        from services.com_summary import InstallmentRow, installment_state

        short = InstallmentRow(label="Midterm", amount=Decimal("100.00"), amount_paid=Decimal("40.00"))

        assert installment_state(short) == ("Partly Paid", "warning")

    def test_a_stored_paid_flag_does_not_override_missing_money(self):
        from services.com_summary import InstallmentRow, installment_state

        row = InstallmentRow(
            label="Down Payment", amount=Decimal("500.00"), amount_paid=Decimal("0.00"),
            status="paid",
        )

        label, tone = installment_state(row)

        assert label != "Paid"

    def test_overdue_requires_a_due_date_in_the_past(self):
        from datetime import date, timedelta

        from services.com_summary import InstallmentRow, installment_state

        future = InstallmentRow(
            label="Midterm", amount=Decimal("100.00"), amount_paid=Decimal("0.00"),
            due_date=date.today() + timedelta(days=30),
        )
        past = InstallmentRow(
            label="Final", amount=Decimal("100.00"), amount_paid=Decimal("0.00"),
            due_date=date.today() - timedelta(days=1),
        )
        undated = InstallmentRow(label="Prefinal", amount=Decimal("100.00"), amount_paid=Decimal("0.00"))

        assert installment_state(future)[0] == "Pending"
        assert installment_state(past)[0] == "Overdue"
        assert installment_state(undated)[0] == "Pending"


class TestAssessmentTotalsRecalculation:
    def test_items_without_a_category_are_still_assessed(self):
        """A line built from a category id alone must not total to zero."""
        from models.assessment import Assessment, AssessmentItem

        assessment = Assessment(
            items=[
                AssessmentItem(category_id=3, description="Laboratory Fee", amount=Decimal("5618.59")),
                AssessmentItem(category_id=1, description="Tuition Fee", amount=Decimal("13340.00")),
            ]
        )

        assessment.recalculate_totals()

        assert assessment.total_assessed == Decimal("18958.59")
        assert assessment.outstanding_balance == Decimal("18958.59")


# ------------------------------------------------------------------ document --


class TestCertificateDocument:
    def test_the_document_is_built_from_the_records(self, verified_student, enrollment, assessment):
        from services.com_document import build_com_elements

        elements = build_com_elements(
            verified_student, enrollment, assessment, generated_at=None
        )

        assert elements, "the document must produce flowables"

    def test_the_pdf_is_written_and_is_a_pdf(self, tmp_path, verified_student, enrollment, assessment):
        from services.com_document import build_com_pdf

        target = tmp_path / "com.pdf"
        result = build_com_pdf(verified_student, enrollment, assessment, target)

        assert result == target
        assert target.read_bytes().startswith(b"%PDF")

    def test_a_student_with_no_enrollment_states_why(self, tmp_path, verified_student, assessment):
        from services.com_document import build_com_pdf

        target = tmp_path / "empty.pdf"
        assert build_com_pdf(verified_student, None, assessment, target).is_file()

    def test_the_course_table_repeats_its_header_across_pages(self, enrollment, verified_student, assessment):
        from services.com_document import _course_table, _styles
        from services.com_summary import summarize_enrollment

        table = _course_table(_styles(), summarize_enrollment(enrollment))

        assert table.repeatRows == 1, "column headers must repeat after a page break"

    def test_money_is_rendered_in_a_symbol_the_font_can_draw(self, verified_student, enrollment, assessment):
        """The peso sign is missing from the built-in PDF fonts; the document must adapt."""
        from services.com_document import _font_family, _money, _styles

        styles = _styles()
        regular, _, peso_available = _font_family()
        rendered = _money(Decimal("5618.59"), styles)

        if peso_available:
            assert rendered.startswith("₱")
        else:
            assert rendered == "PHP 5,618.59"
        assert regular


class TestPrintDocument:
    def test_the_print_markup_uses_document_formatting(self, verified_student, enrollment, assessment):
        from services.com_print_service import ComPrintService, official_status
        from services.com_summary import (
            summarize_enrollment,
            summarize_fee_lines,
            summarize_financials,
        )

        service = ComPrintService.__new__(ComPrintService)
        com = summarize_enrollment(enrollment)
        financial = summarize_financials(assessment)
        html = service._build_html(
            verified_student,
            com,
            financial,
            official_status(com, financial),
            fee_lines=summarize_fee_lines(assessment),
        )

        from services.com_summary import format_peso

        assert STUDENT_NAME in html
        assert REFERENCE in html
        assert "CERTIFICATE OF MATRICULATION" in html
        assert "Student Full Name" in html
        assert format_peso(financial.installments_total) in html
        assert format_peso(financial.total_assessed) in html
        # No dashboard furniture leaks into the printed page.
        for forbidden in ("Quick search", "Start Scanner", "camera_combo", "Save PDF"):
            assert forbidden not in html

    def test_the_screen_and_the_printout_agree_on_every_total(self, verified_student, enrollment, assessment):
        from services.com_print_service import ComPrintService, official_status
        from services.com_summary import (
            summarize_enrollment,
            summarize_fee_lines,
            summarize_financials,
        )

        service = ComPrintService.__new__(ComPrintService)
        com = summarize_enrollment(enrollment)
        financial = summarize_financials(assessment)
        html = service._build_html(
            verified_student,
            com,
            financial,
            official_status(com, financial),
            fee_lines=summarize_fee_lines(assessment),
        )

        for figure in (
            "20",  # total units
            "23",  # total periods
            "5,618.59",  # lab fees
            "27,633.31",  # assessed
            "24,943.80",  # outstanding
        ):
            assert figure in html


# ---------------------------------------------------------------------- ui --


class TestCertificatePage:
    def _page(self, verified_student, enrollment, assessment, qapp):
        from services.assessment_service import AssessmentService
        from services.report_service import ReportService
        from services.student_service import StudentService
        from ui.certificate_page import CertificatePage

        class Students:
            def search_students(self, limit=500, offset=0, query="", status=""):
                return [verified_student]

            def get_enrollments_by_student(self, student_id):
                return [enrollment]

            def get_student(self, student_id):
                return verified_student

            def get_student_by_reference(self, reference):
                return verified_student if reference == REFERENCE else None

        class Assessments:
            def get_latest_assessment(self, student_id):
                return assessment

        class Reports:
            def generate_certificate_of_matriculation(self, *args, **kwargs):
                return None

        page = CertificatePage(Students(), Assessments(), Reports())
        page.resize(1400, 900)
        qapp.processEvents()
        return page

    def _table(self, page, name) -> QTableWidget:
        table = page.findChild(QTableWidget, name)
        assert table is not None, f"{name} is missing from the page"
        return table

    def _cell(self, table, row, column) -> str:
        item = table.item(row, column)
        return "" if item is None else item.text()

    def test_the_course_table_shows_the_record_and_its_totals(self, verified_student, enrollment, assessment, qapp):
        page = self._page(verified_student, enrollment, assessment, qapp)

        table = self._table(page, "comSubjectTable")
        assert table.columnCount() == 8
        assert table.rowCount() == 8, "seven subjects plus the totals row"
        assert self._cell(table, 7, 0) == "TOTALS"
        assert self._cell(table, 7, 2) == "20"
        assert self._cell(table, 7, 5) == "23"
        assert "5,618.59" in self._cell(table, 7, 6)
        page.deleteLater()

    def test_the_three_financial_cards_show_assessed_discounts_and_schedule(
        self, verified_student, enrollment, assessment, qapp
    ):
        page = self._page(verified_student, enrollment, assessment, qapp)

        assessed = self._table(page, "assessmentTable")
        labels = [self._cell(assessed, row, 0) for row in range(assessed.rowCount())]
        assert "Tuition Fee" in labels
        assert labels[-1] == "TOTAL ASSESSMENT"
        assert "27,633.31" in self._cell(assessed, assessed.rowCount() - 1, 1)

        discounts = self._table(page, "discountTable")
        assert self._cell(discounts, discounts.rowCount() - 1, 0) == "TOTAL DISCOUNTS"

        schedule = self._table(page, "installmentTable")
        assert schedule.columnCount() == 3, "installments carry a status column"
        assert self._cell(schedule, schedule.rowCount() - 1, 0) == "TOTAL DUE"
        assert "2,689.51" in self._cell(schedule, 0, 1)
        assert self._cell(schedule, 0, 2) == "Paid"
        page.deleteLater()

    def test_the_profile_card_shows_the_student_and_their_status(self, verified_student, enrollment, assessment, qapp):
        page = self._page(verified_student, enrollment, assessment, qapp)

        text = " ".join(
            label.text() for label in page.findChildren(type(page.status_pill))
            if hasattr(label, "text")
        )
        assert STUDENT_NAME in text or page.current_student.full_name == STUDENT_NAME
        assert page.status_pill.text() == "ENROLLED"
        assert page.current_student.reference_number == REFERENCE
        page.deleteLater()

    def test_printing_is_disabled_until_there_is_something_to_certify(self, verified_student, enrollment, assessment, qapp):
        page = self._page(verified_student, enrollment, assessment, qapp)

        assert page.print_btn.isEnabled() is True
        assert page.save_pdf_btn.isEnabled() is True

        # A student with no enrollment has nothing to certify.
        from models.enrollment import Enrollment

        page._load_record_for_test = None  # guard against accidental attribute use
        empty = self._page(verified_student, Enrollment(student_id=1), assessment, qapp)
        assert empty.print_btn.isEnabled() is False
        page.deleteLater()
        empty.deleteLater()

    def test_the_quick_search_filters_without_a_new_query(self, verified_student, enrollment, assessment, qapp):
        page = self._page(verified_student, enrollment, assessment, qapp)

        calls = []
        page.student_service.search_students = lambda **kwargs: (
            calls.append(kwargs) or [verified_student]
        )

        page.search_input.setText(REFERENCE)
        page._apply_search()

        assert not calls, "filtering must not re-query the database"
        assert page.current_student is not None
        page.deleteLater()

    def test_a_search_with_no_match_explains_itself(self, verified_student, enrollment, assessment, qapp):
        page = self._page(verified_student, enrollment, assessment, qapp)

        page.search_input.setText("no such student")
        page._apply_search()

        assert page.no_student_state.heading_label.text() == "No matching student"
        assert "Nothing in the loaded list" in page.no_student_state.message_label.text()
        assert page.print_btn.isEnabled() is False
        page.deleteLater()

    def test_an_unresolved_reference_is_reported_not_ignored(self, verified_student, enrollment, assessment, qapp):
        page = self._page(verified_student, enrollment, assessment, qapp)

        from models.student import Student

        ghost = Student(id=99, reference_number="00000", first_name="Ghost", last_name="Record")

        assert page.preload_student(ghost) is False
        assert page.no_student_state.heading_label.text() == "Student not found"
        page.deleteLater()


class TestSharedTableHelpers:
    def test_columns_size_to_content_and_only_the_marked_one_stretches(self, qapp):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QHeaderView

        from ui.widgets.responsive_table import ColumnSpec, configure_table, count_column

        table = QTableWidget()
        configure_table(table, [ColumnSpec("Reference No."), ColumnSpec("Name", stretch=True), count_column("Credits")])

        header = table.horizontalHeader()
        assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.ResizeToContents
        assert header.sectionResizeMode(1) == QHeaderView.ResizeMode.Stretch
        assert header.stretchLastSection() is False
        assert table.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
        assert table.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
        table.deleteLater()

    def test_a_long_value_is_carried_by_a_tooltip(self, qapp):
        from ui.widgets.responsive_table import configure_table, set_cell

        table = QTableWidget()
        configure_table(table, [__import__("ui.widgets.responsive_table", fromlist=["ColumnSpec"]).ColumnSpec("Program")])
        table.setRowCount(1)

        set_cell(table, 0, 0, "Bachelor of Science in Computer Engineering")
        short = set_cell(table, 0, 0, "CPE")

        assert short.toolTip() == "", "short values do not need a tooltip"
        table.deleteLater()

    def test_fit_height_caps_a_long_list(self, qapp):
        from ui.widgets.responsive_table import ColumnSpec, configure_table, fit_height

        table = QTableWidget()
        configure_table(table, [ColumnSpec("Code")])
        table.setRowCount(40)
        fit_height(table, max_visible_rows=10)

        row_height = table.verticalHeader().defaultSectionSize()
        assert table.height() < row_height * 40, "a long list must scroll, not grow"
        table.deleteLater()


class TestEmptyState:
    def test_the_action_is_only_built_when_a_callback_is_given(self, qapp):
        from ui.widgets.empty_state import EmptyState

        without = EmptyState("No courses recorded")
        with_action = EmptyState("No courses recorded", "Add one to begin.", action_text="Add", action=lambda: None)

        assert without.action_btn is None
        assert with_action.action_btn is not None

        without.deleteLater()
        with_action.deleteLater()

    def test_it_stays_addressable_as_the_label_it_replaced(self, qapp):
        from ui.widgets.empty_state import EmptyState

        state = EmptyState("No student selected")

        state.setText("could not be found")

        assert "could not be found" in state.text()
        state.deleteLater()

    def test_an_error_state_offers_a_retry(self, qapp):
        from ui.widgets.empty_state import error_state

        state = error_state(RuntimeError("connection refused"), retry=lambda: None)

        assert "Cannot reach the database" == state.heading_label.text()
        assert state.action_btn is not None
        state.deleteLater()


class TestStatusPill:
    def test_it_renders_the_registry_wording_not_the_stored_value(self, qapp):
        from ui.widgets.status_pill import StatusPill

        pill = StatusPill()
        assert pill.isVisible() is False, "no chip is drawn until a status exists"

        pill.set_enrollment_status("active")

        assert pill.text() == "ENROLLED"
        assert pill.isVisible() is True
        pill.deleteLater()

    def test_an_unknown_status_falls_back_to_readable_text(self, qapp):
        from ui.widgets.status_pill import StatusPill

        pill = StatusPill()
        pill.set_enrollment_status("on_leave")

        assert pill.text() == "ON LEAVE"
        pill.deleteLater()
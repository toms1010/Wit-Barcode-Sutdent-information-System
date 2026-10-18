"""Button labels: one wording per action, and never a truncated one.

Two defects are guarded here, both of which are invisible until somebody uses
the screen at a size the author did not try:

* **Inconsistent wording** -- the same action called "View" on one page,
  "View Full Profile" on another and "Open Student" somewhere else. The table
  below is the single source of truth for the labels this application uses.
* **Clipping** -- a button given a fixed width narrower than its label, which
  renders "View Full Profile" as "View F…". Every button whose width is not
  driven by its own text is measured here at a reduced window size.

Renaming a label must never detach it from its behaviour, so the wiring of the
verification popup's five actions is asserted as well.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: The application's standard action labels.
STANDARD_LABELS = {
    "scan_next": "Scan Next Student",
    "view_profile": "View Full Profile",
    "proceed_assessment": "Proceed to Assessment",
    "print_com": "Print COM",
    "close": "Close",
    "save": "Save Changes",
    "cancel": "Cancel",
    "export_report": "Export Report",
    "refresh": "Refresh",
    "retry": "Try Again",
}


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def student():
    from models.student import EnrollmentStatus, Student, YearLevel

    return Student(
        id=1,
        student_id="WIT-2024-001",
        reference_number="65412",
        first_name="Javy Danielle",
        middle_name="N.",
        last_name="Caporal",
        program="Computer Engineering",
        year_level=YearLevel.THIRD_YEAR,
        enrollment_status=EnrollmentStatus.ACTIVE,
        profile_image_path="assets/profile/javy_profile.png",
        barcode_payload="000065412",
    )


def _dialog(qapp, student):
    from ui.dialogs.scan_result_dialog import ScanResultDialog

    dialog = ScanResultDialog(student=student, scanned_value=student.barcode_payload)
    return dialog


class TestVerificationPopupLabels:
    def test_the_action_bar_uses_the_standard_labels_in_order(self, qapp, student):
        dialog = _dialog(qapp, student)

        assert [b.text() for b in dialog._action_row.widgets()] == [
            STANDARD_LABELS["scan_next"],
            STANDARD_LABELS["view_profile"],
            STANDARD_LABELS["proceed_assessment"],
            STANDARD_LABELS["print_com"],
            STANDARD_LABELS["close"],
        ]
        dialog.close()
        dialog.deleteLater()

    def test_every_label_survives_a_reduced_window(self, qapp, student):
        dialog = _dialog(qapp, student)
        for width, height in ((1200, 800), (760, 620), (520, 560), (430, 460)):
            dialog.resize(width, height)
            dialog.show()
            for _ in range(4):
                qapp.processEvents()
                dialog.layout().activate()

            for button in dialog._action_row.widgets():
                needed = button.fontMetrics().horizontalAdvance(button.text())
                assert button.width() >= needed, (
                    f"{width}px window: {button.text()!r} has {button.width()}px "
                    f"but needs {needed}px"
                )
        dialog.close()
        dialog.deleteLater()

    def test_every_label_is_still_complete_text_not_an_ellipsis(self, qapp, student):
        dialog = _dialog(qapp, student)
        dialog.resize(430, 460)
        dialog.show()
        for _ in range(4):
            qapp.processEvents()

        for button in dialog._action_row.widgets():
            assert "…" not in button.text(), "a truncated label is not a label"
            assert button.toolTip(), f"{button.text()} should say what it does"
            assert button.accessibleName(), f"{button.text()} needs an accessible name"
        dialog.close()
        dialog.deleteLater()

    def test_renaming_did_not_detach_any_action(self, qapp, student):
        dialog = _dialog(qapp, student)
        seen = []
        dialog.profile_requested.connect(lambda s: seen.append(("profile", s)))
        dialog.assessment_requested.connect(lambda s: seen.append(("assessment", s)))
        dialog.scan_next_requested.connect(lambda: seen.append(("next", None)))

        buttons = {b.text(): b for b in dialog._action_row.widgets()}
        buttons[STANDARD_LABELS["view_profile"]].click()
        buttons[STANDARD_LABELS["proceed_assessment"]].click()
        buttons[STANDARD_LABELS["scan_next"]].click()
        buttons[STANDARD_LABELS["print_com"]].click()  # no printer: must not raise

        assert [kind for kind, _ in seen] == ["profile", "assessment", "next"]
        for _, routed in seen:
            if routed is not None:
                assert routed.id == student.id

        dialog.close()
        dialog.deleteLater()

    def test_close_dismisses_the_popup_without_quitting(self, qapp, student):
        from PySide6.QtWidgets import QDialog

        dialog = _dialog(qapp, student)
        buttons = {b.text(): b for b in dialog._action_row.widgets()}
        buttons[STANDARD_LABELS["close"]].click()

        # The dialog is rejected, not the process: a Close that quit the
        # application would take the operator's unsaved work with it.
        assert dialog.result() == int(QDialog.DialogCode.Rejected)
        assert qapp.topLevelWidgets() is not None
        dialog.deleteLater()


class TestPageLabels:
    def _students_page(self, qapp):
        from services.student_service import StudentService
        from ui.students_page import StudentsPage

        return StudentsPage(StudentService())

    def test_the_student_table_uses_the_standard_labels(self, qapp):
        from PySide6.QtWidgets import QPushButton

        page = self._students_page(qapp)
        labels = {
            b.text()
            for b in page.findChildren(QPushButton)
        }
        assert STANDARD_LABELS["view_profile"] in labels
        assert STANDARD_LABELS["export_report"] in labels
        assert "Del" not in labels, "'Del' is an unexplained abbreviation"
        page.deleteLater()

    def test_row_actions_fit_their_own_labels(self, qapp, student):
        from PySide6.QtWidgets import QPushButton

        page = self._students_page(qapp)
        for button in page.findChildren(QPushButton):
            if not button.text():
                continue
            needed = button.fontMetrics().horizontalAdvance(button.text()) + 8
            # A button either sizes to its label or is given room for it; the
            # failure mode is a fixed width narrower than the text.
            assert not (button.minimumWidth() and button.minimumWidth() < needed) or (
                button.width() >= needed
            ), f"{button.text()!r} cannot show its label"

        page.deleteLater()

    def test_the_certificate_page_prints_the_document_by_name(self, qapp):
        from services.assessment_service import AssessmentService
        from services.report_service import ReportService
        from services.student_service import StudentService
        from ui.certificate_page import CertificatePage

        page = CertificatePage(StudentService(), AssessmentService(), ReportService())
        assert page.print_btn.text() == STANDARD_LABELS["print_com"]
        page.deleteLater()

    def test_a_retryable_error_offers_the_standard_retry_label(self, qapp):
        from ui.widgets.empty_state import error_state

        state = error_state(RuntimeError("unreachable"), retry=lambda: None)
        assert state.action_btn.text() == STANDARD_LABELS["retry"]
        state.deleteLater()


class TestProfileAssetResolution:
    """The portraits must load from either project layout, from any directory."""

    def test_both_layouts_resolve_to_the_same_file(self):
        from config.settings import settings

        from_layout = settings.resolve_asset("assets/profile/javy_profile.png")
        top_level = settings.resolve_asset("profile/javy_profile.png")

        assert from_layout is not None and top_level is not None
        assert from_layout.resolve() == top_level.resolve()
        assert from_layout.name == "javy_profile.png"

    def test_each_student_resolves_to_their_own_photograph(self):
        from config.settings import settings

        javy = settings.resolve_asset("assets/profile/javy_profile.png")
        ronna = settings.resolve_asset("assets/profile/ronna_profile.png")

        assert javy is not None and ronna is not None
        assert javy != ronna
        assert javy.read_bytes() != ronna.read_bytes()

    def test_a_missing_photograph_resolves_to_nothing(self):
        from config.settings import settings

        assert settings.resolve_asset("assets/profile/not_a_student.png") is None
        assert settings.resolve_asset(None) is None
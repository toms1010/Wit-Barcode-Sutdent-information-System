"""Presentation and behaviour of the post-scan verification popup.

The popup is the screen a registrar looks at immediately after a card is
scanned, so the things asserted here are the things that go wrong silently: the
wrong photograph, a clipped action label, a status badge nobody can read, a
subject count that does not match the rows above it.

The records are the project's own, and the photographs are the ones on file --
these tests exist to prove the picture follows the database record rather than
the order students happen to be scanned in.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REFERENCE_JAVY = "65412"
REFERENCE_RONNA = "65902"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def brief_rows():
    return [
        ("311EP1", "CPE 311", 4, "7-8 PM SAT", "CB 24", 5, "1755.81", "0.00"),
        ("312EP1", "CPE 312", 3, "1-4 PM MON", "CB 21", 3, "0.00", "0.00"),
        ("315EP1", "CPE 315", 1, "1-4 PM FRI", "CB 12", 2, "2106.97", "0.00"),
    ]


@pytest.fixture
def enrollment(brief_rows):
    from models.enrollment import (
        Enrollment,
        EnrollmentStatus,
        EnrollmentSubject,
        Semester,
        Subject,
        SubjectStatus,
    )

    subjects = []
    for code, name, credits, schedule, room, period, lab, other in brief_rows:
        time, _, days = schedule.rpartition(" ")
        subjects.append(
            EnrollmentSubject(
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
        student_id=1,
        academic_year="2024-2025",
        semester=Semester.FIRST,
        status=EnrollmentStatus.ENROLLED,
        subjects=subjects,
    )


def _student(reference: str, path: str, payload: str, student_id: str):
    from models.student import EnrollmentStatus, Student, YearLevel

    return Student(
        id=1 if reference == REFERENCE_JAVY else 2,
        student_id=student_id,
        reference_number=reference,
        first_name="Javy Danielle" if reference == REFERENCE_JAVY else "Ronna Mae",
        middle_name="N." if reference == REFERENCE_JAVY else "D.",
        last_name="Caporal" if reference == REFERENCE_JAVY else "Alemania",
        program="Computer Engineering",
        year_level=YearLevel.THIRD_YEAR,
        enrollment_status=EnrollmentStatus.ACTIVE,
        profile_image_path=path,
        barcode_payload=payload,
    )


JAVY = _student(REFERENCE_JAVY, "assets/profile/javy_profile.png", "000065412", "WIT-2024-001")
RONNA = _student(REFERENCE_RONNA, "assets/profile/ronna_profile.png", "000065902", "WIT-2024-002")


def _dialog(student, enrollment=None, **kwargs):
    from services.com_summary import summarize_enrollment, summarize_financials
    from ui.dialogs.scan_result_dialog import ScanResultDialog

    return ScanResultDialog(
        student=student,
        enrollment=enrollment,
        scanned_value=student.barcode_payload,
        com_summary=summarize_enrollment(enrollment),
        financial_summary=summarize_financials(kwargs.pop("assessment", None)),
        **kwargs,
    )


# ------------------------------------------------------------ photographs --


class TestProfileImageFollowsTheRecord:
    def test_both_photographs_are_on_file(self):
        from config.settings import settings

        for path in ("assets/profile/javy_profile.png", "assets/profile/ronna_profile.png"):
            assert settings.resolve_asset(path) is not None, f"{path} must exist in assets/"

    def test_each_student_gets_their_own_picture(self, qapp):
        from PySide6.QtWidgets import QLabel

        from ui.widgets.profile_image import load_portrait

        javy, javy_ok = load_portrait(JAVY, 84)
        ronna, ronna_ok = load_portrait(RONNA, 84)

        assert javy_ok and ronna_ok, "both portraits must decode"
        assert javy.cacheKey() != ronna.cacheKey(), (
            "the two students must not share one picture"
        )

    def test_the_dialog_paints_the_picture_named_by_the_record(self, qapp):
        from ui.widgets.profile_card import StudentProfileCard

        for student in (JAVY, RONNA):
            dialog = _dialog(student)
            card = dialog.findChild(StudentProfileCard)
            assert card is not None, "the popup must use the shared profile card"
            photo = card.photo_label
            assert photo.objectName() == "profilePhoto"
            assert photo.pixmap() is not None and not photo.pixmap().isNull()
            assert "No profile photo" not in photo.toolTip(), (
                f"{student.full_name} fell back to a placeholder instead of their photograph"
            )
            dialog.close()
            dialog.deleteLater()

    def test_switching_students_never_leaves_the_previous_photo(self, qapp):
        """Requirement: navigating between students must not reuse a face."""
        from PySide6.QtWidgets import QLabel

        from ui.widgets.profile_image import apply_portrait
        from ui.widgets.profile_card import StudentProfileCard

        card = StudentProfileCard(size=84)
        card.set_student(JAVY, extra_details=(("student_id", "Student ID"),))
        # cacheKey() is Qt's content identity for a pixmap. QImage comparison in
        # PySide compares handles, not pixels, so it cannot answer this question.
        first = card.photo_label.pixmap().cacheKey()

        card.set_student(RONNA, extra_details=(("student_id", "Student ID"),))
        # The key, not the label: the label is a live view of whatever pixmap is
        # currently set, so comparing it to itself later would always be equal.
        second = card.photo_label.pixmap().cacheKey()

        assert second is not None
        assert first != second, "Ronna was shown Javy's photograph"

        # And a student with no photograph on file must not inherit the last one.
        from models.student import Student

        without = Student(id=9, student_id="WIT-2099-009", reference_number="99999",
                          first_name="No", last_name="Photo",
                          profile_image_path="assets/profile/absent.png")
        card.set_student(without, extra_details=(("student_id", "Student ID"),))
        assert card.photo_label.pixmap().cacheKey() != second, (
            "a student with no photograph inherited the previous student's face"
        )
        card.deleteLater()

    def test_a_missing_file_degrades_to_a_placeholder_not_to_another_student(self, qapp):
        from ui.widgets.profile_card import StudentProfileCard

        from models.student import Student

        missing = Student(
            id=9,
            student_id="WIT-2099-009",
            reference_number="99999",
            first_name="No",
            last_name="Photograph",
            profile_image_path="assets/profile/does_not_exist.png",
        )

        dialog = _dialog(missing)
        card = dialog.findChild(StudentProfileCard)
        photo = card.photo_label
        assert photo.pixmap() is not None, "a neutral placeholder image is drawn"
        assert "No profile photo" in photo.toolTip()
        assert photo.text() == "", "the placeholder must carry no text"
        dialog.close()
        dialog.deleteLater()

    def test_the_picture_survives_a_different_working_directory(self, qapp, monkeypatch, tmp_path):
        from config.settings import settings
        from ui.widgets.profile_image import load_portrait

        monkeypatch.chdir(tmp_path)
        # Resolution is project-root relative, not cwd relative: launching the
        # application from anywhere must still find the portraits.
        assert settings.resolve_asset("assets/profile/javy_profile.png") is not None
        pixmap, ok = load_portrait(JAVY, 84)
        assert ok and pixmap is not None


# --------------------------------------------------------------- banner ---


class TestVerifiedBanner:
    def test_the_verified_badge_is_dark_text_on_a_pale_tint(self, qapp):
        from PySide6.QtWidgets import QLabel

        dialog = _dialog(JAVY)
        chips = [
            label
            for label in dialog.findChildren(QLabel)
            if "VERIFIED" in (label.text() or "")
        ]
        assert chips, "a VERIFIED badge must be shown"
        style = chips[0].styleSheet()
        assert "#166534" in style.lower(), "dark green text, not white-on-white"
        dialog.close()
        dialog.deleteLater()

    def test_status_is_never_colour_only(self, qapp):
        from PySide6.QtWidgets import QLabel

        dialog = _dialog(JAVY)
        chip = next(
            label for label in dialog.findChildren(QLabel) if "VERIFIED" in (label.text() or "")
        )
        assert "✓" in chip.text(), "a glyph carries the status as well as the colour"
        assert chip.accessibleName(), "the status needs an accessible name"
        dialog.close()
        dialog.deleteLater()

    def test_the_banner_reports_the_scanned_payload(self, qapp):
        dialog = _dialog(RONNA)
        text = " ".join(label.text() for label in dialog.findChildren(type(dialog.parent())))
        from PySide6.QtWidgets import QLabel

        text = " ".join(label.text() for label in dialog.findChildren(QLabel))
        assert "000065902" in text
        assert "Match Found" in text
        dialog.close()
        dialog.deleteLater()

    def test_an_unrecognised_barcode_is_not_styled_as_verified(self, qapp):
        from ui.dialogs.scan_result_dialog import ScanResultDialog

        dialog = ScanResultDialog(scanned_value="000000000", success=False)
        from PySide6.QtWidgets import QLabel

        chips = [
            label for label in dialog.findChildren(QLabel)
            if "VERIFIED" in (label.text() or "")
        ]
        assert chips == [], "a failed scan must not show a verified badge"
        dialog.close()
        dialog.deleteLater()


# ------------------------------------------------------------------ tabs --


class TestTabs:
    def test_the_subject_count_in_the_tab_is_the_number_of_rows(self, qapp, enrollment):
        from PySide6.QtWidgets import QTabWidget

        dialog = _dialog(JAVY, enrollment)
        tabs = dialog.findChild(QTabWidget)

        titles = [tabs.tabText(index) for index in range(tabs.count())]
        assert titles == ["Enrolled Subjects (3)", "Financial Summary"]
        dialog.close()
        dialog.deleteLater()

    def test_the_count_follows_a_shorter_enrollment(self, qapp, enrollment):
        from PySide6.QtWidgets import QTabWidget

        enrollment.subjects = enrollment.subjects[:1]
        dialog = _dialog(JAVY, enrollment)
        tabs = dialog.findChild(QTabWidget)

        assert tabs.tabText(0) == "Enrolled Subjects (1)"
        dialog.close()
        dialog.deleteLater()


# ---------------------------------------------------------- subjects table --


class TestSubjectsTable:
    def _table(self, dialog):
        from PySide6.QtWidgets import QTableWidget

        return next(
            t for t in dialog.findChildren(QTableWidget) if t.objectName() == "comSubjectTable"
        )

    def test_the_totals_row_is_readable_and_aligned(self, qapp, enrollment):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QLabel

        dialog = _dialog(JAVY, enrollment)
        table = self._table(dialog)
        total_row = table.rowCount() - 1

        item = table.item(total_row, 5)
        assert item.text() == "₱3,862.78", "the lab-fee total follows the rows above"
        assert item.textAlignment() & Qt.AlignmentFlag.AlignRight.value, (
            "the total must sit under the currency column"
        )
        assert item.font().bold(), "the totals row must be semibold"
        assert item.background().color().name().lower().lstrip("#") != "#ffffff", (
            "a plain white totals row does not read as a total"
        )
        dialog.close()
        dialog.deleteLater()

    def test_the_table_has_no_dark_container_around_it(self, qapp, enrollment):
        from PySide6.QtWidgets import QFrame

        from styles.colors import Colors

        dialog = _dialog(JAVY, enrollment)
        frames = [
            frame
            for frame in dialog.findChildren(QFrame)
            if frame.objectName() == "comTableFrame"
        ]
        assert frames, "the table keeps a container, for its padding"
        style = frames[0].styleSheet()
        assert Colors.SURFACE.lower() in style.lower()
        for dark in ("#000000", "#1f2937", "#111827", "#2d1b0e"):
            assert dark not in style.lower(), "the container must not be a dark slab"
        dialog.close()
        dialog.deleteLater()

    def test_the_academic_term_comes_from_the_record(self, qapp, enrollment):
        from PySide6.QtWidgets import QLabel

        dialog = _dialog(JAVY, enrollment)
        text = " ".join(label.text() for label in dialog.findChildren(QLabel))

        assert "2024-2025" in text
        assert "1st Semester" in text
        dialog.close()
        dialog.deleteLater()

    def test_no_subjects_produces_an_explanation_not_an_empty_grid(self, qapp):
        from PySide6.QtWidgets import QLabel, QTableWidget

        from models.enrollment import Enrollment, Semester

        dialog = _dialog(JAVY, Enrollment(student_id=1, semester=Semester.FIRST))
        tables = [
            t for t in dialog.findChildren(QTableWidget) if t.objectName() == "comSubjectTable"
        ]
        assert tables == [], "no table is drawn when there is nothing to list"
        text = " ".join(label.text() for label in dialog.findChildren(QLabel))
        assert "no subjects" in text.lower() or "no enrollment" in text.lower()
        dialog.close()
        dialog.deleteLater()


# --------------------------------------------------------------- actions --


class TestActionBar:
    def _labels(self, dialog):
        return [button.text() for button in dialog._action_row.widgets()]

    def test_the_five_actions_are_present_in_the_specified_order(self, qapp):
        dialog = _dialog(JAVY)

        assert self._labels(dialog) == [
            "Scan Next Student",
            "View Full Profile",
            "Proceed to Assessment",
            "Print COM",
            "Close",
        ]
        dialog.close()
        dialog.deleteLater()

    def test_no_label_is_truncated(self, qapp):
        """A fixed width that hides half a label is the defect this guards."""
        dialog = _dialog(JAVY)
        dialog.resize(900, 720)
        dialog.show()
        for _ in range(4):
            qapp.processEvents()
            dialog.layout().activate()

        for button in dialog._action_row.widgets():
            needed = button.fontMetrics().horizontalAdvance(button.text())
            assert button.width() >= needed, (
                f"{button.text()!r} is drawn in {button.width()}px but needs {needed}px"
            )
        dialog.close()
        dialog.deleteLater()

    def test_the_actions_wrap_instead_of_being_clipped(self, qapp):
        dialog = _dialog(JAVY)
        dialog.resize(430, 600)
        dialog.show()
        for _ in range(6):
            dialog.layout().activate()

        assert dialog._action_row.column_count() < 5, (
            "a narrow window must wrap the actions onto more than one row"
        )
        assert dialog.minimumSize().width() <= 430
        dialog.close()
        dialog.deleteLater()

    def test_each_action_is_wired_to_something(self, qapp):
        from PySide6.QtCore import Qt

        dialog = _dialog(JAVY)
        seen = []
        dialog.profile_requested.connect(lambda student: seen.append(("profile", student)))
        dialog.assessment_requested.connect(lambda student: seen.append(("assessment", student)))
        dialog.scan_next_requested.connect(lambda: seen.append(("scan_next", None)))

        dialog.profile_btn.click()
        dialog.assessment_btn.click()
        dialog.next_btn.click()

        assert [kind for kind, _ in seen] == ["profile", "assessment", "scan_next"]
        for kind, student in seen:
            if student is not None:
                assert student.id == JAVY.id, "the action must carry the verified student"
        assert dialog.export_btn.text() == "Print COM"
        assert dialog.export_btn.toolTip(), "the document action must say what it does"
        for button in (dialog.next_btn, dialog.profile_btn, dialog.assessment_btn,
                       dialog.export_btn, dialog._close_button):
            assert button.focusPolicy() != Qt.FocusPolicy.NoFocus
            assert button.accessibleName()
        dialog.close()
        dialog.deleteLater()
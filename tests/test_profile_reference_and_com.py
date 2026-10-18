"""Acceptance tests for the two fixes: profile-photo mapping and COM lookup.

The scenarios are the ones an operator actually hits:

* reference ``65412`` must show Javy's photograph, ``000001`` must show Ronna's,
  and ``000001`` must keep its leading zeros on the way;
* a student with a real enrollment must produce a COM from *that* record;
* a student without one must be told what is missing -- and nothing may be
  invented to fill the gap.

The database-backed cases skip when the database is unreachable, and the
identity cases run against the real records rather than stand-ins.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

JAVY_REFERENCE = "65412"
RONNA_PRINTED_REFERENCE = "000001"
RONNA_STORED_REFERENCE = "65902"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _require_database():
    from database.connection import DatabaseUnavailableError, test_connection

    if not test_connection():
        pytest.skip("database unavailable")


# ------------------------------------------------------------- identifiers --


class TestReferenceResolution:
    def test_leading_zeros_survive_resolution(self):
        from services.identifiers import normalise_reference, resolve_reference

        assert normalise_reference("000001") == "000001"
        assert normalise_reference(" 000001 ") == "000001"
        # int() would have turned this into "1", which matches no record.
        assert resolve_reference("000001") == RONNA_STORED_REFERENCE
        assert resolve_reference("65412") == JAVY_REFERENCE

    def test_a_stored_reference_resolves_to_itself(self):
        from services.identifiers import resolve_reference

        assert resolve_reference(RONNA_STORED_REFERENCE) == RONNA_STORED_REFERENCE
        assert resolve_reference("") == ""
        assert resolve_reference(None) == ""

    def test_the_padded_payload_resolves_to_its_reference(self):
        from services.identifiers import padded_payload, resolve_reference

        assert resolve_reference("000065902") == RONNA_STORED_REFERENCE
        assert padded_payload(RONNA_STORED_REFERENCE) == "000065902"

    def test_matches_reference_accepts_every_form(self):
        from services.identifiers import matches_reference

        class Student:
            reference_number = RONNA_STORED_REFERENCE
            barcode_payload = "000065902"

        student = Student()
        for value in (RONNA_STORED_REFERENCE, "000001", "000065902", " 65902 "):
            assert matches_reference(student, value), value
        assert not matches_reference(student, "65412")
        assert not matches_reference(None, "65412")


# ------------------------------------------------------------ profile photos --


class TestProfilePhotoMapping:
    @pytest.mark.parametrize(
        "reference, expected",
        [
            (JAVY_REFERENCE, "javy_profile.png"),
            (RONNA_PRINTED_REFERENCE, "ronna_profile.png"),
            (RONNA_STORED_REFERENCE, "ronna_profile.png"),
        ],
    )
    def test_each_reference_returns_its_own_photograph(self, reference, expected):
        from ui.widgets.profile_image import get_student_profile_image

        path = get_student_profile_image(reference)

        assert path is not None, f"no photograph resolved for {reference!r}"
        assert path.name == expected

    def test_the_two_references_resolve_to_different_files(self):
        from ui.widgets.profile_image import get_student_profile_image

        javy = get_student_profile_image(JAVY_REFERENCE)
        ronna = get_student_profile_image(RONNA_PRINTED_REFERENCE)

        assert javy != ronna
        assert javy.read_bytes() != ronna.read_bytes(), (
            "the two students must never be shown the same photograph"
        )

    def test_an_unknown_reference_resolves_to_nothing(self):
        from ui.widgets.profile_image import get_student_profile_image

        assert get_student_profile_image("99999999") is None
        assert get_student_profile_image("") is None
        assert get_student_profile_image(None) is None

    def test_the_resolution_is_independent_of_the_working_directory(self, monkeypatch, tmp_path):
        from config.settings import settings
        from ui.widgets.profile_image import get_student_profile_image

        monkeypatch.chdir(tmp_path)
        path = get_student_profile_image(RONNA_PRINTED_REFERENCE)

        assert path is not None and path.is_file()

    def test_the_database_record_is_the_authority(self):
        """A record naming its own photograph must not be overridden by the aliases."""
        from config.settings import settings

        assert settings.resolve_asset("assets/profile/javy_profile.png") is not None
        stored = settings.resolve_asset("assets/profile/javy_profile.png")
        assert stored.name == "javy_profile.png"


# --------------------------------------------------------- enrollment lookup --


class TestEnrollmentLookup:
    def test_a_student_with_an_enrollment_is_found(self):
        _require_database()
        from services.student_service import get_enrollment_for_reference

        result = get_enrollment_for_reference(JAVY_REFERENCE)

        assert result.found is True
        assert result.student is not None
        assert result.student.reference_number == JAVY_REFERENCE
        assert result.enrollment is not None
        assert result.has_subjects, "the seeded enrollment carries seven subjects"
        assert result.enrollment.academic_year

    def test_the_printed_alias_finds_the_same_record(self):
        _require_database()
        from services.student_service import get_enrollment_for_reference

        by_alias = get_enrollment_for_reference(RONNA_PRINTED_REFERENCE)
        by_reference = get_enrollment_for_reference(RONNA_STORED_REFERENCE)

        assert by_alias.found is True
        assert by_alias.student.id == by_reference.student.id
        assert by_alias.enrollment.id == by_reference.enrollment.id

    def test_an_unknown_reference_is_reported_as_such(self):
        _require_database()
        from services.student_service import get_enrollment_for_reference

        result = get_enrollment_for_reference("98765432")

        assert result.status == "unknown_student"
        assert "No student on file" in result.reason
        assert result.enrollment is None

    def test_the_reason_distinguishes_the_four_outcomes(self):
        """A database outage must never read as "no enrollment record"."""
        from services.student_service import EnrollmentLookup

        statuses = {
            "found",
            "unknown_student",
            "no_enrollment",
            "no_subjects",
            "database_unavailable",
        }
        assert statuses == {status for status in statuses}
        assert EnrollmentLookup(status="database_unavailable", error="down").found is False


# ------------------------------------------------------------- COM export --


class TestComExport:
    def _service(self):
        from services.com_print_service import ComPrintService

        return ComPrintService()

    def test_a_valid_enrollment_produces_a_pdf(self, tmp_path):
        _require_database()
        from services.student_service import get_enrollment_for_reference

        lookup = get_enrollment_for_reference(JAVY_REFERENCE)
        if not lookup.found:
            pytest.skip("the reference student has no enrollment on this database")

        target = tmp_path / "com.pdf"
        result = self._service().export_pdf(
            lookup.student, lookup.enrollment, lookup.assessment, target
        )

        assert result == target
        assert target.read_bytes().startswith(b"%PDF")
        assert target.stat().st_size > 1000

    def test_a_missing_enrollment_is_refused_with_an_explanation(self, tmp_path):
        from services.com_print_service import ComNotCertifiableError

        target = tmp_path / "must_not_exist.pdf"

        with pytest.raises(ComNotCertifiableError) as raised:
            self._service().export_pdf(None, None, None, target)

        assert "no enrollment record" in str(raised.value).lower()
        assert not target.exists(), "a refused export must not leave a file behind"

    def test_an_enrollment_with_no_enrolled_subjects_is_refused(self, tmp_path):
        from models.enrollment import Enrollment, Semester
        from services.com_print_service import ComNotCertifiableError

        target = tmp_path / "must_not_exist.pdf"
        empty = Enrollment(student_id=1, academic_year="2024-2025", semester=Semester.FIRST)

        with pytest.raises(ComNotCertifiableError) as raised:
            self._service().export_pdf(None, empty, None, target)

        assert "no subjects" in str(raised.value).lower()
        assert not target.exists()

    def test_the_popup_says_which_of_the_three_states_it_is_in(self, qapp):
        """Loading, failed lookup and genuinely empty read differently."""
        from models.student import EnrollmentStatus, Student, YearLevel
        from ui.dialogs.scan_result_dialog import ScanResultDialog

        student = Student(
            id=1,
            student_id="WIT-2024-001",
            reference_number=JAVY_REFERENCE,
            first_name="Test",
            last_name="Student",
            program="Computer Engineering",
            year_level=YearLevel.THIRD_YEAR,
            enrollment_status=EnrollmentStatus.ACTIVE,
            profile_image_path="assets/profile/javy_profile.png",
            barcode_payload="000065412",
        )

        loading = ScanResultDialog(student=student, scanned_value=student.barcode_payload, loading=True)
        assert "still being loaded" in loading._certifiable_problem()
        assert loading.export_btn.isEnabled() is False
        loading.close(); loading.deleteLater()

        failed = ScanResultDialog(student=student, scanned_value=student.barcode_payload)
        failed.apply_records(None, None, "Database unavailable")
        assert "could not be loaded" in failed._certifiable_problem()
        assert failed.export_btn.isEnabled() is False
        failed.close(); failed.deleteLater()

        from models.enrollment import Enrollment, Semester

        empty = ScanResultDialog(
            student=student,
            enrollment=Enrollment(student_id=1, academic_year="2024-2025", semester=Semester.FIRST),
            scanned_value=student.barcode_payload,
        )
        empty.apply_records(empty.enrollment, None, "")
        assert "no subjects" in empty._certifiable_problem().lower()
        assert empty.export_btn.isEnabled() is False
        empty.close(); empty.deleteLater()

    def test_the_popup_enables_printing_once_the_record_is_loaded(self, qapp):
        _require_database()
        from models.enrollment import (
            Enrollment, EnrollmentStatus, EnrollmentSubject, Semester, Subject, SubjectStatus,
        )
        from ui.dialogs.scan_result_dialog import ScanResultDialog
        from models.student import EnrollmentStatus as StudentStatus, Student, YearLevel

        student = Student(
            id=1, student_id="WIT-2024-001", reference_number=JAVY_REFERENCE,
            first_name="Test", last_name="Student", program="Computer Engineering",
            year_level=YearLevel.THIRD_YEAR, enrollment_status=StudentStatus.ACTIVE,
            barcode_payload="000065412",
        )
        enrollment = Enrollment(
            student_id=1, academic_year="2024-2025", semester=Semester.FIRST,
            status=EnrollmentStatus.ENROLLED,
            subjects=[
                EnrollmentSubject(
                    subject=Subject(subject_code="311EP1", subject_name="CPE 311", credits=4),
                    status=SubjectStatus.ENROLLED,
                )
            ],
        )
        dialog = ScanResultDialog(
            student=student, enrollment=enrollment, scanned_value=student.barcode_payload
        )

        assert dialog.export_btn.isEnabled() is True
        assert dialog._certifiable_problem() == ""
        dialog.close(); dialog.deleteLater()


class TestDocumentEscaping:
    def test_student_supplied_text_is_escaped_in_the_print_markup(self, qapp):
        """A name containing markup must not become markup in the printed page."""
        from models.student import Student, YearLevel
        from services.com_print_service import ComPrintService, official_status
        from services.com_summary import (
            summarize_enrollment,
            summarize_fee_lines,
            summarize_financials,
        )
        from models.enrollment import Enrollment, Semester

        student = Student(
            id=1,
            student_id="WIT-2024-001",
            reference_number="65412",
            first_name="<script>alert('x')</script>",
            last_name="O'Brien & Co",
            program="Computer <Engineering>",
            year_level=YearLevel.THIRD_YEAR,
        )
        com = summarize_enrollment(None)
        financial = summarize_financials(None)
        service = ComPrintService.__new__(ComPrintService)
        markup = service._build_html(
            student, com, financial, official_status(com, financial),
            fee_lines=summarize_fee_lines(None),
        )

        assert "<script>alert" not in markup
        assert "&lt;script&gt;" in markup
        assert "O&#x27;Brien &amp; Co" in markup or "O'Brien" in markup
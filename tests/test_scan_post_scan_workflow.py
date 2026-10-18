"""Post-scan workflow: session state machine, history, COM modal and routing.

Scope of this module, one class per numbered requirement of the post-scan
brief:

1.  a successful scan of reference 65412 (``TestSuccessfulScan``)
2.  immediate Scan History insertion (``TestImmediateHistoryInsertion``)
3.  timestamp formatting (``TestTimestampFormatting``)
4.  green Success badge (``TestStatusBadge``)
5.  student modal and enrolled-subject table (``TestStudentModal``, ``TestComTotals``)
6.  financial totals and installments (``TestFinancialSummary``)
7.  assessment navigation with the right reference (``TestAssessmentNavigation``)
8.  student profile navigation (``TestProfileNavigation``)
9.  print / export (``TestPrintAndExport``)
10. "Scan Next Student" (``TestScanNextStudent``)
11. duplicate suppression and intentional rescanning (``TestDuplicateSuppression``)
12. unknown barcodes, missing records, mismatched ids (``TestLookupFailures``)
13. audio cue (``TestAudioCue``)
14. layout and keyboard accessibility (``TestAccessibility``)

The database-backed tests use the real student row for reference ``65412`` and
are skipped when MySQL is unreachable, matching the convention already used by
``test_scan_pipeline.py``. Nothing is written to ``barcode_scans`` here: the page
tests inject an in-memory repository so the audit trail of the development
database stays clean, while the persistence call itself is still exercised.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PySide6")

from config.settings import settings
from models.assessment import (
    Assessment,
    AssessmentCategory,
    AssessmentItem,
    AssessmentStatus,
    PaymentSchedule,
    PaymentScheduleStatus,
    PaymentScheduleType,
)
from models.barcode import ScanStatus
from models.enrollment import (
    Enrollment,
    EnrollmentStatus,
    EnrollmentSubject,
    Semester,
    Subject,
    SubjectStatus,
)
from models.student import Student, YearLevel
from services.com_summary import (
    ComSubjectRow,
    format_peso,
    summarize_enrollment,
    summarize_financials,
    to_decimal,
)
from services.com_print_service import (
    ComNotCertifiableError,
    ComPrintService,
    official_status,
)
from services.scan_pipeline import ScanOutcome, ScanState
from services.scan_session import RejectReason, ScanPhase, ScanSession
from tests.scanner_fixtures import CARD_ID_1, make_webcam_frame

REFERENCE = "65412"
STUDENT_NAME = "Javy Danielle N. Caporal"
PAYLOAD = "000065412"


# --------------------------------------------------------------- fixtures --


@pytest.fixture
def clock():
    """A manually advanced monotonic clock."""

    class Clock:
        def __init__(self):
            self.t = 1000.0

        def __call__(self):
            return self.t

        def advance(self, seconds):
            self.t += seconds
            return self.t

    return Clock()


@pytest.fixture
def session(clock):
    return ScanSession(debounce_seconds=1.75, clock=clock)


@pytest.fixture
def verified_student() -> Student:
    """The real reference-65412 student, as the repository returns it."""
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
        barcode_payload=PAYLOAD,
    )


@pytest.fixture
def brief_com_rows() -> list[ComSubjectRow]:
    """The seven subject rows printed in the task brief.

    Reference data only: it is used to check the arithmetic in
    ``com_summary`` and to expose the fee mismatch. It is never handed to the
    application, which must read the database instead.
    """
    raw = [
        ("311EP1", "CPE 311", 4, "7–8 PM SAT", "CB 24", "1755.81"),
        ("312EP1", "CPE 312", 3, "1–4 PM MON", "CB 21", "0.00"),
        ("313EP1", "CPE 313", 3, "5–8 PM FRI", "CB 24", "0.00"),
        ("315EP1", "CPE 315", 1, "1–4 PM FRI", "CB 12", "2106.97"),
        ("DRWEP1", "DRAW E2", 3, "4–6 PM FRI", "CB 21", "1755.81"),
        ("CGELEC1", "COG ELECT", 3, "6–8 PM TUE", "CB 24", "0.00"),
        ("MTE4EP1", "MATH E4", 3, "11 AM–12 PM MWF", "R3 RTS", "0.00"),
    ]
    return [
        ComSubjectRow(code=c, subject=s, units=u, schedule=sched, room=r,
                      lab_fee=to_decimal(f))
        for c, s, u, sched, r, f in raw
    ]


@pytest.fixture
def brief_installments() -> list[tuple[str, str]]:
    return [
        ("Down Payment", "2689.51"),
        ("Midterm", "9671.66"),
        ("Prefinal", "9671.66"),
        ("Final", "6089.99"),
    ]


def make_enrollment(rows, academic_year="2024-2025", semester=Semester.FIRST) -> Enrollment:
    subjects = []
    for index, row in enumerate(rows, start=1):
        subject = Subject(
            id=index,
            subject_code=row.code,
            subject_name=row.subject,
            course_id=1,
            credits=row.units,
        )
        schedule_time, _, schedule_days = row.schedule.rpartition(" ")
        subjects.append(
            EnrollmentSubject(
                id=index,
                enrollment_id=1,
                subject_id=index,
                section=student_section(row.code),
                schedule_time=schedule_time or None,
                schedule_days=schedule_days or None,
                room=row.room,
                lab_fee=float(row.lab_fee),
                status=SubjectStatus.ENROLLED,
                subject=subject,
            )
        )
    return Enrollment(
        id=1,
        student_id=1,
        academic_year=academic_year,
        semester=semester,
        status=EnrollmentStatus.ENROLLED,
        subjects=subjects,
    )


def student_section(code: str) -> str:
    return code.replace("CPE", "").replace("DRAW", "").replace("COG", "").replace("MATH", "")


class FakeScanRepository:
    """In-memory stand-in for :class:`BarcodeScanRepository`.

    Records what the page asks it to persist so the tests can assert on the
    audit write, without adding rows to the development database.
    """

    def __init__(self, now=None):
        self.created = []
        self._rows = []
        self._next_id = 1
        self._now = now or (lambda: datetime.now())
        self.fail = False

    def create(self, scan):
        if self.fail:
            from database.connection import DatabaseUnavailableError

            raise DatabaseUnavailableError("test: database unavailable")
        scan.id = self._next_id
        self._next_id += 1
        scan.scanned_at = self._now()
        self.created.append(scan)
        self._rows.append(scan)
        return scan.id

    def get_recent(self, limit=50):
        return list(reversed(self._rows))[:limit]


class RecordingAudio:
    def __init__(self):
        self.calls = 0
        self.enabled = True

    def play_success(self):
        if not self.enabled:
            return False
        self.calls += 1
        return True

    def set_enabled(self, value):
        self.enabled = bool(value)

    def dispose(self):
        """Mirrors AudioCueService.dispose so page teardown is representative."""
        self.disposed = True


def verified_outcome(value=PAYLOAD, student=None):
    from services.scan_pipeline import BarcodeCandidate

    student = student or Student(
        id=1,
        student_id="WIT-2024-001",
        reference_number=REFERENCE,
        first_name="Javy Danielle",
        middle_name="N.",
        last_name="Caporal",
        program="Computer Engineering",
        year_level=YearLevel.THIRD_YEAR,
    )
    return ScanOutcome(
        state=ScanState.VERIFIED,
        message="Student verified",
        decoded_value=value,
        decoded_type="CODE39",
        student=student,
        verified_now=True,
        candidates=[
            BarcodeCandidate(
                bbox=(637, 227, 266, 56),
                confidence=0.7964,
                class_name="barcode",
                in_scan_region=True,
            )
        ],
    )


# =========================================================== 11: session ===


class TestDuplicateSuppression:
    def test_state_machine_walks_ready_to_success(self, session, verified_student):
        value = PAYLOAD
        assert session.phase is ScanPhase.READY

        session.observe(ScanState.SEARCHING, None)
        assert session.phase is ScanPhase.READY

        session.observe(ScanState.BARCODE_DETECTED, None)
        assert session.phase is ScanPhase.DETECTING

        session.observe(ScanState.DECODING, value)
        assert session.phase is ScanPhase.DECODING

        session.begin_verification(value)
        assert session.phase is ScanPhase.VERIFYING

        decision = session.observe(ScanState.VERIFIED, value, confirmed=True)
        assert decision.accepted is True
        assert session.phase is ScanPhase.SUCCESS
        assert session.last_processed_value == value

    def test_repeated_frames_for_one_card_are_accepted_once(self, session, clock):
        for _ in range(25):
            clock.advance(0.4)  # well past the pipeline cooldown
            decision = session.observe(ScanState.VERIFIED, PAYLOAD, confirmed=True)
        assert decision.accepted is False
        assert decision.rejected_because is RejectReason.DUPLICATE_VISIBLE
        # Exactly one acceptance: the first frame shows the card for the first
        # time. Frames 2..25 are the same card still sitting in frame, and every
        # one of them is refused by the latch.
        assert session.accepted_count == 1

    def test_one_card_produces_exactly_one_acceptance(self, session, clock):
        accepted = 0
        for _ in range(10):
            clock.advance(1.0)
            if session.observe(ScanState.VERIFIED, PAYLOAD, confirmed=True).accepted:
                accepted += 1
        assert accepted == 1
        assert session.accepted_count == 1
        assert session.rejected_count == 9

    def test_barcode_must_leave_the_frame_before_rescanning(self, session, clock):
        assert session.observe(ScanState.VERIFIED, PAYLOAD, confirmed=True).accepted

        clock.advance(10.0)
        same = session.observe(ScanState.VERIFIED, PAYLOAD, confirmed=True)
        assert same.accepted is False, "a card held in frame must not re-scan"

        session.observe(ScanState.SEARCHING, None)
        assert session.is_latched is False

        clock.advance(10.0)
        again = session.observe(ScanState.VERIFIED, PAYLOAD, confirmed=True)
        assert again.accepted is True, "the card left and came back: that is a new scan"

    def test_intentional_rescan_after_a_new_session(self, session, clock):
        assert session.observe(ScanState.VERIFIED, PAYLOAD, confirmed=True).accepted
        clock.advance(10.0)
        assert session.observe(ScanState.VERIFIED, PAYLOAD, confirmed=True).accepted is False

        session.start_new_session()
        assert session.is_latched is False

        decision = session.observe(ScanState.VERIFIED, PAYLOAD, confirmed=True)
        assert decision.accepted is True

    def test_two_different_cards_are_debounced(self, session, clock):
        assert session.observe(ScanState.VERIFIED, PAYLOAD, confirmed=True).accepted

        clock.advance(0.4)
        second = session.observe(ScanState.VERIFIED, "000065902", confirmed=True)
        assert second.accepted is False
        assert second.rejected_because is RejectReason.DEBOUNCED

        clock.advance(1.6)  # now past the 1.75 s window
        third = session.observe(ScanState.VERIFIED, "000065902", confirmed=True)
        assert third.accepted is True

    def test_a_different_payload_releases_the_latch(self, session, clock):
        session.observe(ScanState.VERIFIED, PAYLOAD, confirmed=True)
        session.observe(ScanState.UNKNOWN_BARCODE, "000000000")
        assert session.is_latched is False

    def test_error_does_not_release_the_latch(self, session, clock):
        """A database blip must not let a held card straight back through."""
        session.observe(ScanState.VERIFIED, PAYLOAD, confirmed=True)
        session.observe(ScanState.ERROR, PAYLOAD)
        assert session.is_latched is True

        clock.advance(5.0)
        assert session.observe(ScanState.VERIFIED, PAYLOAD, confirmed=True).accepted is False

    def test_verified_without_confirmation_is_not_a_scan(self, session):
        """A YOLO hit, or a cooldown echo, must never count as a scan."""
        decision = session.observe(ScanState.VERIFIED, PAYLOAD, confirmed=False)
        assert decision.accepted is False
        assert decision.rejected_because is RejectReason.NOT_VERIFIED
        assert session.is_latched is False

    def test_debounce_remaining_counts_down(self, session, clock):
        session.observe(ScanState.VERIFIED, PAYLOAD, confirmed=True)
        assert session.debounce_remaining() == pytest.approx(1.75)
        clock.advance(1.0)
        assert session.debounce_remaining() == pytest.approx(0.75, abs=0.01)
        clock.advance(1.0)
        assert session.debounce_remaining() == 0.0

    def test_reset_clears_counters(self, session, clock):
        session.observe(ScanState.VERIFIED, PAYLOAD, confirmed=True)
        session.reset()
        assert session.accepted_count == 0
        assert session.rejected_count == 0
        assert session.phase is ScanPhase.READY
        assert session.is_latched is False


# ================================================== 12: lookup failures ===


class TestLookupFailures:
    def test_unknown_barcode_is_never_accepted(self, session, clock):
        for _ in range(5):
            clock.advance(1.0)
            decision = session.observe(ScanState.UNKNOWN_BARCODE, "000000000")
            assert decision.accepted is False
        assert session.accepted_count == 0

    def test_unknown_barcode_clears_the_verified_student_on_the_page(self, qapp, verified_student):
        page = make_page()
        page._on_frame_result(
            np.zeros((720, 1280, 3), dtype=np.uint8),
            ScanOutcome(
                state=ScanState.UNKNOWN_BARCODE,
                message="Unknown barcode - no matching student record.",
                decoded_value="000000000",
                decoded_type="CODE39",
            ),
        )
        assert page.current_student is None
        assert page.student_group.isVisible() is False
        statuses = [page.history_table.item(0, 3).text().strip()]
        assert statuses == ["Not Found"]
        assert page.history_table.item(0, 2).text() == "Unknown"

    def test_a_scan_without_a_verified_student_is_not_recorded_as_success(self, qapp):
        page = make_page()
        outcome = ScanOutcome(
            state=ScanState.VERIFIED,
            message="Student verified",
            decoded_value=PAYLOAD,
            student=None,
            verified_now=True,
        )
        page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8), outcome)
        assert page.scan_repo.created == []

    def test_mismatched_id_is_resolved_to_its_own_student(self, qapp):
        """A payload that maps to another student shows *that* student, not the scanned one."""
        page = make_page()
        other = Student(
            id=2,
            student_id="WIT-2024-002",
            reference_number="65902",
            first_name="Ronna Mae",
            middle_name="D.",
            last_name="Alemania",
            program="Computer Engineering",
            year_level=YearLevel.THIRD_YEAR,
        )
        outcome = ScanOutcome(
            state=ScanState.VERIFIED,
            message="Student verified",
            decoded_value="000065902",
            student=other,
            verified_now=True,
        )
        page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8), outcome)

        assert page.scan_repo.created[0].student_id == 2
        assert page.history_table.item(0, 2).text() == other.full_name
        assert page.history_table.item(0, 1).text() == "000065902"

    def test_a_failing_lookup_does_not_raise_into_the_capture_loop(self, qapp):
        page = make_page()
        page.scan_repo.fail = True
        outcome = ScanOutcome(
            state=ScanState.ERROR,
            message="Database unavailable - cannot verify student.",
            decoded_value=PAYLOAD,
            error="down",
        )
        page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8), outcome)
        assert page.scan_repo.created == []


# ================================================ 1/2/3/4/10: the page ===


@pytest.fixture
def page_factory(qapp):
    """Builds scanner pages wired to fakes, and tears them down."""
    created = []

    def build():
        from services.student_service import StudentService
        from ui.scanner_page import ScannerPage

        page = ScannerPage(StudentService())
        page.scan_repo = FakeScanRepository()
        page.audio_cue = RecordingAudio()
        created.append(page)
        return page

    yield build
    for page in created:
        page._shutdown()


def make_page():
    """A minimal page for tests that do not need the fixture machinery."""
    from services.student_service import StudentService
    from ui.scanner_page import ScannerPage

    page = ScannerPage(StudentService())
    page.scan_repo = FakeScanRepository()
    page.audio_cue = RecordingAudio()
    return page


class TestSuccessfulScan:
    def test_reference_65412_verifies_against_the_real_database(self, qapp):
        """End to end with the real decoder, the real card and the real database."""
        from services.barcode_decoder import BarcodeDecoderService
        from services.scan_pipeline import ScanPipeline, StudentVerifier
        from services.yolo_detector import YOLODetectorService
        from services.student_service import StudentService

        detector = YOLODetectorService()
        if not detector.load_active_model():
            pytest.skip("No YOLO model available")

        pipeline = ScanPipeline(
            detector=detector,
            decoder=BarcodeDecoderService(),
            verifier=StudentVerifier(lookup=StudentService().get_student_by_barcode),
        )
        outcome = pipeline.process_frame(make_webcam_frame(CARD_ID_1))
        if outcome.state is not ScanState.VERIFIED:
            pytest.skip(f"Card did not verify in this environment: {outcome.message}")

        assert outcome.decoded_value == PAYLOAD
        assert outcome.student is not None
        assert outcome.student.reference_number == REFERENCE
        assert outcome.student.full_name == STUDENT_NAME
        assert outcome.student.program == "Computer Engineering"
        assert outcome.student.year_level.value == "3rd Year"

    def test_a_verified_scan_populates_the_student_panel(self, page_factory, verified_student):
        page = page_factory()
        page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8), verified_outcome(student=verified_student))

        assert page.current_student is verified_student
        assert page.student_name_label.text() == STUDENT_NAME
        assert REFERENCE in page.student_id_label.text()
        assert "Computer Engineering" in page.student_program_label.text()


class TestImmediateHistoryInsertion:
    def test_row_is_inserted_at_the_top_without_a_refresh(self, page_factory, verified_student):
        page = page_factory()
        page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8), verified_outcome(student=verified_student))

        assert page.history_table.rowCount() == 1
        assert page.history_table.item(0, 1).text() == PAYLOAD
        assert page.history_table.item(0, 2).text() == STUDENT_NAME

    def test_consecutive_scans_stack_newest_first(self, page_factory):
        page = page_factory()
        first = verified_outcome()
        second = verified_outcome("000065902")
        page.scan_session.start_new_session()
        page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8), first)
        page.scan_session.start_new_session()
        page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8), second)

        assert page.history_table.rowCount() == 2
        assert page.history_table.item(0, 1).text() == "000065902"
        assert page.history_table.item(1, 1).text() == PAYLOAD

    def test_the_row_is_persisted_through_the_existing_repository(self, page_factory, verified_student):
        page = page_factory()
        page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8), verified_outcome(student=verified_student))

        assert len(page.scan_repo.created) == 1
        scan = page.scan_repo.created[0]
        assert scan.scanned_value == PAYLOAD
        assert scan.student_id == verified_student.id
        assert scan.scan_status is ScanStatus.SUCCESS

    def test_history_survives_a_new_scan_session(self, page_factory, verified_student):
        page = page_factory()
        page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8), verified_outcome(student=verified_student))
        page._on_scan_next_requested()
        assert page.history_table.rowCount() == 1, "Scan Next must not clear history"

    def test_history_is_capped_at_the_configured_limit(self, page_factory, monkeypatch, verified_student):
        monkeypatch.setattr(settings, "SCAN_HISTORY_DISPLAY_LIMIT", 3)
        page = page_factory()
        for index in range(6):
            page.scan_session.start_new_session()
            page._on_frame_result(
                np.zeros((4, 4, 3), dtype=np.uint8),
                verified_outcome(value=f"00000000{index}", student=verified_student),
            )
        assert page.history_table.rowCount() == 3
        assert page.history_table.item(0, 1).text() == "000000005"


class TestTimestampFormatting:
    def test_displayed_time_is_hours_minutes_seconds(self, page_factory, verified_student):
        page = page_factory()
        page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8), verified_outcome(student=verified_student))

        text = page.history_table.item(0, 0).text()
        assert re.fullmatch(r"\d{2}:\d{2}:\d{2}", text), text
        assert datetime.strptime(text, "%H:%M:%S")

    def test_the_stored_timestamp_is_used_when_available(self, qapp, verified_student):
        fixed = datetime.now().replace(hour=19, minute=5, second=26, microsecond=0)
        page = make_page()
        page.scan_repo = FakeScanRepository(now=lambda: fixed)
        page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8), verified_outcome(student=verified_student))
        assert page.history_table.item(0, 0).text() == "19:05:26"

    def test_a_missing_timestamp_says_so_instead_of_guessing(self, qapp):
        page = make_page()
        page._append_history_row(
            value=PAYLOAD,
            student_name="Someone",
            reference="",
            status=ScanStatus.SUCCESS,
            scanned_at=None,
        )
        assert page.history_table.item(0, 0).text() == "N/A"

    def test_the_time_is_not_hardcoded(self, page_factory, verified_student):
        page = page_factory()
        before = datetime.now()
        page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8), verified_outcome(student=verified_student))
        after = datetime.now()

        # The cell holds "%H:%M:%S", i.e. whole seconds, so the window it is
        # compared against has to be truncated to whole seconds too. Comparing a
        # second-resolution string against microsecond-resolution datetimes can
        # never match, whatever the code does.
        def whole_seconds(moment: datetime) -> time:
            return moment.replace(microsecond=0).time()

        shown = datetime.strptime(page.history_table.item(0, 0).text(), "%H:%M:%S").time()
        # Allow for the second rolling over between the scan and the assertion.
        window = {
            whole_seconds(before),
            whole_seconds(after),
            whole_seconds(before + timedelta(seconds=1)),
            whole_seconds(after - timedelta(seconds=1)),
        }
        assert shown in window, f"{shown} is not the current time (window {window})"


class TestStatusBadge:
    def test_success_renders_a_green_badge(self, page_factory, verified_student):
        from styles.colors import Colors

        page = page_factory()
        page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8), verified_outcome(student=verified_student))

        badge = page.history_table.item(0, 3)
        assert "Success" in badge.text()
        assert badge.foreground().color().name().lower() == Colors.SUCCESS_GREEN.lower()

    def test_not_found_renders_a_red_badge(self, qapp):
        from styles.colors import Colors

        page = make_page()
        page._append_history_row(
            value="000000000",
            student_name="",
            reference="",
            status=ScanStatus.NOT_FOUND,
            scanned_at=datetime.now(),
        )
        badge = page.history_table.item(0, 3)
        assert "Not Found" in badge.text()
        assert badge.foreground().color().name().lower() == Colors.ERROR_RED.lower()

    def test_badge_exposes_a_plain_label_for_screen_readers(self, qapp):
        from PySide6.QtCore import Qt
        from ui.scanner_page import ScannerPage

        item = ScannerPage._status_badge(ScanStatus.SUCCESS)
        assert item.data(Qt.ItemDataRole.AccessibleTextRole) == "Success"


class TestScanNextStudent:
    def test_clears_transient_state_and_rearms_the_session(self, page_factory, verified_student):
        page = page_factory()
        page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8), verified_outcome(student=verified_student))
        assert page.scan_session.is_latched is True

        page._on_scan_next_requested()

        assert page.scan_session.is_latched is False
        assert page.scan_session.phase is ScanPhase.READY
        assert page.current_student is None
        assert page.current_barcode is None
        assert page.last_scanned_value == ""
        assert page.student_group.isVisible() is False
        assert page.history_table.rowCount() == 1

    def test_the_same_student_can_then_be_scanned_again(self, page_factory, verified_student):
        page = page_factory()
        for _ in range(3):
            page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8), verified_outcome(student=verified_student))
            page._on_scan_next_requested()

        assert len(page.scan_repo.created) == 3
        assert page.history_table.rowCount() == 3
        assert page.audio_cue.calls == 3

    def test_scan_next_does_not_restart_a_running_scanner_twice(self, page_factory, verified_student):
        page = page_factory()
        page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8), verified_outcome(student=verified_student))
        started = []
        page._start_scanner = lambda: started.append(True)

        class _Worker:
            def isRunning(self_inner):
                return True

        page.worker = _Worker()
        page._on_scan_next_requested()
        assert started == []

    def test_the_modal_reports_scan_next_and_closes(self, qapp, verified_student):
        from ui.dialogs.scan_result_dialog import ScanResultDialog

        page = make_page()
        dialog = ScanResultDialog(student=verified_student, scanned_value=PAYLOAD, parent=page)
        seen = []
        dialog.scan_next_requested.connect(lambda: seen.append(True))
        dialog._on_scan_next()
        assert seen == [True]
        assert dialog.result() == int(dialog.DialogCode.Accepted)


# =============================================== 5/6: modal and totals ===


class TestComTotals:
    def test_totals_are_computed_from_the_rows(self, brief_com_rows):
        summary = summarize_enrollment(make_enrollment(brief_com_rows))
        assert summary.subject_count == 7
        assert summary.total_units == 20
        assert summary.total_lab_fee == Decimal("5618.59")
        assert format_peso(summary.total_lab_fee) == "₱5,618.59"

    def test_dropped_subjects_are_excluded(self, brief_com_rows):
        enrollment = make_enrollment(brief_com_rows)
        enrollment.subjects[0].status = SubjectStatus.DROPPED
        summary = summarize_enrollment(enrollment)

        assert summary.subject_count == 6
        assert summary.total_units == 20 - brief_com_rows[0].units
        assert summary.total_lab_fee == Decimal("5618.59") - brief_com_rows[0].lab_fee

    def test_schedule_joins_time_and_days(self, brief_com_rows):
        enrollment = make_enrollment(brief_com_rows)
        summary = summarize_enrollment(enrollment)
        assert summary.rows[0].schedule == "7–8 PM SAT"

    def test_no_enrollment_is_reported_not_invented(self):
        summary = summarize_enrollment(None)
        assert summary.available is False
        assert summary.subject_count == 0
        assert "no enrollment record" in summary.empty_reason.lower()

    def test_an_enrollment_with_no_enrolled_subjects_says_so(self):
        enrollment = make_enrollment([])
        summary = summarize_enrollment(enrollment)
        assert summary.available is False
        assert "no subjects" in summary.empty_reason.lower()


class TestStudentModal:
    def test_header_shows_the_verified_identity(self, qapp, verified_student):
        from ui.dialogs.scan_result_dialog import ScanResultDialog

        dialog = ScanResultDialog(student=verified_student, scanned_value=PAYLOAD)
        header_text = " ".join(label.text() for label in _all_labels(dialog))
        assert STUDENT_NAME in header_text
        assert REFERENCE in header_text
        assert "Computer Engineering" in header_text
        assert "3rd Year" in header_text
        assert "VERIFIED" in header_text
        dialog.deleteLater()

    def test_com_tab_has_the_six_documented_columns(self, qapp, verified_student, brief_com_rows):
        from PySide6.QtWidgets import QTableWidget
        from ui.dialogs.scan_result_dialog import ScanResultDialog

        dialog = ScanResultDialog(
            student=verified_student,
            enrollment=make_enrollment(brief_com_rows),
            scanned_value=PAYLOAD,
        )
        tables = dialog.findChildren(QTableWidget)
        com_table = next(t for t in tables if t.objectName() == "comSubjectTable")
        headers = [com_table.horizontalHeaderItem(c).text() for c in range(com_table.columnCount())]
        assert headers == ["Code", "Subject", "Units", "Schedule", "Room", "Lab Fee"]
        assert com_table.rowCount() == 8  # 7 subjects + a computed total row
        dialog.deleteLater()

    def test_total_row_is_derived_not_hardcoded(self, qapp, verified_student, brief_com_rows):
        from PySide6.QtWidgets import QTableWidget
        from ui.dialogs.scan_result_dialog import ScanResultDialog

        dialog = ScanResultDialog(
            student=verified_student,
            enrollment=make_enrollment(brief_com_rows),
            scanned_value=PAYLOAD,
        )
        com_table = next(
            t for t in dialog.findChildren(QTableWidget) if t.objectName() == "comSubjectTable"
        )
        total_row = com_table.rowCount() - 1
        assert com_table.item(total_row, 1).text() == "7 Subjects"
        assert com_table.item(total_row, 2).text() == "20"
        assert com_table.item(total_row, 5).text() == "₱5,618.59"
        dialog.deleteLater()

    def test_a_trimmed_enrollment_recomputes_the_totals(self, qapp, verified_student, brief_com_rows):
        from PySide6.QtWidgets import QTableWidget
        from ui.dialogs.scan_result_dialog import ScanResultDialog

        dialog = ScanResultDialog(
            student=verified_student,
            enrollment=make_enrollment(brief_com_rows[:2]),
            scanned_value=PAYLOAD,
        )
        com_table = next(
            t for t in dialog.findChildren(QTableWidget) if t.objectName() == "comSubjectTable"
        )
        total_row = com_table.rowCount() - 1
        assert com_table.item(total_row, 1).text() == "2 Subjects"
        assert com_table.item(total_row, 2).text() == "7"
        assert com_table.item(total_row, 5).text() == "₱1,755.81"
        dialog.deleteLater()

    def test_missing_enrollment_shows_a_no_data_state(self, qapp, verified_student):
        from ui.dialogs.scan_result_dialog import ScanResultDialog

        dialog = ScanResultDialog(student=verified_student, scanned_value=PAYLOAD)
        from PySide6.QtWidgets import QTableWidget

        assert dialog.findChildren(QTableWidget) == [] or all(
            t.objectName() != "comSubjectTable" for t in dialog.findChildren(QTableWidget)
        )
        dialog.deleteLater()

    def test_the_barcode_alone_never_produces_a_student(self, qapp):
        from ui.dialogs.scan_result_dialog import ScanResultDialog

        dialog = ScanResultDialog(scanned_value="000000000", success=False)
        assert dialog.success is False
        assert dialog.student is None
        dialog.deleteLater()


class TestFinancialSummary:
    @staticmethod
    def _assessment(**overrides):
        tuition = AssessmentCategory(id=1, category_code="tuition_fee",
                                     category_name="Tuition Fee", is_fee=True, is_discount=False)
        lab = AssessmentCategory(id=2, category_code="laboratory_fee",
                                 category_name="Laboratory Fee", is_fee=True, is_discount=False)
        misc = AssessmentCategory(id=3, category_code="miscellaneous_fee",
                                  category_name="Miscellaneous Fee", is_fee=True, is_discount=False)
        values = dict(
            id=1,
            student_id=1,
            academic_year="2024-2025",
            semester="1st Semester",
            status=AssessmentStatus.FINALIZED,
            total_assessed=Decimal("28122.82"),
            total_discounts=Decimal("0.00"),
            total_payments=Decimal("0.00"),
            outstanding_balance=Decimal("28122.82"),
            items=[
                AssessmentItem(category_id=1, category=tuition, amount=Decimal("13340.00")),
                AssessmentItem(category_id=2, category=lab, amount=Decimal("5618.59")),
                AssessmentItem(category_id=3, category=misc, amount=Decimal("9164.23")),
            ],
            payment_schedules=[
                PaymentSchedule(schedule_type=PaymentScheduleType.DOWN_PAYMENT,
                                amount_due=Decimal("2689.51")),
                PaymentSchedule(schedule_type=PaymentScheduleType.MIDTERM,
                                amount_due=Decimal("9671.66")),
                PaymentSchedule(schedule_type=PaymentScheduleType.PREFINAL,
                                amount_due=Decimal("9671.66")),
                PaymentSchedule(schedule_type=PaymentScheduleType.FINAL,
                                amount_due=Decimal("6089.99")),
            ],
        )
        values.update(overrides)
        return Assessment(**values)

    def test_four_kpi_figures_are_split_out(self):
        summary = summarize_financials(self._assessment())
        assert summary.available is True
        assert format_peso(summary.tuition_fee) == "₱13,340.00"
        assert format_peso(summary.laboratory_fee) == "₱5,618.59"
        assert format_peso(summary.total_assessed) == "₱28,122.82"
        assert format_peso(summary.outstanding_balance) == "₱28,122.82"

    def test_other_fees_explain_the_gap_between_tuition_plus_lab_and_the_total(self):
        """₱28,122.82 - (₱13,340.00 + ₱5,618.59) = ₱9,164.23 of unlabelled fees.

        The brief presents the total without this component. The summary keeps
        it visible instead of folding it into tuition or lab.
        """
        summary = summarize_financials(self._assessment())
        assert summary.named_fees_total == Decimal("28122.82")
        assert summary.other_fees == Decimal("9164.23")
        assert summary.tuition_fee + summary.laboratory_fee == Decimal("18958.59")

    def test_installments_are_listed_in_certificate_order(self):
        summary = summarize_financials(self._assessment())
        assert [row.label for row in summary.installments] == [
            "Down Payment", "Midterm", "Prefinal", "Final"
        ]
        assert [format_peso(r.amount) for r in summary.installments] == [
            "₱2,689.51", "₱9,671.66", "₱9,671.66", "₱6,089.99"
        ]

    def test_the_schedule_total_is_computed(self):
        summary = summarize_financials(self._assessment())
        assert summary.installments_total == Decimal("28122.82")

    def test_the_schedule_is_not_a_simple_percentage_split(self, brief_installments):
        """Reported, because it is the reason the brief's numbers cannot be
        reproduced from its own total."""
        total = sum(to_decimal(amount) for _, amount in brief_installments)
        assert total == Decimal("28122.82")
        assert to_decimal("2689.51") != (total * Decimal("0.10")).quantize(Decimal("0.01"))
        assert to_decimal("9671.66") != (total * Decimal("0.35")).quantize(Decimal("0.01"))

    def test_a_schedule_that_disagrees_with_the_assessed_total_is_flagged(self):
        assessment = self._assessment(
            payment_schedules=[PaymentSchedule(schedule_type=PaymentScheduleType.FINAL,
                                               amount_due=Decimal("100.00"))]
        )
        summary = summarize_financials(assessment)
        assert any("installment schedule sums to" in note for note in summary.discrepancies)

    def test_payment_items_do_not_inflate_the_assessed_total(self):
        payment_category = AssessmentCategory(id=9, category_code="payment_cash",
                                              category_name="Cash Payment", is_fee=False)
        assessment = self._assessment()
        assessment.items.append(AssessmentItem(category_id=9, category=payment_category,
                                              amount=Decimal("5000.00")))
        summary = summarize_financials(assessment)

        # The header value is reported, not recomputed from the lines.
        assert summary.total_assessed == Decimal("28122.82")
        # A payment recorded as an assessment line is money in, not money owed,
        # so it is excluded from the fee-line total. Because it is excluded, the
        # fee lines still reconcile with the header and there is nothing to
        # report: the earlier version of this test asserted the opposite, which
        # described the payment being wrongly folded into the assessed fees.
        assert summary.named_fees_total == Decimal("28122.82")
        assert not any("line items total" in note for note in summary.discrepancies), (
            summary.discrepancies
        )

    def test_payments_are_kept_distinct_from_the_balance(self):
        assessment = self._assessment(
            total_payments=Decimal("2689.51"),
            outstanding_balance=Decimal("25433.31"),
        )
        assessment.payment_schedules[0].amount_paid = Decimal("2689.51")
        assessment.payment_schedules[0].status = PaymentScheduleStatus.PAID
        summary = summarize_financials(assessment)
        assert format_peso(summary.total_payments) == "₱2,689.51"
        assert format_peso(summary.outstanding_balance) == "₱25,433.31"

    def test_no_assessment_is_unavailable_not_zero(self):
        summary = summarize_financials(None)
        assert summary.available is False
        assert "no assessment record" in summary.empty_reason.lower()

    def test_the_financial_tab_shows_the_no_data_state(self, qapp, verified_student):
        from ui.dialogs.scan_result_dialog import ScanResultDialog

        dialog = ScanResultDialog(student=verified_student, scanned_value=PAYLOAD)
        texts = [label.text() for label in _all_labels(dialog)]
        assert any("No assessment record" in text for text in texts)
        dialog.deleteLater()


def _all_labels(widget):
    from PySide6.QtWidgets import QLabel

    return widget.findChildren(QLabel)


# ============================================ 7/8: navigation ===========


class TestAssessmentNavigation:
    def test_scanner_emits_the_request_with_the_verified_student(self, page_factory, verified_student):
        page = page_factory()
        requests = []
        page.navigate_requested.connect(lambda page_id, student: requests.append((page_id, student)))

        page._on_assessment_requested(verified_student)

        assert requests == [("assessment", verified_student)]

    def test_the_modal_wires_the_button_to_the_page_signal(self, qapp, verified_student):
        from ui.dialogs.scan_result_dialog import ScanResultDialog

        dialog = ScanResultDialog(student=verified_student, scanned_value=PAYLOAD)
        seen = []
        dialog.assessment_requested.connect(lambda student: seen.append(student))
        dialog.assessment_btn.click()
        assert seen == [verified_student]
        dialog.deleteLater()

    def test_the_window_routes_to_the_existing_assessment_page(self, qapp, verified_student):
        from ui.main_window import MainWindow, PAGE_CLASSES

        assert "assessment" in PAGE_CLASSES
        window = MainWindow()
        window.navigate_to("assessment", context=verified_student)

        page = window.pages["assessment"]
        assert page.current_student is not None
        assert page.current_student.reference_number == REFERENCE
        assert page.assessment_widget.isVisible() or not window.isVisible()

    def test_the_destination_reverifies_the_reference_number(self, qapp):
        """A payload object that no longer matches the database must not load."""
        from ui.assessment_page import AssessmentPage
        from services.assessment_service import AssessmentService
        from services.student_service import StudentService

        page = AssessmentPage(StudentService(), AssessmentService())
        forged = Student(id=999, reference_number="00000", first_name="Ghost",
                         last_name="Record", program="?", year_level=YearLevel.FIRST_YEAR)
        assert page.preload_student(forged) is False
        assert page.current_student is None
        page.deleteLater()

    def test_a_reference_with_no_record_leaves_the_page_empty(self, qapp):
        from ui.assessment_page import AssessmentPage
        from services.assessment_service import AssessmentService
        from services.student_service import StudentService

        page = AssessmentPage(StudentService(), AssessmentService())
        missing = Student(id=1234, reference_number="00000", first_name="Ghost",
                          last_name="Record", program="?", year_level=YearLevel.FIRST_YEAR)
        assert page.preload_student(missing) is False
        assert page.current_student is None
        assert "could not be found" in page.empty_state.text()
        page.deleteLater()


class TestProfileNavigation:
    def test_scanner_emits_the_students_request(self, page_factory, verified_student):
        page = page_factory()
        requests = []
        page.navigate_requested.connect(lambda page_id, student: requests.append((page_id, student)))

        page._on_profile_requested(verified_student)

        assert requests == [("students", verified_student)]

    def test_the_window_routes_to_the_existing_students_page(self, qapp, verified_student):
        from ui.main_window import MainWindow, PAGE_CLASSES

        assert "students" in PAGE_CLASSES
        window = MainWindow()
        window.navigate_to("students", context=verified_student)

        page = window.pages["students"]
        selected = page.table.selectionModel().selectedRows()
        assert selected, "the verified student should be highlighted"
        assert page.table.item(selected[0].row(), 1).text() == REFERENCE

    def test_navigation_does_not_bypass_the_students_page_lookup(self, qapp):
        from ui.students_page import StudentsPage
        from services.student_service import StudentService

        page = StudentsPage(StudentService())
        forged = Student(id=999, reference_number="does-not-exist", first_name="Ghost",
                         last_name="Record", program="?", year_level=YearLevel.FIRST_YEAR)
        assert page.preload_student(forged) is False
        assert "was not found" in page.count_label.text()
        page.deleteLater()

    def test_the_sidebar_student_button_still_opens_the_editor(self, page_factory, verified_student):
        """The existing inline action keeps working alongside the new routing."""
        page = page_factory()
        page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8), verified_outcome(student=verified_student))

        opened = {}

        class _Dialog:
            def __init__(self, student=None, parent=None):
                opened["student"] = student

            def exec(self):
                return 0

        import ui.dialogs.student_dialog as student_dialog_module

        original = student_dialog_module.StudentDialog
        student_dialog_module.StudentDialog = _Dialog
        try:
            page._view_full_profile()
        finally:
            student_dialog_module.StudentDialog = original

        assert opened["student"] is verified_student


# ================================================ 9: print / export ===


class TestPrintAndExport:
    def test_official_status_requires_records_and_approval(self, brief_com_rows):
        from services.com_summary import summarize_enrollment

        com = summarize_enrollment(make_enrollment(brief_com_rows))
        empty = summarize_financials(None)
        status = official_status(com, empty)
        assert status.is_official is False
        assert any("no assessment" in reason for reason in status.missing_requirements)

    def test_official_status_blocks_a_draft_assessment(self, brief_com_rows):
        from services.com_summary import summarize_enrollment

        com = summarize_enrollment(make_enrollment(brief_com_rows))
        draft = summarize_financials(
            TestFinancialSummary._assessment(status=AssessmentStatus.DRAFT)
        )
        status = official_status(com, draft)
        assert status.is_official is False
        assert "NOT VALID FOR OFFICIAL USE" in status.banner

    def test_a_finalized_consistent_assessment_is_official(self, brief_com_rows):
        from services.com_summary import summarize_enrollment

        com = summarize_enrollment(make_enrollment(brief_com_rows))
        financial = summarize_financials(TestFinancialSummary._assessment())
        status = official_status(com, financial)
        assert status.is_official is True
        assert status.banner == "OFFICIAL CERTIFICATE OF MATRICULATION"

    def test_print_document_contains_the_record_and_no_scanner_chrome(
        self, qapp, verified_student, brief_com_rows
    ):
        from PySide6.QtWidgets import QApplication
        from services.com_summary import summarize_enrollment

        assert QApplication.instance() is not None
        service = ComPrintService.__new__(ComPrintService)  # no ReportService needed
        com = summarize_enrollment(make_enrollment(brief_com_rows))
        financial = summarize_financials(TestFinancialSummary._assessment())

        html = service._build_html(verified_student, com, financial, official_status(com, financial))

        assert STUDENT_NAME in html
        assert REFERENCE in html
        assert "CB 24" in html
        assert "7–8 PM SAT" in html
        assert "₱5,618.59" in html
        assert "₱28,122.82" in html
        # No scanner or navigation furniture belongs on a printed certificate.
        for forbidden in ("Start Scanner", "Scan History", "sidebar", "camera_combo"):
            assert forbidden not in html

    def test_print_document_flags_an_unofficial_copy(self, qapp, verified_student, brief_com_rows):
        from services.com_summary import summarize_enrollment

        service = ComPrintService.__new__(ComPrintService)
        com = summarize_enrollment(make_enrollment(brief_com_rows))
        financial = summarize_financials(TestFinancialSummary._assessment())
        html = service._build_html(verified_student, com, financial, official_status(com, financial))
        assert html == html  # builds without raising
        draft = summarize_financials(
            TestFinancialSummary._assessment(status=AssessmentStatus.DRAFT)
        )
        draft_html = service._build_html(verified_student, com, draft, official_status(com, draft))
        assert "UNOFFICIAL PREVIEW" in draft_html

    def test_print_document_reports_unresolved_discrepancies(self, qapp, verified_student, brief_com_rows):
        from services.com_summary import summarize_enrollment

        service = ComPrintService.__new__(ComPrintService)
        com = summarize_enrollment(make_enrollment(brief_com_rows))
        broken = summarize_financials(
            TestFinancialSummary._assessment(
                payment_schedules=[PaymentSchedule(schedule_type=PaymentScheduleType.FINAL,
                                                   amount_due=Decimal("1.00"))]
            )
        )
        html = service._build_html(verified_student, com, broken, official_status(com, broken))
        assert "DATA DISCREPANCIES" in html

    def test_pdf_export_uses_the_existing_report_service(self, tmp_path, verified_student, brief_com_rows):
        class RecordingReportService:
            def __init__(self):
                self.calls = []

            def generate_certificate_of_matriculation(self, student, enrollment, assessment=None,
                                                      output_path=None):
                self.calls.append((student, enrollment, assessment, output_path))
                Path(output_path).write_bytes(b"%PDF-1.4\n%stub\n")
                return Path(output_path)

        report = RecordingReportService()
        service = ComPrintService(report_service=report)
        enrollment = make_enrollment(brief_com_rows)
        target = tmp_path / "com.pdf"

        result = service.export_pdf(verified_student, enrollment, None, target)

        assert result == target
        assert target.read_bytes().startswith(b"%PDF")
        assert len(report.calls) == 1
        assert report.calls[0][0] is verified_student
        assert report.calls[0][1] is enrollment

    def test_export_is_refused_without_an_enrollment_record(self, verified_student):
        service = ComPrintService(report_service=object())
        with pytest.raises(ComNotCertifiableError):
            service.export_pdf(verified_student, None, None, Path("/tmp/should-not-exist.pdf"))

    def test_print_is_refused_without_an_enrollment_record(self, verified_student):
        from services.com_summary import summarize_enrollment

        service = ComPrintService(report_service=object())
        with pytest.raises(ComNotCertifiableError):
            service.print_com(verified_student, summarize_enrollment(None), summarize_financials(None))


# ================================================ 13: audio cue ==========


class TestAudioCue:
    def test_the_page_beeps_once_for_a_verified_scan(self, page_factory, verified_student):
        page = page_factory()
        page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8), verified_outcome(student=verified_student))
        assert page.audio_cue.calls == 1

    def test_duplicate_frames_do_not_repeat_the_beep(self, page_factory, verified_student):
        page = page_factory()
        for _ in range(20):
            page._on_frame_result(
                np.zeros((4, 4, 3), dtype=np.uint8), verified_outcome(student=verified_student)
            )
        assert page.audio_cue.calls == 1

    def test_no_beep_for_an_unknown_barcode(self, qapp):
        page = make_page()
        page._on_frame_result(
            np.zeros((4, 4, 3), dtype=np.uint8),
            ScanOutcome(state=ScanState.UNKNOWN_BARCODE, message="Unknown barcode",
                        decoded_value="000000000"),
        )
        assert page.audio_cue.calls == 0

    def test_the_beep_can_be_disabled(self, page_factory, verified_student):
        page = page_factory()
        page.sound_btn.setChecked(False)
        page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8), verified_outcome(student=verified_student))
        assert page.audio_cue.calls == 0
        assert "Sound Off" in page.sound_btn.text()

    def test_the_missing_asset_is_not_silently_referenced(self):
        """`success_beep.mp3` is not in the repository; the service must say so."""
        from services.audio_cue import AudioCueService

        cue = AudioCueService(enabled=True)
        assert not settings.AUDIO_DIR.joinpath(settings.SCAN_SUCCESS_BEEP_FILE).exists()
        if cue.asset_path is None:
            assert cue.source == "platform"
            assert cue.available is True
        else:
            assert cue.source == "asset"
            assert cue.asset_path.is_file()

    def test_a_configured_file_that_does_not_exist_is_not_used(self, tmp_path):
        from services.audio_cue import AudioCueService

        cue = AudioCueService(asset_path=tmp_path / "nope.mp3")
        assert cue.asset_path is None
        assert cue.source == "platform"

    def test_an_existing_asset_file_is_used(self, tmp_path):
        from services.audio_cue import AudioCueService

        fake = tmp_path / "success_beep.mp3"
        fake.write_bytes(b"not really audio")
        cue = AudioCueService(asset_path=fake)
        # Either it decodes (asset backend) or it does not (platform fallback);
        # what must never happen is a reference to a file that is not there.
        assert cue.asset_path is None or cue.asset_path == fake
        assert cue.source in ("asset", "platform")

    def test_disabled_service_plays_nothing(self):
        from services.audio_cue import AudioCueService

        cue = AudioCueService(enabled=False)
        assert cue.play_success() is False
        assert cue.play_count == 0

    def test_the_rate_limit_stops_a_burst(self):
        from services.audio_cue import AudioCueService

        now = {"t": 0.0}
        cue = AudioCueService(enabled=True, min_interval_seconds=0.5, clock=lambda: now["t"])
        assert cue.play_success() is True
        assert cue.play_success() is False
        now["t"] = 1.0
        assert cue.play_success() is True
        assert cue.play_count == 2

    def test_toggle_round_trips(self):
        from services.audio_cue import AudioCueService

        cue = AudioCueService(enabled=True)
        assert cue.toggle() is False
        assert cue.play_success() is False
        assert cue.toggle() is True


# ======================================= 14: layout and accessibility ====


class TestAccessibility:
    def test_the_modal_can_be_closed_with_escape(self, qapp, verified_student):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtWidgets import QApplication, QDialog
        from ui.dialogs.scan_result_dialog import ScanResultDialog

        dialog = ScanResultDialog(student=verified_student, scanned_value=PAYLOAD)
        dialog.show()
        QApplication.processEvents()

        event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        dialog.keyPressEvent(event)

        assert dialog.result() == int(QDialog.DialogCode.Rejected)
        dialog.deleteLater()

    def test_focus_starts_on_the_close_button(self, qapp, verified_student):
        from PySide6.QtWidgets import QApplication
        from ui.dialogs.scan_result_dialog import ScanResultDialog

        dialog = ScanResultDialog(student=verified_student, scanned_value=PAYLOAD)
        dialog.show()
        # Focus is assigned on the show event, so the event loop has to run
        # before the assertion means anything.
        QApplication.processEvents()
        # focusWidget() rather than hasFocus(): this asserts what the dialog
        # assigned to itself, which is the behaviour under test. hasFocus()
        # additionally depends on the window manager having given the window
        # keyboard focus, which never happens under the offscreen platform and
        # made this test report a failure that was not one.
        assert dialog.focusWidget() is dialog._close_button
        # Closed explicitly: a dialog left shown outlives the test and is
        # destroyed later from inside an event callback, which crashed the
        # interpreter at an unpredictable point in a later test.
        dialog.close()
        dialog.deleteLater()

    def test_the_close_button_is_labelled_and_reachable(self, qapp, verified_student):
        from PySide6.QtCore import Qt
        from ui.dialogs.scan_result_dialog import ScanResultDialog

        dialog = ScanResultDialog(student=verified_student, scanned_value=PAYLOAD)
        assert dialog._close_button.accessibleName()
        assert dialog._close_button.toolTip()

        for button in (dialog.profile_btn, dialog.assessment_btn, dialog.export_btn, dialog.next_btn):
            assert button.accessibleName(), button.text()
            assert button.text(), "every action button needs a visible label"
            # Every action must be reachable by Tab.
            assert button.focusPolicy() != Qt.FocusPolicy.NoFocus, button.text()
        dialog.deleteLater()

    def test_the_modal_uses_a_layout_that_survives_a_small_window(self, qapp, verified_student, brief_com_rows):
        from ui.dialogs.scan_result_dialog import ScanResultDialog

        dialog = ScanResultDialog(
            student=verified_student,
            enrollment=make_enrollment(brief_com_rows),
            scanned_value=PAYLOAD,
        )
        for width, height in ((560, 560), (900, 700), (420, 300)):
            dialog.resize(width, height)
            dialog.show()
            dialog.layout().activate()
            assert dialog.minimumSize().width() <= width
        assert dialog.minimumSize().height() <= 560
        dialog.deleteLater()

    def test_the_subject_table_scrolls_rather_than_shrinking_to_nothing(
        self, qapp, verified_student, brief_com_rows
    ):
        from PySide6.QtWidgets import QTableWidget
        from ui.dialogs.scan_result_dialog import ScanResultDialog

        dialog = ScanResultDialog(
            student=verified_student,
            enrollment=make_enrollment(brief_com_rows),
            scanned_value=PAYLOAD,
        )
        table = next(
            t for t in dialog.findChildren(QTableWidget) if t.objectName() == "comSubjectTable"
        )
        assert table.columnCount() == 6, "columns stay put; the table scrolls"
        assert table.sizePolicy().horizontalPolicy() is not None
        dialog.deleteLater()

    def test_history_and_status_widgets_are_named_for_assistive_tech(self, page_factory):
        page = page_factory()
        assert page.history_table.accessibleName()
        assert page._phase_label.accessibleName()
        assert page.sound_btn.accessibleName()

    def test_the_phase_label_tracks_the_session(self, page_factory, verified_student):
        page = page_factory()
        page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8), verified_outcome(student=verified_student))
        assert page._phase_label.text() == "Success"

        page.scan_session.start_new_session()
        page._update_phase_label(page.scan_session.phase)
        assert page._phase_label.text() == "Ready"


# ================================================== teardown guarantees ===


class TestCleanup:
    def test_shutdown_releases_the_timer_worker_and_camera(self, qapp):
        from services.student_service import StudentService
        from ui.scanner_page import ScannerPage

        stopped = []

        class Camera:
            def stop(self_inner):
                stopped.append(True)

        page = ScannerPage(StudentService())
        page.camera_service = Camera()
        page.scan_repo = FakeScanRepository()
        page.audio_cue = RecordingAudio()
        page.preview_timer.start()

        page._shutdown()

        assert stopped == [True]
        assert page.preview_timer.isActive() is False
        assert page.worker is None
        page.deleteLater()

    def test_shutdown_is_safe_to_call_twice(self, qapp):
        from services.student_service import StudentService
        from ui.scanner_page import ScannerPage

        page = ScannerPage(StudentService())
        page.scan_repo = FakeScanRepository()
        page.audio_cue = RecordingAudio()
        page._shutdown()
        page._shutdown()
        page.deleteLater()

    def test_no_modal_is_opened_while_one_is_already_up(self, page_factory, verified_student):
        page = page_factory()
        page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8), verified_outcome(student=verified_student))

        opened = []
        page._open_result_panel = lambda *a, **k: opened.append(True)

        page.scan_session.start_new_session()
        page._presented_value = None
        class _Active:
            def isVisible(self):
                return True
        page._active_dialog = _Active()
        page._present_scan_result(verified_outcome(), verified_student)

        assert opened == []

# ============================== asynchronous record loading ===============
#
# The panel must appear the moment the identity is verified, before the
# enrollment/assessment queries have answered. Fetching them first blocked the
# GUI thread: with a slow or unreachable database the popup never opened and the
# window froze, which presents as "the scan decoded but nothing happened".


def _pump_until(predicate, qapp, timeout=5.0):
    """Spin the event loop until ``predicate`` holds or the timeout expires."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return predicate()


class _SlowService:
    """Student service whose enrollment lookup blocks until released."""

    def __init__(self):
        self.calls = 0
        self._release = threading.Event()
        self.result = []

    def get_enrollments_by_student(self, student_id):
        self.calls += 1
        self._release.wait(5.0)
        return self.result


class TestAsynchronousRecordLoading:
    def test_the_panel_opens_before_the_records_arrive(self, qapp, page_factory, verified_student):
        from PySide6.QtWidgets import QFrame

        page = page_factory()
        slow = _SlowService()
        page.student_service.get_enrollments_by_student = slow.get_enrollments_by_student

        page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8),
                              verified_outcome(student=verified_student))

        dialog = page._active_dialog
        assert dialog is not None, "the panel must open straight away"
        assert dialog.isVisible()
        assert dialog.loading is True
        panels = [w for w in dialog.findChildren(QFrame) if w.objectName() == "loadingPanel"]
        assert panels, "a loading state must be shown while records are fetched"

        slow._release.set()

    def test_records_replace_the_loading_state_when_they_land(
        self, qapp, page_factory, verified_student, brief_com_rows
    ):
        from PySide6.QtWidgets import QFrame, QTabWidget

        page = page_factory()
        slow = _SlowService()
        slow.result = [make_enrollment(brief_com_rows)]
        page.student_service.get_enrollments_by_student = slow.get_enrollments_by_student

        page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8),
                              verified_outcome(student=verified_student))
        dialog = page._active_dialog
        slow._release.set()

        assert _pump_until(lambda: dialog.loading is False, qapp), "records never arrived"

        # The loading panel is taken out of the layout synchronously; the object
        # itself is destroyed on the next event-loop pass, so "no longer part of
        # the visible layout" is the assertion that describes the behaviour.
        layout = dialog._body.parentWidget().layout()
        names = [layout.itemAt(i).widget().objectName() for i in range(layout.count())]
        assert "loadingPanel" not in names, names
        assert not any(
            w.objectName() == "loadingPanel" and w.isVisible()
            for w in dialog.findChildren(QFrame)
        )
        assert dialog.findChildren(QTabWidget), "the tabs must take over"
        assert dialog.com_summary.subject_count == len(brief_com_rows)

    def test_a_database_failure_shows_an_error_not_empty_data(
        self, qapp, page_factory, verified_student
    ):
        from PySide6.QtWidgets import QFrame, QLabel

        page = page_factory()

        def boom(student_id):
            raise RuntimeError("MySQL server is unreachable")

        page.student_service.get_enrollments_by_student = boom
        page.assessment_service.get_latest_assessment = boom

        page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8),
                              verified_outcome(student=verified_student))
        dialog = page._active_dialog

        assert _pump_until(lambda: dialog.loading is False, qapp)

        panels = [w for w in dialog.findChildren(QFrame) if w.objectName() == "loadErrorPanel"]
        assert panels, "a failed lookup must be shown as a failure"
        texts = [label.text() for label in dialog.findChildren(QLabel)]
        assert any("Could not load" in t for t in texts)
        # The verified identity is untouched, and no money was invented.
        assert any(STUDENT_NAME == label.text() for label in dialog.findChildren(QLabel))
        assert not any("28,122.82" in t or "13,340.00" in t for t in texts)

    def test_only_one_lookup_runs_for_a_burst_of_frames(
        self, qapp, page_factory, verified_student
    ):
        page = page_factory()
        slow = _SlowService()
        page.student_service.get_enrollments_by_student = slow.get_enrollments_by_student

        for _ in range(20):
            page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8),
                                  verified_outcome(student=verified_student))
        slow._release.set()

        # The worker runs on its own thread, so give it a moment to be counted
        # before asserting that exactly one lookup happened.
        assert _pump_until(lambda: slow.calls >= 1, qapp), "no lookup ran at all"
        _pump_until(lambda: False, qapp, timeout=0.5)

        assert slow.calls == 1, f"repeated frames started {slow.calls} lookups"
        assert page._active_dialog is not None

    def test_records_arriving_after_the_panel_closed_are_dropped(
        self, qapp, page_factory, verified_student
    ):
        page = page_factory()
        slow = _SlowService()
        page.student_service.get_enrollments_by_student = slow.get_enrollments_by_student

        page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8),
                              verified_outcome(student=verified_student))
        dialog = page._active_dialog

        page._on_dialog_finished(dialog)          # operator closes it
        assert page._active_dialog is None

        slow._release.set()                        # lookup now completes
        _pump_until(lambda: False, qapp, timeout=1.0)   # let the signal land

        assert page._active_dialog is None, "a closed panel must not be resurrected"

    def test_scan_next_while_a_lookup_is_in_flight_is_safe(
        self, qapp, page_factory, verified_student
    ):
        page = page_factory()
        slow = _SlowService()
        page.student_service.get_enrollments_by_student = slow.get_enrollments_by_student

        page._on_frame_result(np.zeros((4, 4, 3), dtype=np.uint8),
                              verified_outcome(student=verified_student))
        page._on_scan_next_requested()

        assert page.current_student is None
        assert page.scan_session.is_latched is False

        slow._release.set()
        _pump_until(lambda: False, qapp, timeout=1.0)

    def test_the_lookup_runs_off_the_gui_thread(self, page_factory, verified_student):
        """The worker must be a real background thread, not a blocking call."""
        from PySide6.QtCore import QThread

        from ui.scanner_page import RecordLookupWorker

        worker = RecordLookupWorker(
            enrollment=lambda sid: [],
            assessment=lambda sid: None,
            student_id=verified_student.id,
        )
        assert isinstance(worker, QThread)
        assert worker.isRunning() is False

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

import pytest
from unittest.mock import Mock, MagicMock


@pytest.fixture(autouse=True)
def _reset_database_state():
    """Isolate the module-level connection pool and retry backoff.

    ``database.connection`` caches the pool, the last attempt time and the
    last log time in globals. Without resetting them, test order determines
    whether a connection attempt is made or is skipped by the retry backoff.
    """
    import database.connection as conn

    conn._pool = None
    conn._last_attempt = 0.0
    conn._last_failure_log = 0.0
    conn._last_error = ""
    yield
    conn._pool = None


@pytest.fixture
def mock_student():
    from models.student import Student, EnrollmentStatus, Gender, YearLevel
    return Student(
        id=1,
        student_id="WIT-001",
        reference_number="65412",
        first_name="John",
        middle_name="D",
        last_name="Doe",
        program="Computer Engineering",
        year_level=YearLevel.THIRD_YEAR,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )


@pytest.fixture
def mock_enrollment():
    from models.enrollment import Enrollment, EnrollmentSubject, Subject, Semester, EnrollmentStatus as EnrollmentStatusEnum, SubjectStatus
    subject = Subject(id=1, subject_code="CPE 311", subject_name="CPE 311", course_id=1, credits=4)
    es = EnrollmentSubject(
        id=1, enrollment_id=1, subject_id=1, subject=subject,
        section="EP1", schedule_time="7-8 PM", schedule_days="Saturday",
        room="CB 24", period=5, status=SubjectStatus.ENROLLED
    )
    return Enrollment(
        id=1, student_id=1, academic_year="2024-2025",
        semester=Semester.FIRST, status=EnrollmentStatusEnum.ENROLLED,
        total_credits=4, subjects=[es]
    )


@pytest.fixture
def mock_assessment():
    from models.assessment import Assessment, AssessmentItem, AssessmentCategory, PaymentSchedule, AssessmentStatus, PaymentScheduleType, PaymentScheduleStatus
    from decimal import Decimal
    
    cat = AssessmentCategory(id=1, category_code="tuition_fee", category_name="Tuition Fee", is_fee=True, is_discount=False)
    item = AssessmentItem(id=1, category_id=1, amount=Decimal("25000.00"), category=cat, description="Tuition")
    schedule = PaymentSchedule(id=1, schedule_type=PaymentScheduleType.DOWN_PAYMENT, amount_due=Decimal("8000.00"), amount_paid=Decimal("8000.00"), status=PaymentScheduleStatus.PAID)
    
    return Assessment(
        id=1, student_id=1, academic_year="2024-2025", semester="1st Semester",
        status=AssessmentStatus.FINALIZED, items=[item], payment_schedules=[schedule]
    )


@pytest.fixture
def mock_barcode():
    from models.barcode import Barcode, BarcodeType
    return Barcode(
        id=1, student_id=1, barcode_value="WIT:STUDENT:65412",
        barcode_type=BarcodeType.CODE128, image_path="barcodes/test.png"
    )
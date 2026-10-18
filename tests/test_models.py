import pytest
from decimal import Decimal
from datetime import date
from models.student import Student, StudentContact, EnrollmentStatus, Gender, YearLevel, ContactType
from models.enrollment import Enrollment, EnrollmentSubject, Subject, Course, Semester, EnrollmentStatus as EnrollmentStatusEnum, SubjectStatus
from models.assessment import Assessment, AssessmentItem, AssessmentCategory, Payment, PaymentSchedule, AssessmentStatus, PaymentMethod, PaymentScheduleType, PaymentScheduleStatus
from models.barcode import Barcode, BarcodeScan, BarcodeType, ScanStatus


class TestStudentModel:
    def test_student_creation(self):
        student = Student(
            student_id="WIT-001",
            reference_number="65412",
            first_name="John",
            middle_name="D",
            last_name="Doe",
            program="Computer Engineering",
            year_level=YearLevel.THIRD_YEAR,
        )
        assert student.student_id == "WIT-001"
        assert student.reference_number == "65412"
        assert student.full_name == "John D Doe"
        assert student.display_name == "Doe, John D."
    
    def test_student_age_calculation(self):
        student = Student(
            date_of_birth=date(2000, 1, 1),
        )
        # Age should be calculated correctly
        assert student.age is not None
        assert student.age >= 20
    
    def test_student_with_contacts(self):
        contacts = [
            StudentContact(contact_type=ContactType.PHONE, contact_value="09123456789", is_primary=True),
            StudentContact(contact_type=ContactType.EMAIL, contact_value="test@email.com", is_primary=False),
        ]
        student = Student(contacts=contacts)
        assert len(student.contacts) == 2
        assert student.contacts[0].is_primary


class TestEnrollmentModel:
    def test_enrollment_creation(self):
        enrollment = Enrollment(
            student_id=1,
            academic_year="2024-2025",
            semester=Semester.FIRST,
        )
        assert enrollment.academic_year == "2024-2025"
        assert enrollment.semester == Semester.FIRST
    
    def test_enrollment_subject(self):
        subject = Subject(subject_code="CPE 311", subject_name="CPE 311", course_id=1, credits=4)
        es = EnrollmentSubject(
            enrollment_id=1,
            subject_id=1,
            subject=subject,
            section="EP1",
            schedule_time="7-8 PM",
            schedule_days="Saturday",
            room="CB 24",
            period=5,
        )
        assert es.subject.credits == 4
        assert es.section == "EP1"


class TestAssessmentModel:
    def test_assessment_creation(self):
        assessment = Assessment(
            student_id=1,
            academic_year="2024-2025",
            semester="1st Semester",
        )
        assert assessment.student_id == 1
        assert assessment.status == AssessmentStatus.DRAFT
    
    def test_assessment_recalculate(self):
        category = AssessmentCategory(
            id=1, category_code="tuition_fee", category_name="Tuition Fee",
            is_fee=True, is_discount=False
        )
        items = [
            AssessmentItem(category_id=1, amount=Decimal("25000.00"), category=category),
            AssessmentItem(category_id=2, amount=Decimal("5000.00"), category=category),
        ]
        assessment = Assessment(items=items)
        assessment.recalculate_totals()
        assert assessment.total_assessed == Decimal("30000.00")


class TestBarcodeModel:
    def test_barcode_creation(self):
        barcode = Barcode(
            student_id=1,
            barcode_value="WIT:STUDENT:65412",
            barcode_type=BarcodeType.CODE128,
        )
        assert barcode.barcode_value == "WIT:STUDENT:65412"
        assert barcode.barcode_type == BarcodeType.CODE128
    
    def test_barcode_scan(self):
        scan = BarcodeScan(
            scanned_value="WIT:STUDENT:65412",
            student_id=1,
            scan_status=ScanStatus.SUCCESS,
            confidence_score=0.95,
        )
        assert scan.scan_status == ScanStatus.SUCCESS
        assert scan.confidence_score == 0.95


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
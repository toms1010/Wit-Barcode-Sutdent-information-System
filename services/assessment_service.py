from typing import Optional, List
from decimal import Decimal
from database.repositories import AssessmentCategoryRepository, AssessmentRepository, PaymentRepository
from models.assessment import (
    Assessment, AssessmentItem, AssessmentCategory, Payment, PaymentSchedule,
    AssessmentStatus, PaymentMethod, PaymentScheduleType, PaymentScheduleStatus
)
import logging

logger = logging.getLogger(__name__)


class AssessmentService:
    def __init__(self):
        self.category_repo = AssessmentCategoryRepository()
        self.assessment_repo = AssessmentRepository()
        self.payment_repo = PaymentRepository()

    def get_categories(self) -> List[AssessmentCategory]:
        return self.category_repo.get_all()

    def create_assessment(self, assessment: Assessment, created_by: int = None) -> Optional[int]:
        assessment.created_by = created_by
        assessment.updated_by = created_by
        assessment.recalculate_totals()
        return self.assessment_repo.create(assessment)

    def get_assessment(self, assessment_id: int) -> Optional[Assessment]:
        return self.assessment_repo.get_by_id(assessment_id)

    def get_student_assessments(self, student_id: int) -> List[Assessment]:
        return self.assessment_repo.get_by_student(student_id)

    def get_latest_assessment(self, student_id: int) -> Optional[Assessment]:
        return self.assessment_repo.get_latest_by_student(student_id)

    def update_assessment(self, assessment: Assessment, updated_by: int = None) -> bool:
        assessment.updated_by = updated_by
        assessment.recalculate_totals()
        return self.assessment_repo.update(assessment)

    def add_assessment_item(self, assessment_id: int, item: AssessmentItem) -> bool:
        assessment = self.assessment_repo.get_by_id(assessment_id)
        if not assessment:
            return False
        assessment.items.append(item)
        return self.update_assessment(assessment)

    def verify_item(self, assessment_id: int, item_id: int, verified_by: int, verified_amount: Decimal) -> bool:
        assessment = self.assessment_repo.get_by_id(assessment_id)
        if not assessment:
            return False
        
        for item in assessment.items:
            if item.id == item_id:
                item.amount = verified_amount
                item.is_verified = True
                item.verified_by = verified_by
                from datetime import datetime
                item.verified_at = datetime.now()
                break
        
        return self.update_assessment(assessment)

    def record_payment(self, payment: Payment, received_by: int = None) -> Optional[int]:
        payment.received_by = received_by
        return self.payment_repo.create(payment)

    def get_payments(self, assessment_id: int) -> List[Payment]:
        return self.payment_repo.get_by_assessment(assessment_id)

    def void_payment(self, payment_id: int, voided_by: int, void_reason: str) -> bool:
        return self.payment_repo.void(payment_id, voided_by, void_reason)

    def generate_payment_schedule(self, assessment: Assessment, schedule_type: PaymentScheduleType = PaymentScheduleType.DOWN_PAYMENT) -> List[PaymentSchedule]:
        schedules = []
        remaining = assessment.outstanding_balance
        
        if schedule_type == PaymentScheduleType.DOWN_PAYMENT:
            schedules.append(PaymentSchedule(
                assessment_id=assessment.id,
                schedule_type=PaymentScheduleType.DOWN_PAYMENT,
                due_date=assessment.assessment_date,
                amount_due=min(Decimal("5000.00"), remaining),
            ))
        elif schedule_type == PaymentScheduleType.MIDTERM:
            schedules.append(PaymentSchedule(
                assessment_id=assessment.id,
                schedule_type=PaymentScheduleType.MIDTERM,
                due_date=assessment.assessment_date,
                amount_due=remaining * Decimal("0.3"),
            ))
        elif schedule_type == PaymentScheduleType.PREFINAL:
            schedules.append(PaymentSchedule(
                assessment_id=assessment.id,
                schedule_type=PaymentScheduleType.PREFINAL,
                due_date=assessment.assessment_date,
                amount_due=remaining * Decimal("0.2"),
            ))
        elif schedule_type == PaymentScheduleType.FINAL:
            schedules.append(PaymentSchedule(
                assessment_id=assessment.id,
                schedule_type=PaymentScheduleType.FINAL,
                due_date=assessment.assessment_date,
                amount_due=remaining,
            ))
        
        return schedules

    def create_default_assessment(self, student_id: int, enrollment_id: int = None, academic_year: str = "2024-2025", semester: str = "1st Semester", created_by: int = None) -> Optional[Assessment]:
        categories = self.get_categories()
        
        assessment = Assessment(
            student_id=student_id,
            enrollment_id=enrollment_id,
            academic_year=academic_year,
            semester=semester,
            status=AssessmentStatus.DRAFT,
            created_by=created_by,
            updated_by=created_by,
        )
        
        for cat in categories:
            if cat.is_fee and not cat.is_discount:
                item = AssessmentItem(
                    assessment_id=0,
                    category_id=cat.id,
                    description=cat.category_name,
                    amount=Decimal("0.00"),
                    is_verified=False,
                    # The category travels with the item: without it
                    # recalculate_totals() cannot tell a fee line from a
                    # discount line, and every total comes out ₱0.00.
                    category=cat,
                )
                assessment.items.append(item)
        
        return assessment
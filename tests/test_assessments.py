import pytest
from unittest.mock import Mock, patch
from decimal import Decimal
from datetime import date
from models.assessment import (
    Assessment, AssessmentItem, AssessmentCategory, Payment, PaymentSchedule,
    AssessmentStatus, PaymentMethod, PaymentScheduleType, PaymentScheduleStatus
)
from services.assessment_service import AssessmentService


class TestAssessmentModel:
    def test_assessment_recalculate_totals(self):
        category_fee = AssessmentCategory(id=1, category_code="tuition_fee", is_fee=True, is_discount=False)
        category_discount = AssessmentCategory(id=2, category_code="scholarship", is_fee=False, is_discount=True)
        category_payment = AssessmentCategory(id=3, category_code="cash", is_fee=False, is_discount=False)
        
        items = [
            AssessmentItem(category_id=1, amount=Decimal("25000.00"), category=category_fee),
            AssessmentItem(category_id=2, amount=Decimal("5000.00"), category=category_discount),
            AssessmentItem(category_id=3, amount=Decimal("10000.00"), category=category_payment),
        ]
        
        schedules = [
            PaymentSchedule(amount_due=Decimal("8000.00"), amount_paid=Decimal("8000.00")),
            PaymentSchedule(amount_due=Decimal("7000.00"), amount_paid=Decimal("5000.00")),
        ]
        
        assessment = Assessment(items=items, payment_schedules=schedules)
        assessment.recalculate_totals()
        
        assert assessment.total_assessed == Decimal("25000.00")
        assert assessment.total_discounts == Decimal("5000.00")
        assert assessment.total_payments == Decimal("13000.00")
        assert assessment.outstanding_balance == Decimal("7000.00")
    
    def test_assessment_to_dict(self):
        category = AssessmentCategory(
            id=1, category_code="tuition_fee", category_name="Tuition Fee",
            is_fee=True, is_discount=False
        )
        item = AssessmentItem(
            id=1, category_id=1, amount=Decimal("25000.00"),
            category=category, description="Semester tuition"
        )
        
        assessment = Assessment(
            id=1, student_id=1, academic_year="2024-2025",
            semester="1st Semester", items=[item]
        )
        assessment.recalculate_totals()
        
        data = assessment.to_dict()
        
        assert data["id"] == 1
        assert data["total_assessed"] == 25000.0
        assert len(data["items"]) == 1
        assert data["items"][0]["category_code"] == "tuition_fee"


class TestPaymentModel:
    def test_payment_creation(self):
        payment = Payment(
            assessment_id=1,
            amount=Decimal("5000.00"),
            payment_method=PaymentMethod.CASH,
            reference_number="REF123",
        )
        
        assert payment.amount == Decimal("5000.00")
        assert payment.payment_method == PaymentMethod.CASH
        assert payment.reference_number == "REF123"
        assert payment.is_voided is False


class TestPaymentScheduleModel:
    def test_schedule_status(self):
        schedule = PaymentSchedule(
            amount_due=Decimal("10000.00"),
            amount_paid=Decimal("10000.00"),
        )
        assert schedule.amount_due == Decimal("10000.00")
        assert schedule.amount_paid == Decimal("10000.00")
        assert schedule.schedule_type == PaymentScheduleType.DOWN_PAYMENT


class TestAssessmentService:
    @patch('services.assessment_service.AssessmentRepository')
    @patch('services.assessment_service.AssessmentCategoryRepository')
    def test_create_default_assessment(self, mock_cat_repo, mock_assess_repo):
        mock_categories = [
            AssessmentCategory(id=1, category_code="tuition_fee", category_name="Tuition Fee", is_fee=True, is_discount=False),
            AssessmentCategory(id=2, category_code="misc_fee", category_name="Miscellaneous Fee", is_fee=True, is_discount=False),
        ]
        mock_cat_repo.return_value.get_all.return_value = mock_categories
        
        service = AssessmentService()
        assessment = service.create_default_assessment(
            student_id=1,
            enrollment_id=1,
            academic_year="2024-2025",
            semester="1st Semester",
            created_by=1,
        )
        
        assert assessment.student_id == 1
        assert assessment.enrollment_id == 1
        assert len(assessment.items) == 2
        assert assessment.items[0].category_id == 1
        assert assessment.items[1].category_id == 2
    
    @patch('services.assessment_service.PaymentRepository')
    @patch('services.assessment_service.AssessmentRepository')
    def test_record_payment(self, mock_assess_repo, mock_payment_repo):
        mock_payment_repo.return_value.create.return_value = 1
        
        service = AssessmentService()
        payment = Payment(
            assessment_id=1,
            amount=Decimal("5000.00"),
            payment_method=PaymentMethod.CASH,
        )
        
        result = service.record_payment(payment, received_by=1)
        
        assert result == 1
        mock_payment_repo.return_value.create.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
from typing import Optional, List
from decimal import Decimal
from database.connection import get_cursor
from models.assessment import Assessment, AssessmentItem, AssessmentCategory, Payment, PaymentSchedule, AssessmentStatus, PaymentMethod, PaymentScheduleType, PaymentScheduleStatus
import logging

logger = logging.getLogger(__name__)


class AssessmentCategoryRepository:
    def get_all(self, active_only: bool = True) -> List[AssessmentCategory]:
        with get_cursor() as cursor:
            query = "SELECT * FROM assessment_categories"
            if active_only:
                query += " WHERE is_active = TRUE"
            query += " ORDER BY display_order"
            cursor.execute(query)
            return [self._row_to_category(row) for row in cursor.fetchall()]

    def get_by_id(self, category_id: int) -> Optional[AssessmentCategory]:
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM assessment_categories WHERE id = %s", (category_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_category(row)

    def _row_to_category(self, row: dict) -> AssessmentCategory:
        return AssessmentCategory(
            id=row["id"],
            category_code=row["category_code"],
            category_name=row["category_name"],
            description=row["description"],
            is_fee=row["is_fee"],
            is_discount=row["is_discount"],
            display_order=row["display_order"],
            is_active=row["is_active"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class AssessmentRepository:
    def create(self, assessment: Assessment) -> Optional[int]:
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO assessments (student_id, enrollment_id, academic_year, semester, assessment_date, status,
                    total_assessed, total_discounts, total_payments, outstanding_balance, notes, created_by, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    assessment.student_id,
                    assessment.enrollment_id,
                    assessment.academic_year,
                    assessment.semester,
                    assessment.assessment_date,
                    assessment.status.value if isinstance(assessment.status, AssessmentStatus) else assessment.status,
                    assessment.total_assessed,
                    assessment.total_discounts,
                    assessment.total_payments,
                    assessment.outstanding_balance,
                    assessment.notes,
                    assessment.created_by,
                    assessment.updated_by,
                ),
            )
            assessment_id = cursor.lastrowid
            
            for item in assessment.items:
                cursor.execute(
                    """
                    INSERT INTO assessment_items (assessment_id, category_id, description, original_amount_text, amount, is_verified, verified_by, verified_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        assessment_id,
                        item.category_id,
                        item.description,
                        item.original_amount_text,
                        item.amount,
                        item.is_verified,
                        item.verified_by,
                        item.verified_at,
                    ),
                )
            
            for schedule in assessment.payment_schedules:
                cursor.execute(
                    """
                    INSERT INTO payment_schedules (assessment_id, schedule_type, due_date, amount_due, amount_paid, status, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        assessment_id,
                        schedule.schedule_type.value if isinstance(schedule.schedule_type, PaymentScheduleType) else schedule.schedule_type,
                        schedule.due_date,
                        schedule.amount_due,
                        schedule.amount_paid,
                        schedule.status.value if isinstance(schedule.status, PaymentScheduleStatus) else schedule.status,
                        schedule.notes,
                    ),
                )
            
            return assessment_id

    def get_by_id(self, assessment_id: int) -> Optional[Assessment]:
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM assessments WHERE id = %s", (assessment_id,))
            row = cursor.fetchone()
            if not row:
                return None
            assessment = self._row_to_assessment(row)
            assessment.items = self._get_items(assessment_id)
            assessment.payment_schedules = self._get_schedules(assessment_id)
            return assessment

    def get_by_student(self, student_id: int) -> List[Assessment]:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM assessments WHERE student_id = %s ORDER BY assessment_date DESC",
                (student_id,),
            )
            assessments = []
            for row in cursor.fetchall():
                assessment = self._row_to_assessment(row)
                assessment.items = self._get_items(assessment.id)
                assessment.payment_schedules = self._get_schedules(assessment.id)
                assessments.append(assessment)
            return assessments

    def get_latest_by_student(self, student_id: int) -> Optional[Assessment]:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM assessments WHERE student_id = %s ORDER BY assessment_date DESC LIMIT 1",
                (student_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            assessment = self._row_to_assessment(row)
            assessment.items = self._get_items(assessment.id)
            assessment.payment_schedules = self._get_schedules(assessment.id)
            return assessment

    def update(self, assessment: Assessment) -> bool:
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                UPDATE assessments SET
                    enrollment_id = %s, academic_year = %s, semester = %s, assessment_date = %s,
                    status = %s, total_assessed = %s, total_discounts = %s, total_payments = %s,
                    outstanding_balance = %s, notes = %s, updated_by = %s
                WHERE id = %s
                """,
                (
                    assessment.enrollment_id,
                    assessment.academic_year,
                    assessment.semester,
                    assessment.assessment_date,
                    assessment.status.value if isinstance(assessment.status, AssessmentStatus) else assessment.status,
                    assessment.total_assessed,
                    assessment.total_discounts,
                    assessment.total_payments,
                    assessment.outstanding_balance,
                    assessment.notes,
                    assessment.updated_by,
                    assessment.id,
                ),
            )
            return cursor.rowcount > 0

    def _row_to_assessment(self, row: dict) -> Assessment:
        return Assessment(
            id=row["id"],
            student_id=row["student_id"],
            enrollment_id=row["enrollment_id"],
            academic_year=row["academic_year"],
            semester=row["semester"],
            assessment_date=row["assessment_date"],
            status=AssessmentStatus(row["status"]),
            total_assessed=Decimal(str(row["total_assessed"])),
            total_discounts=Decimal(str(row["total_discounts"])),
            total_payments=Decimal(str(row["total_payments"])),
            outstanding_balance=Decimal(str(row["outstanding_balance"])),
            notes=row["notes"],
            created_by=row["created_by"],
            updated_by=row["updated_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _get_items(self, assessment_id: int) -> List[AssessmentItem]:
        with get_cursor() as cursor:
            cursor.execute(
                """
                SELECT ai.*, ac.category_code, ac.category_name, ac.is_fee, ac.is_discount
                FROM assessment_items ai
                JOIN assessment_categories ac ON ai.category_id = ac.id
                WHERE ai.assessment_id = %s
                ORDER BY ac.display_order
                """,
                (assessment_id,),
            )
            items = []
            for row in cursor.fetchall():
                category = AssessmentCategory(
                    id=row["category_id"],
                    category_code=row["category_code"],
                    category_name=row["category_name"],
                    is_fee=row["is_fee"],
                    is_discount=row["is_discount"],
                )
                item = AssessmentItem(
                    id=row["id"],
                    assessment_id=row["assessment_id"],
                    category_id=row["category_id"],
                    description=row["description"],
                    original_amount_text=row["original_amount_text"],
                    amount=Decimal(str(row["amount"])),
                    is_verified=row["is_verified"],
                    verified_by=row["verified_by"],
                    verified_at=row["verified_at"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    category=category,
                )
                items.append(item)
            return items

    def _get_schedules(self, assessment_id: int) -> List[PaymentSchedule]:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM payment_schedules WHERE assessment_id = %s ORDER BY due_date",
                (assessment_id,),
            )
            return [
                PaymentSchedule(
                    id=row["id"],
                    assessment_id=row["assessment_id"],
                    schedule_type=PaymentScheduleType(row["schedule_type"]),
                    due_date=row["due_date"],
                    amount_due=Decimal(str(row["amount_due"])),
                    amount_paid=Decimal(str(row["amount_paid"])),
                    status=PaymentScheduleStatus(row["status"]),
                    notes=row["notes"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
                for row in cursor.fetchall()
            ]


class PaymentRepository:
    def create(self, payment: Payment) -> Optional[int]:
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO payments (assessment_id, payment_date, amount, payment_method, reference_number, received_by, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    payment.assessment_id,
                    payment.payment_date,
                    payment.amount,
                    payment.payment_method.value if isinstance(payment.payment_method, PaymentMethod) else payment.payment_method,
                    payment.reference_number,
                    payment.received_by,
                    payment.notes,
                ),
            )
            payment_id = cursor.lastrowid
            
            cursor.execute(
                """
                UPDATE payment_schedules ps
                JOIN (
                    SELECT ps2.id, SUM(p.amount) as total_paid
                    FROM payment_schedules ps2
                    JOIN payments p ON p.assessment_id = ps2.assessment_id
                    WHERE ps2.assessment_id = %s AND p.is_voided = FALSE
                    GROUP BY ps2.id
                ) paid ON ps.id = paid.id
                SET ps.amount_paid = paid.total_paid,
                    ps.status = CASE
                        WHEN paid.total_paid >= ps.amount_due THEN 'paid'
                        WHEN paid.total_paid > 0 THEN 'partial'
                        ELSE 'pending'
                    END
                WHERE ps.assessment_id = %s
                """,
                (payment.assessment_id, payment.assessment_id),
            )
            
            cursor.execute(
                """
                UPDATE assessments a
                JOIN (
                    SELECT assessment_id, SUM(amount) as total_paid
                    FROM payments
                    WHERE assessment_id = %s AND is_voided = FALSE
                    GROUP BY assessment_id
                ) paid ON a.id = paid.assessment_id
                SET a.total_payments = paid.total_paid,
                    a.outstanding_balance = a.total_assessed - a.total_discounts - paid.total_paid,
                    a.status = CASE
                        WHEN paid.total_paid >= a.total_assessed - a.total_discounts THEN 'paid'
                        WHEN paid.total_paid > 0 THEN 'partial'
                        ELSE a.status
                    END
                WHERE a.id = %s
                """,
                (payment.assessment_id, payment.assessment_id),
            )
            
            return payment_id

    def get_by_assessment(self, assessment_id: int) -> List[Payment]:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM payments WHERE assessment_id = %s AND is_voided = FALSE ORDER BY payment_date DESC",
                (assessment_id,),
            )
            return [self._row_to_payment(row) for row in cursor.fetchall()]

    def void(self, payment_id: int, voided_by: int, void_reason: str) -> bool:
        with get_cursor(commit=True) as cursor:
            cursor.execute("SELECT assessment_id, amount FROM payments WHERE id = %s", (payment_id,))
            payment = cursor.fetchone()
            if not payment:
                return False
            
            cursor.execute(
                "UPDATE payments SET is_voided = TRUE, voided_by = %s, voided_at = CURRENT_TIMESTAMP, void_reason = %s WHERE id = %s",
                (voided_by, void_reason, payment_id),
            )
            
            cursor.execute(
                """
                UPDATE payment_schedules ps
                JOIN (
                    SELECT ps2.id, SUM(p.amount) as total_paid
                    FROM payment_schedules ps2
                    JOIN payments p ON p.assessment_id = ps2.assessment_id
                    WHERE ps2.assessment_id = %s AND p.is_voided = FALSE
                    GROUP BY ps2.id
                ) paid ON ps.id = paid.id
                SET ps.amount_paid = COALESCE(paid.total_paid, 0),
                    ps.status = CASE
                        WHEN COALESCE(paid.total_paid, 0) >= ps.amount_due THEN 'paid'
                        WHEN COALESCE(paid.total_paid, 0) > 0 THEN 'partial'
                        ELSE 'pending'
                    END
                WHERE ps.assessment_id = %s
                """,
                (payment["assessment_id"], payment["assessment_id"]),
            )
            
            cursor.execute(
                """
                UPDATE assessments a
                JOIN (
                    SELECT assessment_id, SUM(amount) as total_paid
                    FROM payments
                    WHERE assessment_id = %s AND is_voided = FALSE
                    GROUP BY assessment_id
                ) paid ON a.id = paid.assessment_id
                SET a.total_payments = COALESCE(paid.total_paid, 0),
                    a.outstanding_balance = a.total_assessed - a.total_discounts - COALESCE(paid.total_paid, 0),
                    a.status = CASE
                        WHEN COALESCE(paid.total_paid, 0) >= a.total_assessed - a.total_discounts THEN 'paid'
                        WHEN COALESCE(paid.total_paid, 0) > 0 THEN 'partial'
                        ELSE a.status
                    END
                WHERE a.id = %s
                """,
                (payment["assessment_id"], payment["assessment_id"]),
            )
            
            return cursor.rowcount > 0

    def _row_to_payment(self, row: dict) -> Payment:
        return Payment(
            id=row["id"],
            assessment_id=row["assessment_id"],
            payment_date=row["payment_date"],
            amount=Decimal(str(row["amount"])),
            payment_method=PaymentMethod(row["payment_method"]),
            reference_number=row["reference_number"],
            received_by=row["received_by"],
            notes=row["notes"],
            is_voided=row["is_voided"],
            voided_by=row["voided_by"],
            voided_at=row["voided_at"],
            void_reason=row["void_reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
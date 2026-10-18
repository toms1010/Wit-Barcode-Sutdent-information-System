from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional, List
from enum import Enum
from decimal import Decimal


class AssessmentStatus(str, Enum):
    DRAFT = "draft"
    FINALIZED = "finalized"
    PAID = "paid"
    PARTIAL = "partial"
    CANCELLED = "cancelled"


class PaymentMethod(str, Enum):
    CASH = "cash"
    CHECK = "check"
    BANK_TRANSFER = "bank_transfer"
    ONLINE = "online"
    CARD = "card"
    OTHER = "other"


class PaymentScheduleType(str, Enum):
    DOWN_PAYMENT = "down_payment"
    MIDTERM = "midterm"
    PREFINAL = "prefinal"
    FINAL = "final"
    CUSTOM = "custom"


class PaymentScheduleStatus(str, Enum):
    PENDING = "pending"
    PARTIAL = "partial"
    PAID = "paid"
    OVERDUE = "overdue"
    WAIVED = "waived"


@dataclass
class AssessmentCategory:
    id: Optional[int] = None
    category_code: str = ""
    category_name: str = ""
    description: Optional[str] = None
    is_fee: bool = True
    is_discount: bool = False
    display_order: int = 0
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class AssessmentItem:
    id: Optional[int] = None
    assessment_id: int = 0
    category_id: int = 0
    description: Optional[str] = None
    original_amount_text: Optional[str] = None
    amount: Decimal = Decimal("0.00")
    is_verified: bool = False
    verified_by: Optional[int] = None
    verified_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    category: Optional[AssessmentCategory] = None


@dataclass
class Assessment:
    id: Optional[int] = None
    student_id: int = 0
    enrollment_id: Optional[int] = None
    academic_year: str = ""
    semester: str = ""
    assessment_date: date = field(default_factory=date.today)
    status: AssessmentStatus = AssessmentStatus.DRAFT
    total_assessed: Decimal = Decimal("0.00")
    total_discounts: Decimal = Decimal("0.00")
    total_payments: Decimal = Decimal("0.00")
    outstanding_balance: Decimal = Decimal("0.00")
    notes: Optional[str] = None
    created_by: Optional[int] = None
    updated_by: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    items: List[AssessmentItem] = field(default_factory=list)
    payment_schedules: List["PaymentSchedule"] = field(default_factory=list)

    def recalculate_totals(self):
        """Recompute the header totals from this assessment's own line items.

        An item with no ``category`` attached counts as a fee, which is the same
        rule :meth:`to_dict` uses when reporting items. It matters because the
        totals used to come out as ₱0.00 whenever items were built from a
        ``category_id`` alone -- as :meth:`create_default_assessment` did --
        and a certificate that reports nothing assessed looks identical to a
        student who genuinely owes nothing.
        """
        def is_fee(item):
            return True if item.category is None else bool(
                item.category.is_fee and not item.category.is_discount
            )

        def is_discount(item):
            return bool(item.category and item.category.is_discount)

        self.total_assessed = sum(item.amount for item in self.items if is_fee(item))
        self.total_discounts = sum(
            item.amount for item in self.items if is_discount(item)
        )
        self.total_payments = sum(schedule.amount_paid for schedule in self.payment_schedules)
        self.outstanding_balance = self.total_assessed - self.total_discounts - self.total_payments

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "student_id": self.student_id,
            "enrollment_id": self.enrollment_id,
            "academic_year": self.academic_year,
            "semester": self.semester,
            "assessment_date": self.assessment_date.isoformat() if self.assessment_date else None,
            "status": self.status.value if isinstance(self.status, AssessmentStatus) else self.status,
            "total_assessed": float(self.total_assessed),
            "total_discounts": float(self.total_discounts),
            "total_payments": float(self.total_payments),
            "outstanding_balance": float(self.outstanding_balance),
            "notes": self.notes,
            "items": [
                {
                    "id": item.id,
                    "category_id": item.category_id,
                    "category_code": item.category.category_code if item.category else None,
                    "category_name": item.category.category_name if item.category else None,
                    "description": item.description,
                    "original_amount_text": item.original_amount_text,
                    "amount": float(item.amount),
                    "is_verified": item.is_verified,
                    "is_fee": item.category.is_fee if item.category else True,
                    "is_discount": item.category.is_discount if item.category else False,
                }
                for item in self.items
            ],
            "payment_schedules": [
                {
                    "id": ps.id,
                    "schedule_type": ps.schedule_type.value if isinstance(ps.schedule_type, PaymentScheduleType) else ps.schedule_type,
                    "due_date": ps.due_date.isoformat() if ps.due_date else None,
                    "amount_due": float(ps.amount_due),
                    "amount_paid": float(ps.amount_paid),
                    "status": ps.status.value if isinstance(ps.status, PaymentScheduleStatus) else ps.status,
                }
                for ps in self.payment_schedules
            ],
        }


@dataclass
class Payment:
    id: Optional[int] = None
    assessment_id: int = 0
    payment_date: date = field(default_factory=date.today)
    amount: Decimal = Decimal("0.00")
    payment_method: PaymentMethod = PaymentMethod.CASH
    reference_number: Optional[str] = None
    received_by: Optional[int] = None
    notes: Optional[str] = None
    is_voided: bool = False
    voided_by: Optional[int] = None
    voided_at: Optional[datetime] = None
    void_reason: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class PaymentSchedule:
    id: Optional[int] = None
    assessment_id: int = 0
    schedule_type: PaymentScheduleType = PaymentScheduleType.DOWN_PAYMENT
    due_date: date = field(default_factory=date.today)
    amount_due: Decimal = Decimal("0.00")
    amount_paid: Decimal = Decimal("0.00")
    status: PaymentScheduleStatus = PaymentScheduleStatus.PENDING
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
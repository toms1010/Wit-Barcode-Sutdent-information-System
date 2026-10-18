"""Aggregation of the post-scan student record: COM rows and financial totals.

Everything the scan modal shows about a student is derived here from the
records the repositories return -- never from constants baked into a widget.
The module is deliberately free of Qt so the arithmetic (subject count, total
units, total lab fees, assessed vs. paid vs. outstanding) can be tested without
a running application.

It also refuses to paper over disagreement. When the stored items, the stored
totals, the stored installment schedule and each other do not add up, the
difference is returned as a human readable :attr:`FinancialSummary.discrepancies`
entry instead of being silently reconciled.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable, List, Optional, Sequence

from models.assessment import (
    Assessment,
    AssessmentItem,
    PaymentSchedule,
    PaymentScheduleStatus,
    PaymentScheduleType,
)
from models.enrollment import Enrollment, EnrollmentSubject, SubjectStatus

logger = logging.getLogger(__name__)

#: Schedule rows whose type the COM renders in a fixed order.
INSTALLMENT_ORDER = (
    PaymentScheduleType.DOWN_PAYMENT,
    PaymentScheduleType.MIDTERM,
    PaymentScheduleType.PREFINAL,
    PaymentScheduleType.FINAL,
)

INSTALLMENT_LABELS = {
    PaymentScheduleType.DOWN_PAYMENT: "Down Payment",
    PaymentScheduleType.MIDTERM: "Midterm",
    PaymentScheduleType.PREFINAL: "Prefinal",
    PaymentScheduleType.FINAL: "Final",
    PaymentScheduleType.CUSTOM: "Other",
}

#: Fallback label for a schedule type read as a plain string from the database.
_INSTALLMENT_LABELS_BY_VALUE = {t.value: label for t, label in INSTALLMENT_LABELS.items()}

TUITION_CATEGORY_CODE = "tuition_fee"
LABORATORY_CATEGORY_CODE = "laboratory_fee"


def to_decimal(value, default: Decimal = Decimal("0.00")) -> Decimal:
    """Coerce a database or model amount into a :class:`~decimal.Decimal`.

    MySQL hands back ``Decimal`` for ``DECIMAL`` columns but ``float`` once a
    value has been through :class:`EnrollmentSubject`, and ``None`` for a fee
    that was never set. All three have to sum identically.
    """
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        logger.warning("Non-numeric amount %r treated as %s", value, default)
        return default


def format_peso(amount, blank_for_zero: bool = False) -> str:
    """Render an amount as Philippine pesos, e.g. ``₱5,618.59``."""
    value = to_decimal(amount)
    if blank_for_zero and value == 0:
        return "—"
    return f"₱{value:,.2f}"


def _year_level_text(student) -> str:
    return getattr(student.year_level, "value", student.year_level) or ""


def _enrollment_status_of(item: EnrollmentSubject) -> str:
    return getattr(item.status, "value", item.status)


def build_com_rows(enrollment: Optional[Enrollment]) -> List["ComSubjectRow"]:
    """One row per subject that is still enrolled, in subject-code order.

    Subjects that were dropped, completed or failed are excluded: a Certificate
    of Matriculation lists what the student is currently enrolled in, and the
    totals must describe exactly the rows shown.
    """
    if enrollment is None:
        return []

    rows: List[ComSubjectRow] = []
    for item in enrollment.subjects:
        if _enrollment_status_of(item) != SubjectStatus.ENROLLED.value:
            continue
        subject = item.subject
        rows.append(
            ComSubjectRow(
                code=subject.subject_code if subject else "",
                subject=subject.subject_name if subject else "",
                units=int(subject.credits) if subject and subject.credits is not None else 0,
                schedule=format_schedule(item.schedule_time, item.schedule_days),
                room=item.room or "",
                lab_fee=to_decimal(item.lab_fee),
                section=item.section or "",
                period=item.period or 0,
                other_fee=to_decimal(item.other_fee),
            )
        )
    rows.sort(key=lambda r: (r.code, r.subject))
    return rows


def format_schedule(schedule_time: Optional[str], schedule_days: Optional[str]) -> str:
    """Join the stored time and day fragments into one printable schedule.

    The database stores them separately (``7-8 PM`` / ``SAT``); the COM prints
    them together. Empty fragments are skipped rather than leaving a dangling
    separator.
    """
    parts = [str(p).strip() for p in (schedule_time, schedule_days) if p and str(p).strip()]
    return " ".join(parts)


@dataclass(frozen=True)
class ComSubjectRow:
    code: str
    subject: str
    units: int
    schedule: str
    room: str
    lab_fee: Decimal
    section: str = ""
    #: Class periods per week. ``0`` when the record does not say; the totals row
    #: adds only what is recorded, so an unrecorded period never becomes ₱0 or a
    #: fabricated count.
    period: int = 0
    other_fee: Decimal = Decimal("0.00")


@dataclass(frozen=True)
class ComSummary:
    """The Enrolled Subjects tab, with totals computed from ``rows``."""

    rows: List[ComSubjectRow] = field(default_factory=list)
    academic_year: str = ""
    semester: str = ""
    #: Why the tab is empty, when it is. ``None`` means "no enrollment record".
    empty_reason: Optional[str] = None

    @property
    def subject_count(self) -> int:
        return len(self.rows)

    @property
    def total_units(self) -> int:
        return sum(row.units for row in self.rows)

    @property
    def total_periods(self) -> int:
        return sum(row.period or 0 for row in self.rows)

    @property
    def total_lab_fee(self) -> Decimal:
        return sum((row.lab_fee for row in self.rows), Decimal("0.00"))

    @property
    def total_other_fee(self) -> Decimal:
        return sum((row.other_fee for row in self.rows), Decimal("0.00"))

    @property
    def available(self) -> bool:
        return bool(self.rows)


def summarize_enrollment(enrollment: Optional[Enrollment]) -> ComSummary:
    """Build the COM summary, distinguishing "no record" from "no subjects"."""
    if enrollment is None:
        return ComSummary(
            rows=[],
            empty_reason=(
                "No enrollment record on file for this student. Subjects cannot be "
                "listed until the registrar records an enrollment."
            ),
        )

    rows = build_com_rows(enrollment)
    reason = None
    if not rows:
        reason = (
            f"This enrollment ({enrollment.academic_year or 'no academic year'}, "
            f"{_semester_text(enrollment) or 'no semester'}) has no subjects in "
            "'enrolled' status."
        )
    return ComSummary(
        rows=rows,
        academic_year=enrollment.academic_year or "",
        semester=_semester_text(enrollment),
        empty_reason=reason,
    )


def _semester_text(enrollment: Enrollment) -> str:
    return getattr(enrollment.semester, "value", enrollment.semester) or ""


# --------------------------------------------------------------------- money --


@dataclass(frozen=True)
class InstallmentRow:
    label: str
    amount: Decimal
    amount_paid: Decimal = Decimal("0.00")
    due_date: Optional[object] = None
    status: str = ""


@dataclass(frozen=True)
class FinancialSummary:
    """The Financial Summary tab.

    ``total_assessed`` is what the student owes in total, ``total_payments`` is
    what has actually been received, and ``outstanding_balance`` is the
    difference. They are kept apart deliberately: collapsing them is how a
    system ends up telling an operator that a balance is clear when nothing has
    been paid.
    """

    available: bool = False
    empty_reason: Optional[str] = None
    tuition_fee: Decimal = Decimal("0.00")
    laboratory_fee: Decimal = Decimal("0.00")
    other_fees: Decimal = Decimal("0.00")
    total_assessed: Decimal = Decimal("0.00")
    total_discounts: Decimal = Decimal("0.00")
    total_payments: Decimal = Decimal("0.00")
    outstanding_balance: Decimal = Decimal("0.00")
    installments: List[InstallmentRow] = field(default_factory=list)
    academic_year: str = ""
    semester: str = ""
    status: str = ""
    discrepancies: List[str] = field(default_factory=list)

    @property
    def installments_total(self) -> Decimal:
        return sum((row.amount for row in self.installments), Decimal("0.00"))

    @property
    def named_fees_total(self) -> Decimal:
        return self.tuition_fee + self.laboratory_fee + self.other_fees


def _category_code(item: AssessmentItem) -> str:
    if item.category is not None and item.category.category_code:
        return item.category.category_code
    if item.category is not None:
        return item.category.category_name or ""
    return ""


def _is_fee_item(item: AssessmentItem) -> bool:
    return bool(item.category and item.category.is_fee and not item.category.is_discount)


def _is_discount_item(item: AssessmentItem) -> bool:
    return bool(item.category and item.category.is_discount)


def _installment_label(schedule: PaymentSchedule) -> str:
    schedule_type = schedule.schedule_type
    if isinstance(schedule_type, PaymentScheduleType):
        return INSTALLMENT_LABELS.get(schedule_type, str(schedule_type.value))
    return _INSTALLMENT_LABELS_BY_VALUE.get(str(schedule_type), str(schedule_type))


def summarize_financials(assessment: Optional[Assessment]) -> FinancialSummary:
    """Split an assessment into the four KPI figures plus its schedule.

    With no assessment the result is explicitly *unavailable*, with a reason.
    Nothing is defaulted to zero and presented as a real balance.
    """
    if assessment is None:
        return FinancialSummary(
            available=False,
            empty_reason=(
                "No assessment record on file. Fees, payments and the balance "
                "cannot be shown until Assessment & Fees has been prepared."
            ),
        )

    tuition = Decimal("0.00")
    lab = Decimal("0.00")
    other = Decimal("0.00")
    item_total = Decimal("0.00")
    discount_total = Decimal("0.00")

    for item in assessment.items:
        amount = to_decimal(item.amount)
        if _is_discount_item(item):
            discount_total += amount
            continue
        if not _is_fee_item(item):
            # Payment items recorded as assessment lines. They are money in, not
            # money owed, so they must not inflate the assessed total.
            continue
        item_total += amount
        code = _category_code(item)
        if code == TUITION_CATEGORY_CODE:
            tuition += amount
        elif code == LABORATORY_CATEGORY_CODE:
            lab += amount
        else:
            other += amount

    installments = [
        InstallmentRow(
            label=_installment_label(schedule),
            amount=to_decimal(schedule.amount_due),
            amount_paid=to_decimal(schedule.amount_paid),
            due_date=schedule.due_date,
            status=getattr(schedule.status, "value", schedule.status) or "",
        )
        for schedule in _sorted_schedules(assessment.payment_schedules)
    ]

    total_assessed = to_decimal(assessment.total_assessed)
    total_discounts = to_decimal(assessment.total_discounts)
    total_payments = to_decimal(assessment.total_payments)
    outstanding = to_decimal(assessment.outstanding_balance)

    summary = FinancialSummary(
        available=True,
        tuition_fee=tuition,
        laboratory_fee=lab,
        other_fees=other,
        total_assessed=total_assessed,
        total_discounts=total_discounts,
        total_payments=total_payments,
        outstanding_balance=outstanding,
        installments=installments,
        academic_year=assessment.academic_year or "",
        semester=getattr(assessment.semester, "value", assessment.semester) or "",
        status=getattr(assessment.status, "value", assessment.status) or "",
    )
    return _with_discrepancies(summary, item_total, discount_total)


# ------------------------------------------------------------- fee lines --
# The printed assessment shows named fee lines rather than one "other fees"
# figure, because a parent reading a bill needs to know which charge is which.
# These groups name the categories the registrar records; anything outside them
# is still shown, under its own category name, so no charge is ever dropped
# merely because it is not one of the familiar ones.

MISCELLANEOUS_CATEGORY_CODE = "miscellaneous_fee"

FEE_LINE_GROUPS: Sequence[tuple[str, tuple[str, ...]]] = (
    ("Tuition Fee", (TUITION_CATEGORY_CODE,)),
    ("Miscellaneous Fee", (MISCELLANEOUS_CATEGORY_CODE,)),
    ("Lab Fee", (LABORATORY_CATEGORY_CODE,)),
    (
        "CDA / Diploma / Yearbook",
        ("cda_fee", "diploma_fee", "yearbook_fee", "photography_fee"),
    ),
    (
        "Alumni / Graduation / Notary",
        ("alumni_fee", "graduation_fee", "notarial_fee"),
    ),
)

_FEE_GROUP_BY_CODE = {
    code: label for label, codes in FEE_LINE_GROUPS for code in codes
}


@dataclass(frozen=True)
class MoneyLine:
    """One labelled money row: a fee line, a discount, or an installment."""

    label: str
    amount: Decimal


@dataclass(frozen=True)
class FeeLines:
    """The assessment broken into the lines a bill is read by."""

    fees: List[MoneyLine] = field(default_factory=list)
    discounts: List[MoneyLine] = field(default_factory=list)
    available: bool = False
    empty_reason: Optional[str] = None

    @property
    def fees_total(self) -> Decimal:
        return sum((line.amount for line in self.fees), Decimal("0.00"))

    @property
    def discounts_total(self) -> Decimal:
        return sum((line.amount for line in self.discounts), Decimal("0.00"))


def _fee_line_order(label: str) -> int:
    """Display order for a fee line, from the group it belongs to.

    The category's own ``display_order`` is not available here: the repository
    hydrates items with only the category id, code, name and the fee/discount
    flags, so a category read back from the database has no order to sort by.
    Grouping order is therefore fixed in :data:`FEE_LINE_GROUPS`, and a line
    outside every group sorts after them by name.
    """
    for index, (group_label, _) in enumerate(FEE_LINE_GROUPS):
        if group_label == label:
            return index
    return len(FEE_LINE_GROUPS)


def summarize_fee_lines(assessment: Optional[Assessment]) -> FeeLines:
    """Group an assessment's line items into displayable fee and credit lines.

    Three rules, each of which exists to keep the printed bill defensible:

    * A category the registrar did not record produces no line. Inventing a
      ``₱0.00`` "CDA / Diploma / Yearbook" row on a bill that never mentioned it
      states that the student was charged nothing for it, which is a claim about
      the record rather than a description of it.
    * A fee category outside :data:`FEE_LINE_GROUPS` keeps its own line, named
      after the category. Grouping is presentation, never a filter.
    * Classification follows ``is_fee`` / ``is_discount`` on the stored category,
      not the line's wording or position. An item the registrar flagged as an
      adjustment fee is shown as one, even though a bill reader might expect to
      find it among the credits.
    """
    if assessment is None:
        return FeeLines(
            available=False,
            empty_reason=(
                "No assessment record on file. The fee breakdown cannot be shown "
                "until Assessment & Fees has been prepared for this student."
            ),
        )

    fee_amounts: dict[str, Decimal] = {}
    discount_amounts: dict[str, Decimal] = {}

    for item in assessment.items:
        amount = to_decimal(item.amount)
        code = _category_code(item)
        if _is_discount_item(item):
            label = _fee_line_label(item, code)
            discount_amounts[label] = discount_amounts.get(label, Decimal("0.00")) + amount
            continue
        if not _is_fee_item(item):
            # Payment lines are money in, not money owed. They belong in the
            # payment record, not in the assessment breakdown.
            continue
        label = _FEE_GROUP_BY_CODE.get(code) or _fee_line_label(item, code)
        fee_amounts[label] = fee_amounts.get(label, Decimal("0.00")) + amount

    return FeeLines(
        fees=_money_lines(fee_amounts),
        discounts=_money_lines(discount_amounts),
        available=True,
    )


def _fee_line_label(item: AssessmentItem, code: str) -> str:
    if item.category is not None and item.category.category_name:
        return item.category.category_name
    return code or "Uncategorised"


def _money_lines(amounts: dict[str, Decimal]) -> List[MoneyLine]:
    return [
        MoneyLine(label=label, amount=amounts[label])
        for label in sorted(amounts, key=lambda name: (_fee_line_order(name), name))
    ]


def _sorted_schedules(schedules: Sequence[PaymentSchedule]) -> List[PaymentSchedule]:
    """Order installments the way a COM does, keeping unknowns last."""

    def key(item: PaymentSchedule):
        schedule_type = item.schedule_type
        if isinstance(schedule_type, PaymentScheduleType):
            try:
                return (INSTALLMENT_ORDER.index(schedule_type), "")
            except ValueError:
                return (len(INSTALLMENT_ORDER), schedule_type.value)
        return (len(INSTALLMENT_ORDER), str(schedule_type))

    return sorted(schedules, key=key)


def _with_discrepancies(
    summary: FinancialSummary,
    item_total: Decimal,
    discount_total: Decimal,
) -> FinancialSummary:
    """Report every disagreement instead of reconciling any of them."""
    notes: List[str] = []

    if item_total != summary.total_assessed:
        notes.append(
            f"Stored line items total {format_peso(item_total)} but the assessment "
            f"header records {format_peso(summary.total_assessed)} as assessed. "
            f"The header value is shown; difference {format_peso(summary.total_assessed - item_total)}."
        )

    if discount_total != summary.total_discounts:
        notes.append(
            f"Discount line items total {format_peso(discount_total)} but the header "
            f"records {format_peso(summary.total_discounts)}."
        )

    expected_balance = summary.total_assessed - summary.total_discounts - summary.total_payments
    if expected_balance != summary.outstanding_balance:
        notes.append(
            f"Assessed minus discounts minus payments is {format_peso(expected_balance)}, "
            f"but the recorded balance is {format_peso(summary.outstanding_balance)}."
        )

    if summary.installments:
        schedule_total = summary.installments_total
        if schedule_total != summary.total_assessed:
            notes.append(
                f"The installment schedule sums to {format_peso(schedule_total)}, which "
                f"differs from the assessed total of {format_peso(summary.total_assessed)} "
                f"by {format_peso(schedule_total - summary.total_assessed)}."
            )
        if summary.total_payments == 0 and schedule_total != summary.outstanding_balance:
            notes.append(
                f"No payments are recorded, yet the installment schedule "
                f"({format_peso(schedule_total)}) does not equal the outstanding balance "
                f"({format_peso(summary.outstanding_balance)})."
            )

    if notes:
        logger.warning(
            "Assessment %s has %d unresolved total mismatch(es).",
            summary.academic_year or "?", len(notes),
        )
    return replace(summary, discrepancies=notes)


def verify_com_totals(rows: Iterable[ComSubjectRow]) -> "ComTotalsCheck":
    """Recompute the totals of a set of rows for external comparison.

    Used by the completion report and the tests to state plainly what the
    database produced rather than what a document claimed.
    """
    rows = list(rows)
    total = sum((row.lab_fee for row in rows), Decimal("0.00"))
    return ComTotalsCheck(
        subject_count=len(rows),
        total_units=sum(row.units for row in rows),
        total_lab_fee=total,
    )


@dataclass(frozen=True)
class ComTotalsCheck:
    subject_count: int
    total_units: int
    total_lab_fee: Decimal


# ------------------------------------------------------------- wording --
# Labels shared by the dashboard and the printed document, so the two can never
# describe the same record in two different ways.


#: Registry wording for ``students.enrollment_status``. The stored value is a
#: lifecycle state (``active``); a registrar-facing document calls the same state
#: "Enrolled", which is why the mapping lives here rather than in either caller.
ENROLLMENT_STATUS_LABELS = {
    "active": "Enrolled",
    "inactive": "Inactive",
    "graduated": "Graduated",
    "dropped": "Dropped",
    "on_leave": "On Leave",
}


def enrollment_status_label(value) -> str:
    """Human label for an enrollment status, falling back to the stored value."""
    text = getattr(value, "value", value) or ""
    return ENROLLMENT_STATUS_LABELS.get(str(text).lower(), str(text).replace("_", " ").title() or "—")


def program_and_year(student) -> str:
    """``BS Computer Engineering – 3rd Year``-style text from the record itself.

    The degree prefix is taken from the stored program only when it already
    carries one: no abbreviation is invented for a program the registrar typed
    as, say, "Computer Engineering".
    """
    program = (getattr(student, "program", "") or "").strip()
    year = _year_level_text(student)
    if program and year:
        return f"{program} – {year}"
    return program or year


#: Installment states a schedule row can be shown as, with the tone the UI and the
#: document use for it.
INSTALLMENT_STATES = {
    "paid": "Paid",
    "partial": "Partly Paid",
    "overdue": "Overdue",
    "pending": "Pending",
    "waived": "Waived",
}


def installment_state(row: InstallmentRow, today=None) -> tuple[str, str]:
    """Return ``(label, tone)`` for one installment, from the money not the flag.

    The stored ``status`` is a convenience column maintained alongside payments,
    so it is used only where payments cannot contradict it. An installment is
    called *Paid* only when recorded payments cover it; anything short of that is
    reported as partly paid or pending, and *Overdue* requires a due date that
    has actually passed. That ordering is what stops a schedule from claiming a
    payment record does not support.
    """
    amount = to_decimal(row.amount)
    paid = to_decimal(row.amount_paid)
    stored = (row.status or "").lower()

    if stored == PaymentScheduleStatus.WAIVED.value:
        return INSTALLMENT_STATES["waived"], "neutral"
    if amount <= 0 and paid <= 0:
        return INSTALLMENT_STATES["pending"], "neutral"
    if paid >= amount and paid > 0:
        return INSTALLMENT_STATES["paid"], "success"
    if paid > 0:
        return INSTALLMENT_STATES["partial"], "warning"

    due = _coerce_date(row.due_date)
    if due is not None and due < (today or date.today()):
        return INSTALLMENT_STATES["overdue"], "error"
    return INSTALLMENT_STATES["pending"], "neutral"


def _coerce_date(value) -> Optional[date]:
    """Return a :class:`datetime.date` for a stored due date, or ``None``.

    MySQL returns ``date`` for a ``DATE`` column but ``datetime`` when the value
    comes through a timestamp column, and a string when it arrives as text.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
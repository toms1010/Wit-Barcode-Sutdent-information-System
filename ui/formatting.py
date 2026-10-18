"""One date and money format for the whole interface.

``datetime.strftime('%B')`` follows the process locale, and Qt initialises that
locale from the desktop settings: on this machine ``en_PH`` produced
"Oktubre 17, 2026" in the enrollment header while every printed document said
"October 17, 2026". An operator reading the screen and the certificate side by
side should see the same date spelled the same way, so the month names are
written out here rather than delegated to the locale.

Money formatting lives in :mod:`services.com_summary` and is re-exported so a
widget imports both from one place.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from services.com_summary import format_peso

#: Shown where a value is not recorded. Distinct from a zero: "no fee recorded"
#: is not "no fee".
NOT_RECORDED = "—"

MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
MONTHS_SHORT = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def _as_date(value) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def format_date(value, *, with_year: bool = True) -> str:
    """``Oct 17, 2026`` (or ``Oct 17``), or ``—`` when nothing is recorded."""
    day = _as_date(value)
    if day is None:
        return NOT_RECORDED
    text = f"{MONTHS_SHORT[day.month - 1]} {day.day}"
    return f"{text}, {day.year}" if with_year else text


def format_date_long(value) -> str:
    """``October 17, 2026``, or ``—`` when nothing is recorded."""
    day = _as_date(value)
    if day is None:
        return NOT_RECORDED
    return f"{MONTHS[day.month - 1]} {day.day}, {day.year}"


def format_datetime(value) -> str:
    """``Oct 17, 2026 21:33`` for a timestamp, or ``—``."""
    if value is None:
        return NOT_RECORDED
    if isinstance(value, datetime):
        return f"{format_date(value)}, {value.strftime('%H:%M')}"
    day = _as_date(value)
    return format_date(day) if day else NOT_RECORDED


def money(amount, *, blank_for_zero: bool = False) -> str:
    """Peso formatting, delegated to the shared service helper."""
    return format_peso(amount, blank_for_zero=blank_for_zero)


__all__ = [
    "MONTHS",
    "MONTHS_SHORT",
    "NOT_RECORDED",
    "format_date",
    "format_date_long",
    "format_datetime",
    "format_peso",
    "money",
]
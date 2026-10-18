"""A small status chip used for enrollment and installment states.

Status is the one thing on these screens that must be read instantly, so it is
never rendered as plain text in a column of numbers: a tinted chip carries the
same word with a colour that survives a glance. The tints come in pairs from
``styles.colors`` and were picked to keep the text at or above 4.5:1 against its
own background.

The chip displays wording, never a raw database value -- ``active`` is shown as
"ENROLLED" through :func:`services.com_summary.enrollment_status_label`, so the
interface and the printed document say the same thing about the same record.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSizePolicy

from styles.colors import Colors
from styles.typography import small_label_font
from services.com_summary import enrollment_status_label

#: Enrollment status -> (text colour, background). Keys are the stored
#: ``students.enrollment_status`` values.
_STATUS_TONES = {
    "active": (Colors.SUCCESS_GREEN, Colors.SUCCESS_BG),
    "graduated": (Colors.INFO_BLUE, Colors.INFO_BG),
    "inactive": (Colors.TEXT_SECONDARY, Colors.NEUTRAL_BG),
    "dropped": (Colors.ERROR_RED, Colors.ERROR_BG),
    "on_leave": (Colors.WARNING_ORANGE, Colors.WARNING_BG),
}

#: Tone keys used by :func:`services.com_summary.installment_state`.
_STATE_TONES = {
    "success": (Colors.SUCCESS_GREEN, Colors.SUCCESS_BG),
    "warning": (Colors.WARNING_ORANGE, Colors.WARNING_BG),
    "error": (Colors.ERROR_RED, Colors.ERROR_BG),
    "neutral": (Colors.TEXT_SECONDARY, Colors.NEUTRAL_BG),
}


class StatusPill(QLabel):
    """A tinted, self-sizing chip. Hidden entirely when there is no status."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFont(small_label_font(10))
        self.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        self.setVisible(False)

    def set_tone(self, text: str, tone: tuple[str, str]) -> None:
        foreground, background = tone
        self.setText(text.upper())
        self.setStyleSheet(
            f"color: {foreground};"
            f"background-color: {background};"
            "border-radius: 10px;"
            "padding: 3px 10px;"
            "font-weight: 600;"
        )
        self.setVisible(True)

    def set_enrollment_status(self, value) -> None:
        """Show a student's enrollment status, or hide the chip if unset."""
        key = str(getattr(value, "value", value) or "").lower()
        if not key:
            self.setVisible(False)
            self.clear()
            return
        self.set_tone(enrollment_status_label(key), _STATUS_TONES.get(key, _STATE_TONES["neutral"]))

    def set_state(self, text: str, tone_key: str = "neutral") -> None:
        """Show an arbitrary labelled state (installment status, assessment status)."""
        self.set_tone(text, _STATE_TONES.get(tone_key, _STATE_TONES["neutral"]))


__all__ = ["StatusPill"]
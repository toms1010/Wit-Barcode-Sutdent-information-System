"""The student profile card, shared by every screen that shows a student.

Three surfaces used to build their own version of the same thing — the
verification popup's brief card, the certificate page's profile section, and the
scanner's student panel — which is how they drifted: one showed a photograph,
one showed an initials disc, and one spelled the reference number differently.

This component is that one place. It takes the verified record and renders:

* the photograph, through :mod:`ui.widgets.profile_image` and nothing else, so
  the image always follows the database record rather than the screen;
* the full name;
* the program and year level;
* the reference number;

plus any extra lines a caller needs (student ID, barcode payload) and an
optional status pill.

Nothing here writes HTML: the values are set on Qt labels, so a name containing
markup is displayed as text and cannot become markup. The print document does
build HTML, and escapes every one of these values with ``html.escape`` there.

Reassigning a different student repaints the photograph unconditionally, so the
previous student's face is never left on screen when the record changes.
"""

from __future__ import annotations

from typing import Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from services.com_summary import enrollment_status_label, program_and_year
from styles.colors import Colors
import logging

logger = logging.getLogger(__name__)
from styles.typography import body_bold_font, body_font, section_heading_font, small_label_font
from ui.widgets.profile_image import apply_portrait
from ui.widgets.status_pill import StatusPill


class StudentProfileCard(QFrame):
    """Photograph plus identity, laid out once for the whole application.

    Parameters
    ----------
    title:
        Optional section caption ("STUDENT PROFILE INFORMATION").
    size:
        Photograph edge length in logical pixels. Aspect ratio is preserved and
        the image is centre-cropped by the shared loader.
    show_status:
        Include the enrollment-status pill on its own line.
    detail_label_width:
        Fixed width for the label column, so the values line up in a column
        rather than drifting with the length of the text above them.
    """

    def __init__(
        self,
        title: str = "",
        *,
        size: int = 96,
        show_status: bool = True,
        detail_label_width: int = 150,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("studentProfileCard")
        self._photo_size = size
        self._label_width = detail_label_width
        self._value_labels: dict[str, QLabel] = {}

        self.setStyleSheet(
            f"""
            QFrame#studentProfileCard {{
                background-color: {Colors.SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
            }}
            """
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(20)

        self.photo_label = QLabel()
        self.photo_label.setObjectName("profilePhoto")
        self.photo_label.setFixedSize(size, size)
        self.photo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo_label.setStyleSheet(
            f"border: 1px solid {Colors.BORDER}; border-radius: 10px;"
            f"background-color: {Colors.SURFACE_MUTED};"
        )
        layout.addWidget(self.photo_label)

        details = QVBoxLayout()
        details.setSpacing(10)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("profileHeading")
        self.title_label.setFont(section_heading_font(14))
        self.title_label.setStyleSheet(
            f"color: {Colors.ACCENT_STRONG}; background: transparent;"
        )
        self.title_label.setVisible(bool(title))
        details.addWidget(self.title_label)

        self.name_label = QLabel("—")
        self.name_label.setObjectName("profileFullName")
        self.name_label.setFont(body_bold_font(17))
        self.name_label.setWordWrap(True)
        self.name_label.setStyleSheet(
            f"color: {Colors.TEXT_PRIMARY}; background: transparent;"
        )
        details.addWidget(self.name_label)

        self._rows = QVBoxLayout()
        self._rows.setSpacing(6)
        details.addLayout(self._rows)

        self.status_pill = StatusPill()
        self.status_pill.setVisible(False)
        details.addWidget(self.status_pill, 0, Qt.AlignmentFlag.AlignLeft)

        layout.addLayout(details, 1)
        self._show_status = show_status

    # -- content ---------------------------------------------------------

    def add_detail(self, key: str, label: str) -> QLabel:
        """Add a labelled value row, returning the value label for later use."""
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(12)

        caption = QLabel(label)
        caption.setFont(body_bold_font(12))
        caption.setFixedWidth(self._label_width)
        caption.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; background: transparent;")
        row_layout.addWidget(caption)

        value = QLabel("—")
        value.setObjectName(f"profileDetail_{key}")
        value.setFont(body_font(13))
        value.setWordWrap(True)
        value.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")
        row_layout.addWidget(value, 1)

        self._rows.addWidget(row)
        self._value_labels[key] = value
        return value

    def set_student(self, student, *, extra_details: Sequence[tuple[str, str]] = ()) -> None:
        """Render ``student``: photograph, name, program & year, reference number.

        ``extra_details`` is a sequence of ``(key, label)`` pairs added on the
        fly -- used by surfaces that also show a student ID or a barcode payload.
        """
        for key, label in extra_details:
            if key not in self._value_labels:
                self.add_detail(key, label)

        has_photo = apply_portrait(self.photo_label, student, self._photo_size)
        if not has_photo:
            self.photo_label.setToolTip("No profile photo on file for this student")

        self.name_label.setText(getattr(student, "full_name", "") or "—")

        values = {
            "reference": getattr(student, "reference_number", "") or "—",
            "program": program_and_year(student) or "—",
        }
        for key, _label in extra_details:
            values[key] = self._extra_value(student, key)
        for key, value in values.items():
            label = self._value_labels.get(key)
            if label is not None:
                label.setText(value)

        if self._show_status:
            self.status_pill.set_enrollment_status(
                getattr(student, "enrollment_status", None)
            )

    @staticmethod
    def _extra_value(student, key: str) -> str:
        if key == "student_id":
            return getattr(student, "student_id", "") or "—"
        if key == "barcode":
            return getattr(student, "barcode_payload", "") or "—"
        return getattr(student, key, "") or "—"

    def set_detail(self, key: str, value: str) -> None:
        """Set one rendered value by key.

        Callers that know a key (``"term"``, ``"student_id"``) go through this
        rather than writing to ``_value_labels`` directly, so a card that changes
        its keys cannot leave a surface updating nothing while looking fine.
        """
        label = self._value_labels.get(key)
        if label is None:
            logger.warning("Profile card has no detail named %r", key)
            return
        label.setText(value or "—")

    def clear(self) -> None:
        """Blank the card, including the photograph.

        Called when a surface stops showing a student, so the previous
        student's face is never the last thing left on screen.
        """
        self.photo_label.clear()
        self.photo_label.setToolTip("")
        self.name_label.setText("—")
        for label in self._value_labels.values():
            label.setText("—")
        self.status_pill.setVisible(False)

    def status_text(self) -> str:
        """The displayed status word for this card's student.

        Returns the pill's current text, or the empty string when no student is
        loaded. It previously ignored its surroundings and returned the label for
        an empty value, so a caller asking for this student's status got a
        generic word back.
        """
        if not self.status_pill.isVisible():
            return ""
        return self.status_pill.text()


__all__ = ["StudentProfileCard"]
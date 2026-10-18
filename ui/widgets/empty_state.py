"""Empty, loading and error states as components instead of blank space.

A table with no rows tells an operator nothing: not whether the search matched
nothing, whether the query is still running, or whether the database is down.
Those three look identical on screen unless they are built differently, so this
module makes them different objects:

* :class:`EmptyState` -- there is genuinely nothing to show. It says so, says
  why in one sentence, and offers the action that would change that.
* :class:`LoadingState` -- the data is on its way.
* :func:`error_state` -- the database could not be reached, which is a failure
  with a retry, not an absence of records.

The action button is only constructed when the caller passes one, so a state can
never offer a button wired to nothing, and no placeholder record is invented to
make a screen look populated.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from styles.colors import Colors
from styles.typography import body_font, body_bold_font, button_font

#: Glyphs from the same set the sidebar uses, so the empty state and the
#: navigation speak the same visual language.
STATE_ICONS = {
    "courses": "📖",
    "subjects": "📚",
    "students": "👥",
    "search": "🔍",
    "assessment": "💰",
    "enrollment": "📋",
    "certificate": "📜",
    "document": "🗒️",
    "info": "ℹ️",
    "error": "⚠️",
}


class _IconLabel(QLabel):
    """A glyph rendered in a tinted circle, so it reads as an illustration."""

    def __init__(self, glyph: str, tint: str, parent=None):
        super().__init__(parent)
        self._tint = QColor(tint)
        # The glyph has to be set as the label's *text*. Storing it in an
        # attribute drew the circle and nothing inside it, so every empty state
        # rendered as a blank tinted disc.
        self.setText(glyph)
        self.setFixedSize(56, 56)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._tint.red(), self._tint.green(), self._tint.blue(), 38))
        painter.drawEllipse(0, 0, self.width(), self.height())
        painter.end()
        super().paintEvent(event)


class EmptyState(QFrame):
    """A centred explanation with an optional, already-connected action.

    ``action_text`` and ``action`` are both optional; the button appears only
    when both are supplied, because a visible button that does nothing is worse
    than no button.
    """

    def __init__(
        self,
        heading: str,
        message: str = "",
        *,
        icon: str = "info",
        action_text: str = "",
        action: Optional[Callable[[], None]] = None,
        hint: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("emptyState")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(10)
        layout.setContentsMargins(32, 32, 32, 32)

        self.icon_label = _IconLabel(STATE_ICONS.get(icon, "ℹ️"), Colors.ORANGE)
        icon_row = QHBoxLayout()
        icon_row.addStretch()
        icon_row.addWidget(self.icon_label)
        icon_row.addStretch()
        layout.addLayout(icon_row)

        self.heading_label = QLabel(heading)
        self.heading_label.setFont(body_bold_font(16))
        self.heading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.heading_label.setWordWrap(True)
        self.heading_label.setStyleSheet(f"color: {Colors.DARK_TEXT}; border: none;")
        layout.addWidget(self.heading_label)

        self.message_label = QLabel(message)
        self.message_label.setFont(body_font(13))
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.setWordWrap(True)
        self.message_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT}; border: none;")
        self.message_label.setVisible(bool(message))
        layout.addWidget(self.message_label)

        self.action_btn: Optional[QPushButton] = None
        # The button row exists from the start even when there is no button, so
        # `set_action` later can centre a button in it. Adding the button straight
        # to the root layout instead left it under the icon, off-centre.
        self._button_row = QHBoxLayout()
        self._button_row.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._button_row)
        if action_text and action is not None:
            self._set_action_button(action_text, action)

        self.hint_label = QLabel(hint)
        self.hint_label.setFont(body_font(11))
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet(f"color: {Colors.LIGHT_TEXT}; border: none;")
        self.hint_label.setVisible(bool(hint))
        layout.addWidget(self.hint_label)

    def set_message(self, message: str) -> None:
        self.message_label.setText(message)
        self.message_label.setVisible(bool(message))

    def text(self) -> str:
        """The current message.

        Provided because this component replaces a bare ``QLabel`` on screens that
        addressed their empty state by ``setText``/``text``; callers keep working
        without every call site being rewritten.
        """
        return self.message_label.text()

    def setText(self, message: str) -> None:
        """QLabel-compatible setter, so an existing call site keeps working."""
        self.set_message(message)

    def _set_action_button(self, text: str, action: Callable[[], None]) -> None:
        """Create-or-update the single action button inside the centred row."""
        fresh = self.action_btn is None
        if fresh:
            self.action_btn = QPushButton()
            self._button_row.addStretch()
            self._button_row.addWidget(self.action_btn)
            self._button_row.addStretch()
        self.action_btn.setText(text)
        self.action_btn.setFont(button_font())
        if not fresh:
            # Only a reused button has a connection to drop. Disconnecting a
            # brand-new button raises a libpyside RuntimeWarning because there
            # was nothing connected.
            try:
                self.action_btn.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass
        self.action_btn.clicked.connect(action)
        self.action_btn.setVisible(True)

    def set_action(self, text: str, action: Callable[[], None]) -> None:
        """Show or replace the action button, keeping it connected to ``action``."""
        self._set_action_button(text, action)


class LoadingState(QFrame):
    """A busy state that is visibly different from both empty and failed."""

    def __init__(self, message: str = "Loading…", parent=None):
        super().__init__(parent)
        self.setObjectName("loadingState")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(12)
        layout.setContentsMargins(32, 32, 32, 32)

        self.message_label = QLabel(message)
        self.message_label.setFont(body_font(13))
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT}; border: none;")
        layout.addWidget(self.message_label)

        self.bar = QFrame()
        self.bar.setFixedHeight(4)
        self.bar.setStyleSheet(f"background-color: {Colors.CARD_BORDER}; border-radius: 2px;")
        layout.addWidget(self.bar)

        # A travelling highlight rather than a spinner glyph: it animates with a
        # timer instead of a stylesheet animation, so it is not frozen when the
        # event loop is busy doing the database work it is describing.
        self._highlight = QFrame(self.bar)
        self._highlight.setFixedHeight(4)
        self._highlight.setStyleSheet(
            f"background-color: {Colors.ORANGE}; border-radius: 2px;"
        )
        self._position = 0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._advance)

    def start(self):
        self._timer.start()
        self._paint()

    def stop(self):
        self._timer.stop()

    def _advance(self):
        self._position = (self._position + 8) % max(self.bar.width(), 1)
        self._paint()

    def _paint(self):
        width = max(self.bar.width() // 3, 24)
        self._highlight.setGeometry(self._position, 0, width, self.bar.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._paint()

    def set_message(self, message: str) -> None:
        self.message_label.setText(message)


def error_state(exc: Exception, *, retry: Optional[Callable[[], None]] = None,
                parent: Optional[QWidget] = None) -> EmptyState:
    """A database failure shown as a retryable failure, not as an empty table."""
    state = EmptyState(
        "Cannot reach the database",
        "The records could not be loaded, so nothing below this point is current.",
        icon="error",
        action_text="Try Again" if retry is not None else "",
        action=retry,
        hint=str(exc) if exc else "",
        parent=parent,
    )
    return state


__all__ = ["STATE_ICONS", "EmptyState", "LoadingState", "error_state"]
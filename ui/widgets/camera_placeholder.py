"""The camera viewport's non-live states, as a component instead of a string.

The viewport used to be a ``QLabel`` that held either a live pixmap *or* the
sentence "Camera is off…", swapped by ``setText``/``setPixmap`` on the same
widget. Three things follow from that, and all three were visible on screen:

* **A stale message could survive underneath a live frame.** ``setPixmap`` does
  not clear the text, so the first rendered frame appeared *beside* a sentence
  telling the operator there was no picture.
* **The placeholder could contradict the header.** Nothing tied the words in the
  viewport to whether the scanner was running, so a stopped scanner could show
  "Searching for barcode…" next to a "Stopped" header.
* **"Camera is off" was a bare string in a large empty rectangle.** It read as a
  broken panel rather than as an idle one, and there was no action attached to
  it: the operator was told the camera was off but offered nothing to turn it on
  from where they were looking.

This widget is the placeholder as its own object, so the viewport becomes a
two-page stack (placeholder / live feed) and exactly one of them can be showing.

Motion is used to say something, and both animations are deliberately *quiet*:

* **Standby breathe** while the scanner is stopped. A slow 2.4 s swell of the
  glyph's tint. It reads as "idle, alive, waiting" -- the opposite of a frozen
  panel -- and it is far too slow to be mistaken for progress. It never suggests
  a barcode is being read, because nothing is.
* **Starting sweep** while the camera is opening. A travelling highlight, the
  same idiom :class:`~ui.widgets.empty_state.LoadingState` already uses, driven
  by a timer rather than a stylesheet animation so it keeps moving while the
  worker thread is busy doing the thing it describes.

The error state animates *nothing*. A failure that shimmers reads as "working on
it", and this application cannot retry on its own: a red, still panel with a
retry action is the honest one.

Every timer here is started in ``showEvent`` and stopped in ``hideEvent`` and in
:meth:`dispose`, because a placeholder left ticking after the page is torn down
keeps the Qt event loop alive and repaints into a deleted label.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
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
from styles.typography import body_bold_font, body_font, button_font, small_label_font

logger = logging.getLogger(__name__)

#: One full swell of the standby breathe, in milliseconds. Slow on purpose: this
#: has to read as idle, and anything quicker reads as work in progress.
STANDBY_PERIOD_MS = 2400

#: One pass of the "starting" sweep, in milliseconds.
SWEEP_PERIOD_MS = 1500

#: Frame interval for both animations. 16 ms is roughly 60 fps, which is smooth
#: without asking the compositor for more than the eye can resolve.
TICK_MS = 16

#: Glyph drawn in the disc, per placeholder state.
STATE_GLYPHS = {
    "stopped": "📷",
    "starting": "⏳",
    "error": "⚠️",
}

#: Tint, title, body and whether the state animates, per placeholder state.
#:
#: ``animate`` is False for ``error`` on purpose; see the module docstring.
STATES = {
    "stopped": {
        "tint": Colors.MEDIUM_TEXT,
        "title": "Camera is off",
        "body": "Start the scanner to see the live feed, or upload a card image.",
        "animate": True,
        "motion": "breathe",
    },
    "starting": {
        "tint": Colors.WARNING_ORANGE,
        "title": "Starting the camera…",
        "body": "Opening the camera and loading the detection model.",
        "animate": True,
        "motion": "sweep",
    },
    "error": {
        "tint": Colors.ERROR_RED,
        "title": "The camera could not be started",
        "body": (
            "Check that the camera is connected and not already open in another "
            "application, then start the scanner again."
        ),
        "animate": False,
        "motion": None,
    },
}


class _Disc(QLabel):
    """A glyph in a tinted disc, tinted harder as it breathes."""

    #: Point size of the glyph. The disc is 72 px and the placeholder sits inside
    #: a panel several hundred pixels wide, so an inherited body size rendered a
    #: 12 px camera emoji in the middle of an empty circle and read as a speck
    #: rather than as an illustration.
    GLYPH_PT = 30

    def __init__(self, tint: str, parent=None):
        super().__init__(parent)
        self._tint = QColor(tint)
        #: 0.0 .. 1.0, driven by the owning widget's timer.
        self._intensity = 0.0
        self.setFixedSize(72, 72)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFont(body_bold_font(self.GLYPH_PT))
        self.setStyleSheet("background: transparent; border: none;")

    def set_tint(self, tint: str) -> None:
        self._tint = QColor(tint)
        self.update()

    def set_intensity(self, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        if abs(value - self._intensity) < 0.01:
            return
        self._intensity = value
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        # Swell from a faint tint to a fuller one. The floor stays low so the disc
        # never becomes a solid saturated block that competes with the live
        # feed it is standing in for.
        alpha = int(26 + self._intensity * 34)
        painter.setBrush(QColor(self._tint.red(), self._tint.green(), self._tint.blue(), alpha))
        painter.drawEllipse(self.rect())
        painter.end()
        super().paintEvent(event)


class _SweepBar(QFrame):
    """A travelling highlight, the project's existing "something is happening" idiom."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(4)
        self.setStyleSheet(
            f"background-color: {Colors.CARD_BORDER}; border-radius: 2px;"
        )
        self._highlight = QFrame(self)
        self._highlight.setFixedHeight(4)
        self._highlight.setStyleSheet(
            f"background-color: {Colors.WARNING_ORANGE}; border-radius: 2px;"
        )
        self._position = 0
        self._width = 0

    def set_progress(self, fraction: float) -> None:
        self._position = int((self.width() + self._width) * max(0.0, min(1.0, fraction)))
        self._place()

    def _place(self) -> None:
        span = max(1, self.width() - self._width)
        self._position %= (span + self._width) if self._width else 1
        self._highlight.move(self._position, 0)
        self._highlight.resize(max(1, self._width), self.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._width = max(24, self.width() // 3)
        self._place()

    def reset(self) -> None:
        self._position = 0
        self._place()


class CameraPlaceholder(QFrame):
    """The camera viewport's idle / starting / failed state.

    Parameters
    ----------
    body:
        Overrides the default sentence for the ``stopped`` state only. Each
        other state carries its own copy, so the placeholder can never end up
        describing a starting camera in the words of a stopped one.
    action_text / action:
        An optional action, e.g. "Start Scanner". The button is only built when
        both are supplied, so the placeholder can never show a control wired to
        nothing.
    """

    def __init__(
        self,
        *,
        body: str = "",
        action_text: str = "",
        action: Optional[Callable[[], None]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("cameraPlaceholder")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # Scoped by object name: an unscoped `QFrame { background }` rule is
        # applied by Qt to the children as well, which drew a box around the
        # title, the body and the button separately. See ``ui/widgets/panel.py``.
        self.setStyleSheet(
            f"""
            QFrame#cameraPlaceholder {{
                background-color: {Colors.SURFACE_MUTED};
                border: 1px solid {Colors.CARD_BORDER};
                border-radius: 8px;
            }}
            """
        )

        self._state = "stopped"
        self._phase = 0.0
        self._animation_enabled = True
        #: Per-state sentence overrides supplied by the caller, keyed by state.
        self._bodies: dict = {}
        if body:
            self._bodies["stopped"] = body

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(10)
        layout.setContentsMargins(24, 24, 24, 24)

        self.disc = _Disc(STATES["stopped"]["tint"], self)
        self.disc.setText(STATE_GLYPHS["stopped"])
        self.disc.setFont(self.disc.font())
        disc_row = QHBoxLayout()
        disc_row.addStretch()
        disc_row.addWidget(self.disc)
        disc_row.addStretch()
        layout.addLayout(disc_row)

        self.title_label = QLabel(STATES["stopped"]["title"])
        self.title_label.setFont(body_bold_font(15))
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet(f"color: {Colors.DARK_TEXT}; border: none;")
        layout.addWidget(self.title_label)

        self.body_label = QLabel(body)
        self.body_label.setFont(body_font(13))
        self.body_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body_label.setWordWrap(True)
        self.body_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT}; border: none;")
        layout.addWidget(self.body_label)

        self.action_btn: Optional[QPushButton] = None
        self._action_row = QHBoxLayout()
        self._action_row.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._action_row)
        if action_text and action is not None:
            self.set_action(action_text, action)

        self.sweep_bar = _SweepBar(self)
        layout.addWidget(self.sweep_bar)

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

        self._apply("stopped")

    # ------------------------------------------------------------------ api --

    @property
    def state(self) -> str:
        """The placeholder state currently on screen."""
        return self._state

    def set_state(
        self,
        state: str,
        *,
        title: str = "",
        body: str = "",
        action_text: str = "",
        action: Optional[Callable[[], None]] = None,
    ) -> None:
        """Move to ``state``, which is one of ``stopped`` / ``starting`` / ``error``.

        Unknown states fall back to ``stopped``: an unrecognised state must never
        leave the viewport describing something that is not happening.
        """
        if state not in STATES:
            logger.warning("Unknown camera placeholder state %r; showing stopped", state)
            state = "stopped"
        self._apply(state, title=title, body=body)
        if action_text and action is not None:
            self.set_action(action_text, action)
        elif not action_text:
            self.clear_action()

    def set_action(self, text: str, action: Callable[[], None]) -> None:
        """Create-or-re-label the single action button, keeping it connected.

        Visibility is *not* set here. :meth:`_apply` owns it, because visibility
        is a property of the state -- a "Start Scanner" button has no business on
        screen while the scanner is already starting. Forcing it visible from
        here undid that on every transition.
        """
        fresh = self.action_btn is None
        if fresh:
            self.action_btn = QPushButton()
            self._action_row.addStretch()
            self._action_row.addWidget(self.action_btn)
            self._action_row.addStretch()
        self.action_btn.setText(text)
        self.action_btn.setFont(button_font(13))
        self.action_btn.setMinimumHeight(38)
        self.action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action_btn.setToolTip(f"{text} — opens the scanner controls")
        self.action_btn.setAccessibleName(text)
        if not fresh:
            # Only a reused button has a connection to drop; disconnecting a
            # brand-new one raises a libpyside RuntimeWarning.
            try:
                self.action_btn.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass
        self.action_btn.clicked.connect(action)

    def clear_action(self) -> None:
        if self.action_btn is not None:
            self.action_btn.setVisible(False)

    def set_animation_enabled(self, enabled: bool) -> None:
        """Turn the motion off.

        Exposed rather than hard-wired so the motion can be asserted on and off
        in tests, and so an operator who finds it distracting can have it
        removed without a code change. With it off, every state still reads
        correctly: the tint, the glyph and the copy carry the state on their own.
        """
        self._animation_enabled = bool(enabled)
        if not self._animation_enabled:
            self._phase = 0.0
            self.disc.set_intensity(0.0)
            self.sweep_bar.reset()
        self._sync_timer()

    # ------------------------------------------------------------ internals --

    def _apply(self, state: str, *, title: str = "", body: str = "") -> None:
        spec = STATES[state]
        self._state = state
        self._phase = 0.0

        self.disc.set_tint(spec["tint"])
        self.disc.setText(STATE_GLYPHS[state])
        self.title_label.setText(title or spec["title"])
        # A body supplied for *this* state wins; otherwise the state's own copy.
        # Falling back to the state's default rather than to whatever the previous
        # state said is what stops an error message from still being on screen
        # after a successful restart.
        if body:
            self._bodies[state] = body
        text = self._bodies.get(state) or spec.get("body", "")
        self.body_label.setText(text)
        self.body_label.setVisible(bool(text))
        self.title_label.setAccessibleName(self.title_label.text())

        # The state owns its own recovery action. While starting there is nothing
        # to recover from and a "Start Scanner" button is a lie -- the start has
        # already been pressed -- so the action is hidden for that state alone.
        if self.action_btn is not None:
            self.action_btn.setVisible(state != "starting")

        # The sweep is only meaningful while something is actually starting.
        # Shown for "starting" alone: an error panel that keeps sweeping reads
        # as work in progress, and nothing here is retrying on its own.
        self.sweep_bar.setVisible(
            bool(self._animation_enabled and spec["motion"] == "sweep")
        )
        if self.sweep_bar.isVisible():
            self.sweep_bar.reset()

        self._sync_timer()

    def _sync_timer(self) -> None:
        """Run the timer only when this widget is both eligible and on screen."""
        spec = STATES.get(self._state, {})
        should_run = (
            self._animation_enabled
            and bool(spec.get("animate"))
            and self.isVisible()
        )
        if should_run and not self._timer.isActive():
            self._timer.start()
        elif not should_run and self._timer.isActive():
            self._timer.stop()
            self._phase = 0.0
            self.disc.set_intensity(0.0)
            self.sweep_bar.reset()

    def _tick(self) -> None:
        spec = STATES.get(self._state, {})
        motion = spec.get("motion")
        period = SWEEP_PERIOD_MS if motion == "sweep" else STANDBY_PERIOD_MS
        self._phase = (self._phase + TICK_MS / period) % 1.0

        if motion == "sweep":
            self.sweep_bar.set_progress(self._phase)
            # A gentle companion swell, so the state still reads at a glance
            # even where the sweep is too small to notice.
            self.disc.set_intensity(0.5 - 0.5 * ((self._phase * 2) % 1.0))
            return

        # Breathe: one smooth 0 -> 1 -> 0 swell per period, cosine-shaped so the
        # turnarounds are soft rather than a visible bounce.
        import math

        self.disc.set_intensity(0.5 - 0.5 * math.cos(self._phase * 2 * math.pi))

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_timer()

    def hideEvent(self, event):
        super().hideEvent(event)
        # Stopped here rather than left to run while hidden: a timer on an
        # invisible widget is pure wake-ups, and it is the common case, because
        # the placeholder is hidden for as long as the scanner runs.
        if self._timer.isActive():
            self._timer.stop()
            self._phase = 0.0
            self.disc.set_intensity(0.0)
            self.sweep_bar.reset()

    def dispose(self) -> None:
        """Stop the timer for good. Called from the page's shutdown."""
        if self._timer.isActive():
            self._timer.stop()
        try:
            self._timer.timeout.disconnect(self._tick)
        except (RuntimeError, TypeError):
            pass


__all__ = [
    "STANDBY_PERIOD_MS",
    "STATES",
    "STATE_GLYPHS",
    "SWEEP_PERIOD_MS",
    "CameraPlaceholder",
]

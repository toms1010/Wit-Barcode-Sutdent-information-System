"""A row of buttons that wraps instead of clipping.

Qt has no flow layout, and the alternative to wrapping is the defect this module
exists to remove: a fixed-width action bar whose labels get cut off
("View Full Student Profile" arriving as "View Full Student Profi…"), or a
dialog whose minimum width grows to the sum of five button labels so it can no
longer be shown on a small screen.

The buttons are held in a :class:`QGridLayout` rather than a row of layouts for
a specific reason. A row's *minimum* width is the sum of everything in it, so a
five-button row can never be narrower than all five buttons together -- asking
it to wrap would only push the overflow outside the window. A grid's minimum is
the widest column, so choosing the column count from the available width makes
the minimum and the layout agree, and the buttons wrap without the dialog being
forced wider than the screen it has to fit on.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QGridLayout, QSizePolicy, QWidget


class FlowRow(QWidget):
    """``addWidget`` places buttons left to right, wrapping when they no longer fit.

    Pass ``stretch=True`` for panels that should still fill the row they land in
    (stat cards, for instance): the wrapping decision is then made against each
    widget's *minimum* width rather than its natural one, so a card that would
    happily stretch is still allowed to wrap once stretching cannot help.
    """

    def __init__(self, spacing: int = 10, margins=(0, 0, 0, 0), stretch: bool = False,
                 parent=None):
        super().__init__(parent)
        self._spacing = spacing
        self._items = []
        self._columns = 0
        self._stretch = stretch

        grid = QGridLayout(self)
        grid.setContentsMargins(*margins)
        grid.setHorizontalSpacing(spacing)
        grid.setVerticalSpacing(spacing)
        grid.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
            if not stretch
            else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._grid = grid

        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

    def _desired_width(self, widget) -> int:
        """The width this widget needs before it would have to shrink."""
        if self._stretch:
            return max(1, widget.minimumSizeHint().width())
        return widget.sizeHint().width()

    def addWidget(self, widget) -> None:
        if not self._stretch:
            # Buttons keep their natural width: a QPushButton's minimumSizeHint
            # is narrower than the label it carries, so without this the layout
            # would elide the text instead of moving the button to the next row.
            widget.setMinimumWidth(widget.sizeHint().width())
            widget.setMinimumHeight(widget.sizeHint().height())
            widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._items.append(widget)
        self._relayout()

    def addStretch(self, stretch: int = 0) -> None:
        """Accepted for API symmetry; a wrapping row has no trailing spacer."""
        return None

    def widgets(self) -> list:
        return list(self._items)

    def column_count(self) -> int:
        """How many buttons currently sit side by side."""
        return self._columns

    # -- layout ------------------------------------------------------------

    def _available_width(self) -> int:
        return max(self.width() - self._grid.contentsMargins().left()
                   - self._grid.contentsMargins().right(), 1)

    def _clear(self) -> None:
        while self._grid.count():
            self._grid.takeAt(0)

    def _relayout(self) -> None:
        self._clear()
        if not self._items:
            return

        available = self._available_width()
        widths = [self._desired_width(widget) for widget in self._items]

        columns = 1
        used = widths[0]
        for width in widths[1:]:
            if used + self._spacing + width > available:
                break
            used += self._spacing + width
            columns += 1
        self._columns = columns

        for index, widget in enumerate(self._items):
            row, column = divmod(index, columns)
            self._grid.addWidget(widget, row, column)
            self._grid.setColumnStretch(column, 1 if self._stretch else 0)

        self.setMinimumHeight(self._grid.sizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Deferred: deciding the columns during the resize notification itself
        # measures the previous geometry, which can leave the last button
        # outside the widget.
        QTimer.singleShot(0, self._relayout)


__all__ = ["FlowRow"]
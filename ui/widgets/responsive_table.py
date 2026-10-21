"""Column sizing shared by every data table in the application.

The truncation this module exists to stop (``tudent II`` clipped out of
``Student ID``, ``Ronn...`` out of a name, ``Comp...`` out of a program) was not
caused by short columns alone. It came from asking ``Stretch`` to divide a fixed
width between eight columns: every column then got exactly an eighth of the
viewport, no matter what it contained, so the longest values were always the
ones cut off.

The rule here is the opposite:

* A column that holds variable-length text sizes to its content
  (:attr:`QHeaderView.ResizeMode.ResizeToContents`) and has a floor set from the
  *header* plus a realistic sample of the data, so a long name is never cut off
  even on a small window.
* Only columns explicitly marked flexible share the leftover space, which keeps
  a short ``Room`` column from being padded to the width of a student's name.
* When the content genuinely does not fit -- many columns, a long program name --
  the table scrolls horizontally instead of compressing its columns into
  ellipsised fragments. Scrolling is a reversible inconvenience; a clipped
  reference number on a printed-looking screen is not.
* Long values also carry a tooltip, so the full text is one hover away even when
  a column is user-resized narrower than the data.

Fonts are never shrunk to fit: legibility is the constraint that gives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
)

#: Extra room around a cell's text, matching the padding in ``styles/theme.qss``
#: (``QTableWidget::item { padding: 8px 12px; }``).
CELL_PADDING = 24

#: Value length beyond which a cell also gets a tooltip. Short values fit inside
#: their column, so a tooltip would only be noise.
TOOLTIP_LENGTH = 26


@dataclass(frozen=True)
class ColumnSpec:
    """How one column behaves.

    Attributes
    ----------
    title:
        Header text.
    width:
        Minimum width in logical pixels, derived from the header when ``0``.
        Always honoured, so a column can never be squeezed below the point where
        its own header is unreadable.
    stretch:
        Take part of the leftover horizontal space. At most a couple of columns
        per table should do this.
    align:
        Cell alignment. Money and counts are right- or centre-aligned by
        convention; identifiers stay left-aligned so leading digits line up.
    header_tooltip:
        Optional explanation shown on the header, for columns whose meaning is
        not obvious from the title.
    """

    title: str
    width: int = 0
    stretch: bool = False
    align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    header_tooltip: str = ""

    def resolved_width(self, font_metrics) -> int:
        floor = self.width or font_metrics.horizontalAdvance(self.title)
        return floor + CELL_PADDING


def header_floor(view, title: str) -> int:
    """Width needed for a header plus cell padding, for the widget's font."""
    metrics = view.fontMetrics()
    return metrics.horizontalAdvance(title) + CELL_PADDING


def configure_table(
    table: QTableWidget,
    columns: Sequence[ColumnSpec],
    *,
    row_height: Optional[int] = None,
    horizontal_scroll: bool = True,
) -> QTableWidget:
    """Apply the sizing rules above to ``table`` and return it.

    ``row_height`` defaults to the row height the current font needs, computed at
    runtime rather than hardcoded, so the table stays readable under fractional
    display scaling (125%, 150%) where a fixed pixel height clips descenders.
    """
    metrics = table.fontMetrics()

    table.setColumnCount(len(columns))
    table.setHorizontalHeaderLabels([column.title for column in columns])

    header = table.horizontalHeader()
    header.setStretchLastSection(False)
    # Sections are resized by the modes below; Interactive would otherwise let a
    # stale user-drag width persist across a repopulate.
    header.setSectionsMovable(False)
    header.setHighlightSections(False)

    for index, column in enumerate(columns):
        mode = (
            QHeaderView.ResizeMode.Stretch
            if column.stretch
            else QHeaderView.ResizeMode.ResizeToContents
        )
        header.setSectionResizeMode(index, mode)
        header.resizeSection(index, column.resolved_width(metrics))
        item = table.horizontalHeaderItem(index)
        if item is None:
            continue
        # The header follows its cells' alignment. ``money_column`` and
        # ``count_column`` exist to say "this column is right- or centre-aligned",
        # and that intent was only ever applied to the cells -- so "Lab Fee" sat
        # left-aligned above a column of right-aligned peso figures, and
        # "Credits" left-aligned above centred numbers, on five different pages.
        # A header that does not line up with its column makes the reader check
        # the column twice.
        item.setTextAlignment(column.align)
        if column.header_tooltip:
            item.setToolTip(column.header_tooltip)

    header.setMinimumSectionSize(56)
    if horizontal_scroll:
        table.setHorizontalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    else:
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    table.setWordWrap(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True)
    table.setCornerButtonEnabled(False)
    table.setTextElideMode(Qt.TextElideMode.ElideRight)
    table.setShowGrid(True)

    table.verticalHeader().setVisible(False)
    # Rows keep a constant height: measuring every row's contents on each
    # repopulate is what makes long tables feel sluggish, and no cell in these
    # tables wraps.
    table.verticalHeader().setDefaultSectionSize(row_height or metrics.height() + 18)

    return table


def money_column(title: str, width: int = 116, **kwargs) -> ColumnSpec:
    """A right-aligned peso column.

    Money is right-aligned everywhere in this application so the decimal points
    line up down the column and a misread total is visible at a glance.
    """
    return ColumnSpec(
        title=title,
        width=width,
        align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        **kwargs,
    )


def count_column(title: str, width: int = 84, **kwargs) -> ColumnSpec:
    """A centre-aligned count column (credits, periods, totals)."""
    return ColumnSpec(
        title=title,
        width=width,
        align=Qt.AlignmentFlag.AlignCenter,
        **kwargs,
    )


def fit_height(table: QTableWidget, max_visible_rows: int = 12) -> None:
    """Size a table to its rows, up to a scrollable maximum.

    A table pinned to a default height hides its own totals row behind an inner
    scrollbar -- the row an operator most needs is the one they cannot see. Sizing
    to the content removes the scrollbar entirely for the short tables these
    screens hold (a certificate of matriculation is a handful of subjects), while
    the cap keeps a long list from pushing the rest of the page off screen.
    """
    if table.rowCount() == 0:
        return
    row_height = table.verticalHeader().defaultSectionSize()
    # Before the first layout pass the header has no height yet, and using 0
    # produced a table too short to show its own last row.
    header_height = table.horizontalHeader().height() or (row_height + 12)
    frame = 2 * table.frameWidth()
    wanted = header_height + row_height * table.rowCount() + frame
    cap = header_height + row_height * max_visible_rows + frame
    table.setFixedHeight(min(wanted, cap))


def set_cell(
    table: QTableWidget,
    row: int,
    column: int,
    text: str,
    *,
    align: Optional[Qt.AlignmentFlag] = None,
    tooltip_from: Optional[str] = None,
    family: Optional[str] = None,
) -> QTableWidgetItem:
    """Write one cell, aligning it and attaching a tooltip when it is long.

    ``tooltip_from`` lets a cell show a fuller value than it displays -- a table
    that abbreviates a program name can still offer the complete one on hover.

    ``family`` sets the font family. It is used for the columns whose content
    is a code -- a barcode number, a student id -- where a fixed-pitch font
    keeps the characters aligned and makes a mistyped digit obvious.
    """
    value = "" if text is None else str(text)
    item = QTableWidgetItem(value)
    if align is not None:
        item.setTextAlignment(align)
    if family:
        font = item.font()
        font.setFamily(family)
        item.setFont(font)
    full = tooltip_from if tooltip_from else value
    if len(full) > TOOLTIP_LENGTH:
        item.setToolTip(full)
    table.setItem(row, column, item)
    return item


def fill_table(
    table: QTableWidget,
    rows: Iterable[Sequence[str]],
    columns: Sequence[ColumnSpec],
) -> None:
    """Replace every row of ``table`` with ``rows``, sized and aligned by ``columns``."""
    table.setRowCount(0)
    for values in rows:
        row = table.rowCount()
        table.insertRow(row)
        for column, spec in enumerate(columns):
            value = values[column] if column < len(values) else ""
            set_cell(table, row, column, value, align=spec.align)


__all__ = [
    "CELL_PADDING",
    "ColumnSpec",
    "TOOLTIP_LENGTH",
    "configure_table",
    "count_column",
    "fill_table",
    "fit_height",
    "header_floor",
    "money_column",
    "set_cell",
]
"""Card and bar surfaces, without the cascade side effect.

A container's own ``setStyleSheet("QFrame { border: 1px solid … }")`` is applied
by Qt to the container's *children* as well as to the container, so every label
and field inside the card inherited a border and the panel read as a stack of
nested boxes instead of one card. That is what put a hairline around every
caption in the reports list and every label in the window's top bar.

Every surface in the application is therefore built here, with the rule scoped
to one object name, and the object name is derived from the caller's variable so
two cards on the same page cannot collide.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame

from styles.colors import Colors


def card_frame(
    name: str,
    *,
    background: str = Colors.CARD_BG,
    border: str = Colors.CARD_BORDER,
    radius: int = 12,
    hover_border: str = "",
    property_name: str = "card",
    parent=None,
) -> QFrame:
    """A bordered, rounded surface whose rule applies only to itself."""
    frame = QFrame(parent)
    frame.setObjectName(name)
    if property_name:
        frame.setProperty(property_name, "true")

    rules = [
        f"QFrame#{name} {{"
        f" background-color: {background};"
        f" border: 1px solid {border};"
        f" border-radius: {radius}px;"
        " }"
    ]
    if hover_border:
        rules.append(f"QFrame#{name}:hover {{ border-color: {hover_border}; }}")
    frame.setStyleSheet("\n".join(rules))
    return frame


def bar_frame(
    name: str,
    *,
    background: str,
    border: str,
    border_edge: str = "bottom",
    padding: str = "",
    parent=None,
) -> QFrame:
    """A full-width strip (page header, toolbar, status bar) with one rule.

    ``border_edge`` is the edge that carries the hairline, so the rule cannot
    spread a box around everything the strip contains.
    """
    frame = QFrame(parent)
    frame.setObjectName(name)

    extra = f" padding: {padding};" if padding else ""
    radius = f" border-top-left-radius: {8}px; border-top-right-radius: {8}px;" if border_edge == "none" else ""
    rules = [
        f"QFrame#{name} {{"
        f" background-color: {background};"
        f" border-{border_edge}: 1px solid {border};"
        f"{radius}"
        f"{extra}"
        " }"
    ]
    frame.setStyleSheet("\n".join(rules))
    return frame


__all__ = ["bar_frame", "card_frame"]
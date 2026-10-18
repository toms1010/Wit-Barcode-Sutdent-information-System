"""Shared helpers for reacting to database failures in the UI.

Pages call :func:`show_db_error` from their data-loading boundary so a
connection problem surfaces as an actionable message with an explicit retry,
instead of propagating a raw driver traceback or silently showing an empty
table.

The dialog is deliberately **non-blocking** (``open()`` rather than ``exec()``):
these helpers are frequently reached from widget constructors during startup,
and a nested modal there would deadlock the UI before the main window is shown.

Two details matter for correctness and are easy to get wrong:

* The retry is fired from the dialog's ``finished`` signal, never from
  ``destroyed``. ``destroyed`` also fires when the *parent page* is torn down with
  the dialog still on screen, and the retry then runs against deleted widgets.
* The message shown is the sanitised summary from
  :class:`~database.connection.DatabaseUnavailableError`, not a raw driver
  exception. A driver message can carry the account name, host and connection
  string, which is exactly what must not appear in an operator-facing dialog.
"""

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QWidget

from database.connection import DatabaseUnavailableError, reset_connection_pool

logger = logging.getLogger(__name__)

# Attribute used to keep a single dialog alive per parent and to stop a burst
# of failures (e.g. clicking through several pages) from stacking dialogs.
_ACTIVE_ATTR = "_active_db_error_dialog"


def is_db_error(exc: BaseException) -> bool:
    """True if the exception represents a database connectivity problem."""
    return isinstance(exc, DatabaseUnavailableError)


def _safe_message(exc: BaseException) -> str:
    """An operator-facing description that cannot leak connection details."""
    if isinstance(exc, DatabaseUnavailableError):
        return str(exc)
    # An unexpected exception is described by type only: its message may carry a
    # driver's connection string, and the full text goes to the log.
    return f"The database reported an error ({type(exc).__name__}). See the log for details."


def show_db_error(parent: QWidget, exc: BaseException, retry_callback=None) -> None:
    """Report a database failure and offer a controlled retry.

    Parameters
    ----------
    parent:
        Widget used as the dialog parent.
    exc:
        The :class:`DatabaseUnavailableError` that was raised.
    retry_callback:
        Invoked after the user chooses Retry, following a pool reset so the
        next query performs a genuinely fresh connection attempt. A retry that
        fails again raises its own error, which surfaces as a fresh dialog.
    """
    logger.warning("Database error surfaced to user: %s", exc)

    # If a dialog for this parent is already open, do not stack another one.
    existing = getattr(parent, _ACTIVE_ATTR, None)
    if existing is not None:
        try:
            if existing.isVisible():
                return
        except RuntimeError:
            setattr(parent, _ACTIVE_ATTR, None)

    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle("Database unavailable")
    box.setText("The application could not read or write to the database.")
    box.setInformativeText(_safe_message(exc))

    retry_btn = box.addButton("Retry", QMessageBox.ButtonRole.ActionRole)
    box.addButton(QMessageBox.StandardButton.Close)

    state = {"retry": False}

    def _finished(_result):
        try:
            state["retry"] = box.clickedButton() is retry_btn
        except RuntimeError:
            state["retry"] = False

    box.finished.connect(_finished)

    def _retry_if_requested():
        # Runs from `finished`, so it happens once, whether the operator pressed
        # Retry, pressed Close, or closed the dialog. `destroyed` was the wrong
        # signal: it also fires when the parent page is destroyed with the dialog
        # still open, which retried against deleted widgets.
        setattr(parent, _ACTIVE_ATTR, None)
        if not state["retry"] or retry_callback is None:
            return
        state["retry"] = False
        reset_connection_pool()
        try:
            retry_callback()
        except DatabaseUnavailableError as exc:
            # Still down: the retry raises its own error, which the caller turns
            # into a fresh dialog with the same Retry action. The operator is not
            # left with a log line and nothing on screen.
            logger.warning("Retry did not recover: %s", exc)
            show_db_error(parent, exc, retry_callback=retry_callback)
        except Exception:  # noqa: BLE001 - teardown and callbacks must not raise
            logger.exception("Retry callback failed")

    box.finished.connect(_retry_if_requested)
    # `destroyed` still clears the reference, but fires no work.
    box.destroyed.connect(lambda: setattr(parent, _ACTIVE_ATTR, None))

    # Keep a reference so the dialog is not garbage collected while open.
    setattr(parent, _ACTIVE_ATTR, box)
    box.setWindowModality(Qt.WindowModality.NonModal)
    box.show()  # non-blocking


__all__ = ["is_db_error", "show_db_error"]
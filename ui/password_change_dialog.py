"""Mandatory first-login password change dialog.

Presented immediately after a successful login when the account's
``password_changed_at`` is NULL (i.e. the user must set a new password before
any protected module is accessible). The dialog:

* Verifies the current password against the backend.
* Validates the new password against the complexity policy (length, case,
  digits, symbols, must differ from the current password).
* Persists the new hash and stamps ``password_changed_at`` so the mandatory
  gate is cleared.
* Invalidates the session on success so the user must re-authenticate with the
  new password.

Emits :attr:`password_changed` when the new password has been saved, so the
entry point can proceed to re-authentication and the Dashboard.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from services.auth_service import AuthService, auth_service
from styles.colors import Colors
from styles.typography import body_font, button_font, page_heading_font, small_label_font

import logging

logger = logging.getLogger(__name__)


class PasswordChangeDialog(QDialog):
    """Modal dialog for the mandatory first-login password change."""

    password_changed = Signal(str)  # new_password

    def __init__(self, auth_service: AuthService | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("passwordChangeDialog")
        self.setWindowTitle("Change Password")
        self.setModal(True)
        self.setMinimumSize(460, 420)

        self.auth = auth_service or auth_service
        self._new_password: str | None = None
        self._setup_ui()

    # ------------------------------------------------------------------ layout

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(20)

        # --- Header card ---------------------------------------------------
        header = QFrame()
        header.setObjectName("passwordChangeHeader")
        header.setStyleSheet(
            f"QFrame#passwordChangeHeader {{ "
            f"background-color: {Colors.ACCENT_PALE}; "
            f"border-radius: 12px; }}"
        )
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(24, 24, 24, 24)
        header_layout.setSpacing(8)

        title = QLabel("Set a New Password")
        title.setFont(page_heading_font(20))
        title.setStyleSheet(f"color: {Colors.DARK_BURNT_ORANGE}; font-weight: 700;")
        header_layout.addWidget(title)

        subtitle = QLabel(
            "Your account requires a password change before you can access the "
            "Student Information System. Choose a strong, unique password."
        )
        subtitle.setFont(body_font(12))
        subtitle.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        subtitle.setWordWrap(True)
        header_layout.addWidget(subtitle)

        layout.addWidget(header)

        # --- Form ----------------------------------------------------------
        form = QVBoxLayout()
        form.setSpacing(12)

        self.current_input = QLineEdit()
        self.current_input.setPlaceholderText("Current password")
        self.current_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.current_input.setMinimumHeight(40)
        self.current_input.setToolTip("Enter your current password.")
        form.addLayout(self._labeled("Current password", self.current_input))

        self.new_input = QLineEdit()
        self.new_input.setPlaceholderText("New password")
        self.new_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.new_input.setMinimumHeight(40)
        self.new_input.setToolTip("Enter your new password.")
        form.addLayout(self._labeled("New password", self.new_input))

        self.confirm_input = QLineEdit()
        self.confirm_input.setPlaceholderText("Confirm new password")
        self.confirm_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_input.setMinimumHeight(40)
        self.confirm_input.setToolTip("Confirm your new password.")
        form.addLayout(self._labeled("Confirm new password", self.confirm_input))

        # Inline validation messages (hidden by default).
        self.current_error = QLabel("")
        self.current_error.setProperty("warning", "true")
        self.current_error.setFont(small_label_font(10))
        self.current_error.hide()
        form.addWidget(self.current_error)

        self.new_error = QLabel("")
        self.new_error.setProperty("warning", "true")
        self.new_error.setFont(small_label_font(10))
        self.new_error.hide()
        form.addWidget(self.new_error)

        self.global_error = QLabel("")
        self.global_error.setProperty("warning", "true")
        self.global_error.setFont(small_label_font(11))
        self.global_error.setWordWrap(True)
        self.global_error.hide()
        form.addWidget(self.global_error)

        layout.addLayout(form)
        layout.addStretch()

        # --- Actions -------------------------------------------------------
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Save
        )
        save_btn = self.button_box.button(QDialogButtonBox.StandardButton.Save)
        save_btn.setText("Save & Sign In")
        save_btn.setDefault(True)
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn = self.button_box.button(QDialogButtonBox.StandardButton.Cancel)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button_box.accepted.connect(self._attempt_change)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        # Keyboard: Enter submits from any field.
        self.current_input.returnPressed.connect(
            lambda: self.new_input.setFocus()
        )
        self.new_input.returnPressed.connect(
            lambda: self.confirm_input.setFocus()
        )
        self.confirm_input.returnPressed.connect(self._attempt_change)

        self.current_input.setFocus()

    def _labeled(self, label_text: str, field: QLineEdit) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(4)
        label = QLabel(label_text)
        label.setFont(body_font(11))
        label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        box.addWidget(label)
        box.addWidget(field)
        return box

    # ------------------------------------------------------------------ behavior

    def _clear_messages(self):
        self.global_error.hide()
        self.current_error.hide()
        self.new_error.hide()
        for field in (self.current_input, self.new_input, self.confirm_input):
            field.setStyleSheet("")

    def _show_field_error(self, field: QLineEdit, error_label: QLabel, message: str):
        error_label.setText(message)
        error_label.show()
        field.setStyleSheet(
            f"border: 1px solid {Colors.ERROR_RED}; padding: 7px 12px;"
        )

    def _show_global_error(self, message: str):
        self.global_error.setText(message)
        self.global_error.show()

    def _attempt_change(self):
        if self.auth.current_user is None:
            self.reject()
            return

        self._clear_messages()

        current = self.current_input.text()
        new_password = self.new_input.text()
        confirm = self.confirm_input.text()

        # Frontend validation: report every empty or invalid field at once so
        # the user can fix everything in a single pass. Never call the backend
        # with incomplete input.
        has_error = False
        if not current:
            self._show_field_error(
                self.current_input, self.current_error,
                "Current password is required."
            )
            has_error = True
        if not new_password:
            self._show_field_error(
                self.new_input, self.new_error,
                "New password is required."
            )
            has_error = True
        if not confirm:
            self._show_field_error(
                self.confirm_input, self.new_error,
                "Please confirm your new password."
            )
            has_error = True
        if new_password and confirm and new_password != confirm:
            self._show_field_error(
                self.confirm_input, self.new_error,
                "The confirmation does not match the new password."
            )
            has_error = True
        if has_error:
            if not current:
                self.current_input.setFocus()
            elif not new_password:
                self.new_input.setFocus()
            else:
                self.confirm_input.setFocus()
            return

        user = self.auth.current_user
        if user is None:
            self.reject()
            return

        try:
            result = self.auth.change_password(user.id, current, new_password)
        except Exception as exc:  # noqa: BLE001 - surface unexpected errors safely
            logger.exception("Password change raised an unexpected error")
            self._show_global_error(
                "Password change could not be completed. Try again later."
            )
            self.current_input.setFocus()
            return

        if result.success:
            self._new_password = new_password
            self.password_changed.emit(new_password)
            self.accept()
        else:
            # Surface the error generically without revealing account state.
            self._show_global_error(result.message or "Password change failed.")
            self.current_input.setFocus()

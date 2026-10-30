"""Login page for the WIT Barcode Student Information System.

A self-contained, responsive login form presented inside a centred card. It is
a :class:`PySide6.QtWidgets.QWidget` (not a dialog) so it can be shown either
as a standalone modal dialog at application start or embedded when a session
expires mid-session.

Emits :attr:`login_successful` with the authenticated :class:`models.user.User`
and :attr:`cancelled` when the user closes without signing in.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config.settings import settings
from services.auth_service import AuthService, auth_service
from styles.colors import Colors
from styles.typography import (
    app_title_font,
    body_font,
    button_font,
    page_heading_font,
    section_heading_font,
    small_label_font,
)

import logging

logger = logging.getLogger(__name__)

# Maximum width of the login card; it shrinks on narrow screens.
CARD_MAX_WIDTH = 460
# Internal card padding.
CARD_PADDING = 32


class LoginPage(QWidget):
    """The login form."""

    login_successful = Signal(object)  # User
    cancelled = Signal()

    def __init__(self, auth_service: AuthService | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("loginPage")
        self.auth = auth_service or auth_service
        self._authenticating = False
        self._setup_ui()

    # ------------------------------------------------------------------ layout

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addStretch()

        # Centred card
        self.card = QFrame()
        self.card.setObjectName("loginCard")
        self.card.setProperty("card", "true")
        self.card.setMaximumWidth(CARD_MAX_WIDTH)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(CARD_PADDING, CARD_PADDING, CARD_PADDING, CARD_PADDING)
        card_layout.setSpacing(20)

        # --- Branding ------------------------------------------------------
        brand = QVBoxLayout()
        brand.setSpacing(10)

        self.logo_label = QLabel()
        self.logo_label.setAlignment(Qt.AlignCenter)
        self.logo_label.setFixedSize(80, 80)
        logo_path = settings.LOGO_PATH
        if logo_path.exists():
            pixmap = QPixmap(str(logo_path))
            if not pixmap.isNull():
                self.logo_label.setPixmap(
                    pixmap.scaled(
                        80,
                        80,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                )
        self.logo_label.setStyleSheet("background: transparent;")
        brand.addWidget(self.logo_label, alignment=Qt.AlignCenter)

        app_name = QLabel(settings.APP_NAME)
        app_name.setFont(app_title_font(26))
        app_name.setAlignment(Qt.AlignCenter)
        app_name.setStyleSheet(f"color: {Colors.DARK_BURNT_ORANGE}; font-weight: 700;")
        brand.addWidget(app_name, alignment=Qt.AlignCenter)

        tagline = QLabel(
            "Student Information & Enrolment Management System"
        )
        tagline.setFont(body_font(13))
        tagline.setAlignment(Qt.AlignCenter)
        tagline.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        tagline.setWordWrap(True)
        brand.addWidget(tagline, alignment=Qt.AlignCenter)

        card_layout.addLayout(brand)

        # --- Form ----------------------------------------------------------
        form = QVBoxLayout()
        form.setSpacing(12)

        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Username")
        self.username_input.setMinimumHeight(40)
        self.username_input.setToolTip("Enter your username.")
        form.addLayout(self._labeled("Username", self.username_input))

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Password")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setMinimumHeight(40)
        self.password_input.setToolTip("Enter your password.")
        form.addLayout(self._labeled("Password", self.password_input))

        # Show/hide toggle.
        self.toggle_password = QPushButton("👁")
        self.toggle_password.setProperty("ghost", "true")
        self.toggle_password.setFixedSize(36, 36)
        self.toggle_password.setToolTip("Show/hide password")
        self.toggle_password.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_password.clicked.connect(self._toggle_password_visibility)
        toggle_row = QHBoxLayout()
        toggle_row.addStretch()
        toggle_row.addWidget(self.toggle_password)
        form.addLayout(toggle_row)

        # Inline validation / error messages (hidden by default).
        self.username_error = QLabel("")
        self.username_error.setProperty("warning", "true")
        self.username_error.setFont(small_label_font(10))
        self.username_error.hide()
        form.addWidget(self.username_error)

        self.password_error = QLabel("")
        self.password_error.setProperty("warning", "true")
        self.password_error.setFont(small_label_font(10))
        self.password_error.hide()
        form.addWidget(self.password_error)

        # Global auth error (invalid credentials, locked, service unavailable).
        self.auth_error = QLabel("")
        self.auth_error.setProperty("warning", "true")
        self.auth_error.setFont(small_label_font(11))
        self.auth_error.setWordWrap(True)
        self.auth_error.hide()
        form.addWidget(self.auth_error)

        card_layout.addLayout(form)

        # --- Actions -------------------------------------------------------
        actions = QVBoxLayout()
        actions.setSpacing(10)

        self.sign_in_btn = QPushButton("Sign In")
        self.sign_in_btn.setMinimumHeight(44)
        self.sign_in_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sign_in_btn.setDefault(True)
        self.sign_in_btn.clicked.connect(self._attempt_login)
        actions.addWidget(self.sign_in_btn)

        # Loading indicator shown while authentication is in flight.
        self.loading = QLabel("")
        self.loading.setFont(small_label_font(10))
        self.loading.setAlignment(Qt.AlignCenter)
        self.loading.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        self.loading.hide()
        actions.addWidget(self.loading)

        card_layout.addLayout(actions)

        # --- Footer --------------------------------------------------------
        footer = QVBoxLayout()
        footer.setSpacing(6)
        footer.addStretch()

        version = QLabel(f"v{settings.APP_VERSION}")
        version.setFont(small_label_font(10))
        version.setAlignment(Qt.AlignCenter)
        version.setStyleSheet(f"color: {Colors.LIGHT_TEXT};")
        footer.addWidget(version, alignment=Qt.AlignCenter)

        copyright_ = QLabel(
            f"\u00a9 {settings.INSTITUTION_NAME}. All rights reserved."
        )
        copyright_.setFont(small_label_font(10))
        copyright_.setAlignment(Qt.AlignCenter)
        copyright_.setStyleSheet(f"color: {Colors.LIGHT_TEXT};")
        copyright_.setWordWrap(True)
        footer.addWidget(copyright_, alignment=Qt.AlignCenter)

        card_layout.addLayout(footer)

        outer.addWidget(self.card, alignment=Qt.AlignCenter)
        outer.addStretch()

        # --- Keyboard shortcuts -------------------------------------------
        # Enter submits the form from either field.
        self.username_input.returnPressed.connect(self._focus_password)
        self.password_input.returnPressed.connect(self._attempt_login)

        # Initial focus.
        self.username_input.setFocus()

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

    def _toggle_password_visibility(self):
        if self.password_input.echoMode() == QLineEdit.EchoMode.Password:
            self.password_input.setEchoMode(QLineEdit.EchoMode.Normal)
            self.toggle_password.setText("🙈")
            self.toggle_password.setToolTip("Hide password")
        else:
            self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.toggle_password.setText("👁")
            self.toggle_password.setToolTip("Show password")

    def _focus_password(self):
        self.password_input.setFocus()

    def _clear_messages(self):
        self.auth_error.hide()
        self.username_error.hide()
        self.password_error.hide()
        self.username_input.setStyleSheet("")
        self.password_input.setStyleSheet("")

    def _show_field_error(self, field: QLineEdit, error_label: QLabel, message: str):
        error_label.setText(message)
        error_label.show()
        field.setStyleSheet(
            f"border: 1px solid {Colors.ERROR_RED}; "
            f"padding: 7px 12px;"
        )

    def _show_auth_error(self, message: str):
        self.auth_error.setText(message)
        self.auth_error.show()

    def _set_authenticating(self, value: bool):
        self._authenticating = value
        self.sign_in_btn.setEnabled(not value)
        self.sign_in_btn.setText("Signing in…" if value else "Sign In")
        self.username_input.setEnabled(not value)
        self.password_input.setEnabled(not value)
        self.toggle_password.setEnabled(not value)
        if value:
            self.loading.setText("Verifying your credentials…")
            self.loading.show()
        else:
            self.loading.hide()

    def _attempt_login(self):
        if self._authenticating:
            return
        self._clear_messages()

        username = self.username_input.text().strip()
        password = self.password_input.text()

        # Frontend validation: report every empty required field at once so the
        # user fixes everything in a single pass. Never submit empty credentials.
        has_error = False
        if not username:
            self._show_field_error(
                self.username_input, self.username_error,
                "Username is required."
            )
            has_error = True
        if not password:
            self._show_field_error(
                self.password_input, self.password_error,
                "Password is required."
            )
            has_error = True
        if has_error:
            # Focus the first empty field so the user can correct it immediately.
            if not username:
                self.username_input.setFocus()
            else:
                self.password_input.setFocus()
            return

        self._set_authenticating(True)

        # Authentication is real: delegated to AuthService which queries the
        # database. Run it directly (the DB call is fast; if it ever blocks the
        # UI this can be moved to a QThread without changing the UI contract).
        try:
            result = self.auth.login(username, password)
        except Exception as exc:  # noqa: BLE001 - surface unexpected errors safely
            logger.exception("Login raised an unexpected error")
            self._set_authenticating(False)
            self._show_auth_error(
                "Authentication could not be completed. Try again later."
            )
            return

        self._set_authenticating(False)

        if result.success and result.user is not None:
            logger.info("Login successful for %s", result.user.username)
            self.login_successful.emit(result.user)
        else:
            # Generic message regardless of cause (wrong password, missing
            # account, inactive, locked). Do not enumerate accounts.
            self._show_auth_error(result.message or "Invalid username or password.")
            if result.locked:
                self.password_input.setFocus()
            else:
                self.username_input.setFocus()

    # ------------------------------------------------------------------ sizing

    def sizeHint(self) -> QSize:
        return QSize(640, 720)

    def resizeEvent(self, event):
        # Keep the card centred and at most CARD_MAX_WIDTH regardless of window
        # size, so the form stays readable on tablet/desktop and full-width on
        # small screens.
        super().resizeEvent(event)
        available = min(self.width(), CARD_MAX_WIDTH + 2 * CARD_PADDING)
        self.card.setFixedWidth(max(available - 2 * CARD_PADDING, 300))

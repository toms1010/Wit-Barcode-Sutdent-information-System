"""Modal login dialog shown before the application's main window is accessible.

Wraps :class:`ui.login_page.LoginPage` and exposes the authenticated
:class:`models.user.User` once the user signs in successfully.
"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QVBoxLayout

from services.auth_service import AuthService, auth_service
from ui.login_page import LoginPage


class LoginDialog(QDialog):
    """Blocking login dialog. Returns :data:`QDialog.Accepted` only when the
    user has been authenticated against the backend."""

    def __init__(self, auth_service: AuthService | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("loginDialog")
        self.setWindowTitle("Sign In")
        self.setModal(True)
        self.setMinimumSize(640, 720)

        self.auth = auth_service or auth_service
        self.authenticated_user = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.login_page = LoginPage(auth_service=self.auth, parent=self)
        self.login_page.login_successful.connect(self._on_success)
        self.login_page.cancelled.connect(self.reject)
        layout.addWidget(self.login_page)

        # Size the dialog around the login card.
        self.resize(self.login_page.sizeHint())

    def _on_success(self, user):
        self.authenticated_user = user
        self.accept()

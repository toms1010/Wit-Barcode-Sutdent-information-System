#!/usr/bin/env python3
"""
WIT Barcode Student Information System
Main entry point

Application start-up flow:

1. Apply the theme and fonts.
2. Show the login dialog. The user must authenticate against the backend
   before the main window is ever created.
3. If the authenticated account requires a mandatory first-login password
   change (``password_changed_at`` is NULL), show the password-change dialog
   before granting access to any protected module.
4. After the password change succeeds, the session is invalidated and the user
   re-authenticates with the new password.
5. On successful sign-in, build :class:`ui.main_window.MainWindow` with the
   authenticated user and run the event loop.
6. If the user logs out from the main window, return to the login dialog
   instead of quitting. Closing the main window (or cancelling login) exits.
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from PySide6.QtWidgets import QApplication

from config.logging_config import setup_logging
from config.settings import settings
from services.auth_service import auth_service
from styles import apply_theme
from ui.login_dialog import LoginDialog
from ui.main_window import MainWindow
from ui.password_change_dialog import PasswordChangeDialog


def main():
    # Setup logging first
    logger = setup_logging()
    logger.info("Starting WIT Barcode Student Information System")

    # Create application
    app = QApplication(sys.argv)
    app.setApplicationName(settings.APP_NAME)
    app.setApplicationVersion(settings.APP_VERSION)
    app.setOrganizationName("Western Institute of Technology")

    # Apply theme (loads fonts and stylesheet)
    apply_theme(app)

    # Session loop: login -> [password change] -> main window -> (logout) -> login -> ...
    # A new event loop is entered each time the main window opens; this is a
    # standard desktop pattern and keeps login and the app shell cleanly
    # separated.
    while True:
        login_dialog = LoginDialog(auth_service=auth_service)
        if login_dialog.exec() != 1:  # QDialog.Accepted
            logger.info("Login cancelled; exiting.")
            return 0

        user = login_dialog.authenticated_user
        logger.info("Authenticated user: %s", user)

        # Mandatory first-login password change: the account's
        # ``password_changed_at`` is NULL, so no protected module may be
        # accessed until a new password is set.
        if user.must_change_password:
            change_dialog = PasswordChangeDialog(auth_service=auth_service, parent=None)
            if change_dialog.exec() != 1:  # QDialog.Accepted
                # User cancelled the mandatory password change: return to login
                # rather than granting access to protected modules.
                logger.info(
                    "Password change cancelled for %s; returning to login.",
                    user.username,
                )
                # Invalidate the partial session so the user must log in again.
                auth_service.logout()
                continue

            # Password change succeeded. Re-authenticate with the new password
            # so the session is valid for the dashboard, then open the window
            # directly (no second login prompt).
            new_password = change_dialog._new_password
            if new_password is None:
                logger.error(
                    "Password change reported success but no new password was "
                    "returned; returning to login."
                )
                auth_service.logout()
                continue

            res = auth_service.login(user.username, new_password)
            if not res.success or res.user is None:
                logger.error(
                    "Re-authentication after password change failed for %s.",
                    user.username,
                )
                auth_service.logout()
                continue

            user = res.user
            logger.info(
                "Password changed for %s; session refreshed with new password.",
                user.username,
            )

        # Normal login (password already changed): open the main window.
        logger.info("Authenticated user: %s", user)

        window = MainWindow(current_user=user, auth_service=auth_service)
        window.show()

        # Block until the main window is closed.
        app.exec()

        if getattr(window, "logout_requested", False):
            logger.info("Logout requested; returning to login screen.")
            window.deleteLater()
            continue

        logger.info("Application exited.")
        return 0


if __name__ == "__main__":
    sys.exit(main())

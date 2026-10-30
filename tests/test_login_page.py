"""UI tests for the login page (requirement 11.7, 11.8).

Uses pytest-qt to drive the widget and a mocked AuthService so no real
credentials or database are involved.
"""

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

import pytest

from services.auth_service import AuthService
from ui.login_page import LoginPage


class FakeAuthService(AuthService):
    """Stand-in that lets tests script login outcomes without a database."""

    def __init__(self):
        # Avoid touching the real module-level session or DB.
        self._current_user = None
        self._failures = {}
        self.login_calls = []
        self.logout_calls = 0
        self.result_to_return = None
        self.raise_on_login = None

    def login(self, username, password):
        self.login_calls.append((username, password))
        if self.raise_on_login:
            raise self.raise_on_login
        return self.result_to_return

    def logout(self):
        self.logout_calls += 1
        previous = self._current_user
        self._current_user = None
        return previous

    @property
    def current_user(self):
        return self._current_user

    @property
    def is_authenticated(self):
        return self._current_user is not None

    def require_auth(self):
        if self._current_user is None:
            raise PermissionError("No authenticated session.")
        return self._current_user

    def require_role(self, *roles):
        user = self.require_auth()
        if user.role not in roles:
            raise PermissionError("Forbidden")
        return user


@pytest.fixture
def fake_auth():
    return FakeAuthService()


@pytest.fixture
def page(qtbot, fake_auth):
    widget = LoginPage(auth_service=fake_auth)
    qtbot.addWidget(widget)
    return widget


# --------------------------------------------------------------------------- structure


def test_login_page_has_required_fields(page):
    assert page.username_input is not None
    assert page.password_input is not None
    assert page.sign_in_btn is not None
    assert page.toggle_password is not None
    assert page.username_input.placeholderText() == "Username"
    assert page.password_input.placeholderText() == "Password"
    assert page.password_input.echoMode() == page.password_input.EchoMode.Password


def test_logo_and_branding_visible(page):
    from config.settings import settings
    # App name rendered as the card title.
    title = page.findChild(QApplication.instance().topLevelWidgets()[0].__class__, "") if False else None
    # The app name label is set on the card; verify it exists via text.
    app_name_labels = page.findChildren(QLabel)
    assert any(settings.APP_NAME in (l.text() or "") for l in app_name_labels)
    # Tagline present.
    assert any("Enrolment" in (l.text() or "") for l in app_name_labels)


# --------------------------------------------------------------------------- validation


def test_empty_submission_shows_field_errors(qtbot, page, fake_auth):
    page.sign_in_btn.click()
    qtbot.wait(60)

    assert page.username_error.isHidden() is False
    assert page.password_error.isHidden() is False
    assert "required" in page.username_error.text()
    assert "required" in page.password_error.text()
    # Nothing was sent to the auth service.
    assert fake_auth.login_calls == []


def test_password_only_submission_reports_missing_user(qtbot, page, fake_auth):
    page.password_input.setText("somepassword")
    page.sign_in_btn.click()
    qtbot.wait(60)

    assert page.username_error.isHidden() is False
    assert fake_auth.login_calls == []


# --------------------------------------------------------------------------- show/hide toggle


def test_show_hide_password_toggle(page, qtbot):
    assert page.password_input.echoMode() == page.password_input.EchoMode.Password

    page.toggle_password.click()
    assert page.password_input.echoMode() == page.password_input.EchoMode.Normal

    page.toggle_password.click()
    assert page.password_input.echoMode() == page.password_input.EchoMode.Password


# --------------------------------------------------------------------------- success / failure


def test_successful_login_emits_user(qtbot, page, fake_auth):
    from models.user import User
    user = User(id=1, username="admin", email="admin@wit.edu.ph",
                full_name="System Administrator", role="admin", is_active=True)
    fake_auth.result_to_return = type("Result", (), {
        "success": True, "user": user, "message": "",
        "locked": False, "lockout_remaining": 0,
    })()

    page.username_input.setText("admin")
    page.password_input.setText("correct-password")

    emitted = []
    page.login_successful.connect(lambda u: emitted.append(u))

    page.sign_in_btn.click()
    qtbot.wait(50)

    assert len(emitted) == 1
    assert emitted[0] is user
    assert fake_auth.login_calls == [("admin", "correct-password")]


def test_failed_login_shows_generic_error(qtbot, page, fake_auth):
    fake_auth.result_to_return = type("Result", (), {
        "success": False, "user": None, "message": "Invalid username or password.",
        "locked": False, "lockout_remaining": 0,
    })()

    page.username_input.setText("admin")
    page.password_input.setText("wrong")

    page.sign_in_btn.click()
    qtbot.wait(60)

    assert page.auth_error.isHidden() is False
    assert page.auth_error.text() == "Invalid username or password."
    # Button re-enabled after failure so the user can retry.
    assert page.sign_in_btn.isEnabled()


def test_login_loading_and_completion(qtbot, page, fake_auth):
    from models.user import User
    user = User(id=1, username="admin", email="admin@wit.edu.ph",
                full_name="System Administrator", role="admin", is_active=True)
    fake_auth.result_to_return = type("Result", (), {
        "success": True, "user": user, "message": "",
        "locked": False, "lockout_remaining": 0,
    })()

    # Loading starts hidden.
    assert page.loading.isVisible() is False
    assert page.sign_in_btn.isEnabled()

    page.username_input.setText("admin")
    page.password_input.setText("pw")
    page.sign_in_btn.click()
    qtbot.wait(60)

    # After a fast login completes, loading is hidden and the button is back.
    assert page.loading.isVisible() is False
    assert page.sign_in_btn.isEnabled()


def test_enter_key_submits(qtbot, page, fake_auth):
    from models.user import User
    user = User(id=1, username="admin", email="admin@wit.edu.ph",
                full_name="System Administrator", role="admin", is_active=True)
    fake_auth.result_to_return = type("Result", (), {
        "success": True, "user": user, "message": "",
        "locked": False, "lockout_remaining": 0,
    })()

    page.username_input.setText("admin")
    page.password_input.setText("correct-password")
    emitted = []
    page.login_successful.connect(lambda u: emitted.append(u))

    QTest.keyClick(page.password_input, Qt.Key_Return)
    qtbot.wait(30)

    assert len(emitted) == 1
    assert fake_auth.login_calls == [("admin", "correct-password")]


def test_exception_during_login_shows_safe_error(qtbot, page, fake_auth):
    fake_auth.raise_on_login = RuntimeError("db exploded")

    page.username_input.setText("admin")
    page.password_input.setText("pw")

    page.sign_in_btn.click()
    qtbot.wait(60)

    assert page.auth_error.isHidden() is False
    assert "could not be completed" in page.auth_error.text()
    # Button re-enabled even on unexpected error.
    assert page.sign_in_btn.isEnabled() is True

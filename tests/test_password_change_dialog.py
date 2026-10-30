"""UI tests for the mandatory first-login password change dialog.

Uses pytest-qt to drive the widget and a mocked AuthService so no real
credentials or database are involved.
"""

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox, QPushButton

import pytest

from services.auth_service import AuthService
from ui.password_change_dialog import PasswordChangeDialog


class FakeAuthService(AuthService):
    """Stand-in that lets tests script password-change outcomes."""

    def __init__(self):
        self._current_user = None
        self.change_calls = []
        self.result_to_return = None
        self.raise_on_change = None

    def change_password(self, user_id, old_password, new_password):
        self.change_calls.append((user_id, old_password, new_password))
        if self.raise_on_change:
            raise self.raise_on_change
        return self.result_to_return

    def logout(self):
        previous = self._current_user
        self._current_user = None
        return previous

    @property
    def current_user(self):
        return self._current_user

    @property
    def is_authenticated(self):
        return self._current_user is not None


class FakeUser:
    def __init__(self, id=1, username="adminwit", role="admin"):
        self.id = id
        self.username = username
        self.role = role


@pytest.fixture
def fake_auth():
    return FakeAuthService()


@pytest.fixture
def dialog(qtbot, fake_auth):
    fake_auth._current_user = FakeUser()
    dlg = PasswordChangeDialog(auth_service=fake_auth)
    qtbot.addWidget(dlg)
    return dlg


# --------------------------------------------------------------------------- structure


def test_password_change_dialog_has_required_fields(dialog):
    assert dialog.current_input is not None
    assert dialog.new_input is not None
    assert dialog.confirm_input is not None
    assert dialog.current_input.echoMode() == dialog.current_input.EchoMode.Password
    assert dialog.new_input.echoMode() == dialog.new_input.EchoMode.Password
    assert dialog.confirm_input.echoMode() == dialog.confirm_input.EchoMode.Password


# --------------------------------------------------------------------------- validation


def test_empty_fields_show_errors(qtbot, dialog, fake_auth):
    dialog.button_box.button(QDialogButtonBox.StandardButton.Save).click()
    qtbot.wait(60)

    assert dialog.current_error.isHidden() is False
    assert dialog.new_error.isHidden() is False
    assert fake_auth.change_calls == []


def test_password_mismatch_shows_error(qtbot, dialog, fake_auth):
    dialog.current_input.setText("old-password")
    dialog.new_input.setText("NewPass123!")
    dialog.confirm_input.setText("DifferentPass123!")
    dialog.button_box.button(QDialogButtonBox.StandardButton.Save).click()
    qtbot.wait(60)

    assert dialog.new_error.isHidden() is False
    assert "does not match" in dialog.new_error.text()
    assert fake_auth.change_calls == []


# --------------------------------------------------------------------------- success / failure


def test_successful_change_emits_signal(qtbot, dialog, fake_auth):
    from models.user import User
    user = User(id=1, username="adminwit", email="adminwit@wit.edu.ph",
                full_name="System Administrator", role="admin", is_active=True)
    fake_auth.result_to_return = type("Result", (), {
        "success": True, "user": None, "message": "Password changed successfully.",
        "locked": False, "lockout_remaining": 0,
    })()

    dialog.current_input.setText("old-password")
    dialog.new_input.setText("N3w!Passw0rd!")
    dialog.confirm_input.setText("N3w!Passw0rd!")

    emitted = []
    dialog.password_changed.connect(lambda: emitted.append(True))

    dialog.button_box.button(QDialogButtonBox.StandardButton.Save).click()
    qtbot.wait(60)

    assert len(emitted) == 1
    assert fake_auth.change_calls == [(1, "old-password", "N3w!Passw0rd!")]


def test_failed_change_shows_error(qtbot, dialog, fake_auth):
    fake_auth.result_to_return = type("Result", (), {
        "success": False, "user": None, "message": "Current password is incorrect.",
        "locked": False, "lockout_remaining": 0,
    })()

    dialog.current_input.setText("wrong")
    dialog.new_input.setText("N3w!Passw0rd!")
    dialog.confirm_input.setText("N3w!Passw0rd!")

    dialog.button_box.button(QDialogButtonBox.StandardButton.Save).click()
    qtbot.wait(60)

    assert dialog.global_error.isHidden() is False
    assert "incorrect" in dialog.global_error.text()


def test_exception_during_change_shows_safe_error(qtbot, dialog, fake_auth):
    fake_auth.raise_on_change = RuntimeError("db exploded")

    dialog.current_input.setText("old-password")
    dialog.new_input.setText("N3w!Passw0rd!")
    dialog.confirm_input.setText("N3w!Passw0rd!")

    dialog.button_box.button(QDialogButtonBox.StandardButton.Save).click()
    qtbot.wait(60)

    assert dialog.global_error.isHidden() is False
    assert "could not be completed" in dialog.global_error.text()


# --------------------------------------------------------------------------- enter key submits


def test_enter_key_submits(qtbot, dialog, fake_auth):
    from models.user import User
    user = User(id=1, username="adminwit", email="adminwit@wit.edu.ph",
                full_name="System Administrator", role="admin", is_active=True)
    fake_auth.result_to_return = type("Result", (), {
        "success": True, "user": None, "message": "Password changed successfully.",
        "locked": False, "lockout_remaining": 0,
    })()

    dialog.current_input.setText("old-password")
    dialog.new_input.setText("N3w!Passw0rd!")
    dialog.confirm_input.setText("N3w!Passw0rd!")

    emitted = []
    dialog.password_changed.connect(lambda: emitted.append(True))

    QTest.keyClick(dialog.confirm_input, Qt.Key_Return)
    qtbot.wait(60)

    assert len(emitted) == 1
    assert fake_auth.change_calls == [(1, "old-password", "N3w!Passw0rd!")]

"""Tests for the authentication service (requirement 11.7).

Covers valid/invalid login, empty submission, repeated-attempt lockout,
session creation/restoration, role-based authorization, logout, and database
unavailability. Uses mocks so no real credentials are exercised.
"""

from unittest.mock import MagicMock, patch

import pytest

from services.auth_service import (
    AuthService,
    AuthResult,
    PBKDF2_ITERATIONS,
    auth_service,
    ROLE_ADMIN,
    ROLE_STAFF,
    ROLE_VIEWER,
)
from models.user import User


# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def auth():
    """Fresh AuthService per test, with no cached session or failures."""
    service = AuthService()
    service._current_user = None
    service._failures.clear()
    return service


@pytest.fixture
def admin_user():
    return User(id=1, username="admin", email="admin@wit.edu.ph",
                full_name="System Administrator", role=ROLE_ADMIN, is_active=True)


@pytest.fixture
def viewer_user():
    return User(id=9, username="viewer", email="viewer@wit.edu.ph",
                full_name="View Only User", role=ROLE_VIEWER, is_active=True)


# --------------------------------------------------------------------------- password hashing


def test_password_hash_roundtrip():
    password = "Str0ng!Passw0rd"
    hashed = AuthService.hash_password(password)
    assert hashed.startswith("pbkdf2_sha256$")
    assert str(PBKDF2_ITERATIONS) in hashed
    assert AuthService._verify_password(password, hashed) is True
    assert AuthService._verify_password("wrong-password", hashed) is False


def test_password_hash_includes_random_salt():
    h1 = AuthService.hash_password("same-password")
    h2 = AuthService.hash_password("same-password")
    assert h1 != h2  # different salts -> different hashes


# --------------------------------------------------------------------------- valid / invalid login


def test_valid_login_sets_session(auth, admin_user):
    fake_row = {
        "id": 1, "username": "admin", "email": "admin@wit.edu.ph",
        "full_name": "System Administrator", "role": "admin", "is_active": True,
        "password_changed_at": None,
        "failed_attempts": 0,
        "locked_until": None,
        "password_hash": AuthService.hash_password("correct-password"),
    }
    with patch("services.auth_service.get_cursor") as mock_get_cursor:
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = fake_row
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor

        result = auth.login("admin", "correct-password")

    assert result.success is True
    assert result.user is not None
    assert result.user.username == "admin"
    assert result.user.role == ROLE_ADMIN
    assert auth.is_authenticated is True
    assert auth.current_user is not None
    assert auth.current_user.id == 1


def test_valid_login_via_username_only(auth, admin_user):
    """Login must use the username only — an email address is not accepted."""
    fake_row = {
        "id": 1, "username": "admin", "email": "admin@wit.edu.ph",
        "full_name": "System Administrator", "role": "admin", "is_active": True,
        "password_changed_at": None,
        "failed_attempts": 0,
        "locked_until": None,
        "password_hash": AuthService.hash_password("correct-password"),
    }

    with patch("services.auth_service.get_cursor") as mock_get_cursor:
        mock_cursor = MagicMock()
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor

        def fake_fetchone():
            # Simulate the real query: only match by username, not email.
            call_args = mock_cursor.execute.call_args
            if call_args:
                params = call_args[0][1]  # ("admin",) or ("admin@wit.edu.ph",)
                if params and params[0] == "admin":
                    return fake_row
            return None

        mock_cursor.fetchone.side_effect = fake_fetchone

        # Username works.
        result = auth.login("admin", "correct-password")
        assert result.success is True

        # Email address is NOT accepted as a login identifier.
        result = auth.login("admin@wit.edu.ph", "correct-password")
        assert result.success is False
        assert result.message == "Invalid username or password."


def test_invalid_password_generic_message(auth, admin_user):
    fake_row = {
        "id": 1, "username": "admin", "email": "admin@wit.edu.ph",
        "full_name": "System Administrator", "role": "admin", "is_active": True,
        "password_changed_at": None,
        "failed_attempts": 0,
        "locked_until": None,
        "password_hash": AuthService.hash_password("correct-password"),
    }
    with patch("services.auth_service.get_cursor") as mock_get_cursor:
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = fake_row
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor

        result = auth.login("admin", "wrong-password")

    assert result.success is False
    assert result.user is None
    # Generic message: must not reveal whether the account exists or the
    # password was wrong.
    assert result.message == "Invalid username or password."
    assert auth.is_authenticated is False


def test_missing_account_generic_message(auth):
    with patch("services.auth_service.get_cursor") as mock_get_cursor:
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor

        result = auth.login("no-such-user", "password")

    assert result.success is False
    assert result.message == "Invalid username or password."
    # Timing-safe: even missing accounts run the verification path.
    assert auth.is_authenticated is False


def test_inactive_account_rejected(auth, admin_user):
    inactive = User(id=1, username="admin", email="admin@wit.edu.ph",
                    full_name="System Administrator", role=ROLE_ADMIN,
                    is_active=False)
    fake_row = {
        "id": 1, "username": "admin", "email": "admin@wit.edu.ph",
        "full_name": "System Administrator", "role": "admin", "is_active": False,
        "password_changed_at": None,
        "failed_attempts": 0,
        "locked_until": None,
        "password_hash": AuthService.hash_password("correct-password"),
    }
    with patch("services.auth_service.get_cursor") as mock_get_cursor:
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = fake_row
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor

        result = auth.login("admin", "correct-password")

    assert result.success is False
    assert result.message == "Invalid username or password."
    assert auth.is_authenticated is False


def test_empty_credentials_rejected(auth):
    assert auth.login("", "password").success is False
    assert auth.login("admin", "").success is False
    assert auth.login("   ", "   ").success is False
    assert auth.is_authenticated is False


# --------------------------------------------------------------------------- repeated attempts / lockout


def test_repeated_failures_lock_out_account(auth):
    with patch("services.auth_service.get_cursor") as mock_get_cursor:
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None  # unknown account always fails
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor

        for i in range(5):
            result = auth.login("victim", "bad-password")
            assert result.success is False

        # 6th attempt should be locked out.
        result = auth.login("victim", "bad-password")
        assert result.success is False
        assert result.locked is True
        assert result.lockout_remaining > 0
        assert "Too many failed attempts" in result.message


def test_lockout_message_does_not_enumerate_accounts(auth):
    with patch("services.auth_service.get_cursor") as mock_get_cursor:
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor

        for _ in range(5):
            auth.login("victim", "bad-password")
        result = auth.login("victim", "bad-password")

    assert result.locked is True
    assert result.lockout_remaining > 0
    assert "Too many failed attempts" in result.message
    assert "second(s)" in result.message


def test_successful_login_clears_failure_count(auth):
    fake_row = {
        "id": 1, "username": "admin", "email": "admin@wit.edu.ph",
        "full_name": "System Administrator", "role": "admin", "is_active": True,
        "password_changed_at": None,
        "failed_attempts": 0,
        "locked_until": None,
        "password_hash": AuthService.hash_password("correct-password"),
    }
    with patch("services.auth_service.get_cursor") as mock_get_cursor:
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = fake_row
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor

        # Two failures then success.
        auth.login("admin", "bad")
        auth.login("admin", "bad")
        result = auth.login("admin", "correct-password")

    assert result.success is True
    assert auth._failures == {}  # cleared on success


# --------------------------------------------------------------------------- session lifecycle


def test_session_restored_until_logout(auth, admin_user):
    fake_row = {
        "id": 1, "username": "admin", "email": "admin@wit.edu.ph",
        "full_name": "System Administrator", "role": "admin", "is_active": True,
        "password_changed_at": None,
        "failed_attempts": 0,
        "locked_until": None,
        "password_hash": AuthService.hash_password("correct-password"),
    }
    with patch("services.auth_service.get_cursor") as mock_get_cursor:
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = fake_row
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor
        auth.login("admin", "correct-password")

    # Session persists across calls (in-memory for the desktop session).
    assert auth.is_authenticated is True
    assert auth.current_user.username == "admin"

    logged_out = auth.logout()
    assert logged_out.username == "admin"
    assert auth.is_authenticated is False
    assert auth.current_user is None


def test_require_auth_returns_current_user(auth, admin_user):
    auth._current_user = admin_user
    assert auth.require_auth() is admin_user

    auth._current_user = None
    with pytest.raises(PermissionError):
        auth.require_auth()


def test_require_role_enforces_permissions(auth, admin_user, viewer_user):
    # Admin passes admin+staff checks.
    auth._current_user = admin_user
    assert auth.require_role(ROLE_ADMIN) is admin_user
    assert auth.require_role(ROLE_STAFF, ROLE_ADMIN) is admin_user

    # Viewer cannot pass admin/staff checks.
    auth._current_user = viewer_user
    with pytest.raises(PermissionError):
        auth.require_role(ROLE_ADMIN)
    with pytest.raises(PermissionError):
        auth.require_role(ROLE_STAFF)

    # Viewer passes viewer checks.
    assert auth.require_role(ROLE_VIEWER) is viewer_user

    auth._current_user = None
    with pytest.raises(PermissionError):
        auth.require_role(ROLE_VIEWER)


# --------------------------------------------------------------------------- password change


def test_change_password_success(auth, admin_user):
    with patch.object(AuthService, "_lookup_user_by_id",
                      return_value=(admin_user, AuthService.hash_password("old-password"))), \
         patch("services.auth_service.get_cursor") as mock_get_cursor:
        mock_cursor = MagicMock()
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor
        auth._current_user = admin_user
        result = auth.change_password(1, "old-password", "N3w!Passw0rd")

    assert result.success is True
    assert auth.is_authenticated is False  # session invalidated


def test_change_password_wrong_old_password(auth, admin_user):
    with patch.object(AuthService, "_lookup_user_by_id",
                      return_value=(admin_user, AuthService.hash_password("old-password"))), \
         patch("services.auth_service.get_cursor"):
        auth._current_user = admin_user
        result = auth.change_password(1, "wrong-old", "N3w!Passw0rd")

    assert result.success is False
    assert result.message == "Current password is incorrect."
    # Session must remain intact on failure.
    assert auth.is_authenticated is True


def test_change_password_new_equals_old(auth, admin_user):
    with patch.object(AuthService, "_lookup_user_by_id",
                      return_value=(admin_user, AuthService.hash_password("old-password"))):
        auth._current_user = admin_user
        result = auth.change_password(1, "old-password", "old-password")

    assert result.success is False
    assert "differ from your current password" in result.message


def test_change_password_too_short(auth, admin_user):
    with patch.object(AuthService, "_lookup_user_by_id",
                      return_value=(admin_user, AuthService.hash_password("old-password"))):
        auth._current_user = admin_user
        result = auth.change_password(1, "old-password", "Short1!")

    assert result.success is False
    assert "at least 8 characters" in result.message


def test_change_password_missing_symbol(auth, admin_user):
    with patch.object(AuthService, "_lookup_user_by_id",
                      return_value=(admin_user, AuthService.hash_password("old-password"))):
        auth._current_user = admin_user
        result = auth.change_password(1, "old-password", "NoSymbol1234")

    assert result.success is False
    assert "symbol" in result.message


def test_change_password_no_session(auth):
    result = auth.change_password(1, "old", "N3w!Passw0rd")
    assert result.success is False
    assert "No authenticated session" in result.message


# --------------------------------------------------------------------------- database unavailable


def test_database_unavailable_during_login(auth):
    from database.connection import DatabaseUnavailableError
    with patch("services.auth_service.get_cursor") as mock_get_cursor:
        mock_get_cursor.side_effect = DatabaseUnavailableError("down")
        result = auth.login("admin", "password")

    assert result.success is False
    assert result.message == "Authentication service unavailable. Try again later."
    assert auth.is_authenticated is False


# --------------------------------------------------------------------------- module-level singleton


def test_module_level_singleton_has_no_session_by_default():
    # The shared service should start unauthenticated (tests isolate state
    # via the conftest fixture, but assert the default here).
    assert auth_service.current_user is None

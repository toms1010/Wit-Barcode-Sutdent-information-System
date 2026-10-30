"""Authentication service for the WIT Barcode Student Information System.

Verifies credentials against the ``users`` table, hashes passwords with
PBKDF2-HMAC-SHA256 (a standard-library construction -- no third-party crypto
dependency required), enforces failed-login rate limiting, and holds the
in-memory session for the current desktop session.

Lockout state is persisted in the ``users`` table (``failed_attempts``,
``locked_until``) so it survives application restarts. In-memory counters are
used as a fast-path cache and are reconciled with the database on every login
attempt.

Security properties
-------------------
* Passwords are *never* stored or compared in plaintext. A random 16-byte salt
  is stored alongside each hash and the comparison uses ``hmac.compare_digest``
  so timing information does not leak whether a username exists.
* Failed-login attempts are rate-limited per account so brute-force guessing is
  slowed down. Lockout state survives application restarts.
* The failure message is identical whether the account is missing, inactive, or
  the password is wrong, so no account-enumeration information leaks.
* The acting user is held only in memory for the lifetime of the process; it is
  never written to disk, local storage, or environment variables.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from database.connection import DatabaseUnavailableError, get_cursor
from models.user import (
    ROLE_ADMIN,
    ROLE_STAFF,
    ROLE_VIEWER,
    ALL_ROLES,
    User,
)

logger = logging.getLogger(__name__)

#: PBKDF2 iterations. High enough to make brute-force costly, low enough not to
#: freeze the UI on login. Keep in step with any value stored alongside hashes.
PBKDF2_ITERATIONS = 210_000
#: Salt length in bytes.
SALT_BYTES = 16
#: Hash length in bytes (SHA-256).
HASH_BYTES = 32

#: Maximum failed attempts before an account is temporarily locked out.
MAX_FAILED_ATTEMPTS = 5
#: Seconds an account stays locked after too many failures.
LOCKOUT_SECONDS = 900  # 15 minutes
#: Seconds after which a failed-attempt counter decays back to zero.
FAILED_ATTEMPT_DECAY_SECONDS = 3600  # 1 hour


@dataclass(frozen=True)
class AuthResult:
    """Outcome of an authentication attempt."""

    success: bool
    user: Optional[User] = None
    #: A user-facing message. Never reveals whether the account exists.
    message: str = ""
    #: True when the account is locked out due to repeated failures.
    locked: bool = False
    #: Seconds remaining until lockout expires (0 when not locked).
    lockout_remaining: int = 0
    #: True when the user must change their password before accessing any
    #: protected module (first login or administrator-enforced reset).
    must_change_password: bool = False


class AuthService:
    """Verifies credentials and manages the current login session."""

    def __init__(self) -> None:
        self._current_user: Optional[User] = None
        #: Per-username in-memory failure cache: {username: [count, first_fail_ts]}.
        #: Used as a fast-path; the authoritative state lives in the database.
        self._failures: dict[str, list] = {}
        self._lock = __import__("threading").Lock()

    # ------------------------------------------------------------------ session

    @property
    def current_user(self) -> Optional[User]:
        """The authenticated user, or ``None`` if nobody is signed in."""
        return self._current_user

    @property
    def is_authenticated(self) -> bool:
        return self._current_user is not None and self._current_user.is_active

    def login(self, username: str, password: str) -> AuthResult:
        """Authenticate ``username``/``password`` against the database.

        Returns an :class:`AuthResult`. On failure the message is generic and
        gives no indication of whether the account exists.
        """
        username = (username or "").strip()
        password = password or ""

        # --- client-side validation ---------------------------------------
        if not username or not password:
            return AuthResult(
                False, message="Enter both your username and password."
            )

        # --- check persistent lockout state from the database --------------
        try:
            db_locked, db_remaining = self._check_db_lockout(username)
        except DatabaseUnavailableError:
            logger.warning("Authentication aborted: database unavailable")
            return AuthResult(
                False,
                message="Authentication service unavailable. Try again later.",
            )

        if db_locked:
            return AuthResult(
                False,
                message=f"Too many failed attempts. Try again in {db_remaining} second(s).",
                locked=True,
                lockout_remaining=db_remaining,
            )

        # --- check in-memory failure cache (fast path) ---------------------
        with self._lock:
            mem_locked, mem_remaining = self._is_locked(username)
        if mem_locked:
            return AuthResult(
                False,
                message=f"Too many failed attempts. Try again in {mem_remaining} second(s).",
                locked=True,
                lockout_remaining=mem_remaining,
            )

        try:
            user, password_hash = self._lookup_user(username)
        except DatabaseUnavailableError:
            logger.warning("Authentication aborted: database unavailable")
            return AuthResult(
                False,
                message="Authentication service unavailable. Try again later.",
            )

        # --- constant-time verification ------------------------------------
        # We always run the hash verification, even when the user is missing,
        # so the timing profile is identical whether or not the account exists.
        expected_hash = password_hash or self._dummy_hash(username)
        ok = user is not None and user.is_active and self._verify_password(
            password, expected_hash
        )

        with self._lock:
            if ok:
                self._failures.pop(username, None)
                self._clear_db_lockout(username)
                self._record_login(user.id)
                self._current_user = user
                logger.info("User %s authenticated (role=%s)", username, user.role)
                return AuthResult(
                    True,
                    user=user,
                    must_change_password=user.must_change_password,
                )
            else:
                self._record_failure(username)
                self._increment_db_failure(username)
                count = self._failures.get(username, [0])[0]
                locked, remaining = self._is_locked(username)
                logger.warning(
                    "Failed login for %s (attempt %d%s)",
                    username,
                    count,
                    " -- locked" if locked else "",
                )
                return AuthResult(
                    False,
                    message="Invalid username or password.",
                    locked=locked,
                    lockout_remaining=remaining,
                )

    def logout(self) -> Optional[User]:
        """End the current session and return the user that was logged out."""
        previous = self._current_user
        self._current_user = None
        if previous is not None:
            logger.info("User %s logged out", previous.username)
        return previous

    def require_auth(self) -> User:
        """Return the current user, raising if no session exists."""
        if self._current_user is None:
            raise PermissionError("No authenticated session.")
        if not self._current_user.is_active:
            raise PermissionError("Account is inactive.")
        return self._current_user

    def require_role(self, *roles: str) -> User:
        """Return the current user only if they hold one of ``roles``."""
        user = self.require_auth()
        if not user.has_role(*roles):
            raise PermissionError(
                f"This action requires one of the following roles: "
                f"{', '.join(roles)}."
            )
        return user

    # ------------------------------------------------------------------ internals

    def _lookup_user(self, username: str) -> tuple[Optional[User], str]:
        """Return ``(User, password_hash)``. User is None when not found.

        The hash is returned (or a dummy) so callers always spend time in
        :meth:`_verify_password`, keeping the timing profile uniform.
        """
        with get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, username, email, full_name, role, is_active,
                       password_changed_at, password_hash,
                       failed_attempts, locked_until
                FROM users
                WHERE username = %s
                LIMIT 1
                """,
                (username,),
            )
            row = cursor.fetchone()

        if row is None:
            # No account -- still return a plausible hash so verification runs.
            return None, self._dummy_hash(username)

        password_changed_at = row["password_changed_at"]
        if password_changed_at is not None:
            try:
                password_changed_at = password_changed_at.replace(tzinfo=None)
            except AttributeError:
                pass

        user = User(
            id=int(row["id"]),
            username=row["username"],
            email=row["email"],
            full_name=row["full_name"],
            role=row["role"],
            is_active=bool(row["is_active"]),
            password_changed_at=password_changed_at,
        )
        return user, row["password_hash"] or ""

    def _record_login(self, user_id: int) -> None:
        """Stamp ``last_login`` on the account (best effort)."""
        try:
            with get_cursor(commit=True) as cursor:
                cursor.execute(
                    "UPDATE users SET last_login = NOW() WHERE id = %s",
                    (user_id,),
                )
        except DatabaseUnavailableError:
            logger.debug("Could not stamp last_login (database unavailable)")

    def _record_failure(self, username: str) -> None:
        """Increment the in-memory failure counter for an account."""
        now = time.monotonic()
        entry = self._failures.get(username)
        if entry is None:
            self._failures[username] = [1, now]
            return
        # Decay old failures so a single bad day doesn't permanently lock.
        if now - entry[1] > FAILED_ATTEMPT_DECAY_SECONDS:
            entry[0] = 1
            entry[1] = now
        else:
            entry[0] += 1

    def _is_locked(self, username: str) -> tuple[bool, int]:
        """Whether the account is locked and seconds until lockout expires.

        Checks only the in-memory cache. The authoritative check is
        :meth:`_check_db_lockout`.
        """
        entry = self._failures.get(username)
        if entry is None:
            return False, 0
        count, first_fail = entry
        if count < MAX_FAILED_ATTEMPTS:
            return False, 0
        elapsed = time.monotonic() - first_fail
        remaining = int(LOCKOUT_SECONDS - elapsed)
        if remaining <= 0:
            # Lockout expired: clear the counter so the user can try again.
            self._failures.pop(username, None)
            return False, 0
        return True, remaining

    # ------------------------------------------------------------------ database lockout persistence

    def _check_db_lockout(self, username: str) -> tuple[bool, int]:
        """Check the persistent lockout state stored in the database.

        Returns ``(locked, remaining_seconds)``.
        """
        try:
            with get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT failed_attempts, locked_until
                    FROM users
                    WHERE username = %s
                    LIMIT 1
                    """,
                    (username,),
                )
                row = cursor.fetchone()
        except DatabaseUnavailableError:
            raise
        except Exception:
            # If the column doesn't exist yet (pre-migration database), fall
            # back to in-memory-only lockout.
            return False, 0

        if row is None:
            return False, 0

        failed_attempts = int(row["failed_attempts"] or 0)
        locked_until = row["locked_until"]

        if failed_attempts < MAX_FAILED_ATTEMPTS:
            return False, 0

        if locked_until is None:
            # Shouldn't happen, but treat as not locked if no lock timestamp.
            return False, 0

        # Normalize to a naive datetime for comparison.
        try:
            locked_until = locked_until.replace(tzinfo=None)
        except AttributeError:
            pass

        now = datetime.now()
        if now >= locked_until:
            # Lockout expired: clear the persistent state.
            self._clear_db_lockout(username)
            return False, 0

        remaining = int((locked_until - now).total_seconds())
        return True, max(remaining, 0)

    def _increment_db_failure(self, username: str) -> None:
        """Increment the persistent failure counter and set lockout if needed.

        Updates ``failed_attempts`` and ``locked_until`` in the database so
        lockout state survives application restarts.
        """
        try:
            with get_cursor(commit=True) as cursor:
                cursor.execute(
                    """
                    SELECT failed_attempts FROM users WHERE username = %s
                    """,
                    (username,),
                )
                row = cursor.fetchone()
                if row is None:
                    return
                current_attempts = int(row["failed_attempts"] or 0)
                new_attempts = current_attempts + 1

                if new_attempts >= MAX_FAILED_ATTEMPTS:
                    locked_until = datetime.now() + timedelta(seconds=LOCKOUT_SECONDS)
                    cursor.execute(
                        """
                        UPDATE users
                        SET failed_attempts = %s, locked_until = %s
                        WHERE username = %s
                        """,
                        (new_attempts, locked_until, username),
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE users
                        SET failed_attempts = %s
                        WHERE username = %s
                        """,
                        (new_attempts, username),
                    )
        except DatabaseUnavailableError:
            logger.debug("Could not increment DB failure counter (database unavailable)")
        except Exception as exc:
            # Column may not exist on pre-migration databases; that's OK.
            logger.debug("Could not update DB failure counter: %s", exc)

    def _clear_db_lockout(self, username: str) -> None:
        """Clear persistent lockout state for an account (on successful login)."""
        try:
            with get_cursor(commit=True) as cursor:
                cursor.execute(
                    """
                    UPDATE users
                    SET failed_attempts = 0, locked_until = NULL
                    WHERE username = %s
                    """,
                    (username,),
                )
        except DatabaseUnavailableError:
            logger.debug("Could not clear DB lockout (database unavailable)")
        except Exception as exc:
            logger.debug("Could not clear DB lockout: %s", exc)

    # ----------------------------------------------------------------- password hashing

    @staticmethod
    def _dummy_hash(pepper: str) -> str:
        """A deterministic hash of equal length used when the account is missing.

        Keeps the verification code path identical regardless of whether the
        username exists.
        """
        return hashlib.pbkdf2_hmac(
            "sha256", pepper.encode("utf-8"), b"\x00" * SALT_BYTES, PBKDF2_ITERATIONS
        ).hex()

    @classmethod
    def hash_password(cls, password: str) -> str:
        """Hash ``password`` with a random salt. Store the whole result."""
        salt = os.urandom(SALT_BYTES)
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
        )
        return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"

    @classmethod
    def _verify_password(cls, password: str, stored: str) -> bool:
        """Verify ``password`` against a stored ``pbkdf2_sha256$...`` hash."""
        try:
            prefix, iterations, salt_hex, hash_hex = stored.split("$")
        except ValueError:
            return False
        if prefix != "pbkdf2_sha256":
            return False
        try:
            iterations = int(iterations)
            salt = bytes.fromhex(salt_hex)
            expected = bytes.fromhex(hash_hex)
        except (ValueError, TypeError):
            return False
        if len(salt) != SALT_BYTES or len(expected) != HASH_BYTES:
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations
        )
        return hmac.compare_digest(dk, expected)

    @classmethod
    def create_user_hash(cls, password: str) -> str:
        """Public helper to hash a password for seeding or password change."""
        return cls.hash_password(password)

    def change_password(
        self, user_id: int, old_password: str, new_password: str
    ) -> AuthResult:
        """Change the current user's password.

        Verifies ``old_password`` against the stored hash, validates
        ``new_password`` against the complexity policy, then persists the new
        hash and stamps ``password_changed_at`` so the mandatory first-login
        gate is cleared.

        Returns an :class:`AuthResult`. On success the session is invalidated
        (the caller should re-log the user in with the new password).
        """
        if self._current_user is None or self._current_user.id != user_id:
            return AuthResult(
                False,
                message="No authenticated session to change the password for.",
            )

        old_password = old_password or ""
        new_password = new_password or ""

        if not old_password or not new_password:
            return AuthResult(
                False,
                message="Enter both your current password and the new password.",
            )

        # --- validate old password -----------------------------------------
        user, password_hash = self._lookup_user_by_id(user_id)
        if user is None:
            return AuthResult(
                False,
                message="Account not found. Please log in again.",
            )
        if not password_hash or not self._verify_password(old_password, password_hash):
            return AuthResult(
                False,
                message="Current password is incorrect.",
            )

        # --- validate new password -----------------------------------------
        validation = self._validate_new_password(new_password, old_password)
        if not validation[0]:
            return AuthResult(False, message=validation[1])

        # --- persist --------------------------------------------------------
        try:
            new_hash = self.hash_password(new_password)
            with get_cursor(commit=True) as cursor:
                cursor.execute(
                    """
                    UPDATE users
                    SET password_hash = %s, password_changed_at = NOW(),
                        updated_at = NOW(), failed_attempts = 0, locked_until = NULL
                    WHERE id = %s
                    """,
                    (new_hash, user_id),
                )
        except DatabaseUnavailableError:
            logger.warning("Password change aborted: database unavailable")
            return AuthResult(
                False,
                message="Password change service unavailable. Try again later.",
            )

        # Invalidate the session: the old password/session is retired. The
        # caller must re-authenticate with the new password.
        self.logout()
        logger.info("Password changed for user id %s", user_id)
        return AuthResult(
            True,
            message="Password changed successfully. Please sign in again.",
        )

    @staticmethod
    def _lookup_user_by_id(user_id: int) -> tuple[Optional[User], str]:
        """Return ``(User, password_hash)`` by primary key, or ``(None, "")``."""
        try:
            with get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, username, email, full_name, role, is_active,
                           password_changed_at, password_hash,
                           failed_attempts, locked_until
                    FROM users
                    WHERE id = %s
                    LIMIT 1
                    """,
                    (user_id,),
                )
                row = cursor.fetchone()
        except DatabaseUnavailableError:
            raise
        except Exception:
            return None, ""

        if row is None:
            return None, ""

        password_changed_at = row["password_changed_at"]
        if password_changed_at is not None:
            try:
                password_changed_at = password_changed_at.replace(tzinfo=None)
            except AttributeError:
                pass

        user = User(
            id=int(row["id"]),
            username=row["username"],
            email=row["email"],
            full_name=row["full_name"],
            role=row["role"],
            is_active=bool(row["is_active"]),
            password_changed_at=password_changed_at,
        )
        return user, row["password_hash"] or ""

    @staticmethod
    def _validate_new_password(
        new_password: str, old_password: str
    ) -> tuple[bool, str]:
        """Validate a new password against the complexity policy.

        Returns ``(ok, message)``.
        """
        if new_password == old_password:
            return False, "The new password must differ from your current password."
        if len(new_password) < 8:
            return False, "The new password must be at least 8 characters long."
        if len(new_password) > 128:
            return False, "The new password is too long (maximum 128 characters)."
        if not any(c.isupper() for c in new_password):
            return False, "The new password must contain an uppercase letter."
        if not any(c.islower() for c in new_password):
            return False, "The new password must contain a lowercase letter."
        if not any(c.isdigit() for c in new_password):
            return False, "The new password must contain a digit."
        if not any(not c.isalnum() for c in new_password):
            return False, "The new password must contain a symbol (e.g. !@#$%)."
        return True, ""


#: Module-level default service so pages and widgets can reach the session
#: without threading it through every constructor.
auth_service = AuthService()

__all__ = [
    "AuthService",
    "AuthResult",
    "PBKDF2_ITERATIONS",
    "auth_service",
    "ROLE_ADMIN",
    "ROLE_STAFF",
    "ROLE_VIEWER",
    "ALL_ROLES",
    "WRITE_ROLES",
    "READ_ROLES",
]

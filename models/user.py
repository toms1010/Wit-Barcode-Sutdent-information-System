"""User model for the authentication system.

The application's :data:`database.schema.SCHEMA_SQL` already defines a
``users`` table (username, email, password_hash, role, is_active, ...).
This module is the in-memory representation of an authenticated account.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


#: Roles recognised by the system. Kept in sync with the ``users.role`` ENUM
#: in the schema so the UI and the backend agree on what each account may do.
ROLE_ADMIN = "admin"
ROLE_STAFF = "staff"
ROLE_VIEWER = "viewer"

ALL_ROLES = (ROLE_ADMIN, ROLE_STAFF, ROLE_VIEWER)

#: Roles that may write/modify student, enrolment, assessment and payment data.
WRITE_ROLES = (ROLE_ADMIN, ROLE_STAFF)

#: Roles that may read all records.
READ_ROLES = ALL_ROLES


@dataclass(frozen=True)
class User:
    """An authenticated application user.

    Attributes are intentionally read-only (frozen dataclass): the acting
    user for a session is established at login and never mutated, which keeps
    authorization decisions predictable.

    ``password_changed_at`` records when the user last set their own password.
    When it is ``None`` the account is in a mandatory first-login password
    change state and must not be allowed past the password-change gate until a
    new password is set.
    """

    id: int
    username: str
    email: str
    full_name: str
    role: str
    is_active: bool = True
    password_changed_at: Optional[datetime] = None

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def is_staff(self) -> bool:
        return self.role in WRITE_ROLES

    @property
    def can_write(self) -> bool:
        """True if the user may create or modify records."""
        return self.is_active and self.role in WRITE_ROLES

    @property
    def can_read(self) -> bool:
        """True if the user may view records."""
        return self.is_active

    @property
    def must_change_password(self) -> bool:
        """True if the user is forced to change their password on next login."""
        return self.password_changed_at is None

    def has_role(self, *roles: str) -> bool:
        return self.role in roles

    def __str__(self) -> str:
        return f"{self.full_name} ({self.username}) [{self.role}]"

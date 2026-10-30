# Administrator Account Initialization & Access Recovery

This document describes how to provision the initial administrator account for
the WIT Barcode Student Information System, how the mandatory first-login
password change works, and how to recover access if the administrator account
becomes unavailable.

## 1. Initial Administrator Account

The system provisions a single initial administrator account during database
initialization:

| Property     | Value                          |
|--------------|--------------------------------|
| Username     | `adminwit`                     |
| Initial password | `adminwir123`               |
| Role         | `admin`                        |
| Email        | `adminwit@wit.edu.ph`          |
| Full name    | System Administrator           |

### How it is provisioned

The account is created by `scripts/initialize_database.py` during schema
initialization (`_ensure_admin_user`). The account is created **only once**:

* If no `adminwit` account exists, it is inserted with the initial password
  `adminwir123`, role `admin`, and `password_changed_at = NULL`.
* If `adminwit` already exists, initialization **skips** provisioning. The
  existing password and account data are **never** reset or overwritten, so
  re-running initialization is safe.

### Password storage

The initial password is **never stored in plaintext**. It is hashed with
PBKDF2-HMAC-SHA256 (210,000 iterations, random 16-byte salt) and the hash is
stored in the `users.password_hash` column. The plaintext value never appears
in the database, source code, or application logs.

### Mandatory first-login password change

The `adminwit` account is provisioned with `password_changed_at = NULL`, which
places it in a mandatory first-login password-change state. The required
workflow is:

1. Open the application → the **Login page** is displayed.
2. Enter username `adminwit` and the initial password `adminwir123`.
3. Credentials are verified against the backend database (no simulation or
   bypass).
4. Because `password_changed_at` is `NULL`, the **password-change dialog** is
   shown before any protected module (Dashboard, Students, Courses, Subjects,
   Enrollment, Certificate of Matriculation, Assessments, etc.) is accessible.
5. The administrator enters the current password (`adminwir123`) and a new
   password that meets the complexity policy:
   * At least 8 characters
   * At least one uppercase letter
   * At least one lowercase letter
   * At least one digit
   * At least one symbol (e.g. `!@#$%`)
   * Must differ from the current password
6. The new password is hashed and stored; `password_changed_at` is stamped,
   clearing the mandatory gate.
7. The session is invalidated and the administrator signs in again with the
   new password.
8. After successful re-authentication, the administrator is redirected to the
   **Dashboard**.

Until step 6 is completed, no protected module or API endpoint is accessible.

## 2. Running Database Initialization

```bash
# From the project root:
cd "/home/tommyp/Desktop/Desktop-app/WIT Barcode Student Information System"

# Ensure a .env file exists (copy from .env.example and review settings):
cp .env.example .env   # if .env does not already exist

# Run initialization (creates schema, migrates existing tables, provisions
# the adminwit account only if it does not already exist):
python scripts/initialize_database.py
```

The script:

1. Connects to the configured MySQL database.
2. Creates the schema (`CREATE TABLE IF NOT EXISTS`), so re-running is safe.
3. Runs the `password_changed_at` column migration on pre-existing databases
   (idempotent — skipped if the column already exists).
4. Provisions `adminwit` only if no such username exists.
5. Seeds student, course, subject, enrollment, and assessment reference data.

### Environment variables

Relevant variables from `.env`:

| Variable             | Example                  | Purpose                                |
|----------------------|--------------------------|----------------------------------------|
| `DB_HOST`            | `localhost`              | MySQL host                             |
| `DB_PORT`            | `3306`                   | MySQL port                             |
| `DB_NAME`            | `wit_barcode_system`     | Database name                          |
| `DB_USER`            | `wit_user`               | Database account                       |
| `DB_PASSWORD`        | *(from .env)*            | Database account password              |
| `APP_NAME`           | `WIT Barcode ...`        | Application display name               |
| `APP_VERSION`        | `1.0.0`                  | Application version                    |

No administrator password is read from the environment — `adminwit` /
`adminwir123` are fixed by the deployment specification and are not
configurable via `.env`.

## 3. Role-Based Access Control

The `users.role` column is an ENUM: `admin`, `staff`, `viewer`. The
`adminwit` account has role `admin`, which grants access to all modules:

* Dashboard
* Students
* Barcode Scanner
* Subject Enrollment
* Courses & Subjects
* Certificate of Matriculation
* Assessment & Fees
* Reports & Analytics
* Training Center
* Settings

Authorization is enforced **server-side** in `ui/main_window.py`
(`ROLE_PAGES`) and in each service that performs writes. Hiding navigation
items alone is insufficient — direct API/endpoint access is also validated.

## 4. Session Management & Logout

* The authenticated session lives in memory for the lifetime of the desktop
  process. It is never written to disk or local storage.
* Passwords are never stored in local storage.
* **Logout** (from the user menu in the top bar) invalidates the session and
  returns the user to the Login page.
* After logout, the browser Back button cannot restore protected content
  because the session is cleared on the server side.
* Session expiration and authentication failures consistently redirect to the
  Login page.

## 5. Access Recovery

If the `adminwit` account becomes unavailable (forgotten password, locked
out, or the account is missing), recover access as follows:

### 5.1 Forgotten password (account exists, known username)

An administrator with `admin` or `staff` role who can still sign in can reset
another administrator's password directly in the database:

```sql
-- Hash the new password with the application's hasher, then update:
UPDATE users
SET password_hash = '<new pbkdf2_sha256 hash>',
    password_changed_at = NULL,
    updated_at = NOW()
WHERE username = 'adminwit';
```

Setting `password_changed_at = NULL` forces a mandatory password change on
next login. Generate the hash with the application's hasher:

```python
from services.auth_service import AuthService
print(AuthService.hash_password("<new-password>"))
```

### 5.2 Account locked out (repeated failed attempts)

Failed logins are rate-limited per account: after 5 failed attempts, the
account is locked for 15 minutes. The lockout expires automatically. No
administrator intervention is required — wait for the lockout to expire, then
sign in. Failed-attempt counters decay after 1 hour of inactivity.

### 5.3 Account missing or database restored from backup

If no `adminwit` account exists (e.g. the database was initialized before this
feature was added, or restored from a backup without the account), re-run
initialization:

```bash
python scripts/initialize_database.py
```

`_ensure_admin_user` checks whether `adminwit` already exists and only
inserts it if absent, so re-running is safe and will not overwrite an
existing administrator's credentials.

If the account exists but is inaccessible and no other administrator can sign
in, reset the password directly in the database as described in section 5.1.

### 5.4 Never do the following

* Do not hardcode `adminwit` / `adminwir123` in frontend code.
* Do not store the plaintext password in logs, source control, or local
  storage.
* Do not bypass backend verification or simulate authentication.
* Do not create a second "emergency" administrator account with a known
  password — use the database reset procedure above.

## 6. Acceptance Criteria

| # | Criterion                                                                 | Status |
|---|---------------------------------------------------------------------------|--------|
| 1 | The initial administrator account (`adminwit`) is provisioned successfully. | Implemented |
| 2 | The specified initial credentials authenticate against the backend.         | Implemented |
| 3 | The password is never stored in plaintext.                                | Implemented |
| 4 | First-login password change is enforced before protected modules load.    | Implemented |
| 5 | Protected routes and APIs reject unauthenticated users.                   | Implemented |
| 6 | Administrator permissions are enforced server-side.                       | Implemented |
| 7 | Logout invalidates the session and returns to the Login page.             | Implemented |
| 8 | Database initialization does not overwrite existing administrator creds.  | Implemented |
| 9 | Automated authentication tests pass.                                      | Implemented |

## 7. Related Files

* `scripts/initialize_database.py` — schema initialization, migration, and
  `adminwit` provisioning (`_ensure_admin_user`, `_migrate_users_table`).
* `database/schema.py` — `users` table definition including
  `password_changed_at`.
* `services/auth_service.py` — `AuthService`: login, `change_password`,
  password hashing (PBKDF2-HMAC-SHA256), rate limiting, session, role checks.
* `models/user.py` — `User` model with `must_change_password` property.
* `ui/login_page.py` / `ui/login_dialog.py` — login UI.
* `ui/password_change_dialog.py` — mandatory first-login password change UI.
* `ui/main_window.py` — role-based route protection and logout.
* `main.py` — login → [password change] → dashboard session loop.
* `tests/test_auth_service.py` — authentication service tests (22 tests).
* `tests/test_login_page.py` — login page UI tests (10 tests).
* `tests/test_password_change_dialog.py` — password change dialog tests (7 tests).

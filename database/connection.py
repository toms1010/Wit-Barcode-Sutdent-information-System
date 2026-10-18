"""MySQL connection management.

Error-handling contract
-----------------------
* A database failure raises :class:`DatabaseUnavailableError`.
* Repository methods never convert a connection failure into an empty result
  set. ``get_all()`` returning ``[]`` therefore always means "the query ran and
  matched no rows".
* Pages catch :class:`DatabaseUnavailableError` at the data-loading boundary,
  surface an actionable message, and offer an explicit retry.
* Connection attempts are throttled so a down database cannot flood the log or
  spin in a tight loop.
"""

import logging
import threading
import time
from contextlib import contextmanager
from typing import Optional

import mysql.connector
from mysql.connector import pooling
from mysql.connector import Error as MySQLError

from config.settings import settings

logger = logging.getLogger(__name__)

# Minimum seconds between connection attempts after a failure.
RETRY_INTERVAL_SECONDS = 30.0
# Minimum seconds between repeated failure log lines.
LOG_THROTTLE_SECONDS = 60.0

_pool: Optional[pooling.MySQLConnectionPool] = None
_lock = threading.Lock()
_last_attempt: float = 0.0
_last_failure_log: float = 0.0
_last_error: str = ""


class DatabaseUnavailableError(RuntimeError):
    """The configured MySQL account could not connect.

    Attributes
    ----------
    original:
        The underlying driver exception, kept for diagnostics. It is never
        logged directly so that connection details are not leaked.
    """

    def __init__(self, message: str, original: Optional[BaseException] = None):
        super().__init__(message)
        self.original = original

    @property
    def detail(self) -> str:
        """Driver-level detail, for an explicit diagnostic command only."""
        return f"{type(self.original).__name__}: {self.original}" if self.original else ""


def _throttled_warn(reason: str) -> None:
    global _last_failure_log
    now = time.monotonic()
    if now - _last_failure_log >= LOG_THROTTLE_SECONDS:
        _last_failure_log = now
        logger.warning("Database unavailable: %s", reason)


def get_connection_pool(force: bool = False) -> pooling.MySQLConnectionPool:
    """Return the shared connection pool, creating it on first use.

    Parameters
    ----------
    force:
        Bypass the retry-interval backoff and attempt a new connection. Used by
        explicit user-initiated retries.

    Raises
    ------
    DatabaseUnavailableError
        If the pool cannot be created, or an attempt is skipped due to backoff.
    """
    global _pool, _last_attempt, _last_error

    with _lock:
        if _pool is not None:
            return _pool

        now = time.monotonic()
        if not force and _last_attempt and (now - _last_attempt) < RETRY_INTERVAL_SECONDS:
            _throttled_warn(
                f"skipping connect attempt (retry in "
                f"{int(RETRY_INTERVAL_SECONDS - (now - _last_attempt))}s): {_last_error}"
            )
            raise DatabaseUnavailableError(
                "Database is not reachable. A retry is being rate-limited; "
                "use Retry to try again immediately.",
                None,
            )

        _last_attempt = now
        try:
            _pool = pooling.MySQLConnectionPool(
                pool_name="wit_pool",
                pool_size=10,
                **settings.get_database_config(),
            )
        except MySQLError as exc:
            _pool = None
            _last_error = f"{type(exc).__name__}: {exc}"
            _throttled_warn(
                f"could not create connection pool for "
                f"{settings.DB_USER}@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME} "
                f"({type(exc).__name__})"
            )
            raise DatabaseUnavailableError(
                f"Could not connect to MySQL database '{settings.DB_NAME}' as user "
                f"'{settings.DB_USER}' on {settings.DB_HOST}:{settings.DB_PORT}. "
                f"Verify the database exists and the account has the required privileges. "
                f"See DATABASE_SETUP.md.",
                exc,
            ) from exc

        logger.info("Database connection pool created")
        return _pool


@contextmanager
def get_connection():
    """Yield a pooled connection. Returns it to the pool on exit."""
    pool = get_connection_pool()
    conn = None
    try:
        conn = pool.get_connection()
        if conn is None:
            raise DatabaseUnavailableError("Connection pool returned no connection.")
        yield conn
    except DatabaseUnavailableError:
        raise
    except MySQLError as exc:
        _throttled_warn(f"connection error: {type(exc).__name__}")
        raise DatabaseUnavailableError(
            "Lost the database connection while running a query.", exc
        ) from exc
    finally:
        if conn is not None:
            try:
                if conn.is_connected():
                    conn.close()
            except MySQLError:
                pass


@contextmanager
def get_cursor(commit: bool = False):
    """Yield a dict cursor, committing or rolling back as appropriate.

    ``commit=False`` is a genuine dry run: the transaction is rolled back when
    the block exits, including on the success path. That guarantee depends on
    the connection not being in autocommit mode -- with ``autocommit=True`` every
    statement commits immediately and this rollback would do nothing.
    """
    with get_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            yield cursor
            if commit:
                conn.commit()
        except DatabaseUnavailableError:
            raise
        except MySQLError as exc:
            try:
                conn.rollback()
            except MySQLError:
                pass
            raise DatabaseUnavailableError(
                "The database rejected a query. The change was rolled back.", exc
            ) from exc
        finally:
            if not commit:
                # Discard writes made inside a dry-run block.
                try:
                    conn.rollback()
                except MySQLError:
                    pass
            try:
                cursor.close()
            except MySQLError:
                pass


def existing_user_id(user_id: Optional[int]) -> Optional[int]:
    """Return ``user_id`` only if that account exists, else ``None``.

    Audit columns on ``barcodes`` and ``id_cards`` (``generated_by``,
    ``regenerated_by``) are foreign keys onto ``users(id)``. The application has
    no sign-in, so the acting user comes from ``settings.DEFAULT_USER_ID`` --
    but a deployment whose ``users`` table is empty (which is exactly the state
    of a freshly initialised database here) has no row 1 to point at. Writing it
    anyway does not silently lose the audit value; it raises
    ``ERROR 1452 (23000): a foreign key constraint fails`` and the whole insert
    rolls back, so the operator sees a generation failure caused by a missing
    login rather than by anything wrong with the card.

    Returning ``None`` instead means the audit column stays NULL -- which is
    accurate: no identified user performed the action -- and the insert
    succeeds.
    """
    if user_id is None:
        return None
    try:
        candidate = int(user_id)
    except (TypeError, ValueError):
        return None

    with get_cursor() as cursor:
        cursor.execute("SELECT id FROM users WHERE id = %s LIMIT 1", (candidate,))
        row = cursor.fetchone()
    if row is None:
        logger.info(
            "Configured acting user id %s does not exist; recording the action "
            "with a NULL audit column instead.",
            candidate,
        )
        return None
    return int(row["id"])


def reset_connection_pool() -> None:
    """Discard the cached pool and the retry backoff so the next call reconnects.

    Clearing ``_last_attempt`` is essential: this is what the UI's Retry action
    calls, and without it the fresh attempt would be rejected by the throttle
    and fail immediately.
    """
    global _pool, _last_attempt, _last_error
    with _lock:
        _pool = None
        _last_attempt = 0.0
        _last_error = ""
    logger.info("Connection pool reset; next query will reconnect")


def test_connection() -> bool:
    """Return True if ``SELECT 1`` succeeds. Never raises."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            finally:
                cursor.close()
        return True
    except DatabaseUnavailableError:
        return False


def initialize_database() -> bool:
    """Create tables and seed reference rows. Returns False on failure."""
    from database.schema import SCHEMA_SQL

    try:
        with get_cursor(commit=True) as cursor:
            for statement in SCHEMA_SQL.split(";"):
                statement = statement.strip()
                if statement:
                    cursor.execute(statement)
    except DatabaseUnavailableError as exc:
        logger.error("Database initialization aborted: %s", exc)
        return False
    except MySQLError as exc:
        logger.error("Database initialization failed: %s", type(exc).__name__)
        return False
    logger.info("Database schema initialized successfully")
    return True

"""Regression tests for the database error-handling contract.

Contract under test:

* A connection failure raises ``DatabaseUnavailableError``; it is never
  converted into an empty result set.
* ``get_all()`` returning ``[]`` means the query ran and matched no rows.
* Repeated failures are throttled rather than reconnecting in a loop.
* An explicit retry bypasses the throttle.
"""

import pytest
from unittest.mock import Mock, patch

from database.connection import (
    DatabaseUnavailableError,
    get_connection,
    get_connection_pool,
    get_cursor,
    reset_connection_pool,
)
from database.connection import test_connection as probe_connection


class TestConnectionFailureContract:
    def test_pool_creation_failure_raises_domain_error(self):
        from mysql.connector import Error as MySQLError

        with patch(
            "database.connection.pooling.MySQLConnectionPool",
            side_effect=MySQLError(msg="Access denied"),
        ):
            with pytest.raises(DatabaseUnavailableError) as exc_info:
                get_connection_pool()

        message = str(exc_info.value)
        # Message must be actionable and must not leak the password.
        assert "wit_user" in message
        assert "wit_barcode_system" in message
        assert "wit_password" not in message

    def test_original_exception_is_preserved_for_diagnostics(self):
        from mysql.connector import Error as MySQLError

        with patch(
            "database.connection.pooling.MySQLConnectionPool",
            side_effect=MySQLError(msg="boom"),
        ):
            with pytest.raises(DatabaseUnavailableError) as exc_info:
                get_connection_pool()

        assert exc_info.value.original is not None
        assert "boom" in exc_info.value.detail

    def test_repository_does_not_fabricate_empty_results(self):
        """get_all must raise rather than pretend the table is empty."""
        from mysql.connector import Error as MySQLError
        from database.repositories import StudentRepository

        with patch(
            "database.connection.pooling.MySQLConnectionPool",
            side_effect=MySQLError(msg="Access denied"),
        ):
            repo = StudentRepository()
            with pytest.raises(DatabaseUnavailableError):
                repo.get_all(limit=10)

    def test_repeat_failures_are_throttled(self):
        from mysql.connector import Error as MySQLError

        with patch(
            "database.connection.pooling.MySQLConnectionPool",
            side_effect=MySQLError(msg="down"),
        ) as mock_pool:
            with pytest.raises(DatabaseUnavailableError):
                get_connection_pool()
            first_calls = mock_pool.call_count

            # Second attempt inside the backoff window must not reconnect.
            with pytest.raises(DatabaseUnavailableError):
                get_connection_pool()
            assert mock_pool.call_count == first_calls, (
                "connection was retried inside the backoff window"
            )

    def test_force_retry_bypasses_throttle(self):
        from mysql.connector import Error as MySQLError

        with patch(
            "database.connection.pooling.MySQLConnectionPool",
            side_effect=MySQLError(msg="down"),
        ) as mock_pool:
            with pytest.raises(DatabaseUnavailableError):
                get_connection_pool()
            with pytest.raises(DatabaseUnavailableError):
                get_connection_pool(force=True)

            assert mock_pool.call_count == 2, (
                "force=True must perform a fresh connection attempt"
            )

    def test_reset_pool_forces_reconnect(self):
        from mysql.connector import Error as MySQLError

        with patch(
            "database.connection.pooling.MySQLConnectionPool",
            side_effect=MySQLError(msg="down"),
        ) as mock_pool:
            with pytest.raises(DatabaseUnavailableError):
                get_connection_pool()
            reset_connection_pool()
            with pytest.raises(DatabaseUnavailableError):
                get_connection_pool()

            assert mock_pool.call_count == 2

    def test_probe_returns_false_without_raising(self):
        from mysql.connector import Error as MySQLError

        with patch(
            "database.connection.pooling.MySQLConnectionPool",
            side_effect=MySQLError(msg="down"),
        ):
            assert probe_connection() is False


class TestSuccessPathStillWorks:
    def test_cursor_yields_and_closes(self):
        """The happy path must be unaffected by the new error handling."""
        mock_pool = Mock()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_pool.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        with patch("database.connection.get_connection_pool", return_value=mock_pool):
            with get_cursor() as cursor:
                assert cursor is mock_cursor

        mock_cursor.close.assert_called_once()

    def test_empty_query_result_is_still_a_list(self):
        """A successful query with no rows must yield [], not raise."""
        mock_pool = Mock()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_pool.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []

        with patch("database.connection.get_connection_pool", return_value=mock_pool):
            from database.repositories import StudentRepository

            repo = StudentRepository()
            result = repo.get_all(limit=5)

        assert result == []

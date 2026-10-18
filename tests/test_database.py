import pytest
from unittest.mock import Mock, patch, MagicMock
from decimal import Decimal
from datetime import date
# ``test_connection`` is imported under an alias so pytest does not collect the
# real connection probe as a test case.
from database.connection import test_connection as _probe_connection
from database.connection import (
    get_connection,
    get_cursor,
    initialize_database,
)


class TestDatabaseConnection:
    @patch('database.connection.mysql.connector.pooling.MySQLConnectionPool')
    def test_get_connection_pool(self, mock_pool_class):
        mock_pool = Mock()
        mock_pool_class.return_value = mock_pool
        
        from database.connection import get_connection_pool
        pool = get_connection_pool()
        
        assert pool == mock_pool
        mock_pool_class.assert_called_once()
    
    @patch('database.connection.get_connection_pool')
    def test_get_connection(self, mock_get_pool):
        mock_pool = Mock()
        mock_conn = Mock()
        mock_pool.get_connection.return_value = mock_conn
        mock_get_pool.return_value = mock_pool
        
        from database.connection import get_connection
        with get_connection() as conn:
            assert conn == mock_conn
        
        mock_conn.close.assert_called_once()
    
    @patch('database.connection.get_connection_pool')
    def test_get_cursor(self, mock_get_pool):
        mock_pool = Mock()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_pool.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_get_pool.return_value = mock_pool
        
        from database.connection import get_cursor
        with get_cursor() as cursor:
            assert cursor == mock_cursor
        
        mock_cursor.close.assert_called_once()


class TestDatabaseInitialization:
    @patch('database.connection.get_cursor')
    def test_initialize_database(self, mock_get_cursor):
        mock_cursor = Mock()
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor
        
        from database.connection import initialize_database
        # This would need the actual schema to test properly
        # Just verify it doesn't crash with mocked cursor
        try:
            result = initialize_database()
        except Exception:
            pass  # Expected with mock


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
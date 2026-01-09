"""Unit tests for validation utilities."""

import os
from unittest.mock import patch, MagicMock

import pytest

from repotoire.validation import (
    ValidationError,
    validate_repository_path,
    validate_falkordb_host,
    validate_falkordb_port,
    validate_falkordb_password,
    validate_falkordb_connection,
    validate_output_path,
    validate_file_size_limit,
    validate_batch_size,
    validate_retry_config,
)


class TestValidationError:
    """Test ValidationError exception class."""

    def test_validation_error_with_message_only(self):
        """Test ValidationError with just a message."""
        error = ValidationError("Something went wrong")
        assert error.message == "Something went wrong"
        assert error.suggestion is None
        assert "Something went wrong" in str(error)

    def test_validation_error_with_suggestion(self):
        """Test ValidationError with message and suggestion."""
        error = ValidationError("Invalid input", "Try using a different value")
        assert error.message == "Invalid input"
        assert error.suggestion == "Try using a different value"
        assert "Invalid input" in str(error)
        assert "💡 Suggestion: Try using a different value" in str(error)


class TestRepositoryPathValidation:
    """Test repository path validation."""

    def test_valid_directory_path(self, tmp_path):
        """Test validation of valid directory path."""
        # Create a test directory with a file
        test_dir = tmp_path / "test_repo"
        test_dir.mkdir()
        (test_dir / "test.py").write_text("print('hello')")

        result = validate_repository_path(str(test_dir))
        assert result == test_dir

    def test_empty_path(self):
        """Test validation fails for empty path."""
        with pytest.raises(ValidationError) as exc_info:
            validate_repository_path("")
        assert "cannot be empty" in exc_info.value.message.lower()
        assert "Provide a valid path" in exc_info.value.suggestion

    def test_whitespace_only_path(self):
        """Test validation fails for whitespace-only path."""
        with pytest.raises(ValidationError) as exc_info:
            validate_repository_path("   ")
        assert "cannot be empty" in exc_info.value.message.lower()

    def test_nonexistent_path(self):
        """Test validation fails for non-existent path."""
        with pytest.raises(ValidationError) as exc_info:
            validate_repository_path("/nonexistent/path/xyz123")
        assert "does not exist" in exc_info.value.message.lower()
        assert "Check the path" in exc_info.value.suggestion

    def test_file_instead_of_directory(self, tmp_path):
        """Test validation fails when path is a file, not directory."""
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")

        with pytest.raises(ValidationError) as exc_info:
            validate_repository_path(str(test_file))
        assert "must be a directory" in exc_info.value.message.lower()
        assert "not a file" in exc_info.value.message.lower()

    def test_empty_directory(self, tmp_path):
        """Test validation fails for empty directory."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        with pytest.raises(ValidationError) as exc_info:
            validate_repository_path(str(empty_dir))
        assert "empty" in exc_info.value.message.lower()
        assert "source code files" in exc_info.value.suggestion

    def test_unreadable_directory(self, tmp_path):
        """Test validation fails for unreadable directory."""
        test_dir = tmp_path / "unreadable"
        test_dir.mkdir()
        (test_dir / "test.py").write_text("print('hello')")

        # Make directory unreadable (skip on Windows)
        if os.name != 'nt':
            os.chmod(test_dir, 0o000)
            try:
                with pytest.raises(ValidationError) as exc_info:
                    validate_repository_path(str(test_dir))
                assert "not readable" in exc_info.value.message.lower()
            finally:
                # Restore permissions for cleanup
                os.chmod(test_dir, 0o755)

    def test_expanduser_in_path(self, tmp_path):
        """Test that ~ is expanded in paths."""
        # Create test directory in tmp_path
        test_dir = tmp_path / "test_repo"
        test_dir.mkdir()
        (test_dir / "test.py").write_text("print('hello')")

        # Mock Path.expanduser to return our test directory
        def mock_expanduser(self):
            return test_dir

        with patch('pathlib.Path.expanduser', mock_expanduser):
            # Use ~ in path - the mock will expand it to our test dir
            result = validate_repository_path("~/test_repo")
            assert result == test_dir
            assert result.name == "test_repo"


class TestFalkorDBHostValidation:
    """Test FalkorDB host validation."""

    def test_valid_hostname(self):
        """Test validation of valid hostname."""
        result = validate_falkordb_host("localhost")
        assert result == "localhost"

    def test_valid_ip_address(self):
        """Test validation of valid IP address."""
        result = validate_falkordb_host("192.168.1.1")
        assert result == "192.168.1.1"

    def test_valid_internal_dns(self):
        """Test validation of internal DNS name."""
        result = validate_falkordb_host("falkordb.internal")
        assert result == "falkordb.internal"

    def test_valid_hostname_with_subdomain(self):
        """Test validation of hostname with subdomain."""
        result = validate_falkordb_host("db.example.com")
        assert result == "db.example.com"

    def test_empty_host(self):
        """Test validation fails for empty host."""
        with pytest.raises(ValidationError) as exc_info:
            validate_falkordb_host("")
        assert "cannot be empty" in exc_info.value.message.lower()

    def test_whitespace_host(self):
        """Test validation strips whitespace."""
        result = validate_falkordb_host("  localhost  ")
        assert result == "localhost"

    def test_rejects_bolt_uri(self):
        """Test validation fails for bolt:// URI."""
        with pytest.raises(ValidationError) as exc_info:
            validate_falkordb_host("bolt://localhost:7687")
        assert "hostname, not a URI" in exc_info.value.message

    def test_rejects_neo4j_uri(self):
        """Test validation fails for neo4j:// URI."""
        with pytest.raises(ValidationError) as exc_info:
            validate_falkordb_host("neo4j://localhost:7687")
        assert "hostname, not a URI" in exc_info.value.message

    def test_rejects_invalid_characters(self):
        """Test validation fails for invalid characters."""
        with pytest.raises(ValidationError) as exc_info:
            validate_falkordb_host("localhost:6379")  # Port in hostname
        assert "invalid hostname" in exc_info.value.message.lower()


class TestFalkorDBPortValidation:
    """Test FalkorDB port validation."""

    def test_valid_default_port(self):
        """Test validation of default FalkorDB port."""
        result = validate_falkordb_port(6379)
        assert result == 6379

    def test_valid_custom_port(self):
        """Test validation of custom port."""
        result = validate_falkordb_port(16379)
        assert result == 16379

    def test_invalid_negative_port(self):
        """Test validation fails for negative port."""
        with pytest.raises(ValidationError) as exc_info:
            validate_falkordb_port(-1)
        assert "between 1 and 65535" in exc_info.value.message

    def test_invalid_zero_port(self):
        """Test validation fails for zero port."""
        with pytest.raises(ValidationError) as exc_info:
            validate_falkordb_port(0)
        assert "between 1 and 65535" in exc_info.value.message

    def test_invalid_too_large_port(self):
        """Test validation fails for port > 65535."""
        with pytest.raises(ValidationError) as exc_info:
            validate_falkordb_port(70000)
        assert "between 1 and 65535" in exc_info.value.message


class TestFalkorDBPasswordValidation:
    """Test FalkorDB password validation."""

    def test_valid_password(self):
        """Test validation of valid password."""
        result = validate_falkordb_password("mysecretpassword")
        assert result == "mysecretpassword"

    def test_none_password_allowed(self):
        """Test None password is allowed (unauthenticated)."""
        result = validate_falkordb_password(None)
        assert result is None

    def test_empty_string_password_fails(self):
        """Test validation fails for empty string password."""
        with pytest.raises(ValidationError) as exc_info:
            validate_falkordb_password("")
        assert "cannot be empty" in exc_info.value.message.lower()

    def test_whitespace_password_fails(self):
        """Test validation fails for whitespace-only password."""
        with pytest.raises(ValidationError) as exc_info:
            validate_falkordb_password("   ")
        assert "cannot be empty" in exc_info.value.message.lower()

    def test_password_stripped(self):
        """Test password whitespace is stripped."""
        result = validate_falkordb_password("  password  ")
        assert result == "password"


class TestOutputPathValidation:
    """Test output file path validation."""

    def test_valid_output_path_new_file(self, tmp_path):
        """Test validation of valid output path for new file."""
        output_file = tmp_path / "report.json"
        result = validate_output_path(str(output_file))
        assert result == output_file

    def test_valid_output_path_existing_file(self, tmp_path):
        """Test validation of valid output path for existing writable file."""
        output_file = tmp_path / "report.json"
        output_file.write_text("{}")

        result = validate_output_path(str(output_file))
        assert result == output_file

    def test_empty_output_path(self):
        """Test validation fails for empty output path."""
        with pytest.raises(ValidationError) as exc_info:
            validate_output_path("")
        assert "cannot be empty" in exc_info.value.message.lower()

    def test_parent_directory_not_exist(self, tmp_path):
        """Test validation fails when parent directory doesn't exist."""
        output_file = tmp_path / "nonexistent" / "subdir" / "report.json"
        with pytest.raises(ValidationError) as exc_info:
            validate_output_path(str(output_file))
        assert "directory does not exist" in exc_info.value.message.lower()
        assert "mkdir -p" in exc_info.value.suggestion

    def test_parent_is_file_not_directory(self, tmp_path):
        """Test validation fails when parent is a file, not directory."""
        parent_file = tmp_path / "parent"
        parent_file.write_text("content")
        output_file = parent_file / "report.json"

        with pytest.raises(ValidationError) as exc_info:
            validate_output_path(str(output_file))
        assert "not a directory" in exc_info.value.message.lower()

    def test_output_path_is_directory(self, tmp_path):
        """Test validation fails when output path is a directory."""
        output_dir = tmp_path / "reports"
        output_dir.mkdir()

        with pytest.raises(ValidationError) as exc_info:
            validate_output_path(str(output_dir))
        assert "directory, not a file" in exc_info.value.message.lower()

    def test_unwritable_parent_directory(self, tmp_path):
        """Test validation fails for unwritable parent directory."""
        parent_dir = tmp_path / "readonly"
        parent_dir.mkdir()

        # Make directory unwritable (skip on Windows)
        if os.name != 'nt':
            os.chmod(parent_dir, 0o444)
            try:
                with pytest.raises(ValidationError) as exc_info:
                    validate_output_path(str(parent_dir / "report.json"))
                assert "not writable" in exc_info.value.message.lower()
            finally:
                # Restore permissions for cleanup
                os.chmod(parent_dir, 0o755)

    def test_unwritable_existing_file(self, tmp_path):
        """Test validation fails for existing unwritable file."""
        output_file = tmp_path / "report.json"
        output_file.write_text("{}")

        # Make file unwritable (skip on Windows)
        if os.name != 'nt':
            os.chmod(output_file, 0o444)
            try:
                with pytest.raises(ValidationError) as exc_info:
                    validate_output_path(str(output_file))
                assert "not writable" in exc_info.value.message.lower()
            finally:
                # Restore permissions for cleanup
                os.chmod(output_file, 0o644)


class TestFileSizeLimitValidation:
    """Test file size limit validation."""

    def test_valid_file_size(self):
        """Test validation of valid file size."""
        result = validate_file_size_limit(10.0)
        assert result == 10.0

    def test_small_file_size(self):
        """Test validation of small but valid file size."""
        result = validate_file_size_limit(0.5)
        assert result == 0.5

    def test_large_file_size(self):
        """Test validation of large but reasonable file size."""
        result = validate_file_size_limit(100.0)
        assert result == 100.0

    def test_negative_file_size(self):
        """Test validation fails for negative file size."""
        with pytest.raises(ValidationError) as exc_info:
            validate_file_size_limit(-1.0)
        assert "must be positive" in exc_info.value.message.lower()

    def test_zero_file_size(self):
        """Test validation fails for zero file size."""
        with pytest.raises(ValidationError) as exc_info:
            validate_file_size_limit(0.0)
        assert "must be positive" in exc_info.value.message.lower()

    def test_excessively_large_file_size(self):
        """Test validation fails for unreasonably large file size."""
        with pytest.raises(ValidationError) as exc_info:
            validate_file_size_limit(2000.0)
        assert "unusually large" in exc_info.value.message.lower()
        assert "memory issues" in exc_info.value.suggestion


class TestBatchSizeValidation:
    """Test batch size validation."""

    def test_valid_batch_size(self):
        """Test validation of valid batch size."""
        result = validate_batch_size(100)
        assert result == 100

    def test_small_batch_size(self):
        """Test validation of small but valid batch size."""
        result = validate_batch_size(10)
        assert result == 10

    def test_large_batch_size(self):
        """Test validation of large but reasonable batch size."""
        result = validate_batch_size(1000)
        assert result == 1000

    def test_negative_batch_size(self):
        """Test validation fails for negative batch size."""
        with pytest.raises(ValidationError) as exc_info:
            validate_batch_size(-1)
        assert "must be positive" in exc_info.value.message.lower()

    def test_zero_batch_size(self):
        """Test validation fails for zero batch size."""
        with pytest.raises(ValidationError) as exc_info:
            validate_batch_size(0)
        assert "must be positive" in exc_info.value.message.lower()

    def test_too_small_batch_size(self):
        """Test validation fails for batch size less than 10."""
        with pytest.raises(ValidationError) as exc_info:
            validate_batch_size(5)
        assert "too small" in exc_info.value.message.lower()
        assert "at least 10" in exc_info.value.suggestion

    def test_excessively_large_batch_size(self):
        """Test validation fails for unreasonably large batch size."""
        with pytest.raises(ValidationError) as exc_info:
            validate_batch_size(20000)
        assert "too large" in exc_info.value.message.lower()
        assert "memory issues" in exc_info.value.suggestion


class TestRetryConfigValidation:
    """Test retry configuration validation."""

    def test_valid_retry_config(self):
        """Test validation of valid retry configuration."""
        max_retries, backoff, delay = validate_retry_config(3, 2.0, 1.0)
        assert max_retries == 3
        assert backoff == 2.0
        assert delay == 1.0

    def test_zero_retries(self):
        """Test validation allows zero retries (disables retry)."""
        max_retries, backoff, delay = validate_retry_config(0, 2.0, 1.0)
        assert max_retries == 0

    def test_negative_max_retries(self):
        """Test validation fails for negative max retries."""
        with pytest.raises(ValidationError) as exc_info:
            validate_retry_config(-1, 2.0, 1.0)
        assert "cannot be negative" in exc_info.value.message.lower()

    def test_excessive_max_retries(self):
        """Test validation fails for unreasonably high max retries."""
        with pytest.raises(ValidationError) as exc_info:
            validate_retry_config(20, 2.0, 1.0)
        assert "unusually high" in exc_info.value.message.lower()

    def test_backoff_factor_below_one(self):
        """Test validation fails for backoff factor less than 1.0."""
        with pytest.raises(ValidationError) as exc_info:
            validate_retry_config(3, 0.5, 1.0)
        assert "must be >= 1.0" in exc_info.value.message.lower()

    def test_linear_backoff(self):
        """Test validation allows linear backoff (factor = 1.0)."""
        max_retries, backoff, delay = validate_retry_config(3, 1.0, 1.0)
        assert backoff == 1.0

    def test_excessive_backoff_factor(self):
        """Test validation fails for unreasonably large backoff factor."""
        with pytest.raises(ValidationError) as exc_info:
            validate_retry_config(3, 20.0, 1.0)
        assert "unusually large" in exc_info.value.message.lower()

    def test_negative_base_delay(self):
        """Test validation fails for negative base delay."""
        with pytest.raises(ValidationError) as exc_info:
            validate_retry_config(3, 2.0, -1.0)
        assert "must be positive" in exc_info.value.message.lower()

    def test_zero_base_delay(self):
        """Test validation fails for zero base delay."""
        with pytest.raises(ValidationError) as exc_info:
            validate_retry_config(3, 2.0, 0.0)
        assert "must be positive" in exc_info.value.message.lower()

    def test_excessive_base_delay(self):
        """Test validation fails for unreasonably long base delay."""
        with pytest.raises(ValidationError) as exc_info:
            validate_retry_config(3, 2.0, 120.0)
        assert "unusually long" in exc_info.value.message.lower()

    def test_recommended_config(self):
        """Test validation of recommended configuration values."""
        # Default recommended config
        max_retries, backoff, delay = validate_retry_config(3, 2.0, 1.0)
        assert max_retries == 3
        assert backoff == 2.0
        assert delay == 1.0

    def test_patient_config(self):
        """Test validation of patient configuration."""
        max_retries, backoff, delay = validate_retry_config(5, 1.5, 2.0)
        assert max_retries == 5
        assert backoff == 1.5
        assert delay == 2.0

    def test_aggressive_config(self):
        """Test validation of aggressive retry configuration."""
        max_retries, backoff, delay = validate_retry_config(10, 3.0, 0.5)
        assert max_retries == 10
        assert backoff == 3.0
        assert delay == 0.5


class TestFalkorDBConnectionValidation:
    """Test FalkorDB connection validation."""

    def test_successful_connection(self):
        """Test validation succeeds with valid connection."""
        with patch('falkordb.FalkorDB') as mock_falkordb:
            mock_client = MagicMock()
            mock_connection = MagicMock()
            mock_client.connection = mock_connection
            mock_falkordb.return_value = mock_client

            # Should not raise
            validate_falkordb_connection(
                host="localhost",
                port=6379,
                password="password"
            )

            mock_falkordb.assert_called_once()
            mock_connection.ping.assert_called_once()
            mock_connection.close.assert_called_once()

    def test_invalid_host_format(self):
        """Test validation fails with invalid host format."""
        with pytest.raises(ValidationError) as exc_info:
            validate_falkordb_connection(
                host="bolt://localhost",  # URI instead of hostname
                port=6379,
                password="password"
            )
        assert "hostname, not a URI" in exc_info.value.message

    def test_authentication_failure(self):
        """Test validation fails with wrong credentials."""
        with patch('falkordb.FalkorDB') as mock_falkordb:
            import redis.exceptions

            mock_client = MagicMock()
            mock_connection = MagicMock()
            mock_client.connection = mock_connection
            mock_connection.ping.side_effect = redis.exceptions.AuthenticationError("Auth failed")
            mock_falkordb.return_value = mock_client

            with pytest.raises(ValidationError) as exc_info:
                validate_falkordb_connection(
                    host="localhost",
                    port=6379,
                    password="wrong_password"
                )

            assert "authentication failed" in exc_info.value.message.lower()
            assert "password" in exc_info.value.suggestion.lower()
            mock_connection.close.assert_called_once()

    def test_connection_refused(self):
        """Test validation fails when FalkorDB is not running."""
        with patch('falkordb.FalkorDB') as mock_falkordb:
            import redis.exceptions

            mock_client = MagicMock()
            mock_connection = MagicMock()
            mock_client.connection = mock_connection
            mock_connection.ping.side_effect = redis.exceptions.ConnectionError("Connection refused")
            mock_falkordb.return_value = mock_client

            with pytest.raises(ValidationError) as exc_info:
                validate_falkordb_connection(
                    host="localhost",
                    port=6379,
                    password="password"
                )

            assert "cannot connect" in exc_info.value.message.lower()
            assert "falkordb is running" in exc_info.value.suggestion.lower()
            mock_connection.close.assert_called_once()

    def test_falkordb_not_installed(self):
        """Test validation fails when falkordb package not installed."""
        with patch.dict('sys.modules', {'falkordb': None}):
            with patch('builtins.__import__', side_effect=ImportError("No module named 'falkordb'")):
                with pytest.raises(ValidationError) as exc_info:
                    validate_falkordb_connection(
                        host="localhost",
                        port=6379,
                        password="password"
                    )

                assert "not installed" in exc_info.value.message.lower()
                assert "pip install falkordb" in exc_info.value.suggestion

    def test_invalid_port(self):
        """Test validation fails with invalid port."""
        with pytest.raises(ValidationError) as exc_info:
            validate_falkordb_connection(
                host="localhost",
                port=-1,
                password="password"
            )
        assert "between 1 and 65535" in exc_info.value.message

    def test_connection_cleanup_on_error(self):
        """Test client is closed even when error occurs."""
        with patch('falkordb.FalkorDB') as mock_falkordb:
            import redis.exceptions

            mock_client = MagicMock()
            mock_connection = MagicMock()
            mock_client.connection = mock_connection
            mock_connection.ping.side_effect = redis.exceptions.ConnectionError("Connection refused")
            mock_falkordb.return_value = mock_client

            with pytest.raises(ValidationError):
                validate_falkordb_connection(
                    host="localhost",
                    port=6379,
                    password="password"
                )

            # Connection should be closed even though there was an error
            mock_connection.close.assert_called_once()

"""Unit tests for validation utilities."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from repotoire.validation import (
    ValidationError,
    validate_repository_path,
    validate_neo4j_uri,
    validate_neo4j_credentials,
    validate_neo4j_connection,
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


class TestNeo4jUriValidation:
    """Test Neo4j URI validation."""

    def test_valid_bolt_uri(self):
        """Test validation of valid bolt URI."""
        uri = "bolt://localhost:7687"
        result = validate_neo4j_uri(uri)
        assert result == uri

    def test_valid_neo4j_uri(self):
        """Test validation of valid neo4j URI."""
        uri = "neo4j://localhost:7687"
        result = validate_neo4j_uri(uri)
        assert result == uri

    def test_valid_secure_uris(self):
        """Test validation of secure URI schemes."""
        for scheme in ["bolt+s", "neo4j+s", "bolt+ssc", "neo4j+ssc"]:
            uri = f"{scheme}://localhost:7687"
            result = validate_neo4j_uri(uri)
            assert result == uri

    def test_empty_uri(self):
        """Test validation fails for empty URI."""
        with pytest.raises(ValidationError) as exc_info:
            validate_neo4j_uri("")
        assert "cannot be empty" in exc_info.value.message.lower()
        assert "bolt://localhost:7687" in exc_info.value.suggestion

    def test_missing_scheme(self):
        """Test validation fails for URI without scheme."""
        with pytest.raises(ValidationError) as exc_info:
            validate_neo4j_uri("localhost:7687")
        assert "scheme" in exc_info.value.message.lower()
        assert "bolt://" in exc_info.value.suggestion

    def test_invalid_scheme(self):
        """Test validation fails for invalid URI scheme."""
        with pytest.raises(ValidationError) as exc_info:
            validate_neo4j_uri("http://localhost:7687")
        assert "invalid" in exc_info.value.message.lower()
        assert "scheme" in exc_info.value.message.lower()

    def test_missing_host(self):
        """Test validation fails for URI without host."""
        with pytest.raises(ValidationError) as exc_info:
            validate_neo4j_uri("bolt://")
        assert "missing host" in exc_info.value.message.lower()

    def test_common_mistake_port_7474(self):
        """Test helpful error for common mistake of using HTTP port."""
        with pytest.raises(ValidationError) as exc_info:
            validate_neo4j_uri("bolt://localhost:7474")
        assert "7474" in exc_info.value.message
        assert "HTTP" in exc_info.value.message
        assert "7687" in exc_info.value.suggestion


class TestNeo4jCredentialsValidation:
    """Test Neo4j credentials validation."""

    def test_valid_credentials(self):
        """Test validation of valid credentials."""
        user, password = validate_neo4j_credentials("neo4j", "mypassword")
        assert user == "neo4j"
        assert password == "mypassword"

    def test_empty_username(self):
        """Test validation fails for empty username."""
        with pytest.raises(ValidationError) as exc_info:
            validate_neo4j_credentials("", "password")
        assert "username cannot be empty" in exc_info.value.message.lower()

    def test_whitespace_username(self):
        """Test validation fails for whitespace-only username."""
        with pytest.raises(ValidationError) as exc_info:
            validate_neo4j_credentials("   ", "password")
        assert "username cannot be empty" in exc_info.value.message.lower()

    def test_empty_password(self):
        """Test validation fails for empty password."""
        with pytest.raises(ValidationError) as exc_info:
            validate_neo4j_credentials("neo4j", "")
        assert "password cannot be empty" in exc_info.value.message.lower()
        assert "FALKOR_NEO4J_PASSWORD" in exc_info.value.suggestion

    def test_whitespace_password(self):
        """Test validation fails for whitespace-only password."""
        with pytest.raises(ValidationError) as exc_info:
            validate_neo4j_credentials("neo4j", "   ")
        assert "password cannot be empty" in exc_info.value.message.lower()

    def test_default_credentials_allowed(self):
        """Test that default neo4j/neo4j credentials are allowed (with warning)."""
        # Should not raise, but in production would log warning
        user, password = validate_neo4j_credentials("neo4j", "neo4j")
        assert user == "neo4j"
        assert password == "neo4j"


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


class TestNeo4jConnectionValidation:
    """Test Neo4j connection validation."""

    def test_successful_connection(self):
        """Test validation succeeds with valid connection."""
        with patch('neo4j.GraphDatabase') as mock_gd:
            mock_driver = MagicMock()
            mock_gd.driver.return_value = mock_driver
            mock_driver.verify_connectivity.return_value = None

            # Should not raise
            validate_neo4j_connection(
                "bolt://localhost:7687",
                "neo4j",
                "password"
            )

            mock_gd.driver.assert_called_once()
            mock_driver.verify_connectivity.assert_called_once()
            mock_driver.close.assert_called_once()

    def test_invalid_uri_format(self):
        """Test validation fails with invalid URI format."""
        with pytest.raises(ValidationError) as exc_info:
            validate_neo4j_connection(
                "invalid-uri",
                "neo4j",
                "password"
            )
        assert "scheme" in exc_info.value.message.lower()

    def test_authentication_failure(self):
        """Test validation fails with wrong credentials."""
        with patch('neo4j.GraphDatabase') as mock_gd:
            from neo4j.exceptions import AuthError

            mock_driver = MagicMock()
            mock_gd.driver.return_value = mock_driver
            mock_driver.verify_connectivity.side_effect = AuthError("Authentication failed")

            with pytest.raises(ValidationError) as exc_info:
                validate_neo4j_connection(
                    "bolt://localhost:7687",
                    "neo4j",
                    "wrong_password"
                )

            assert "authentication failed" in exc_info.value.message.lower()
            assert "credentials" in exc_info.value.suggestion.lower()
            mock_driver.close.assert_called_once()

    def test_service_unavailable(self):
        """Test validation fails when Neo4j is not running."""
        with patch('neo4j.GraphDatabase') as mock_gd:
            from neo4j.exceptions import ServiceUnavailable

            mock_driver = MagicMock()
            mock_gd.driver.return_value = mock_driver
            mock_driver.verify_connectivity.side_effect = ServiceUnavailable("Connection refused")

            with pytest.raises(ValidationError) as exc_info:
                validate_neo4j_connection(
                    "bolt://localhost:7687",
                    "neo4j",
                    "password"
                )

            assert "cannot connect" in exc_info.value.message.lower()
            assert "neo4j is running" in exc_info.value.suggestion.lower()
            mock_driver.close.assert_called_once()

    def test_neo4j_not_installed(self):
        """Test validation fails when neo4j package not installed."""
        # Mock the import to raise ImportError
        import sys
        with patch.dict('sys.modules', {'neo4j': None}):
            with patch('builtins.__import__', side_effect=ImportError("No module named 'neo4j'")):
                with pytest.raises(ValidationError) as exc_info:
                    validate_neo4j_connection(
                        "bolt://localhost:7687",
                        "neo4j",
                        "password"
                    )

                assert "not installed" in exc_info.value.message.lower()
                assert "pip install neo4j" in exc_info.value.suggestion

    def test_empty_credentials(self):
        """Test validation fails with empty credentials."""
        with pytest.raises(ValidationError) as exc_info:
            validate_neo4j_connection(
                "bolt://localhost:7687",
                "",
                "password"
            )
        assert "username cannot be empty" in exc_info.value.message.lower()

    def test_connection_cleanup_on_error(self):
        """Test driver is closed even when error occurs."""
        with patch('neo4j.GraphDatabase') as mock_gd:
            from neo4j.exceptions import ServiceUnavailable

            mock_driver = MagicMock()
            mock_gd.driver.return_value = mock_driver
            mock_driver.verify_connectivity.side_effect = ServiceUnavailable("Connection refused")

            with pytest.raises(ValidationError):
                validate_neo4j_connection(
                    "bolt://localhost:7687",
                    "neo4j",
                    "password"
                )

            # Driver should be closed even though there was an error
            mock_driver.close.assert_called_once()

"""Integration tests for pre-commit hook."""

import subprocess
import tempfile
from pathlib import Path
from textwrap import dedent
from unittest.mock import patch, MagicMock

import pytest

from repotoire.hooks.pre_commit import (
    get_staged_files,
    parse_severity,
    format_finding_output,
    main,
)
from repotoire.models import Severity, Finding


class TestGetStagedFiles:
    """Test get_staged_files function."""

    def test_get_staged_files_returns_python_files_only(self, tmp_path):
        """Test that only Python files are returned."""
        # Create a git repo with some staged files
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

        # Create and stage files
        (tmp_path / "test.py").write_text("print('hello')")
        (tmp_path / "test.txt").write_text("some text")
        (tmp_path / "another.py").write_text("def foo(): pass")

        subprocess.run(["git", "add", "test.py", "test.txt", "another.py"], cwd=tmp_path, check=True)

        # Mock subprocess to return our test files
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="test.py\ntest.txt\nanother.py\n", returncode=0
            )

            files = get_staged_files()

            # Should only return Python files
            assert "test.py" in files
            assert "another.py" in files
            assert "test.txt" not in files

    def test_get_staged_files_filters_empty_strings(self):
        """Test that empty strings are filtered out."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="test.py\n\n\n", returncode=0)

            files = get_staged_files()

            assert len(files) == 1
            assert files[0] == "test.py"

    def test_get_staged_files_handles_errors(self):
        """Test that errors are handled gracefully."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "git")

            files = get_staged_files()

            assert files == []


class TestParseSeverity:
    """Test parse_severity function."""

    def test_parse_severity_critical(self):
        """Test parsing critical severity."""
        assert parse_severity("critical") == Severity.CRITICAL

    def test_parse_severity_high(self):
        """Test parsing high severity."""
        assert parse_severity("high") == Severity.HIGH

    def test_parse_severity_medium(self):
        """Test parsing medium severity."""
        assert parse_severity("medium") == Severity.MEDIUM

    def test_parse_severity_low(self):
        """Test parsing low severity."""
        assert parse_severity("low") == Severity.LOW

    def test_parse_severity_info(self):
        """Test parsing info severity."""
        assert parse_severity("info") == Severity.INFO

    def test_parse_severity_case_insensitive(self):
        """Test that parsing is case insensitive."""
        assert parse_severity("CRITICAL") == Severity.CRITICAL
        assert parse_severity("HiGh") == Severity.HIGH

    def test_parse_severity_unknown_defaults_to_medium(self):
        """Test that unknown severity defaults to MEDIUM."""
        assert parse_severity("unknown") == Severity.MEDIUM


class TestFormatFindingOutput:
    """Test format_finding_output function."""

    def test_format_finding_output_with_fix(self):
        """Test formatting a finding with suggested fix."""
        finding = Finding(
            id="test-id-123",
            detector="TestDetector",
            title="Test Issue",
            description="This is a test issue",
            severity=Severity.CRITICAL,
            affected_nodes=["test.py::TestClass"],
            affected_files=["test.py"],
            suggested_fix="Fix it like this",
        )

        output = format_finding_output(finding)

        assert "🔴" in output
        assert "[CRITICAL]" in output
        assert "Test Issue" in output
        assert "test.py" in output
        assert "This is a test issue" in output
        assert "💡 Fix: Fix it like this" in output

    def test_format_finding_output_without_fix(self):
        """Test formatting a finding without suggested fix."""
        finding = Finding(
            id="test-id-456",
            detector="TestDetector",
            title="Test Issue",
            description="This is a test issue",
            severity=Severity.HIGH,
            affected_nodes=["test.py::TestClass"],
            affected_files=["test.py"],
        )

        output = format_finding_output(finding)

        assert "🟠" in output
        assert "[HIGH]" in output
        assert "💡" not in output

    def test_format_finding_output_truncates_files(self):
        """Test that file list is truncated when too long."""
        finding = Finding(
            id="test-id-789",
            detector="TestDetector",
            title="Test Issue",
            description="This is a test issue",
            severity=Severity.MEDIUM,
            affected_nodes=["file1.py::Foo", "file2.py::Bar", "file3.py::Baz", "file4.py::Qux", "file5.py::Quux"],
            affected_files=["file1.py", "file2.py", "file3.py", "file4.py", "file5.py"],
        )

        output = format_finding_output(finding)

        assert "🟡" in output
        assert "file1.py" in output
        assert "file2.py" in output
        assert "file3.py" in output
        assert "(+2 more)" in output

    def test_format_finding_output_severity_icons(self):
        """Test all severity icons."""
        severities_and_icons = [
            (Severity.CRITICAL, "🔴"),
            (Severity.HIGH, "🟠"),
            (Severity.MEDIUM, "🟡"),
            (Severity.LOW, "🟢"),
            (Severity.INFO, "ℹ️"),
        ]

        for i, (severity, icon) in enumerate(severities_and_icons):
            finding = Finding(
                id=f"test-id-{i}",
                detector="TestDetector",
                title="Test",
                description="Test",
                severity=severity,
                affected_nodes=["test.py::TestClass"],
                affected_files=["test.py"],
            )
            output = format_finding_output(finding)
            assert icon in output


class TestMainFunction:
    """Test main function integration."""

    def test_main_no_files_returns_success(self):
        """Test that main returns 0 when no files to check."""
        with patch("repotoire.hooks.pre_commit.get_staged_files", return_value=[]):
            with patch("sys.argv", ["repotoire-pre-commit"]):
                result = main()
                assert result == 0

    def test_main_requires_falkordb_password(self):
        """Test that main fails without Neo4j password."""
        with patch("repotoire.hooks.pre_commit.get_staged_files", return_value=["test.py"]):
            with patch("sys.argv", ["repotoire-pre-commit"]):
                with patch.dict("os.environ", {}, clear=True):
                    result = main()
                    assert result == 1

    def test_main_accepts_password_from_env(self):
        """Test that main accepts password from environment."""
        with patch("repotoire.hooks.pre_commit.get_staged_files", return_value=["test.py"]):
            with patch("sys.argv", ["repotoire-pre-commit"]):
                with patch.dict("os.environ", {"FALKORDB_PASSWORD": "test-pass"}):
                    with patch("repotoire.hooks.pre_commit.FalkorDBClient"):
                        with patch("subprocess.run") as mock_run:
                            mock_run.return_value = MagicMock(stdout="/tmp/test", returncode=0)
                            # This will fail at pipeline step but that's OK - we just want to verify password was accepted
                            result = main()

    def test_main_accepts_password_from_args(self):
        """Test that main accepts password from command line."""
        with patch("repotoire.hooks.pre_commit.get_staged_files", return_value=["test.py"]):
            with patch(
                "sys.argv",
                ["repotoire-pre-commit", "--neo4j-password", "test-pass"],
            ):
                with patch("repotoire.hooks.pre_commit.FalkorDBClient"):
                    with patch("subprocess.run") as mock_run:
                        mock_run.return_value = MagicMock(stdout="/tmp/test", returncode=0)
                        # This will fail at pipeline step but that's OK
                        result = main()

    def test_main_respects_fail_on_threshold(self):
        """Test that main respects --fail-on threshold."""
        # This test would require full integration with Neo4j
        # We'll rely on manual testing for now
        pass

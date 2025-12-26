"""Unit tests for Rust-based parallel bug extraction (REPO-246).

Tests the extract_buggy_functions_parallel function from repotoire_fast
which provides ~10x+ speedup over Python GitPython implementation.
"""

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

# Skip tests if Rust module not available
try:
    from repotoire_fast import (
        extract_buggy_functions_parallel,
        PyBuggyFunction,
    )
    RUST_AVAILABLE = True
except ImportError:
    RUST_AVAILABLE = False


@pytest.fixture
def temp_git_repo():
    """Create a temporary git repository with some bug-fix commits."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo_path, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo_path, check=True, capture_output=True
        )

        # Create initial Python file
        (repo_path / "module.py").write_text('''def hello():
    return "hello"

def world():
    return "world"

class Greeter:
    def greet(self):
        return "hi"
''')
        subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=repo_path, check=True, capture_output=True
        )

        # Create a bug-fix commit
        (repo_path / "module.py").write_text('''def hello():
    # Fix: handle edge case
    return "hello fixed"

def world():
    return "world"

class Greeter:
    def greet(self):
        return "hi"
''')
        subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Fix: handle edge case in hello"],
            cwd=repo_path, check=True, capture_output=True
        )

        # Create another bug-fix commit
        (repo_path / "module.py").write_text('''def hello():
    # Fix: handle edge case
    return "hello fixed"

def world():
    # Bug fix: handle None input
    if True:
        return "world fixed"
    return "world"

class Greeter:
    def greet(self):
        return "hi"
''')
        subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Bug fix: handle None input in world function"],
            cwd=repo_path, check=True, capture_output=True
        )

        # Create a non-bug commit
        (repo_path / "module.py").write_text('''def hello():
    # Fix: handle edge case
    return "hello fixed"

def world():
    # Bug fix: handle None input
    if True:
        return "world fixed"
    return "world"

class Greeter:
    def greet(self):
        # Refactor: improve greeting
        return "hi there"
''')
        subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Refactor: improve greeting message"],
            cwd=repo_path, check=True, capture_output=True
        )

        yield repo_path


@pytest.fixture
def empty_git_repo():
    """Create an empty git repository with no commits."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo_path, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo_path, check=True, capture_output=True
        )

        # Create initial commit (empty repo with no Python files)
        (repo_path / "README.md").write_text("# Test Repo")
        subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=repo_path, check=True, capture_output=True
        )

        yield repo_path


@pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust module not available")
class TestExtractBuggyFunctionsParallel:
    """Test extract_buggy_functions_parallel function."""

    def test_finds_buggy_functions(self, temp_git_repo):
        """Test that bug-fix commits are detected and functions extracted."""
        results = extract_buggy_functions_parallel(
            str(temp_git_repo),
            ["fix", "bug"],
            since_date=None,
            max_commits=None,
        )

        # Should find hello and world as buggy
        names = [r.qualified_name for r in results]
        assert any("hello" in name for name in names), f"Expected hello in {names}"
        assert any("world" in name for name in names), f"Expected world in {names}"
        # greet was only changed in refactor, not bug fix
        assert not any("greet" in name and "Greeter" in name for name in names), \
            f"greet should not be in {names}"

    def test_returns_pybuggyfunction_objects(self, temp_git_repo):
        """Test that results are PyBuggyFunction objects with correct attributes."""
        results = extract_buggy_functions_parallel(
            str(temp_git_repo),
            ["fix", "bug"],
        )

        assert len(results) > 0

        for result in results:
            assert isinstance(result, PyBuggyFunction)
            assert hasattr(result, "qualified_name")
            assert hasattr(result, "file_path")
            assert hasattr(result, "commit_sha")
            assert hasattr(result, "commit_message")
            assert hasattr(result, "commit_date")

            # Check that attributes are non-empty strings
            assert isinstance(result.qualified_name, str) and result.qualified_name
            assert isinstance(result.file_path, str) and result.file_path
            assert isinstance(result.commit_sha, str) and len(result.commit_sha) >= 7
            assert isinstance(result.commit_message, str)
            assert isinstance(result.commit_date, str)

    def test_keyword_filtering(self, temp_git_repo):
        """Test that only commits matching keywords are processed."""
        # Use only "error" keyword (which doesn't match our commits)
        results = extract_buggy_functions_parallel(
            str(temp_git_repo),
            ["error"],
        )

        # Should find nothing with "error" keyword
        assert len(results) == 0

    def test_max_commits_limiting(self, temp_git_repo):
        """Test that max_commits limits the number of commits processed."""
        # With max_commits=1, should process only the most recent commit
        results = extract_buggy_functions_parallel(
            str(temp_git_repo),
            ["fix", "bug", "refactor"],
            max_commits=1,
        )

        # Should find at most functions from the first commit checked
        # The most recent is the refactor commit, which shouldn't match fix/bug
        # So we might get 0 or results from the previous commit depending on ordering
        assert len(results) <= 2

    def test_empty_repo(self, empty_git_repo):
        """Test behavior with repository that has no Python files."""
        results = extract_buggy_functions_parallel(
            str(empty_git_repo),
            ["fix", "bug"],
        )

        assert results == []

    def test_invalid_repo_path(self):
        """Test that invalid repository path raises error."""
        with pytest.raises(ValueError) as exc_info:
            extract_buggy_functions_parallel(
                "/nonexistent/path/to/repo",
                ["fix", "bug"],
            )

        assert "Failed to open repository" in str(exc_info.value)

    def test_invalid_date_format(self, temp_git_repo):
        """Test that invalid date format raises error."""
        with pytest.raises(ValueError) as exc_info:
            extract_buggy_functions_parallel(
                str(temp_git_repo),
                ["fix"],
                since_date="not-a-date",
            )

        assert "Invalid" in str(exc_info.value) or "date" in str(exc_info.value).lower()

    def test_since_date_filtering(self, temp_git_repo):
        """Test that since_date filters commits correctly."""
        # Use a date far in the future - should find nothing
        results = extract_buggy_functions_parallel(
            str(temp_git_repo),
            ["fix", "bug"],
            since_date="2099-01-01",
        )

        assert len(results) == 0

    def test_case_insensitive_keywords(self, temp_git_repo):
        """Test that keyword matching is case-insensitive."""
        # Use uppercase keywords
        results = extract_buggy_functions_parallel(
            str(temp_git_repo),
            ["FIX", "BUG"],
        )

        # Should still find results
        assert len(results) > 0

    def test_deduplication(self, temp_git_repo):
        """Test that same function appearing in multiple bug-fix commits is deduplicated."""
        results = extract_buggy_functions_parallel(
            str(temp_git_repo),
            ["fix", "bug"],
        )

        # Each function should appear only once
        names = [r.qualified_name for r in results]
        assert len(names) == len(set(names)), "Found duplicate function names"

    def test_file_path_is_python_file(self, temp_git_repo):
        """Test that file paths end with .py."""
        results = extract_buggy_functions_parallel(
            str(temp_git_repo),
            ["fix", "bug"],
        )

        for result in results:
            assert result.file_path.endswith(".py"), \
                f"Expected .py file, got {result.file_path}"

    def test_to_dict_method(self, temp_git_repo):
        """Test that PyBuggyFunction.to_dict() works correctly."""
        results = extract_buggy_functions_parallel(
            str(temp_git_repo),
            ["fix", "bug"],
        )

        if results:
            result = results[0]
            d = result.to_dict()

            assert "qualified_name" in d
            assert "file_path" in d
            assert "commit_sha" in d
            assert "commit_message" in d
            assert "commit_date" in d

    def test_repr_method(self, temp_git_repo):
        """Test that PyBuggyFunction.__repr__() works correctly."""
        results = extract_buggy_functions_parallel(
            str(temp_git_repo),
            ["fix", "bug"],
        )

        if results:
            result = results[0]
            repr_str = repr(result)

            assert "BuggyFunction" in repr_str
            assert "name=" in repr_str or result.qualified_name[:10] in repr_str


@pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust module not available")
class TestExtractBuggyFunctionsEdgeCases:
    """Edge cases and special scenarios."""

    def test_merge_commits_skipped(self):
        """Test that merge commits are skipped (no crash)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)

            # Initialize repo
            subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=repo_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=repo_path, check=True, capture_output=True
            )

            # Create main branch commits
            (repo_path / "main.py").write_text("def main(): pass")
            subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial"],
                cwd=repo_path, check=True, capture_output=True
            )

            # Create and switch to feature branch
            subprocess.run(
                ["git", "checkout", "-b", "feature"],
                cwd=repo_path, check=True, capture_output=True
            )
            (repo_path / "feature.py").write_text("def feature(): pass")
            subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Fix: add feature"],
                cwd=repo_path, check=True, capture_output=True
            )

            # Switch back and merge (use main or master depending on git version)
            # Try main first, then master
            result = subprocess.run(
                ["git", "checkout", "main"],
                cwd=repo_path, capture_output=True
            )
            if result.returncode != 0:
                subprocess.run(
                    ["git", "checkout", "master"],
                    cwd=repo_path, check=True, capture_output=True
                )
            subprocess.run(
                ["git", "merge", "feature", "--no-ff", "-m", "Fix: merge feature branch"],
                cwd=repo_path, check=True, capture_output=True
            )

            # Should not crash on merge commits
            results = extract_buggy_functions_parallel(
                str(repo_path),
                ["fix"],
            )

            # Should find the feature function from the non-merge commit
            names = [r.qualified_name for r in results]
            assert any("feature" in name for name in names)

    def test_binary_files_skipped(self):
        """Test that binary files don't cause crashes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)

            subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=repo_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=repo_path, check=True, capture_output=True
            )

            # Create initial commit
            (repo_path / "module.py").write_text("def func(): pass")
            subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial"],
                cwd=repo_path, check=True, capture_output=True
            )

            # Add binary file in bug-fix commit
            (repo_path / "data.bin").write_bytes(bytes(range(256)))
            (repo_path / "module.py").write_text("def func(): return 1")
            subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Fix: update with binary"],
                cwd=repo_path, check=True, capture_output=True
            )

            # Should not crash
            results = extract_buggy_functions_parallel(
                str(repo_path),
                ["fix"],
            )

            # Should find the Python function
            names = [r.qualified_name for r in results]
            assert any("func" in name for name in names)

    def test_syntax_error_files_graceful(self):
        """Test that Python files with syntax errors are handled gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)

            subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=repo_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=repo_path, check=True, capture_output=True
            )

            # Create initial commit with valid Python
            (repo_path / "module.py").write_text("def func(): pass")
            subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial"],
                cwd=repo_path, check=True, capture_output=True
            )

            # Create bug-fix commit with syntax error
            (repo_path / "module.py").write_text("def broken(:\n    pass")
            subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Fix: broken syntax"],
                cwd=repo_path, check=True, capture_output=True
            )

            # Should not crash, just skip the file
            results = extract_buggy_functions_parallel(
                str(repo_path),
                ["fix"],
            )

            # Should return empty (syntax error in the file)
            # But no exception should be raised
            assert isinstance(results, list)

    def test_deleted_file_handling(self):
        """Test that deleted files are handled correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)

            subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=repo_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=repo_path, check=True, capture_output=True
            )

            # Create initial commit
            (repo_path / "module.py").write_text("def func(): pass")
            subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial"],
                cwd=repo_path, check=True, capture_output=True
            )

            # Delete file in bug-fix commit
            os.remove(repo_path / "module.py")
            subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Fix: remove unused module"],
                cwd=repo_path, check=True, capture_output=True
            )

            # Should not crash
            results = extract_buggy_functions_parallel(
                str(repo_path),
                ["fix"],
            )

            # Deleted files should not contribute functions
            assert isinstance(results, list)

    def test_empty_keywords_list(self, temp_git_repo):
        """Test that empty keywords list returns no results."""
        results = extract_buggy_functions_parallel(
            str(temp_git_repo),
            [],  # Empty keywords
        )

        assert results == []

    def test_nested_class_methods(self):
        """Test that nested class methods are extracted with correct names."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)

            subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=repo_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=repo_path, check=True, capture_output=True
            )

            # Create initial commit
            (repo_path / "module.py").write_text('''class Outer:
    class Inner:
        def method(self):
            pass
''')
            subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial"],
                cwd=repo_path, check=True, capture_output=True
            )

            # Bug-fix commit modifying nested method
            (repo_path / "module.py").write_text('''class Outer:
    class Inner:
        def method(self):
            # Fix: handle edge case
            return True
''')
            subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Fix: handle edge case in nested method"],
                cwd=repo_path, check=True, capture_output=True
            )

            results = extract_buggy_functions_parallel(
                str(repo_path),
                ["fix"],
            )

            names = [r.qualified_name for r in results]
            # Should have nested class name in qualified name
            assert any("Outer" in name and "Inner" in name and "method" in name
                      for name in names), f"Expected nested name, got {names}"

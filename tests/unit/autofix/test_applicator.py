"""Unit tests for FixApplicator."""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import tempfile
import shutil

from repotoire.autofix.applicator import FixApplicator
from repotoire.autofix.models import (
    FixProposal,
    FixStatus,
    FixConfidence,
    FixType,
    CodeChange,
    Evidence,
)
from repotoire.models import Finding, Severity


@pytest.fixture
def temp_repo():
    """Create a temporary directory with git repo."""
    import git

    temp_dir = tempfile.mkdtemp()
    repo_path = Path(temp_dir)

    # Initialize git repo
    repo = git.Repo.init(repo_path)

    # Create test file
    test_file = repo_path / "test.py"
    test_file.write_text("def foo():\n    pass\n")

    # Initial commit
    repo.index.add(["test.py"])
    repo.index.commit("Initial commit")

    yield repo_path, repo

    # Cleanup
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def temp_dir_no_git():
    """Create a temporary directory without git."""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def sample_finding():
    """Create a sample finding."""
    return Finding(
        id="test-1",
        title="Rename function",
        description="Rename foo to bar",
        severity=Severity.LOW,
        affected_files=["test.py"],
        affected_nodes=["test.foo"],
        line_start=1,
        detector="manual",
    )


@pytest.fixture
def sample_fix(sample_finding):
    """Create a sample fix proposal."""
    return FixProposal(
        id="fix-123",
        finding=sample_finding,
        fix_type=FixType.REFACTOR,
        confidence=FixConfidence.HIGH,
        changes=[
            CodeChange(
                file_path=Path("test.py"),
                original_code="def foo():",
                fixed_code="def bar():",
                start_line=1,
                end_line=1,
                description="Rename foo to bar",
            )
        ],
        title="Rename function",
        description="Rename foo to bar",
        rationale="Better naming",
        evidence=Evidence(),
        syntax_valid=True,
        status=FixStatus.APPROVED,
    )


class TestFixApplicator:
    """Unit tests for FixApplicator class."""

    def test_init_with_git_repo(self, temp_repo):
        """Test initialization with valid git repository."""
        repo_path, _ = temp_repo
        applicator = FixApplicator(repo_path, create_branch=True)

        assert applicator.repository_path == repo_path
        assert applicator.create_branch is True
        assert applicator.repo is not None

    def test_init_without_git_repo(self, temp_dir_no_git):
        """Test initialization without git repository."""
        applicator = FixApplicator(temp_dir_no_git, create_branch=False)

        assert applicator.repository_path == temp_dir_no_git
        assert applicator.create_branch is False
        assert applicator.repo is None

    def test_apply_change_success(self, temp_repo, sample_fix):
        """Test applying a code change successfully."""
        repo_path, _ = temp_repo
        applicator = FixApplicator(repo_path, create_branch=False)

        change = sample_fix.changes[0]
        success, error = applicator._apply_change(change)

        assert success is True
        assert error is None

        # Verify file was modified
        test_file = repo_path / "test.py"
        content = test_file.read_text()
        assert "def bar():" in content
        assert "def foo():" not in content

    def test_apply_change_file_not_found(self, temp_repo, sample_fix):
        """Test applying change to non-existent file."""
        repo_path, _ = temp_repo
        applicator = FixApplicator(repo_path, create_branch=False)

        # Change to non-existent file
        change = CodeChange(
            file_path=Path("nonexistent.py"),
            original_code="foo",
            fixed_code="bar",
            start_line=1,
            end_line=1,
            description="Test",
        )

        success, error = applicator._apply_change(change)

        assert success is False
        assert "not found" in error.lower()

    def test_apply_change_original_code_not_found(self, temp_repo, sample_fix):
        """Test applying change when original code doesn't match."""
        repo_path, _ = temp_repo
        applicator = FixApplicator(repo_path, create_branch=False)

        # Change with wrong original code
        change = CodeChange(
            file_path=Path("test.py"),
            original_code="def baz():",  # This doesn't exist
            fixed_code="def qux():",
            start_line=1,
            end_line=1,
            description="Test",
        )

        success, error = applicator._apply_change(change)

        assert success is False
        assert "not found" in error.lower()

    def test_apply_fix_success(self, temp_repo, sample_fix):
        """Test applying a complete fix."""
        repo_path, _ = temp_repo
        applicator = FixApplicator(repo_path, create_branch=False)

        success, error = applicator.apply_fix(sample_fix, commit=False)

        assert success is True
        assert error is None
        assert sample_fix.status == FixStatus.APPLIED
        assert sample_fix.applied_at is not None

    def test_apply_fix_with_commit(self, temp_repo, sample_fix):
        """Test applying fix with git commit."""
        repo_path, repo = temp_repo
        applicator = FixApplicator(repo_path, create_branch=False)

        initial_commit_count = len(list(repo.iter_commits()))

        success, error = applicator.apply_fix(sample_fix, commit=True)

        assert success is True
        # Should have one more commit
        assert len(list(repo.iter_commits())) == initial_commit_count + 1

    def test_apply_fix_with_branch(self, temp_repo, sample_fix):
        """Test applying fix with branch creation."""
        repo_path, repo = temp_repo
        applicator = FixApplicator(repo_path, create_branch=True)

        # Set branch name
        sample_fix.branch_name = "test-branch"

        success, error = applicator.apply_fix(sample_fix, commit=True)

        assert success is True
        assert "test-branch" in repo.heads
        assert repo.active_branch.name == "test-branch"

    def test_apply_fix_failure(self, temp_repo, sample_fix):
        """Test fix application failure."""
        repo_path, _ = temp_repo
        applicator = FixApplicator(repo_path, create_branch=False)

        # Modify the fix to have invalid change
        sample_fix.changes[0].file_path = Path("nonexistent.py")

        success, error = applicator.apply_fix(sample_fix, commit=False)

        assert success is False
        assert error is not None
        assert sample_fix.status == FixStatus.FAILED

    def test_apply_batch_success(self, temp_repo, sample_fix, sample_finding):
        """Test applying multiple fixes."""
        repo_path, _ = temp_repo

        # Create second file and fix
        test_file2 = repo_path / "test2.py"
        test_file2.write_text("def baz():\n    pass\n")

        fix2 = FixProposal(
            id="fix-456",
            finding=sample_finding,
            fix_type=FixType.REFACTOR,
            confidence=FixConfidence.HIGH,
            changes=[
                CodeChange(
                    file_path=Path("test2.py"),
                    original_code="def baz():",
                    fixed_code="def qux():",
                    start_line=1,
                    end_line=1,
                    description="Rename baz to qux",
                )
            ],
            title="Rename baz",
            description="Rename baz to qux",
            rationale="Better naming",
            evidence=Evidence(),
            syntax_valid=True,
            status=FixStatus.APPROVED,
        )

        applicator = FixApplicator(repo_path, create_branch=False)
        successful, failed = applicator.apply_batch([sample_fix, fix2], commit_each=False)

        assert len(successful) == 2
        assert len(failed) == 0

    def test_apply_batch_with_failures(self, temp_repo, sample_fix, sample_finding):
        """Test batch application with some failures."""
        repo_path, _ = temp_repo

        # Create a fix that will fail
        bad_fix = FixProposal(
            id="fix-bad",
            finding=sample_finding,
            fix_type=FixType.REFACTOR,
            confidence=FixConfidence.HIGH,
            changes=[
                CodeChange(
                    file_path=Path("nonexistent.py"),
                    original_code="foo",
                    fixed_code="bar",
                    start_line=1,
                    end_line=1,
                    description="Will fail",
                )
            ],
            title="Bad fix",
            description="This will fail",
            rationale="Testing",
            evidence=Evidence(),
            syntax_valid=True,
            status=FixStatus.APPROVED,
        )

        applicator = FixApplicator(repo_path, create_branch=False)
        successful, failed = applicator.apply_batch([sample_fix, bad_fix], commit_each=False)

        assert len(successful) == 1
        assert len(failed) == 1
        assert failed[0][0].id == "fix-bad"

    def test_apply_batch_skips_unapproved(self, temp_repo, sample_fix, sample_finding):
        """Test batch application skips unapproved fixes."""
        repo_path, _ = temp_repo

        # Create unapproved fix
        unapproved_fix = FixProposal(
            id="fix-pending",
            finding=sample_finding,
            fix_type=FixType.REFACTOR,
            confidence=FixConfidence.HIGH,
            changes=[],
            title="Unapproved",
            description="Not approved",
            rationale="Testing",
            evidence=Evidence(),
            syntax_valid=True,
            status=FixStatus.PENDING,  # Not approved
        )

        applicator = FixApplicator(repo_path, create_branch=False)
        successful, failed = applicator.apply_batch([sample_fix, unapproved_fix], commit_each=False)

        # Only approved fix should be applied
        assert len(successful) == 1
        assert len(failed) == 0

    def test_generate_commit_message(self, temp_repo, sample_fix):
        """Test commit message generation."""
        repo_path, _ = temp_repo
        applicator = FixApplicator(repo_path, create_branch=False)

        message = applicator._generate_commit_message(sample_fix)

        assert "Rename function" in message
        assert sample_fix.fix_type.value in message
        assert "Repotoire Auto-Fix" in message

    def test_generate_commit_message_uses_provided_message(self, temp_repo, sample_fix):
        """Test that provided commit message is used."""
        repo_path, _ = temp_repo
        applicator = FixApplicator(repo_path, create_branch=False)

        sample_fix.commit_message = "Custom commit message"
        message = applicator._generate_commit_message(sample_fix)

        assert message == "Custom commit message"

    def test_generate_batch_commit_message(self, temp_repo, sample_fix, sample_finding):
        """Test batch commit message generation."""
        repo_path, _ = temp_repo
        applicator = FixApplicator(repo_path, create_branch=False)

        fix2 = FixProposal(
            id="fix-456",
            finding=sample_finding,
            fix_type=FixType.DOCUMENTATION,
            confidence=FixConfidence.MEDIUM,
            changes=[],
            title="Add docstring",
            description="Add docs",
            rationale="Documentation",
            evidence=Evidence(),
            syntax_valid=True,
        )

        message = applicator._generate_batch_commit_message([sample_fix, fix2])

        assert "2 auto-fixes" in message
        assert "Rename function" in message
        assert "Add docstring" in message

    def test_rollback_success(self, temp_repo, sample_fix):
        """Test successful rollback."""
        repo_path, _ = temp_repo
        applicator = FixApplicator(repo_path, create_branch=False)

        # Apply fix
        applicator.apply_fix(sample_fix, commit=False)

        # Verify changed
        test_file = repo_path / "test.py"
        assert "def bar():" in test_file.read_text()

        # Rollback
        success = applicator.rollback()

        assert success is True
        # Verify reverted
        assert "def foo():" in test_file.read_text()

    def test_rollback_without_repo(self, temp_dir_no_git):
        """Test rollback without git repository."""
        applicator = FixApplicator(temp_dir_no_git, create_branch=False)

        success = applicator.rollback()

        assert success is False

    @patch("subprocess.run")
    def test_run_tests_success(self, mock_run, temp_repo):
        """Test running tests successfully."""
        repo_path, _ = temp_repo
        applicator = FixApplicator(repo_path, create_branch=False, use_sandbox=False)

        # Mock successful test run
        mock_run.return_value = Mock(returncode=0, stdout="All tests passed", stderr="")

        result = applicator.run_tests()

        assert result.success is True
        assert "passed" in (result.stdout + result.stderr).lower()
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_run_tests_failure(self, mock_run, temp_repo):
        """Test running tests with failures."""
        repo_path, _ = temp_repo
        applicator = FixApplicator(repo_path, create_branch=False, use_sandbox=False)

        # Mock failed test run
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="Tests failed")

        result = applicator.run_tests()

        assert result.success is False
        assert "failed" in (result.stdout + result.stderr).lower()

    @patch("subprocess.run")
    def test_run_tests_custom_command(self, mock_run, temp_repo):
        """Test running tests with custom command."""
        repo_path, _ = temp_repo
        applicator = FixApplicator(repo_path, create_branch=False, use_sandbox=False)

        mock_run.return_value = Mock(returncode=0, stdout="OK", stderr="")

        result = applicator.run_tests(test_command="python -m pytest")

        assert result.success is True
        # Check that the command was split correctly
        call_args = mock_run.call_args
        assert call_args[0][0] == ["python", "-m", "pytest"]

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_run_tests_command_not_found(self, mock_run, temp_repo):
        """Test running tests when command doesn't exist."""
        repo_path, _ = temp_repo
        applicator = FixApplicator(repo_path, create_branch=False, use_sandbox=False)

        result = applicator.run_tests(test_command="nonexistent-command")

        assert result.success is False
        assert "not found" in (result.stdout + result.stderr).lower()

    def test_create_branch_new(self, temp_repo, sample_fix):
        """Test creating a new branch."""
        repo_path, repo = temp_repo
        applicator = FixApplicator(repo_path, create_branch=True)

        sample_fix.branch_name = "feature-branch"
        applicator._create_branch(sample_fix)

        assert "feature-branch" in repo.heads
        assert repo.active_branch.name == "feature-branch"

    def test_create_branch_existing(self, temp_repo, sample_fix):
        """Test checking out existing branch."""
        repo_path, repo = temp_repo

        # Create branch manually
        repo.create_head("existing-branch")

        applicator = FixApplicator(repo_path, create_branch=True)
        sample_fix.branch_name = "existing-branch"

        applicator._create_branch(sample_fix)

        assert repo.active_branch.name == "existing-branch"

    def test_create_commit(self, temp_repo, sample_fix):
        """Test creating a git commit."""
        repo_path, repo = temp_repo
        applicator = FixApplicator(repo_path, create_branch=False)

        # Apply changes first
        for change in sample_fix.changes:
            applicator._apply_change(change)

        initial_count = len(list(repo.iter_commits()))

        applicator._create_commit(sample_fix)

        assert len(list(repo.iter_commits())) == initial_count + 1

        # Check commit message
        latest_commit = list(repo.iter_commits())[0]
        assert "Rename function" in latest_commit.message

    def test_create_batch_commit(self, temp_repo, sample_fix, sample_finding):
        """Test creating a batch commit."""
        repo_path, repo = temp_repo

        # Create second file
        test_file2 = repo_path / "test2.py"
        test_file2.write_text("def baz():\n    pass\n")

        fix2 = FixProposal(
            id="fix-456",
            finding=sample_finding,
            fix_type=FixType.REFACTOR,
            confidence=FixConfidence.HIGH,
            changes=[
                CodeChange(
                    file_path=Path("test2.py"),
                    original_code="def baz():",
                    fixed_code="def qux():",
                    start_line=1,
                    end_line=1,
                    description="Rename baz to qux",
                )
            ],
            title="Rename baz",
            description="Rename baz to qux",
            rationale="Better naming",
            evidence=Evidence(),
            syntax_valid=True,
            status=FixStatus.APPROVED,
        )

        applicator = FixApplicator(repo_path, create_branch=False)

        # Apply changes
        for fix in [sample_fix, fix2]:
            for change in fix.changes:
                applicator._apply_change(change)

        initial_count = len(list(repo.iter_commits()))

        applicator._create_batch_commit([sample_fix, fix2])

        assert len(list(repo.iter_commits())) == initial_count + 1

        # Check commit message
        latest_commit = list(repo.iter_commits())[0]
        assert "2 auto-fixes" in latest_commit.message

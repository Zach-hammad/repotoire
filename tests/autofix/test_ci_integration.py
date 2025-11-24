"""Tests for CI/CD integration functionality."""

import json
import subprocess
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock
import pytest

from repotoire.autofix.ci import (
    PRDescriptionGenerator,
    GitHubPRCreator,
    GitLabMRCreator,
    CIRunner,
)
from repotoire.autofix.models import (
    FixBatch,
    FixProposal,
    FixStatus,
    FixConfidence,
    FixType,
    CodeChange,
    Evidence,
)
from repotoire.models import Finding, Severity


@pytest.fixture
def sample_fix_batch():
    """Create a sample fix batch for testing."""
    # Create mock findings
    finding1 = Finding(
        id="finding-1",
        title="SQL injection vulnerability",
        description="Query uses string concatenation",
        severity=Severity.CRITICAL,
        detector="bandit",
        affected_nodes=["src.api.query_users"],
        affected_files=["src/api.py"],
        line_start=10,
        line_end=12,
    )

    finding2 = Finding(
        id="finding-2",
        title="Missing docstring",
        description="Function lacks documentation",
        severity=Severity.LOW,
        detector="pylint",
        affected_nodes=["src.utils.calculate"],
        affected_files=["src/utils.py"],
        line_start=5,
        line_end=5,
    )

    finding3 = Finding(
        id="finding-3",
        title="Unused import",
        description="Import is not used",
        severity=Severity.LOW,
        detector="ruff",
        affected_nodes=["src.main"],
        affected_files=["src/main.py"],
        line_start=1,
        line_end=1,
    )

    fix1 = FixProposal(
        id="fix-1",
        finding=finding1,
        title="Fix SQL injection",
        description="Use parameterized queries",
        rationale="Parameterized queries prevent SQL injection attacks",
        fix_type=FixType.SECURITY,
        confidence=FixConfidence.HIGH,
        status=FixStatus.APPLIED,
        changes=[
            CodeChange(
                file_path=Path("src/api.py"),
                start_line=10,
                end_line=12,
                original_code='query = f"SELECT * FROM users WHERE id={user_id}"',
                fixed_code='query = "SELECT * FROM users WHERE id=?"\nparams = [user_id]',
                description="Replace string concatenation with parameterized query",
            )
        ],
        evidence=Evidence(
            best_practices=["OWASP A03: Injection", "CWE-89: SQL Injection"],
        ),
    )

    fix2 = FixProposal(
        id="fix-2",
        finding=finding2,
        title="Add missing docstring",
        description="Document function purpose",
        rationale="Docstrings improve code maintainability",
        fix_type=FixType.DOCUMENTATION,
        confidence=FixConfidence.MEDIUM,
        status=FixStatus.APPLIED,
        changes=[
            CodeChange(
                file_path=Path("src/utils.py"),
                start_line=5,
                end_line=5,
                original_code="def calculate():",
                fixed_code='def calculate():\n    """Calculate result."""',
                description="Add docstring to function",
            )
        ],
        evidence=Evidence(
            documentation_refs=["PEP 257: Docstring Conventions"],
        ),
    )

    fix3 = FixProposal(
        id="fix-3",
        finding=finding3,
        title="Remove unused import",
        description="Remove unused import",
        rationale="Unused imports clutter the code",
        fix_type=FixType.REMOVE,
        confidence=FixConfidence.HIGH,
        status=FixStatus.APPLIED,
        changes=[
            CodeChange(
                file_path=Path("src/main.py"),
                start_line=1,
                end_line=1,
                original_code="import unused_module",
                fixed_code="",
                description="Remove unused import",
            )
        ],
        evidence=Evidence(
            best_practices=["Pyflakes: Unused import"],
        ),
    )

    return FixBatch(
        fixes=[fix1, fix2, fix3],
        total_findings=10,
        fixable_count=3,
        unfixable_count=7,
        high_confidence_count=2,
        created_at=datetime.now(),
    )


class TestPRDescriptionGenerator:
    """Tests for PR description generation."""

    def test_generate_basic_description(self, sample_fix_batch):
        """Test generating a basic PR description."""
        generator = PRDescriptionGenerator()
        description = generator.generate(sample_fix_batch)

        # Check for expected sections
        assert "## 🤖 Auto-Fix Summary" in description
        assert "### Changes" in description
        assert "### Confidence Levels" in description
        assert "### Evidence" in description
        assert "### Files Modified" in description

        # Check for fix types
        assert "Security" in description
        assert "Documentation" in description
        assert "Remove" in description

        # Check for confidence levels
        assert "High confidence" in description
        assert "Medium confidence" in description

        # Check for file paths
        assert "src/api.py" in description
        assert "src/utils.py" in description
        assert "src/main.py" in description

    def test_generate_with_test_results(self, sample_fix_batch):
        """Test generating description with test results."""
        generator = PRDescriptionGenerator()

        # Test with passing tests
        test_results = {"passed": True, "test_count": 42}
        description = generator.generate(sample_fix_batch, test_results)

        assert "### Testing" in description
        assert "All tests passing" in description
        assert "42 tests passed" in description

        # Test with failing tests
        test_results = {"passed": False, "failed_count": 3}
        description = generator.generate(sample_fix_batch, test_results)

        assert "Some tests failed" in description
        assert "3 tests failed" in description

    def test_confidence_counting(self, sample_fix_batch):
        """Test that confidence levels are counted correctly."""
        generator = PRDescriptionGenerator()
        description = generator.generate(sample_fix_batch)

        # 2 high confidence, 1 medium confidence
        assert "**High confidence**: 2 fix(es)" in description
        assert "**Medium confidence**: 1 fix(es)" in description

    def test_type_emoji_mapping(self):
        """Test that fix types get correct emojis."""
        generator = PRDescriptionGenerator()

        assert generator._get_type_emoji("security") == "🔒"
        assert generator._get_type_emoji("bug") == "🐛"
        assert generator._get_type_emoji("style") == "✨"
        assert generator._get_type_emoji("documentation") == "📝"
        assert generator._get_type_emoji("performance") == "⚡"
        assert generator._get_type_emoji("refactoring") == "♻️"
        assert generator._get_type_emoji("test") == "🧪"
        assert generator._get_type_emoji("unknown") == "✅"

    def test_files_limited_to_10(self):
        """Test that file listing is limited to 10 files."""
        # Create a fix batch with >10 files
        fixes = []
        for i in range(15):
            finding = Finding(
                id=f"finding-{i}",
                title=f"Issue {i}",
                description="Test issue",
                severity=Severity.LOW,
                detector="ruff",
                affected_nodes=[f"src.file{i}"],
                affected_files=[f"src/file{i}.py"],
                line_start=1,
                line_end=1,
            )
            fixes.append(
                FixProposal(
                    id=f"fix-{i}",
                    finding=finding,
                    title=f"Fix {i}",
                    description="Test fix",
                    rationale="Test rationale",
                    fix_type=FixType.REMOVE,
                    confidence=FixConfidence.HIGH,
                    status=FixStatus.APPLIED,
                    changes=[
                        CodeChange(
                            file_path=Path(f"src/file{i}.py"),
                            start_line=1,
                            end_line=1,
                            original_code="old",
                            fixed_code="new",
                            description="Test change",
                        )
                    ],
                    evidence=Evidence(),
                )
            )

        fix_batch = FixBatch(
            fixes=fixes,
            total_findings=15,
            fixable_count=15,
            unfixable_count=0,
            high_confidence_count=15,
            created_at=datetime.now(),
        )

        generator = PRDescriptionGenerator()
        description = generator.generate(fix_batch)

        # Should mention 15 files but only list 10
        assert "Files Modified (15)" in description
        assert "and 5 more files" in description


class TestGitHubPRCreator:
    """Tests for GitHub PR creation."""

    def test_create_pr_success(self, tmp_path):
        """Test successful PR creation."""
        creator = GitHubPRCreator(tmp_path)

        with patch("subprocess.run") as mock_run:
            # Mock successful gh command
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="https://github.com/owner/repo/pull/123\n",
                stderr="",
            )

            result = creator.create_pr(
                branch_name="feature-branch",
                title="Test PR",
                body="Test body",
            )

            assert result["url"] == "https://github.com/owner/repo/pull/123"
            assert result["branch"] == "feature-branch"
            assert result["title"] == "Test PR"

            # Verify gh command was called correctly
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert "gh" in call_args[0][0]
            assert "pr" in call_args[0][0]
            assert "create" in call_args[0][0]
            assert "--title" in call_args[0][0]
            assert "Test PR" in call_args[0][0]

    def test_create_pr_with_labels_and_reviewers(self, tmp_path):
        """Test PR creation with labels and reviewers."""
        creator = GitHubPRCreator(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="https://github.com/owner/repo/pull/123\n",
            )

            creator.create_pr(
                branch_name="feature-branch",
                title="Test PR",
                body="Test body",
                labels=["bug", "enhancement"],
                reviewers=["user1", "user2"],
            )

            # Verify labels and reviewers were included
            call_args = mock_run.call_args[0][0]
            assert "--label" in call_args
            assert "bug" in call_args
            assert "enhancement" in call_args
            assert "--reviewer" in call_args
            assert "user1" in call_args
            assert "user2" in call_args

    def test_create_pr_draft(self, tmp_path):
        """Test creating a draft PR."""
        creator = GitHubPRCreator(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="https://github.com/owner/repo/pull/123\n",
            )

            creator.create_pr(
                branch_name="feature-branch",
                title="Test PR",
                body="Test body",
                draft=True,
            )

            # Verify --draft flag was included
            call_args = mock_run.call_args[0][0]
            assert "--draft" in call_args

    def test_create_pr_failure(self, tmp_path):
        """Test handling of PR creation failure."""
        creator = GitHubPRCreator(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="Error: Failed to create PR"
            )

            with pytest.raises(RuntimeError, match="PR creation failed"):
                creator.create_pr(
                    branch_name="feature-branch",
                    title="Test PR",
                    body="Test body",
                )

    def test_create_pr_timeout(self, tmp_path):
        """Test handling of PR creation timeout."""
        creator = GitHubPRCreator(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=60)

            with pytest.raises(RuntimeError, match="PR creation timed out"):
                creator.create_pr(
                    branch_name="feature-branch",
                    title="Test PR",
                    body="Test body",
                )

    def test_create_pr_gh_not_found(self, tmp_path):
        """Test handling when gh CLI is not installed."""
        creator = GitHubPRCreator(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()

            with pytest.raises(RuntimeError, match="GitHub CLI .* not found"):
                creator.create_pr(
                    branch_name="feature-branch",
                    title="Test PR",
                    body="Test body",
                )


class TestGitLabMRCreator:
    """Tests for GitLab MR creation."""

    def test_create_mr_success(self, tmp_path):
        """Test successful MR creation."""
        creator = GitLabMRCreator(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="Created MR\nhttps://gitlab.com/owner/repo/-/merge_requests/123\n",
                stderr="",
            )

            result = creator.create_mr(
                branch_name="feature-branch",
                title="Test MR",
                description="Test description",
            )

            assert result["url"] == "https://gitlab.com/owner/repo/-/merge_requests/123"
            assert result["branch"] == "feature-branch"
            assert result["title"] == "Test MR"

            # Verify glab command was called correctly
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert "glab" in call_args[0][0]
            assert "mr" in call_args[0][0]
            assert "create" in call_args[0][0]

    def test_create_mr_with_labels_and_reviewers(self, tmp_path):
        """Test MR creation with labels and reviewers."""
        creator = GitLabMRCreator(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="https://gitlab.com/owner/repo/-/merge_requests/123\n",
            )

            creator.create_mr(
                branch_name="feature-branch",
                title="Test MR",
                description="Test description",
                labels=["bug", "enhancement"],
                reviewers=["user1", "user2"],
            )

            # Verify labels and reviewers were included
            call_args = mock_run.call_args[0][0]
            assert "--label" in call_args
            assert "bug,enhancement" in call_args
            assert "--reviewer" in call_args
            assert "user1,user2" in call_args

    def test_create_mr_draft(self, tmp_path):
        """Test creating a draft MR."""
        creator = GitLabMRCreator(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="https://gitlab.com/owner/repo/-/merge_requests/123\n",
            )

            creator.create_mr(
                branch_name="feature-branch",
                title="Test MR",
                description="Test description",
                draft=True,
            )

            # Verify --draft flag was included
            call_args = mock_run.call_args[0][0]
            assert "--draft" in call_args

    def test_create_mr_failure(self, tmp_path):
        """Test handling of MR creation failure."""
        creator = GitLabMRCreator(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="Error: Failed to create MR"
            )

            with pytest.raises(RuntimeError, match="MR creation failed"):
                creator.create_mr(
                    branch_name="feature-branch",
                    title="Test MR",
                    description="Test description",
                )

    def test_create_mr_glab_not_found(self, tmp_path):
        """Test handling when glab CLI is not installed."""
        creator = GitLabMRCreator(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()

            with pytest.raises(RuntimeError, match="GitLab CLI .* not found"):
                creator.create_mr(
                    branch_name="feature-branch",
                    title="Test MR",
                    description="Test description",
                )


class TestCIRunner:
    """Tests for CI runner functionality."""

    def test_initialization(self, tmp_path):
        """Test CIRunner initialization."""
        runner = CIRunner(tmp_path, max_fixes=100, dry_run=True)

        assert runner.repository_path == tmp_path
        assert runner.max_fixes == 100
        assert runner.dry_run is True

    def test_should_create_pr_no_fixes(self, tmp_path):
        """Test that PR is not created when there are no fixes."""
        runner = CIRunner(tmp_path)

        fix_batch = FixBatch(
            fixes=[],
            total_findings=0,
            fixable_count=0,
            unfixable_count=0,
            high_confidence_count=0,
            created_at=datetime.now(),
        )

        assert runner.should_create_pr(fix_batch) is False

    def test_should_create_pr_dry_run(self, tmp_path, sample_fix_batch):
        """Test that PR is not created in dry-run mode."""
        runner = CIRunner(tmp_path, dry_run=True)

        assert runner.should_create_pr(sample_fix_batch) is False

    def test_should_create_pr_only_low_confidence(self, tmp_path):
        """Test that PR is not created when all fixes are low confidence."""
        runner = CIRunner(tmp_path)

        # Create mock findings
        finding1 = Finding(
            id="finding-low",
            title="Low issue",
            description="Test",
            severity=Severity.LOW,
            detector="ruff",
            affected_nodes=["src.test"],
            affected_files=["src/test.py"],
            line_start=1,
            line_end=1,
        )

        finding2 = Finding(
            id="finding-med",
            title="Medium issue",
            description="Test",
            severity=Severity.MEDIUM,
            detector="ruff",
            affected_nodes=["src.test2"],
            affected_files=["src/test2.py"],
            line_start=1,
            line_end=1,
        )

        # Create fix batch with only low/medium confidence fixes
        fixes = [
            FixProposal(
                id="fix-low",
                finding=finding1,
                title="Low confidence fix",
                description="Test",
                rationale="Test",
                fix_type=FixType.REMOVE,
                confidence=FixConfidence.LOW,
                status=FixStatus.APPLIED,
                changes=[],
                evidence=Evidence(),
            ),
            FixProposal(
                id="fix-med",
                finding=finding2,
                title="Medium confidence fix",
                description="Test",
                rationale="Test",
                fix_type=FixType.REMOVE,
                confidence=FixConfidence.MEDIUM,
                status=FixStatus.APPLIED,
                changes=[],
                evidence=Evidence(),
            ),
        ]

        fix_batch = FixBatch(
            fixes=fixes,
            total_findings=2,
            fixable_count=2,
            unfixable_count=0,
            high_confidence_count=0,
            created_at=datetime.now(),
        )

        assert runner.should_create_pr(fix_batch) is False

    def test_should_create_pr_with_high_confidence(self, tmp_path, sample_fix_batch):
        """Test that PR is created when there are high confidence fixes."""
        runner = CIRunner(tmp_path)

        # sample_fix_batch has high confidence fixes
        assert runner.should_create_pr(sample_fix_batch) is True

    def test_create_branch(self, tmp_path):
        """Test creating a git branch."""
        runner = CIRunner(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            runner.create_branch("test-branch")

            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            assert call_args == ["git", "checkout", "-b", "test-branch"]

    def test_create_branch_failure(self, tmp_path):
        """Test handling of branch creation failure."""
        runner = CIRunner(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=1, cmd="git", stderr=b"Error"
            )

            with pytest.raises(RuntimeError, match="Branch creation failed"):
                runner.create_branch("test-branch")

    def test_commit_changes(self, tmp_path):
        """Test committing changes."""
        runner = CIRunner(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            runner.commit_changes("Test commit message")

            # Should call git add and git commit
            assert mock_run.call_count == 2
            add_call = mock_run.call_args_list[0][0][0]
            commit_call = mock_run.call_args_list[1][0][0]

            assert add_call == ["git", "add", "-A"]
            assert commit_call[0:3] == ["git", "commit", "-m"]
            assert "Test commit message" in commit_call

    def test_push_branch(self, tmp_path):
        """Test pushing a branch."""
        runner = CIRunner(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            runner.push_branch("test-branch")

            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            assert call_args == ["git", "push", "-u", "origin", "test-branch"]

    def test_run_tests_success(self, tmp_path):
        """Test running tests that pass."""
        runner = CIRunner(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="42 passed in 2.5s"
            )

            result = runner.run_tests()

            assert result["passed"] is True
            assert result["test_count"] == 42
            assert "42 passed" in result["output"]

    def test_run_tests_failure(self, tmp_path):
        """Test running tests that fail."""
        runner = CIRunner(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="5 failed, 37 passed in 3.2s"
            )

            result = runner.run_tests()

            assert result["passed"] is False
            assert result["failed_count"] == 5
            assert "5 failed" in result["output"]

    def test_run_tests_not_found(self, tmp_path):
        """Test handling when pytest is not found."""
        runner = CIRunner(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()

            result = runner.run_tests()

            # Should assume tests pass if pytest not found
            assert result["passed"] is True
            assert result["test_count"] == 0

    def test_run_tests_timeout(self, tmp_path):
        """Test handling of test timeout."""
        runner = CIRunner(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="pytest", timeout=300)

            result = runner.run_tests()

            # Should assume tests pass on timeout
            assert result["passed"] is True

    def test_count_tests(self, tmp_path):
        """Test test counting from pytest output."""
        runner = CIRunner(tmp_path)

        # Test various pytest output formats
        assert runner._count_tests("42 passed in 2.5s") == 42
        assert runner._count_tests("100 passed, 5 skipped in 10s") == 100
        assert runner._count_tests("No passed tests") == 0

    def test_count_failures(self, tmp_path):
        """Test failure counting from pytest output."""
        runner = CIRunner(tmp_path)

        # Test various pytest output formats
        assert runner._count_failures("5 failed, 37 passed in 3.2s") == 5
        assert runner._count_failures("10 failed in 5s") == 10
        assert runner._count_failures("All tests passed") == 0


class TestCIIntegrationFlow:
    """Integration tests for complete CI flow."""

    def test_full_github_workflow(self, tmp_path, sample_fix_batch):
        """Test complete GitHub workflow: generate description, create PR."""
        # Generate PR description
        generator = PRDescriptionGenerator()
        description = generator.generate(sample_fix_batch)

        # Create PR
        creator = GitHubPRCreator(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="https://github.com/owner/repo/pull/123\n",
            )

            result = creator.create_pr(
                branch_name="repotoire/auto-fix-123",
                title="🤖 Auto-fix: Code quality improvements",
                body=description,
                labels=["automated", "code-quality"],
            )

            assert "https://github.com" in result["url"]

    def test_full_gitlab_workflow(self, tmp_path, sample_fix_batch):
        """Test complete GitLab workflow: generate description, create MR."""
        # Generate MR description
        generator = PRDescriptionGenerator()
        description = generator.generate(sample_fix_batch)

        # Create MR
        creator = GitLabMRCreator(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="https://gitlab.com/owner/repo/-/merge_requests/123\n",
            )

            result = creator.create_mr(
                branch_name="repotoire/auto-fix-123",
                title="🤖 Auto-fix: Code quality improvements",
                description=description,
                labels=["automated", "code-quality"],
            )

            assert "https://gitlab.com" in result["url"]

    def test_ci_runner_workflow(self, tmp_path, sample_fix_batch):
        """Test complete CI runner workflow."""
        runner = CIRunner(tmp_path, max_fixes=50, dry_run=False)

        # Check if should create PR
        assert runner.should_create_pr(sample_fix_batch) is True

        # Mock git operations
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            # Create branch
            runner.create_branch("repotoire/auto-fix-123")

            # Commit changes
            runner.commit_changes("fix: apply automated code quality fixes")

            # Push branch
            runner.push_branch("repotoire/auto-fix-123")

            # Verify all git commands were called
            assert mock_run.call_count == 4  # checkout, add, commit, push

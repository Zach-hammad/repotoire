"""Integration tests for auto-fix workflow."""

import pytest
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime
import tempfile
import shutil

from repotoire.autofix import (
    AutoFixEngine,
    InteractiveReviewer,
    FixApplicator,
    FixProposal,
    FixStatus,
    FixConfidence,
    Evidence,
)
from repotoire.autofix.models import FixType, CodeChange
from repotoire.models import Finding, Severity


@pytest.fixture
def temp_repo(tmp_path):
    """Create a temporary git repository for testing."""
    import git

    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()

    # Initialize git repo
    repo = git.Repo.init(repo_path)

    # Create a test file with code smell
    test_file = repo_path / "example.py"
    test_file.write_text("""def process_data(x):
    # Missing type hints and docstring
    if x == None:  # Bad: should use 'is None'
        return []
    return [item * 2 for item in x]
""")

    # Initial commit
    repo.index.add(["example.py"])
    repo.index.commit("Initial commit")

    yield repo_path, repo

    # Cleanup
    shutil.rmtree(repo_path, ignore_errors=True)


@pytest.fixture
def sample_finding():
    """Create a sample finding for testing."""
    return Finding(
        id="test-1",
        title="Use 'is None' instead of '== None'",
        description="Comparison to None should use 'is' instead of '=='",
        severity=Severity.MEDIUM,
        affected_files=["example.py"],
        affected_nodes=["example.process_data"],
        line_start=4,
        detector="pylint",
    )


@pytest.fixture
def sample_fix_proposal(sample_finding):
    """Create a sample fix proposal."""
    return FixProposal(
        id="fix-123",
        finding=sample_finding,
        fix_type=FixType.REFACTOR,
        confidence=FixConfidence.HIGH,
        changes=[
            CodeChange(
                file_path=Path("example.py"),
                original_code="    if x == None:  # Bad: should use 'is None'\n        return []",
                fixed_code="    if x is None:  # Fixed: use 'is None'\n        return []",
                start_line=3,
                end_line=4,
                description="Use 'is None' for None comparison",
            )
        ],
        title="Fix None comparison",
        description="Change == None to is None for PEP 8 compliance",
        rationale="PEP 8 recommends using 'is' for None comparisons",
        evidence=Evidence(
            documentation_refs=["PEP 8: Comparisons to singletons should use 'is'"],
            best_practices=["Using 'is None' is more explicit and prevents bugs"],
            similar_patterns=["Found in 10 other files in codebase"],
        ),
        syntax_valid=True,
        status=FixStatus.PENDING,
    )


class TestAutoFixEngineIntegration:
    """Integration tests for AutoFixEngine."""

    @pytest.mark.asyncio
    @patch("repotoire.autofix.engine.LLMClient")
    @patch("repotoire.autofix.engine.CodeEmbedder")
    @patch("repotoire.autofix.engine.GraphRAGRetriever")
    async def test_generate_fix_with_mocked_llm(
        self, mock_retriever_class, mock_embedder_class, mock_llm_class, sample_finding, temp_repo
    ):
        """Test fix generation with mocked LLM response."""
        repo_path, _ = temp_repo

        # Mock Neo4j client
        mock_neo4j = Mock()
        mock_neo4j.close = Mock()

        # Mock LLM client - LLMClient.generate returns string directly
        mock_llm = Mock()
        mock_llm.generate.return_value = """```json
{
    "title": "Fix None comparison",
    "description": "Change == None to is None",
    "rationale": "PEP 8 compliance",
    "evidence": {
        "documentation_refs": ["PEP 8: Use 'is' for None"],
        "best_practices": ["More explicit and safe"],
        "similar_patterns": ["Used in 10+ files"]
    },
    "changes": [{
        "file_path": "example.py",
        "original_code": "if x == None:",
        "fixed_code": "if x is None:",
        "start_line": 4,
        "end_line": 4,
        "description": "Fix None comparison"
    }]
}
```"""
        mock_llm_class.return_value = mock_llm

        # Mock RAG retriever
        mock_retriever = Mock()
        mock_retriever.retrieve.return_value = []
        mock_retriever_class.return_value = mock_retriever

        # Create engine
        engine = AutoFixEngine(mock_neo4j, openai_api_key="test-key")

        # Generate fix
        fix = await engine.generate_fix(sample_finding, repo_path)

        # Verify fix was generated
        assert fix is not None
        assert fix.title == "Fix None comparison"
        assert len(fix.changes) == 1
        assert fix.changes[0].fixed_code == "if x is None:"
        assert fix.evidence.documentation_refs == ["PEP 8: Use 'is' for None"]


class TestFixApplicatorIntegration:
    """Integration tests for FixApplicator."""

    def test_apply_fix_to_file(self, temp_repo, sample_fix_proposal):
        """Test applying a fix to an actual file."""
        repo_path, repo = temp_repo

        # Create applicator
        applicator = FixApplicator(repo_path, create_branch=False)

        # Mark fix as approved
        sample_fix_proposal.status = FixStatus.APPROVED

        # Apply fix
        success, error = applicator.apply_fix(sample_fix_proposal, commit=False)

        # Verify success
        assert success is True
        assert error is None
        assert sample_fix_proposal.status == FixStatus.APPLIED

        # Verify file was modified
        test_file = repo_path / "example.py"
        content = test_file.read_text()
        assert "if x is None:  # Fixed" in content
        assert "if x == None:  # Bad" not in content

    def test_apply_fix_with_git_commit(self, temp_repo, sample_fix_proposal):
        """Test applying fix with git commit."""
        repo_path, repo = temp_repo

        # Create applicator
        applicator = FixApplicator(repo_path, create_branch=False)

        # Mark fix as approved
        sample_fix_proposal.status = FixStatus.APPROVED

        # Apply fix with commit
        success, error = applicator.apply_fix(sample_fix_proposal, commit=True)

        # Verify success
        assert success is True

        # Verify commit was created
        assert len(list(repo.iter_commits())) == 2  # Initial + fix commit
        latest_commit = list(repo.iter_commits())[0]
        assert "Fix None comparison" in latest_commit.message

    def test_apply_fix_with_branch_creation(self, temp_repo, sample_fix_proposal):
        """Test applying fix with branch creation."""
        repo_path, repo = temp_repo

        # Create applicator with branch creation
        applicator = FixApplicator(repo_path, create_branch=True)

        # Mark fix as approved
        sample_fix_proposal.status = FixStatus.APPROVED

        # Apply fix
        success, error = applicator.apply_fix(sample_fix_proposal, commit=True)

        # Verify success
        assert success is True

        # Verify branch was created
        assert sample_fix_proposal.branch_name in repo.heads

        # Verify we're on the fix branch
        assert repo.active_branch.name == sample_fix_proposal.branch_name

    def test_apply_batch_fixes(self, temp_repo, sample_fix_proposal):
        """Test applying multiple fixes at once."""
        repo_path, repo = temp_repo

        # Create second fix
        fix2 = FixProposal(
            id="fix-456",
            finding=sample_fix_proposal.finding,
            fix_type=FixType.DOCUMENTATION,
            confidence=FixConfidence.HIGH,
            changes=[
                CodeChange(
                    file_path=Path("example.py"),
                    original_code="def process_data(x):",
                    fixed_code='def process_data(x):\n    """Process data by doubling each item."""',
                    start_line=2,
                    end_line=2,
                    description="Add docstring",
                )
            ],
            title="Add docstring",
            description="Add missing docstring",
            rationale="Improve documentation",
            evidence=Evidence(),
            syntax_valid=True,
            status=FixStatus.APPROVED,
        )

        sample_fix_proposal.status = FixStatus.APPROVED

        # Create applicator
        applicator = FixApplicator(repo_path, create_branch=False)

        # Apply batch
        successful, failed = applicator.apply_batch([sample_fix_proposal, fix2], commit_each=False)

        # Verify both applied
        assert len(successful) == 2
        assert len(failed) == 0

    def test_rollback_changes(self, temp_repo, sample_fix_proposal):
        """Test rolling back applied changes."""
        repo_path, repo = temp_repo

        # Create applicator
        applicator = FixApplicator(repo_path, create_branch=False)

        # Apply fix
        sample_fix_proposal.status = FixStatus.APPROVED
        applicator.apply_fix(sample_fix_proposal, commit=False)

        # Verify file was modified
        test_file = repo_path / "example.py"
        assert "if x is None:  # Fixed" in test_file.read_text()

        # Rollback
        success = applicator.rollback()

        # Verify rollback
        assert success is True
        assert "if x == None:  # Bad" in test_file.read_text()
        assert "if x is None:  # Fixed" not in test_file.read_text()


class TestInteractiveReviewerIntegration:
    """Integration tests for InteractiveReviewer."""

    def test_review_fix_display(self, sample_fix_proposal):
        """Test that fix display doesn't crash."""
        from rich.console import Console
        from io import StringIO

        # Create console with string buffer
        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)

        # Create reviewer
        reviewer = InteractiveReviewer(console)

        # Mock the approval prompt to return True
        with patch("repotoire.autofix.reviewer.Confirm.ask", return_value=True):
            approved = reviewer.review_fix(sample_fix_proposal)

        # Verify approval
        assert approved is True

        # Verify output contains key information
        output_text = output.getvalue()
        assert "fix-123" in output_text  # Fix ID
        assert "PEP 8" in output_text  # Evidence

    def test_review_batch_with_auto_approve(self, sample_fix_proposal):
        """Test batch review with auto-approve."""
        from rich.console import Console
        from io import StringIO

        output = StringIO()
        console = Console(file=output, force_terminal=True)

        reviewer = InteractiveReviewer(console)

        # Review with auto-approve
        approved = reviewer.review_batch([sample_fix_proposal], auto_approve_high=True)

        # High-confidence fix should be auto-approved
        assert len(approved) == 1
        assert approved[0].status == FixStatus.APPROVED


class TestEndToEndWorkflow:
    """End-to-end tests of the complete auto-fix workflow."""

    @pytest.mark.asyncio
    @patch("repotoire.autofix.engine.LLMClient")
    @patch("repotoire.autofix.engine.CodeEmbedder")
    @patch("repotoire.autofix.engine.GraphRAGRetriever")
    async def test_complete_workflow(
        self, mock_retriever_class, mock_embedder_class, mock_llm_class, sample_finding, temp_repo
    ):
        """Test complete workflow: generate -> review -> apply."""
        repo_path, repo = temp_repo

        # Mock Neo4j
        mock_neo4j = Mock()

        # Mock LLM client - LLMClient.generate returns string directly
        mock_llm = Mock()
        mock_llm.generate.return_value = """```json
{
    "title": "Fix None comparison",
    "description": "Change == None to is None",
    "rationale": "PEP 8 compliance",
    "evidence": {
        "documentation_refs": ["PEP 8"],
        "best_practices": ["Explicit is better"],
        "similar_patterns": []
    },
    "changes": [{
        "file_path": "example.py",
        "original_code": "    if x == None:  # Bad: should use 'is None'\\n        return []",
        "fixed_code": "    if x is None:  # Fixed\\n        return []",
        "start_line": 3,
        "end_line": 4,
        "description": "Fix"
    }]
}
```"""
        mock_llm_class.return_value = mock_llm

        # Mock RAG
        mock_retriever = Mock()
        mock_retriever.retrieve.return_value = []
        mock_retriever_class.return_value = mock_retriever

        # Step 1: Generate fix
        engine = AutoFixEngine(mock_neo4j, openai_api_key="test-key")
        fix = await engine.generate_fix(sample_finding, repo_path)

        assert fix is not None
        assert fix.syntax_valid is True

        # Step 2: Approve fix (simulate review)
        fix.status = FixStatus.APPROVED

        # Step 3: Apply fix
        applicator = FixApplicator(repo_path, create_branch=True)
        success, error = applicator.apply_fix(fix, commit=True)

        assert success is True
        assert fix.status == FixStatus.APPLIED

        # Verify file was fixed
        test_file = repo_path / "example.py"
        content = test_file.read_text()
        assert "if x is None:" in content

        # Verify git state
        assert fix.branch_name in repo.heads
        assert len(list(repo.iter_commits())) == 2  # Initial + fix

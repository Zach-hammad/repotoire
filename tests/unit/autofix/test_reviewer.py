"""Unit tests for InteractiveReviewer."""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from io import StringIO
from datetime import datetime

from rich.console import Console

from repotoire.autofix.reviewer import InteractiveReviewer
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
def mock_console():
    """Create a mock Rich console."""
    output = StringIO()
    console = Console(file=output, force_terminal=True, width=120)
    return console, output


@pytest.fixture
def sample_finding():
    """Create a sample finding."""
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
                original_code="if x == None:",
                fixed_code="if x is None:",
                start_line=3,
                end_line=3,
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


@pytest.fixture
def low_confidence_fix(sample_finding):
    """Create a low confidence fix."""
    return FixProposal(
        id="fix-456",
        finding=sample_finding,
        fix_type=FixType.REFACTOR,
        confidence=FixConfidence.LOW,
        changes=[
            CodeChange(
                file_path=Path("example.py"),
                original_code="def foo():",
                fixed_code="def bar():",
                start_line=1,
                end_line=1,
                description="Rename function",
            )
        ],
        title="Rename function",
        description="Rename foo to bar",
        rationale="Better naming",
        evidence=Evidence(),
        syntax_valid=False,
        status=FixStatus.PENDING,
    )


class TestInteractiveReviewer:
    """Unit tests for InteractiveReviewer class."""

    def test_init_with_custom_console(self):
        """Test initialization with custom console."""
        console = Console()
        reviewer = InteractiveReviewer(console)
        assert reviewer.console is console

    def test_init_without_console(self):
        """Test initialization creates default console."""
        reviewer = InteractiveReviewer()
        assert reviewer.console is not None
        assert isinstance(reviewer.console, Console)

    def test_generate_diff(self, sample_fix_proposal):
        """Test diff generation."""
        reviewer = InteractiveReviewer()
        diff = reviewer._generate_diff(
            "if x == None:",
            "if x is None:",
            "example.py"
        )

        assert "---" in diff
        assert "+++" in diff
        assert "a/example.py" in diff
        assert "b/example.py" in diff
        assert "-if x == None:" in diff or "== None" in diff
        assert "+if x is None:" in diff or "is None" in diff

    def test_generate_diff_multiline(self):
        """Test diff generation with multiline code."""
        reviewer = InteractiveReviewer()
        original = "def foo():\n    pass"
        fixed = "def bar():\n    pass"

        diff = reviewer._generate_diff(original, fixed, "test.py")

        assert "---" in diff
        assert "+++" in diff

    def test_show_metadata(self, mock_console, sample_fix_proposal):
        """Test metadata display."""
        console, output = mock_console
        reviewer = InteractiveReviewer(console)

        reviewer._show_metadata(sample_fix_proposal)

        output_text = output.getvalue()
        assert "fix-123" in output_text
        assert "Use 'is None'" in output_text
        assert "MEDIUM" in output_text
        assert "Refactor" in output_text
        assert "HIGH" in output_text
        assert "example.py" in output_text

    def test_show_evidence_with_all_fields(self, mock_console, sample_fix_proposal):
        """Test evidence display with all fields populated."""
        console, output = mock_console
        reviewer = InteractiveReviewer(console)

        reviewer._show_evidence(sample_fix_proposal)

        output_text = output.getvalue()
        assert "Documentation & Standards" in output_text or "PEP 8" in output_text
        assert "Best Practices" in output_text or "more explicit" in output_text
        assert "Similar Patterns" in output_text or "10 other files" in output_text

    def test_show_evidence_empty(self, mock_console, sample_finding):
        """Test evidence display with empty evidence."""
        console, output = mock_console
        reviewer = InteractiveReviewer(console)

        fix = FixProposal(
            id="fix-1",
            finding=sample_finding,
            fix_type=FixType.REFACTOR,
            confidence=FixConfidence.MEDIUM,
            changes=[],
            title="Test",
            description="Test",
            rationale="Test",
            evidence=Evidence(),  # Empty evidence
            syntax_valid=True,
        )

        reviewer._show_evidence(fix)

        # Should not crash, output may be empty or minimal
        output_text = output.getvalue()
        assert output_text is not None  # Just check it doesn't crash

    def test_show_code_change(self, mock_console, sample_fix_proposal):
        """Test code change display."""
        console, output = mock_console
        reviewer = InteractiveReviewer(console)

        change = sample_fix_proposal.changes[0]
        reviewer._show_code_change(change, index=1, total=1)

        output_text = output.getvalue()
        assert "example.py" in output_text
        assert "Use 'is None'" in output_text

    def test_show_validation_syntax_valid(self, mock_console, sample_fix_proposal):
        """Test validation display for valid syntax."""
        console, output = mock_console
        reviewer = InteractiveReviewer(console)

        reviewer._show_validation(sample_fix_proposal)

        output_text = output.getvalue()
        assert "Syntax valid" in output_text or "✓" in output_text

    def test_show_validation_syntax_invalid(self, mock_console, low_confidence_fix):
        """Test validation display for invalid syntax."""
        console, output = mock_console
        reviewer = InteractiveReviewer(console)

        reviewer._show_validation(low_confidence_fix)

        output_text = output.getvalue()
        assert "Syntax errors" in output_text or "✗" in output_text

    def test_show_tests(self, mock_console, sample_fix_proposal):
        """Test generated tests display."""
        console, output = mock_console
        reviewer = InteractiveReviewer(console)

        sample_fix_proposal.tests_generated = True
        sample_fix_proposal.test_code = "def test_foo():\n    assert True"

        reviewer._show_tests(sample_fix_proposal)

        output_text = output.getvalue()
        assert "test_foo" in output_text or "Generated Tests" in output_text

    @patch("repotoire.autofix.reviewer.Confirm.ask", return_value=True)
    def test_prompt_approval_high_confidence(self, mock_confirm, mock_console, sample_fix_proposal):
        """Test approval prompt for high confidence fix."""
        console, output = mock_console
        reviewer = InteractiveReviewer(console)

        approved = reviewer._prompt_approval(sample_fix_proposal)

        assert approved is True
        mock_confirm.assert_called_once()

    @patch("repotoire.autofix.reviewer.Confirm.ask", return_value=False)
    def test_prompt_approval_rejected(self, mock_confirm, mock_console, sample_fix_proposal):
        """Test rejection in approval prompt."""
        console, output = mock_console
        reviewer = InteractiveReviewer(console)

        approved = reviewer._prompt_approval(sample_fix_proposal)

        assert approved is False
        mock_confirm.assert_called_once()

    @patch("repotoire.autofix.reviewer.Confirm.ask", return_value=True)
    def test_prompt_approval_low_confidence_warning(self, mock_confirm, mock_console, low_confidence_fix):
        """Test approval prompt shows warning for low confidence."""
        console, output = mock_console
        reviewer = InteractiveReviewer(console)

        reviewer._prompt_approval(low_confidence_fix)

        output_text = output.getvalue()
        assert "Warning" in output_text or "LOW confidence" in output_text

    @patch("repotoire.autofix.reviewer.Confirm.ask", return_value=True)
    def test_prompt_approval_invalid_syntax_warning(self, mock_confirm, mock_console, low_confidence_fix):
        """Test approval prompt shows warning for invalid syntax."""
        console, output = mock_console
        reviewer = InteractiveReviewer(console)

        reviewer._prompt_approval(low_confidence_fix)

        output_text = output.getvalue()
        assert "Syntax validation failed" in output_text or "Warning" in output_text

    @patch("repotoire.autofix.reviewer.Confirm.ask", side_effect=[True, True])
    def test_review_fix_approved(self, mock_confirm, mock_console, sample_fix_proposal):
        """Test reviewing and approving a fix."""
        console, output = mock_console
        reviewer = InteractiveReviewer(console)

        with patch.object(console, "clear"):
            approved = reviewer.review_fix(sample_fix_proposal)

        assert approved is True
        # Should show all sections
        output_text = output.getvalue()
        assert "fix-123" in output_text

    @patch("repotoire.autofix.reviewer.Confirm.ask", return_value=False)
    def test_review_fix_rejected(self, mock_confirm, mock_console, sample_fix_proposal):
        """Test reviewing and rejecting a fix."""
        console, output = mock_console
        reviewer = InteractiveReviewer(console)

        with patch.object(console, "clear"):
            approved = reviewer.review_fix(sample_fix_proposal)

        assert approved is False

    def test_review_batch_empty(self, mock_console):
        """Test batch review with no fixes."""
        console, output = mock_console
        reviewer = InteractiveReviewer(console)

        approved = reviewer.review_batch([])

        assert approved == []
        output_text = output.getvalue()
        assert "No fixes to review" in output_text

    @patch("repotoire.autofix.reviewer.Confirm.ask", side_effect=[True, True])
    def test_review_batch_manual_approval(self, mock_confirm, mock_console, sample_fix_proposal):
        """Test batch review with manual approval."""
        console, output = mock_console
        reviewer = InteractiveReviewer(console)

        with patch.object(console, "clear"):
            approved = reviewer.review_batch([sample_fix_proposal], auto_approve_high=False)

        assert len(approved) == 1
        assert approved[0].status == FixStatus.APPROVED

    def test_review_batch_auto_approve_high(self, mock_console, sample_fix_proposal):
        """Test batch review with auto-approve for high confidence."""
        console, output = mock_console
        reviewer = InteractiveReviewer(console)

        approved = reviewer.review_batch([sample_fix_proposal], auto_approve_high=True)

        assert len(approved) == 1
        assert approved[0].status == FixStatus.APPROVED
        output_text = output.getvalue()
        assert "Auto-approved" in output_text or "high confidence" in output_text

    @patch("repotoire.autofix.reviewer.Confirm.ask", side_effect=[True, False])  # First approved, then stop
    def test_review_batch_multiple_fixes(self, mock_confirm, mock_console, sample_fix_proposal, sample_finding):
        """Test batch review with multiple fixes."""
        console, output = mock_console
        reviewer = InteractiveReviewer(console)

        fix2 = FixProposal(
            id="fix-789",
            finding=sample_finding,
            fix_type=FixType.DOCUMENTATION,
            confidence=FixConfidence.MEDIUM,
            changes=[],
            title="Add docstring",
            description="Add missing docstring",
            rationale="Documentation",
            evidence=Evidence(),
            syntax_valid=True,
        )

        with patch.object(console, "clear"):
            approved = reviewer.review_batch([sample_fix_proposal, fix2], auto_approve_high=False)

        # First fix approved, second skipped because user said no to continue
        assert len(approved) >= 1
        output_text = output.getvalue()
        assert "Summary" in output_text

    @patch("repotoire.autofix.reviewer.Confirm.ask", side_effect=[False, True, True])  # Reject first, continue, approve second
    def test_review_batch_with_rejection(self, mock_confirm, mock_console, sample_fix_proposal, sample_finding):
        """Test batch review with some rejections."""
        console, output = mock_console
        reviewer = InteractiveReviewer(console)

        fix2 = FixProposal(
            id="fix-789",
            finding=sample_finding,
            fix_type=FixType.DOCUMENTATION,
            confidence=FixConfidence.MEDIUM,
            changes=[],
            title="Add docstring",
            description="Add missing docstring",
            rationale="Documentation",
            evidence=Evidence(),
            syntax_valid=True,
        )

        with patch.object(console, "clear"):
            approved = reviewer.review_batch([sample_fix_proposal, fix2], auto_approve_high=False)

        # First rejected, second approved
        assert sample_fix_proposal.status == FixStatus.REJECTED
        assert fix2.status == FixStatus.APPROVED
        assert len(approved) == 1

    def test_show_summary(self, mock_console):
        """Test summary display."""
        console, output = mock_console
        reviewer = InteractiveReviewer(console)

        reviewer.show_summary(total=10, approved=7, applied=6, failed=1)

        output_text = output.getvalue()
        assert "10" in output_text  # Total
        assert "7" in output_text   # Approved
        assert "6" in output_text   # Applied
        assert "1" in output_text   # Failed

    def test_show_summary_no_fixes(self, mock_console):
        """Test summary with no fixes."""
        console, output = mock_console
        reviewer = InteractiveReviewer(console)

        reviewer.show_summary(total=5, approved=0, applied=0, failed=0)

        output_text = output.getvalue()
        assert "No fixes were approved" in output_text or "0" in output_text

    def test_show_summary_all_failed(self, mock_console):
        """Test summary with all fixes failed."""
        console, output = mock_console
        reviewer = InteractiveReviewer(console)

        reviewer.show_summary(total=5, approved=5, applied=0, failed=5)

        output_text = output.getvalue()
        assert "failed" in output_text.lower()

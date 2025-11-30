"""Interactive fix review UI for human-in-the-loop approval."""

from pathlib import Path
from typing import Optional, List, Tuple
import difflib

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.prompt import Confirm, Prompt
from rich import box

from repotoire.autofix.models import FixProposal, FixStatus, FixConfidence
from repotoire.autofix.learning import (
    DecisionStore,
    AdaptiveConfidence,
    FixDecision,
    UserDecision,
    RejectionReason,
    create_decision_id,
)
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Map of rejection reason choices
REJECTION_REASONS = {
    "1": (RejectionReason.STYLE_MISMATCH, "Style mismatch (naming, formatting)"),
    "2": (RejectionReason.TOO_RISKY, "Too risky or broad change"),
    "3": (RejectionReason.INCORRECT_LOGIC, "Incorrect logic or fix"),
    "4": (RejectionReason.NOT_NEEDED, "Fix not needed"),
    "5": (RejectionReason.BREAKS_TESTS, "Would break tests"),
    "6": (RejectionReason.OTHER, "Other reason"),
}


class InteractiveReviewer:
    """Interactive UI for reviewing and approving fixes."""

    def __init__(
        self,
        console: Optional[Console] = None,
        decision_store: Optional[DecisionStore] = None,
        repository_path: Optional[Path] = None,
    ):
        """Initialize reviewer.

        Args:
            console: Rich console for output (creates new if None)
            decision_store: Store for recording decisions (creates default if None)
            repository_path: Path to repository for learning context
        """
        self.console = console or Console()
        self.decision_store = decision_store or DecisionStore()
        self.repository_path = repository_path
        self.adaptive_confidence = AdaptiveConfidence(self.decision_store)

    def review_fix(
        self, fix: FixProposal
    ) -> Tuple[bool, Optional[RejectionReason], Optional[str]]:
        """Review a fix proposal interactively.

        Args:
            fix: Fix proposal to review

        Returns:
            Tuple of (approved, rejection_reason, rejection_comment)
        """
        self.console.clear()
        self.console.rule("[bold blue]Auto-Fix Proposal[/bold blue]", style="blue")
        self.console.print()

        # Show historical context if available
        self._show_historical_context(fix)

        # Show fix metadata
        self._show_metadata(fix)
        self.console.print()

        # Show each code change
        for i, change in enumerate(fix.changes, 1):
            self._show_code_change(change, index=i, total=len(fix.changes))
            self.console.print()

        # Show validation status
        self._show_validation(fix)
        self.console.print()

        # Show generated tests (if available)
        if fix.tests_generated and fix.test_code:
            self._show_tests(fix)
            self.console.print()

        # Prompt for approval
        return self._prompt_approval(fix)

    def _show_historical_context(self, fix: FixProposal) -> None:
        """Show historical context for this fix type.

        Args:
            fix: The fix being reviewed
        """
        repo_str = str(self.repository_path) if self.repository_path else None
        context = self.adaptive_confidence.get_review_context(
            fix_type=fix.fix_type.value,
            confidence=fix.confidence,
            repository=repo_str,
        )

        if context:
            self.console.print(f"[dim]💡 {context}[/dim]")
            self.console.print()

        # Show warnings for historically rejected fix types
        warnings = self.adaptive_confidence.get_warnings(
            fix_type=fix.fix_type.value,
            repository=repo_str,
        )
        for warning in warnings:
            self.console.print(f"[yellow]⚠️  {warning}[/yellow]")

        if warnings:
            self.console.print()

    def _show_metadata(self, fix: FixProposal) -> None:
        """Display fix metadata."""
        # Confidence badge
        confidence_color = {
            FixConfidence.HIGH: "green",
            FixConfidence.MEDIUM: "yellow",
            FixConfidence.LOW: "red",
        }[fix.confidence]

        # Create metadata table
        table = Table(box=box.ROUNDED, show_header=False, padding=(0, 1))
        table.add_column("Field", style="cyan", width=20)
        table.add_column("Value")

        table.add_row("Fix ID", f"[bold]{fix.id}[/bold]")
        table.add_row("Issue", f"[yellow]{fix.finding.title}[/yellow]")
        table.add_row(
            "Severity",
            f"[red]{fix.finding.severity.value.upper()}[/red]"
            if fix.finding.severity.value == "critical"
            else f"{fix.finding.severity.value.upper()}",
        )
        table.add_row("Fix Type", fix.fix_type.value.replace("_", " ").title())
        table.add_row(
            "Confidence",
            f"[{confidence_color}]●[/{confidence_color}] {fix.confidence.value.upper()}",
        )
        table.add_row("Files", ", ".join(fix.finding.affected_files))

        self.console.print(table)
        self.console.print()

        # Show description and rationale
        self.console.print(Panel(fix.description, title="[bold]Description[/bold]", border_style="blue"))
        self.console.print()
        self.console.print(Panel(fix.rationale, title="[bold]Rationale[/bold]", border_style="green"))
        self.console.print()

        # Show evidence/research backing
        self._show_evidence(fix)

    def _show_evidence(self, fix: FixProposal) -> None:
        """Display research backing and evidence for the fix."""
        if not fix.evidence:
            return

        evidence_lines = []

        # Documentation references
        if fix.evidence.documentation_refs:
            evidence_lines.append("[bold cyan]📚 Documentation & Standards:[/bold cyan]")
            for ref in fix.evidence.documentation_refs:
                evidence_lines.append(f"  • {ref}")
            evidence_lines.append("")

        # Best practices
        if fix.evidence.best_practices:
            evidence_lines.append("[bold green]✓ Best Practices:[/bold green]")
            for practice in fix.evidence.best_practices:
                evidence_lines.append(f"  • {practice}")
            evidence_lines.append("")

        # Similar patterns from codebase
        if fix.evidence.similar_patterns:
            evidence_lines.append("[bold magenta]🔍 Similar Patterns in Codebase:[/bold magenta]")
            for pattern in fix.evidence.similar_patterns:
                evidence_lines.append(f"  • {pattern}")
            evidence_lines.append("")

        # RAG context (related code)
        if fix.evidence.rag_context:
            evidence_lines.append("[bold yellow]🧠 Related Code (RAG):[/bold yellow]")
            evidence_lines.append(f"  Found {len(fix.evidence.rag_context)} related code snippet(s)")

        if evidence_lines:
            evidence_text = "\n".join(evidence_lines)
            self.console.print(
                Panel(evidence_text, title="[bold]Research & Evidence[/bold]", border_style="cyan")
            )
            self.console.print()

    def _show_code_change(self, change, index: int, total: int) -> None:
        """Display a code change with diff."""
        title = f"Change {index}/{total}: {change.file_path}"
        if change.description:
            title += f" - {change.description}"

        self.console.print(Panel(title, style="bold magenta"))

        # Generate diff
        diff = self._generate_diff(
            change.original_code, change.fixed_code, str(change.file_path)
        )

        # Show diff with syntax highlighting
        self.console.print(Syntax(diff, "diff", theme="monokai", line_numbers=False))

    def _generate_diff(self, original: str, fixed: str, filename: str) -> str:
        """Generate unified diff between original and fixed code.

        Args:
            original: Original code
            fixed: Fixed code
            filename: File name for diff header

        Returns:
            Unified diff string
        """
        original_lines = original.splitlines(keepends=True)
        fixed_lines = fixed.splitlines(keepends=True)

        diff = difflib.unified_diff(
            original_lines,
            fixed_lines,
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
            lineterm="",
        )

        return "".join(diff)

    def _show_validation(self, fix: FixProposal) -> None:
        """Display validation status."""
        validation_items = []

        # Syntax validation
        if fix.syntax_valid:
            validation_items.append("[green]✓[/green] Syntax valid")
        else:
            validation_items.append("[red]✗[/red] Syntax errors detected")

        # Test generation
        if fix.tests_generated:
            validation_items.append("[green]✓[/green] Tests generated")
        else:
            validation_items.append("[yellow]○[/yellow] No tests generated")

        validation_text = " | ".join(validation_items)
        self.console.print(Panel(validation_text, title="[bold]Validation[/bold]", border_style="cyan"))

    def _show_tests(self, fix: FixProposal) -> None:
        """Display generated test code."""
        self.console.print(Panel("[bold]Generated Tests[/bold]", style="bold green"))
        self.console.print(
            Syntax(fix.test_code, "python", theme="monokai", line_numbers=True)
        )

    def _prompt_approval(
        self, fix: FixProposal
    ) -> Tuple[bool, Optional[RejectionReason], Optional[str]]:
        """Prompt user for approval.

        Args:
            fix: Fix proposal

        Returns:
            Tuple of (approved, rejection_reason, rejection_comment)
        """
        self.console.rule(style="blue")

        # Show warning for low confidence fixes
        if fix.confidence == FixConfidence.LOW:
            self.console.print(
                "[yellow]⚠️  Warning: This fix has LOW confidence. Please review carefully.[/yellow]"
            )
            self.console.print()

        # Show warning for invalid syntax
        if not fix.syntax_valid:
            self.console.print(
                "[red]⚠️  Warning: Syntax validation failed. This fix may not work.[/red]"
            )
            self.console.print()

        # Prompt for decision
        approved = Confirm.ask(
            "[bold cyan]Apply this fix?[/bold cyan]",
            default=fix.confidence == FixConfidence.HIGH,
        )

        if approved:
            return (True, None, None)

        # Collect rejection reason
        return self._collect_rejection_info()

    def _collect_rejection_info(self) -> Tuple[bool, Optional[RejectionReason], Optional[str]]:
        """Collect rejection reason and optional comment.

        Returns:
            Tuple of (False, rejection_reason, rejection_comment)
        """
        self.console.print()
        self.console.print("[bold]Why are you rejecting this fix?[/bold]")

        # Show reason options
        for key, (_, description) in REJECTION_REASONS.items():
            self.console.print(f"  {key}. {description}")

        # Get reason choice
        choice = Prompt.ask(
            "Select reason",
            choices=list(REJECTION_REASONS.keys()),
            default="6",
        )

        reason, _ = REJECTION_REASONS[choice]

        # Get optional comment
        comment = None
        if Confirm.ask("[dim]Add a comment?[/dim]", default=False):
            comment = Prompt.ask("[dim]Comment[/dim]")

        return (False, reason, comment)

    def review_batch(
        self, fixes: List[FixProposal], auto_approve_high: bool = False
    ) -> List[FixProposal]:
        """Review multiple fixes interactively.

        Args:
            fixes: List of fix proposals
            auto_approve_high: Automatically approve high-confidence fixes

        Returns:
            List of approved fixes
        """
        if not fixes:
            self.console.print("[yellow]No fixes to review.[/yellow]")
            return []

        approved_fixes = []
        repo_str = str(self.repository_path) if self.repository_path else ""

        self.console.print(f"\n[bold]Reviewing {len(fixes)} fix proposal(s)...[/bold]\n")

        for i, fix in enumerate(fixes, 1):
            self.console.print(f"[dim]Fix {i} of {len(fixes)}[/dim]")
            self.console.print()

            # Check if auto-approve should be skipped for this fix type
            skip_auto = self.adaptive_confidence.should_skip_auto_approve(
                fix_type=fix.fix_type.value,
                repository=repo_str if repo_str else None,
            )

            # Auto-approve high confidence if requested (unless historically rejected)
            if (
                auto_approve_high
                and fix.confidence == FixConfidence.HIGH
                and fix.syntax_valid
                and not skip_auto
            ):
                self.console.print(
                    f"[green]✓[/green] Auto-approved (high confidence): {fix.title}"
                )
                fix.status = FixStatus.APPROVED
                approved_fixes.append(fix)

                # Record the auto-approval
                self._record_decision(fix, UserDecision.APPROVED, None, None)

                self.console.print()
                continue

            # Show warning if auto-approve was skipped
            if auto_approve_high and skip_auto and fix.confidence == FixConfidence.HIGH:
                self.console.print(
                    f"[yellow]⚠️  Skipping auto-approve for '{fix.fix_type.value}' "
                    f"(historically rejected)[/yellow]"
                )
                self.console.print()

            # Interactive review
            approved, rejection_reason, rejection_comment = self.review_fix(fix)

            if approved:
                fix.status = FixStatus.APPROVED
                approved_fixes.append(fix)
                self.console.print("[green]✓ Fix approved[/green]\n")

                # Record approval
                self._record_decision(fix, UserDecision.APPROVED, None, None)
            else:
                fix.status = FixStatus.REJECTED
                self.console.print("[red]✗ Fix rejected[/red]\n")

                # Record rejection with reason
                self._record_decision(
                    fix, UserDecision.REJECTED, rejection_reason, rejection_comment
                )

            # Ask to continue if more fixes
            if i < len(fixes):
                if not Confirm.ask("[dim]Continue to next fix?[/dim]", default=True):
                    break

        # Summary
        self.console.rule(style="blue")
        self.console.print(
            f"\n[bold]Summary:[/bold] {len(approved_fixes)}/{len(fixes)} fixes approved\n"
        )

        return approved_fixes

    def _record_decision(
        self,
        fix: FixProposal,
        decision: UserDecision,
        rejection_reason: Optional[RejectionReason],
        rejection_comment: Optional[str],
    ) -> None:
        """Record a decision to the store.

        Args:
            fix: The fix proposal
            decision: User's decision
            rejection_reason: Reason for rejection (if rejected)
            rejection_comment: User's comment (if provided)
        """
        try:
            file_path = fix.finding.affected_files[0] if fix.finding.affected_files else ""
            repo_str = str(self.repository_path) if self.repository_path else ""

            # Calculate characteristics
            total_lines_changed = sum(
                len(c.fixed_code.splitlines()) for c in fix.changes
            )

            fix_decision = FixDecision(
                id=create_decision_id(fix.id),
                fix_id=fix.id,
                decision=decision,
                rejection_reason=rejection_reason,
                rejection_comment=rejection_comment,
                fix_type=fix.fix_type.value,
                confidence=fix.confidence.value.upper(),
                finding_type=fix.finding.title,
                file_path=file_path,
                repository=repo_str,
                characteristics={
                    "lines_changed": total_lines_changed,
                    "has_tests": fix.tests_generated,
                    "syntax_valid": fix.syntax_valid,
                    "num_changes": len(fix.changes),
                },
            )

            self.decision_store.record(fix_decision)
            logger.debug(f"Recorded decision: {decision.value} for fix {fix.id}")

        except Exception as e:
            logger.warning(f"Failed to record decision: {e}")

    def show_summary(
        self, total: int, approved: int, applied: int, failed: int
    ) -> None:
        """Show final summary of fix session.

        Args:
            total: Total fixes generated
            approved: Number approved
            applied: Number successfully applied
            failed: Number that failed to apply
        """
        self.console.rule("[bold blue]Auto-Fix Session Summary[/bold blue]", style="blue")
        self.console.print()

        table = Table(box=box.ROUNDED, show_header=False, padding=(0, 2))
        table.add_column("Metric", style="cyan", width=25)
        table.add_column("Count", justify="right", style="bold")

        table.add_row("Fixes Generated", str(total))
        table.add_row("Approved for Application", str(approved))
        table.add_row("Successfully Applied", f"[green]{applied}[/green]")
        if failed > 0:
            table.add_row("Failed to Apply", f"[red]{failed}[/red]")

        self.console.print(table)
        self.console.print()

        # Success message
        if applied > 0:
            self.console.print(
                f"[green]✓ {applied} fix(es) have been applied to your codebase.[/green]"
            )
            self.console.print(
                "[dim]Review the changes with 'git diff' and commit when ready.[/dim]"
            )
        elif approved == 0:
            self.console.print("[yellow]No fixes were approved.[/yellow]")
        else:
            self.console.print("[red]All approved fixes failed to apply.[/red]")

        self.console.print()

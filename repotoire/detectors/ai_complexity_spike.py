"""AI complexity spike detector.

Detects sudden complexity increases in previously simple functions.
This pattern often occurs when AI adds features without proper refactoring.

The detector tracks cyclomatic complexity per function over git history
and flags significant jumps (e.g., from <5 to >15) as potential AI-induced
complexity spikes.
"""

import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph import FalkorDBClient
from repotoire.logging_config import get_logger
from repotoire.models import CollaborationMetadata, Finding, Severity

logger = get_logger(__name__)

# Check for GitPython
try:
    import git
    GIT_AVAILABLE = True
except ImportError:
    GIT_AVAILABLE = False
    git = None  # type: ignore


@dataclass
class ComplexitySpike:
    """Represents a detected complexity spike in a function."""

    file_path: str
    function_name: str
    qualified_name: str
    before_complexity: int
    after_complexity: int
    complexity_delta: int
    spike_date: datetime
    commit_sha: str
    commit_message: str
    author: str
    line_number: int


class AIComplexitySpikeDetector(CodeSmellDetector):
    """Detects sudden complexity increases in previously simple functions.

    This detector identifies functions where cyclomatic complexity jumped
    significantly, which often happens when AI assistants add features
    without proper refactoring.

    Configuration:
        repository_path: Path to repository root (required)
        spike_threshold: Minimum complexity increase to flag (default: 10)
        before_max: Maximum "before" complexity to qualify as "previously simple" (default: 5)
        after_min: Minimum "after" complexity to flag (default: 15)
        window_days: Only flag spikes within this many days (default: 30)
        max_findings: Maximum findings to report (default: 50)
    """

    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict] = None,
    ):
        """Initialize AI complexity spike detector.

        Args:
            graph_client: FalkorDB database client
            detector_config: Configuration dictionary with:
                - repository_path: Path to repository root (required)
                - spike_threshold: Minimum complexity delta to flag
                - before_max: Max "simple" complexity threshold
                - after_min: Min "complex" complexity threshold
                - window_days: Flag spikes within this window
                - max_findings: Max findings to report
        """
        super().__init__(graph_client, detector_config)

        config = detector_config or {}
        self.repository_path = Path(config.get("repository_path", "."))
        self.spike_threshold = config.get("spike_threshold", 10)
        self.before_max = config.get("before_max", 5)
        self.after_min = config.get("after_min", 15)
        self.window_days = config.get("window_days", 30)
        self.max_findings = config.get("max_findings", 50)

        if not self.repository_path.exists():
            raise ValueError(f"Repository path does not exist: {self.repository_path}")

    def detect(self) -> List[Finding]:
        """Run detection for AI complexity spikes.

        Returns:
            List of findings for detected complexity spikes
        """
        if not GIT_AVAILABLE:
            logger.warning("GitPython not available, skipping AI complexity spike detection")
            return []

        logger.info(f"Running AI complexity spike detection on {self.repository_path}")

        # Get complexity history from git
        spikes = self._find_complexity_spikes()

        # Create findings
        findings = []
        for spike in spikes[: self.max_findings]:
            finding = self._create_finding(spike)
            if finding:
                findings.append(finding)

        logger.info(f"Found {len(findings)} AI complexity spike findings")
        return findings

    def _find_complexity_spikes(self) -> List[ComplexitySpike]:
        """Find functions with significant complexity spikes.

        Returns:
            List of ComplexitySpike objects
        """
        try:
            repo = git.Repo(self.repository_path, search_parent_directories=True)
        except (git.exc.InvalidGitRepositoryError, git.exc.NoSuchPathError):
            logger.warning(f"Not a git repository: {self.repository_path}")
            return []

        # Get commits within window
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.window_days)
        
        # Build function complexity history
        # Map: file_path -> function_name -> [(commit_sha, commit_date, complexity)]
        complexity_history: Dict[str, Dict[str, List[Tuple[str, datetime, int]]]] = {}

        # Get relevant commits (recent first)
        try:
            commits = list(repo.iter_commits("HEAD", max_count=500))
        except git.exc.GitCommandError:
            logger.warning("Failed to iterate commits")
            return []

        # Process commits from oldest to newest to build history
        for commit in reversed(commits):
            commit_date = commit.committed_datetime
            if commit_date.tzinfo is None:
                commit_date = commit_date.replace(tzinfo=timezone.utc)

            # Get changed Python files in this commit
            changed_files = self._get_changed_python_files(commit)

            for file_path in changed_files:
                # Get complexity at this commit
                try:
                    file_content = self._get_file_at_commit(repo, commit, file_path)
                    if file_content is None:
                        continue

                    complexities = self._calculate_function_complexities(file_content)

                    if file_path not in complexity_history:
                        complexity_history[file_path] = {}

                    for func_name, complexity, line_num in complexities:
                        if func_name not in complexity_history[file_path]:
                            complexity_history[file_path][func_name] = []

                        complexity_history[file_path][func_name].append(
                            (commit.hexsha, commit_date, complexity, line_num, commit.message, commit.author.name)
                        )
                except Exception as e:
                    logger.debug(f"Failed to analyze {file_path} at {commit.hexsha[:8]}: {e}")
                    continue

        # Detect spikes
        spikes = []

        for file_path, functions in complexity_history.items():
            for func_name, history in functions.items():
                spike = self._detect_spike_in_history(file_path, func_name, history, cutoff_date)
                if spike:
                    spikes.append(spike)

        # Sort by severity (complexity delta descending, then by date)
        spikes.sort(key=lambda s: (-s.complexity_delta, -s.spike_date.timestamp()))

        return spikes

    def _get_changed_python_files(self, commit: "git.Commit") -> List[str]:
        """Get Python files changed in a commit.

        Args:
            commit: Git commit object

        Returns:
            List of changed Python file paths
        """
        changed = []

        if commit.parents:
            parent = commit.parents[0]
            try:
                diffs = parent.diff(commit)
                for diff in diffs:
                    path = diff.b_path or diff.a_path
                    if path and path.endswith(".py"):
                        changed.append(path)
            except Exception:
                pass
        else:
            # Initial commit
            try:
                for path in commit.stats.files.keys():
                    if path.endswith(".py"):
                        changed.append(path)
            except Exception:
                pass

        return changed

    def _get_file_at_commit(
        self, repo: "git.Repo", commit: "git.Commit", file_path: str
    ) -> Optional[str]:
        """Get file content at a specific commit.

        Args:
            repo: Git repository object
            commit: Commit to check
            file_path: Path to file

        Returns:
            File content as string or None if not found
        """
        try:
            blob = commit.tree / file_path
            return blob.data_stream.read().decode("utf-8", errors="replace")
        except (KeyError, AttributeError):
            return None

    def _calculate_function_complexities(
        self, source_code: str
    ) -> List[Tuple[str, int, int]]:
        """Calculate cyclomatic complexity for all functions in source code.

        Uses radon library for complexity calculation.

        Args:
            source_code: Python source code

        Returns:
            List of (function_name, complexity, line_number) tuples
        """
        try:
            from radon.complexity import cc_visit
        except ImportError:
            logger.warning("radon not installed, cannot calculate complexity")
            return []

        results = []

        try:
            blocks = cc_visit(source_code)
            for block in blocks:
                # radon returns Function, Method, and Class blocks
                # We want functions and methods
                if block.letter in ("F", "M"):
                    results.append((block.name, block.complexity, block.lineno))
                elif block.letter == "C":
                    # For classes, include methods
                    for method in getattr(block, "methods", []):
                        full_name = f"{block.name}.{method.name}"
                        results.append((full_name, method.complexity, method.lineno))
        except SyntaxError:
            # Invalid Python syntax
            pass
        except Exception as e:
            logger.debug(f"Failed to calculate complexity: {e}")

        return results

    def _detect_spike_in_history(
        self,
        file_path: str,
        func_name: str,
        history: List[Tuple[str, datetime, int, int, str, str]],
        cutoff_date: datetime,
    ) -> Optional[ComplexitySpike]:
        """Detect a complexity spike in function history.

        Args:
            file_path: Path to the file
            func_name: Function name
            history: List of (sha, date, complexity, line, message, author) tuples
            cutoff_date: Only flag spikes after this date

        Returns:
            ComplexitySpike if detected, None otherwise
        """
        if len(history) < 2:
            return None

        # Look for significant jumps
        for i in range(1, len(history)):
            prev_sha, prev_date, prev_complexity, prev_line, _, _ = history[i - 1]
            curr_sha, curr_date, curr_complexity, curr_line, message, author = history[i]

            # Ensure cutoff_date is timezone-aware for comparison
            if cutoff_date.tzinfo is None:
                cutoff_date = cutoff_date.replace(tzinfo=timezone.utc)
            if curr_date.tzinfo is None:
                curr_date = curr_date.replace(tzinfo=timezone.utc)

            # Check if within window
            if curr_date < cutoff_date:
                continue

            # Check spike criteria:
            # 1. Before was "simple" (complexity <= before_max)
            # 2. After is "complex" (complexity >= after_min)
            # 3. Delta exceeds threshold
            delta = curr_complexity - prev_complexity

            is_spike = (
                prev_complexity <= self.before_max
                and curr_complexity >= self.after_min
                and delta >= self.spike_threshold
            )

            if is_spike:
                # Construct qualified name
                qualified_name = f"{file_path}::{func_name}"

                return ComplexitySpike(
                    file_path=file_path,
                    function_name=func_name,
                    qualified_name=qualified_name,
                    before_complexity=prev_complexity,
                    after_complexity=curr_complexity,
                    complexity_delta=delta,
                    spike_date=curr_date,
                    commit_sha=curr_sha,
                    commit_message=message.strip().split("\n")[0][:100],  # First line, truncated
                    author=author,
                    line_number=curr_line,
                )

        return None

    def _create_finding(self, spike: ComplexitySpike) -> Finding:
        """Create a Finding from a ComplexitySpike.

        Args:
            spike: Detected complexity spike

        Returns:
            Finding object
        """
        # Determine severity based on recency and delta
        days_ago = (datetime.now(timezone.utc) - spike.spike_date).days
        
        if days_ago <= 7:
            severity = Severity.HIGH
        elif days_ago <= 14:
            severity = Severity.MEDIUM
        else:
            severity = Severity.LOW

        # Boost severity for extreme spikes
        if spike.complexity_delta >= 20:
            severity = Severity.HIGH

        finding_id = str(uuid.uuid4())

        description = self._build_description(spike)
        suggested_fix = self._build_suggested_fix(spike)

        finding = Finding(
            id=finding_id,
            detector="AIComplexitySpikeDetector",
            severity=severity,
            title=f"Complexity spike in '{spike.function_name}' ({spike.before_complexity} → {spike.after_complexity})",
            description=description,
            affected_nodes=[spike.qualified_name],
            affected_files=[spike.file_path],
            graph_context={
                "before_complexity": spike.before_complexity,
                "after_complexity": spike.after_complexity,
                "complexity_delta": spike.complexity_delta,
                "spike_date": spike.spike_date.isoformat(),
                "commit_sha": spike.commit_sha[:8],
                "commit_message": spike.commit_message,
                "author": spike.author,
                "line_number": spike.line_number,
                "days_ago": days_ago,
            },
            suggested_fix=suggested_fix,
            estimated_effort=self._estimate_effort(spike),
            created_at=datetime.now(),
        )

        # Add collaboration metadata
        finding.add_collaboration_metadata(
            CollaborationMetadata(
                detector="AIComplexitySpikeDetector",
                confidence=0.85,  # High confidence for detected pattern
                evidence=[
                    "complexity_spike",
                    f"delta_{spike.complexity_delta}",
                    f"before_{spike.before_complexity}",
                    f"after_{spike.after_complexity}",
                ],
                tags=["ai-generated", "complexity", "refactoring-needed"],
            )
        )

        return finding

    def _build_description(self, spike: ComplexitySpike) -> str:
        """Build description for complexity spike finding."""
        days_ago = (datetime.now(timezone.utc) - spike.spike_date).days

        desc = f"Function **{spike.function_name}** experienced a significant complexity spike.\n\n"
        desc += "### Complexity Change\n"
        desc += f"- **Before**: {spike.before_complexity} (simple)\n"
        desc += f"- **After**: {spike.after_complexity} (complex)\n"
        desc += f"- **Increase**: +{spike.complexity_delta}\n\n"
        desc += "### Commit Details\n"
        desc += f"- **When**: {days_ago} days ago ({spike.spike_date.strftime('%Y-%m-%d')})\n"
        desc += f"- **Commit**: `{spike.commit_sha[:8]}`\n"
        desc += f"- **Message**: {spike.commit_message}\n"
        desc += f"- **Author**: {spike.author}\n\n"
        desc += "### Why This Matters\n"
        desc += "Sudden complexity increases in previously simple functions often indicate:\n"
        desc += "- Features added without proper refactoring\n"
        desc += "- AI-generated code that needs human review\n"
        desc += "- Technical debt accumulation\n"
        desc += "- Reduced testability and maintainability\n"

        return desc

    def _build_suggested_fix(self, spike: ComplexitySpike) -> str:
        """Build suggested fix for complexity spike."""
        suggestions = []
        suggestions.append(f"1. **Review commit {spike.commit_sha[:8]}** to understand what changes were made")
        suggestions.append(f"2. **Extract helper functions** - Break down the {spike.after_complexity}-complexity function into smaller units")
        suggestions.append("3. **Apply refactoring patterns**:")
        suggestions.append("   - Extract Method: Move logical blocks into separate functions")
        suggestions.append("   - Replace Conditional with Polymorphism if many if/else branches")
        suggestions.append("   - Decompose Conditional: Extract complex conditions into named functions")
        suggestions.append(f"4. **Target complexity**: Reduce to below 10 (currently {spike.after_complexity})")

        return "\n".join(suggestions)

    def _estimate_effort(self, spike: ComplexitySpike) -> str:
        """Estimate effort to fix complexity spike."""
        if spike.after_complexity < 20:
            return "Small (1-2 hours)"
        elif spike.after_complexity < 30:
            return "Medium (half day)"
        elif spike.after_complexity < 50:
            return "Large (1 day)"
        else:
            return "Extra Large (2+ days)"

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity for a finding."""
        return finding.severity

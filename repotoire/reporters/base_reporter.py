"""Base reporter interface for Repotoire report generators.

Phase 7 improvement: Provides common functionality for all reporters including
code snippet extraction and configuration handling.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional

from repotoire.models import CodebaseHealth, Finding
from repotoire.config import ReportingConfig
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class BaseReporter(ABC):
    """Abstract base class for report generators.

    Provides common functionality for all report formats including:
    - Code snippet extraction from source files
    - Configuration handling
    - Repository path management

    Subclasses must implement the generate() method.
    """

    def __init__(
        self,
        repo_path: Path | str | None = None,
        include_snippets: bool = True,
        config: ReportingConfig | None = None,
    ):
        """Initialize base reporter.

        Args:
            repo_path: Path to repository for extracting code snippets
            include_snippets: Whether to include code snippets in reports
            config: Optional reporting configuration for themes and branding
        """
        self.repo_path = Path(repo_path) if repo_path else None
        self.include_snippets = include_snippets
        self.config = config or ReportingConfig()

    @abstractmethod
    def generate(self, health: CodebaseHealth, output_path: Path) -> None:
        """Generate report from health data.

        Args:
            health: CodebaseHealth instance with analysis results
            output_path: Path to output file
        """
        pass

    def _extract_code_snippets(
        self,
        findings: List[Finding],
        max_files_per_finding: int = 3,
        context_lines: int = 5,
    ) -> Dict[str, Dict]:
        """Extract code snippets for all findings.

        Shared utility for extracting relevant code context from source files.

        Args:
            findings: List of findings to extract snippets for
            max_files_per_finding: Maximum number of files to extract per finding
            context_lines: Number of context lines around the target line

        Returns:
            Dictionary mapping finding IDs to their code snippets
        """
        if not self.include_snippets or not self.repo_path:
            return {}

        snippets = {}
        for finding in findings:
            finding_snippets = []
            if finding.affected_files:
                for file_path in finding.affected_files[:max_files_per_finding]:
                    snippet = self._extract_single_snippet(
                        file_path,
                        finding.line_start,
                        finding.line_end,
                        context_lines,
                    )
                    if snippet:
                        finding_snippets.append(snippet)
            if finding_snippets:
                snippets[finding.id] = finding_snippets

        return snippets

    def _extract_single_snippet(
        self,
        file_path: str,
        line_start: int | None,
        line_end: int | None,
        context_lines: int = 5,
    ) -> Optional[Dict]:
        """Extract code snippet from a single file.

        Args:
            file_path: Path to source file (relative to repo_path)
            line_start: Starting line number (1-indexed)
            line_end: Ending line number (1-indexed)
            context_lines: Number of context lines

        Returns:
            Dictionary with file path, line numbers, and code content
        """
        if not self.repo_path:
            return None

        try:
            full_path = self.repo_path / file_path
            if not full_path.exists():
                return None

            content = full_path.read_text()
            lines = content.splitlines()

            # Determine range
            if line_start is not None:
                start = max(1, line_start - context_lines)
                end = min(len(lines), (line_end or line_start) + context_lines)
            else:
                # No line info - return first N lines
                start = 1
                end = min(len(lines), self.config.max_snippet_lines)

            snippet_lines = lines[start - 1:end]

            return {
                "file_path": file_path,
                "start_line": start,
                "end_line": end,
                "highlight_start": line_start,
                "highlight_end": line_end,
                "content": "\n".join(snippet_lines),
                "language": self._detect_language(file_path),
            }

        except Exception as e:
            logger.debug(f"Failed to extract snippet from {file_path}: {e}")
            return None

    def _detect_language(self, file_path: str) -> str:
        """Detect programming language from file extension.

        Args:
            file_path: Path to file

        Returns:
            Language identifier for syntax highlighting
        """
        ext_to_lang = {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".java": "java",
            ".go": "go",
            ".rs": "rust",
            ".rb": "ruby",
            ".php": "php",
            ".c": "c",
            ".cpp": "cpp",
            ".h": "c",
            ".hpp": "cpp",
            ".cs": "csharp",
            ".swift": "swift",
            ".kt": "kotlin",
            ".scala": "scala",
            ".sql": "sql",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".json": "json",
            ".xml": "xml",
            ".html": "html",
            ".css": "css",
            ".scss": "scss",
            ".md": "markdown",
        }

        ext = Path(file_path).suffix.lower()
        return ext_to_lang.get(ext, "text")

    def _get_severity_color(self, severity) -> str:
        """Get color for severity level.

        Args:
            severity: Severity enum value

        Returns:
            CSS color string
        """
        from repotoire.models import Severity

        colors = {
            Severity.CRITICAL: "#dc3545",  # Red
            Severity.HIGH: "#fd7e14",      # Orange
            Severity.MEDIUM: "#ffc107",    # Yellow
            Severity.LOW: "#17a2b8",       # Teal
            Severity.INFO: "#6c757d",      # Gray
        }
        return colors.get(severity, "#6c757d")

    def _get_grade_color(self, grade: str) -> str:
        """Get color for health grade.

        Args:
            grade: Grade letter (A-F)

        Returns:
            CSS color string
        """
        colors = {
            "A": self.config.theme.grade_a_color,
            "B": self.config.theme.grade_b_color,
            "C": self.config.theme.grade_c_color,
            "D": self.config.theme.grade_d_color,
            "F": self.config.theme.grade_f_color,
        }
        return colors.get(grade, "#6c757d")

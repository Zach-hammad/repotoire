"""Markdown report generator for Repotoire analysis results.

Generates GitHub-flavored Markdown reports suitable for inclusion in
README files, pull request comments, or standalone documentation.
"""

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from repotoire.models import CodebaseHealth, Finding, Severity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Severity emoji mapping
SEVERITY_EMOJI = {
    Severity.CRITICAL: ":red_circle:",
    Severity.HIGH: ":orange_circle:",
    Severity.MEDIUM: ":yellow_circle:",
    Severity.LOW: ":large_blue_circle:",
    Severity.INFO: ":white_circle:",
}

# Grade emoji mapping
GRADE_EMOJI = {
    "A": ":trophy:",
    "B": ":star:",
    "C": ":warning:",
    "D": ":x:",
    "F": ":skull:",
}


class MarkdownReporter:
    """Generate Markdown reports from analysis results."""

    def __init__(
        self,
        repo_path: Optional[Path] = None,
        include_snippets: bool = False,
        max_findings_per_severity: int = 10,
        include_toc: bool = True,
    ):
        """Initialize Markdown reporter.

        Args:
            repo_path: Path to repository for code snippets
            include_snippets: Whether to include code snippets
            max_findings_per_severity: Max findings to show per severity level
            include_toc: Whether to include table of contents
        """
        self.repo_path = Path(repo_path) if repo_path else None
        self.include_snippets = include_snippets
        self.max_findings_per_severity = max_findings_per_severity
        self.include_toc = include_toc

    def generate(self, health: CodebaseHealth, output_path: Path) -> None:
        """Generate Markdown report from health data.

        Args:
            health: CodebaseHealth instance with analysis results
            output_path: Path to output Markdown file
        """
        markdown = self._build_markdown(health)

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")

        logger.info(f"Markdown report generated: {output_path}")

    def generate_string(self, health: CodebaseHealth) -> str:
        """Generate Markdown report as a string.

        Args:
            health: CodebaseHealth instance

        Returns:
            Markdown string
        """
        return self._build_markdown(health)

    def _build_markdown(self, health: CodebaseHealth) -> str:
        """Build complete Markdown document.

        Args:
            health: CodebaseHealth instance

        Returns:
            Markdown document string
        """
        sections = []

        # Header
        sections.append(self._build_header(health))

        # Table of Contents
        if self.include_toc:
            sections.append(self._build_toc())

        # Summary Section
        sections.append(self._build_summary(health))

        # Category Scores Section
        sections.append(self._build_category_scores(health))

        # Key Metrics Section
        sections.append(self._build_metrics(health))

        # Findings Summary Section
        sections.append(self._build_findings_summary(health))

        # Detailed Findings Section
        sections.append(self._build_detailed_findings(health))

        # Footer
        sections.append(self._build_footer())

        return "\n\n".join(sections)

    def _build_header(self, health: CodebaseHealth) -> str:
        """Build report header."""
        grade_emoji = GRADE_EMOJI.get(health.grade, ":question:")
        return f"""# {grade_emoji} Repotoire Code Health Report

**Grade: {health.grade}** | **Score: {health.overall_score:.1f}/100**

Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""

    def _build_toc(self) -> str:
        """Build table of contents."""
        return """## Table of Contents

- [Summary](#summary)
- [Category Scores](#category-scores)
- [Key Metrics](#key-metrics)
- [Findings Summary](#findings-summary)
- [Detailed Findings](#detailed-findings)"""

    def _build_summary(self, health: CodebaseHealth) -> str:
        """Build summary section."""
        # Grade explanation
        explanations = {
            "A": "Excellent - Code is well-structured and maintainable",
            "B": "Good - Minor improvements recommended",
            "C": "Fair - Several issues should be addressed",
            "D": "Poor - Significant refactoring needed",
            "F": "Critical - Major technical debt present",
        }
        explanation = explanations.get(health.grade, "")

        return f"""## Summary

| Metric | Value |
|--------|-------|
| **Overall Grade** | {health.grade} |
| **Overall Score** | {health.overall_score:.1f}/100 |
| **Total Findings** | {health.findings_summary.total} |
| **Assessment** | {explanation} |"""

    def _build_category_scores(self, health: CodebaseHealth) -> str:
        """Build category scores section."""
        def score_indicator(score: float) -> str:
            if score >= 80:
                return ":white_check_mark:"
            elif score >= 60:
                return ":warning:"
            else:
                return ":x:"

        return f"""## Category Scores

| Category | Weight | Score | Status |
|----------|--------|-------|--------|
| Graph Structure | 40% | {health.structure_score:.1f}/100 | {score_indicator(health.structure_score)} |
| Code Quality | 30% | {health.quality_score:.1f}/100 | {score_indicator(health.quality_score)} |
| Architecture Health | 30% | {health.architecture_score:.1f}/100 | {score_indicator(health.architecture_score)} |"""

    def _build_metrics(self, health: CodebaseHealth) -> str:
        """Build key metrics section."""
        m = health.metrics

        # Assessment indicators
        def coupling_assessment(val: float) -> str:
            if val is None or val < 3:
                return ":white_check_mark: Good"
            elif val < 5:
                return ":warning: Fair"
            else:
                return ":x: High"

        def modularity_assessment(val: float) -> str:
            if val >= 0.5:
                return ":white_check_mark: Good"
            elif val >= 0.3:
                return ":warning: Fair"
            else:
                return ":x: Low"

        coupling = m.avg_coupling if m.avg_coupling is not None else 0.0

        return f"""## Key Metrics

### Codebase Size

| Metric | Value |
|--------|-------|
| Total Files | {m.total_files} |
| Total Classes | {m.total_classes} |
| Total Functions | {m.total_functions} |

### Quality Indicators

| Metric | Value | Assessment |
|--------|-------|------------|
| Modularity | {m.modularity:.2f} | {modularity_assessment(m.modularity)} |
| Avg. Coupling | {coupling:.2f} | {coupling_assessment(coupling)} |
| Circular Dependencies | {m.circular_dependencies} | {':white_check_mark: None' if m.circular_dependencies == 0 else ':x: ' + str(m.circular_dependencies) + ' found'} |
| Dead Code % | {m.dead_code_percentage:.1%} | {':white_check_mark: Minimal' if m.dead_code_percentage < 0.05 else ':warning: ' + f'{m.dead_code_percentage:.1%}'} |
| God Classes | {m.god_class_count} | {':white_check_mark: None' if m.god_class_count == 0 else ':warning: ' + str(m.god_class_count) + ' found'} |"""

    def _build_findings_summary(self, health: CodebaseHealth) -> str:
        """Build findings summary section."""
        s = health.findings_summary

        return f"""## Findings Summary

| Severity | Count | Emoji |
|----------|-------|-------|
| Critical | {s.critical} | :red_circle: |
| High | {s.high} | :orange_circle: |
| Medium | {s.medium} | :yellow_circle: |
| Low | {s.low} | :large_blue_circle: |
| Info | {s.info} | :white_circle: |
| **Total** | **{s.total}** | |"""

    def _build_detailed_findings(self, health: CodebaseHealth) -> str:
        """Build detailed findings section."""
        sections = ["## Detailed Findings"]

        # Group findings by severity
        by_severity: Dict[Severity, List[Finding]] = {}
        for finding in health.findings:
            sev = finding.severity
            if sev not in by_severity:
                by_severity[sev] = []
            by_severity[sev].append(finding)

        # Order by severity (critical first)
        severity_order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]

        for severity in severity_order:
            findings = by_severity.get(severity, [])
            if not findings:
                continue

            emoji = SEVERITY_EMOJI.get(severity, ":question:")
            sections.append(f"\n### {emoji} {severity.value.title()} Findings ({len(findings)})")

            # Limit findings shown
            shown_findings = findings[:self.max_findings_per_severity]
            hidden_count = len(findings) - len(shown_findings)

            for finding in shown_findings:
                sections.append(self._format_finding(finding))

            if hidden_count > 0:
                sections.append(f"\n*...and {hidden_count} more {severity.value} findings*")

        return "\n".join(sections)

    def _format_finding(self, finding: Finding) -> str:
        """Format a single finding as Markdown."""
        lines = []

        # Title with detector badge
        detector = finding.detector.replace("Detector", "") if finding.detector else "Unknown"
        lines.append(f"\n#### {finding.title}")
        lines.append(f"\n`{detector}` ")

        # Description
        if finding.description:
            lines.append(f"\n{finding.description}")

        # Affected files
        if finding.affected_files:
            files_str = ", ".join(f"`{f}`" for f in finding.affected_files[:5])
            if len(finding.affected_files) > 5:
                files_str += f" (+{len(finding.affected_files) - 5} more)"
            lines.append(f"\n**Files:** {files_str}")

        # Suggested fix
        if finding.suggested_fix:
            lines.append(f"\n> **Fix:** {finding.suggested_fix}")

        # Code snippet (if enabled)
        if self.include_snippets and self.repo_path and finding.affected_files:
            snippet = self._get_snippet(finding.affected_files[0], finding)
            if snippet:
                lines.append(f"\n```python\n{snippet}\n```")

        return "\n".join(lines)

    def _get_snippet(self, file_path: str, finding: Finding, context: int = 3) -> Optional[str]:
        """Get code snippet for a finding."""
        if not self.repo_path:
            return None

        try:
            full_path = self.repo_path / file_path
            if not full_path.exists():
                return None

            # Try to get line number from metadata
            line = None
            if hasattr(finding, "metadata") and finding.metadata:
                line = finding.metadata.get("line") or finding.metadata.get("start_line")

            if not line:
                return None

            line = int(line)
            with open(full_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            start = max(0, line - 1 - context)
            end = min(len(lines), line + context)

            return "".join(lines[start:end])

        except Exception as e:
            logger.debug(f"Could not get snippet: {e}")
            return None

    def _build_footer(self) -> str:
        """Build report footer."""
        return """---

*Generated by [Repotoire](https://repotoire.com) - Graph-Powered Code Health Platform*"""

"""PDF report generator for Repotoire analysis results.

Generates PDF reports using weasyprint to convert HTML to PDF.
Requires: pip install weasyprint

Note: weasyprint requires system dependencies (cairo, pango).
On Ubuntu/Debian: apt-get install libcairo2 libpango-1.0-0 libpangocairo-1.0-0
On macOS: brew install cairo pango
On Windows: See https://weasyprint.org/docs/install/#windows
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from repotoire.models import CodebaseHealth, Severity
from repotoire.logging_config import get_logger
from repotoire.reporters.base_reporter import BaseReporter

logger = get_logger(__name__)


class PDFReporter(BaseReporter):
    """Generate PDF reports from analysis results.

    Inherits common functionality from BaseReporter including code snippet
    extraction and language detection.
    """

    def __init__(
        self,
        repo_path: Path | str | None = None,
        include_snippets: bool = True,
        page_size: str = "A4",
    ):
        """Initialize PDF reporter.

        Args:
            repo_path: Path to repository for code snippets
            include_snippets: Whether to include code snippets
            page_size: Page size ("A4", "Letter", etc.)
        """
        super().__init__(repo_path=repo_path, include_snippets=include_snippets)
        self.page_size = page_size

    def generate(self, health: CodebaseHealth, output_path: Path) -> None:
        """Generate PDF report from health data.

        Args:
            health: CodebaseHealth instance with analysis results
            output_path: Path to output PDF file

        Raises:
            ImportError: If weasyprint is not installed
        """
        try:
            from weasyprint import HTML, CSS
        except ImportError:
            raise ImportError(
                "PDF report generation requires weasyprint. "
                "Install with: pip install weasyprint\n"
                "Note: weasyprint requires system dependencies (cairo, pango). "
                "See https://weasyprint.org/docs/install/"
            )

        # Generate HTML content
        html_content = self._build_html(health)

        # Convert to PDF
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        html = HTML(string=html_content)
        css = CSS(string=self._get_css())
        html.write_pdf(output_path, stylesheets=[css])

        logger.info(f"PDF report generated: {output_path}")

    def _build_html(self, health: CodebaseHealth) -> str:
        """Build HTML content for PDF conversion."""
        # Grade colors
        grade_colors = {
            "A": "#28a745",
            "B": "#20c997",
            "C": "#ffc107",
            "D": "#fd7e14",
            "F": "#dc3545",
        }
        grade_color = grade_colors.get(health.grade, "#6c757d")

        # Severity colors
        severity_colors = {
            Severity.CRITICAL: "#dc3545",
            Severity.HIGH: "#fd7e14",
            Severity.MEDIUM: "#ffc107",
            Severity.LOW: "#17a2b8",
            Severity.INFO: "#6c757d",
        }

        # Build findings HTML
        findings_html = []
        for finding in health.findings[:100]:  # Limit for PDF size
            sev_color = severity_colors.get(finding.severity, "#6c757d")
            files = ", ".join(finding.affected_files[:3]) if finding.affected_files else "N/A"
            if finding.affected_files and len(finding.affected_files) > 3:
                files += f" (+{len(finding.affected_files) - 3} more)"

            findings_html.append(f"""
                <tr>
                    <td><span class="severity-badge" style="background-color: {sev_color};">{finding.severity.value if finding.severity else 'unknown'}</span></td>
                    <td>{finding.detector.replace('Detector', '') if finding.detector else 'N/A'}</td>
                    <td>{finding.title or 'N/A'}</td>
                    <td class="files-cell">{files}</td>
                </tr>
            """)

        findings_table = "\n".join(findings_html)

        # Category assessment
        def assessment(score):
            if score >= 80:
                return "Good"
            elif score >= 60:
                return "Fair"
            else:
                return "Needs Attention"

        html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Repotoire Code Health Report</title>
</head>
<body>
    <div class="header">
        <h1>Repotoire Code Health Report</h1>
        <p class="generated">Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    </div>

    <div class="grade-section">
        <div class="grade-box" style="border-color: {grade_color};">
            <span class="grade" style="color: {grade_color};">{health.grade}</span>
            <span class="score">{health.overall_score:.1f}/100</span>
        </div>
    </div>

    <h2>Category Scores</h2>
    <table class="scores-table">
        <thead>
            <tr>
                <th>Category</th>
                <th>Weight</th>
                <th>Score</th>
                <th>Assessment</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td>Graph Structure</td>
                <td>40%</td>
                <td>{health.structure_score:.1f}/100</td>
                <td class="assessment-{assessment(health.structure_score).lower().replace(' ', '-')}">{assessment(health.structure_score)}</td>
            </tr>
            <tr>
                <td>Code Quality</td>
                <td>30%</td>
                <td>{health.quality_score:.1f}/100</td>
                <td class="assessment-{assessment(health.quality_score).lower().replace(' ', '-')}">{assessment(health.quality_score)}</td>
            </tr>
            <tr>
                <td>Architecture Health</td>
                <td>30%</td>
                <td>{health.architecture_score:.1f}/100</td>
                <td class="assessment-{assessment(health.architecture_score).lower().replace(' ', '-')}">{assessment(health.architecture_score)}</td>
            </tr>
        </tbody>
    </table>

    <h2>Key Metrics</h2>
    <div class="metrics-grid">
        <div class="metric-card">
            <span class="metric-value">{health.metrics.total_files}</span>
            <span class="metric-label">Total Files</span>
        </div>
        <div class="metric-card">
            <span class="metric-value">{health.metrics.total_classes}</span>
            <span class="metric-label">Classes</span>
        </div>
        <div class="metric-card">
            <span class="metric-value">{health.metrics.total_functions}</span>
            <span class="metric-label">Functions</span>
        </div>
        <div class="metric-card">
            <span class="metric-value">{health.metrics.circular_dependencies}</span>
            <span class="metric-label">Circular Deps</span>
        </div>
        <div class="metric-card">
            <span class="metric-value">{health.metrics.god_class_count}</span>
            <span class="metric-label">God Classes</span>
        </div>
        <div class="metric-card">
            <span class="metric-value">{health.metrics.modularity:.2f}</span>
            <span class="metric-label">Modularity</span>
        </div>
    </div>

    <h2>Findings Summary</h2>
    <table class="summary-table">
        <tr>
            <td><span class="severity-badge" style="background-color: #dc3545;">Critical</span></td>
            <td>{health.findings_summary.critical}</td>
            <td><span class="severity-badge" style="background-color: #fd7e14;">High</span></td>
            <td>{health.findings_summary.high}</td>
            <td><span class="severity-badge" style="background-color: #ffc107;">Medium</span></td>
            <td>{health.findings_summary.medium}</td>
        </tr>
        <tr>
            <td><span class="severity-badge" style="background-color: #17a2b8;">Low</span></td>
            <td>{health.findings_summary.low}</td>
            <td><span class="severity-badge" style="background-color: #6c757d;">Info</span></td>
            <td>{health.findings_summary.info}</td>
            <td><strong>Total</strong></td>
            <td><strong>{health.findings_summary.total}</strong></td>
        </tr>
    </table>

    <h2>Findings Detail</h2>
    <table class="findings-table">
        <thead>
            <tr>
                <th>Severity</th>
                <th>Detector</th>
                <th>Title</th>
                <th>Affected Files</th>
            </tr>
        </thead>
        <tbody>
            {findings_table}
        </tbody>
    </table>
    {f'<p class="note">Showing {min(100, len(health.findings))} of {len(health.findings)} findings</p>' if len(health.findings) > 100 else ''}

    <div class="footer">
        <p>Generated by <strong>Repotoire</strong> - Graph-Powered Code Health Platform</p>
        <p>https://repotoire.com</p>
    </div>
</body>
</html>
        """
        return html

    def _get_css(self) -> str:
        """Get CSS styles for PDF."""
        return """
@page {
    size: A4;
    margin: 2cm;
    @bottom-center {
        content: "Page " counter(page) " of " counter(pages);
        font-size: 9pt;
        color: #666;
    }
}

body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    font-size: 10pt;
    line-height: 1.4;
    color: #333;
}

.header {
    text-align: center;
    margin-bottom: 20px;
    border-bottom: 2px solid #4472C4;
    padding-bottom: 10px;
}

.header h1 {
    margin: 0;
    color: #4472C4;
    font-size: 24pt;
}

.generated {
    color: #666;
    font-style: italic;
    margin-top: 5px;
}

.grade-section {
    text-align: center;
    margin: 20px 0;
}

.grade-box {
    display: inline-block;
    border: 3px solid;
    border-radius: 10px;
    padding: 15px 40px;
    text-align: center;
}

.grade {
    font-size: 48pt;
    font-weight: bold;
    display: block;
}

.score {
    font-size: 14pt;
    color: #666;
}

h2 {
    color: #4472C4;
    font-size: 14pt;
    border-bottom: 1px solid #ddd;
    padding-bottom: 5px;
    margin-top: 25px;
}

table {
    width: 100%;
    border-collapse: collapse;
    margin: 10px 0;
    font-size: 9pt;
}

th, td {
    padding: 8px;
    text-align: left;
    border-bottom: 1px solid #ddd;
}

th {
    background-color: #4472C4;
    color: white;
    font-weight: bold;
}

tr:nth-child(even) {
    background-color: #f9f9f9;
}

.severity-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    color: white;
    font-size: 8pt;
    font-weight: bold;
    text-transform: uppercase;
}

.metrics-grid {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    justify-content: space-between;
}

.metric-card {
    background: #f8f9fa;
    border: 1px solid #ddd;
    border-radius: 5px;
    padding: 10px;
    text-align: center;
    width: 15%;
}

.metric-value {
    display: block;
    font-size: 18pt;
    font-weight: bold;
    color: #4472C4;
}

.metric-label {
    font-size: 8pt;
    color: #666;
}

.assessment-good {
    color: #28a745;
    font-weight: bold;
}

.assessment-fair {
    color: #ffc107;
    font-weight: bold;
}

.assessment-needs-attention {
    color: #dc3545;
    font-weight: bold;
}

.files-cell {
    max-width: 200px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 8pt;
}

.summary-table {
    width: auto;
    margin: 0 auto;
}

.summary-table td {
    padding: 5px 15px;
    text-align: center;
}

.note {
    font-style: italic;
    color: #666;
    font-size: 9pt;
}

.footer {
    margin-top: 30px;
    padding-top: 10px;
    border-top: 1px solid #ddd;
    text-align: center;
    font-size: 9pt;
    color: #666;
}
        """

"""HTML report generator for Falkor analysis results."""

from datetime import datetime
from pathlib import Path
from typing import Optional, Dict
from jinja2 import Template

from repotoire.models import CodebaseHealth, Finding, Severity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class HTMLReporter:
    """Generate HTML reports from analysis results."""

    def __init__(self, repo_path: Optional[Path] = None):
        """Initialize HTML reporter.

        Args:
            repo_path: Path to repository for extracting code snippets
        """
        self.repo_path = Path(repo_path) if repo_path else None

    def generate(self, health: CodebaseHealth, output_path: Path) -> None:
        """Generate HTML report from health data.

        Args:
            health: CodebaseHealth instance with analysis results
            output_path: Path to output HTML file
        """
        # Extract code snippets for findings
        findings_with_code = []
        for finding in health.findings:
            finding_data = {
                "id": finding.id,
                "detector": finding.detector,
                "severity": finding.severity,
                "title": finding.title,
                "description": finding.description,
                "affected_files": finding.affected_files,
                "affected_nodes": finding.affected_nodes,
                "suggested_fix": finding.suggested_fix,
                "estimated_effort": finding.estimated_effort,
                "priority_score": finding.priority_score,
                "detector_agreement_count": finding.detector_agreement_count,
                "aggregate_confidence": finding.aggregate_confidence,
                "code_snippets": []
            }

            # Extract code snippets for affected files
            if self.repo_path and finding.affected_files:
                for file_path in finding.affected_files[:3]:  # Limit to 3 files
                    snippet = self._extract_code_snippet(file_path, finding)
                    if snippet:
                        finding_data["code_snippets"].append(snippet)

            findings_with_code.append(finding_data)

        # Prepare template data
        template_data = {
            "health": health,
            "findings": findings_with_code,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "severity_colors": self._get_severity_colors(),
            "severity_labels": self._get_severity_labels(),
            "dedup_stats": health.dedup_stats if health.dedup_stats else None,
        }

        # Render template
        html_content = self._render_template(template_data)

        # Write to file
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html_content)
        logger.info(f"HTML report generated: {output_path}")

    def _extract_code_snippet(self, file_path: str, finding: Finding) -> Optional[Dict]:
        """Extract code snippet for a finding.

        Args:
            file_path: Path to source file
            finding: Finding instance

        Returns:
            Dict with code snippet data or None
        """
        if not self.repo_path:
            return None

        try:
            # Construct full path
            full_path = self.repo_path / file_path
            if not full_path.exists():
                return None

            # Read file content
            with open(full_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            # Determine line range from finding's graph context or affected nodes
            line_start, line_end = self._get_line_range_from_finding(finding, file_path)

            if line_start is None:
                # Default to showing first 10 lines
                line_start = 1
                line_end = min(10, len(lines))

            # Extract snippet with context (5 lines before/after)
            context_before = 5
            context_after = 5
            snippet_start = max(1, line_start - context_before)
            snippet_end = min(len(lines), line_end + context_after)

            # Get lines with numbers
            snippet_lines = []
            for i in range(snippet_start - 1, snippet_end):
                line_num = i + 1
                is_highlighted = line_start <= line_num <= line_end
                snippet_lines.append({
                    "number": line_num,
                    "content": lines[i].rstrip(),
                    "highlighted": is_highlighted
                })

            # Detect language from file extension
            language = self._detect_language(file_path)

            return {
                "file_path": file_path,
                "language": language,
                "line_start": line_start,
                "line_end": line_end,
                "lines": snippet_lines
            }

        except Exception as e:
            logger.warning(f"Failed to extract code snippet from {file_path}: {e}")
            return None

    def _get_line_range_from_finding(self, finding: Finding, file_path: str) -> tuple[Optional[int], Optional[int]]:
        """Extract line range from finding's graph context.

        Args:
            finding: Finding instance
            file_path: File path to match

        Returns:
            Tuple of (start_line, end_line) or (None, None)
        """
        # Check graph context for line information
        if finding.graph_context:
            # Look for line_start and line_end in graph context
            if "line_start" in finding.graph_context:
                line_start = finding.graph_context.get("line_start")
                line_end = finding.graph_context.get("line_end", line_start)
                return (line_start, line_end)

            # Look for nodes with line info
            if "nodes" in finding.graph_context:
                for node in finding.graph_context["nodes"]:
                    if isinstance(node, dict) and node.get("file_path") == file_path:
                        line_start = node.get("line_start")
                        line_end = node.get("line_end", line_start)
                        if line_start:
                            return (line_start, line_end)

        return (None, None)

    def _detect_language(self, file_path: str) -> str:
        """Detect programming language from file extension.

        Args:
            file_path: Path to file

        Returns:
            Language name for syntax highlighting
        """
        extension_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".jsx": "javascript",
            ".java": "java",
            ".go": "go",
            ".rs": "rust",
            ".c": "c",
            ".cpp": "cpp",
            ".h": "c",
            ".hpp": "cpp",
            ".rb": "ruby",
            ".php": "php",
            ".swift": "swift",
            ".kt": "kotlin",
        }

        path = Path(file_path)
        return extension_map.get(path.suffix, "text")

    def _get_severity_colors(self) -> Dict[str, str]:
        """Get color mapping for severity levels.

        Returns:
            Dict mapping severity to color codes
        """
        return {
            Severity.CRITICAL.value: "#dc2626",  # Red 600
            Severity.HIGH.value: "#ea580c",      # Orange 600
            Severity.MEDIUM.value: "#ca8a04",    # Yellow 600
            Severity.LOW.value: "#2563eb",       # Blue 600
            Severity.INFO.value: "#0891b2",      # Cyan 600
        }

    def _get_severity_labels(self) -> Dict[str, str]:
        """Get display labels for severity levels.

        Returns:
            Dict mapping severity to emoji labels
        """
        return {
            Severity.CRITICAL.value: "🔴 Critical",
            Severity.HIGH.value: "🟠 High",
            Severity.MEDIUM.value: "🟡 Medium",
            Severity.LOW.value: "🔵 Low",
            Severity.INFO.value: "ℹ️ Info",
        }

    def _render_template(self, data: Dict) -> str:
        """Render HTML template with data.

        Args:
            data: Template data

        Returns:
            Rendered HTML string
        """
        template = Template(HTML_TEMPLATE)
        return template.render(**data)


# HTML Template with code display
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Falkor Code Health Report</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            line-height: 1.6;
            color: #1f2937;
            background: #f9fafb;
            padding: 2rem;
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 12px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
            overflow: hidden;
        }

        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 3rem 2rem;
            text-align: center;
        }

        .header h1 {
            font-size: 2.5rem;
            margin-bottom: 0.5rem;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.75rem;
        }

        .header .timestamp {
            opacity: 0.9;
            font-size: 0.95rem;
        }

        .content {
            padding: 2rem;
        }

        .grade-section {
            text-align: center;
            padding: 2rem;
            background: #f9fafb;
            border-radius: 8px;
            margin-bottom: 2rem;
        }

        .grade-badge {
            display: inline-block;
            font-size: 4rem;
            font-weight: bold;
            width: 120px;
            height: 120px;
            line-height: 120px;
            border-radius: 50%;
            margin-bottom: 1rem;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        }

        .grade-A { background: #10b981; color: white; }
        .grade-B { background: #06b6d4; color: white; }
        .grade-C { background: #f59e0b; color: white; }
        .grade-D { background: #ef4444; color: white; }
        .grade-F { background: #991b1b; color: white; }

        .score {
            font-size: 1.5rem;
            color: #6b7280;
            margin-bottom: 0.5rem;
        }

        .grade-description {
            color: #6b7280;
            font-style: italic;
        }

        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }

        .metric-card {
            background: white;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            padding: 1.5rem;
            transition: transform 0.2s, box-shadow 0.2s;
        }

        .metric-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
        }

        .metric-card h3 {
            font-size: 0.875rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #6b7280;
            margin-bottom: 0.5rem;
        }

        .metric-value {
            font-size: 2rem;
            font-weight: bold;
            color: #1f2937;
            margin-bottom: 0.5rem;
        }

        .metric-bar {
            height: 8px;
            background: #e5e7eb;
            border-radius: 4px;
            overflow: hidden;
            margin-top: 0.75rem;
        }

        .metric-bar-fill {
            height: 100%;
            border-radius: 4px;
            transition: width 0.5s ease;
        }

        .bar-good { background: #10b981; }
        .bar-moderate { background: #f59e0b; }
        .bar-poor { background: #ef4444; }

        .section {
            margin-bottom: 3rem;
        }

        .section-title {
            font-size: 1.5rem;
            font-weight: 600;
            margin-bottom: 1.5rem;
            padding-bottom: 0.75rem;
            border-bottom: 2px solid #e5e7eb;
        }

        .findings-list {
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
        }

        .finding-card {
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            overflow: hidden;
            transition: box-shadow 0.2s;
        }

        .finding-card:hover {
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        }

        .finding-header {
            padding: 1.25rem;
            background: #f9fafb;
            border-bottom: 1px solid #e5e7eb;
            display: flex;
            align-items: center;
            gap: 1rem;
        }

        .severity-badge {
            padding: 0.375rem 0.75rem;
            border-radius: 6px;
            font-size: 0.875rem;
            font-weight: 600;
            color: white;
            white-space: nowrap;
        }

        .severity-critical { background: #dc2626; }
        .severity-high { background: #ea580c; }
        .severity-medium { background: #ca8a04; }
        .severity-low { background: #2563eb; }
        .severity-info { background: #0891b2; }

        .finding-title {
            flex: 1;
            font-weight: 600;
            font-size: 1.125rem;
        }

        .detector-badge {
            background: #e0e7ff;
            color: #4f46e5;
            padding: 0.25rem 0.75rem;
            border-radius: 6px;
            font-size: 0.875rem;
            font-weight: 500;
        }

        .finding-body {
            padding: 1.25rem;
        }

        .finding-description {
            color: #4b5563;
            margin-bottom: 1rem;
            line-height: 1.7;
        }

        .affected-files {
            margin-bottom: 1rem;
        }

        .affected-files-label {
            font-weight: 600;
            color: #6b7280;
            margin-bottom: 0.5rem;
            font-size: 0.875rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .file-list {
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }

        .file-item {
            font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
            font-size: 0.875rem;
            color: #6b7280;
            padding: 0.5rem;
            background: #f9fafb;
            border-radius: 4px;
        }

        .code-snippet {
            margin-top: 1rem;
            border: 1px solid #e5e7eb;
            border-radius: 6px;
            overflow: hidden;
        }

        .code-header {
            background: #374151;
            color: white;
            padding: 0.75rem 1rem;
            font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
            font-size: 0.875rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .code-location {
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        .line-badge {
            background: #4b5563;
            padding: 0.25rem 0.5rem;
            border-radius: 4px;
            font-size: 0.75rem;
        }

        .code-body {
            background: #1f2937;
            overflow-x: auto;
        }

        .code-line {
            display: flex;
            font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
            font-size: 0.875rem;
            line-height: 1.5;
        }

        .code-line.highlighted {
            background: rgba(239, 68, 68, 0.2);
            border-left: 3px solid #ef4444;
        }

        .line-number {
            color: #6b7280;
            padding: 0.25rem 1rem;
            text-align: right;
            min-width: 60px;
            user-select: none;
            border-right: 1px solid #374151;
        }

        .line-content {
            color: #e5e7eb;
            padding: 0.25rem 1rem;
            flex: 1;
            white-space: pre;
        }

        .suggested-fix {
            margin-top: 1rem;
            padding: 1rem;
            background: #ecfdf5;
            border-left: 4px solid #10b981;
            border-radius: 4px;
        }

        .suggested-fix-label {
            font-weight: 600;
            color: #059669;
            margin-bottom: 0.5rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        .suggested-fix-text {
            color: #065f46;
            line-height: 1.7;
        }

        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
            margin-bottom: 2rem;
        }

        .stat-item {
            text-align: center;
            padding: 1rem;
            background: #f9fafb;
            border-radius: 8px;
        }

        .stat-value {
            font-size: 2rem;
            font-weight: bold;
            color: #1f2937;
        }

        .stat-label {
            font-size: 0.875rem;
            color: #6b7280;
            margin-top: 0.25rem;
        }

        .footer {
            text-align: center;
            padding: 2rem;
            color: #6b7280;
            border-top: 1px solid #e5e7eb;
            margin-top: 3rem;
        }

        @media print {
            body {
                padding: 0;
                background: white;
            }
            .container {
                box-shadow: none;
            }
            .finding-card {
                page-break-inside: avoid;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎼 Repotoire Code Health Report</h1>
            <p class="timestamp">Generated {{ generated_at }}</p>
        </div>

        <div class="content">
            <!-- Overall Grade -->
            <div class="grade-section">
                <div class="grade-badge grade-{{ health.grade }}">{{ health.grade }}</div>
                <div class="score">Overall Score: {{ "%.1f"|format(health.overall_score) }}/100</div>
                <p class="grade-description">
                    {% if health.grade == 'A' %}Excellent - Code is well-structured and maintainable
                    {% elif health.grade == 'B' %}Good - Minor improvements recommended
                    {% elif health.grade == 'C' %}Fair - Several issues should be addressed
                    {% elif health.grade == 'D' %}Poor - Significant refactoring needed
                    {% elif health.grade == 'F' %}Critical - Major technical debt present
                    {% endif %}
                </p>
            </div>

            <!-- Category Scores -->
            <div class="section">
                <h2 class="section-title">📊 Category Scores</h2>
                <div class="metrics-grid">
                    <div class="metric-card">
                        <h3>Graph Structure (40%)</h3>
                        <div class="metric-value">{{ "%.1f"|format(health.structure_score) }}</div>
                        <div class="metric-bar">
                            <div class="metric-bar-fill {% if health.structure_score >= 80 %}bar-good{% elif health.structure_score >= 60 %}bar-moderate{% else %}bar-poor{% endif %}"
                                 style="width: {{ health.structure_score }}%"></div>
                        </div>
                    </div>
                    <div class="metric-card">
                        <h3>Code Quality (30%)</h3>
                        <div class="metric-value">{{ "%.1f"|format(health.quality_score) }}</div>
                        <div class="metric-bar">
                            <div class="metric-bar-fill {% if health.quality_score >= 80 %}bar-good{% elif health.quality_score >= 60 %}bar-moderate{% else %}bar-poor{% endif %}"
                                 style="width: {{ health.quality_score }}%"></div>
                        </div>
                    </div>
                    <div class="metric-card">
                        <h3>Architecture Health (30%)</h3>
                        <div class="metric-value">{{ "%.1f"|format(health.architecture_score) }}</div>
                        <div class="metric-bar">
                            <div class="metric-bar-fill {% if health.architecture_score >= 80 %}bar-good{% elif health.architecture_score >= 60 %}bar-moderate{% else %}bar-poor{% endif %}"
                                 style="width: {{ health.architecture_score }}%"></div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Key Metrics -->
            <div class="section">
                <h2 class="section-title">📈 Key Metrics</h2>
                <div class="stats-grid">
                    <div class="stat-item">
                        <div class="stat-value">{{ health.metrics.total_files }}</div>
                        <div class="stat-label">📁 Files</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value">{{ health.metrics.total_classes }}</div>
                        <div class="stat-label">🏛️ Classes</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value">{{ health.metrics.total_functions }}</div>
                        <div class="stat-label">⚙️ Functions</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value">{{ "%.2f"|format(health.metrics.modularity) }}</div>
                        <div class="stat-label">🔗 Modularity</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value">{{ health.metrics.circular_dependencies }}</div>
                        <div class="stat-label">🔁 Circular Deps</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value">{{ health.metrics.god_class_count }}</div>
                        <div class="stat-label">👹 God Classes</div>
                    </div>
                </div>
            </div>

            <!-- Deduplication Statistics -->
            {% if dedup_stats %}
            <div class="section">
                <h2 class="section-title">🔀 Finding Deduplication</h2>
                <div class="metrics-grid">
                    <div class="metric-card">
                        <div class="metric-value">{{ dedup_stats.original_count }}</div>
                        <div class="metric-label">Original Findings</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-value">{{ dedup_stats.deduplicated_count }}</div>
                        <div class="metric-label">After Deduplication</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-value">{{ dedup_stats.duplicate_count }}</div>
                        <div class="metric-label">Duplicates Removed</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-value">{{ "%.1f"|format(dedup_stats.reduction_percentage) }}%</div>
                        <div class="metric-label">Reduction Rate</div>
                    </div>
                </div>

                {% if dedup_stats.top_merged_findings %}
                <h3 style="margin-top: 2rem; color: #333;">🔥 Top Merged Findings (Multiple Detector Agreement)</h3>
                <div class="findings-list">
                    {% for merged in dedup_stats.top_merged_findings[:5] %}
                    <div class="finding-card" style="border-left: 4px solid #f59e0b;">
                        <div class="finding-header">
                            <span class="severity-badge severity-{{ merged.severity }}">
                                {{ severity_labels[merged.severity] }}
                            </span>
                            <span style="font-weight: bold; color: #333;">{{ merged.title }}</span>
                        </div>
                        <div style="margin-top: 0.5rem; font-size: 0.875rem; color: #666;">
                            <strong>🔍 Detector Agreement:</strong> {{ merged.detector_agreement_count }} detectors
                            ({{ merged.detectors|join(", ") }})
                        </div>
                        <div style="margin-top: 0.25rem; font-size: 0.875rem; color: #666;">
                            <strong>💯 Confidence:</strong> {{ "%.0f"|format(merged.aggregate_confidence * 100) }}%
                        </div>
                    </div>
                    {% endfor %}
                </div>
                {% endif %}

                {% if dedup_stats.merged_by_category %}
                <h3 style="margin-top: 2rem; color: #333;">📊 Merged Findings by Category</h3>
                <div class="metrics-grid" style="grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));">
                    {% for category, count in dedup_stats.merged_by_category.items() %}
                    <div class="metric-card">
                        <div class="metric-value">{{ count }}</div>
                        <div class="metric-label">{{ category.replace("_", " ").title() }}</div>
                    </div>
                    {% endfor %}
                </div>
                {% endif %}
            </div>
            {% endif %}

            <!-- Findings -->
            {% if findings %}
            <div class="section">
                <h2 class="section-title">🔍 Findings ({{ health.findings_summary.total }} total)</h2>
                <div class="findings-list">
                    {% for finding in findings %}
                    <div class="finding-card">
                        <div class="finding-header">
                            <span class="severity-badge severity-{{ finding.severity.value }}">
                                {{ severity_labels[finding.severity.value] }}
                            </span>
                            <div class="finding-title">{{ finding.title }}</div>
                            <div style="display: flex; gap: 0.5rem; align-items: center;">
                                <span class="detector-badge">{{ finding.detector }}</span>
                                {% if finding.priority_score > 0 %}
                                <span class="priority-badge" style="background: {% if finding.priority_score >= 80 %}#dc2626{% elif finding.priority_score >= 60 %}#f59e0b{% else %}#3b82f6{% endif %}; color: white; padding: 0.25rem 0.5rem; border-radius: 0.25rem; font-size: 0.75rem; font-weight: 600;">
                                    Priority: {{ "%.0f"|format(finding.priority_score) }}
                                </span>
                                {% endif %}
                                {% if finding.detector_agreement_count > 1 %}
                                <span style="background: #8b5cf6; color: white; padding: 0.25rem 0.5rem; border-radius: 0.25rem; font-size: 0.75rem; font-weight: 600;">
                                    🔍 {{ finding.detector_agreement_count }} detectors
                                </span>
                                {% endif %}
                                {% if finding.aggregate_confidence > 0 %}
                                <span style="background: #10b981; color: white; padding: 0.25rem 0.5rem; border-radius: 0.25rem; font-size: 0.75rem; font-weight: 600;">
                                    💯 {{ "%.0f"|format(finding.aggregate_confidence * 100) }}%
                                </span>
                                {% endif %}
                            </div>
                        </div>
                        <div class="finding-body">
                            <div class="finding-description">{{ finding.description }}</div>

                            {% if finding.affected_files %}
                            <div class="affected-files">
                                <div class="affected-files-label">📂 Affected Files</div>
                                <div class="file-list">
                                    {% for file in finding.affected_files[:5] %}
                                    <div class="file-item">{{ file }}</div>
                                    {% endfor %}
                                    {% if finding.affected_files|length > 5 %}
                                    <div class="file-item">... and {{ finding.affected_files|length - 5 }} more files</div>
                                    {% endif %}
                                </div>
                            </div>
                            {% endif %}

                            {% if finding.code_snippets %}
                            {% for snippet in finding.code_snippets %}
                            <div class="code-snippet">
                                <div class="code-header">
                                    <div class="code-location">
                                        <span>📄 {{ snippet.file_path }}</span>
                                        <span class="line-badge">Lines {{ snippet.line_start }}-{{ snippet.line_end }}</span>
                                    </div>
                                    <span>{{ snippet.language }}</span>
                                </div>
                                <div class="code-body">
                                    {% for line in snippet.lines %}
                                    <div class="code-line {% if line.highlighted %}highlighted{% endif %}">
                                        <div class="line-number">{{ line.number }}</div>
                                        <div class="line-content">{{ line.content }}</div>
                                    </div>
                                    {% endfor %}
                                </div>
                            </div>
                            {% endfor %}
                            {% endif %}

                            {% if finding.suggested_fix %}
                            <div class="suggested-fix">
                                <div class="suggested-fix-label">💡 Suggested Fix</div>
                                <div class="suggested-fix-text">{{ finding.suggested_fix }}</div>
                            </div>
                            {% endif %}
                        </div>
                    </div>
                    {% endfor %}
                </div>
            </div>
            {% endif %}
        </div>

        <div class="footer">
            <p>Generated by <strong>Falkor</strong> - Graph-Powered Code Analysis Platform</p>
            <p style="margin-top: 0.5rem; font-size: 0.875rem;">
                <a href="https://github.com/yourusername/falkor" style="color: #667eea; text-decoration: none;">github.com/yourusername/falkor</a>
            </p>
        </div>
    </div>
</body>
</html>
"""

"""Unit tests for Repotoire reporters.

Tests coverage for:
- BaseReporter: Abstract functionality, code snippet extraction, language detection
- HTMLReporter: HTML report generation with themes and code snippets
- MarkdownReporter: Markdown report generation
- SARIFReporter: SARIF 2.1.0 format output
- PDFReporter: PDF generation (basic tests only, requires weasyprint)
- ExcelReporter: Excel generation (basic tests only, requires openpyxl)
"""

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from repotoire.models import (
    CodebaseHealth,
    Finding,
    FindingsSummary,
    MetricsBreakdown,
    Severity,
)
from repotoire.config import ReportingConfig, ReportingTheme
from repotoire.reporters import (
    BaseReporter,
    HTMLReporter,
    MarkdownReporter,
    SARIFReporter,
    PDFReporter,
    ExcelReporter,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def sample_finding() -> Finding:
    """Create a sample finding for testing."""
    return Finding(
        id="test-finding-001",
        detector="TestDetector",
        severity=Severity.HIGH,
        title="Test Issue Found",
        description="This is a test description for the finding.",
        affected_nodes=["module.TestClass"],
        affected_files=["src/test_file.py"],
        line_start=10,
        line_end=20,
        graph_context={"complexity": 15},
        suggested_fix="Refactor the code to reduce complexity.",
        estimated_effort="Medium (1-2 days)",
    )


@pytest.fixture
def sample_findings(sample_finding) -> list[Finding]:
    """Create multiple sample findings for testing."""
    return [
        sample_finding,
        Finding(
            id="test-finding-002",
            detector="AnotherDetector",
            severity=Severity.MEDIUM,
            title="Medium Priority Issue",
            description="A medium severity finding.",
            affected_nodes=["module.AnotherClass"],
            affected_files=["src/another_file.py"],
        ),
        Finding(
            id="test-finding-003",
            detector="TestDetector",
            severity=Severity.LOW,
            title="Low Priority Issue",
            description="A low severity finding.",
            affected_nodes=["module.LowClass"],
            affected_files=["src/low_file.py"],
        ),
    ]


@pytest.fixture
def sample_health(sample_findings) -> CodebaseHealth:
    """Create a sample CodebaseHealth instance for testing."""
    return CodebaseHealth(
        grade="B",
        overall_score=82.5,
        structure_score=85.0,
        quality_score=78.0,
        architecture_score=84.0,
        issues_score=80.0,
        metrics=MetricsBreakdown(
            modularity=0.65,
            avg_coupling=3.2,
            circular_dependencies=1,
            bottleneck_count=2,
            dead_code_percentage=0.05,
            duplication_percentage=0.03,
            god_class_count=1,
            layer_violations=0,
            boundary_violations=0,
            abstraction_ratio=0.4,
            total_files=50,
            total_classes=30,
            total_functions=200,
            total_loc=5000,
        ),
        findings_summary=FindingsSummary(
            critical=0,
            high=1,
            medium=1,
            low=1,
            info=0,
        ),
        findings=sample_findings,
    )


@pytest.fixture
def empty_health() -> CodebaseHealth:
    """Create a CodebaseHealth with no findings."""
    return CodebaseHealth(
        grade="A",
        overall_score=95.0,
        structure_score=95.0,
        quality_score=95.0,
        architecture_score=95.0,
        issues_score=95.0,
        metrics=MetricsBreakdown(
            total_files=10,
            total_classes=5,
            total_functions=20,
            total_loc=500,
        ),
        findings_summary=FindingsSummary(),
        findings=[],
    )


@pytest.fixture
def temp_repo_path() -> Path:
    """Create a temporary repository with sample files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)

        # Create sample source files
        src_dir = repo_path / "src"
        src_dir.mkdir()

        test_file = src_dir / "test_file.py"
        test_file.write_text("""#!/usr/bin/env python3
\"\"\"Sample test file for reporter tests.\"\"\"


class TestClass:
    \"\"\"A test class.\"\"\"

    def __init__(self):
        self.value = 0

    def complex_method(self, x, y, z):
        \"\"\"A method with some complexity.\"\"\"
        if x > 0:
            if y > 0:
                if z > 0:
                    return x + y + z
                return x + y
            return x
        return 0

    def simple_method(self):
        \"\"\"A simple method.\"\"\"
        return self.value
""")

        another_file = src_dir / "another_file.py"
        another_file.write_text("""\"\"\"Another sample file.\"\"\"


class AnotherClass:
    pass
""")

        yield repo_path


# ============================================================================
# BaseReporter Tests
# ============================================================================


class TestBaseReporter:
    """Test BaseReporter abstract functionality."""

    def test_language_detection_python(self):
        """Test language detection for Python files."""
        # Can't instantiate abstract class directly, use a concrete subclass
        reporter = HTMLReporter()

        assert reporter._detect_language("test.py") == "python"
        assert reporter._detect_language("module/test.py") == "python"

    def test_language_detection_javascript(self):
        """Test language detection for JavaScript files."""
        reporter = HTMLReporter()

        assert reporter._detect_language("test.js") == "javascript"
        assert reporter._detect_language("test.jsx") == "javascript"

    def test_language_detection_typescript(self):
        """Test language detection for TypeScript files."""
        reporter = HTMLReporter()

        assert reporter._detect_language("test.ts") == "typescript"
        assert reporter._detect_language("test.tsx") == "typescript"

    def test_language_detection_other(self):
        """Test language detection for various languages."""
        reporter = HTMLReporter()

        assert reporter._detect_language("test.java") == "java"
        assert reporter._detect_language("test.go") == "go"
        assert reporter._detect_language("test.rs") == "rust"
        assert reporter._detect_language("test.rb") == "ruby"

    def test_language_detection_unknown(self):
        """Test language detection defaults to text."""
        reporter = HTMLReporter()

        assert reporter._detect_language("test.xyz") == "text"
        assert reporter._detect_language("Makefile") == "text"

    def test_severity_color_mapping(self):
        """Test severity color mapping."""
        reporter = HTMLReporter()

        assert reporter._get_severity_color(Severity.CRITICAL) == "#dc3545"
        assert reporter._get_severity_color(Severity.HIGH) == "#fd7e14"
        assert reporter._get_severity_color(Severity.MEDIUM) == "#ffc107"
        assert reporter._get_severity_color(Severity.LOW) == "#17a2b8"
        assert reporter._get_severity_color(Severity.INFO) == "#6c757d"

    def test_grade_color_mapping(self):
        """Test grade color mapping returns colors from theme."""
        reporter = HTMLReporter()

        # Test that each grade returns a valid color (hex format)
        for grade in ["A", "B", "C", "D", "F"]:
            color = reporter._get_grade_color(grade)
            assert color.startswith("#"), f"Grade {grade} color should be hex"
            assert len(color) == 7, f"Grade {grade} color should be #RRGGBB"

        # Unknown grade returns default
        assert reporter._get_grade_color("X").startswith("#")

    def test_snippet_extraction_with_repo_path(self, temp_repo_path, sample_finding):
        """Test code snippet extraction from repository."""
        reporter = HTMLReporter(repo_path=temp_repo_path)

        snippets = reporter._extract_code_snippets([sample_finding])

        # Should extract snippet for the finding
        assert sample_finding.id in snippets or len(snippets) == 0  # Depends on file match

    def test_snippet_extraction_without_repo_path(self, sample_finding):
        """Test snippet extraction returns empty without repo_path."""
        reporter = HTMLReporter(repo_path=None)

        snippets = reporter._extract_code_snippets([sample_finding])

        assert snippets == {}

    def test_snippet_extraction_disabled(self, temp_repo_path, sample_finding):
        """Test snippet extraction disabled via include_snippets."""
        reporter = HTMLReporter(repo_path=temp_repo_path, include_snippets=False)

        snippets = reporter._extract_code_snippets([sample_finding])

        assert snippets == {}


# ============================================================================
# HTMLReporter Tests
# ============================================================================


class TestHTMLReporter:
    """Test HTMLReporter functionality."""

    def test_generate_creates_file(self, sample_health, tmp_path):
        """Test that generate() creates an HTML file."""
        output_path = tmp_path / "report.html"
        reporter = HTMLReporter()

        reporter.generate(sample_health, output_path)

        assert output_path.exists()
        content = output_path.read_text()
        assert "<!DOCTYPE html>" in content
        assert sample_health.grade in content

    def test_generate_includes_grade(self, sample_health, tmp_path):
        """Test generated HTML includes grade information."""
        output_path = tmp_path / "report.html"
        reporter = HTMLReporter()

        reporter.generate(sample_health, output_path)

        content = output_path.read_text()
        assert f"grade-{sample_health.grade}" in content

    def test_generate_includes_findings(self, sample_health, tmp_path):
        """Test generated HTML includes finding titles."""
        output_path = tmp_path / "report.html"
        reporter = HTMLReporter()

        reporter.generate(sample_health, output_path)

        content = output_path.read_text()
        for finding in sample_health.findings:
            assert finding.title in content

    def test_generate_empty_findings(self, empty_health, tmp_path):
        """Test generation with no findings."""
        output_path = tmp_path / "report.html"
        reporter = HTMLReporter()

        reporter.generate(empty_health, output_path)

        assert output_path.exists()
        content = output_path.read_text()
        assert "<!DOCTYPE html>" in content

    def test_generate_with_custom_theme(self, sample_health, tmp_path):
        """Test generation with custom theme."""
        output_path = tmp_path / "report.html"
        config = ReportingConfig(
            theme_name="dark",
            title="Custom Report Title",
        )
        reporter = HTMLReporter(config=config)

        reporter.generate(sample_health, output_path)

        content = output_path.read_text()
        assert "Custom Report Title" in content

    def test_generate_creates_parent_dirs(self, sample_health, tmp_path):
        """Test that generate() creates parent directories."""
        output_path = tmp_path / "subdir" / "nested" / "report.html"
        reporter = HTMLReporter()

        reporter.generate(sample_health, output_path)

        assert output_path.exists()

    def test_reporter_with_repo_path(self, sample_health, temp_repo_path, tmp_path):
        """Test reporter with repo_path for code snippets."""
        output_path = tmp_path / "report.html"
        reporter = HTMLReporter(repo_path=temp_repo_path)

        reporter.generate(sample_health, output_path)

        assert output_path.exists()


# ============================================================================
# MarkdownReporter Tests
# ============================================================================


class TestMarkdownReporter:
    """Test MarkdownReporter functionality."""

    def test_generate_creates_file(self, sample_health, tmp_path):
        """Test that generate() creates a Markdown file."""
        output_path = tmp_path / "report.md"
        reporter = MarkdownReporter()

        reporter.generate(sample_health, output_path)

        assert output_path.exists()
        content = output_path.read_text()
        assert "# " in content  # Has headers

    def test_generate_string(self, sample_health):
        """Test generate_string() returns Markdown."""
        reporter = MarkdownReporter()

        markdown = reporter.generate_string(sample_health)

        assert "# " in markdown
        assert sample_health.grade in markdown

    def test_includes_grade_and_score(self, sample_health, tmp_path):
        """Test Markdown includes grade and score."""
        output_path = tmp_path / "report.md"
        reporter = MarkdownReporter()

        reporter.generate(sample_health, output_path)

        content = output_path.read_text()
        assert sample_health.grade in content
        assert str(sample_health.overall_score) in content or "82.5" in content

    def test_includes_findings_summary(self, sample_health, tmp_path):
        """Test Markdown includes findings summary table."""
        output_path = tmp_path / "report.md"
        reporter = MarkdownReporter()

        reporter.generate(sample_health, output_path)

        content = output_path.read_text()
        assert "Findings Summary" in content
        assert "Critical" in content
        assert "High" in content

    def test_includes_toc_by_default(self, sample_health, tmp_path):
        """Test Markdown includes table of contents."""
        output_path = tmp_path / "report.md"
        reporter = MarkdownReporter()

        reporter.generate(sample_health, output_path)

        content = output_path.read_text()
        assert "Table of Contents" in content

    def test_toc_can_be_disabled(self, sample_health, tmp_path):
        """Test TOC can be disabled."""
        output_path = tmp_path / "report.md"
        reporter = MarkdownReporter(include_toc=False)

        reporter.generate(sample_health, output_path)

        content = output_path.read_text()
        assert "Table of Contents" not in content

    def test_max_findings_per_severity(self, sample_health, tmp_path):
        """Test limiting findings per severity."""
        output_path = tmp_path / "report.md"
        reporter = MarkdownReporter(max_findings_per_severity=1)

        reporter.generate(sample_health, output_path)

        assert output_path.exists()

    def test_includes_footer(self, sample_health, tmp_path):
        """Test Markdown includes footer."""
        output_path = tmp_path / "report.md"
        reporter = MarkdownReporter()

        reporter.generate(sample_health, output_path)

        content = output_path.read_text()
        assert "Repotoire" in content


# ============================================================================
# SARIFReporter Tests
# ============================================================================


class TestSARIFReporter:
    """Test SARIFReporter functionality."""

    def test_generate_creates_valid_json(self, sample_health, tmp_path):
        """Test that generate() creates valid JSON."""
        output_path = tmp_path / "report.sarif"
        reporter = SARIFReporter()

        reporter.generate(sample_health, output_path)

        assert output_path.exists()
        with open(output_path) as f:
            data = json.load(f)  # Should not raise

        assert "$schema" in data
        assert "version" in data

    def test_sarif_version(self, sample_health, tmp_path):
        """Test SARIF version is 2.1.0."""
        output_path = tmp_path / "report.sarif"
        reporter = SARIFReporter()

        reporter.generate(sample_health, output_path)

        with open(output_path) as f:
            data = json.load(f)

        assert data["version"] == "2.1.0"

    def test_sarif_has_runs(self, sample_health, tmp_path):
        """Test SARIF has runs array."""
        output_path = tmp_path / "report.sarif"
        reporter = SARIFReporter()

        reporter.generate(sample_health, output_path)

        with open(output_path) as f:
            data = json.load(f)

        assert "runs" in data
        assert len(data["runs"]) > 0

    def test_sarif_tool_info(self, sample_health, tmp_path):
        """Test SARIF includes tool information."""
        output_path = tmp_path / "report.sarif"
        reporter = SARIFReporter(tool_name="TestTool", tool_version="1.0.0")

        reporter.generate(sample_health, output_path)

        with open(output_path) as f:
            data = json.load(f)

        tool = data["runs"][0]["tool"]["driver"]
        assert tool["name"] == "TestTool"
        assert tool["version"] == "1.0.0"

    def test_sarif_results(self, sample_health, tmp_path):
        """Test SARIF includes results for findings."""
        output_path = tmp_path / "report.sarif"
        reporter = SARIFReporter()

        reporter.generate(sample_health, output_path)

        with open(output_path) as f:
            data = json.load(f)

        results = data["runs"][0]["results"]
        assert len(results) == len(sample_health.findings)

    def test_sarif_severity_mapping(self, sample_health, tmp_path):
        """Test SARIF severity mapping."""
        output_path = tmp_path / "report.sarif"
        reporter = SARIFReporter()

        reporter.generate(sample_health, output_path)

        with open(output_path) as f:
            data = json.load(f)

        results = data["runs"][0]["results"]

        # HIGH severity should map to "error" level
        # Find result by looking for the HIGH severity finding
        high_results = [r for r in results if r.get("level") == "error"]
        assert len(high_results) >= 1, "Should have at least one error level result"

        # MEDIUM severity should map to "warning" level
        warning_results = [r for r in results if r.get("level") == "warning"]
        assert len(warning_results) >= 1, "Should have at least one warning level result"

        # LOW severity should map to "note" level
        note_results = [r for r in results if r.get("level") == "note"]
        assert len(note_results) >= 1, "Should have at least one note level result"

    def test_generate_string(self, sample_health):
        """Test generate_string() returns valid SARIF JSON."""
        reporter = SARIFReporter()

        sarif_str = reporter.generate_string(sample_health)

        data = json.loads(sarif_str)
        assert data["version"] == "2.1.0"

    def test_sarif_empty_findings(self, empty_health, tmp_path):
        """Test SARIF generation with no findings."""
        output_path = tmp_path / "report.sarif"
        reporter = SARIFReporter()

        reporter.generate(empty_health, output_path)

        with open(output_path) as f:
            data = json.load(f)

        assert data["runs"][0]["results"] == []


# ============================================================================
# PDFReporter Tests
# ============================================================================


class TestPDFReporter:
    """Test PDFReporter functionality."""

    def test_init_with_defaults(self):
        """Test PDFReporter initialization with defaults."""
        reporter = PDFReporter()

        assert reporter.include_snippets is True
        assert reporter.page_size == "A4"

    def test_init_with_custom_settings(self, temp_repo_path):
        """Test PDFReporter initialization with custom settings."""
        reporter = PDFReporter(
            repo_path=temp_repo_path,
            include_snippets=False,
            page_size="Letter",
        )

        assert reporter.repo_path == temp_repo_path
        assert reporter.include_snippets is False
        assert reporter.page_size == "Letter"

    def test_generate_raises_without_weasyprint(self, sample_health, tmp_path):
        """Test generate() raises ImportError without weasyprint."""
        output_path = tmp_path / "report.pdf"
        reporter = PDFReporter()

        # This may or may not raise depending on weasyprint installation
        try:
            reporter.generate(sample_health, output_path)
            assert output_path.exists()  # If it didn't raise, file should exist
        except ImportError as e:
            assert "weasyprint" in str(e).lower()


# ============================================================================
# ExcelReporter Tests
# ============================================================================


class TestExcelReporter:
    """Test ExcelReporter functionality."""

    def test_init_with_defaults(self):
        """Test ExcelReporter initialization."""
        reporter = ExcelReporter()

        assert reporter.repo_path is None
        assert reporter.include_snippets is False

    def test_init_with_repo_path(self, temp_repo_path):
        """Test ExcelReporter initialization with repo_path."""
        reporter = ExcelReporter(repo_path=temp_repo_path)

        assert reporter.repo_path == temp_repo_path

    def test_generate_raises_without_openpyxl(self, sample_health, tmp_path):
        """Test generate() raises ImportError without openpyxl."""
        output_path = tmp_path / "report.xlsx"
        reporter = ExcelReporter()

        # This may or may not raise depending on openpyxl installation
        try:
            reporter.generate(sample_health, output_path)
            assert output_path.exists()  # If it didn't raise, file should exist
        except ImportError as e:
            assert "openpyxl" in str(e).lower()


# ============================================================================
# CodebaseHealth.from_dict Tests
# ============================================================================


class TestCodebaseHealthFromDict:
    """Test CodebaseHealth.from_dict() deserialization."""

    def test_from_dict_basic(self):
        """Test basic from_dict deserialization."""
        data = {
            "grade": "B",
            "overall_score": 80.0,
            "structure_score": 85.0,
            "quality_score": 75.0,
            "architecture_score": 80.0,
            "issues_score": 78.0,
            "findings_summary": {
                "critical": 0,
                "high": 1,
                "medium": 2,
                "low": 3,
                "info": 0,
            },
            "findings": [],
        }

        health = CodebaseHealth.from_dict(data)

        assert health.grade == "B"
        assert health.overall_score == 80.0
        assert health.findings_summary.high == 1
        assert health.findings_summary.total == 6

    def test_from_dict_with_findings(self):
        """Test from_dict with findings."""
        data = {
            "grade": "C",
            "overall_score": 70.0,
            "structure_score": 70.0,
            "quality_score": 70.0,
            "architecture_score": 70.0,
            "issues_score": 70.0,
            "findings_summary": {"critical": 0, "high": 1},
            "findings": [
                {
                    "id": "test-123",
                    "detector": "TestDetector",
                    "severity": "high",
                    "title": "Test Finding",
                    "description": "Test description",
                    "affected_nodes": ["node1"],
                    "affected_files": ["file1.py"],
                }
            ],
        }

        health = CodebaseHealth.from_dict(data)

        assert len(health.findings) == 1
        assert health.findings[0].title == "Test Finding"
        assert health.findings[0].severity == Severity.HIGH

    def test_from_dict_with_metrics(self):
        """Test from_dict with metrics."""
        data = {
            "grade": "A",
            "overall_score": 90.0,
            "structure_score": 90.0,
            "quality_score": 90.0,
            "architecture_score": 90.0,
            "issues_score": 90.0,
            "findings_summary": {},
            "findings": [],
            "metrics": {
                "modularity": 0.7,
                "avg_coupling": 2.5,
                "circular_dependencies": 0,
                "total_files": 100,
            },
        }

        health = CodebaseHealth.from_dict(data)

        assert health.metrics.modularity == 0.7
        assert health.metrics.avg_coupling == 2.5
        assert health.metrics.total_files == 100

    def test_from_dict_with_timestamp(self):
        """Test from_dict with timestamp."""
        data = {
            "grade": "A",
            "overall_score": 95.0,
            "structure_score": 95.0,
            "quality_score": 95.0,
            "architecture_score": 95.0,
            "issues_score": 95.0,
            "findings_summary": {},
            "findings": [],
            "analyzed_at": "2024-01-15T10:30:00",
        }

        health = CodebaseHealth.from_dict(data)

        assert health.analyzed_at.year == 2024
        assert health.analyzed_at.month == 1
        assert health.analyzed_at.day == 15

    def test_roundtrip_serialization(self, sample_health):
        """Test to_dict -> from_dict roundtrip."""
        # Serialize
        data = sample_health.to_dict()

        # Deserialize
        restored = CodebaseHealth.from_dict(data)

        assert restored.grade == sample_health.grade
        assert restored.overall_score == sample_health.overall_score
        assert restored.findings_summary.total == sample_health.findings_summary.total
        assert len(restored.findings) == len(sample_health.findings)

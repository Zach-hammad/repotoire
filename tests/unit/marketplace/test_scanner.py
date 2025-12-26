"""Unit tests for the marketplace asset scanner.

Tests cover:
- Pattern detection (CRITICAL, HIGH, MEDIUM, LOW severity)
- Verdict generation
- File scanning
- Content scanning
- Edge cases
"""

import pytest
from pathlib import Path
import tempfile

from repotoire.marketplace.scanner import (
    AssetScanner,
    DangerousPattern,
    ScanFinding,
    SeverityLevel,
    DANGEROUS_PATTERNS,
    SCANNABLE_EXTENSIONS,
)


class TestSeverityLevel:
    """Tests for SeverityLevel enum."""

    def test_severity_values(self):
        """Severity levels have correct string values."""
        assert SeverityLevel.CRITICAL.value == "critical"
        assert SeverityLevel.HIGH.value == "high"
        assert SeverityLevel.MEDIUM.value == "medium"
        assert SeverityLevel.LOW.value == "low"


class TestScanFinding:
    """Tests for ScanFinding dataclass."""

    def test_to_dict(self):
        """ScanFinding converts to dictionary correctly."""
        finding = ScanFinding(
            severity=SeverityLevel.HIGH,
            category="network_access",
            message="Network access detected",
            file_path="test.py",
            line_number=10,
            pattern_matched="requests.get(",
        )

        result = finding.to_dict()

        assert result["severity"] == "high"
        assert result["category"] == "network_access"
        assert result["message"] == "Network access detected"
        assert result["file_path"] == "test.py"
        assert result["line_number"] == 10
        assert result["pattern_matched"] == "requests.get("

    def test_to_dict_with_none_values(self):
        """ScanFinding handles None values in to_dict."""
        finding = ScanFinding(
            severity=SeverityLevel.LOW,
            category="test",
            message="Test message",
        )

        result = finding.to_dict()

        assert result["file_path"] is None
        assert result["line_number"] is None
        assert result["pattern_matched"] is None


class TestDangerousPatterns:
    """Tests for pattern definitions."""

    def test_critical_patterns_exist(self):
        """CRITICAL patterns are defined."""
        critical_categories = [
            "shell_injection",
            "eval_exec",
            "base64_exec",
            "env_exfiltration",
            "pickle_load",
            "marshal_load",
            "os_system",
        ]
        for category in critical_categories:
            assert category in DANGEROUS_PATTERNS
            assert DANGEROUS_PATTERNS[category].severity == SeverityLevel.CRITICAL

    def test_high_patterns_exist(self):
        """HIGH patterns are defined."""
        high_categories = [
            "network_access",
            "file_write",
            "subprocess_any",
            "ctypes_import",
            "socket_access",
        ]
        for category in high_categories:
            assert category in DANGEROUS_PATTERNS
            assert DANGEROUS_PATTERNS[category].severity == SeverityLevel.HIGH

    def test_medium_patterns_exist(self):
        """MEDIUM patterns are defined."""
        medium_categories = [
            "hardcoded_secret",
            "ip_address",
            "private_key_content",
            "aws_credentials",
            "generic_api_key",
        ]
        for category in medium_categories:
            assert category in DANGEROUS_PATTERNS
            assert DANGEROUS_PATTERNS[category].severity == SeverityLevel.MEDIUM

    def test_low_patterns_exist(self):
        """LOW patterns are defined."""
        low_categories = [
            "todo_fixme",
            "debug_print",
        ]
        for category in low_categories:
            assert category in DANGEROUS_PATTERNS
            assert DANGEROUS_PATTERNS[category].severity == SeverityLevel.LOW


class TestAssetScanner:
    """Tests for AssetScanner class."""

    @pytest.fixture
    def scanner(self):
        """Create a scanner instance."""
        return AssetScanner()

    # =========================================================================
    # CRITICAL pattern detection
    # =========================================================================

    def test_detect_eval(self, scanner):
        """Scanner detects eval() calls."""
        content = "result = eval(user_input)"
        findings = scanner._scan_content(content, "test.py")

        assert len(findings) == 1
        assert findings[0].severity == SeverityLevel.CRITICAL
        assert findings[0].category == "eval_exec"

    def test_detect_exec(self, scanner):
        """Scanner detects exec() calls."""
        content = "exec(code_string)"
        findings = scanner._scan_content(content, "test.py")

        assert len(findings) == 1
        assert findings[0].severity == SeverityLevel.CRITICAL
        assert findings[0].category == "eval_exec"

    def test_detect_shell_injection(self, scanner):
        """Scanner detects shell=True in subprocess."""
        content = "subprocess.call(cmd, shell=True)"
        findings = scanner._scan_content(content, "test.py")

        assert len(findings) >= 1
        critical = [f for f in findings if f.severity == SeverityLevel.CRITICAL]
        assert len(critical) >= 1
        assert any(f.category == "shell_injection" for f in critical)

    def test_detect_os_system(self, scanner):
        """Scanner detects os.system() calls."""
        content = "os.system('rm -rf /')"
        findings = scanner._scan_content(content, "test.py")

        assert len(findings) == 1
        assert findings[0].severity == SeverityLevel.CRITICAL
        assert findings[0].category == "os_system"

    def test_detect_pickle_load(self, scanner):
        """Scanner detects pickle.load() calls."""
        content = "data = pickle.load(file)"
        findings = scanner._scan_content(content, "test.py")

        assert len(findings) == 1
        assert findings[0].severity == SeverityLevel.CRITICAL
        assert findings[0].category == "pickle_load"

    def test_detect_pickle_loads(self, scanner):
        """Scanner detects pickle.loads() calls."""
        content = "data = pickle.loads(data)"
        findings = scanner._scan_content(content, "test.py")

        assert len(findings) == 1
        assert findings[0].severity == SeverityLevel.CRITICAL
        assert findings[0].category == "pickle_load"

    # =========================================================================
    # HIGH pattern detection
    # =========================================================================

    def test_detect_network_access_requests(self, scanner):
        """Scanner detects requests library usage."""
        content = "response = requests.get('https://example.com')"
        findings = scanner._scan_content(content, "test.py")

        assert len(findings) >= 1
        high = [f for f in findings if f.severity == SeverityLevel.HIGH]
        assert len(high) >= 1
        assert any(f.category == "network_access" for f in high)

    def test_detect_network_access_httpx(self, scanner):
        """Scanner detects httpx library usage."""
        content = "response = httpx.post('https://api.example.com', json=data)"
        findings = scanner._scan_content(content, "test.py")

        assert len(findings) >= 1
        high = [f for f in findings if f.severity == SeverityLevel.HIGH]
        assert len(high) >= 1

    def test_detect_file_write(self, scanner):
        """Scanner detects file write operations."""
        content = "f = open('file.txt', 'w')"
        findings = scanner._scan_content(content, "test.py")

        assert len(findings) >= 1
        high = [f for f in findings if f.severity == SeverityLevel.HIGH]
        assert len(high) >= 1
        assert any(f.category == "file_write" for f in high)

    def test_detect_subprocess_run(self, scanner):
        """Scanner detects subprocess.run() calls."""
        content = "subprocess.run(['ls', '-la'])"
        findings = scanner._scan_content(content, "test.py")

        assert len(findings) >= 1
        high = [f for f in findings if f.severity == SeverityLevel.HIGH]
        assert len(high) >= 1
        assert any(f.category == "subprocess_any" for f in high)

    def test_detect_ctypes_import(self, scanner):
        """Scanner detects ctypes imports."""
        content = "import ctypes"
        findings = scanner._scan_content(content, "test.py")

        assert len(findings) >= 1
        high = [f for f in findings if f.severity == SeverityLevel.HIGH]
        assert len(high) >= 1
        assert any(f.category == "ctypes_import" for f in high)

    def test_detect_socket(self, scanner):
        """Scanner detects socket usage."""
        content = "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)"
        findings = scanner._scan_content(content, "test.py")

        assert len(findings) >= 1
        high = [f for f in findings if f.severity == SeverityLevel.HIGH]
        assert len(high) >= 1
        assert any(f.category == "socket_access" for f in high)

    # =========================================================================
    # MEDIUM pattern detection
    # =========================================================================

    def test_detect_hardcoded_secret(self, scanner):
        """Scanner detects hardcoded secrets."""
        content = "api_key = 'sk_live_abcdef1234567890'"
        findings = scanner._scan_content(content, "test.py")

        assert len(findings) >= 1
        medium = [f for f in findings if f.severity == SeverityLevel.MEDIUM]
        assert len(medium) >= 1

    def test_detect_ip_address(self, scanner):
        """Scanner detects hardcoded IP addresses."""
        content = "server = '192.168.1.100'"
        findings = scanner._scan_content(content, "test.py")

        assert len(findings) >= 1
        medium = [f for f in findings if f.severity == SeverityLevel.MEDIUM]
        assert len(medium) >= 1
        assert any(f.category == "ip_address" for f in medium)

    def test_detect_private_key(self, scanner):
        """Scanner detects private key content."""
        content = "key = '''-----BEGIN PRIVATE KEY-----\nMIIEvg...\n-----END PRIVATE KEY-----'''"
        findings = scanner._scan_content(content, "test.py")

        assert len(findings) >= 1
        medium = [f for f in findings if f.severity == SeverityLevel.MEDIUM]
        assert len(medium) >= 1
        assert any(f.category == "private_key_content" for f in medium)

    def test_detect_aws_key(self, scanner):
        """Scanner detects AWS access key IDs."""
        content = "aws_key = 'AKIAIOSFODNN7EXAMPLE'"
        findings = scanner._scan_content(content, "test.py")

        assert len(findings) >= 1
        medium = [f for f in findings if f.severity == SeverityLevel.MEDIUM]
        assert len(medium) >= 1
        assert any(f.category == "aws_credentials" for f in medium)

    # =========================================================================
    # LOW pattern detection
    # =========================================================================

    def test_detect_todo_comment(self, scanner):
        """Scanner detects TODO comments."""
        content = "# TODO: Fix this later"
        findings = scanner._scan_content(content, "test.py")

        assert len(findings) >= 1
        low = [f for f in findings if f.severity == SeverityLevel.LOW]
        assert len(low) >= 1
        assert any(f.category == "todo_fixme" for f in low)

    def test_detect_fixme_comment(self, scanner):
        """Scanner detects FIXME comments."""
        content = "# FIXME: This is broken"
        findings = scanner._scan_content(content, "test.py")

        assert len(findings) >= 1
        low = [f for f in findings if f.severity == SeverityLevel.LOW]
        assert len(low) >= 1

    # =========================================================================
    # Verdict tests
    # =========================================================================

    def test_verdict_approved_no_findings(self, scanner):
        """Empty findings returns approved verdict."""
        verdict, message = scanner.get_verdict([])

        assert verdict == "approved"
        assert "No issues" in message

    def test_verdict_rejected_critical(self, scanner):
        """CRITICAL findings result in rejected verdict."""
        findings = [
            ScanFinding(
                severity=SeverityLevel.CRITICAL,
                category="eval_exec",
                message="Dynamic code execution detected",
            )
        ]

        verdict, message = scanner.get_verdict(findings)

        assert verdict == "rejected"
        assert "Critical" in message

    def test_verdict_pending_review_high(self, scanner):
        """HIGH findings result in pending_review verdict."""
        findings = [
            ScanFinding(
                severity=SeverityLevel.HIGH,
                category="network_access",
                message="Network access detected",
            )
        ]

        verdict, message = scanner.get_verdict(findings)

        assert verdict == "pending_review"
        assert "review" in message.lower()

    def test_verdict_approved_with_warnings_medium(self, scanner):
        """MEDIUM findings result in approved_with_warnings verdict."""
        findings = [
            ScanFinding(
                severity=SeverityLevel.MEDIUM,
                category="hardcoded_secret",
                message="Potential secret detected",
            )
        ]

        verdict, message = scanner.get_verdict(findings)

        assert verdict == "approved_with_warnings"
        assert "warning" in message.lower()

    def test_verdict_approved_low_only(self, scanner):
        """LOW-only findings result in approved verdict."""
        findings = [
            ScanFinding(
                severity=SeverityLevel.LOW,
                category="todo_fixme",
                message="TODO comment detected",
            )
        ]

        verdict, message = scanner.get_verdict(findings)

        assert verdict == "approved"

    def test_verdict_critical_takes_precedence(self, scanner):
        """CRITICAL verdict takes precedence over others."""
        findings = [
            ScanFinding(severity=SeverityLevel.LOW, category="todo", message="Todo"),
            ScanFinding(severity=SeverityLevel.MEDIUM, category="ip", message="IP"),
            ScanFinding(severity=SeverityLevel.HIGH, category="net", message="Net"),
            ScanFinding(severity=SeverityLevel.CRITICAL, category="eval", message="Eval"),
        ]

        verdict, _ = scanner.get_verdict(findings)

        assert verdict == "rejected"

    # =========================================================================
    # File scanning tests
    # =========================================================================

    def test_scan_file(self, scanner):
        """Scanner can scan a file."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write("result = eval(user_input)")
            f.flush()

            findings = scanner._scan_file(Path(f.name))

            assert len(findings) >= 1
            assert any(f.category == "eval_exec" for f in findings)

    def test_scan_asset_directory(self, scanner):
        """Scanner can scan a directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test files
            (Path(tmpdir) / "main.py").write_text("result = eval(input())")
            (Path(tmpdir) / "utils.py").write_text("import subprocess")
            (Path(tmpdir) / "data.txt").write_text("This is not scanned")

            findings = scanner.scan_asset(Path(tmpdir))

            # Should find eval in main.py
            assert any(f.category == "eval_exec" for f in findings)

    def test_scan_skips_large_files(self, scanner):
        """Scanner skips files larger than max size."""
        scanner.max_file_size = 10  # Very small limit

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write("x" * 100)  # Larger than limit
            f.write("\neval(x)")
            f.flush()

            findings = scanner._scan_file(Path(f.name))

            assert len(findings) == 0

    def test_scan_nonexistent_path(self, scanner):
        """Scanner handles nonexistent paths gracefully."""
        findings = scanner.scan_asset(Path("/nonexistent/path"))

        assert findings == []

    # =========================================================================
    # Summary tests
    # =========================================================================

    def test_get_summary(self, scanner):
        """Scanner generates correct summary."""
        findings = [
            ScanFinding(severity=SeverityLevel.CRITICAL, category="a", message="a"),
            ScanFinding(severity=SeverityLevel.CRITICAL, category="b", message="b"),
            ScanFinding(severity=SeverityLevel.HIGH, category="c", message="c"),
            ScanFinding(severity=SeverityLevel.MEDIUM, category="d", message="d"),
            ScanFinding(severity=SeverityLevel.LOW, category="e", message="e"),
            ScanFinding(severity=SeverityLevel.LOW, category="f", message="f"),
        ]

        summary = scanner.get_summary(findings)

        assert summary["critical"] == 2
        assert summary["high"] == 1
        assert summary["medium"] == 1
        assert summary["low"] == 2
        assert summary["total"] == 6

    def test_get_summary_empty(self, scanner):
        """Scanner summary handles empty findings."""
        summary = scanner.get_summary([])

        assert summary["critical"] == 0
        assert summary["high"] == 0
        assert summary["medium"] == 0
        assert summary["low"] == 0
        assert summary["total"] == 0

    # =========================================================================
    # Edge cases
    # =========================================================================

    def test_line_number_calculation(self, scanner):
        """Scanner calculates correct line numbers."""
        content = """line 1
line 2
result = eval(x)
line 4"""
        findings = scanner._scan_content(content, "test.py")

        assert len(findings) == 1
        assert findings[0].line_number == 3

    def test_pattern_matched_truncation(self, scanner):
        """Long pattern matches are truncated."""
        content = "eval(" + "x" * 200 + ")"
        findings = scanner._scan_content(content, "test.py")

        assert len(findings) == 1
        assert len(findings[0].pattern_matched) <= 100

    def test_custom_patterns(self):
        """Scanner accepts custom patterns."""
        custom_patterns = {
            "custom_danger": DangerousPattern(
                pattern=r"dangerous_function\s*\(",
                severity=SeverityLevel.CRITICAL,
                message="Custom dangerous function detected",
                category="custom_danger",
            )
        }

        scanner = AssetScanner(patterns=custom_patterns)
        findings = scanner._scan_content("dangerous_function()", "test.py")

        assert len(findings) == 1
        assert findings[0].category == "custom_danger"

    def test_custom_extensions(self):
        """Scanner respects custom extensions."""
        scanner = AssetScanner(extensions={".xyz"})

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create files with different extensions
            (Path(tmpdir) / "file.py").write_text("eval(x)")
            (Path(tmpdir) / "file.xyz").write_text("eval(x)")

            findings = scanner.scan_asset(Path(tmpdir))

            # Should only scan .xyz file
            file_paths = [f.file_path for f in findings]
            assert any("file.xyz" in p for p in file_paths)
            assert not any("file.py" in p for p in file_paths)

    def test_safe_code_no_findings(self, scanner):
        """Safe code produces no findings."""
        content = """
def add(a, b):
    '''Add two numbers.'''
    return a + b

class Calculator:
    def __init__(self):
        self.value = 0

    def add(self, x):
        self.value += x
        return self.value
"""
        findings = scanner._scan_content(content, "test.py")

        # Should have no critical/high findings
        critical_high = [
            f for f in findings
            if f.severity in (SeverityLevel.CRITICAL, SeverityLevel.HIGH)
        ]
        assert len(critical_high) == 0

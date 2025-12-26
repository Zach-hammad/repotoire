"""Security scanner for marketplace assets.

This module provides automated security scanning for marketplace assets,
detecting dangerous patterns like code injection, shell execution, and
potential malware.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class SeverityLevel(str, Enum):
    """Severity level for scan findings."""

    CRITICAL = "critical"  # Block immediately, no manual override
    HIGH = "high"  # Requires manual review
    MEDIUM = "medium"  # Warning, allow with flag
    LOW = "low"  # Informational only


@dataclass
class ScanFinding:
    """A finding from the security scan."""

    severity: SeverityLevel
    category: str  # e.g., "shell_injection", "eval_exec"
    message: str  # Human-readable description
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    pattern_matched: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "severity": self.severity.value,
            "category": self.category,
            "message": self.message,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "pattern_matched": self.pattern_matched,
        }


@dataclass
class DangerousPattern:
    """Definition of a dangerous pattern to detect."""

    pattern: str
    severity: SeverityLevel
    message: str
    category: str
    # Compiled regex pattern (lazily compiled)
    _compiled: Optional[re.Pattern] = field(default=None, repr=False)

    def get_regex(self) -> re.Pattern:
        """Get compiled regex, compiling lazily if needed."""
        if self._compiled is None:
            self._compiled = re.compile(self.pattern, re.IGNORECASE | re.MULTILINE)
        return self._compiled


# Pattern definitions organized by severity
DANGEROUS_PATTERNS: dict[str, DangerousPattern] = {
    # ==========================================================================
    # CRITICAL - Block immediately, no manual override
    # ==========================================================================
    "shell_injection": DangerousPattern(
        pattern=r"subprocess\.call\([^)]*shell\s*=\s*True",
        severity=SeverityLevel.CRITICAL,
        message="Shell injection vulnerability detected",
        category="shell_injection",
    ),
    "eval_exec": DangerousPattern(
        pattern=r"\b(eval|exec)\s*\(",
        severity=SeverityLevel.CRITICAL,
        message="Dynamic code execution (eval/exec) detected",
        category="eval_exec",
    ),
    "base64_exec": DangerousPattern(
        pattern=r"base64\.(b64)?decode.*exec",
        severity=SeverityLevel.CRITICAL,
        message="Encoded code execution detected",
        category="base64_exec",
    ),
    "env_exfiltration": DangerousPattern(
        pattern=r"os\.environ.*requests?\.(post|put|patch)",
        severity=SeverityLevel.CRITICAL,
        message="Potential environment variable exfiltration",
        category="env_exfiltration",
    ),
    "pickle_load": DangerousPattern(
        pattern=r"pickle\.(load|loads)\s*\(",
        severity=SeverityLevel.CRITICAL,
        message="Unsafe pickle deserialization detected (arbitrary code execution)",
        category="pickle_load",
    ),
    "marshal_load": DangerousPattern(
        pattern=r"marshal\.(load|loads)\s*\(",
        severity=SeverityLevel.CRITICAL,
        message="Unsafe marshal deserialization detected",
        category="marshal_load",
    ),
    "compile_exec": DangerousPattern(
        pattern=r"compile\s*\([^)]+\)\s*\)\s*\n.*exec",
        severity=SeverityLevel.CRITICAL,
        message="Dynamic code compilation and execution detected",
        category="compile_exec",
    ),
    "os_system": DangerousPattern(
        pattern=r"os\.system\s*\(",
        severity=SeverityLevel.CRITICAL,
        message="Direct shell command execution via os.system()",
        category="os_system",
    ),
    # ==========================================================================
    # HIGH - Requires manual review
    # ==========================================================================
    "network_access": DangerousPattern(
        pattern=r"(requests|httpx|urllib|aiohttp)\.(get|post|put|patch|delete|head|request)\s*\(",
        severity=SeverityLevel.HIGH,
        message="Network access detected - requires review",
        category="network_access",
    ),
    "file_write": DangerousPattern(
        pattern=r"open\s*\([^)]+,\s*['\"][wa]['\"]",
        severity=SeverityLevel.HIGH,
        message="File write operation detected",
        category="file_write",
    ),
    "subprocess_any": DangerousPattern(
        pattern=r"subprocess\.(run|call|Popen|check_output|check_call)\s*\(",
        severity=SeverityLevel.HIGH,
        message="Subprocess execution detected",
        category="subprocess_any",
    ),
    "ctypes_import": DangerousPattern(
        pattern=r"(from\s+ctypes\s+import|import\s+ctypes)",
        severity=SeverityLevel.HIGH,
        message="C library access via ctypes detected",
        category="ctypes_import",
    ),
    "socket_access": DangerousPattern(
        pattern=r"socket\.(socket|create_connection|connect)\s*\(",
        severity=SeverityLevel.HIGH,
        message="Low-level socket access detected",
        category="socket_access",
    ),
    "yaml_unsafe_load": DangerousPattern(
        pattern=r"yaml\.(load|unsafe_load)\s*\([^)]*(?!Loader\s*=\s*yaml\.SafeLoader)",
        severity=SeverityLevel.HIGH,
        message="Potentially unsafe YAML loading detected (use safe_load)",
        category="yaml_unsafe_load",
    ),
    # ==========================================================================
    # MEDIUM - Warning, allow with flag
    # ==========================================================================
    "hardcoded_secret": DangerousPattern(
        pattern=r"(api_key|password|secret|token|auth_token|private_key)\s*=\s*['\"][^'\"]{8,}['\"]",
        severity=SeverityLevel.MEDIUM,
        message="Potential hardcoded secret detected",
        category="hardcoded_secret",
    ),
    "ip_address": DangerousPattern(
        pattern=r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}",
        severity=SeverityLevel.MEDIUM,
        message="Hardcoded IP address detected",
        category="ip_address",
    ),
    "private_key_content": DangerousPattern(
        pattern=r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----",
        severity=SeverityLevel.MEDIUM,
        message="Private key content detected",
        category="private_key_content",
    ),
    "aws_credentials": DangerousPattern(
        pattern=r"(AKIA|A3T|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}",
        severity=SeverityLevel.MEDIUM,
        message="Potential AWS access key ID detected",
        category="aws_credentials",
    ),
    "generic_api_key": DangerousPattern(
        pattern=r"(sk-|pk_live_|sk_live_|rk_live_)[a-zA-Z0-9]{20,}",
        severity=SeverityLevel.MEDIUM,
        message="Potential API key detected (Stripe, OpenAI, etc.)",
        category="generic_api_key",
    ),
    # ==========================================================================
    # LOW - Informational only
    # ==========================================================================
    "todo_fixme": DangerousPattern(
        pattern=r"#\s*(TODO|FIXME|XXX|HACK|BUG):",
        severity=SeverityLevel.LOW,
        message="Development comment detected",
        category="todo_fixme",
    ),
    "debug_print": DangerousPattern(
        pattern=r"\bprint\s*\([^)]*password|secret|token",
        severity=SeverityLevel.LOW,
        message="Debug print with potential sensitive data",
        category="debug_print",
    ),
}

# File extensions to scan
SCANNABLE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".md",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".sh",
    ".bash",
}

# Maximum file size to scan (1MB)
MAX_FILE_SIZE = 1 * 1024 * 1024


class AssetScanner:
    """Security scanner for marketplace assets.

    Scans asset files for dangerous patterns and returns findings
    with severity levels.

    Usage:
        scanner = AssetScanner()
        findings = scanner.scan_asset(Path("/path/to/extracted/asset"))
        verdict, message = scanner.get_verdict(findings)

    The verdict determines what happens to the asset:
    - "rejected": CRITICAL findings, block immediately
    - "pending_review": HIGH findings, needs manual review
    - "approved_with_warnings": MEDIUM findings only
    - "approved": No significant findings
    """

    def __init__(
        self,
        patterns: Optional[dict[str, DangerousPattern]] = None,
        extensions: Optional[set[str]] = None,
        max_file_size: int = MAX_FILE_SIZE,
    ):
        """Initialize the scanner.

        Args:
            patterns: Custom patterns to use (defaults to DANGEROUS_PATTERNS)
            extensions: File extensions to scan (defaults to SCANNABLE_EXTENSIONS)
            max_file_size: Maximum file size to scan in bytes
        """
        self.patterns = patterns or DANGEROUS_PATTERNS
        self.extensions = extensions or SCANNABLE_EXTENSIONS
        self.max_file_size = max_file_size

    def scan_asset(self, asset_path: Path) -> list[ScanFinding]:
        """Scan an asset directory for dangerous patterns.

        Args:
            asset_path: Path to the extracted asset directory

        Returns:
            List of ScanFinding objects for all detected issues
        """
        findings: list[ScanFinding] = []

        if not asset_path.exists():
            return findings

        if asset_path.is_file():
            # Single file scan
            findings.extend(self._scan_file(asset_path))
        else:
            # Directory scan
            for file_path in asset_path.rglob("*"):
                if file_path.is_file() and file_path.suffix.lower() in self.extensions:
                    findings.extend(self._scan_file(file_path))

        return findings

    def _scan_file(self, file_path: Path) -> list[ScanFinding]:
        """Scan a single file for dangerous patterns.

        Args:
            file_path: Path to the file to scan

        Returns:
            List of ScanFinding objects for issues found in this file
        """
        findings: list[ScanFinding] = []

        # Skip files that are too large
        try:
            if file_path.stat().st_size > self.max_file_size:
                return findings
        except OSError:
            return findings

        # Read file content
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, IOError):
            return findings

        # Scan content
        file_findings = self._scan_content(content, str(file_path))
        findings.extend(file_findings)

        return findings

    def _scan_content(self, content: str, file_path: str) -> list[ScanFinding]:
        """Scan content string for dangerous patterns.

        Args:
            content: File content to scan
            file_path: Path to the file (for reporting)

        Returns:
            List of ScanFinding objects
        """
        findings: list[ScanFinding] = []
        lines = content.split("\n")

        for pattern_name, pattern in self.patterns.items():
            regex = pattern.get_regex()
            for match in regex.finditer(content):
                # Calculate line number
                line_number = content[:match.start()].count("\n") + 1
                findings.append(
                    ScanFinding(
                        severity=pattern.severity,
                        category=pattern.category,
                        message=pattern.message,
                        file_path=file_path,
                        line_number=line_number,
                        pattern_matched=match.group(0)[:100],  # Truncate long matches
                    )
                )

        return findings

    def get_verdict(self, findings: list[ScanFinding]) -> tuple[str, str]:
        """Get the overall verdict based on findings.

        Args:
            findings: List of scan findings

        Returns:
            Tuple of (verdict, message) where verdict is one of:
            - "rejected": CRITICAL findings, block immediately
            - "pending_review": HIGH findings, needs manual review
            - "approved_with_warnings": MEDIUM findings only
            - "approved": No significant findings
        """
        if not findings:
            return ("approved", "No issues detected")

        # Check for CRITICAL findings
        critical_findings = [f for f in findings if f.severity == SeverityLevel.CRITICAL]
        if critical_findings:
            categories = set(f.category for f in critical_findings)
            return (
                "rejected",
                f"Critical security issue detected: {', '.join(categories)}",
            )

        # Check for HIGH findings
        high_findings = [f for f in findings if f.severity == SeverityLevel.HIGH]
        if high_findings:
            return (
                "pending_review",
                f"High-severity findings require manual review ({len(high_findings)} issues)",
            )

        # Check for MEDIUM findings
        medium_findings = [f for f in findings if f.severity == SeverityLevel.MEDIUM]
        if medium_findings:
            return (
                "approved_with_warnings",
                f"Asset approved with {len(medium_findings)} warning(s)",
            )

        # Only LOW findings
        return ("approved", "No significant issues detected")

    def get_summary(self, findings: list[ScanFinding]) -> dict:
        """Get a summary of findings by severity.

        Args:
            findings: List of scan findings

        Returns:
            Dictionary with counts by severity level
        """
        summary = {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "total": len(findings),
        }

        for finding in findings:
            summary[finding.severity.value] += 1

        return summary

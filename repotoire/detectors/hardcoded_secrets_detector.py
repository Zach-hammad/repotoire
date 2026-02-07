"""Hardcoded secrets detector - identifies potential security vulnerabilities.

REPO-XXX: Fast detection of hardcoded secrets, API keys, passwords, and credentials.
Replaces semgrep-based secret scanning with graph-native + file-based approach.
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Pattern, Set, Tuple

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph import FalkorDBClient
from repotoire.logging_config import get_logger
from repotoire.models import CollaborationMetadata, Finding, Severity


class HardcodedSecretsDetector(CodeSmellDetector):
    """Detects hardcoded secrets, API keys, passwords, and credentials.

    Scans source files for patterns that indicate sensitive data has been
    hardcoded rather than retrieved from environment variables or secrets
    management systems.

    Severity is CRITICAL because hardcoded secrets can lead to:
    - Credential leakage via source control
    - Unauthorized access to systems
    - Compliance violations (PCI-DSS, SOC2, etc.)
    """

    # Secret type to severity mapping
    SECRET_SEVERITY = {
        "private_key": Severity.CRITICAL,
        "aws_credentials": Severity.CRITICAL,
        "api_key": Severity.CRITICAL,
        "password": Severity.HIGH,
        "connection_string": Severity.CRITICAL,
        "jwt_token": Severity.CRITICAL,
        "oauth_token": Severity.CRITICAL,
        "generic_secret": Severity.HIGH,
        "base64_secret": Severity.MEDIUM,
    }

    # Regex patterns for secret detection
    # Format: (pattern_name, compiled_regex, secret_type, description)
    SECRET_PATTERNS: List[Tuple[str, Pattern[str], str, str]] = [
        # Private keys (very high confidence)
        (
            "rsa_private_key",
            re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----", re.IGNORECASE),
            "private_key",
            "RSA/EC/DSA private key detected",
        ),
        (
            "pgp_private_key",
            re.compile(r"-----BEGIN PGP PRIVATE KEY BLOCK-----", re.IGNORECASE),
            "private_key",
            "PGP private key detected",
        ),
        # AWS credentials
        (
            "aws_access_key_id",
            re.compile(r"""(AKIA[0-9A-Z]{16})"""),
            "aws_credentials",
            "AWS Access Key ID detected",
        ),
        (
            "aws_secret_key",
            re.compile(r"""(?:aws_secret(?:_access)?_key|secret_key)[\s]*[=:]+[\s]*['\"]?([A-Za-z0-9/+=]{40})['\"]?""", re.IGNORECASE),
            "aws_credentials",
            "AWS Secret Access Key detected",
        ),
        # Generic API keys
        (
            "api_key_assignment",
            re.compile(r"""(?:api[_-]?key|apikey|api_token|auth_token|access_token)[\s]*[=:]+[\s]*['\"]([a-zA-Z0-9_\-]{20,})['\"]""", re.IGNORECASE),
            "api_key",
            "API key assignment detected",
        ),
        (
            "bearer_token",
            re.compile(r"""['\"]Bearer\s+([a-zA-Z0-9_\-\.]{20,})['\"]""", re.IGNORECASE),
            "api_key",
            "Bearer token detected",
        ),
        (
            "authorization_header",
            re.compile(r"""['\"]Authorization['\"][\s]*:[\s]*['\"](?:Basic|Bearer)\s+([a-zA-Z0-9_\-\.=+/]{20,})['\"]""", re.IGNORECASE),
            "api_key",
            "Authorization header with token detected",
        ),
        # Passwords
        (
            "password_assignment",
            re.compile(r"""(?:password|passwd|pwd|pass|secret)[\s]*[=:]+[\s]*['\"]([^'\"]{8,})['\"]""", re.IGNORECASE),
            "password",
            "Password assignment detected",
        ),
        (
            "db_password",
            re.compile(r"""(?:db_password|database_password|mysql_pwd|postgres_password)[\s]*[=:]+[\s]*['\"]([^'\"]+)['\"]""", re.IGNORECASE),
            "password",
            "Database password detected",
        ),
        # Connection strings
        (
            "mongodb_uri",
            re.compile(r"""mongodb(?:\+srv)?://[^:]+:([^@]+)@[^\s'"]+""", re.IGNORECASE),
            "connection_string",
            "MongoDB connection string with password detected",
        ),
        (
            "postgres_uri",
            re.compile(r"""postgres(?:ql)?://[^:]+:([^@]+)@[^\s'"]+""", re.IGNORECASE),
            "connection_string",
            "PostgreSQL connection string with password detected",
        ),
        (
            "mysql_uri",
            re.compile(r"""mysql://[^:]+:([^@]+)@[^\s'"]+""", re.IGNORECASE),
            "connection_string",
            "MySQL connection string with password detected",
        ),
        (
            "redis_uri",
            re.compile(r"""redis://[^:]+:([^@]+)@[^\s'"]+""", re.IGNORECASE),
            "connection_string",
            "Redis connection string with password detected",
        ),
        (
            "jdbc_connection",
            re.compile(r"""jdbc:[^/]+//[^:]+:([^@;]+)@""", re.IGNORECASE),
            "connection_string",
            "JDBC connection string with password detected",
        ),
        # JWT tokens
        (
            "jwt_token",
            re.compile(r"""['\"]?(eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,})['\"]?"""),
            "jwt_token",
            "JWT token detected",
        ),
        # OAuth tokens
        (
            "github_token",
            re.compile(r"""(ghp_[a-zA-Z0-9]{36})"""),
            "oauth_token",
            "GitHub personal access token detected",
        ),
        (
            "github_oauth",
            re.compile(r"""(gho_[a-zA-Z0-9]{36})"""),
            "oauth_token",
            "GitHub OAuth token detected",
        ),
        (
            "gitlab_token",
            re.compile(r"""(?:gitlab[_-]?token|gl[_-]?token)[\s]*[=:]+[\s]*['\"]?(glpat-[a-zA-Z0-9_\-]{20,})['\"]?""", re.IGNORECASE),
            "oauth_token",
            "GitLab personal access token detected",
        ),
        (
            "slack_token",
            re.compile(r"""xox[baprs]-[0-9]{10,}-[a-zA-Z0-9]{10,}"""),
            "oauth_token",
            "Slack token detected",
        ),
        (
            "stripe_key",
            re.compile(r"""(?:sk|pk|rk)_(?:live|test)_[a-zA-Z0-9]{20,}"""),
            "api_key",
            "Stripe API key detected",
        ),
        (
            "sendgrid_key",
            re.compile(r"""(SG\.[a-zA-Z0-9_\-]{20,}\.[a-zA-Z0-9_\-]{20,})"""),
            "api_key",
            "SendGrid API key detected",
        ),
        (
            "twilio_key",
            re.compile(r"""SK[a-f0-9]{32}"""),
            "api_key",
            "Twilio API key detected",
        ),
        # Generic secrets
        (
            "generic_secret",
            re.compile(r"""(?:secret|private|credential|cred)[\s]*[=:]+[\s]*['\"]([a-zA-Z0-9_\-]{16,})['\"]""", re.IGNORECASE),
            "generic_secret",
            "Generic secret assignment detected",
        ),
        # Base64 encoded secrets (high entropy strings)
        (
            "base64_secret",
            re.compile(r"""['\"]([A-Za-z0-9+/]{40,}={0,2})['\"]"""),
            "base64_secret",
            "Potential base64-encoded secret detected",
        ),
    ]

    # File patterns to skip (tests, examples, docs)
    SKIP_FILE_PATTERNS = [
        r"test[s]?[/\\]",
        r"__test__",
        r"_test\.py$",
        r"test_[^/\\]+\.py$",
        r"[/\\]tests?[/\\]",
        r"[/\\]spec[s]?[/\\]",
        r"[/\\]mock[s]?[/\\]",
        r"[/\\]fixture[s]?[/\\]",
        r"[/\\]example[s]?[/\\]",
        r"[/\\]doc[s]?[/\\]",
        r"[/\\]documentation[/\\]",
        r"README",
        r"CHANGELOG",
        r"\.md$",
        r"\.rst$",
        r"\.txt$",
        r"\.lock$",
        r"node_modules[/\\]",
        r"vendor[/\\]",
        r"\.git[/\\]",
    ]

    # Line patterns to skip (false positives)
    SKIP_LINE_PATTERNS = [
        r"^\s*#",              # Comments
        r"^\s*//",             # C-style comments
        r"^\s*\*",             # Block comment lines
        r"^\s*\"\"\"",         # Docstrings
        r"^\s*'''",            # Docstrings
        r"example",            # Example values
        r"placeholder",        # Placeholder values
        r"your[_-]?api[_-]?key",  # Placeholder patterns
        r"<[^>]+>",            # Placeholder templates
        r"\$\{[^}]+\}",        # Variable interpolation
        r"process\.env",       # Environment variable access
        r"os\.environ",        # Python env access
        r"getenv",             # Environment variable access
        r"TODO",               # TODO comments
        r"FIXME",              # FIXME comments
        r"XXX",                # XXX comments
        r"test",               # Test values
        r"dummy",              # Dummy values
        r"fake",               # Fake values
        r"mock",               # Mock values
        r"sample",             # Sample values
    ]

    # Value patterns that are obvious false positives
    FALSE_POSITIVE_VALUES = [
        r"^[x\*]+$",              # Masked values (xxxx, ****)
        r"^password$",            # The word "password"
        r"^secret$",              # The word "secret"
        r"^changeme$",            # Common placeholder
        r"^replace[_-]?me$",      # Common placeholder
        r"^your[_-]",             # Your_xxx placeholders
        r"^\$\{",                 # Variable interpolation
        r"^ENV\[",                # Environment variable
        r"^\{\{",                 # Template variable
        r"^<[^>]+>$",             # Template placeholder
        r"^None$",                # None value
        r"^null$",                # Null value
        r"^undefined$",           # Undefined value
        r"^true$",                # Boolean
        r"^false$",               # Boolean
        r"^0{8,}$",               # All zeros
        r"^1{8,}$",               # All ones
        r"^test",                 # Test prefix
        r"^demo",                 # Demo prefix
        r"^abc",                  # ABC prefix (common test)
    ]

    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict[str, Any]] = None,
    ):
        """Initialize hardcoded secrets detector.

        Args:
            graph_client: FalkorDB database client
            detector_config: Optional configuration dict with settings
        """
        super().__init__(graph_client, detector_config)
        self.logger = get_logger(__name__)

        config = detector_config or {}
        self.max_findings = config.get("max_findings", 100)
        self.include_base64 = config.get("include_base64", False)  # High false positive rate
        self.source_root = config.get("source_root")

        # Compile skip patterns for efficiency
        self._skip_file_re = [re.compile(p, re.IGNORECASE) for p in self.SKIP_FILE_PATTERNS]
        self._skip_line_re = [re.compile(p, re.IGNORECASE) for p in self.SKIP_LINE_PATTERNS]
        self._false_positive_re = [re.compile(p, re.IGNORECASE) for p in self.FALSE_POSITIVE_VALUES]

    def detect(self) -> List[Finding]:
        """Detect hardcoded secrets in the codebase.

        Uses two strategies:
        1. Query string literals from the graph (if available)
        2. Scan source files directly (fallback)

        Returns:
            List of findings for hardcoded secrets
        """
        findings: List[Finding] = []
        seen_secrets: Set[str] = set()  # Dedupe by secret hash

        # Strategy 1: Try graph-based detection (fast for indexed strings)
        graph_findings = self._detect_from_graph()
        findings.extend(graph_findings)

        # Strategy 2: File-based scanning (comprehensive)
        if self.source_root:
            file_findings = self._detect_from_files(seen_secrets)
            findings.extend(file_findings)

        # Sort by severity (critical first), then by file
        severity_order = {
            Severity.CRITICAL: 0,
            Severity.HIGH: 1,
            Severity.MEDIUM: 2,
            Severity.LOW: 3,
            Severity.INFO: 4,
        }
        findings.sort(key=lambda f: (severity_order.get(f.severity, 4), f.affected_files[0] if f.affected_files else ""))

        # Limit findings
        findings = findings[:self.max_findings]

        self.logger.info(f"HardcodedSecretsDetector found {len(findings)} potential secrets")
        return findings

    def _detect_from_graph(self) -> List[Finding]:
        """Detect secrets from graph string literal nodes.

        Returns:
            List of findings from graph analysis
        """
        findings = []

        # Query string literals from the graph
        repo_filter = self._get_isolation_filter("s")
        query = f"""
        MATCH (s:StringLiteral)
        WHERE s.value IS NOT NULL {repo_filter}
        OPTIONAL MATCH (s)<-[:CONTAINS*]-(f:File)
        RETURN s.value AS value,
               s.lineStart AS line_start,
               s.lineEnd AS line_end,
               f.filePath AS file_path
        LIMIT 10000
        """

        try:
            results = self.db.execute_query(query, self._get_query_params())
        except Exception as e:
            self.logger.debug(f"Graph-based detection unavailable: {e}")
            return findings

        for row in results:
            value = row.get("value", "")
            file_path = row.get("file_path", "unknown")
            line_start = row.get("line_start")

            if self._should_skip_file(file_path):
                continue

            for pattern_name, pattern, secret_type, description in self.SECRET_PATTERNS:
                if secret_type == "base64_secret" and not self.include_base64:
                    continue

                match = pattern.search(value)
                if match:
                    # Extract the matched secret value
                    matched_value = match.group(1) if match.lastindex else match.group(0)

                    if self._is_false_positive(matched_value, value):
                        continue

                    finding = self._create_finding(
                        pattern_name=pattern_name,
                        secret_type=secret_type,
                        description=description,
                        file_path=file_path,
                        line_start=line_start,
                        line_content=value[:100],  # Truncate for safety
                        matched_value=matched_value,
                    )
                    findings.append(finding)
                    break  # One finding per string

        return findings

    def _detect_from_files(self, seen_secrets: Set[str]) -> List[Finding]:
        """Detect secrets by scanning source files.

        Args:
            seen_secrets: Set of already-found secrets (for deduplication)

        Returns:
            List of findings from file scanning
        """
        findings = []
        source_path = Path(self.source_root)

        if not source_path.exists():
            self.logger.warning(f"Source root does not exist: {self.source_root}")
            return findings

        # Incremental mode: skip unchanged files if changed_files is set
        changed_files: Optional[Set[Path]] = self.config.get("changed_files")

        # Scan supported file types
        extensions = {".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rb", ".php", ".yml", ".yaml", ".json", ".env", ".sh", ".bash"}

        for file_path in source_path.rglob("*"):
            # Skip unchanged files in incremental mode
            if changed_files is not None and file_path not in changed_files:
                continue
            if not file_path.is_file():
                continue

            if file_path.suffix.lower() not in extensions:
                continue

            relative_path = str(file_path.relative_to(source_path))

            if self._should_skip_file(relative_path):
                continue

            try:
                file_findings = self._scan_file(file_path, relative_path, seen_secrets)
                findings.extend(file_findings)

                if len(findings) >= self.max_findings:
                    break
            except Exception as e:
                self.logger.debug(f"Error scanning {file_path}: {e}")
                continue

        return findings

    def _scan_file(self, file_path: Path, relative_path: str, seen_secrets: Set[str]) -> List[Finding]:
        """Scan a single file for secrets.

        Args:
            file_path: Full path to the file
            relative_path: Relative path for reporting
            seen_secrets: Set of already-found secrets

        Returns:
            List of findings from this file
        """
        findings = []

        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return findings

        lines = content.split("\n")

        for line_num, line in enumerate(lines, start=1):
            if self._should_skip_line(line):
                continue

            for pattern_name, pattern, secret_type, description in self.SECRET_PATTERNS:
                if secret_type == "base64_secret" and not self.include_base64:
                    continue

                match = pattern.search(line)
                if match:
                    matched_value = match.group(1) if match.lastindex else match.group(0)

                    if self._is_false_positive(matched_value, line):
                        continue

                    # Dedupe by secret value hash
                    secret_hash = hash(matched_value[:20])
                    if secret_hash in seen_secrets:
                        continue
                    seen_secrets.add(secret_hash)

                    finding = self._create_finding(
                        pattern_name=pattern_name,
                        secret_type=secret_type,
                        description=description,
                        file_path=relative_path,
                        line_start=line_num,
                        line_content=line.strip()[:100],
                        matched_value=matched_value,
                    )
                    findings.append(finding)
                    break  # One finding per line

        return findings

    def _should_skip_file(self, file_path: str) -> bool:
        """Check if file should be skipped.

        Args:
            file_path: Path to check

        Returns:
            True if file should be skipped
        """
        for pattern in self._skip_file_re:
            if pattern.search(file_path):
                return True
        return False

    def _should_skip_line(self, line: str) -> bool:
        """Check if line should be skipped.

        Args:
            line: Line content to check

        Returns:
            True if line should be skipped
        """
        for pattern in self._skip_line_re:
            if pattern.search(line):
                return True
        return False

    def _is_false_positive(self, matched_value: str, full_context: str) -> bool:
        """Check if matched value is likely a false positive.

        Args:
            matched_value: The matched secret value
            full_context: Full line or context

        Returns:
            True if likely a false positive
        """
        if not matched_value or len(matched_value) < 8:
            return True

        # Check value against false positive patterns
        for pattern in self._false_positive_re:
            if pattern.search(matched_value):
                return True

        # Check for low entropy (repeated characters)
        if len(set(matched_value)) < 4:
            return True

        # Check for obvious test patterns
        context_lower = full_context.lower()
        if any(word in context_lower for word in ["example", "sample", "test", "demo", "placeholder", "mock"]):
            return True

        return False

    def _create_finding(
        self,
        pattern_name: str,
        secret_type: str,
        description: str,
        file_path: str,
        line_start: Optional[int],
        line_content: str,
        matched_value: str,
    ) -> Finding:
        """Create a finding for a detected secret.

        Args:
            pattern_name: Name of the pattern that matched
            secret_type: Type of secret (api_key, password, etc.)
            description: Human-readable description
            file_path: Path to the affected file
            line_start: Line number where secret was found
            line_content: Truncated line content (for context)
            matched_value: The matched secret value

        Returns:
            Finding object
        """
        severity = self.SECRET_SEVERITY.get(secret_type, Severity.HIGH)

        # Mask the secret value for the finding
        if len(matched_value) > 10:
            masked = matched_value[:4] + "*" * (len(matched_value) - 8) + matched_value[-4:]
        else:
            masked = "*" * len(matched_value)

        title = f"Hardcoded {secret_type.replace('_', ' ')}: {file_path}"
        if line_start:
            title += f":{line_start}"

        description_text = (
            f"{description}.\n\n"
            f"**Location:** `{file_path}`"
            + (f" (line {line_start})" if line_start else "")
            + f"\n**Pattern:** `{pattern_name}`\n"
            f"**Masked value:** `{masked}`\n\n"
            "Hardcoded secrets in source code pose severe security risks:\n"
            "- Secrets may be exposed in version control history\n"
            "- Credentials can be extracted from compiled binaries\n"
            "- Rotation requires code changes and redeployment"
        )

        recommendation = (
            "1. **Remove the secret immediately** from the source code\n"
            "2. **Rotate the credential** - assume it has been compromised\n"
            "3. **Use environment variables** or a secrets manager:\n"
            "   - `os.environ['API_KEY']` (Python)\n"
            "   - `process.env.API_KEY` (Node.js)\n"
            "   - AWS Secrets Manager, HashiCorp Vault, etc.\n"
            "4. **Scan git history** for previous commits containing secrets\n"
            "5. Consider using tools like `git-secrets` or `trufflehog` in CI/CD"
        )

        finding = Finding(
            id=f"hardcoded_secret_{pattern_name}_{hash(file_path + str(line_start))}",
            detector="HardcodedSecretsDetector",
            severity=severity,
            title=title,
            description=description_text,
            affected_nodes=[],
            affected_files=[file_path] if file_path != "unknown" else [],
            line_start=line_start,
            line_end=line_start,
            suggested_fix=recommendation,
            estimated_effort="Immediate (security critical)",
            graph_context={
                "pattern_name": pattern_name,
                "secret_type": secret_type,
                "masked_value": masked,
            },
        )

        # Add collaboration metadata for cross-detector use
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="HardcodedSecretsDetector",
            confidence=0.90 if secret_type in ("private_key", "aws_credentials", "jwt_token") else 0.75,
            evidence=[pattern_name, secret_type],
            tags=["security", "secrets", "hardcoded", secret_type],
        ))

        return finding

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity based on secret type.

        Args:
            finding: Finding to assess

        Returns:
            Severity level (typically CRITICAL or HIGH)
        """
        secret_type = finding.graph_context.get("secret_type", "generic_secret")
        return self.SECRET_SEVERITY.get(secret_type, Severity.HIGH)

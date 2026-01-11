"""Secure tool execution in isolated sandboxes for external analysis tools.

This module provides ToolExecutor for running external analysis tools (ruff, bandit,
pylint, mypy, semgrep, etc.) in E2B sandboxes, preventing credential leakage and
ensuring secrets are never exposed to external tools.

Usage:
    ```python
    from repotoire.sandbox import ToolExecutor, ToolExecutorConfig

    config = ToolExecutorConfig.from_env()
    executor = ToolExecutor(config)

    result = await executor.execute_tool(
        repo_path=Path("/path/to/repo"),
        command="ruff check --output-format=json .",
        tool_name="ruff",
    )

    if result.success:
        print(f"Tool output: {result.stdout}")
    else:
        print(f"Tool failed: {result.stderr}")
    ```

Security:
    - All tools run in isolated E2B sandbox
    - Host filesystem never exposed to tool code
    - .env files, credentials, and secrets excluded from upload
    - Configurable exclusion patterns via config
    - Detailed logging of excluded files for debugging
"""

import asyncio
import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from repotoire.logging_config import get_logger
from repotoire.sandbox.client import SandboxExecutor, CommandResult
from repotoire.sandbox.config import SandboxConfig
from repotoire.sandbox.exceptions import (
    SandboxConfigurationError,
    SandboxTimeoutError,
    SandboxExecutionError,
)

logger = get_logger(__name__)


# =============================================================================
# Sensitive File Patterns - Security Critical
# =============================================================================

DEFAULT_SENSITIVE_PATTERNS: List[str] = [
    # Environment files (may contain API keys, database passwords)
    ".env",
    ".env.*",
    "*.env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".envrc",
    # Git credentials
    ".git/config",
    ".git/credentials",
    ".gitconfig",
    ".git-credentials",
    # SSH keys and related
    ".ssh/",
    ".ssh/**",
    "*.pem",
    "*.key",
    "*.ppk",
    "id_rsa",
    "id_rsa*",
    "id_ed25519",
    "id_ed25519*",
    "id_ecdsa",
    "id_ecdsa*",
    "id_dsa",
    "id_dsa*",
    "known_hosts",
    "authorized_keys",
    # Cloud provider credentials
    ".aws/",
    ".aws/**",
    ".azure/",
    ".azure/**",
    ".gcloud/",
    ".gcloud/**",
    ".config/gcloud/",
    ".config/gcloud/**",
    "credentials.json",
    "service-account*.json",
    "gcloud-service-key*.json",
    # Kubernetes/Docker secrets
    ".kube/config",
    ".kube/**",
    ".docker/config.json",
    # Package manager tokens
    ".npmrc",
    ".pypirc",
    ".gem/credentials",
    ".yarnrc",
    ".yarnrc.yml",
    # Certificates and keystores
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.crt",
    "*.cer",
    "*.der",
    "*.keystore",
    "*.truststore",
    # Named secrets files
    "*secret*",
    "*secrets*",
    "*password*",
    "*passwords*",
    "*credential*",
    "*credentials*",
    "*token*",
    "*tokens*",
    "secrets.yaml",
    "secrets.yml",
    "secrets.json",
    "secrets.toml",
    ".secrets",
    ".secrets/",
    ".secrets/**",
    # IDE/Editor settings that may contain tokens
    ".idea/",
    ".idea/**",
    ".vscode/settings.json",
    # Terraform state (contains sensitive values)
    "*.tfstate",
    "*.tfstate.*",
    ".terraform/",
    ".terraform/**",
    # Ansible vault files
    "*vault*.yml",
    "*vault*.yaml",
    # Generic config files that often contain secrets
    "config.local.*",
    "*.local.json",
    "*.local.yaml",
    "*.local.yml",
    "*.local.toml",
    # Backup files that might contain secrets
    "*.bak",
    "*.backup",
    "*.old",
    # History files
    ".bash_history",
    ".zsh_history",
    ".python_history",
    ".psql_history",
    ".mysql_history",
    # Database files
    "*.sqlite",
    "*.sqlite3",
    "*.db",
]

# =============================================================================
# Content-Based Secret Detection Patterns - Security Critical
# =============================================================================
# These regex patterns detect secrets embedded in file contents.
# Each pattern is a tuple of (name, regex, description).

SECRET_CONTENT_PATTERNS: List[Tuple[str, re.Pattern, str]] = [
    # AWS credentials
    (
        "aws_access_key",
        re.compile(r"AKIA[0-9A-Z]{16}", re.IGNORECASE),
        "AWS Access Key ID",
    ),
    (
        "aws_secret_key",
        re.compile(
            r"(?i)aws[_\-]?secret[_\-]?(?:access[_\-]?)?key[\s]*[=:]\s*['\"]?([A-Za-z0-9/+=]{40})['\"]?",
            re.IGNORECASE,
        ),
        "AWS Secret Access Key",
    ),
    # GitHub tokens
    (
        "github_token_classic",
        re.compile(r"ghp_[A-Za-z0-9]{36,}"),
        "GitHub Personal Access Token (Classic)",
    ),
    (
        "github_token_fine",
        re.compile(r"github_pat_[A-Za-z0-9]{22}_[A-Za-z0-9]{59}"),
        "GitHub Fine-Grained Token",
    ),
    (
        "github_oauth",
        re.compile(r"gho_[A-Za-z0-9]{36}"),
        "GitHub OAuth Token",
    ),
    (
        "github_app_token",
        re.compile(r"ghu_[A-Za-z0-9]{36}|ghs_[A-Za-z0-9]{36}"),
        "GitHub App Token",
    ),
    # GitLab tokens
    (
        "gitlab_token",
        re.compile(r"glpat-[A-Za-z0-9\-_]{20,}"),
        "GitLab Personal Access Token",
    ),
    # Slack tokens
    (
        "slack_token",
        re.compile(r"xox[baprs]-[0-9]{10,13}-[0-9]{10,13}[a-zA-Z0-9-]*"),
        "Slack Token",
    ),
    (
        "slack_webhook",
        re.compile(r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[a-zA-Z0-9]+"),
        "Slack Webhook URL",
    ),
    # OpenAI API keys
    (
        "openai_key",
        re.compile(r"sk-[A-Za-z0-9]{20}T3BlbkFJ[A-Za-z0-9]{20}"),
        "OpenAI API Key (legacy)",
    ),
    (
        "openai_key_proj",
        re.compile(r"sk-proj-[A-Za-z0-9\-_]{80,}"),
        "OpenAI Project API Key",
    ),
    # Anthropic API keys
    (
        "anthropic_key",
        re.compile(r"sk-ant-[A-Za-z0-9\-_]{90,}"),
        "Anthropic API Key",
    ),
    # Stripe keys
    (
        "stripe_key",
        re.compile(r"sk_(?:live|test)_[A-Za-z0-9]{24,}"),
        "Stripe Secret Key",
    ),
    (
        "stripe_restricted",
        re.compile(r"rk_(?:live|test)_[A-Za-z0-9]{24,}"),
        "Stripe Restricted Key",
    ),
    # Twilio credentials
    (
        "twilio_key",
        re.compile(r"SK[a-f0-9]{32}"),
        "Twilio API Key",
    ),
    # SendGrid API key
    (
        "sendgrid_key",
        re.compile(r"SG\.[A-Za-z0-9\-_]{22}\.[A-Za-z0-9\-_]{43}"),
        "SendGrid API Key",
    ),
    # Google API keys
    (
        "google_api_key",
        re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
        "Google API Key",
    ),
    # Firebase
    (
        "firebase_key",
        re.compile(r"(?i)firebase[_\-]?(?:api[_\-]?)?key[\s]*[=:]\s*['\"]?([A-Za-z0-9\-_]{39})['\"]?"),
        "Firebase API Key",
    ),
    # Heroku API key
    (
        "heroku_key",
        re.compile(r"(?i)heroku[_\-]?api[_\-]?key[\s]*[=:]\s*['\"]?([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})['\"]?"),
        "Heroku API Key",
    ),
    # NPM tokens
    (
        "npm_token",
        re.compile(r"npm_[A-Za-z0-9]{36}"),
        "NPM Access Token",
    ),
    # PyPI tokens
    (
        "pypi_token",
        re.compile(r"pypi-[A-Za-z0-9\-_]{50,}"),
        "PyPI API Token",
    ),
    # JWT tokens (be careful - only match full JWTs with signature)
    (
        "jwt_token",
        re.compile(r"eyJ[A-Za-z0-9\-_]+\.eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+"),
        "JSON Web Token (JWT)",
    ),
    # Generic private keys
    (
        "private_key_rsa",
        re.compile(r"-----BEGIN (?:RSA )?PRIVATE KEY-----"),
        "RSA Private Key",
    ),
    (
        "private_key_openssh",
        re.compile(r"-----BEGIN OPENSSH PRIVATE KEY-----"),
        "OpenSSH Private Key",
    ),
    (
        "private_key_ec",
        re.compile(r"-----BEGIN EC PRIVATE KEY-----"),
        "EC Private Key",
    ),
    (
        "private_key_pgp",
        re.compile(r"-----BEGIN PGP PRIVATE KEY BLOCK-----"),
        "PGP Private Key",
    ),
    # Generic API key patterns (more likely to have false positives)
    (
        "generic_api_key",
        re.compile(
            r"(?i)(?:api[_\-]?key|apikey|api[_\-]?secret|api[_\-]?token)[\s]*[=:]\s*['\"]?([A-Za-z0-9\-_]{20,})['\"]?"
        ),
        "Generic API Key Assignment",
    ),
    # Generic password assignments (high value but also higher false positives)
    (
        "generic_password",
        re.compile(
            r"(?i)(?:password|passwd|pwd|secret)[\s]*[=:]\s*['\"]([^'\"]{8,})['\"]"
        ),
        "Generic Password Assignment",
    ),
    # Database connection strings
    (
        "postgres_uri",
        re.compile(
            r"postgres(?:ql)?://[^:]+:[^@]+@[^/]+/[^\s\"']+",
            re.IGNORECASE,
        ),
        "PostgreSQL Connection String with Password",
    ),
    (
        "mysql_uri",
        re.compile(
            r"mysql://[^:]+:[^@]+@[^/]+/[^\s\"']+",
            re.IGNORECASE,
        ),
        "MySQL Connection String with Password",
    ),
    (
        "mongodb_uri",
        re.compile(
            r"mongodb(?:\+srv)?://[^:]+:[^@]+@[^\s\"']+",
            re.IGNORECASE,
        ),
        "MongoDB Connection String with Password",
    ),
    (
        "redis_uri",
        re.compile(
            r"redis://[^:]*:[^@]+@[^\s\"']+",
            re.IGNORECASE,
        ),
        "Redis Connection String with Password",
    ),
    # E2B API key (our own service!)
    (
        "e2b_key",
        re.compile(r"e2b_[A-Za-z0-9]{32,}"),
        "E2B API Key",
    ),
    # Linear API key
    (
        "linear_key",
        re.compile(r"lin_api_[A-Za-z0-9]{40,}"),
        "Linear API Key",
    ),
    # Datadog API key
    (
        "datadog_key",
        re.compile(r"(?i)(?:dd|datadog)[_\-]?(?:api[_\-]?)?key[\s]*[=:]\s*['\"]?([a-f0-9]{32})['\"]?"),
        "Datadog API Key",
    ),
    # Basic auth in URLs
    (
        "basic_auth_url",
        re.compile(r"https?://[^:]+:[^@]+@[^\s\"']+"),
        "URL with Basic Auth Credentials",
    ),
]

# File extensions to scan for secrets (skip binary files)
SCANNABLE_EXTENSIONS: Set[str] = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".config", ".properties",
    ".env", ".sh", ".bash", ".zsh", ".fish",
    ".rb", ".php", ".java", ".go", ".rs", ".c", ".cpp", ".h", ".hpp",
    ".cs", ".swift", ".kt", ".scala", ".groovy", ".gradle",
    ".tf", ".tfvars", ".hcl",
    ".xml", ".html", ".htm", ".vue", ".svelte",
    ".md", ".txt", ".rst", ".log",
    ".sql", ".prisma",
    ".dockerfile", "",  # Empty string for files without extension (like Dockerfile)
}

# Max file size to scan for secrets (skip large files for performance)
MAX_CONTENT_SCAN_SIZE_BYTES: int = 1024 * 1024  # 1 MB

# Patterns to always include (tool config files needed for analysis)
DEFAULT_INCLUDE_PATTERNS: List[str] = [
    # Python tooling configs
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    ".ruff.toml",
    "ruff.toml",
    ".bandit",
    ".pylintrc",
    "pylintrc",
    "mypy.ini",
    ".mypy.ini",
    ".semgrepignore",
    "semgrep.yaml",
    ".semgrep.yaml",
    ".jscpd.json",
    ".vulture",
    # Pytest config
    "pytest.ini",
    "conftest.py",
    "tox.ini",
    # General project files
    "requirements.txt",
    "requirements*.txt",
    "constraints.txt",
    "Pipfile",
    "Pipfile.lock",
    "poetry.lock",
    "uv.lock",
]

# Non-source files to exclude for performance (not security)
DEFAULT_EXCLUDE_NON_SOURCE: List[str] = [
    # Git directory
    ".git/",
    ".git/**",
    # Python caches
    "__pycache__/",
    "**/__pycache__/",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    # Virtual environments
    ".venv/",
    "venv/",
    "env/",
    ".virtualenv/",
    # Node.js
    "node_modules/",
    "node_modules/**",
    # Test/build caches
    ".tox/",
    ".nox/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".cache/",
    # Coverage
    "htmlcov/",
    ".coverage",
    "coverage.xml",
    # Build artifacts
    "*.egg-info/",
    "dist/",
    "build/",
    # Temp files
    "*.log",
    "*.tmp",
    "*.swp",
    "*.swo",
    "*~",
]


# =============================================================================
# Tool Executor Result
# =============================================================================


@dataclass
class ToolExecutorResult:
    """Result of tool execution in sandbox.

    Attributes:
        success: Whether tool completed successfully (exit_code == 0)
        stdout: Standard output from tool
        stderr: Standard error from tool
        exit_code: Exit code from tool
        duration_ms: Total execution time in milliseconds
        tool_name: Name of the tool that was executed
        files_uploaded: Number of files uploaded to sandbox
        files_excluded: Number of files excluded (sensitive patterns)
        excluded_patterns_matched: Which patterns caused exclusions
        sandbox_id: ID of the sandbox instance used
        timed_out: Whether execution was terminated due to timeout
    """

    success: bool
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    tool_name: str

    # Upload statistics
    files_uploaded: int = 0
    files_excluded: int = 0
    excluded_patterns_matched: List[str] = field(default_factory=list)

    # Execution metadata
    sandbox_id: Optional[str] = None
    timed_out: bool = False

    @property
    def summary(self) -> str:
        """Generate human-readable summary of tool execution."""
        if self.timed_out:
            return f"{self.tool_name} timed out after {self.duration_ms}ms"

        status = "SUCCESS" if self.success else "FAILED"
        return (
            f"{self.tool_name} {status}: "
            f"{self.files_uploaded} files analyzed, "
            f"{self.files_excluded} files excluded for security"
        )


# =============================================================================
# Tool Executor Configuration
# =============================================================================


@dataclass
class ToolExecutorConfig:
    """Configuration for tool execution in sandbox.

    Attributes:
        sandbox_config: Underlying E2B sandbox configuration
        tool_timeout_seconds: Timeout for tool execution (default: 300)
        sensitive_patterns: File patterns to exclude for security
        include_patterns: File patterns to always include (tool configs)
        exclude_non_source: Patterns for non-source files to exclude
        working_dir: Working directory inside sandbox (default: /code)
        enabled: Whether sandbox tool execution is enabled
        fallback_local: Whether to fall back to local execution when sandbox unavailable
        enable_content_scanning: Whether to scan file contents for secrets (default: true)
    """

    sandbox_config: SandboxConfig
    tool_timeout_seconds: int = 300
    sensitive_patterns: List[str] = field(default_factory=list)
    include_patterns: List[str] = field(default_factory=list)
    exclude_non_source: List[str] = field(default_factory=list)
    working_dir: str = "/code"
    enabled: bool = True
    fallback_local: bool = True  # Allow local fallback with warning
    enable_content_scanning: bool = True  # Scan file contents for secrets

    def __post_init__(self):
        """Merge default patterns with custom patterns."""
        # Merge sensitive patterns
        all_sensitive = set(DEFAULT_SENSITIVE_PATTERNS)
        all_sensitive.update(self.sensitive_patterns)
        self.sensitive_patterns = list(all_sensitive)

        # Merge include patterns
        all_include = set(DEFAULT_INCLUDE_PATTERNS)
        all_include.update(self.include_patterns)
        self.include_patterns = list(all_include)

        # Merge non-source patterns
        all_exclude = set(DEFAULT_EXCLUDE_NON_SOURCE)
        all_exclude.update(self.exclude_non_source)
        self.exclude_non_source = list(all_exclude)

    @classmethod
    def from_env(cls) -> "ToolExecutorConfig":
        """Create configuration from environment variables.

        Environment Variables:
            TOOL_TIMEOUT_SECONDS: Tool execution timeout (default: 300)
            SANDBOX_TOOLS_ENABLED: Enable sandbox tool execution (default: true)
            SANDBOX_FALLBACK_LOCAL: Allow local fallback (default: true)
            SANDBOX_EXCLUDE_PATTERNS: Comma-separated additional sensitive patterns
            SANDBOX_INCLUDE_PATTERNS: Comma-separated additional include patterns
            SANDBOX_CONTENT_SCANNING: Enable content-based secret scanning (default: true)
        """
        sandbox_config = SandboxConfig.from_env()

        tool_timeout = int(os.getenv("TOOL_TIMEOUT_SECONDS", "300"))

        enabled_str = os.getenv("SANDBOX_TOOLS_ENABLED", "true").lower()
        enabled = enabled_str in ("true", "1", "yes")

        fallback_str = os.getenv("SANDBOX_FALLBACK_LOCAL", "true").lower()
        fallback_local = fallback_str in ("true", "1", "yes")

        content_scanning_str = os.getenv("SANDBOX_CONTENT_SCANNING", "true").lower()
        enable_content_scanning = content_scanning_str in ("true", "1", "yes")

        # Parse additional patterns
        sensitive_str = os.getenv("SANDBOX_EXCLUDE_PATTERNS", "")
        sensitive_patterns = [p.strip() for p in sensitive_str.split(",") if p.strip()]

        include_str = os.getenv("SANDBOX_INCLUDE_PATTERNS", "")
        include_patterns = [p.strip() for p in include_str.split(",") if p.strip()]

        return cls(
            sandbox_config=sandbox_config,
            tool_timeout_seconds=tool_timeout,
            sensitive_patterns=sensitive_patterns,
            include_patterns=include_patterns,
            enabled=enabled,
            fallback_local=fallback_local,
            enable_content_scanning=enable_content_scanning,
        )


# =============================================================================
# Secret File Filter
# =============================================================================


@dataclass
class SecretScanResult:
    """Result of scanning a file for secrets.

    Attributes:
        has_secrets: Whether secrets were detected
        secrets_found: List of (secret_type, line_number, description) tuples
        file_path: Path to the scanned file
    """

    has_secrets: bool
    secrets_found: List[Tuple[str, int, str]]
    file_path: str


class SecretFileFilter:
    """Filter files for upload to sandbox, excluding sensitive files.

    This filter excludes:
    1. Sensitive files (credentials, secrets, tokens) - by filename pattern
    2. Files containing embedded secrets - by content scanning
    3. Non-source files (caches, build artifacts) - for performance

    While preserving:
    - Tool configuration files needed for analysis
    - Source code files without embedded secrets

    Security:
        Content-based scanning catches secrets that filename patterns miss,
        such as API keys hardcoded in source files or config files with
        non-standard names.
    """

    def __init__(
        self,
        sensitive_patterns: List[str],
        include_patterns: List[str],
        exclude_non_source: List[str],
        enable_content_scanning: bool = True,
        content_patterns: Optional[List[Tuple[str, re.Pattern, str]]] = None,
    ):
        """Initialize with filtering patterns.

        Args:
            sensitive_patterns: Patterns for sensitive files to exclude
            include_patterns: Patterns for files to always include
            exclude_non_source: Patterns for non-source files to exclude
            enable_content_scanning: Whether to scan file contents for secrets
            content_patterns: Custom content patterns (default: SECRET_CONTENT_PATTERNS)
        """
        self.sensitive_patterns = sensitive_patterns
        self.include_patterns = include_patterns
        self.exclude_non_source = exclude_non_source
        self.enable_content_scanning = enable_content_scanning
        self.content_patterns = content_patterns or SECRET_CONTENT_PATTERNS
        self._excluded_by_pattern: Dict[str, int] = {}
        self._excluded_by_content: Dict[str, List[Tuple[str, int, str]]] = {}
        self._content_scan_cache: Dict[str, SecretScanResult] = {}

    def should_include(self, path: Path, relative_to: Path) -> bool:
        """Check if file should be included in upload.

        Performs both filename pattern matching and content-based secret scanning.

        Args:
            path: Absolute path to file
            relative_to: Repository root to make relative paths

        Returns:
            True if file should be uploaded, False to exclude
        """
        try:
            relative_path = path.relative_to(relative_to)
            rel_str = str(relative_path)
            rel_parts = relative_path.parts
        except ValueError:
            return False

        # Check if file matches include patterns (always upload these)
        # NOTE: Even included files get content-scanned for secrets
        is_include_pattern_match = False
        for pattern in self.include_patterns:
            if self._matches_pattern(rel_str, path.name, rel_parts, pattern):
                is_include_pattern_match = True
                break

        # Check if file matches sensitive patterns (security exclusion)
        for pattern in self.sensitive_patterns:
            if self._matches_pattern(rel_str, path.name, rel_parts, pattern):
                self._excluded_by_pattern[pattern] = (
                    self._excluded_by_pattern.get(pattern, 0) + 1
                )
                return False

        # Check if file matches non-source patterns (performance exclusion)
        for pattern in self.exclude_non_source:
            if self._matches_pattern(rel_str, path.name, rel_parts, pattern):
                return False

        # Content-based secret scanning (even for include-pattern matches)
        if self.enable_content_scanning and path.is_file():
            scan_result = self.scan_content_for_secrets(path)
            if scan_result.has_secrets:
                # Log the secrets found
                self._excluded_by_content[rel_str] = scan_result.secrets_found
                logger.warning(
                    f"Excluding file with embedded secrets: {rel_str}",
                    extra={
                        "secrets_found": [
                            {"type": s[0], "line": s[1], "desc": s[2]}
                            for s in scan_result.secrets_found
                        ]
                    },
                )
                return False

        return True

    def scan_content_for_secrets(self, path: Path) -> SecretScanResult:
        """Scan file contents for embedded secrets.

        Uses regex patterns to detect API keys, passwords, tokens, private keys,
        and other sensitive values in file contents.

        Args:
            path: Path to file to scan

        Returns:
            SecretScanResult with detection results
        """
        path_str = str(path)

        # Check cache first
        if path_str in self._content_scan_cache:
            return self._content_scan_cache[path_str]

        # Skip files that are too large
        try:
            file_size = path.stat().st_size
            if file_size > MAX_CONTENT_SCAN_SIZE_BYTES:
                result = SecretScanResult(
                    has_secrets=False, secrets_found=[], file_path=path_str
                )
                self._content_scan_cache[path_str] = result
                return result
        except OSError:
            result = SecretScanResult(
                has_secrets=False, secrets_found=[], file_path=path_str
            )
            self._content_scan_cache[path_str] = result
            return result

        # Skip non-scannable file types
        suffix = path.suffix.lower()
        if suffix not in SCANNABLE_EXTENSIONS:
            result = SecretScanResult(
                has_secrets=False, secrets_found=[], file_path=path_str
            )
            self._content_scan_cache[path_str] = result
            return result

        # Read and scan file contents
        secrets_found: List[Tuple[str, int, str]] = []

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()

            for line_num, line in enumerate(lines, start=1):
                # Skip comments (basic heuristic to reduce false positives)
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("//"):
                    # Still scan for high-confidence patterns even in comments
                    # (real secrets in comments are still secrets!)
                    pass

                for pattern_name, pattern, description in self.content_patterns:
                    if pattern.search(line):
                        # Avoid duplicate detections on same line
                        existing = [s for s in secrets_found if s[1] == line_num and s[0] == pattern_name]
                        if not existing:
                            secrets_found.append((pattern_name, line_num, description))

        except (OSError, UnicodeDecodeError) as e:
            logger.debug(f"Could not scan {path_str} for secrets: {e}")
            result = SecretScanResult(
                has_secrets=False, secrets_found=[], file_path=path_str
            )
            self._content_scan_cache[path_str] = result
            return result

        result = SecretScanResult(
            has_secrets=len(secrets_found) > 0,
            secrets_found=secrets_found,
            file_path=path_str,
        )
        self._content_scan_cache[path_str] = result
        return result

    def _matches_pattern(
        self, rel_str: str, filename: str, rel_parts: tuple, pattern: str
    ) -> bool:
        """Check if path matches a glob pattern.

        Args:
            rel_str: Relative path as string
            filename: Just the filename
            rel_parts: Tuple of path components
            pattern: Glob pattern to match

        Returns:
            True if pattern matches
        """
        # Handle directory patterns (ending with /)
        if pattern.endswith("/"):
            dir_pattern = pattern.rstrip("/")
            # Check if any parent directory matches
            for part in rel_parts[:-1]:
                if fnmatch.fnmatch(part, dir_pattern):
                    return True
            # Check the first component
            if rel_parts and fnmatch.fnmatch(rel_parts[0], dir_pattern):
                return True

        # Handle ** patterns (recursive glob)
        elif "**" in pattern:
            if fnmatch.fnmatch(rel_str, pattern):
                return True

        # Handle simple patterns
        else:
            # Match against filename
            if fnmatch.fnmatch(filename, pattern):
                return True
            # Match against full relative path
            if fnmatch.fnmatch(rel_str, pattern):
                return True

        return False

    def filter_files(self, repo_path: Path) -> tuple[List[Path], List[str]]:
        """Get list of files to upload and patterns that caused exclusions.

        Args:
            repo_path: Path to repository root

        Returns:
            Tuple of (files to upload, patterns/reasons that matched excluded files)
        """
        files = []
        repo_path = repo_path.resolve()
        self._excluded_by_pattern = {}
        self._excluded_by_content = {}
        self._content_scan_cache = {}

        for path in repo_path.rglob("*"):
            if path.is_file() and self.should_include(path, repo_path):
                files.append(path)

        # Get unique patterns that caused exclusions (both filename and content)
        matched_patterns = list(self._excluded_by_pattern.keys())

        # Add content-based exclusion reasons
        for rel_path, secrets in self._excluded_by_content.items():
            for secret_type, _, desc in secrets:
                pattern_key = f"content:{secret_type}"
                if pattern_key not in matched_patterns:
                    matched_patterns.append(pattern_key)

        return files, matched_patterns

    def get_exclusion_stats(self) -> Dict[str, int]:
        """Get statistics on which patterns caused exclusions.

        Returns:
            Dictionary mapping patterns to exclusion counts.
            Content-based patterns are prefixed with 'content:'.
        """
        stats = dict(self._excluded_by_pattern)

        # Add content-based exclusion stats
        content_stats: Dict[str, int] = {}
        for rel_path, secrets in self._excluded_by_content.items():
            for secret_type, _, _ in secrets:
                key = f"content:{secret_type}"
                content_stats[key] = content_stats.get(key, 0) + 1

        stats.update(content_stats)
        return stats

    def get_content_exclusion_details(self) -> Dict[str, List[Tuple[str, int, str]]]:
        """Get detailed information about content-based exclusions.

        Returns:
            Dictionary mapping file paths to list of (secret_type, line_number, description)
        """
        return dict(self._excluded_by_content)

    def clear_cache(self) -> None:
        """Clear the content scan cache.

        Call this when re-scanning the same repository with potentially changed files.
        """
        self._content_scan_cache = {}


# =============================================================================
# Tool Executor
# =============================================================================


class ToolExecutor:
    """Execute external analysis tools in isolated E2B sandbox.

    This class provides secure tool execution by:
    1. Filtering out sensitive files before upload
    2. Uploading only safe repository contents to sandbox
    3. Executing analysis tools in isolated environment
    4. Returning tool output without exposing host secrets

    Security Properties:
    - Host filesystem never fully exposed to tools
    - Secrets (.env, credentials, keys) excluded from upload
    - Only explicitly safe files available in sandbox
    - Detailed logging of excluded files for auditing
    """

    def __init__(self, config: ToolExecutorConfig):
        """Initialize tool executor.

        Args:
            config: Tool executor configuration
        """
        self.config = config
        self._file_filter = SecretFileFilter(
            sensitive_patterns=config.sensitive_patterns,
            include_patterns=config.include_patterns,
            exclude_non_source=config.exclude_non_source,
            enable_content_scanning=config.enable_content_scanning,
        )

    async def execute_tool(
        self,
        repo_path: Path,
        command: str,
        tool_name: str,
        timeout: Optional[int] = None,
        env_vars: Optional[Dict[str, str]] = None,
    ) -> ToolExecutorResult:
        """Execute analysis tool in isolated sandbox.

        Args:
            repo_path: Path to repository root
            command: Tool command to execute
            tool_name: Name of the tool (for logging)
            timeout: Tool timeout in seconds (default: from config)
            env_vars: Environment variables to inject into sandbox

        Returns:
            ToolExecutorResult with execution details

        Raises:
            SandboxConfigurationError: If E2B is not configured and fallback disabled
            SandboxTimeoutError: If tool exceeds timeout
            SandboxExecutionError: If sandbox operations fail
        """
        timeout = timeout or self.config.tool_timeout_seconds
        env_vars = env_vars or {}

        repo_path = Path(repo_path).resolve()
        if not repo_path.is_dir():
            raise ValueError(f"Repository path is not a directory: {repo_path}")

        # Check if sandbox is available
        if not self.config.enabled:
            logger.info(f"Sandbox disabled, running {tool_name} locally")
            return await self._execute_local(repo_path, command, tool_name, timeout)

        if not self.config.sandbox_config.is_configured:
            if self.config.fallback_local:
                logger.warning(
                    f"E2B API key not configured, falling back to local execution for {tool_name}. "
                    "WARNING: Secrets may be exposed to the tool."
                )
                return await self._execute_local(repo_path, command, tool_name, timeout)
            else:
                raise SandboxConfigurationError(
                    "E2B API key required for sandbox tool execution",
                    suggestion="Set E2B_API_KEY or enable SANDBOX_FALLBACK_LOCAL",
                )

        logger.info(f"Starting sandbox execution for {tool_name}")

        sandbox_id: Optional[str] = None
        start_time = asyncio.get_event_loop().time()

        try:
            async with SandboxExecutor(self.config.sandbox_config) as sandbox:
                sandbox_id = sandbox._sandbox_id

                # Step 1: Upload repository files (filtered)
                files_uploaded, files_excluded, patterns_matched = (
                    await self._upload_repository(sandbox, repo_path)
                )

                # Step 2: Set environment variables (if any)
                if env_vars:
                    await self._set_env_vars(sandbox, env_vars)

                # Step 3: Execute tool
                tool_result = await self._execute_command(
                    sandbox, command, timeout
                )

                duration_ms = int(
                    (asyncio.get_event_loop().time() - start_time) * 1000
                )

                return ToolExecutorResult(
                    success=tool_result.exit_code == 0,
                    stdout=tool_result.stdout,
                    stderr=tool_result.stderr,
                    exit_code=tool_result.exit_code,
                    duration_ms=duration_ms,
                    tool_name=tool_name,
                    files_uploaded=files_uploaded,
                    files_excluded=files_excluded,
                    excluded_patterns_matched=patterns_matched,
                    sandbox_id=sandbox_id,
                    timed_out=False,
                )

        except SandboxTimeoutError:
            duration_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
            logger.warning(f"{tool_name} execution timed out after {timeout}s")

            return ToolExecutorResult(
                success=False,
                stdout="",
                stderr=f"Tool execution timed out after {timeout} seconds",
                exit_code=-1,
                duration_ms=duration_ms,
                tool_name=tool_name,
                sandbox_id=sandbox_id,
                timed_out=True,
            )

        except Exception as e:
            duration_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
            logger.error(f"{tool_name} execution failed: {e}", exc_info=True)

            # Re-raise sandbox-specific exceptions
            if isinstance(e, (SandboxConfigurationError, SandboxExecutionError)):
                raise

            raise SandboxExecutionError(
                f"Tool execution failed: {e}",
                sandbox_id=sandbox_id,
                operation="execute_tool",
            )

    async def _execute_local(
        self,
        repo_path: Path,
        command: str,
        tool_name: str,
        timeout: int,
    ) -> ToolExecutorResult:
        """Execute tool locally using async subprocess (fallback when sandbox unavailable).

        WARNING: This exposes the full repository including secrets to the tool.

        Uses asyncio.create_subprocess_shell to avoid blocking the event loop
        during subprocess execution.

        Args:
            repo_path: Path to repository
            command: Tool command to execute
            tool_name: Name of the tool
            timeout: Timeout in seconds

        Returns:
            ToolExecutorResult with execution details
        """
        import time

        start_time = time.time()

        try:
            # Use async subprocess to avoid blocking the event loop
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=repo_path,
            )

            try:
                # Wait for process with timeout
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
                stdout = stdout_bytes.decode("utf-8", errors="replace")
                stderr = stderr_bytes.decode("utf-8", errors="replace")
                returncode = process.returncode or 0

            except asyncio.TimeoutError:
                # Kill the process on timeout
                process.kill()
                await process.wait()  # Clean up zombie process
                duration_ms = int((time.time() - start_time) * 1000)
                return ToolExecutorResult(
                    success=False,
                    stdout="",
                    stderr=f"Local tool execution timed out after {timeout} seconds",
                    exit_code=-1,
                    duration_ms=duration_ms,
                    tool_name=tool_name,
                    timed_out=True,
                )

            duration_ms = int((time.time() - start_time) * 1000)

            return ToolExecutorResult(
                success=returncode == 0,
                stdout=stdout,
                stderr=stderr,
                exit_code=returncode,
                duration_ms=duration_ms,
                tool_name=tool_name,
                files_uploaded=0,
                files_excluded=0,
                excluded_patterns_matched=[],
                sandbox_id=None,
                timed_out=False,
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(f"Local tool execution failed: {e}", exc_info=True)
            return ToolExecutorResult(
                success=False,
                stdout="",
                stderr=f"Local tool execution failed: {e}",
                exit_code=-1,
                duration_ms=duration_ms,
                tool_name=tool_name,
                timed_out=False,
            )

    async def _upload_repository(
        self, sandbox: SandboxExecutor, repo_path: Path
    ) -> tuple[int, int, List[str]]:
        """Upload repository files to sandbox (filtered for security).

        Args:
            sandbox: Active sandbox executor
            repo_path: Path to repository root

        Returns:
            Tuple of (files_uploaded, files_excluded, patterns_matched)
        """
        logger.info(f"Scanning repository for upload: {repo_path}")

        # Filter files (includes both filename and content-based filtering)
        files, matched_patterns = self._file_filter.filter_files(repo_path)
        exclusion_stats = self._file_filter.get_exclusion_stats()
        content_exclusions = self._file_filter.get_content_exclusion_details()

        # Calculate excluded count
        total_files = sum(1 for _ in repo_path.rglob("*") if _.is_file())
        files_excluded = total_files - len(files)

        # Log filename-pattern exclusions
        filename_patterns = {k: v for k, v in exclusion_stats.items() if not k.startswith("content:")}
        content_patterns = {k: v for k, v in exclusion_stats.items() if k.startswith("content:")}

        if filename_patterns:
            logger.info(
                f"Excluded {sum(filename_patterns.values())} files by filename pattern",
                extra={"exclusion_patterns": filename_patterns},
            )
            for pattern, count in filename_patterns.items():
                logger.debug(f"  Pattern '{pattern}' matched {count} files")

        # Log content-based exclusions with more detail
        if content_patterns:
            logger.warning(
                f"Excluded {len(content_exclusions)} files with embedded secrets",
                extra={"content_exclusion_types": content_patterns},
            )
            for file_path, secrets in content_exclusions.items():
                secret_types = [s[0] for s in secrets]
                logger.info(
                    f"  File '{file_path}' contains: {', '.join(set(secret_types))}"
                )

        total_size = sum(f.stat().st_size for f in files)
        logger.info(
            f"Uploading {len(files)} files ({total_size / 1024 / 1024:.1f} MB) to sandbox"
        )

        # Upload files maintaining directory structure
        loop = asyncio.get_event_loop()

        for file_path in files:
            relative_path = file_path.relative_to(repo_path)
            sandbox_path = f"{self.config.working_dir}/{relative_path}"

            try:
                # Create parent directories
                parent_dir = str(Path(sandbox_path).parent)
                if parent_dir != self.config.working_dir:
                    await sandbox.execute_command(f"mkdir -p {parent_dir}")

                # Read and upload file
                content = file_path.read_text(encoding="utf-8", errors="replace")

                await loop.run_in_executor(
                    None,
                    lambda p=sandbox_path, c=content: sandbox._sandbox.files.write(
                        p, c
                    ),
                )

            except UnicodeDecodeError:
                # Binary file - read as bytes
                content_bytes = file_path.read_bytes()
                await loop.run_in_executor(
                    None,
                    lambda p=sandbox_path, c=content_bytes: sandbox._sandbox.files.write(
                        p, c
                    ),
                )

            except Exception as e:
                logger.warning(f"Failed to upload {relative_path}: {e}")

        logger.info(f"Repository uploaded to sandbox at {self.config.working_dir}")
        return len(files), files_excluded, matched_patterns

    async def _set_env_vars(
        self, sandbox: SandboxExecutor, env_vars: Dict[str, str]
    ) -> None:
        """Set environment variables in sandbox.

        Args:
            sandbox: Active sandbox executor
            env_vars: Environment variables to set
        """
        if not env_vars:
            return

        logger.debug(f"Setting {len(env_vars)} environment variables in sandbox")

        # Create export commands
        export_commands = []
        for key, value in env_vars.items():
            # Escape single quotes in value
            escaped_value = value.replace("'", "'\\''")
            export_commands.append(f"export {key}='{escaped_value}'")

        # Write to .bashrc so they persist
        env_script = "\n".join(export_commands)
        await sandbox.execute_command(f"echo '{env_script}' >> ~/.bashrc")

    async def _execute_command(
        self, sandbox: SandboxExecutor, command: str, timeout: int
    ) -> CommandResult:
        """Execute tool command in sandbox.

        Args:
            sandbox: Active sandbox executor
            command: Tool command to run
            timeout: Timeout in seconds

        Returns:
            CommandResult from tool execution
        """
        logger.info(f"Running tool command: {command}")

        # Build full command with working directory
        full_command = f"cd {self.config.working_dir} && {command}"

        return await sandbox.execute_command(full_command, timeout=timeout)


# =============================================================================
# Synchronous Wrapper for Detector Integration
# =============================================================================


def run_tool_sync(
    repo_path: Path,
    command: str,
    tool_name: str,
    timeout: Optional[int] = None,
    config: Optional[ToolExecutorConfig] = None,
) -> ToolExecutorResult:
    """Synchronous wrapper for tool execution.

    This provides a simple sync interface for detector integration.

    Args:
        repo_path: Path to repository
        command: Tool command to execute
        tool_name: Name of the tool
        timeout: Tool timeout in seconds
        config: Tool executor config (default: from environment)

    Returns:
        ToolExecutorResult with execution details
    """
    config = config or ToolExecutorConfig.from_env()
    executor = ToolExecutor(config)

    return asyncio.run(
        executor.execute_tool(
            repo_path=repo_path,
            command=command,
            tool_name=tool_name,
            timeout=timeout,
        )
    )

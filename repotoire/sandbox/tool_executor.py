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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

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
    """

    sandbox_config: SandboxConfig
    tool_timeout_seconds: int = 300
    sensitive_patterns: List[str] = field(default_factory=list)
    include_patterns: List[str] = field(default_factory=list)
    exclude_non_source: List[str] = field(default_factory=list)
    working_dir: str = "/code"
    enabled: bool = True
    fallback_local: bool = True  # Allow local fallback with warning

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
        """
        sandbox_config = SandboxConfig.from_env()

        tool_timeout = int(os.getenv("TOOL_TIMEOUT_SECONDS", "300"))

        enabled_str = os.getenv("SANDBOX_TOOLS_ENABLED", "true").lower()
        enabled = enabled_str in ("true", "1", "yes")

        fallback_str = os.getenv("SANDBOX_FALLBACK_LOCAL", "true").lower()
        fallback_local = fallback_str in ("true", "1", "yes")

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
        )


# =============================================================================
# Secret File Filter
# =============================================================================


class SecretFileFilter:
    """Filter files for upload to sandbox, excluding sensitive files.

    This filter excludes:
    1. Sensitive files (credentials, secrets, tokens)
    2. Non-source files (caches, build artifacts)

    While preserving:
    - Tool configuration files needed for analysis
    - Source code files
    """

    def __init__(
        self,
        sensitive_patterns: List[str],
        include_patterns: List[str],
        exclude_non_source: List[str],
    ):
        """Initialize with filtering patterns.

        Args:
            sensitive_patterns: Patterns for sensitive files to exclude
            include_patterns: Patterns for files to always include
            exclude_non_source: Patterns for non-source files to exclude
        """
        self.sensitive_patterns = sensitive_patterns
        self.include_patterns = include_patterns
        self.exclude_non_source = exclude_non_source
        self._excluded_by_pattern: Dict[str, int] = {}

    def should_include(self, path: Path, relative_to: Path) -> bool:
        """Check if file should be included in upload.

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
        for pattern in self.include_patterns:
            if self._matches_pattern(rel_str, path.name, rel_parts, pattern):
                return True

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

        return True

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
            Tuple of (files to upload, patterns that matched excluded files)
        """
        files = []
        repo_path = repo_path.resolve()
        self._excluded_by_pattern = {}

        for path in repo_path.rglob("*"):
            if path.is_file() and self.should_include(path, repo_path):
                files.append(path)

        # Get unique patterns that caused exclusions
        matched_patterns = list(self._excluded_by_pattern.keys())

        return files, matched_patterns

    def get_exclusion_stats(self) -> Dict[str, int]:
        """Get statistics on which patterns caused exclusions.

        Returns:
            Dictionary mapping patterns to exclusion counts
        """
        return dict(self._excluded_by_pattern)


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
        """Execute tool locally (fallback when sandbox unavailable).

        WARNING: This exposes the full repository including secrets to the tool.

        Args:
            repo_path: Path to repository
            command: Tool command to execute
            tool_name: Name of the tool
            timeout: Timeout in seconds

        Returns:
            ToolExecutorResult with execution details
        """
        import subprocess
        import time

        start_time = time.time()

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=repo_path,
                timeout=timeout,
            )

            duration_ms = int((time.time() - start_time) * 1000)

            return ToolExecutorResult(
                success=result.returncode == 0,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                duration_ms=duration_ms,
                tool_name=tool_name,
                files_uploaded=0,
                files_excluded=0,
                excluded_patterns_matched=[],
                sandbox_id=None,
                timed_out=False,
            )

        except subprocess.TimeoutExpired:
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

        # Filter files
        files, matched_patterns = self._file_filter.filter_files(repo_path)
        exclusion_stats = self._file_filter.get_exclusion_stats()

        # Calculate excluded count
        total_files = sum(1 for _ in repo_path.rglob("*") if _.is_file())
        files_excluded = total_files - len(files)

        # Log exclusion details
        if exclusion_stats:
            logger.info(
                f"Excluded {files_excluded} files for security",
                extra={"exclusion_patterns": exclusion_stats},
            )
            for pattern, count in exclusion_stats.items():
                logger.debug(f"  Pattern '{pattern}' matched {count} files")

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

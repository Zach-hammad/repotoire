"""Secure test execution in isolated sandboxes.

This module provides TestExecutor for running tests in E2B sandboxes,
preventing malicious auto-fix code from accessing host resources.

Usage:
    ```python
    from repotoire.sandbox import TestExecutor, TestExecutorConfig

    config = TestExecutorConfig.from_env()
    executor = TestExecutor(config)

    result = await executor.run_tests(
        repo_path=Path("/path/to/repo"),
        command="pytest tests/ -v",
        env_vars={"DATABASE_URL": "postgresql://test:test@localhost/test"},
    )

    if result.success:
        print(f"Tests passed! {result.tests_passed}/{result.tests_total}")
    else:
        print(f"Tests failed: {result.stderr}")
    ```

Security:
    - All tests run in isolated E2B sandbox
    - Host filesystem never exposed to test code
    - .env files and secrets excluded from upload
    - Only explicitly provided env vars injected
    - Timeout enforcement prevents runaway tests
"""

import asyncio
import fnmatch
import os
import re
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
# Default File Exclusion Patterns
# =============================================================================

DEFAULT_EXCLUDE_PATTERNS: List[str] = [
    # Git
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
    ".env",
    ".env.*",
    "*.env",
    # Node.js
    "node_modules/",
    # Test caches
    ".tox/",
    ".nox/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    # Coverage
    "htmlcov/",
    ".coverage",
    "coverage.xml",
    # Build artifacts
    "*.egg-info/",
    "dist/",
    "build/",
    # IDE
    ".idea/",
    ".vscode/",
    # Temp files
    "*.log",
    "*.tmp",
    "*.swp",
    "*.swo",
    "*~",
    # Credentials (SECURITY CRITICAL)
    "*.pem",
    "*.key",
    "*.crt",
    "credentials.json",
    "secrets.json",
    "*.secret",
]


# =============================================================================
# Test Result Model
# =============================================================================


@dataclass
class TestResult:
    """Result of test execution in sandbox.

    Attributes:
        success: Whether tests passed (exit_code == 0)
        stdout: Standard output from test execution
        stderr: Standard error from test execution
        exit_code: Exit code from test runner
        duration_ms: Total execution time in milliseconds
        tests_passed: Number of tests that passed (parsed from output)
        tests_failed: Number of tests that failed (parsed from output)
        tests_skipped: Number of tests skipped (parsed from output)
        tests_total: Total number of tests run
        coverage_percent: Test coverage percentage (if coverage was run)
        artifacts: Downloaded files (e.g., coverage.xml)
        sandbox_id: ID of the sandbox instance used
        timed_out: Whether execution was terminated due to timeout
    """

    success: bool
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int

    # Parsed test statistics
    tests_passed: Optional[int] = None
    tests_failed: Optional[int] = None
    tests_skipped: Optional[int] = None
    tests_total: Optional[int] = None
    coverage_percent: Optional[float] = None

    # Downloaded artifacts
    artifacts: Dict[str, bytes] = field(default_factory=dict)

    # Execution metadata
    sandbox_id: Optional[str] = None
    timed_out: bool = False

    @property
    def summary(self) -> str:
        """Generate human-readable summary of test results."""
        if self.timed_out:
            return f"Tests timed out after {self.duration_ms}ms"

        if self.tests_total is not None:
            status = "PASSED" if self.success else "FAILED"
            parts = [f"{status}: {self.tests_passed}/{self.tests_total} tests passed"]
            if self.tests_failed:
                parts.append(f"{self.tests_failed} failed")
            if self.tests_skipped:
                parts.append(f"{self.tests_skipped} skipped")
            if self.coverage_percent is not None:
                parts.append(f"{self.coverage_percent:.1f}% coverage")
            return ", ".join(parts)

        return "PASSED" if self.success else f"FAILED (exit code {self.exit_code})"


# =============================================================================
# Test Executor Configuration
# =============================================================================


@dataclass
class TestExecutorConfig:
    """Configuration for test execution in sandbox.

    Attributes:
        sandbox_config: Underlying E2B sandbox configuration
        test_timeout_seconds: Timeout for test execution (default: 300)
        max_test_timeout_seconds: Maximum allowed timeout (default: 1800)
        exclude_patterns: File patterns to exclude from upload
        install_command: Command to install dependencies (default: pip install -e .)
        artifacts_to_download: Files to download after tests
        working_dir: Working directory inside sandbox (default: /code)
    """

    sandbox_config: SandboxConfig
    test_timeout_seconds: int = 300
    max_test_timeout_seconds: int = 1800  # 30 minutes max
    exclude_patterns: List[str] = field(default_factory=list)
    install_command: Optional[str] = "pip install -e ."
    artifacts_to_download: List[str] = field(
        default_factory=lambda: ["coverage.xml", ".coverage", "junit.xml"]
    )
    working_dir: str = "/code"

    def __post_init__(self):
        """Validate and merge exclude patterns."""
        # Merge default patterns with custom patterns
        all_patterns = set(DEFAULT_EXCLUDE_PATTERNS)
        all_patterns.update(self.exclude_patterns)
        self.exclude_patterns = list(all_patterns)

        # Validate timeout
        if self.test_timeout_seconds > self.max_test_timeout_seconds:
            logger.warning(
                f"Test timeout {self.test_timeout_seconds}s exceeds max "
                f"{self.max_test_timeout_seconds}s, capping"
            )
            self.test_timeout_seconds = self.max_test_timeout_seconds

    @classmethod
    def from_env(cls) -> "TestExecutorConfig":
        """Create configuration from environment variables.

        Environment Variables:
            TEST_TIMEOUT_SECONDS: Test execution timeout (default: 300)
            TEST_EXCLUDE_PATTERNS: Comma-separated additional exclude patterns
            TEST_INSTALL_COMMAND: Dependency installation command
        """
        sandbox_config = SandboxConfig.from_env()

        test_timeout = int(os.getenv("TEST_TIMEOUT_SECONDS", "300"))
        max_timeout = int(os.getenv("TEST_MAX_TIMEOUT_SECONDS", "1800"))

        exclude_patterns_str = os.getenv("TEST_EXCLUDE_PATTERNS", "")
        exclude_patterns = [
            p.strip() for p in exclude_patterns_str.split(",") if p.strip()
        ]

        install_command = os.getenv("TEST_INSTALL_COMMAND", "pip install -e .")
        if install_command.lower() == "none":
            install_command = None

        artifacts_str = os.getenv(
            "TEST_ARTIFACTS_DOWNLOAD", "coverage.xml,.coverage,junit.xml"
        )
        artifacts = [a.strip() for a in artifacts_str.split(",") if a.strip()]

        return cls(
            sandbox_config=sandbox_config,
            test_timeout_seconds=test_timeout,
            max_test_timeout_seconds=max_timeout,
            exclude_patterns=exclude_patterns,
            install_command=install_command,
            artifacts_to_download=artifacts,
        )


# =============================================================================
# Pytest Output Parser
# =============================================================================


class PytestOutputParser:
    """Parse pytest output to extract test statistics."""

    # Pattern for pytest summary line: "5 passed, 2 failed, 1 skipped in 0.42s"
    SUMMARY_PATTERN = re.compile(
        r"(?:=+\s+)?"
        r"(?:(?P<passed>\d+)\s+passed)?"
        r"(?:,?\s*(?P<failed>\d+)\s+failed)?"
        r"(?:,?\s*(?P<skipped>\d+)\s+skipped)?"
        r"(?:,?\s*(?P<deselected>\d+)\s+deselected)?"
        r"(?:,?\s*(?P<error>\d+)\s+error)?"
        r"(?:,?\s*(?P<warnings>\d+)\s+warnings?)?"
        r"\s+in\s+[\d.]+s"
    )

    # Pattern for coverage: "TOTAL    1000    200    80%"
    COVERAGE_PATTERN = re.compile(r"TOTAL\s+\d+\s+\d+\s+(\d+(?:\.\d+)?)[%]")

    # Alternative coverage pattern: "Coverage: 80.5%"
    COVERAGE_ALT_PATTERN = re.compile(r"Coverage[:\s]+(\d+(?:\.\d+)?)[%]")

    @classmethod
    def parse(cls, stdout: str, stderr: str) -> Dict:
        """Parse pytest output and return statistics.

        Args:
            stdout: Standard output from pytest
            stderr: Standard error from pytest

        Returns:
            Dictionary with parsed statistics
        """
        output = stdout + "\n" + stderr
        result = {
            "tests_passed": None,
            "tests_failed": None,
            "tests_skipped": None,
            "tests_total": None,
            "coverage_percent": None,
        }

        # Parse summary line
        for match in cls.SUMMARY_PATTERN.finditer(output):
            passed = match.group("passed")
            failed = match.group("failed")
            skipped = match.group("skipped")

            if passed is not None:
                result["tests_passed"] = int(passed)
            if failed is not None:
                result["tests_failed"] = int(failed)
            if skipped is not None:
                result["tests_skipped"] = int(skipped)

            # Calculate total
            total = 0
            for key in ["tests_passed", "tests_failed", "tests_skipped"]:
                if result[key] is not None:
                    total += result[key]
            if total > 0:
                result["tests_total"] = total

            break  # Use first match

        # Parse coverage
        for pattern in [cls.COVERAGE_PATTERN, cls.COVERAGE_ALT_PATTERN]:
            match = pattern.search(output)
            if match:
                result["coverage_percent"] = float(match.group(1))
                break

        return result


# =============================================================================
# File Filter
# =============================================================================


class FileFilter:
    """Filter files for upload to sandbox."""

    def __init__(self, patterns: List[str]):
        """Initialize with exclusion patterns.

        Args:
            patterns: Glob patterns to exclude
        """
        self.patterns = patterns
        self._compiled_patterns: Set[str] = set(patterns)

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
            # Path not under repository
            return False

        for pattern in self._compiled_patterns:
            # Handle directory patterns (ending with /)
            if pattern.endswith("/"):
                dir_pattern = pattern.rstrip("/")
                # Check if any parent directory matches
                for i, part in enumerate(rel_parts[:-1]):
                    if fnmatch.fnmatch(part, dir_pattern):
                        return False
                # Check the file's directory
                if rel_parts and fnmatch.fnmatch(rel_parts[0], dir_pattern):
                    return False

            # Handle ** patterns
            elif "**" in pattern:
                # Convert glob to regex-friendly pattern
                if fnmatch.fnmatch(rel_str, pattern):
                    return False

            # Handle simple patterns
            else:
                # Match against filename
                if fnmatch.fnmatch(path.name, pattern):
                    return False
                # Match against full relative path
                if fnmatch.fnmatch(rel_str, pattern):
                    return False

        return True

    def filter_files(self, repo_path: Path) -> List[Path]:
        """Get list of files to upload from repository.

        Args:
            repo_path: Path to repository root

        Returns:
            List of file paths to upload
        """
        files = []
        repo_path = repo_path.resolve()

        for path in repo_path.rglob("*"):
            if path.is_file() and self.should_include(path, repo_path):
                files.append(path)

        return files


# =============================================================================
# Test Executor
# =============================================================================


class TestExecutor:
    """Execute tests in isolated E2B sandbox.

    This class provides secure test execution by:
    1. Uploading repository files to isolated sandbox
    2. Installing dependencies in sandbox
    3. Running test commands
    4. Capturing output and downloading artifacts
    5. Cleaning up sandbox resources

    Security Properties:
    - Host filesystem never exposed to test code
    - Secrets (.env, credentials) excluded from upload
    - Only explicitly provided env vars available in sandbox
    - Network access from sandbox IP (not host)
    - Timeout enforcement prevents resource exhaustion
    """

    def __init__(self, config: TestExecutorConfig):
        """Initialize test executor.

        Args:
            config: Test executor configuration
        """
        self.config = config
        self._file_filter = FileFilter(config.exclude_patterns)

    async def run_tests(
        self,
        repo_path: Path,
        command: str = "pytest",
        env_vars: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        install_deps: bool = True,
    ) -> TestResult:
        """Run tests in isolated sandbox.

        Args:
            repo_path: Path to repository root
            command: Test command to execute (default: pytest)
            env_vars: Environment variables to inject into sandbox
            timeout: Test timeout in seconds (default: from config)
            install_deps: Whether to install dependencies first

        Returns:
            TestResult with execution details and parsed statistics

        Raises:
            SandboxConfigurationError: If E2B is not configured
            SandboxTimeoutError: If tests exceed timeout
            SandboxExecutionError: If sandbox operations fail
        """
        timeout = timeout or self.config.test_timeout_seconds
        env_vars = env_vars or {}

        # Validate configuration
        if not self.config.sandbox_config.is_configured:
            raise SandboxConfigurationError(
                "E2B API key required for sandbox test execution",
                suggestion="Set E2B_API_KEY or use --local-tests flag",
            )

        repo_path = Path(repo_path).resolve()
        if not repo_path.is_dir():
            raise ValueError(f"Repository path is not a directory: {repo_path}")

        logger.info(f"Starting sandbox test execution: {command}")

        sandbox_id: Optional[str] = None
        start_time = asyncio.get_event_loop().time()

        try:
            async with SandboxExecutor(self.config.sandbox_config) as sandbox:
                sandbox_id = sandbox._sandbox_id

                # Step 1: Upload repository files
                await self._upload_repository(sandbox, repo_path)

                # Step 2: Set environment variables
                if env_vars:
                    await self._set_env_vars(sandbox, env_vars)

                # Step 3: Install dependencies
                if install_deps and self.config.install_command:
                    await self._install_dependencies(sandbox)

                # Step 4: Run tests
                test_result = await self._execute_tests(sandbox, command, timeout)

                # Step 5: Download artifacts
                artifacts = await self._download_artifacts(sandbox)

                # Step 6: Parse output
                parsed = PytestOutputParser.parse(
                    test_result.stdout, test_result.stderr
                )

                duration_ms = int(
                    (asyncio.get_event_loop().time() - start_time) * 1000
                )

                return TestResult(
                    success=test_result.exit_code == 0,
                    stdout=test_result.stdout,
                    stderr=test_result.stderr,
                    exit_code=test_result.exit_code,
                    duration_ms=duration_ms,
                    tests_passed=parsed.get("tests_passed"),
                    tests_failed=parsed.get("tests_failed"),
                    tests_skipped=parsed.get("tests_skipped"),
                    tests_total=parsed.get("tests_total"),
                    coverage_percent=parsed.get("coverage_percent"),
                    artifacts=artifacts,
                    sandbox_id=sandbox_id,
                    timed_out=False,
                )

        except SandboxTimeoutError as e:
            duration_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
            logger.warning(f"Test execution timed out after {timeout}s")

            return TestResult(
                success=False,
                stdout="",
                stderr=f"Test execution timed out after {timeout} seconds",
                exit_code=-1,
                duration_ms=duration_ms,
                sandbox_id=sandbox_id,
                timed_out=True,
            )

        except Exception as e:
            duration_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
            logger.error(f"Test execution failed: {e}", exc_info=True)

            # Re-raise sandbox-specific exceptions
            if isinstance(e, (SandboxConfigurationError, SandboxExecutionError)):
                raise

            raise SandboxExecutionError(
                f"Test execution failed: {e}",
                sandbox_id=sandbox_id,
                operation="run_tests",
            )

    async def _upload_repository(
        self, sandbox: SandboxExecutor, repo_path: Path
    ) -> None:
        """Upload repository files to sandbox.

        Args:
            sandbox: Active sandbox executor
            repo_path: Path to repository root
        """
        logger.info(f"Scanning repository for upload: {repo_path}")

        # Filter files
        files = self._file_filter.filter_files(repo_path)
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

    async def _install_dependencies(self, sandbox: SandboxExecutor) -> None:
        """Install dependencies in sandbox.

        Args:
            sandbox: Active sandbox executor
        """
        if not self.config.install_command:
            return

        logger.info(f"Installing dependencies: {self.config.install_command}")

        # Change to working directory and install
        result = await sandbox.execute_command(
            f"cd {self.config.working_dir} && {self.config.install_command}"
        )

        if result.exit_code != 0:
            logger.warning(f"Dependency installation failed: {result.stderr}")
            # Continue anyway - some tests might still work

    async def _execute_tests(
        self, sandbox: SandboxExecutor, command: str, timeout: int
    ) -> CommandResult:
        """Execute test command in sandbox.

        Args:
            sandbox: Active sandbox executor
            command: Test command to run
            timeout: Timeout in seconds

        Returns:
            CommandResult from test execution
        """
        logger.info(f"Running tests: {command}")

        # Build full command with working directory
        full_command = f"cd {self.config.working_dir} && {command}"

        return await sandbox.execute_command(full_command, timeout=timeout)

    async def _download_artifacts(
        self, sandbox: SandboxExecutor
    ) -> Dict[str, bytes]:
        """Download test artifacts from sandbox.

        Args:
            sandbox: Active sandbox executor

        Returns:
            Dictionary mapping artifact names to contents
        """
        artifacts: Dict[str, bytes] = {}

        for artifact_name in self.config.artifacts_to_download:
            artifact_path = f"{self.config.working_dir}/{artifact_name}"

            try:
                # Check if file exists
                check_result = await sandbox.execute_command(
                    f"test -f {artifact_path} && echo 'exists'"
                )

                if "exists" in check_result.stdout:
                    downloaded = await sandbox.download_files([artifact_path])
                    if artifact_path in downloaded:
                        artifacts[artifact_name] = downloaded[artifact_path]
                        logger.debug(
                            f"Downloaded artifact: {artifact_name} "
                            f"({len(artifacts[artifact_name])} bytes)"
                        )

            except Exception as e:
                logger.debug(f"Could not download artifact {artifact_name}: {e}")

        if artifacts:
            logger.info(f"Downloaded {len(artifacts)} test artifacts")

        return artifacts


# =============================================================================
# Synchronous Wrapper for CLI Integration
# =============================================================================


def run_tests_sync(
    repo_path: Path,
    command: str = "pytest",
    env_vars: Optional[Dict[str, str]] = None,
    timeout: Optional[int] = None,
    config: Optional[TestExecutorConfig] = None,
) -> TestResult:
    """Synchronous wrapper for test execution.

    This provides a simple sync interface for CLI tools and scripts.

    Args:
        repo_path: Path to repository
        command: Test command to execute
        env_vars: Environment variables for sandbox
        timeout: Test timeout in seconds
        config: Test executor config (default: from environment)

    Returns:
        TestResult with execution details
    """
    config = config or TestExecutorConfig.from_env()
    executor = TestExecutor(config)

    return asyncio.run(
        executor.run_tests(
            repo_path=repo_path,
            command=command,
            env_vars=env_vars,
            timeout=timeout,
        )
    )

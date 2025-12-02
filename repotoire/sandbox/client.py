"""E2B sandbox executor for secure code execution.

This module provides a high-level interface for executing code and commands
in E2B cloud sandboxes. It supports async context manager usage for proper
resource cleanup and graceful degradation when E2B is not configured.

Quota enforcement (REPO-299):
    When customer_id and tier are provided, the executor will:
    - Check quotas before creating a sandbox
    - Track concurrent sandboxes
    - Record usage after completion
"""

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

from repotoire.logging_config import get_logger
from repotoire.sandbox.config import SandboxConfig
from repotoire.sandbox.exceptions import (
    SandboxConfigurationError,
    SandboxExecutionError,
    SandboxTimeoutError,
    SandboxResourceError,
)

if TYPE_CHECKING:
    from repotoire.db.models import PlanTier
    from repotoire.sandbox.enforcement import QuotaEnforcer
    from repotoire.sandbox.usage import SandboxUsageTracker

logger = get_logger(__name__)


@dataclass
class ExecutionResult:
    """Result of code execution in a sandbox.

    Attributes:
        stdout: Standard output from execution
        stderr: Standard error from execution
        exit_code: Exit code (0 for success)
        duration_ms: Execution duration in milliseconds
        error: Error message if execution failed (None on success)
    """

    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        """Check if execution was successful."""
        return self.exit_code == 0 and self.error is None


@dataclass
class CommandResult:
    """Result of shell command execution in a sandbox.

    Attributes:
        stdout: Standard output from command
        stderr: Standard error from command
        exit_code: Exit code (0 for success)
        duration_ms: Execution duration in milliseconds
    """

    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int

    @property
    def success(self) -> bool:
        """Check if command was successful."""
        return self.exit_code == 0


class SandboxExecutor:
    """Execute code and commands in E2B cloud sandboxes.

    This class provides a high-level interface for:
    - Executing Python code in isolated sandboxes
    - Running shell commands
    - Uploading and downloading files
    - Proper resource cleanup via async context manager
    - Quota enforcement (REPO-299)

    Usage:
        ```python
        config = SandboxConfig.from_env()

        async with SandboxExecutor(config) as sandbox:
            result = await sandbox.execute_code("print('Hello!')")
            print(result.stdout)
        ```

    With quota enforcement:
        ```python
        from repotoire.sandbox.enforcement import QuotaEnforcer
        from repotoire.sandbox.usage import SandboxUsageTracker
        from repotoire.db.models import PlanTier

        config = SandboxConfig.from_env()
        enforcer = QuotaEnforcer()
        tracker = SandboxUsageTracker()

        async with SandboxExecutor(
            config,
            customer_id="cust_123",
            tier=PlanTier.PRO,
            quota_enforcer=enforcer,
            usage_tracker=tracker,
        ) as sandbox:
            result = await sandbox.execute_code("print('Hello!')")
        # Usage automatically recorded on exit
        ```

    When E2B_API_KEY is not configured, operations will raise
    SandboxConfigurationError with helpful guidance.
    """

    def __init__(
        self,
        config: SandboxConfig,
        customer_id: Optional[str] = None,
        tier: Optional["PlanTier"] = None,
        quota_enforcer: Optional["QuotaEnforcer"] = None,
        usage_tracker: Optional["SandboxUsageTracker"] = None,
        operation_type: str = "code_execution",
    ):
        """Initialize sandbox executor.

        Args:
            config: Sandbox configuration (use SandboxConfig.from_env())
            customer_id: Customer identifier for quota enforcement
            tier: Customer's subscription tier for quota limits
            quota_enforcer: QuotaEnforcer instance for checking limits
            usage_tracker: SandboxUsageTracker for recording usage
            operation_type: Type of operation for metrics/tracking
        """
        self.config = config
        self.customer_id = customer_id
        self.tier = tier
        self.quota_enforcer = quota_enforcer
        self.usage_tracker = usage_tracker
        self.operation_type = operation_type
        self._sandbox = None
        self._sandbox_id: Optional[str] = None
        self._start_time: Optional[float] = None
        self._quota_checked = False

    async def __aenter__(self) -> "SandboxExecutor":
        """Enter async context and create sandbox.

        Returns:
            Self for use in async with block

        Raises:
            SandboxConfigurationError: If E2B is not configured
            QuotaExceededError: If customer quota is exceeded
        """
        if not self.config.is_configured:
            logger.warning(
                "E2B API key not configured, sandbox features unavailable"
            )
            # Don't create sandbox - operations will fail with clear error
            return self

        # Check quota before creating sandbox (REPO-299)
        if self.customer_id and self.tier and self.quota_enforcer:
            try:
                await self.quota_enforcer.enforce_or_raise(self.customer_id, self.tier)
                self._quota_checked = True
                logger.debug(
                    f"Quota check passed for {self.customer_id}",
                    extra={"tier": self.tier.value if self.tier else None},
                )
            except Exception as e:
                # Re-raise QuotaExceededError, but let other errors through
                # with a warning (fail open for non-quota errors)
                from repotoire.sandbox.enforcement import QuotaExceededError
                if isinstance(e, QuotaExceededError):
                    raise
                logger.warning(f"Quota check failed (non-blocking): {e}")

        # Track concurrent sandboxes (REPO-299)
        if self.customer_id and self.usage_tracker:
            await self.usage_tracker.increment_concurrent(
                self.customer_id,
                "pending",  # Will be updated with real sandbox_id
                self.operation_type,
            )

        try:
            # Import e2b only when actually needed
            from e2b_code_interpreter import Sandbox

            logger.info("Creating E2B sandbox...")
            self._start_time = time.time()

            # Create sandbox with configured options
            # Note: E2B uses sync API, we run it in executor to be async-friendly
            loop = asyncio.get_event_loop()

            def create_sandbox():
                return Sandbox.create(
                    template=self.config.sandbox_template,
                    timeout=self.config.timeout_seconds,
                    api_key=self.config.api_key,
                )

            self._sandbox = await loop.run_in_executor(None, create_sandbox)

            # Store sandbox ID for logging/debugging
            self._sandbox_id = getattr(self._sandbox, "sandbox_id", "unknown")

            # Update concurrent tracking with real sandbox ID
            if self.customer_id and self.usage_tracker:
                # Remove the "pending" entry and add with real ID
                await self.usage_tracker.decrement_concurrent(self.customer_id, "pending")
                await self.usage_tracker.increment_concurrent(
                    self.customer_id,
                    self._sandbox_id,
                    self.operation_type,
                )

            duration_ms = int((time.time() - self._start_time) * 1000)
            logger.info(
                f"E2B sandbox created",
                extra={
                    "sandbox_id": self._sandbox_id,
                    "duration_ms": duration_ms,
                    "customer_id": self.customer_id,
                },
            )

        except ImportError:
            # Clean up concurrent tracking on failure
            if self.customer_id and self.usage_tracker:
                await self.usage_tracker.decrement_concurrent(self.customer_id, "pending")
            raise SandboxConfigurationError(
                "e2b-code-interpreter package not installed",
                suggestion="Install with: pip install e2b-code-interpreter",
            )
        except Exception as e:
            # Clean up concurrent tracking on failure
            if self.customer_id and self.usage_tracker:
                await self.usage_tracker.decrement_concurrent(self.customer_id, "pending")

            error_msg = str(e)
            if "API key" in error_msg or "authentication" in error_msg.lower():
                raise SandboxConfigurationError(
                    f"E2B authentication failed: {error_msg}",
                    suggestion="Check that E2B_API_KEY is valid",
                )
            raise SandboxConfigurationError(
                f"Failed to create E2B sandbox: {error_msg}",
                suggestion="Check E2B service status and configuration",
            )

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit async context and cleanup sandbox.

        Records usage and decrements concurrent count (REPO-299).
        """
        # Calculate duration for usage tracking
        duration_seconds = 0.0
        if self._start_time is not None:
            duration_seconds = time.time() - self._start_time

        if self._sandbox is not None:
            try:
                logger.debug(f"Killing sandbox {self._sandbox_id}")
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._sandbox.kill)
                logger.info(f"Sandbox {self._sandbox_id} terminated")
            except Exception as e:
                logger.warning(
                    f"Failed to cleanly terminate sandbox: {e}",
                    extra={"sandbox_id": self._sandbox_id},
                )
            finally:
                self._sandbox = None

        # Record usage after sandbox completes (REPO-299)
        if self.customer_id and self.usage_tracker:
            # Decrement concurrent count
            if self._sandbox_id:
                await self.usage_tracker.decrement_concurrent(
                    self.customer_id,
                    self._sandbox_id,
                )

            # Record usage (minutes and cost)
            if duration_seconds > 0:
                # Calculate cost based on config
                from repotoire.sandbox.metrics import calculate_cost
                cost_usd = calculate_cost(
                    duration_seconds,
                    cpu_count=self.config.cpu_count,
                    memory_gb=self.config.memory_mb / 1024.0,
                )

                try:
                    await self.usage_tracker.record_usage(
                        self.customer_id,
                        duration_seconds,
                        cost_usd,
                    )
                    logger.debug(
                        f"Recorded usage for {self.customer_id}",
                        extra={
                            "duration_seconds": duration_seconds,
                            "cost_usd": cost_usd,
                        },
                    )
                except Exception as e:
                    logger.warning(f"Failed to record usage: {e}")

    def _ensure_sandbox(self, operation: str) -> None:
        """Ensure sandbox is available for operation.

        Args:
            operation: Name of the operation being attempted

        Raises:
            SandboxConfigurationError: If sandbox is not available
        """
        if self._sandbox is None:
            if not self.config.is_configured:
                raise SandboxConfigurationError(
                    "E2B API key required for sandbox execution",
                    operation=operation,
                    suggestion="Set the E2B_API_KEY environment variable",
                )
            raise SandboxConfigurationError(
                "Sandbox not initialized - use 'async with SandboxExecutor(config)' context",
                operation=operation,
            )

    async def execute_code(
        self,
        code: str,
        language: str = "python",
        timeout: Optional[int] = None,
    ) -> ExecutionResult:
        """Execute code in the sandbox.

        Args:
            code: Source code to execute
            language: Programming language (default: "python")
            timeout: Execution timeout in seconds (default: use config timeout)

        Returns:
            ExecutionResult with stdout, stderr, exit_code, and timing

        Raises:
            SandboxConfigurationError: If sandbox is not configured
            SandboxTimeoutError: If execution exceeds timeout
            SandboxExecutionError: If execution fails
        """
        self._ensure_sandbox("execute_code")
        timeout = timeout or self.config.timeout_seconds

        logger.debug(
            f"Executing {language} code",
            extra={
                "sandbox_id": self._sandbox_id,
                "code_length": len(code),
                "timeout": timeout,
            },
        )

        start_time = time.time()

        try:
            loop = asyncio.get_event_loop()

            # Execute code with timeout
            execution = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: self._sandbox.run_code(code),
                ),
                timeout=timeout,
            )

            duration_ms = int((time.time() - start_time) * 1000)

            # E2B returns logs as lists, join them
            stdout = "\n".join(execution.logs.stdout) if execution.logs.stdout else ""
            stderr = "\n".join(execution.logs.stderr) if execution.logs.stderr else ""

            # Check for execution errors
            error_msg = None
            exit_code = 0

            if execution.error:
                error_msg = str(execution.error)
                exit_code = 1
                logger.warning(
                    f"Code execution error: {error_msg[:100]}...",
                    extra={"sandbox_id": self._sandbox_id},
                )

            result = ExecutionResult(
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                duration_ms=duration_ms,
                error=error_msg,
            )

            logger.debug(
                f"Code execution completed",
                extra={
                    "sandbox_id": self._sandbox_id,
                    "exit_code": exit_code,
                    "duration_ms": duration_ms,
                    "stdout_length": len(stdout),
                    "stderr_length": len(stderr),
                },
            )

            return result

        except asyncio.TimeoutError:
            duration_ms = int((time.time() - start_time) * 1000)
            raise SandboxTimeoutError(
                f"Code execution timed out after {timeout}s",
                timeout=timeout,
                sandbox_id=self._sandbox_id,
                operation="execute_code",
            )
        except Exception as e:
            if isinstance(e, (SandboxTimeoutError, SandboxConfigurationError)):
                raise

            error_msg = str(e)
            duration_ms = int((time.time() - start_time) * 1000)

            # Check for resource errors
            if "memory" in error_msg.lower() or "oom" in error_msg.lower():
                raise SandboxResourceError(
                    f"Sandbox memory limit exceeded: {error_msg}",
                    sandbox_id=self._sandbox_id,
                    operation="execute_code",
                    resource_type="memory",
                    limit=f"{self.config.memory_mb}MB",
                )

            raise SandboxExecutionError(
                f"Code execution failed: {error_msg}",
                sandbox_id=self._sandbox_id,
                operation="execute_code",
            )

    async def execute_command(
        self,
        cmd: str,
        timeout: Optional[int] = None,
    ) -> CommandResult:
        """Execute a shell command in the sandbox.

        Args:
            cmd: Shell command to execute
            timeout: Execution timeout in seconds (default: use config timeout)

        Returns:
            CommandResult with stdout, stderr, exit_code, and timing

        Raises:
            SandboxConfigurationError: If sandbox is not configured
            SandboxTimeoutError: If command exceeds timeout
            SandboxExecutionError: If command fails
        """
        self._ensure_sandbox("execute_command")
        timeout = timeout or self.config.timeout_seconds

        logger.debug(
            f"Executing command: {cmd[:50]}...",
            extra={
                "sandbox_id": self._sandbox_id,
                "timeout": timeout,
            },
        )

        start_time = time.time()

        try:
            loop = asyncio.get_event_loop()

            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: self._sandbox.commands.run(cmd),
                ),
                timeout=timeout,
            )

            duration_ms = int((time.time() - start_time) * 1000)

            command_result = CommandResult(
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                exit_code=result.exit_code,
                duration_ms=duration_ms,
            )

            logger.debug(
                f"Command completed",
                extra={
                    "sandbox_id": self._sandbox_id,
                    "exit_code": result.exit_code,
                    "duration_ms": duration_ms,
                },
            )

            return command_result

        except asyncio.TimeoutError:
            raise SandboxTimeoutError(
                f"Command execution timed out after {timeout}s",
                timeout=timeout,
                sandbox_id=self._sandbox_id,
                operation="execute_command",
            )
        except Exception as e:
            if isinstance(e, (SandboxTimeoutError, SandboxConfigurationError)):
                raise

            # Handle CommandExitException from e2b SDK - non-zero exit codes are
            # expected for linters/tools that find issues (e.g., ruff returns 1
            # when it finds lint issues, bandit returns 1 for security issues)
            from e2b.sandbox.commands.command_handle import CommandExitException
            if isinstance(e, CommandExitException):
                duration_ms = int((time.time() - start_time) * 1000)
                # Extract result from exception - e2b stores the process handle
                return CommandResult(
                    stdout=e.stdout or "",
                    stderr=e.stderr or "",
                    exit_code=e.exit_code,
                    duration_ms=duration_ms,
                )

            raise SandboxExecutionError(
                f"Command execution failed: {e}",
                sandbox_id=self._sandbox_id,
                operation="execute_command",
            )

    async def upload_files(self, files: List[Path]) -> None:
        """Upload files to the sandbox.

        Files are uploaded to /code/ directory in the sandbox.

        Args:
            files: List of local file paths to upload

        Raises:
            SandboxConfigurationError: If sandbox is not configured
            SandboxExecutionError: If upload fails
            FileNotFoundError: If a local file doesn't exist
        """
        self._ensure_sandbox("upload_files")

        logger.debug(
            f"Uploading {len(files)} files",
            extra={"sandbox_id": self._sandbox_id},
        )

        loop = asyncio.get_event_loop()

        for file_path in files:
            path = Path(file_path)

            if not path.exists():
                raise FileNotFoundError(f"File not found: {path}")

            if not path.is_file():
                raise SandboxExecutionError(
                    f"Path is not a file: {path}",
                    sandbox_id=self._sandbox_id,
                    operation="upload_files",
                )

            try:
                content = path.read_text()
                sandbox_path = f"/code/{path.name}"

                await loop.run_in_executor(
                    None,
                    lambda p=sandbox_path, c=content: self._sandbox.files.write(p, c),
                )

                logger.debug(
                    f"Uploaded {path.name} to {sandbox_path}",
                    extra={"sandbox_id": self._sandbox_id},
                )

            except Exception as e:
                raise SandboxExecutionError(
                    f"Failed to upload {path.name}: {e}",
                    sandbox_id=self._sandbox_id,
                    operation="upload_files",
                )

        logger.info(
            f"Uploaded {len(files)} files to sandbox",
            extra={"sandbox_id": self._sandbox_id},
        )

    async def download_files(self, paths: List[str]) -> Dict[str, bytes]:
        """Download files from the sandbox.

        Args:
            paths: List of paths within the sandbox to download

        Returns:
            Dictionary mapping paths to file contents (bytes)

        Raises:
            SandboxConfigurationError: If sandbox is not configured
            SandboxExecutionError: If download fails
        """
        self._ensure_sandbox("download_files")

        logger.debug(
            f"Downloading {len(paths)} files",
            extra={"sandbox_id": self._sandbox_id},
        )

        loop = asyncio.get_event_loop()
        results: Dict[str, bytes] = {}

        for path in paths:
            try:
                content = await loop.run_in_executor(
                    None,
                    lambda p=path: self._sandbox.files.read(p),
                )

                # E2B returns string, encode to bytes
                if isinstance(content, str):
                    content = content.encode("utf-8")

                results[path] = content

                logger.debug(
                    f"Downloaded {path} ({len(content)} bytes)",
                    extra={"sandbox_id": self._sandbox_id},
                )

            except Exception as e:
                raise SandboxExecutionError(
                    f"Failed to download {path}: {e}",
                    sandbox_id=self._sandbox_id,
                    operation="download_files",
                )

        logger.info(
            f"Downloaded {len(results)} files from sandbox",
            extra={"sandbox_id": self._sandbox_id},
        )

        return results

    async def list_files(self, path: str = "/code") -> List[str]:
        """List files in a sandbox directory.

        Args:
            path: Directory path within the sandbox

        Returns:
            List of file/directory names

        Raises:
            SandboxConfigurationError: If sandbox is not configured
            SandboxExecutionError: If listing fails
        """
        self._ensure_sandbox("list_files")

        try:
            loop = asyncio.get_event_loop()
            files = await loop.run_in_executor(
                None,
                lambda: self._sandbox.files.list(path),
            )

            # E2B returns FileInfo objects, extract names
            file_names = [f.name for f in files] if files else []

            logger.debug(
                f"Listed {len(file_names)} items in {path}",
                extra={"sandbox_id": self._sandbox_id},
            )

            return file_names

        except Exception as e:
            raise SandboxExecutionError(
                f"Failed to list files in {path}: {e}",
                sandbox_id=self._sandbox_id,
                operation="list_files",
            )

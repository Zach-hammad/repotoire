"""Secure skill execution in E2B sandbox.

This module provides the SkillExecutor class that wraps SandboxExecutor
specifically for MCP skill execution. It replaces dangerous exec() calls
with isolated sandbox execution.

Security requirements:
- NEVER fall back to local exec() - fail secure if sandbox unavailable
- All skill executions must be logged for audit trail
- Timeout and memory limits must be enforced
- Skill errors must not crash the host process
- Input/output must be JSON-serializable

REPO-313: Enhanced with:
- Two-tier skill caching (L1: local, L2: Redis)
- Skill code cached by hash for fast reloading
- Cache invalidation on file changes
"""

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from repotoire.logging_config import get_logger

if TYPE_CHECKING:
    from repotoire.cache import TwoTierSkillCache
from repotoire.sandbox.client import SandboxExecutor, ExecutionResult
from repotoire.sandbox.config import SandboxConfig
from repotoire.sandbox.exceptions import (
    SandboxConfigurationError,
    SandboxTimeoutError,
    SandboxExecutionError,
    # Skill-specific exceptions
    SkillLoadError,
    SkillExecutionError,
    SkillTimeoutError,
    SkillSecurityError,
)

logger = get_logger(__name__)


# Result marker used to extract JSON result from stdout
RESULT_MARKER = "__SKILL_RESULT__"


@dataclass
class SkillResult:
    """Result of skill execution.

    Attributes:
        success: Whether execution completed successfully
        result: Return value from skill (JSON-serializable)
        stdout: Standard output captured during execution
        stderr: Standard error captured during execution
        duration_ms: Execution time in milliseconds
        error: Error message if execution failed
        error_type: Exception type if execution failed
        traceback: Stack trace if execution failed
    """

    success: bool
    result: Optional[Any] = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    error: Optional[str] = None
    error_type: Optional[str] = None
    traceback: Optional[str] = None


@dataclass
class SkillAuditEntry:
    """Audit log entry for skill execution.

    Attributes:
        timestamp: When execution occurred
        skill_name: Name of the skill
        skill_hash: Hash of skill code for tracking
        context_size: Size of input context in bytes
        duration_ms: Execution time in milliseconds
        success: Whether execution succeeded
        error: Error message if failed
        sandbox_id: ID of the sandbox used
    """

    timestamp: str
    skill_name: str
    skill_hash: str
    context_size: int
    duration_ms: int
    success: bool
    error: Optional[str] = None
    sandbox_id: Optional[str] = None


@dataclass
class SkillExecutorConfig:
    """Configuration for skill execution.

    Attributes:
        timeout_seconds: Maximum execution time (default: 300 = 5 min)
        memory_mb: Memory limit in MB (default: 1024 = 1GB)
        max_output_size: Maximum output size in bytes (default: 10MB)
        max_context_size: Maximum input context size in bytes (default: 10MB)
        enable_audit_log: Whether to log all executions (default: True)
    """

    timeout_seconds: int = 300
    memory_mb: int = 1024
    max_output_size: int = 10 * 1024 * 1024
    max_context_size: int = 10 * 1024 * 1024
    enable_audit_log: bool = True


class SkillExecutor:
    """Execute MCP skills in isolated E2B sandboxes.

    This class provides secure skill execution by:
    1. Serializing skill input to JSON
    2. Generating a wrapper script that runs in the sandbox
    3. Executing the script in an isolated E2B sandbox
    4. Deserializing and returning the result
    5. Logging all executions for audit trail

    SECURITY: Never falls back to local exec() - fails secure if sandbox unavailable.

    Usage:
        ```python
        config = SkillExecutorConfig(timeout_seconds=300)

        async with SkillExecutor(config) as executor:
            result = await executor.execute_skill(
                skill_code='''
                def analyze(code: str) -> dict:
                    return {"lines": len(code.split())}
                ''',
                skill_name="analyze",
                context={"code": "def foo(): pass"}
            )
            print(result.result)  # {"lines": 3}
        ```
    """

    # Cache for compiled skills by content hash
    _skill_cache: Dict[str, str] = {}

    def __init__(
        self,
        config: Optional[SkillExecutorConfig] = None,
        sandbox_config: Optional[SandboxConfig] = None,
    ):
        """Initialize skill executor.

        Args:
            config: Skill executor configuration
            sandbox_config: E2B sandbox configuration (loaded from env if not provided)
        """
        self.config = config or SkillExecutorConfig()
        self.sandbox_config = sandbox_config or SandboxConfig.from_env()

        # Override sandbox timeout/memory with skill config
        self.sandbox_config.timeout_seconds = self.config.timeout_seconds
        self.sandbox_config.memory_mb = self.config.memory_mb

        self._sandbox: Optional[SandboxExecutor] = None
        self._audit_log: List[SkillAuditEntry] = []

    async def __aenter__(self) -> "SkillExecutor":
        """Enter async context and create sandbox.

        Raises:
            SkillSecurityError: If sandbox cannot be created and no fallback allowed
        """
        if not self.sandbox_config.is_configured:
            raise SkillSecurityError(
                "E2B API key required for secure skill execution",
                suggestion="Set the E2B_API_KEY environment variable. "
                "Local exec() is disabled for security.",
            )

        self._sandbox = SandboxExecutor(self.sandbox_config)
        await self._sandbox.__aenter__()

        logger.info(
            "SkillExecutor initialized",
            extra={
                "timeout_seconds": self.config.timeout_seconds,
                "memory_mb": self.config.memory_mb,
            },
        )

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit async context and cleanup sandbox."""
        if self._sandbox is not None:
            await self._sandbox.__aexit__(exc_type, exc_val, exc_tb)
            self._sandbox = None

    def _hash_skill(self, skill_code: str) -> str:
        """Generate hash for skill code for caching and audit.

        Args:
            skill_code: Python source code

        Returns:
            SHA256 hash of the code
        """
        return hashlib.sha256(skill_code.encode("utf-8")).hexdigest()[:16]

    def _generate_wrapper_script(
        self,
        skill_code: str,
        skill_name: str,
        context: Dict[str, Any],
    ) -> str:
        """Generate the wrapper script that runs in the sandbox.

        Args:
            skill_code: Python code containing the skill function
            skill_name: Name of the function to call
            context: Input arguments as a dictionary

        Returns:
            Complete Python script to execute in sandbox
        """
        # Escape the context JSON properly
        context_json = json.dumps(context)
        # Escape backslashes and quotes for embedding in string literal
        context_json_escaped = context_json.replace("\\", "\\\\").replace("'", "\\'")

        wrapper = f'''
import json
import sys
import traceback

# Skill code
{skill_code}

# Context passed as JSON
try:
    context = json.loads('{context_json_escaped}')
except json.JSONDecodeError as e:
    print("{RESULT_MARKER}")
    print(json.dumps({{
        "success": False,
        "error_type": "JSONDecodeError",
        "error_message": f"Failed to parse context: {{e}}",
        "traceback": ""
    }}))
    sys.exit(1)

try:
    # Call the skill function with context
    result = {skill_name}(**context)

    # Output result as JSON
    print("{RESULT_MARKER}")
    print(json.dumps({{"success": True, "result": result}}))
except Exception as e:
    print("{RESULT_MARKER}")
    print(json.dumps({{
        "success": False,
        "error_type": type(e).__name__,
        "error_message": str(e),
        "traceback": traceback.format_exc()
    }}))
'''
        return wrapper

    def _parse_result(self, stdout: str, stderr: str) -> SkillResult:
        """Parse skill execution result from sandbox output.

        Args:
            stdout: Standard output from sandbox
            stderr: Standard error from sandbox

        Returns:
            SkillResult with parsed result or error
        """
        # Find the result marker
        if RESULT_MARKER not in stdout:
            return SkillResult(
                success=False,
                stdout=stdout,
                stderr=stderr,
                error="Skill did not produce expected output marker",
                error_type="OutputError",
            )

        # Split at marker and get JSON result
        parts = stdout.split(RESULT_MARKER)
        pre_marker_output = parts[0].strip() if len(parts) > 0 else ""
        result_json = parts[1].strip() if len(parts) > 1 else ""

        try:
            result_data = json.loads(result_json)
        except json.JSONDecodeError as e:
            return SkillResult(
                success=False,
                stdout=pre_marker_output,
                stderr=stderr,
                error=f"Failed to parse skill result JSON: {e}",
                error_type="JSONDecodeError",
            )

        if result_data.get("success"):
            return SkillResult(
                success=True,
                result=result_data.get("result"),
                stdout=pre_marker_output,
                stderr=stderr,
            )
        else:
            return SkillResult(
                success=False,
                stdout=pre_marker_output,
                stderr=stderr,
                error=result_data.get("error_message", "Unknown error"),
                error_type=result_data.get("error_type", "Unknown"),
                traceback=result_data.get("traceback"),
            )

    def _log_audit(
        self,
        skill_name: str,
        skill_hash: str,
        context_size: int,
        duration_ms: int,
        success: bool,
        error: Optional[str] = None,
        sandbox_id: Optional[str] = None,
    ) -> None:
        """Log skill execution for audit trail.

        Args:
            skill_name: Name of the executed skill
            skill_hash: Hash of skill code
            context_size: Size of input context in bytes
            duration_ms: Execution time
            success: Whether execution succeeded
            error: Error message if failed
            sandbox_id: ID of sandbox used
        """
        if not self.config.enable_audit_log:
            return

        entry = SkillAuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            skill_name=skill_name,
            skill_hash=skill_hash,
            context_size=context_size,
            duration_ms=duration_ms,
            success=success,
            error=error,
            sandbox_id=sandbox_id,
        )

        self._audit_log.append(entry)

        # Log to standard logger
        log_extra = {
            "skill_name": skill_name,
            "skill_hash": skill_hash,
            "context_size": context_size,
            "duration_ms": duration_ms,
            "success": success,
        }

        if success:
            logger.info("Skill execution completed", extra=log_extra)
        else:
            log_extra["error"] = error
            logger.warning("Skill execution failed", extra=log_extra)

    async def execute_skill(
        self,
        skill_code: str,
        skill_name: str,
        context: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> SkillResult:
        """Execute a skill in the sandbox.

        Args:
            skill_code: Python code containing the skill function
            skill_name: Name of the function to call
            context: Input arguments as a dictionary (must be JSON-serializable)
            timeout: Override timeout in seconds (default: use config)

        Returns:
            SkillResult with execution result or error

        Raises:
            SkillSecurityError: If sandbox is not available
            SkillTimeoutError: If execution exceeds timeout
            SkillExecutionError: If execution fails in sandbox
        """
        if self._sandbox is None:
            raise SkillSecurityError(
                "Sandbox not initialized - use 'async with SkillExecutor()' context",
                skill_name=skill_name,
                suggestion="Ensure SkillExecutor is used as async context manager",
            )

        context = context or {}
        timeout = timeout or self.config.timeout_seconds

        # Validate context size
        context_json = json.dumps(context)
        context_size = len(context_json.encode("utf-8"))

        if context_size > self.config.max_context_size:
            raise SkillExecutionError(
                f"Context size ({context_size} bytes) exceeds limit "
                f"({self.config.max_context_size} bytes)",
                skill_name=skill_name,
            )

        # Generate skill hash for caching/audit
        skill_hash = self._hash_skill(skill_code)

        # Generate wrapper script
        wrapper_script = self._generate_wrapper_script(skill_code, skill_name, context)

        start_time = time.time()
        sandbox_id = getattr(self._sandbox, "_sandbox_id", "unknown")

        logger.debug(
            f"Executing skill '{skill_name}'",
            extra={
                "skill_hash": skill_hash,
                "context_size": context_size,
                "timeout": timeout,
                "sandbox_id": sandbox_id,
            },
        )

        try:
            # Execute in sandbox
            exec_result = await self._sandbox.execute_code(
                wrapper_script,
                language="python",
                timeout=timeout,
            )

            duration_ms = int((time.time() - start_time) * 1000)

            # Check for timeout from sandbox
            if isinstance(exec_result.error, str) and "timeout" in exec_result.error.lower():
                self._log_audit(
                    skill_name=skill_name,
                    skill_hash=skill_hash,
                    context_size=context_size,
                    duration_ms=duration_ms,
                    success=False,
                    error=f"Timeout after {timeout}s",
                    sandbox_id=sandbox_id,
                )
                raise SkillTimeoutError(
                    f"Skill '{skill_name}' timed out after {timeout}s",
                    skill_name=skill_name,
                    timeout_seconds=timeout,
                )

            # Parse result
            result = self._parse_result(exec_result.stdout, exec_result.stderr)
            result.duration_ms = duration_ms

            # Validate output size
            if result.result is not None:
                result_json = json.dumps(result.result)
                result_size = len(result_json.encode("utf-8"))
                if result_size > self.config.max_output_size:
                    self._log_audit(
                        skill_name=skill_name,
                        skill_hash=skill_hash,
                        context_size=context_size,
                        duration_ms=duration_ms,
                        success=False,
                        error=f"Output size ({result_size} bytes) exceeds limit",
                        sandbox_id=sandbox_id,
                    )
                    raise SkillExecutionError(
                        f"Skill output size ({result_size} bytes) exceeds limit "
                        f"({self.config.max_output_size} bytes)",
                        skill_name=skill_name,
                    )

            # Log audit entry
            self._log_audit(
                skill_name=skill_name,
                skill_hash=skill_hash,
                context_size=context_size,
                duration_ms=duration_ms,
                success=result.success,
                error=result.error if not result.success else None,
                sandbox_id=sandbox_id,
            )

            return result

        except SandboxTimeoutError:
            duration_ms = int((time.time() - start_time) * 1000)
            self._log_audit(
                skill_name=skill_name,
                skill_hash=skill_hash,
                context_size=context_size,
                duration_ms=duration_ms,
                success=False,
                error=f"Timeout after {timeout}s",
                sandbox_id=sandbox_id,
            )
            raise SkillTimeoutError(
                f"Skill '{skill_name}' timed out after {timeout}s",
                skill_name=skill_name,
                timeout_seconds=timeout,
            )

        except SandboxExecutionError as e:
            duration_ms = int((time.time() - start_time) * 1000)
            self._log_audit(
                skill_name=skill_name,
                skill_hash=skill_hash,
                context_size=context_size,
                duration_ms=duration_ms,
                success=False,
                error=str(e),
                sandbox_id=sandbox_id,
            )
            raise SkillExecutionError(
                f"Skill '{skill_name}' execution failed: {e}",
                skill_name=skill_name,
            )

        except SandboxConfigurationError as e:
            raise SkillSecurityError(
                f"Sandbox configuration error: {e}",
                skill_name=skill_name,
                suggestion="Check E2B_API_KEY and sandbox configuration",
            )

    async def execute_skill_sync_wrapper(
        self,
        skill_code: str,
        skill_name: str,
        context: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> SkillResult:
        """Synchronous wrapper for execute_skill (for use in sync code).

        This method runs the async execute_skill in a new event loop if needed.

        Args:
            skill_code: Python code containing the skill function
            skill_name: Name of the function to call
            context: Input arguments as a dictionary
            timeout: Override timeout in seconds

        Returns:
            SkillResult with execution result or error
        """
        try:
            loop = asyncio.get_running_loop()
            # Already in async context, just await
            return await self.execute_skill(skill_code, skill_name, context, timeout)
        except RuntimeError:
            # No running loop, create one
            return asyncio.run(
                self.execute_skill(skill_code, skill_name, context, timeout)
            )

    def get_audit_log(self) -> List[SkillAuditEntry]:
        """Get the audit log of skill executions.

        Returns:
            List of audit log entries
        """
        return list(self._audit_log)

    def clear_audit_log(self) -> int:
        """Clear the audit log.

        Returns:
            Number of entries cleared
        """
        count = len(self._audit_log)
        self._audit_log.clear()
        return count


def load_skill_secure(
    skill_code: str,
    skill_name: str,
    executor: SkillExecutor,
) -> Callable[..., Any]:
    """Load a skill that executes securely in sandbox.

    This returns a callable that, when invoked, executes the skill
    in the sandbox with the provided arguments.

    Args:
        skill_code: Python code containing the skill function
        skill_name: Name of the function to call
        executor: SkillExecutor instance to use

    Returns:
        Callable that executes the skill in sandbox

    Example:
        ```python
        async with SkillExecutor() as executor:
            analyze = load_skill_secure(
                '''
                def analyze(code: str) -> dict:
                    return {"lines": len(code.split())}
                ''',
                "analyze",
                executor
            )

            # This executes in sandbox
            result = await analyze(code="def foo(): pass")
        ```
    """
    async def skill_wrapper(**kwargs: Any) -> Any:
        result = await executor.execute_skill(skill_code, skill_name, kwargs)
        if not result.success:
            raise SkillExecutionError(
                result.error or "Skill execution failed",
                skill_name=skill_name,
                error_type=result.error_type,
                traceback=result.traceback,
            )
        return result.result

    # Preserve metadata
    skill_wrapper.__name__ = skill_name
    skill_wrapper.__doc__ = f"Sandboxed skill: {skill_name}"

    return skill_wrapper


# =============================================================================
# Skill Caching Integration (REPO-313)
# =============================================================================


async def get_cached_skill(
    skill_id: str,
    cache: Optional["TwoTierSkillCache"] = None,
) -> Optional["CachedSkill"]:  # type: ignore[name-defined]
    """Get a cached skill by ID.

    Uses the two-tier skill cache (L1 local + L2 Redis) for fast lookups.

    Args:
        skill_id: Unique skill identifier
        cache: Optional cache instance (uses global if not provided)

    Returns:
        CachedSkill or None if not cached
    """
    if cache is None:
        try:
            from repotoire.cache import get_skill_cache

            cache = await get_skill_cache()
        except Exception as e:
            logger.warning(f"Could not get skill cache: {e}")
            return None

    return await cache.get(skill_id)


async def cache_skill(
    skill_id: str,
    skill_name: str,
    skill_code: str,
    source_path: Optional[str] = None,
    cache: Optional["TwoTierSkillCache"] = None,
) -> bool:
    """Cache a loaded skill for future use.

    Stores the skill in both L1 (local) and L2 (Redis) caches.

    Args:
        skill_id: Unique skill identifier
        skill_name: Human-readable skill name
        skill_code: Python source code
        source_path: Optional path to skill source file
        cache: Optional cache instance (uses global if not provided)

    Returns:
        True if successfully cached
    """
    if cache is None:
        try:
            from repotoire.cache import get_skill_cache

            cache = await get_skill_cache()
        except Exception as e:
            logger.warning(f"Could not get skill cache: {e}")
            return False

    from repotoire.cache import CachedSkill

    code_hash = hashlib.sha256(skill_code.encode("utf-8")).hexdigest()[:16]

    cached_skill = CachedSkill(
        skill_id=skill_id,
        skill_name=skill_name,
        skill_code=skill_code,
        code_hash=code_hash,
        loaded_at=datetime.now(timezone.utc).isoformat(),
        source_path=source_path,
    )

    return await cache.set(skill_id, cached_skill)


async def invalidate_skill_cache(
    skill_id: Optional[str] = None,
    cache: Optional["TwoTierSkillCache"] = None,
) -> bool:
    """Invalidate cached skills.

    Args:
        skill_id: Specific skill to invalidate, or None for all
        cache: Optional cache instance (uses global if not provided)

    Returns:
        True if successfully invalidated
    """
    if cache is None:
        try:
            from repotoire.cache import get_skill_cache

            cache = await get_skill_cache()
        except Exception as e:
            logger.warning(f"Could not get skill cache: {e}")
            return False

    if skill_id:
        return await cache.invalidate(skill_id)
    else:
        await cache.clear_all()
        return True

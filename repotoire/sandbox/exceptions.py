"""Custom exceptions for sandbox execution.

All sandbox-related exceptions inherit from SandboxError and include
contextual information for debugging (sandbox_id, operation, etc.).
"""

from typing import Optional


class SandboxError(Exception):
    """Base exception for all sandbox-related errors.

    Attributes:
        message: Human-readable error message
        sandbox_id: ID of the sandbox instance (if available)
        operation: The operation that failed (e.g., "execute_code", "upload_files")
    """

    def __init__(
        self,
        message: str,
        sandbox_id: Optional[str] = None,
        operation: Optional[str] = None,
    ):
        self.message = message
        self.sandbox_id = sandbox_id
        self.operation = operation

        # Build full message with context
        parts = [message]
        if sandbox_id:
            parts.append(f"sandbox_id={sandbox_id}")
        if operation:
            parts.append(f"operation={operation}")

        super().__init__(" | ".join(parts))


class SandboxConfigurationError(SandboxError):
    """Raised when sandbox configuration is invalid or missing.

    Common causes:
    - E2B_API_KEY environment variable not set
    - Invalid timeout or memory configuration
    - Missing required dependencies
    """

    def __init__(
        self,
        message: str,
        sandbox_id: Optional[str] = None,
        operation: Optional[str] = None,
        suggestion: Optional[str] = None,
    ):
        super().__init__(message, sandbox_id, operation)
        self.suggestion = suggestion


class SandboxTimeoutError(SandboxError):
    """Raised when sandbox operation exceeds the configured timeout.

    Attributes:
        timeout: The timeout value in seconds that was exceeded
    """

    def __init__(
        self,
        message: str,
        timeout: float,
        sandbox_id: Optional[str] = None,
        operation: Optional[str] = None,
    ):
        super().__init__(message, sandbox_id, operation)
        self.timeout = timeout


class SandboxExecutionError(SandboxError):
    """Raised when code execution fails within the sandbox.

    Attributes:
        exit_code: Exit code from the execution (if available)
        stdout: Standard output captured before failure
        stderr: Standard error captured before failure
    """

    def __init__(
        self,
        message: str,
        sandbox_id: Optional[str] = None,
        operation: Optional[str] = None,
        exit_code: Optional[int] = None,
        stdout: Optional[str] = None,
        stderr: Optional[str] = None,
    ):
        super().__init__(message, sandbox_id, operation)
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class SandboxResourceError(SandboxError):
    """Raised when sandbox resource limits are exceeded.

    Common causes:
    - Memory limit exceeded
    - CPU time limit exceeded
    - Disk space exhausted
    """

    def __init__(
        self,
        message: str,
        sandbox_id: Optional[str] = None,
        operation: Optional[str] = None,
        resource_type: Optional[str] = None,
        limit: Optional[str] = None,
    ):
        super().__init__(message, sandbox_id, operation)
        self.resource_type = resource_type
        self.limit = limit


# =============================================================================
# Skill Execution Exceptions (REPO-289)
# =============================================================================


class SkillError(Exception):
    """Base exception for all skill-related errors.

    Attributes:
        message: Human-readable error message
        skill_name: Name of the skill that caused the error
    """

    def __init__(
        self,
        message: str,
        skill_name: Optional[str] = None,
    ):
        self.message = message
        self.skill_name = skill_name

        # Build full message with context
        parts = [message]
        if skill_name:
            parts.append(f"skill={skill_name}")

        super().__init__(" | ".join(parts))


class SkillLoadError(SkillError):
    """Raised when a skill cannot be loaded.

    Common causes:
    - Skill code has syntax errors
    - Skill function not found after execution
    - Invalid skill name
    """

    def __init__(
        self,
        message: str,
        skill_name: Optional[str] = None,
        cause: Optional[Exception] = None,
    ):
        super().__init__(message, skill_name)
        self.cause = cause


class SkillExecutionError(SkillError):
    """Raised when skill execution fails in the sandbox.

    Attributes:
        error_type: Original exception type from sandbox
        traceback: Stack trace from sandbox (if available)
    """

    def __init__(
        self,
        message: str,
        skill_name: Optional[str] = None,
        error_type: Optional[str] = None,
        traceback: Optional[str] = None,
    ):
        super().__init__(message, skill_name)
        self.error_type = error_type
        self.traceback = traceback


class SkillTimeoutError(SkillError):
    """Raised when skill execution exceeds the configured timeout.

    Attributes:
        timeout_seconds: The timeout value that was exceeded
    """

    def __init__(
        self,
        message: str,
        skill_name: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
    ):
        super().__init__(message, skill_name)
        self.timeout_seconds = timeout_seconds


class SkillSecurityError(SkillError):
    """Raised when sandbox is unavailable and security prevents fallback.

    This exception is raised when:
    - E2B_API_KEY is not configured
    - Sandbox creation fails
    - Any situation where local exec() would be the only alternative

    SECURITY: Never fall back to local exec() - fail secure.

    Attributes:
        suggestion: Helpful message for resolving the issue
    """

    def __init__(
        self,
        message: str,
        skill_name: Optional[str] = None,
        suggestion: Optional[str] = None,
    ):
        super().__init__(message, skill_name)
        self.suggestion = suggestion

"""E2B sandbox module for secure code execution.

This module provides secure cloud sandbox execution for running untrusted code,
tests, analysis tools, and skill code in isolated E2B environments.

Usage:
    ```python
    from repotoire.sandbox import SandboxExecutor, SandboxConfig

    config = SandboxConfig.from_env()

    async with SandboxExecutor(config) as sandbox:
        # Execute Python code
        result = await sandbox.execute_code('''
            import sys
            print(f"Python {sys.version}")
        ''')
        print(result.stdout)

        # Run shell commands
        cmd_result = await sandbox.execute_command("ls -la /code")
        print(cmd_result.stdout)

        # Upload files
        await sandbox.upload_files([Path("src/module.py")])

        # Run tests
        test_result = await sandbox.execute_command("pytest tests/ -v")
    ```

Configuration:
    Set these environment variables:
    - E2B_API_KEY: Required API key for E2B service
    - E2B_TIMEOUT_SECONDS: Execution timeout (default: 300)
    - E2B_MEMORY_MB: Memory limit in MB (default: 1024)
    - E2B_CPU_COUNT: CPU core count (default: 1)

Graceful Degradation:
    When E2B_API_KEY is not set, the module will:
    1. Log a warning on initialization
    2. Raise SandboxConfigurationError with helpful message when used
    This allows development without E2B while clearly indicating
    sandbox features are unavailable.
"""

from repotoire.sandbox.client import (
    SandboxExecutor,
    ExecutionResult,
    CommandResult,
)
from repotoire.sandbox.config import SandboxConfig
from repotoire.sandbox.exceptions import (
    SandboxError,
    SandboxConfigurationError,
    SandboxExecutionError,
    SandboxTimeoutError,
    SandboxResourceError,
    # Skill-specific exceptions (REPO-289)
    SkillError,
    SkillLoadError,
    SkillExecutionError,
    SkillTimeoutError,
    SkillSecurityError,
)
from repotoire.sandbox.skill_executor import (
    SkillExecutor,
    SkillExecutorConfig,
    SkillResult,
    SkillAuditEntry,
    load_skill_secure,
)
from repotoire.sandbox.test_executor import (
    TestExecutor,
    TestExecutorConfig,
    TestResult,
    PytestOutputParser,
    FileFilter,
    DEFAULT_EXCLUDE_PATTERNS,
    run_tests_sync,
)
from repotoire.sandbox.code_validator import (
    CodeValidator,
    ValidationConfig,
    ValidationResult,
    ValidationError,
    ValidationWarning,
    ValidationLevel,
    validate_syntax_only,
)
from repotoire.sandbox.tool_executor import (
    ToolExecutor,
    ToolExecutorConfig,
    ToolExecutorResult,
    SecretFileFilter,
    DEFAULT_SENSITIVE_PATTERNS,
    run_tool_sync,
)

__all__ = [
    # Main client
    "SandboxExecutor",
    # Skill executor (REPO-289)
    "SkillExecutor",
    "SkillExecutorConfig",
    "SkillResult",
    "SkillAuditEntry",
    "load_skill_secure",
    # Test executor (REPO-290)
    "TestExecutor",
    "TestExecutorConfig",
    "TestResult",
    "PytestOutputParser",
    "FileFilter",
    "DEFAULT_EXCLUDE_PATTERNS",
    "run_tests_sync",
    # Configuration
    "SandboxConfig",
    # Result types
    "ExecutionResult",
    "CommandResult",
    # Sandbox exceptions
    "SandboxError",
    "SandboxConfigurationError",
    "SandboxExecutionError",
    "SandboxTimeoutError",
    "SandboxResourceError",
    # Skill exceptions (REPO-289)
    "SkillError",
    "SkillLoadError",
    "SkillExecutionError",
    "SkillTimeoutError",
    "SkillSecurityError",
    # Code validator (REPO-291)
    "CodeValidator",
    "ValidationConfig",
    "ValidationResult",
    "ValidationError",
    "ValidationWarning",
    "ValidationLevel",
    "validate_syntax_only",
    # Tool executor (REPO-292)
    "ToolExecutor",
    "ToolExecutorConfig",
    "ToolExecutorResult",
    "SecretFileFilter",
    "DEFAULT_SENSITIVE_PATTERNS",
    "run_tool_sync",
]

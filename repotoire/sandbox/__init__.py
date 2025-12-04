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

Trial Mode:
    New users get 50 free sandbox executions to try the service.
    After trial, a subscription is required. Usage is tracked via
    SandboxMetricsCollector.
"""

from repotoire.sandbox.client import (
    SandboxExecutor,
    ExecutionResult,
    CommandResult,
)
from repotoire.sandbox.config import SandboxConfig, DEFAULT_TRIAL_EXECUTIONS
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
from repotoire.sandbox.tiers import (
    TierSandboxConfig,
    TIER_SANDBOX_CONFIGS,
    TEMPLATE_ANALYZER,
    TEMPLATE_ENTERPRISE,
    get_sandbox_config_for_tier,
    get_template_for_tier,
    tier_has_rust,
)
from repotoire.sandbox.metrics import (
    SandboxMetrics,
    SandboxMetricsCollector,
    calculate_cost,
    track_sandbox_operation,
    get_metrics_collector,
    CPU_RATE_PER_SECOND,
    MEMORY_RATE_PER_GB_SECOND,
    MINIMUM_CHARGE,
)
from repotoire.sandbox.alerts import (
    AlertEvent,
    AlertManager,
    CostThresholdAlert,
    FailureRateAlert,
    SlowOperationAlert,
    SlackChannel,
    EmailChannel,
    WebhookChannel,
    run_alert_check,
)
from repotoire.sandbox.trial import (
    TrialManager,
    TrialStatus,
    TrialLimitExceeded,
    TIER_EXECUTION_LIMITS,
    get_trial_manager,
    check_trial_limit,
)
from repotoire.sandbox.quotas import (
    SandboxQuota,
    QuotaOverride,
    TIER_QUOTAS,
    get_quota_for_tier,
    get_default_quota,
    apply_override,
)
from repotoire.sandbox.usage import (
    SandboxUsageTracker,
    UsageSummary,
    ConcurrentSession,
    get_usage_tracker,
)
from repotoire.sandbox.session_tracker import (
    DistributedSessionTracker,
    SessionInfo,
    SessionTrackerError,
    SessionTrackerUnavailableError,
    get_session_tracker,
    close_session_tracker,
)
from repotoire.sandbox.enforcement import (
    QuotaEnforcer,
    QuotaExceededError,
    QuotaCheckResult,
    QuotaStatus,
    QuotaType,
    QuotaWarningLevel,
    get_quota_enforcer,
)
from repotoire.sandbox.override_service import (
    QuotaOverrideService,
    get_override_service,
    get_redis_client,
    close_redis_client,
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
    "DEFAULT_TRIAL_EXECUTIONS",
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
    # Tier-based templates (REPO-294)
    "TierSandboxConfig",
    "TIER_SANDBOX_CONFIGS",
    "TEMPLATE_ANALYZER",
    "TEMPLATE_ENTERPRISE",
    "get_sandbox_config_for_tier",
    "get_template_for_tier",
    "tier_has_rust",
    # Metrics and cost tracking (REPO-295)
    "SandboxMetrics",
    "SandboxMetricsCollector",
    "calculate_cost",
    "track_sandbox_operation",
    "get_metrics_collector",
    "CPU_RATE_PER_SECOND",
    "MEMORY_RATE_PER_GB_SECOND",
    "MINIMUM_CHARGE",
    # Alerting (REPO-295)
    "AlertEvent",
    "AlertManager",
    "CostThresholdAlert",
    "FailureRateAlert",
    "SlowOperationAlert",
    "SlackChannel",
    "EmailChannel",
    "WebhookChannel",
    "run_alert_check",
    # Trial management (REPO-296)
    "TrialManager",
    "TrialStatus",
    "TrialLimitExceeded",
    "TIER_EXECUTION_LIMITS",
    "get_trial_manager",
    "check_trial_limit",
    # Quota management (REPO-299)
    "SandboxQuota",
    "QuotaOverride",
    "TIER_QUOTAS",
    "get_quota_for_tier",
    "get_default_quota",
    "apply_override",
    # Usage tracking (REPO-299)
    "SandboxUsageTracker",
    "UsageSummary",
    "ConcurrentSession",
    "get_usage_tracker",
    # Session tracking (REPO-311)
    "DistributedSessionTracker",
    "SessionInfo",
    "SessionTrackerError",
    "SessionTrackerUnavailableError",
    "get_session_tracker",
    "close_session_tracker",
    # Quota enforcement (REPO-299)
    "QuotaEnforcer",
    "QuotaExceededError",
    "QuotaCheckResult",
    "QuotaStatus",
    "QuotaType",
    "QuotaWarningLevel",
    "get_quota_enforcer",
    # Override service (REPO-312)
    "QuotaOverrideService",
    "get_override_service",
    "get_redis_client",
    "close_redis_client",
]

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

from repotoire.sandbox.alerts import (
    AlertEvent,
    AlertManager,
    CostThresholdAlert,
    EmailChannel,
    FailureRateAlert,
    SlackChannel,
    SlowOperationAlert,
    WebhookChannel,
    run_alert_check,
)
from repotoire.sandbox.billing import (
    SANDBOX_MINUTE_RATE_USD,
    SandboxBillingError,
    SandboxBillingService,
    get_sandbox_billing_service,
    report_sandbox_usage_to_stripe,
    reset_sandbox_billing_service,
)
from repotoire.sandbox.client import (
    CommandResult,
    ExecutionResult,
    SandboxExecutor,
)
from repotoire.sandbox.code_validator import (
    CodeValidator,
    ValidationConfig,
    ValidationError,
    ValidationLevel,
    ValidationResult,
    ValidationWarning,
    validate_syntax_only,
)
from repotoire.sandbox.config import DEFAULT_TRIAL_EXECUTIONS, SandboxConfig
from repotoire.sandbox.enforcement import (
    QuotaCheckResult,
    QuotaEnforcer,
    QuotaExceededError,
    QuotaStatus,
    QuotaType,
    QuotaWarningLevel,
    get_quota_enforcer,
)
from repotoire.sandbox.exceptions import (
    SandboxConfigurationError,
    SandboxError,
    SandboxExecutionError,
    SandboxResourceError,
    SandboxTimeoutError,
    # Skill-specific exceptions (REPO-289)
    SkillError,
    SkillExecutionError,
    SkillLoadError,
    SkillSecurityError,
    SkillTimeoutError,
)
from repotoire.sandbox.metrics import (
    CPU_RATE_PER_SECOND,
    MEMORY_RATE_PER_GB_SECOND,
    MINIMUM_CHARGE,
    SandboxMetrics,
    SandboxMetricsCollector,
    calculate_cost,
    get_metrics_collector,
    track_sandbox_operation,
)
from repotoire.sandbox.override_service import (
    QuotaOverrideService,
    close_redis_client,
    get_override_service,
    get_redis_client,
)
from repotoire.sandbox.quotas import (
    TIER_QUOTAS,
    QuotaOverride,
    SandboxQuota,
    apply_override,
    get_default_quota,
    get_quota_for_tier,
)
from repotoire.sandbox.session_tracker import (
    DistributedSessionTracker,
    SessionInfo,
    SessionTrackerError,
    SessionTrackerUnavailableError,
    close_session_tracker,
    get_session_tracker,
)
from repotoire.sandbox.skill_executor import (
    SkillAuditEntry,
    SkillExecutor,
    SkillExecutorConfig,
    SkillResult,
    load_skill_secure,
)
from repotoire.sandbox.test_executor import (
    DEFAULT_EXCLUDE_PATTERNS,
    FileFilter,
    PytestOutputParser,
    TestExecutor,
    TestExecutorConfig,
    TestResult,
    run_tests_sync,
)
from repotoire.sandbox.tiers import (
    TEMPLATE_ANALYZER,
    TEMPLATE_ENTERPRISE,
    TIER_SANDBOX_CONFIGS,
    TierSandboxConfig,
    get_sandbox_config_for_tier,
    get_template_for_tier,
    tier_has_rust,
)
from repotoire.sandbox.tool_executor import (
    DEFAULT_SENSITIVE_PATTERNS,
    SecretFileFilter,
    ToolExecutor,
    ToolExecutorConfig,
    ToolExecutorResult,
    run_tool_sync,
)
from repotoire.sandbox.trial import (
    TIER_EXECUTION_LIMITS,
    TrialLimitExceeded,
    TrialManager,
    TrialStatus,
    check_trial_limit,
    get_trial_manager,
)
from repotoire.sandbox.usage import (
    ConcurrentSession,
    SandboxUsageTracker,
    UsageSummary,
    get_usage_tracker,
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
    # Billing service (REPO-313)
    "SandboxBillingService",
    "SandboxBillingError",
    "get_sandbox_billing_service",
    "reset_sandbox_billing_service",
    "report_sandbox_usage_to_stripe",
    "SANDBOX_MINUTE_RATE_USD",
]

# E2B Sandbox: Secure Isolated Execution

Repotoire uses [E2B](https://e2b.dev) cloud sandboxes to provide secure, isolated execution environments for running untrusted code, tests, analysis tools, and MCP skills without exposing your host system or secrets.

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Security Model](#security-model)
- [Configuration Reference](#configuration-reference)
- [Executor Types](#executor-types)
- [Custom Templates](#custom-templates)
- [Subscription Tiers](#subscription-tiers)
- [Metrics & Cost Tracking](#metrics--cost-tracking)
- [Alerting](#alerting)
- [Trial Mode](#trial-mode)
- [CLI Reference](#cli-reference)
- [Performance](#performance)
- [Troubleshooting](#troubleshooting)
- [API Reference](#api-reference)

## Overview

The sandbox module provides secure execution for:

- **Test Execution** (`TestExecutor`): Run pytest in isolation to validate auto-fix changes
- **Tool Execution** (`ToolExecutor`): Run analysis tools (ruff, bandit, mypy, etc.) safely
- **Skill Execution** (`SkillExecutor`): Execute MCP skill code without local `exec()`
- **Code Validation** (`CodeValidator`): Multi-level validation of AI-generated fixes

### Why Sandboxed Execution?

| Risk | Without Sandbox | With Sandbox |
|------|-----------------|--------------|
| Malicious test code | Could access host filesystem | Isolated, no host access |
| Secret exposure | `.env` files readable by tools | Secrets filtered before upload |
| Resource exhaustion | Could crash host system | Hard limits on CPU/memory |
| Network attacks | Tools could exfiltrate data | Monitored egress, separate IP |
| Credential theft | Analysis tools see all credentials | Credentials never uploaded |

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Repotoire Host                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌───────────────┐    ┌────────────────────────────────────────────────┐   │
│  │  User Request │───▶│            Executor Selection                   │   │
│  │ (analyze/fix) │    │                                                 │   │
│  └───────────────┘    │  SandboxConfig.from_env() checks:               │   │
│                       │    • E2B_API_KEY set? → Cloud sandbox           │   │
│                       │    • Fallback enabled? → Local (with warning)   │   │
│                       │    • Neither? → Fail secure                     │   │
│                       └──────────────────┬─────────────────────────────┘   │
│                                          │                                  │
│                       ┌──────────────────┼──────────────────┐              │
│                       ▼                  ▼                  ▼              │
│               ┌───────────────┐  ┌───────────────┐  ┌────────────────┐    │
│               │ TestExecutor  │  │ ToolExecutor  │  │ SkillExecutor  │    │
│               │  (pytest)     │  │ (ruff, etc.)  │  │  (MCP skills)  │    │
│               └───────┬───────┘  └───────┬───────┘  └───────┬────────┘    │
│                       │                  │                   │             │
│                       └──────────────────┼───────────────────┘             │
│                                          │                                  │
│                       ┌──────────────────▼──────────────────┐              │
│                       │        SecretFileFilter             │              │
│                       │  • Excludes .env, *.key, *.pem     │              │
│                       │  • Excludes credentials.json       │              │
│                       │  • Preserves tool configs          │              │
│                       └──────────────────┬──────────────────┘              │
│                                          │                                  │
└──────────────────────────────────────────┼──────────────────────────────────┘
                                           │
                                           ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        E2B Cloud Sandbox (Firecracker microVM)              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Isolated Environment                                                │   │
│  │                                                                      │   │
│  │  • Separate filesystem (/code/)                                     │   │
│  │  • Pre-installed tools (ruff, bandit, mypy, semgrep, pytest)       │   │
│  │  • Resource limits (CPU, memory, time)                              │   │
│  │  • Separate network namespace                                       │   │
│  │  • No access to host secrets                                        │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  Output: stdout, stderr, exit_code, artifacts                               │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Quick Start

### 1. Create E2B Account

1. Go to [e2b.dev](https://e2b.dev) and sign up
2. Navigate to Dashboard → API Keys
3. Create a new API key

### 2. Configure API Key

```bash
# Option 1: Environment variable (recommended)
export E2B_API_KEY="e2b_xxx_your_key_here"

# Option 2: .env file
echo "E2B_API_KEY=e2b_xxx_your_key" >> .env
```

### 3. Install Dependencies

```bash
# Install with sandbox support
pip install repotoire[sandbox]

# Or with full features
pip install repotoire[all]
```

### 4. Verify Setup

```python
from repotoire.sandbox import SandboxConfig

config = SandboxConfig.from_env()
print(f"Sandbox configured: {config.is_configured}")
```

### 5. Run Analysis with Sandbox

```bash
# Analysis tools run in sandbox automatically when E2B_API_KEY is set
repotoire analyze /path/to/repo

# Run tests in sandbox during auto-fix
repotoire auto-fix /path/to/repo --sandbox-tests
```

## Security Model

### What IS Isolated

E2B sandboxes provide hardware-level isolation via Firecracker microVMs:

| Resource | Isolation Level | Details |
|----------|-----------------|---------|
| Filesystem | **Complete** | Sandbox has its own filesystem, no access to host |
| Processes | **Complete** | Cannot see or interact with host processes |
| Network | **Isolated** | Separate network namespace, egress monitored |
| Environment | **Isolated** | Only explicitly passed env vars available |
| Memory | **Complete** | Hard memory limits enforced by hypervisor |
| CPU | **Complete** | CPU limits enforced, no side-channel access |

### What is NOT Isolated

Be aware of these limitations:

| Vector | Risk Level | Details |
|--------|------------|---------|
| Timing attacks | Low | Execution time visible to caller |
| Network egress | Low | Code can make outbound requests (monitored) |
| E2B API key | N/A | Stored on host, never sent to sandbox |

### Secret Protection

Repotoire automatically filters sensitive files **before** uploading to sandbox:

```
Files NEVER uploaded:
├── .env, .env.*, *.env           # Environment files
├── .git/config, .gitconfig       # Git credentials
├── *.pem, *.key, id_rsa*         # SSH/TLS keys
├── .aws/, .gcloud/, .azure/      # Cloud credentials
├── credentials.json              # Service account keys
├── *secret*, *password*, *token* # Named secrets
├── .npmrc, .pypirc               # Package manager tokens
├── *.tfstate                     # Terraform state
└── .kube/config                  # Kubernetes config
```

Configure additional exclusions:

```yaml
# .repotoirerc
sandbox:
  exclude_patterns:
    - "internal_secrets/"
    - "**/api_keys.yaml"
    - "config/production.json"
```

### Audit Logging

All sandbox operations are logged for security audit:

```python
# SkillExecutor audit log
executor = SkillExecutor()
async with executor:
    result = await executor.execute_skill(code, "my_skill", {})

# Get audit log
for entry in executor.get_audit_log():
    print(f"{entry.timestamp}: {entry.skill_name} - {entry.success}")
```

Logged information includes:
- Timestamp
- Operation type
- Skill/tool hash (content fingerprint)
- Duration
- Success/failure
- Error messages (if any)
- Sandbox ID

## Configuration Reference

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `E2B_API_KEY` | Yes* | - | E2B API key for cloud sandbox |
| `E2B_TIMEOUT_SECONDS` | No | 300 | Default execution timeout (10-3600) |
| `E2B_MEMORY_MB` | No | 1024 | Memory limit in MB (256-16384) |
| `E2B_CPU_COUNT` | No | 1 | CPU core count (1-8) |
| `E2B_SANDBOX_TEMPLATE` | No | - | Custom template name |
| `SANDBOX_TRIAL_EXECUTIONS` | No | 50 | Free trial executions |
| `SANDBOX_TOOLS_ENABLED` | No | true | Enable sandbox for tools |
| `SANDBOX_FALLBACK_LOCAL` | No | true | Allow local fallback |
| `TOOL_TIMEOUT_SECONDS` | No | 300 | Tool execution timeout |
| `TEST_TIMEOUT_SECONDS` | No | 300 | Test execution timeout |
| `REPOTOIRE_TIMESCALE_URI` | No | - | TimescaleDB for metrics |

*Required for cloud sandbox; optional if using local fallback (with security warnings).

### Config File Options

```yaml
# .repotoirerc or repotoire.yaml
sandbox:
  # Execution settings
  timeout_seconds: 300
  memory_mb: 1024
  cpu_count: 1

  # Tool execution
  tools_enabled: true
  tool_timeout_seconds: 300
  fallback_local: true

  # Test execution
  test_timeout_seconds: 300
  test_install_command: "pip install -e ."
  test_artifacts:
    - "coverage.xml"
    - ".coverage"
    - "junit.xml"

  # File filtering
  exclude_patterns:
    - ".env*"
    - "*.key"
    - "internal_secrets/"

  # Validation
  run_import_check: true
  run_type_check: false
  fail_on_type_errors: false
```

### Code-Level Configuration

```python
from repotoire.sandbox import (
    SandboxConfig,
    TestExecutorConfig,
    ToolExecutorConfig,
    SkillExecutorConfig,
    ValidationConfig,
)

# Basic sandbox config
sandbox_config = SandboxConfig(
    api_key="e2b_xxx",
    timeout_seconds=300,
    memory_mb=1024,
    cpu_count=1,
    sandbox_template="repotoire-analyzer",
)

# Or load from environment
sandbox_config = SandboxConfig.from_env()

# Or load from subscription tier
sandbox_config = SandboxConfig.from_tier(PlanTier.PRO)

# Test executor config
test_config = TestExecutorConfig(
    sandbox_config=sandbox_config,
    test_timeout_seconds=300,
    install_command="pip install -e .",
    artifacts_to_download=["coverage.xml"],
)

# Tool executor config
tool_config = ToolExecutorConfig(
    sandbox_config=sandbox_config,
    tool_timeout_seconds=300,
    sensitive_patterns=["custom_secret_*"],
    fallback_local=False,
)

# Skill executor config
skill_config = SkillExecutorConfig(
    timeout_seconds=60,
    memory_mb=512,
    max_output_size=10 * 1024 * 1024,
    enable_audit_log=True,
)

# Code validation config
validation_config = ValidationConfig(
    run_import_check=True,
    run_type_check=True,
    run_smoke_test=False,
    fail_on_type_errors=False,
    timeout_seconds=30,
)
```

## Executor Types

### TestExecutor

Runs pytest in isolated sandbox for validating auto-fix changes:

```python
from repotoire.sandbox import TestExecutor, TestExecutorConfig

config = TestExecutorConfig.from_env()
executor = TestExecutor(config)

result = await executor.run_tests(
    repo_path=Path("/path/to/repo"),
    command="pytest tests/ -v --cov=src",
    env_vars={"DATABASE_URL": "sqlite:///:memory:"},
    timeout=300,
)

if result.success:
    print(f"Tests passed: {result.tests_passed}/{result.tests_total}")
    print(f"Coverage: {result.coverage_percent}%")
else:
    print(f"Tests failed: {result.stderr}")

# Download test artifacts
for name, content in result.artifacts.items():
    print(f"Artifact: {name} ({len(content)} bytes)")
```

### ToolExecutor

Runs external analysis tools (ruff, bandit, mypy, etc.) safely:

```python
from repotoire.sandbox import ToolExecutor, ToolExecutorConfig

config = ToolExecutorConfig.from_env()
executor = ToolExecutor(config)

result = await executor.execute_tool(
    repo_path=Path("/path/to/repo"),
    command="ruff check --output-format=json .",
    tool_name="ruff",
    timeout=60,
)

print(f"Tool: {result.tool_name}")
print(f"Files uploaded: {result.files_uploaded}")
print(f"Files excluded: {result.files_excluded}")
print(f"Duration: {result.duration_ms}ms")

if result.success:
    import json
    findings = json.loads(result.stdout)
```

### SkillExecutor

Executes MCP skill code securely (replaces dangerous `exec()`):

```python
from repotoire.sandbox import SkillExecutor, SkillExecutorConfig

config = SkillExecutorConfig(timeout_seconds=60)

async with SkillExecutor(config) as executor:
    result = await executor.execute_skill(
        skill_code='''
def analyze(code: str) -> dict:
    lines = code.split("\\n")
    return {
        "line_count": len(lines),
        "char_count": len(code),
    }
''',
        skill_name="analyze",
        context={"code": "def hello():\\n    pass"},
    )

    if result.success:
        print(f"Result: {result.result}")
    else:
        print(f"Error: {result.error}")
```

**Security Note**: SkillExecutor **never** falls back to local `exec()`. If sandbox is unavailable, it raises `SkillSecurityError`.

### CodeValidator

Multi-level validation for AI-generated code fixes:

```python
from repotoire.sandbox import CodeValidator, ValidationConfig

config = ValidationConfig(
    run_import_check=True,
    run_type_check=True,
    fail_on_type_errors=False,
)

async with CodeValidator(config) as validator:
    result = await validator.validate(
        fixed_code="def greet(name: str) -> str:\\n    return f'Hello, {name}'",
        file_path="src/greet.py",
        original_code="def greet(name): return 'Hello, ' + name",
    )

    print(f"Valid: {result.is_valid}")
    print(f"Syntax: {result.syntax_valid}")
    print(f"Import: {result.import_valid}")
    print(f"Type: {result.type_valid}")

    for error in result.errors:
        print(f"Error [{error.level}]: {error.message}")
```

Validation levels:
1. **Syntax** (Level 1): Fast, local `ast.parse()` - always runs
2. **Import** (Level 2): Can the module be imported? - catches ImportError, NameError
3. **Type** (Level 3): Does mypy pass? - optional, non-blocking by default
4. **Smoke** (Level 4): Can key functions be called? - optional, planned

## Custom Templates

Repotoire provides custom E2B templates with pre-installed tools for faster startup:

### Available Templates

| Template | Tier | Tools | Startup |
|----------|------|-------|---------|
| `repotoire-analyzer` | FREE | ruff, bandit, mypy, semgrep, pytest, jscpd | ~5-10s |
| `repotoire-enterprise` | PRO/ENTERPRISE | All above + Rust extensions | ~5-10s |

### Building Templates

```bash
# Navigate to template directory
cd e2b-templates/repotoire-analyzer

# Build template (requires E2B CLI)
e2b template build

# Test template
e2b sandbox spawn --template repotoire-analyzer
```

### Template Configuration

```toml
# e2b-templates/repotoire-analyzer/e2b.toml
[template]
name = "repotoire-analyzer"

[build]
dockerfile = "Dockerfile"

[sandbox]
cpu = 2
memory = 2048
timeout = 300
```

### Using Custom Templates

```bash
# Via environment variable
export E2B_SANDBOX_TEMPLATE="repotoire-analyzer"

# Via config file
sandbox:
  template: "repotoire-analyzer"

# Via code
config = SandboxConfig(
    api_key="...",
    sandbox_template="repotoire-analyzer",
)
```

## Subscription Tiers

Sandbox configuration varies by subscription tier:

| Aspect | FREE | PRO | ENTERPRISE |
|--------|------|-----|------------|
| Template | `repotoire-analyzer` | `repotoire-enterprise` | `repotoire-enterprise` |
| Timeout | 300s (5 min) | 600s (10 min) | 600s (10 min) |
| Memory | 2 GB | 4 GB | 4 GB |
| CPU | 2 cores | 4 cores | 4 cores |
| Rust Extensions | No | Yes | Yes |
| Executions/month | 50 (trial) | 5,000 | Unlimited |

Load tier-specific config:

```python
from repotoire.sandbox import SandboxConfig
from repotoire.db.models import PlanTier

# Automatically uses tier-appropriate template and limits
config = SandboxConfig.from_tier(PlanTier.PRO)
```

## Metrics & Cost Tracking

Track sandbox usage and costs with TimescaleDB integration:

### Enable Metrics

```bash
# Start TimescaleDB
cd docker/timescaledb && docker compose up -d

# Configure connection
export REPOTOIRE_TIMESCALE_URI="postgresql://repotoire:password@localhost:5432/repotoire_metrics"
```

### E2B Pricing

| Resource | Rate |
|----------|------|
| CPU | $0.000014/CPU-second |
| Memory | $0.0000025/GB-second |
| Minimum | $0.001/session |

**Example**: 60s execution with 2 CPUs, 2GB RAM = ~$0.002

### Using Metrics Collector

```python
from repotoire.sandbox import (
    SandboxMetricsCollector,
    track_sandbox_operation,
    calculate_cost,
)

# Manual cost calculation
cost = calculate_cost(
    duration_seconds=60,
    cpu_count=2,
    memory_gb=2.0,
)
print(f"Estimated cost: ${cost:.4f}")

# Track operation with context manager
async with track_sandbox_operation(
    operation_type="test_execution",
    customer_id="cust_123",
    cpu_count=2,
    memory_mb=2048,
) as metrics:
    result = await sandbox.execute_command("pytest tests/")
    metrics.exit_code = result.exit_code
    metrics.success = result.success
# Metrics automatically recorded to TimescaleDB
```

### CLI Stats

```bash
# Show summary for last 30 days
repotoire sandbox-stats

# Show last 7 days with breakdown
repotoire sandbox-stats --period 7 --by-type

# Show slow operations
repotoire sandbox-stats --slow

# Show failures
repotoire sandbox-stats --failures

# Admin: Top customers by cost
repotoire sandbox-stats --top-customers 10 --json-output
```

## Alerting

Set up alerts for cost thresholds, failure rates, and slow operations:

### Configure Alerts

```python
from repotoire.sandbox import (
    AlertManager,
    CostThresholdAlert,
    FailureRateAlert,
    SlowOperationAlert,
    SlackChannel,
    EmailChannel,
)

# Create alert manager
manager = AlertManager()

# Add notification channels
manager.add_channel(SlackChannel(webhook_url="https://hooks.slack.com/..."))
manager.add_channel(EmailChannel(to_emails=["ops@company.com"]))

# Register alerts
manager.register(CostThresholdAlert(threshold_usd=10.0, period_hours=24))
manager.register(FailureRateAlert(threshold_percent=10.0, period_hours=1))
manager.register(SlowOperationAlert(threshold_ms=30000))

# Check alerts (call periodically)
events = await manager.check_all()
```

### Environment-Based Setup

```bash
# Slack alerts
export SLACK_WEBHOOK_URL="https://hooks.slack.com/..."

# Email alerts
export SMTP_HOST="smtp.sendgrid.net"
export SMTP_PORT="587"
export SMTP_USER="apikey"
export SMTP_PASSWORD="SG.xxx"
export ALERT_TO_EMAILS="ops@company.com,team@company.com"

# Thresholds
export ALERT_COST_THRESHOLD="10.0"
export ALERT_FAILURE_RATE_THRESHOLD="10.0"
export ALERT_SLOW_OPERATION_MS="30000"
```

## Trial Mode

New users get free sandbox executions to try the service:

### Trial Limits

| Tier | Executions | Period |
|------|------------|--------|
| Trial | 50 | One-time |
| Pro | 5,000 | Monthly (resets) |
| Enterprise | Unlimited | - |

### Check Trial Status

```python
from repotoire.sandbox import TrialManager

manager = TrialManager()
await manager.connect()

status = await manager.get_trial_status("customer_123")
print(f"Used: {status.executions_used}/{status.executions_limit}")
print(f"Remaining: {status.executions_remaining}")
print(f"On trial: {status.is_trial}")
print(f"Exceeded: {status.is_exceeded}")
```

### Using Trial Decorator

```python
from repotoire.sandbox import check_trial_limit

@check_trial_limit
async def run_sandbox_operation(customer_id: str, code: str):
    # Only runs if customer has remaining executions
    # Automatically increments usage on success
    ...
```

## CLI Reference

### `repotoire sandbox-stats`

Show sandbox execution metrics and cost statistics.

```bash
repotoire sandbox-stats [OPTIONS]

Options:
  -p, --period INTEGER       Days to look back (default: 30)
  -c, --customer-id TEXT     Filter by customer ID
  --by-type                  Show breakdown by operation type
  --slow                     Show slow operations (>10s)
  --failures                 Show recent failures
  --top-customers INTEGER    Show top N customers by cost
  --json-output              Output as JSON
```

### Auto-Fix with Sandbox Tests

```bash
# Run tests in sandbox after applying fixes
repotoire auto-fix /path/to/repo --sandbox-tests

# Run tests locally (faster but less secure)
repotoire auto-fix /path/to/repo --local-tests
```

## Performance

### Startup Times

| Configuration | Time |
|--------------|------|
| Default E2B template | ~30-60s |
| Custom `repotoire-analyzer` template | ~5-10s |
| Subsequent operations (sandbox reused) | <1s |

### Cost Estimates

| Operation | Duration | Cost |
|-----------|----------|------|
| Single tool (ruff) | ~5s | ~$0.001 |
| Full analysis (8 tools) | ~60s | ~$0.002 |
| Test suite (small) | ~30s | ~$0.001 |
| Test suite (large) | ~300s | ~$0.005 |

### Optimization Tips

1. **Use custom templates**: Pre-installed tools avoid installation time
2. **Batch operations**: Reuse sandbox for multiple tools
3. **Set appropriate timeouts**: Don't over-provision time limits
4. **Filter files aggressively**: Upload only necessary files
5. **Enable local fallback for dev**: Use `SANDBOX_FALLBACK_LOCAL=true` during development

## Troubleshooting

### E2B API Key Not Found

**Error:**
```
SandboxConfigurationError: E2B API key required for sandbox execution
```

**Solutions:**
```bash
# Set the environment variable
export E2B_API_KEY="e2b_xxx_your_key"

# Or use local fallback (with warning)
export SANDBOX_FALLBACK_LOCAL=true
```

### Sandbox Timeout

**Error:**
```
SandboxTimeoutError: Execution timed out after 300 seconds
```

**Causes:**
- Long-running tests
- Infinite loops in analyzed code
- Large file uploads

**Solutions:**
```bash
# Increase timeout
export E2B_TIMEOUT_SECONDS=600

# Or via config
sandbox:
  timeout_seconds: 600
```

### File Upload Failed

**Error:**
```
SandboxExecutionError: Failed to upload file.py: ...
```

**Causes:**
- File too large
- Binary file encoding issues
- Permission errors

**Solutions:**
- Check file size limits
- Exclude large/binary files
- Verify file permissions

### Tool Not Found in Sandbox

**Error:**
```
Command 'semgrep' not found
```

**Cause:** Using default E2B template without pre-installed tools.

**Solution:** Use custom Repotoire template:
```bash
export E2B_SANDBOX_TEMPLATE="repotoire-analyzer"

# Or build custom template
cd e2b-templates/repotoire-analyzer
e2b template build
```

### Local Fallback Warning

**Warning:**
```
WARNING: Secrets may be exposed to the tool.
```

**Cause:** Running without E2B API key with fallback enabled.

**Solutions:**
1. Get E2B API key from https://e2b.dev
2. Or disable fallback for production: `SANDBOX_FALLBACK_LOCAL=false`

### Memory Limit Exceeded

**Error:**
```
SandboxResourceError: Sandbox memory limit exceeded
```

**Solutions:**
```bash
# Increase memory limit
export E2B_MEMORY_MB=2048

# Or use PRO tier for more resources
config = SandboxConfig.from_tier(PlanTier.PRO)
```

### Trial Limit Exceeded

**Error:**
```
TrialLimitExceeded: Trial limit exceeded (50/50 executions).
```

**Solution:** Upgrade at https://repotoire.dev/pricing

## API Reference

### Core Classes

#### SandboxExecutor

```python
from repotoire.sandbox import SandboxExecutor, SandboxConfig

config = SandboxConfig.from_env()

async with SandboxExecutor(config) as sandbox:
    # Execute Python code
    result = await sandbox.execute_code("print('Hello!')")

    # Execute shell command
    cmd_result = await sandbox.execute_command("ls -la /code")

    # Upload files
    await sandbox.upload_files([Path("src/module.py")])

    # Download files
    files = await sandbox.download_files(["/code/output.txt"])

    # List files
    names = await sandbox.list_files("/code")
```

#### Result Types

```python
from repotoire.sandbox import (
    ExecutionResult,   # Code execution result
    CommandResult,     # Shell command result
    TestResult,        # Test execution result
    ToolExecutorResult, # Tool execution result
    SkillResult,       # Skill execution result
    ValidationResult,  # Code validation result
)
```

#### Exceptions

```python
from repotoire.sandbox import (
    SandboxError,              # Base exception
    SandboxConfigurationError, # Config issues
    SandboxExecutionError,     # Execution failures
    SandboxTimeoutError,       # Timeout exceeded
    SandboxResourceError,      # Resource limits exceeded
    SkillSecurityError,        # Sandbox unavailable for skill
    TrialLimitExceeded,        # Trial quota exceeded
)
```

### Synchronous Wrappers

For CLI and sync code integration:

```python
from repotoire.sandbox import run_tests_sync, run_tool_sync

# Sync test execution
result = run_tests_sync(
    repo_path=Path("/path/to/repo"),
    command="pytest tests/",
)

# Sync tool execution
result = run_tool_sync(
    repo_path=Path("/path/to/repo"),
    command="ruff check .",
    tool_name="ruff",
)
```

---

## See Also

- [AUTO_FIX.md](AUTO_FIX.md) - AI-powered code fixing with sandbox validation
- [TIMESCALEDB_METRICS.md](TIMESCALEDB_METRICS.md) - Historical metrics storage
- [RAG_API.md](RAG_API.md) - RAG system for code intelligence
- [E2B Documentation](https://e2b.dev/docs) - Official E2B documentation

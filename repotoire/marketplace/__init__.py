"""Marketplace dependency resolution, versioning, security scanning, and Claude integration.

This package provides:
- VersionConstraint: Parse and check npm-style version constraints
- DependencyResolver: Resolve dependencies with cycle detection
- Lockfile: Generate and manage lockfiles
- AssetUpdater: Check and apply updates
- AssetScanner: Security scanning for dangerous patterns
- ClaudeConfigManager: Manage Claude Desktop/Code configuration
- Claude.ai export functions for project instructions and artifacts

Usage:
    from repotoire.marketplace import VersionConstraint, DependencyResolver

    # Parse a version constraint
    constraint = VersionConstraint.parse("^1.2.3")
    assert constraint.satisfies("1.5.0")  # True
    assert not constraint.satisfies("2.0.0")  # False

    # Resolve dependencies
    resolver = DependencyResolver(api_client, cache)
    resolved = await resolver.resolve({"@pub/asset": "^1.0.0"})

    # Scan asset for security issues
    from repotoire.marketplace import AssetScanner
    scanner = AssetScanner()
    findings = scanner.scan_asset(Path("/path/to/asset"))
    verdict, message = scanner.get_verdict(findings)

    # Manage Claude config
    from repotoire.marketplace import ClaudeConfigManager
    manager = ClaudeConfigManager()
    manager.add_mcp_server("my-skill", "npx", ["-y", "@pub/server"])

    # Export to Claude.ai
    from repotoire.marketplace import export_as_project_instructions
    instructions = export_as_project_instructions(assets)
"""

from .scanner import (
    AssetScanner,
    DangerousPattern,
    ScanFinding,
    SeverityLevel,
    DANGEROUS_PATTERNS,
    SCANNABLE_EXTENSIONS,
)
from .versioning import (
    ConstraintType,
    ConflictingVersionsError,
    DependencyCycleError,
    DependencyError,
    DependencyResolver,
    Lockfile,
    LockfileEntry,
    NoMatchingVersionError,
    ResolvedDependency,
    UpdateAvailable,
    UpdateType,
    AssetUpdater,
    VersionConstraint,
)
from .claude_integration import (
    ClaudeConfigManager,
    ClaudeConfigError,
    ConfigBackupError,
    ConfigNotFoundError,
    MCPServerConfig,
    HookConfig,
)
from .claudeai_export import (
    ExportedAsset,
    export_as_project_instructions,
    export_as_artifact,
    export_style_instructions,
    export_prompt_template,
    generate_clipboard_text,
    load_asset_from_file,
)
from .analytics import (
    AnalyticsTracker,
    EventData,
    AssetStatsResponse,
    DailyStats,
    AssetTrends,
    CreatorStatsResponse,
    PlatformStats,
    aggregate_daily_stats,
    update_rolling_stats,
    update_publisher_stats,
    cleanup_old_events,
)

__all__ = [
    # Scanner
    "AssetScanner",
    "DangerousPattern",
    "ScanFinding",
    "SeverityLevel",
    "DANGEROUS_PATTERNS",
    "SCANNABLE_EXTENSIONS",
    # Versioning
    "ConstraintType",
    "ConflictingVersionsError",
    "DependencyCycleError",
    "DependencyError",
    "DependencyResolver",
    "Lockfile",
    "LockfileEntry",
    "NoMatchingVersionError",
    "ResolvedDependency",
    "UpdateAvailable",
    "UpdateType",
    "AssetUpdater",
    "VersionConstraint",
    # Claude integration
    "ClaudeConfigManager",
    "ClaudeConfigError",
    "ConfigBackupError",
    "ConfigNotFoundError",
    "MCPServerConfig",
    "HookConfig",
    # Claude.ai export
    "ExportedAsset",
    "export_as_project_instructions",
    "export_as_artifact",
    "export_style_instructions",
    "export_prompt_template",
    "generate_clipboard_text",
    "load_asset_from_file",
    # Analytics
    "AnalyticsTracker",
    "EventData",
    "AssetStatsResponse",
    "DailyStats",
    "AssetTrends",
    "CreatorStatsResponse",
    "PlatformStats",
    "aggregate_daily_stats",
    "update_rolling_stats",
    "update_publisher_stats",
    "cleanup_old_events",
]

"""Configuration management for Repotoire.

Configuration Priority Chain (highest to lowest):
1. Command-line arguments (--falkordb-host, --log-level, etc.)
2. Environment variables (FALKORDB_HOST, FALKORDB_PORT, etc.)
3. Config file (.reporc, repotoire.toml)
4. Built-in defaults

Config files are searched hierarchically:
1. Current directory
2. Parent directories (up to root)
3. User home directory (~/.reporc or ~/.config/repotoire.toml)

Environment Variable Names:
- FALKORDB_HOST
- FALKORDB_PORT
- FALKORDB_PASSWORD
- REPOTOIRE_INGESTION_PATTERNS (comma-separated)
- REPOTOIRE_INGESTION_FOLLOW_SYMLINKS (true/false)
- REPOTOIRE_INGESTION_MAX_FILE_SIZE_MB
- REPOTOIRE_INGESTION_BATCH_SIZE
- REPOTOIRE_ANALYSIS_MIN_MODULARITY
- REPOTOIRE_ANALYSIS_MAX_COUPLING
- REPOTOIRE_LOG_LEVEL (or LOG_LEVEL)
- REPOTOIRE_LOG_FORMAT (or LOG_FORMAT)
- REPOTOIRE_LOG_FILE (or LOG_FILE)

Example .reporc (YAML):
```yaml
database:
  host: localhost
  port: 6379
  password: ${FALKORDB_PASSWORD}

ingestion:
  patterns:
    - "**/*.py"
    - "**/*.js"
  follow_symlinks: false
  max_file_size_mb: 10
  batch_size: 100

analysis:
  min_modularity: 0.3
  max_coupling: 5.0

logging:
  level: INFO
  format: human
  file: logs/repotoire.log
```

Example repotoire.toml:
```toml
[database]
host = "localhost"
port = 6379
password = "${FALKORDB_PASSWORD}"

[ingestion]
patterns = ["**/*.py", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx"]
follow_symlinks = false
max_file_size_mb = 10
batch_size = 100

[analysis]
min_modularity = 0.3
max_coupling = 5.0

[logging]
level = "INFO"
format = "human"
file = "logs/repotoire.log"
```
"""

import os
import json
import re
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass, field

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

try:
    import tomli
    HAS_TOML = True
except ImportError:
    try:
        import tomllib as tomli  # Python 3.11+
        HAS_TOML = True
    except ImportError:
        HAS_TOML = False

from repotoire.logging_config import get_logger

logger = get_logger(__name__)


# ============================================================================
# Enums for config value validation (Phase 4 improvements)
# ============================================================================


class LogLevel(str, Enum):
    """Valid log levels."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class SecretsPolicy(str, Enum):
    """Secrets detection policy options."""
    REDACT = "redact"
    BLOCK = "block"
    WARN = "warn"
    FAIL = "fail"


class SeverityThreshold(str, Enum):
    """Severity threshold options for detectors."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


# ============================================================================
# Deprecation helpers for env var migration (FALKOR_* -> REPOTOIRE_*)
# ============================================================================

# Mapping of deprecated env vars to their new names
_DEPRECATED_ENV_VARS = {
    # Ingestion
    "FALKOR_INGESTION_PATTERNS": "REPOTOIRE_INGESTION_PATTERNS",
    "FALKOR_INGESTION_FOLLOW_SYMLINKS": "REPOTOIRE_INGESTION_FOLLOW_SYMLINKS",
    "FALKOR_INGESTION_MAX_FILE_SIZE_MB": "REPOTOIRE_INGESTION_MAX_FILE_SIZE_MB",
    "FALKOR_INGESTION_BATCH_SIZE": "REPOTOIRE_INGESTION_BATCH_SIZE",
    # Analysis
    "FALKOR_ANALYSIS_MIN_MODULARITY": "REPOTOIRE_ANALYSIS_MIN_MODULARITY",
    "FALKOR_ANALYSIS_MAX_COUPLING": "REPOTOIRE_ANALYSIS_MAX_COUPLING",
    # Secrets
    "FALKOR_SECRETS_ENABLED": "REPOTOIRE_SECRETS_ENABLED",
    "FALKOR_SECRETS_POLICY": "REPOTOIRE_SECRETS_POLICY",
    # Logging
    "FALKOR_LOG_LEVEL": "REPOTOIRE_LOG_LEVEL",
    "FALKOR_LOG_FORMAT": "REPOTOIRE_LOG_FORMAT",
    "FALKOR_LOG_FILE": "REPOTOIRE_LOG_FILE",
    # TimescaleDB
    "FALKOR_TIMESCALE_ENABLED": "REPOTOIRE_TIMESCALE_ENABLED",
    "FALKOR_TIMESCALE_URI": "REPOTOIRE_TIMESCALE_URI",
    "FALKOR_TIMESCALE_AUTO_TRACK": "REPOTOIRE_TIMESCALE_AUTO_TRACK",
    # RAG
    "FALKOR_RAG_CACHE_ENABLED": "REPOTOIRE_RAG_CACHE_ENABLED",
    "FALKOR_RAG_CACHE_TTL": "REPOTOIRE_RAG_CACHE_TTL",
    "FALKOR_RAG_CACHE_MAX_SIZE": "REPOTOIRE_RAG_CACHE_MAX_SIZE",
}


def _get_env_with_fallback(new_key: str, old_key: Optional[str] = None) -> Optional[str]:
    """Get environment variable with deprecation warning for old keys.

    Args:
        new_key: The new (preferred) environment variable name
        old_key: The deprecated environment variable name (optional)

    Returns:
        The environment variable value, or None if not set
    """
    # Check new key first
    if value := os.getenv(new_key):
        return value

    # Check old key with deprecation warning
    if old_key:
        if value := os.getenv(old_key):
            logger.warning(
                f"Environment variable '{old_key}' is deprecated, use '{new_key}' instead"
            )
            return value

    # Check our mapping for auto-detected deprecated vars
    for deprecated, replacement in _DEPRECATED_ENV_VARS.items():
        if replacement == new_key:
            if value := os.getenv(deprecated):
                logger.warning(
                    f"Environment variable '{deprecated}' is deprecated, use '{new_key}' instead"
                )
                return value

    return None


class ConfigError(Exception):
    """Raised when configuration is invalid or cannot be loaded."""
    pass


@dataclass
class DatabaseConfig:
    """FalkorDB connection configuration."""
    host: str = "localhost"
    port: int = 6379
    password: Optional[str] = None
    max_retries: int = 3
    retry_backoff_factor: float = 2.0  # Exponential backoff multiplier
    retry_base_delay: float = 1.0  # Base delay in seconds


# Backward compatibility alias
Neo4jConfig = DatabaseConfig


@dataclass
class IngestionConfig:
    """Ingestion pipeline configuration."""
    patterns: list[str] = field(default_factory=lambda: [
        "**/*.py",
        "**/*.ts", "**/*.tsx",
        "**/*.js", "**/*.jsx",
        "**/*.java",
        "**/*.go",
    ])
    exclude_patterns: list[str] = field(default_factory=lambda: [
        "**/test_*.py",
        "**/*_test.py",
        "**/tests/**",
        "**/__tests__/**",
        "**/*.test.ts",
        "**/*.test.tsx",
        "**/*.test.js",
        "**/*.test.jsx",
        "**/*.spec.ts",
        "**/*.spec.tsx",
        "**/*.spec.js",
        "**/*.spec.jsx",
        "**/node_modules/**",
        "**/vendor/**",
        "**/dist/**",
        "**/build/**",
        "**/.venv/**",
        "**/venv/**",
        "**/env/**",
    ])
    exclude_dirs: list[str] = field(default_factory=lambda: [
        ".git",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "build",
        "dist",
        ".tox",
        ".eggs",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".coverage",
        "htmlcov",
        ".next",
        ".nuxt",
    ])
    follow_symlinks: bool = False
    max_file_size_mb: float = 10.0
    batch_size: int = 100


@dataclass
class AnalysisConfig:
    """Analysis engine configuration."""
    min_modularity: float = 0.3
    max_coupling: float = 5.0


@dataclass
class RuffConfig:
    """Ruff detector configuration."""
    enabled: bool = True
    select_rules: Optional[list[str]] = None  # If set, only run these rules
    ignore_rules: list[str] = field(default_factory=list)  # Rules to ignore
    max_findings: int = 500


@dataclass
class BanditConfig:
    """Bandit security detector configuration."""
    enabled: bool = True
    severity_threshold: str = "low"  # low, medium, high
    confidence_threshold: str = "low"  # low, medium, high
    skip_tests: list[str] = field(default_factory=list)  # e.g., ["B101", "B601"]
    max_findings: int = 200


@dataclass
class MypyConfig:
    """Mypy type checker configuration."""
    enabled: bool = True
    strict: bool = False
    ignore_missing_imports: bool = True
    max_findings: int = 300


@dataclass
class PylintConfig:
    """Pylint detector configuration."""
    enabled: bool = True
    enable_only: list[str] = field(default_factory=list)  # Specific checks to run
    disable: list[str] = field(default_factory=list)  # Checks to disable
    max_findings: int = 50
    jobs: int = 4  # Parallel jobs


@dataclass
class RadonConfig:
    """Radon complexity detector configuration."""
    enabled: bool = True
    complexity_threshold: str = "C"  # A, B, C, D, E, F - min complexity to report
    max_findings: int = 100


@dataclass
class JscpdConfig:
    """JSCPD duplicate code detector configuration."""
    enabled: bool = True
    min_lines: int = 5  # Minimum lines for duplicate detection
    min_tokens: int = 50  # Minimum tokens for duplicate detection
    threshold: float = 0.0  # 0-100, percentage threshold


@dataclass
class VultureConfig:
    """Vulture dead code detector configuration."""
    enabled: bool = True
    min_confidence: int = 60  # 0-100, minimum confidence threshold
    max_findings: int = 100


@dataclass
class SemgrepConfig:
    """Semgrep security detector configuration."""
    enabled: bool = True
    rulesets: list[str] = field(default_factory=lambda: [
        "p/python",
        "p/owasp-top-ten",
    ])
    severity_threshold: str = "info"  # info, warning, error
    max_findings: int = 100


@dataclass
class DetectorConfig:
    """Detector thresholds and per-detector configuration."""
    # Enable/disable control
    enabled_detectors: Optional[list[str]] = None  # None = all enabled, or list of names
    disabled_detectors: list[str] = field(default_factory=list)  # Detectors to disable

    # God class detector thresholds
    god_class_high_method_count: int = 20
    god_class_medium_method_count: int = 15
    god_class_high_complexity: int = 100
    god_class_medium_complexity: int = 50
    god_class_high_loc: int = 500
    god_class_medium_loc: int = 300
    god_class_high_lcom: float = 0.8  # Lack of cohesion (0-1, higher is worse)
    god_class_medium_lcom: float = 0.6

    # Per-detector configuration
    ruff: RuffConfig = field(default_factory=RuffConfig)
    bandit: BanditConfig = field(default_factory=BanditConfig)
    mypy: MypyConfig = field(default_factory=MypyConfig)
    pylint: PylintConfig = field(default_factory=PylintConfig)
    radon: RadonConfig = field(default_factory=RadonConfig)
    jscpd: JscpdConfig = field(default_factory=JscpdConfig)
    vulture: VultureConfig = field(default_factory=VultureConfig)
    semgrep: SemgrepConfig = field(default_factory=SemgrepConfig)


@dataclass
class CustomSecretPattern:
    """Custom secret pattern definition."""
    name: str  # Name for the pattern (e.g., "Internal API Key")
    pattern: str  # Regex pattern to match
    risk_level: str = "high"  # critical, high, medium, low
    remediation: str = ""  # Remediation suggestion

    def __post_init__(self):
        """Validate the regex pattern on initialization."""
        try:
            re.compile(self.pattern)
        except re.error as e:
            raise ValueError(f"Invalid regex in secret pattern '{self.name}': {e}")

        # Validate risk_level
        valid_risk_levels = {"critical", "high", "medium", "low"}
        if self.risk_level.lower() not in valid_risk_levels:
            raise ValueError(
                f"Invalid risk_level '{self.risk_level}' in pattern '{self.name}'. "
                f"Must be one of: {', '.join(sorted(valid_risk_levels))}"
            )


@dataclass
class SecretsConfig:
    """Secrets detection configuration.

    Example configuration:
    ```yaml
    secrets:
      enabled: true
      policy: redact
      entropy_detection: true
      entropy_threshold: 4.0
      min_entropy_length: 20
      large_file_threshold_mb: 1.0
      parallel_workers: 4
      cache_enabled: true
      custom_patterns:
        - name: "Internal API Key"
          pattern: "MYCOMPANY_[A-Za-z0-9]{32}"
          risk_level: critical
          remediation: "Remove key and rotate via internal key management"
        - name: "Dev Environment Token"
          pattern: "dev_token_[a-z0-9]{16}"
          risk_level: medium
          remediation: "Use environment variables instead of hardcoding"
    ```
    """
    enabled: bool = True
    policy: str = "redact"  # redact, block, warn, fail
    # Entropy detection settings
    entropy_detection: bool = True
    entropy_threshold: float = 4.0
    min_entropy_length: int = 20
    # Performance settings
    large_file_threshold_mb: float = 1.0  # Stream files larger than this
    parallel_workers: int = 4  # Number of parallel workers for batch scanning
    cache_enabled: bool = True  # Enable hash-based caching
    # Custom patterns (list of dicts with name, pattern, risk_level, remediation)
    custom_patterns: list = field(default_factory=list)


@dataclass
class LoggingConfig:
    """Logging configuration."""
    level: str = "INFO"
    format: str = "human"  # "human" or "json"
    file: Optional[str] = None


@dataclass
class TimescaleConfig:
    """TimescaleDB configuration for metrics tracking."""
    enabled: bool = False
    connection_string: Optional[str] = None
    auto_track: bool = False  # Automatically track metrics after analysis


@dataclass
class EmbeddingsConfig:
    """Embeddings configuration for RAG vector search.

    Example configuration:
    ```yaml
    embeddings:
      backend: "local"  # "openai" or "local"
      model: "all-MiniLM-L6-v2"  # optional, uses backend default if not set
    ```

    Backends:
    - openai: High quality (1536 dims), requires API key, $0.13/1M tokens
    - local: Free, fast (384 dims), uses sentence-transformers (~85-90% quality)
    """
    backend: str = "openai"  # "openai" or "local"
    model: Optional[str] = None  # Uses backend default if not set


@dataclass
class RAGConfig:
    """RAG (Retrieval-Augmented Generation) configuration.

    Example configuration:
    ```yaml
    rag:
      cache_enabled: true
      cache_ttl: 3600
      cache_max_size: 1000
    ```
    """
    cache_enabled: bool = True  # Enable query result caching
    cache_ttl: int = 3600  # Time-to-live in seconds (default: 1 hour)
    cache_max_size: int = 1000  # Maximum cache entries (LRU eviction)


@dataclass
class ReportingTheme:
    """Theme colors for report generation.

    Example configuration:
    ```yaml
    reporting:
      theme:
        primary_color: "#667eea"
        header_gradient_start: "#667eea"
        header_gradient_end: "#764ba2"
        background_color: "#f9fafb"
        text_color: "#1f2937"
        link_color: "#4f46e5"
    ```
    """
    primary_color: str = "#667eea"
    header_gradient_start: str = "#667eea"
    header_gradient_end: str = "#764ba2"
    background_color: str = "#f9fafb"
    text_color: str = "#1f2937"
    link_color: str = "#4f46e5"
    grade_a_color: str = "#10b981"
    grade_b_color: str = "#06b6d4"
    grade_c_color: str = "#f59e0b"
    grade_d_color: str = "#ef4444"
    grade_f_color: str = "#991b1b"


@dataclass
class ReportingConfig:
    """Reporting and output configuration.

    Example configuration:
    ```yaml
    reporting:
      theme_name: "light"  # "light", "dark", or "custom"
      title: "Code Health Report"
      logo_url: "https://example.com/logo.png"
      footer_text: "Generated by Repotoire"
      include_snippets: true
      max_findings: 100
      theme:
        primary_color: "#667eea"
    ```
    """
    # Theme selection: "light", "dark", or "custom"
    theme_name: str = "light"

    # Branding options
    title: str = "Repotoire Code Health Report"
    logo_url: Optional[str] = None
    footer_text: str = "Generated by Repotoire - Graph-Powered Code Health Platform"
    footer_link: str = "https://repotoire.com"

    # Report content options
    include_snippets: bool = True
    max_findings: int = 100
    max_snippet_lines: int = 10

    # Custom theme (used when theme_name is "custom")
    theme: ReportingTheme = field(default_factory=ReportingTheme)


@dataclass
class RepotoireConfig:
    """Complete Repotoire configuration."""
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    ingestion: IngestionConfig = field(default_factory=IngestionConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    detectors: DetectorConfig = field(default_factory=DetectorConfig)
    secrets: SecretsConfig = field(default_factory=SecretsConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    timescale: TimescaleConfig = field(default_factory=TimescaleConfig)
    rag: RAGConfig = field(default_factory=RAGConfig)
    embeddings: EmbeddingsConfig = field(default_factory=EmbeddingsConfig)
    reporting: ReportingConfig = field(default_factory=ReportingConfig)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RepotoireConfig":
        """Create config from dictionary.

        Args:
            data: Configuration dictionary

        Returns:
            RepotoireConfig instance
        """
        # Expand environment variables
        data = _expand_env_vars(data)

        # Parse detector config with nested per-detector configs
        detector_data = data.get("detectors", {})
        detector_config = _parse_detector_config(detector_data)

        # Parse reporting config with nested theme
        reporting_data = data.get("reporting", {})
        reporting_config = _parse_reporting_config(reporting_data)

        return cls(
            database=DatabaseConfig(**data.get("database", {})),
            ingestion=IngestionConfig(**data.get("ingestion", {})),
            analysis=AnalysisConfig(**data.get("analysis", {})),
            detectors=detector_config,
            secrets=SecretsConfig(**data.get("secrets", {})),
            logging=LoggingConfig(**data.get("logging", {})),
            rag=RAGConfig(**data.get("rag", {})),
            embeddings=EmbeddingsConfig(**data.get("embeddings", {})),
            reporting=reporting_config,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary.

        Returns:
            Configuration as dictionary
        """
        return {
            "database": {
                "host": self.database.host,
                "port": self.database.port,
                "password": self.database.password,
                "max_retries": self.database.max_retries,
                "retry_backoff_factor": self.database.retry_backoff_factor,
                "retry_base_delay": self.database.retry_base_delay,
            },
            "ingestion": {
                "patterns": self.ingestion.patterns,
                "exclude_patterns": self.ingestion.exclude_patterns,
                "exclude_dirs": self.ingestion.exclude_dirs,
                "follow_symlinks": self.ingestion.follow_symlinks,
                "max_file_size_mb": self.ingestion.max_file_size_mb,
                "batch_size": self.ingestion.batch_size,
            },
            "analysis": {
                "min_modularity": self.analysis.min_modularity,
                "max_coupling": self.analysis.max_coupling,
            },
            "detectors": {
                "enabled_detectors": self.detectors.enabled_detectors,
                "disabled_detectors": self.detectors.disabled_detectors,
                "god_class_high_method_count": self.detectors.god_class_high_method_count,
                "god_class_medium_method_count": self.detectors.god_class_medium_method_count,
                "god_class_high_complexity": self.detectors.god_class_high_complexity,
                "god_class_medium_complexity": self.detectors.god_class_medium_complexity,
                "god_class_high_loc": self.detectors.god_class_high_loc,
                "god_class_medium_loc": self.detectors.god_class_medium_loc,
                "god_class_high_lcom": self.detectors.god_class_high_lcom,
                "god_class_medium_lcom": self.detectors.god_class_medium_lcom,
                "ruff": {
                    "enabled": self.detectors.ruff.enabled,
                    "select_rules": self.detectors.ruff.select_rules,
                    "ignore_rules": self.detectors.ruff.ignore_rules,
                    "max_findings": self.detectors.ruff.max_findings,
                },
                "bandit": {
                    "enabled": self.detectors.bandit.enabled,
                    "severity_threshold": self.detectors.bandit.severity_threshold,
                    "confidence_threshold": self.detectors.bandit.confidence_threshold,
                    "skip_tests": self.detectors.bandit.skip_tests,
                    "max_findings": self.detectors.bandit.max_findings,
                },
                "mypy": {
                    "enabled": self.detectors.mypy.enabled,
                    "strict": self.detectors.mypy.strict,
                    "ignore_missing_imports": self.detectors.mypy.ignore_missing_imports,
                    "max_findings": self.detectors.mypy.max_findings,
                },
                "pylint": {
                    "enabled": self.detectors.pylint.enabled,
                    "enable_only": self.detectors.pylint.enable_only,
                    "disable": self.detectors.pylint.disable,
                    "max_findings": self.detectors.pylint.max_findings,
                    "jobs": self.detectors.pylint.jobs,
                },
                "radon": {
                    "enabled": self.detectors.radon.enabled,
                    "complexity_threshold": self.detectors.radon.complexity_threshold,
                    "max_findings": self.detectors.radon.max_findings,
                },
                "jscpd": {
                    "enabled": self.detectors.jscpd.enabled,
                    "min_lines": self.detectors.jscpd.min_lines,
                    "min_tokens": self.detectors.jscpd.min_tokens,
                    "threshold": self.detectors.jscpd.threshold,
                },
                "vulture": {
                    "enabled": self.detectors.vulture.enabled,
                    "min_confidence": self.detectors.vulture.min_confidence,
                    "max_findings": self.detectors.vulture.max_findings,
                },
                "semgrep": {
                    "enabled": self.detectors.semgrep.enabled,
                    "rulesets": self.detectors.semgrep.rulesets,
                    "severity_threshold": self.detectors.semgrep.severity_threshold,
                    "max_findings": self.detectors.semgrep.max_findings,
                },
            },
            "secrets": {
                "enabled": self.secrets.enabled,
                "policy": self.secrets.policy,
                "entropy_detection": self.secrets.entropy_detection,
                "entropy_threshold": self.secrets.entropy_threshold,
                "min_entropy_length": self.secrets.min_entropy_length,
                "large_file_threshold_mb": self.secrets.large_file_threshold_mb,
                "parallel_workers": self.secrets.parallel_workers,
                "cache_enabled": self.secrets.cache_enabled,
                "custom_patterns": self.secrets.custom_patterns,
            },
            "logging": {
                "level": self.logging.level,
                "format": self.logging.format,
                "file": self.logging.file,
            },
            "rag": {
                "cache_enabled": self.rag.cache_enabled,
                "cache_ttl": self.rag.cache_ttl,
                "cache_max_size": self.rag.cache_max_size,
            },
            "embeddings": {
                "backend": self.embeddings.backend,
                "model": self.embeddings.model,
            },
            "reporting": {
                "theme_name": self.reporting.theme_name,
                "title": self.reporting.title,
                "logo_url": self.reporting.logo_url,
                "footer_text": self.reporting.footer_text,
                "footer_link": self.reporting.footer_link,
                "include_snippets": self.reporting.include_snippets,
                "max_findings": self.reporting.max_findings,
                "max_snippet_lines": self.reporting.max_snippet_lines,
                "theme": {
                    "primary_color": self.reporting.theme.primary_color,
                    "header_gradient_start": self.reporting.theme.header_gradient_start,
                    "header_gradient_end": self.reporting.theme.header_gradient_end,
                    "background_color": self.reporting.theme.background_color,
                    "text_color": self.reporting.theme.text_color,
                    "link_color": self.reporting.theme.link_color,
                    "grade_a_color": self.reporting.theme.grade_a_color,
                    "grade_b_color": self.reporting.theme.grade_b_color,
                    "grade_c_color": self.reporting.theme.grade_c_color,
                    "grade_d_color": self.reporting.theme.grade_d_color,
                    "grade_f_color": self.reporting.theme.grade_f_color,
                },
            },
        }

    def merge(self, other: "RepotoireConfig") -> "RepotoireConfig":
        """Merge with another config (other takes precedence).

        Args:
            other: Config to merge with

        Returns:
            New merged config
        """
        merged_dict = self.to_dict()
        other_dict = other.to_dict()

        # Deep merge
        for section, values in other_dict.items():
            if section not in merged_dict:
                merged_dict[section] = values
            else:
                merged_dict[section].update(values)

        return RepotoireConfig.from_dict(merged_dict)


# Backward compatibility alias
FalkorConfig = RepotoireConfig


def _parse_detector_config(data: Dict[str, Any]) -> DetectorConfig:
    """Parse detector configuration with nested per-detector configs.

    Args:
        data: Detector configuration dictionary

    Returns:
        DetectorConfig instance with properly parsed nested configs
    """
    # Extract nested per-detector configs
    ruff_data = data.pop("ruff", {}) if "ruff" in data else {}
    bandit_data = data.pop("bandit", {}) if "bandit" in data else {}
    mypy_data = data.pop("mypy", {}) if "mypy" in data else {}
    pylint_data = data.pop("pylint", {}) if "pylint" in data else {}
    radon_data = data.pop("radon", {}) if "radon" in data else {}
    jscpd_data = data.pop("jscpd", {}) if "jscpd" in data else {}
    vulture_data = data.pop("vulture", {}) if "vulture" in data else {}
    semgrep_data = data.pop("semgrep", {}) if "semgrep" in data else {}

    # Create nested configs
    ruff_config = RuffConfig(**ruff_data) if ruff_data else RuffConfig()
    bandit_config = BanditConfig(**bandit_data) if bandit_data else BanditConfig()
    mypy_config = MypyConfig(**mypy_data) if mypy_data else MypyConfig()
    pylint_config = PylintConfig(**pylint_data) if pylint_data else PylintConfig()
    radon_config = RadonConfig(**radon_data) if radon_data else RadonConfig()
    jscpd_config = JscpdConfig(**jscpd_data) if jscpd_data else JscpdConfig()
    vulture_config = VultureConfig(**vulture_data) if vulture_data else VultureConfig()
    semgrep_config = SemgrepConfig(**semgrep_data) if semgrep_data else SemgrepConfig()

    # Create main config with remaining fields and nested configs
    return DetectorConfig(
        ruff=ruff_config,
        bandit=bandit_config,
        mypy=mypy_config,
        pylint=pylint_config,
        radon=radon_config,
        jscpd=jscpd_config,
        vulture=vulture_config,
        semgrep=semgrep_config,
        **data  # Pass remaining fields directly
    )


def _parse_reporting_config(data: Dict[str, Any]) -> ReportingConfig:
    """Parse reporting configuration with nested theme.

    Args:
        data: Reporting configuration dictionary

    Returns:
        ReportingConfig instance with properly parsed nested theme
    """
    # Extract nested theme config
    theme_data = data.pop("theme", {}) if "theme" in data else {}

    # Create theme config
    theme_config = ReportingTheme(**theme_data) if theme_data else ReportingTheme()

    # Apply dark theme defaults if theme_name is "dark"
    if data.get("theme_name") == "dark" and not theme_data:
        theme_config = ReportingTheme(
            primary_color="#818cf8",
            header_gradient_start="#1e1b4b",
            header_gradient_end="#312e81",
            background_color="#111827",
            text_color="#f3f4f6",
            link_color="#a5b4fc",
            grade_a_color="#34d399",
            grade_b_color="#22d3ee",
            grade_c_color="#fbbf24",
            grade_d_color="#f87171",
            grade_f_color="#dc2626",
        )

    # Create main config with remaining fields and nested theme
    return ReportingConfig(
        theme=theme_config,
        **data  # Pass remaining fields directly
    )


def _expand_env_vars(data: Union[Dict, list, str, Any]) -> Any:
    """Recursively expand environment variables in config data.

    Supports ${VAR_NAME} and $VAR_NAME syntax.

    Args:
        data: Configuration data (dict, list, str, or primitive)

    Returns:
        Data with environment variables expanded
    """
    if isinstance(data, dict):
        return {k: _expand_env_vars(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_expand_env_vars(item) for item in data]
    elif isinstance(data, str):
        # Match ${VAR} or $VAR
        pattern = re.compile(r'\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)')

        def replace_var(match):
            var_name = match.group(1) or match.group(2)
            return os.environ.get(var_name, match.group(0))

        return pattern.sub(replace_var, data)
    else:
        return data


def find_config_file(start_dir: Optional[Path] = None) -> Optional[Path]:
    """Find config file using hierarchical search.

    Searches in order:
    1. start_dir (or current directory)
    2. Parent directories up to root
    3. User home directory

    Looks for (in order of preference):
    - .reporc (YAML/JSON)
    - falkor.toml

    Args:
        start_dir: Starting directory for search (default: current directory)

    Returns:
        Path to config file, or None if not found
    """
    if start_dir is None:
        start_dir = Path.cwd()
    else:
        start_dir = Path(start_dir).resolve()

    # Search current directory and parents
    current = start_dir
    while True:
        # Check for .reporc
        falkorrc = current / ".reporc"
        if falkorrc.exists() and falkorrc.is_file():
            logger.info(f"Found config file: {falkorrc}")
            return falkorrc

        # Check for falkor.toml
        falkor_toml = current / "falkor.toml"
        if falkor_toml.exists() and falkor_toml.is_file():
            logger.info(f"Found config file: {falkor_toml}")
            return falkor_toml

        # Move to parent
        parent = current.parent
        if parent == current:  # Reached root
            break
        current = parent

    # Check home directory
    home = Path.home()

    # Check ~/.reporc
    home_falkorrc = home / ".reporc"
    if home_falkorrc.exists() and home_falkorrc.is_file():
        logger.info(f"Found config file: {home_falkorrc}")
        return home_falkorrc

    # Check ~/.config/falkor.toml
    config_dir = home / ".config"
    config_toml = config_dir / "falkor.toml"
    if config_toml.exists() and config_toml.is_file():
        logger.info(f"Found config file: {config_toml}")
        return config_toml

    logger.debug("No config file found")
    return None


def find_ignore_file(start_dir: Optional[Path] = None) -> Optional[Path]:
    """Find .repotoireignore file using hierarchical search.

    Searches in order:
    1. start_dir (or current directory)
    2. Parent directories up to root

    Args:
        start_dir: Starting directory for search (default: current directory)

    Returns:
        Path to ignore file, or None if not found
    """
    if start_dir is None:
        start_dir = Path.cwd()
    else:
        start_dir = Path(start_dir).resolve()

    # Search current directory and parents
    current = start_dir
    while True:
        ignore_file = current / ".repotoireignore"
        if ignore_file.exists() and ignore_file.is_file():
            logger.info(f"Found ignore file: {ignore_file}")
            return ignore_file

        # Move to parent
        parent = current.parent
        if parent == current:  # Reached root
            break
        current = parent

    logger.debug("No .repotoireignore file found")
    return None


def load_ignore_patterns(ignore_file: Optional[Path] = None, start_dir: Optional[Path] = None) -> list[str]:
    """Load ignore patterns from .repotoireignore file.

    File format:
    - One pattern per line (fnmatch/glob syntax)
    - Lines starting with # are comments
    - Empty lines are ignored
    - Patterns are relative to the repository root

    Example .repotoireignore:
    ```
    # Ignore test files
    test_*.py
    *_test.py

    # Ignore specific directories
    vendor/**
    build/**

    # Ignore specific file types
    *.log
    *.tmp
    ```

    Args:
        ignore_file: Explicit path to ignore file (optional)
        start_dir: Starting directory for hierarchical search (default: current directory)

    Returns:
        List of ignore patterns (empty list if no file found)
    """
    if ignore_file is None:
        ignore_file = find_ignore_file(start_dir)

    if ignore_file is None:
        return []

    patterns = []
    try:
        content = ignore_file.read_text()
        for line in content.splitlines():
            # Strip whitespace
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue
            patterns.append(line)

        logger.info(f"Loaded {len(patterns)} patterns from {ignore_file}")
        return patterns

    except Exception as e:
        logger.warning(f"Failed to read ignore file {ignore_file}: {e}")
        return []


def load_config_file(file_path: Path) -> Dict[str, Any]:
    """Load configuration from file.

    Supports:
    - .reporc (YAML or JSON)
    - falkor.toml (TOML)

    Args:
        file_path: Path to config file

    Returns:
        Configuration dictionary

    Raises:
        ConfigError: If file cannot be parsed or format not supported
    """
    file_path = Path(file_path)

    if not file_path.exists():
        raise ConfigError(f"Config file not found: {file_path}")

    try:
        content = file_path.read_text()
    except Exception as e:
        raise ConfigError(f"Failed to read config file {file_path}: {e}")

    # Detect format and parse
    if file_path.name == ".reporc" or file_path.suffix in [".yaml", ".yml", ".json"]:
        # Try YAML first (if available and appropriate extension)
        if HAS_YAML and file_path.suffix in [".yaml", ".yml", ""]:
            try:
                data = yaml.safe_load(content)
                logger.debug(f"Loaded YAML config from {file_path}")
                return data or {}
            except yaml.YAMLError:
                pass  # Try JSON

        # Try JSON
        try:
            data = json.loads(content)
            logger.debug(f"Loaded JSON config from {file_path}")
            return data
        except json.JSONDecodeError as e:
            raise ConfigError(
                f"Failed to parse {file_path} as YAML or JSON: {e}\n"
                f"Install PyYAML for YAML support: pip install pyyaml"
            )

    elif file_path.suffix == ".toml":
        if not HAS_TOML:
            raise ConfigError(
                f"TOML support not available. Install tomli: pip install tomli"
            )

        try:
            data = tomli.loads(content)
            logger.debug(f"Loaded TOML config from {file_path}")
            return data
        except Exception as e:
            raise ConfigError(f"Failed to parse TOML config {file_path}: {e}")

    else:
        raise ConfigError(f"Unsupported config file format: {file_path}")


def load_config_from_env() -> Dict[str, Any]:
    """Load configuration from environment variables.

    Environment variables take precedence over config files but are
    overridden by command-line arguments.

    Uses _get_env_with_fallback() to show deprecation warnings when
    old FALKOR_* environment variables are used.

    Returns:
        Configuration dictionary with values from environment
    """
    config = {}

    # Database (FalkorDB) configuration
    # Note: FALKORDB_* is kept as-is since it's the database name
    database = {}
    if host := os.getenv("FALKORDB_HOST"):
        database["host"] = host
    if port := os.getenv("FALKORDB_PORT"):
        try:
            database["port"] = int(port)
        except ValueError:
            logger.warning(f"Invalid FALKORDB_PORT value: {port}, ignoring")
    if password := os.getenv("FALKORDB_PASSWORD"):
        database["password"] = password
    if max_retries := os.getenv("REPOTOIRE_DB_MAX_RETRIES"):
        try:
            database["max_retries"] = int(max_retries)
        except ValueError:
            logger.warning(f"Invalid REPOTOIRE_DB_MAX_RETRIES value: {max_retries}, ignoring")
    if retry_backoff_factor := os.getenv("REPOTOIRE_DB_RETRY_BACKOFF_FACTOR"):
        try:
            database["retry_backoff_factor"] = float(retry_backoff_factor)
        except ValueError:
            logger.warning(f"Invalid REPOTOIRE_DB_RETRY_BACKOFF_FACTOR value: {retry_backoff_factor}, ignoring")
    if retry_base_delay := os.getenv("REPOTOIRE_DB_RETRY_BASE_DELAY"):
        try:
            database["retry_base_delay"] = float(retry_base_delay)
        except ValueError:
            logger.warning(f"Invalid REPOTOIRE_DB_RETRY_BASE_DELAY value: {retry_base_delay}, ignoring")
    if database:
        config["database"] = database

    # Ingestion configuration (with deprecation warnings for FALKOR_INGESTION_*)
    ingestion = {}
    if patterns := _get_env_with_fallback("REPOTOIRE_INGESTION_PATTERNS"):
        ingestion["patterns"] = [p.strip() for p in patterns.split(",")]
    if follow_symlinks := _get_env_with_fallback("REPOTOIRE_INGESTION_FOLLOW_SYMLINKS"):
        ingestion["follow_symlinks"] = follow_symlinks.lower() in ("true", "1", "yes")
    if max_file_size := _get_env_with_fallback("REPOTOIRE_INGESTION_MAX_FILE_SIZE_MB"):
        try:
            ingestion["max_file_size_mb"] = float(max_file_size)
        except ValueError:
            logger.warning(f"Invalid REPOTOIRE_INGESTION_MAX_FILE_SIZE_MB: {max_file_size}")
    if batch_size := _get_env_with_fallback("REPOTOIRE_INGESTION_BATCH_SIZE"):
        try:
            ingestion["batch_size"] = int(batch_size)
        except ValueError:
            logger.warning(f"Invalid REPOTOIRE_INGESTION_BATCH_SIZE: {batch_size}")
    if ingestion:
        config["ingestion"] = ingestion

    # Analysis configuration (with deprecation warnings for FALKOR_ANALYSIS_*)
    analysis = {}
    if min_modularity := _get_env_with_fallback("REPOTOIRE_ANALYSIS_MIN_MODULARITY"):
        try:
            analysis["min_modularity"] = float(min_modularity)
        except ValueError:
            logger.warning(f"Invalid REPOTOIRE_ANALYSIS_MIN_MODULARITY: {min_modularity}")
    if max_coupling := _get_env_with_fallback("REPOTOIRE_ANALYSIS_MAX_COUPLING"):
        try:
            analysis["max_coupling"] = float(max_coupling)
        except ValueError:
            logger.warning(f"Invalid REPOTOIRE_ANALYSIS_MAX_COUPLING: {max_coupling}")
    if analysis:
        config["analysis"] = analysis

    # Secrets configuration (with deprecation warnings for FALKOR_SECRETS_*)
    secrets = {}
    if secrets_enabled := _get_env_with_fallback("REPOTOIRE_SECRETS_ENABLED"):
        secrets["enabled"] = secrets_enabled.lower() in ("true", "1", "yes")
    if secrets_policy := _get_env_with_fallback("REPOTOIRE_SECRETS_POLICY"):
        secrets["policy"] = secrets_policy.lower()
    if secrets:
        config["secrets"] = secrets

    # Logging configuration (with deprecation warnings for FALKOR_LOG_*)
    # Also supports unprefixed LOG_LEVEL, LOG_FORMAT, LOG_FILE as additional fallbacks
    logging_cfg = {}
    if level := _get_env_with_fallback("REPOTOIRE_LOG_LEVEL") or os.getenv("LOG_LEVEL"):
        logging_cfg["level"] = level.upper()
    if fmt := _get_env_with_fallback("REPOTOIRE_LOG_FORMAT") or os.getenv("LOG_FORMAT"):
        logging_cfg["format"] = fmt
    if file := _get_env_with_fallback("REPOTOIRE_LOG_FILE") or os.getenv("LOG_FILE"):
        logging_cfg["file"] = file
    if logging_cfg:
        config["logging"] = logging_cfg

    # TimescaleDB configuration (with deprecation warnings for FALKOR_TIMESCALE_*)
    timescale = {}
    if enabled := _get_env_with_fallback("REPOTOIRE_TIMESCALE_ENABLED"):
        timescale["enabled"] = enabled.lower() in ("true", "1", "yes")
    if connection_string := _get_env_with_fallback("REPOTOIRE_TIMESCALE_URI"):
        timescale["connection_string"] = connection_string
    if auto_track := _get_env_with_fallback("REPOTOIRE_TIMESCALE_AUTO_TRACK"):
        timescale["auto_track"] = auto_track.lower() in ("true", "1", "yes")
    if timescale:
        config["timescale"] = timescale

    # RAG configuration (with deprecation warnings for FALKOR_RAG_*)
    rag = {}
    if cache_enabled := _get_env_with_fallback("REPOTOIRE_RAG_CACHE_ENABLED"):
        rag["cache_enabled"] = cache_enabled.lower() in ("true", "1", "yes")
    if cache_ttl := _get_env_with_fallback("REPOTOIRE_RAG_CACHE_TTL"):
        try:
            rag["cache_ttl"] = int(cache_ttl)
        except ValueError:
            logger.warning(f"Invalid REPOTOIRE_RAG_CACHE_TTL value: {cache_ttl}")
    if cache_max_size := _get_env_with_fallback("REPOTOIRE_RAG_CACHE_MAX_SIZE"):
        try:
            rag["cache_max_size"] = int(cache_max_size)
        except ValueError:
            logger.warning(f"Invalid REPOTOIRE_RAG_CACHE_MAX_SIZE value: {cache_max_size}")
    if rag:
        config["rag"] = rag

    return config


def _deep_merge_dicts(base: Dict, override: Dict) -> Dict:
    """Deep merge two dictionaries.

    Args:
        base: Base dictionary
        override: Dictionary with overriding values

    Returns:
        Merged dictionary (base is not modified)
    """
    result = base.copy()

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge_dicts(result[key], value)
        else:
            result[key] = value

    return result


def load_config(
    config_file: Optional[Union[str, Path]] = None,
    search_path: Optional[Path] = None,
    use_env: bool = True,
) -> FalkorConfig:
    """Load Falkor configuration with fallback chain.

    Priority order (highest to lowest):
    1. Command-line arguments (handled by CLI)
    2. Environment variables (FALKOR_*)
    3. Config file (.reporc, falkor.toml)
    4. Built-in defaults

    Args:
        config_file: Explicit path to config file (optional)
        search_path: Starting directory for hierarchical search (default: current dir)
        use_env: Whether to load from environment variables (default: True)

    Returns:
        FalkorConfig instance with merged configuration

    Raises:
        ConfigError: If specified config file cannot be loaded
    """
    # Start with empty dict (defaults will be applied by FalkorConfig.from_dict)
    merged_data: Dict[str, Any] = {}

    # Layer 3: Load from config file if available
    if config_file:
        # Explicit config file specified
        config_path = Path(config_file)
        file_data = load_config_file(config_path)
        logger.info(f"Loaded configuration from {config_path}")
        merged_data = _deep_merge_dicts(merged_data, file_data)
    else:
        # Search for config file
        config_path = find_config_file(search_path)
        if config_path:
            file_data = load_config_file(config_path)
            logger.info(f"Loaded configuration from {config_path}")
            merged_data = _deep_merge_dicts(merged_data, file_data)
        else:
            logger.debug("No config file found")

    # Layer 2: Load from environment variables
    if use_env:
        env_data = load_config_from_env()
        if env_data:
            logger.debug(f"Loaded configuration from environment variables")
            merged_data = _deep_merge_dicts(merged_data, env_data)

    # Create final config from merged data (applies defaults for missing values)
    return FalkorConfig.from_dict(merged_data)


def validate_config(config: RepotoireConfig) -> List[str]:
    """Validate configuration and return warnings.

    Performs comprehensive validation of all configuration values,
    checking for invalid values, out-of-range parameters, and
    potential performance issues.

    Args:
        config: The configuration to validate

    Returns:
        List of warning messages (empty if no warnings)

    Raises:
        ConfigError: If configuration has invalid values that cannot be used
    """
    warnings: List[str] = []

    # ========================================================================
    # Database validation
    # ========================================================================
    if config.database.port <= 0:
        raise ConfigError("database.port must be a positive integer")
    if config.database.port > 65535:
        raise ConfigError("database.port must be <= 65535")
    if config.database.max_retries < 0:
        raise ConfigError("database.max_retries cannot be negative")
    if config.database.retry_backoff_factor <= 0:
        raise ConfigError("database.retry_backoff_factor must be positive")
    if config.database.retry_base_delay < 0:
        raise ConfigError("database.retry_base_delay cannot be negative")

    # ========================================================================
    # Ingestion validation
    # ========================================================================
    if config.ingestion.batch_size < 1:
        raise ConfigError("ingestion.batch_size must be at least 1")
    if config.ingestion.batch_size < 10:
        warnings.append(
            "ingestion.batch_size < 10 may hurt performance due to increased "
            "network round-trips. Consider using 50-100 for optimal performance."
        )
    if config.ingestion.batch_size > 1000:
        warnings.append(
            "ingestion.batch_size > 1000 may cause memory issues on large files. "
            "Consider using 100-500 for a balance of performance and memory."
        )
    if config.ingestion.max_file_size_mb <= 0:
        raise ConfigError("ingestion.max_file_size_mb must be positive")
    if config.ingestion.max_file_size_mb > 100:
        warnings.append(
            "ingestion.max_file_size_mb > 100 may cause memory issues. "
            "Consider excluding large files via patterns."
        )
    if not config.ingestion.patterns:
        warnings.append(
            "ingestion.patterns is empty - no files will be ingested"
        )

    # ========================================================================
    # Analysis validation
    # ========================================================================
    if not 0 <= config.analysis.min_modularity <= 1:
        raise ConfigError("analysis.min_modularity must be between 0 and 1")
    if config.analysis.max_coupling < 0:
        raise ConfigError("analysis.max_coupling cannot be negative")

    # ========================================================================
    # Detector validation
    # ========================================================================
    # God class thresholds
    if config.detectors.god_class_high_method_count < config.detectors.god_class_medium_method_count:
        warnings.append(
            "god_class_high_method_count < god_class_medium_method_count - "
            "high severity will never trigger before medium"
        )
    if config.detectors.god_class_high_complexity < config.detectors.god_class_medium_complexity:
        warnings.append(
            "god_class_high_complexity < god_class_medium_complexity - "
            "high severity will never trigger before medium"
        )
    if config.detectors.god_class_high_loc < config.detectors.god_class_medium_loc:
        warnings.append(
            "god_class_high_loc < god_class_medium_loc - "
            "high severity will never trigger before medium"
        )
    if not 0 <= config.detectors.god_class_high_lcom <= 1:
        raise ConfigError("detectors.god_class_high_lcom must be between 0 and 1")
    if not 0 <= config.detectors.god_class_medium_lcom <= 1:
        raise ConfigError("detectors.god_class_medium_lcom must be between 0 and 1")
    if config.detectors.god_class_high_lcom < config.detectors.god_class_medium_lcom:
        warnings.append(
            "god_class_high_lcom < god_class_medium_lcom - "
            "high severity will never trigger before medium"
        )

    # Per-detector validation
    if config.detectors.ruff.max_findings <= 0:
        warnings.append("detectors.ruff.max_findings <= 0 will suppress all Ruff findings")
    if config.detectors.bandit.max_findings <= 0:
        warnings.append("detectors.bandit.max_findings <= 0 will suppress all Bandit findings")
    if config.detectors.mypy.max_findings <= 0:
        warnings.append("detectors.mypy.max_findings <= 0 will suppress all Mypy findings")
    if config.detectors.pylint.max_findings <= 0:
        warnings.append("detectors.pylint.max_findings <= 0 will suppress all Pylint findings")
    if config.detectors.pylint.jobs < 1:
        raise ConfigError("detectors.pylint.jobs must be at least 1")
    if config.detectors.vulture.min_confidence < 0 or config.detectors.vulture.min_confidence > 100:
        raise ConfigError("detectors.vulture.min_confidence must be between 0 and 100")
    if config.detectors.jscpd.threshold < 0 or config.detectors.jscpd.threshold > 100:
        raise ConfigError("detectors.jscpd.threshold must be between 0 and 100")

    # Validate severity thresholds are valid strings
    valid_severity = {"low", "medium", "high"}
    if config.detectors.bandit.severity_threshold.lower() not in valid_severity:
        raise ConfigError(
            f"detectors.bandit.severity_threshold must be one of: {', '.join(valid_severity)}"
        )
    if config.detectors.bandit.confidence_threshold.lower() not in valid_severity:
        raise ConfigError(
            f"detectors.bandit.confidence_threshold must be one of: {', '.join(valid_severity)}"
        )

    valid_radon_complexity = {"a", "b", "c", "d", "e", "f"}
    if config.detectors.radon.complexity_threshold.lower() not in valid_radon_complexity:
        raise ConfigError(
            f"detectors.radon.complexity_threshold must be one of: {', '.join(sorted(valid_radon_complexity))}"
        )

    valid_semgrep_severity = {"info", "warning", "error"}
    if config.detectors.semgrep.severity_threshold.lower() not in valid_semgrep_severity:
        raise ConfigError(
            f"detectors.semgrep.severity_threshold must be one of: {', '.join(valid_semgrep_severity)}"
        )

    # ========================================================================
    # Secrets validation
    # ========================================================================
    try:
        SecretsPolicy(config.secrets.policy.lower())
    except ValueError:
        valid_policies = [p.value for p in SecretsPolicy]
        raise ConfigError(
            f"secrets.policy '{config.secrets.policy}' is invalid. "
            f"Must be one of: {', '.join(valid_policies)}"
        )

    if config.secrets.entropy_threshold < 0:
        raise ConfigError("secrets.entropy_threshold cannot be negative")
    if config.secrets.entropy_threshold > 8:
        warnings.append(
            "secrets.entropy_threshold > 8 is very high - may miss legitimate secrets"
        )
    if config.secrets.min_entropy_length < 1:
        raise ConfigError("secrets.min_entropy_length must be at least 1")
    if config.secrets.parallel_workers < 1:
        raise ConfigError("secrets.parallel_workers must be at least 1")

    # Validate custom patterns (if any are CustomSecretPattern instances)
    for i, pattern in enumerate(config.secrets.custom_patterns):
        if isinstance(pattern, dict):
            # Pattern is a dict - validate regex
            if "pattern" in pattern:
                try:
                    re.compile(pattern["pattern"])
                except re.error as e:
                    raise ConfigError(
                        f"Invalid regex in secrets.custom_patterns[{i}]: {e}"
                    )

    # ========================================================================
    # Logging validation
    # ========================================================================
    try:
        LogLevel(config.logging.level.upper())
    except ValueError:
        valid_levels = [l.value for l in LogLevel]
        raise ConfigError(
            f"logging.level '{config.logging.level}' is invalid. "
            f"Must be one of: {', '.join(valid_levels)}"
        )

    valid_formats = {"human", "json"}
    if config.logging.format.lower() not in valid_formats:
        raise ConfigError(
            f"logging.format must be one of: {', '.join(valid_formats)}"
        )

    # ========================================================================
    # RAG validation
    # ========================================================================
    if config.rag.cache_ttl < 0:
        raise ConfigError("rag.cache_ttl cannot be negative")
    if config.rag.cache_max_size < 0:
        raise ConfigError("rag.cache_max_size cannot be negative")
    if config.rag.cache_enabled and config.rag.cache_max_size == 0:
        warnings.append(
            "rag.cache_enabled is True but cache_max_size is 0 - cache will be ineffective"
        )

    # ========================================================================
    # Embeddings validation
    # ========================================================================
    valid_backends = {"openai", "local", "deepinfra"}
    if config.embeddings.backend.lower() not in valid_backends:
        raise ConfigError(
            f"embeddings.backend must be one of: {', '.join(valid_backends)}"
        )

    # ========================================================================
    # Reporting validation
    # ========================================================================
    valid_themes = {"light", "dark", "custom"}
    if config.reporting.theme_name.lower() not in valid_themes:
        raise ConfigError(
            f"reporting.theme_name must be one of: {', '.join(valid_themes)}"
        )

    if config.reporting.max_findings <= 0:
        warnings.append("reporting.max_findings <= 0 will show no findings in reports")

    if config.reporting.max_snippet_lines <= 0:
        warnings.append("reporting.max_snippet_lines <= 0 will disable code snippets")

    # Validate color formats (basic hex check)
    hex_color_pattern = re.compile(r'^#[0-9a-fA-F]{6}$')
    theme = config.reporting.theme
    color_fields = [
        ("primary_color", theme.primary_color),
        ("header_gradient_start", theme.header_gradient_start),
        ("header_gradient_end", theme.header_gradient_end),
        ("background_color", theme.background_color),
        ("text_color", theme.text_color),
        ("link_color", theme.link_color),
        ("grade_a_color", theme.grade_a_color),
        ("grade_b_color", theme.grade_b_color),
        ("grade_c_color", theme.grade_c_color),
        ("grade_d_color", theme.grade_d_color),
        ("grade_f_color", theme.grade_f_color),
    ]

    for field_name, color in color_fields:
        if not hex_color_pattern.match(color):
            warnings.append(
                f"reporting.theme.{field_name} '{color}' is not a valid hex color (expected #RRGGBB)"
            )

    return warnings


def generate_config_template(format: str = "yaml") -> str:
    """Generate configuration file template.

    Args:
        format: Template format ("yaml", "json", or "toml")

    Returns:
        Configuration template as string

    Raises:
        ValueError: If format is not supported
    """
    config = FalkorConfig()
    data = config.to_dict()

    if format == "yaml":
        if not HAS_YAML:
            raise ConfigError("YAML support not available. Install: pip install pyyaml")

        template = yaml.dump(data, default_flow_style=False, sort_keys=False)
        return f"""# Falkor Configuration File (.reporc)
#
# This file configures Falkor's behavior. It can be placed:
# - In your project root: .reporc
# - In your home directory: ~/.reporc
# - In your config directory: ~/.config/falkor.toml
#
# Environment variables can be referenced using ${{VAR_NAME}} syntax.

{template}"""

    elif format == "json":
        # Add comments as special keys (JSON doesn't support real comments)
        commented_data = {
            "_comment": "Falkor Configuration File (.reporc)",
            "_note": "Environment variables can be referenced using ${VAR_NAME} syntax",
        }
        commented_data.update(data)
        template = json.dumps(commented_data, indent=2)
        return template

    elif format == "toml":
        if not HAS_TOML:
            raise ConfigError("TOML support not available. Install: pip install tomli")

        # Manual TOML generation (tomli doesn't have dump)
        lines = [
            "# Repotoire Configuration File (repotoire.toml)",
            "#",
            "# This file configures Repotoire's behavior. It can be placed:",
            "# - In your project root: repotoire.toml",
            "# - In your home directory: ~/.config/repotoire.toml",
            "#",
            "# Environment variables can be referenced using ${VAR_NAME} syntax.",
            "",
            "[database]",
            f'host = "{data["database"]["host"]}"',
            f'port = {data["database"]["port"]}',
            f'password = "{data["database"]["password"] or ""}"',
            f'max_retries = {data["database"]["max_retries"]}',
            f'retry_backoff_factor = {data["database"]["retry_backoff_factor"]}',
            f'retry_base_delay = {data["database"]["retry_base_delay"]}',
            "",
            "[ingestion]",
            f'patterns = {json.dumps(data["ingestion"]["patterns"])}',
            f'follow_symlinks = {str(data["ingestion"]["follow_symlinks"]).lower()}',
            f'max_file_size_mb = {data["ingestion"]["max_file_size_mb"]}',
            f'batch_size = {data["ingestion"]["batch_size"]}',
            "",
            "[analysis]",
            f'min_modularity = {data["analysis"]["min_modularity"]}',
            f'max_coupling = {data["analysis"]["max_coupling"]}',
            "",
            "[logging]",
            f'level = "{data["logging"]["level"]}"',
            f'format = "{data["logging"]["format"]}"',
            f'file = "{data["logging"]["file"] or ""}"',
        ]

        return "\n".join(lines)

    else:
        raise ValueError(f"Unsupported format: {format}. Use 'yaml', 'json', or 'toml'")

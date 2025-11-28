"""Configuration management for Falkor.

Configuration Priority Chain (highest to lowest):
1. Command-line arguments (--neo4j-uri, --log-level, etc.)
2. Environment variables (FALKOR_NEO4J_URI, FALKOR_NEO4J_USER, etc.)
3. Config file (.reporc, falkor.toml)
4. Built-in defaults

Config files are searched hierarchically:
1. Current directory
2. Parent directories (up to root)
3. User home directory (~/.reporc or ~/.config/falkor.toml)

Environment Variable Names:
- FALKOR_NEO4J_URI
- FALKOR_NEO4J_USER
- FALKOR_NEO4J_PASSWORD
- FALKOR_INGESTION_PATTERNS (comma-separated)
- FALKOR_INGESTION_FOLLOW_SYMLINKS (true/false)
- FALKOR_INGESTION_MAX_FILE_SIZE_MB
- FALKOR_INGESTION_BATCH_SIZE
- FALKOR_ANALYSIS_MIN_MODULARITY
- FALKOR_ANALYSIS_MAX_COUPLING
- FALKOR_LOG_LEVEL (or LOG_LEVEL)
- FALKOR_LOG_FORMAT (or LOG_FORMAT)
- FALKOR_LOG_FILE (or LOG_FILE)

Example .reporc (YAML):
```yaml
neo4j:
  uri: bolt://localhost:7687
  user: neo4j
  password: ${NEO4J_PASSWORD}

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
  file: logs/falkor.log
```

Example falkor.toml:
```toml
[neo4j]
uri = "bolt://localhost:7687"
user = "neo4j"
password = "${NEO4J_PASSWORD}"

[ingestion]
patterns = ["**/*.py", "**/*.js"]
follow_symlinks = false
max_file_size_mb = 10
batch_size = 100

[analysis]
min_modularity = 0.3
max_coupling = 5.0

[logging]
level = "INFO"
format = "human"
file = "logs/falkor.log"
```
"""

import os
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional, Union
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


class ConfigError(Exception):
    """Raised when configuration is invalid or cannot be loaded."""
    pass


@dataclass
class Neo4jConfig:
    """Neo4j connection configuration."""
    uri: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password: Optional[str] = None
    max_retries: int = 3
    retry_backoff_factor: float = 2.0  # Exponential backoff multiplier
    retry_base_delay: float = 1.0  # Base delay in seconds


@dataclass
class IngestionConfig:
    """Ingestion pipeline configuration."""
    patterns: list[str] = field(default_factory=lambda: ["**/*.py"])
    follow_symlinks: bool = False
    max_file_size_mb: float = 10.0
    batch_size: int = 100


@dataclass
class AnalysisConfig:
    """Analysis engine configuration."""
    min_modularity: float = 0.3
    max_coupling: float = 5.0


@dataclass
class DetectorConfig:
    """Detector thresholds configuration."""
    # God class detector thresholds
    god_class_high_method_count: int = 20
    god_class_medium_method_count: int = 15
    god_class_high_complexity: int = 100
    god_class_medium_complexity: int = 50
    god_class_high_loc: int = 500
    god_class_medium_loc: int = 300
    god_class_high_lcom: float = 0.8  # Lack of cohesion (0-1, higher is worse)
    god_class_medium_lcom: float = 0.6


@dataclass
class CustomSecretPattern:
    """Custom secret pattern definition."""
    name: str  # Name for the pattern (e.g., "Internal API Key")
    pattern: str  # Regex pattern to match
    risk_level: str = "high"  # critical, high, medium, low
    remediation: str = ""  # Remediation suggestion


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
class FalkorConfig:
    """Complete Falkor configuration."""
    neo4j: Neo4jConfig = field(default_factory=Neo4jConfig)
    ingestion: IngestionConfig = field(default_factory=IngestionConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    detectors: DetectorConfig = field(default_factory=DetectorConfig)
    secrets: SecretsConfig = field(default_factory=SecretsConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    timescale: TimescaleConfig = field(default_factory=TimescaleConfig)
    rag: RAGConfig = field(default_factory=RAGConfig)
    embeddings: EmbeddingsConfig = field(default_factory=EmbeddingsConfig)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FalkorConfig":
        """Create config from dictionary.

        Args:
            data: Configuration dictionary

        Returns:
            FalkorConfig instance
        """
        # Expand environment variables
        data = _expand_env_vars(data)

        return cls(
            neo4j=Neo4jConfig(**data.get("neo4j", {})),
            ingestion=IngestionConfig(**data.get("ingestion", {})),
            analysis=AnalysisConfig(**data.get("analysis", {})),
            detectors=DetectorConfig(**data.get("detectors", {})),
            secrets=SecretsConfig(**data.get("secrets", {})),
            logging=LoggingConfig(**data.get("logging", {})),
            rag=RAGConfig(**data.get("rag", {})),
            embeddings=EmbeddingsConfig(**data.get("embeddings", {})),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary.

        Returns:
            Configuration as dictionary
        """
        return {
            "neo4j": {
                "uri": self.neo4j.uri,
                "user": self.neo4j.user,
                "password": self.neo4j.password,
                "max_retries": self.neo4j.max_retries,
                "retry_backoff_factor": self.neo4j.retry_backoff_factor,
                "retry_base_delay": self.neo4j.retry_base_delay,
            },
            "ingestion": {
                "patterns": self.ingestion.patterns,
                "follow_symlinks": self.ingestion.follow_symlinks,
                "max_file_size_mb": self.ingestion.max_file_size_mb,
                "batch_size": self.ingestion.batch_size,
            },
            "analysis": {
                "min_modularity": self.analysis.min_modularity,
                "max_coupling": self.analysis.max_coupling,
            },
            "detectors": {
                "god_class_high_method_count": self.detectors.god_class_high_method_count,
                "god_class_medium_method_count": self.detectors.god_class_medium_method_count,
                "god_class_high_complexity": self.detectors.god_class_high_complexity,
                "god_class_medium_complexity": self.detectors.god_class_medium_complexity,
                "god_class_high_loc": self.detectors.god_class_high_loc,
                "god_class_medium_loc": self.detectors.god_class_medium_loc,
                "god_class_high_lcom": self.detectors.god_class_high_lcom,
                "god_class_medium_lcom": self.detectors.god_class_medium_lcom,
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
        }

    def merge(self, other: "FalkorConfig") -> "FalkorConfig":
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

        return FalkorConfig.from_dict(merged_dict)


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

    Returns:
        Configuration dictionary with values from environment
    """
    config = {}

    # Neo4j configuration
    neo4j = {}
    if uri := os.getenv("FALKOR_NEO4J_URI"):
        neo4j["uri"] = uri
    if user := os.getenv("FALKOR_NEO4J_USER"):
        neo4j["user"] = user
    if password := os.getenv("FALKOR_NEO4J_PASSWORD"):
        neo4j["password"] = password
    if max_retries := os.getenv("FALKOR_NEO4J_MAX_RETRIES"):
        try:
            neo4j["max_retries"] = int(max_retries)
        except ValueError:
            logger.warning(f"Invalid FALKOR_NEO4J_MAX_RETRIES value: {max_retries}, ignoring")
    if retry_backoff_factor := os.getenv("FALKOR_NEO4J_RETRY_BACKOFF_FACTOR"):
        try:
            neo4j["retry_backoff_factor"] = float(retry_backoff_factor)
        except ValueError:
            logger.warning(f"Invalid FALKOR_NEO4J_RETRY_BACKOFF_FACTOR value: {retry_backoff_factor}, ignoring")
    if retry_base_delay := os.getenv("FALKOR_NEO4J_RETRY_BASE_DELAY"):
        try:
            neo4j["retry_base_delay"] = float(retry_base_delay)
        except ValueError:
            logger.warning(f"Invalid FALKOR_NEO4J_RETRY_BASE_DELAY value: {retry_base_delay}, ignoring")
    if neo4j:
        config["neo4j"] = neo4j

    # Ingestion configuration
    ingestion = {}
    if patterns := os.getenv("FALKOR_INGESTION_PATTERNS"):
        ingestion["patterns"] = [p.strip() for p in patterns.split(",")]
    if follow_symlinks := os.getenv("FALKOR_INGESTION_FOLLOW_SYMLINKS"):
        ingestion["follow_symlinks"] = follow_symlinks.lower() in ("true", "1", "yes")
    if max_file_size := os.getenv("FALKOR_INGESTION_MAX_FILE_SIZE_MB"):
        try:
            ingestion["max_file_size_mb"] = float(max_file_size)
        except ValueError:
            logger.warning(f"Invalid FALKOR_INGESTION_MAX_FILE_SIZE_MB: {max_file_size}")
    if batch_size := os.getenv("FALKOR_INGESTION_BATCH_SIZE"):
        try:
            ingestion["batch_size"] = int(batch_size)
        except ValueError:
            logger.warning(f"Invalid FALKOR_INGESTION_BATCH_SIZE: {batch_size}")
    if ingestion:
        config["ingestion"] = ingestion

    # Analysis configuration
    analysis = {}
    if min_modularity := os.getenv("FALKOR_ANALYSIS_MIN_MODULARITY"):
        try:
            analysis["min_modularity"] = float(min_modularity)
        except ValueError:
            logger.warning(f"Invalid FALKOR_ANALYSIS_MIN_MODULARITY: {min_modularity}")
    if max_coupling := os.getenv("FALKOR_ANALYSIS_MAX_COUPLING"):
        try:
            analysis["max_coupling"] = float(max_coupling)
        except ValueError:
            logger.warning(f"Invalid FALKOR_ANALYSIS_MAX_COUPLING: {max_coupling}")
    if analysis:
        config["analysis"] = analysis

    # Secrets configuration
    secrets = {}
    if secrets_enabled := os.getenv("FALKOR_SECRETS_ENABLED"):
        secrets["enabled"] = secrets_enabled.lower() in ("true", "1", "yes")
    if secrets_policy := os.getenv("FALKOR_SECRETS_POLICY"):
        secrets["policy"] = secrets_policy.lower()
    if secrets:
        config["secrets"] = secrets

    # Logging configuration (support both FALKOR_ prefix and unprefixed)
    logging_cfg = {}
    if level := os.getenv("FALKOR_LOG_LEVEL") or os.getenv("LOG_LEVEL"):
        logging_cfg["level"] = level.upper()
    if format := os.getenv("FALKOR_LOG_FORMAT") or os.getenv("LOG_FORMAT"):
        logging_cfg["format"] = format
    if file := os.getenv("FALKOR_LOG_FILE") or os.getenv("LOG_FILE"):
        logging_cfg["file"] = file
    if logging_cfg:
        config["logging"] = logging_cfg

    # TimescaleDB configuration (support both REPOTOIRE_ and FALKOR_ prefixes)
    timescale = {}
    if enabled := os.getenv("FALKOR_TIMESCALE_ENABLED") or os.getenv("REPOTOIRE_TIMESCALE_ENABLED"):
        timescale["enabled"] = enabled.lower() in ("true", "1", "yes")
    if connection_string := os.getenv("FALKOR_TIMESCALE_URI") or os.getenv("REPOTOIRE_TIMESCALE_URI"):
        timescale["connection_string"] = connection_string
    if auto_track := os.getenv("FALKOR_TIMESCALE_AUTO_TRACK") or os.getenv("REPOTOIRE_TIMESCALE_AUTO_TRACK"):
        timescale["auto_track"] = auto_track.lower() in ("true", "1", "yes")
    if timescale:
        config["timescale"] = timescale

    # RAG configuration (support both REPOTOIRE_ and FALKOR_ prefixes)
    rag = {}
    if cache_enabled := os.getenv("FALKOR_RAG_CACHE_ENABLED") or os.getenv("REPOTOIRE_RAG_CACHE_ENABLED"):
        rag["cache_enabled"] = cache_enabled.lower() in ("true", "1", "yes")
    if cache_ttl := os.getenv("FALKOR_RAG_CACHE_TTL") or os.getenv("REPOTOIRE_RAG_CACHE_TTL"):
        try:
            rag["cache_ttl"] = int(cache_ttl)
        except ValueError:
            logger.warning(f"Invalid RAG_CACHE_TTL value: {cache_ttl}")
    if cache_max_size := os.getenv("FALKOR_RAG_CACHE_MAX_SIZE") or os.getenv("REPOTOIRE_RAG_CACHE_MAX_SIZE"):
        try:
            rag["cache_max_size"] = int(cache_max_size)
        except ValueError:
            logger.warning(f"Invalid RAG_CACHE_MAX_SIZE value: {cache_max_size}")
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
            "# Falkor Configuration File (falkor.toml)",
            "#",
            "# This file configures Falkor's behavior. It can be placed:",
            "# - In your project root: falkor.toml",
            "# - In your home directory: ~/.config/falkor.toml",
            "#",
            "# Environment variables can be referenced using ${VAR_NAME} syntax.",
            "",
            "[neo4j]",
            f'uri = "{data["neo4j"]["uri"]}"',
            f'user = "{data["neo4j"]["user"]}"',
            f'password = "{data["neo4j"]["password"] or ""}"',
            f'max_retries = {data["neo4j"]["max_retries"]}',
            f'retry_backoff_factor = {data["neo4j"]["retry_backoff_factor"]}',
            f'retry_base_delay = {data["neo4j"]["retry_base_delay"]}',
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

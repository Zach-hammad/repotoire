"""Unit tests for configuration management."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from repotoire.config import (
    FalkorConfig,
    Neo4jConfig,
    IngestionConfig,
    AnalysisConfig,
    LoggingConfig,
    ConfigError,
    load_config_file,
    find_config_file,
    load_config,
    load_config_from_env,
    generate_config_template,
    _expand_env_vars,
)


class TestConfigDataClasses:
    """Test configuration data classes."""

    def test_neo4j_config_defaults(self):
        """Test Neo4j config default values."""
        config = Neo4jConfig()
        assert config.uri == "bolt://localhost:7687"
        assert config.user == "neo4j"
        assert config.password is None

    def test_ingestion_config_defaults(self):
        """Test ingestion config default values."""
        config = IngestionConfig()
        assert config.patterns == ["**/*.py"]
        assert config.follow_symlinks is False
        assert config.max_file_size_mb == 10.0
        assert config.batch_size == 100

    def test_analysis_config_defaults(self):
        """Test analysis config default values."""
        config = AnalysisConfig()
        assert config.min_modularity == 0.3
        assert config.max_coupling == 5.0

    def test_logging_config_defaults(self):
        """Test logging config default values."""
        config = LoggingConfig()
        assert config.level == "INFO"
        assert config.format == "human"
        assert config.file is None

    def test_falkor_config_defaults(self):
        """Test complete Falkor config with defaults."""
        config = FalkorConfig()
        assert isinstance(config.neo4j, Neo4jConfig)
        assert isinstance(config.ingestion, IngestionConfig)
        assert isinstance(config.analysis, AnalysisConfig)
        assert isinstance(config.logging, LoggingConfig)

    def test_falkor_config_from_dict(self):
        """Test creating config from dictionary."""
        data = {
            "neo4j": {
                "uri": "bolt://custom:7687",
                "user": "admin",
                "password": "secret",
            },
            "ingestion": {
                "patterns": ["**/*.js"],
                "follow_symlinks": True,
            },
            "logging": {
                "level": "DEBUG",
            },
        }

        config = FalkorConfig.from_dict(data)

        assert config.neo4j.uri == "bolt://custom:7687"
        assert config.neo4j.user == "admin"
        assert config.neo4j.password == "secret"
        assert config.ingestion.patterns == ["**/*.js"]
        assert config.ingestion.follow_symlinks is True
        assert config.logging.level == "DEBUG"

    def test_falkor_config_to_dict(self):
        """Test converting config to dictionary."""
        config = FalkorConfig()
        data = config.to_dict()

        assert "neo4j" in data
        assert "ingestion" in data
        assert "analysis" in data
        assert "logging" in data

        assert data["neo4j"]["uri"] == "bolt://localhost:7687"
        assert data["ingestion"]["patterns"] == ["**/*.py"]

    def test_falkor_config_merge(self):
        """Test merging two configs."""
        config1 = FalkorConfig()
        config2_data = {
            "neo4j": {"uri": "bolt://new:7687"},
            "logging": {"level": "DEBUG"},
        }
        config2 = FalkorConfig.from_dict(config2_data)

        merged = config1.merge(config2)

        # config2 values should override
        assert merged.neo4j.uri == "bolt://new:7687"
        assert merged.logging.level == "DEBUG"

        # config1 values should be preserved
        assert merged.neo4j.user == "neo4j"
        assert merged.ingestion.patterns == ["**/*.py"]


class TestEnvVarExpansion:
    """Test environment variable expansion."""

    def test_expand_simple_string(self):
        """Test expanding ${VAR} syntax."""
        with patch.dict("os.environ", {"MY_VAR": "test_value"}):
            result = _expand_env_vars("${MY_VAR}")
            assert result == "test_value"

    def test_expand_dollar_var(self):
        """Test expanding $VAR syntax."""
        with patch.dict("os.environ", {"MY_VAR": "test_value"}):
            result = _expand_env_vars("$MY_VAR")
            assert result == "test_value"

    def test_expand_in_string(self):
        """Test expanding variable in middle of string."""
        with patch.dict("os.environ", {"HOST": "localhost"}):
            result = _expand_env_vars("bolt://${HOST}:7687")
            assert result == "bolt://localhost:7687"

    def test_expand_multiple_vars(self):
        """Test expanding multiple variables."""
        with patch.dict("os.environ", {"HOST": "localhost", "PORT": "7687"}):
            result = _expand_env_vars("bolt://${HOST}:${PORT}")
            assert result == "bolt://localhost:7687"

    def test_expand_dict(self):
        """Test expanding variables in dictionary."""
        with patch.dict("os.environ", {"PASSWORD": "secret"}):
            data = {"neo4j": {"password": "${PASSWORD}"}}
            result = _expand_env_vars(data)
            assert result["neo4j"]["password"] == "secret"

    def test_expand_list(self):
        """Test expanding variables in list."""
        with patch.dict("os.environ", {"PATTERN": "*.py"}):
            data = ["${PATTERN}", "*.js"]
            result = _expand_env_vars(data)
            assert result == ["*.py", "*.js"]

    def test_expand_undefined_var(self):
        """Test that undefined variables are left unchanged."""
        result = _expand_env_vars("${UNDEFINED_VAR}")
        assert result == "${UNDEFINED_VAR}"


class TestConfigFileLoading:
    """Test loading configuration from files."""

    def test_load_yaml_config(self):
        """Test loading YAML config file."""
        pytest.importorskip("yaml")  # Skip if PyYAML not installed

        temp_dir = tempfile.mkdtemp()
        config_path = Path(temp_dir) / ".falkorrc"

        yaml_content = """
neo4j:
  uri: bolt://test:7687
  user: test_user

ingestion:
  patterns:
    - "**/*.py"
    - "**/*.js"
"""
        config_path.write_text(yaml_content)

        try:
            data = load_config_file(config_path)
            assert data["neo4j"]["uri"] == "bolt://test:7687"
            assert data["neo4j"]["user"] == "test_user"
            assert "**/*.js" in data["ingestion"]["patterns"]
        finally:
            import shutil
            shutil.rmtree(temp_dir)

    def test_load_json_config(self):
        """Test loading JSON config file."""
        temp_dir = tempfile.mkdtemp()
        config_path = Path(temp_dir) / ".falkorrc"

        json_content = {
            "neo4j": {
                "uri": "bolt://test:7687",
                "user": "test_user",
            }
        }
        config_path.write_text(json.dumps(json_content))

        try:
            data = load_config_file(config_path)
            assert data["neo4j"]["uri"] == "bolt://test:7687"
            assert data["neo4j"]["user"] == "test_user"
        finally:
            import shutil
            shutil.rmtree(temp_dir)

    def test_load_toml_config(self):
        """Test loading TOML config file."""
        pytest.importorskip("tomli")  # Skip if tomli not installed

        temp_dir = tempfile.mkdtemp()
        config_path = Path(temp_dir) / "falkor.toml"

        toml_content = """
[neo4j]
uri = "bolt://test:7687"
user = "test_user"

[ingestion]
patterns = ["**/*.py", "**/*.js"]
"""
        config_path.write_text(toml_content)

        try:
            data = load_config_file(config_path)
            assert data["neo4j"]["uri"] == "bolt://test:7687"
            assert data["neo4j"]["user"] == "test_user"
            assert "**/*.js" in data["ingestion"]["patterns"]
        finally:
            import shutil
            shutil.rmtree(temp_dir)

    def test_load_config_file_not_found(self):
        """Test error when config file doesn't exist."""
        with pytest.raises(ConfigError, match="not found"):
            load_config_file(Path("/nonexistent/config.yaml"))

    def test_load_config_invalid_format(self):
        """Test error with unsupported file format."""
        temp_dir = tempfile.mkdtemp()
        config_path = Path(temp_dir) / "config.xml"
        config_path.write_text("<config></config>")

        try:
            with pytest.raises(ConfigError, match="Unsupported"):
                load_config_file(config_path)
        finally:
            import shutil
            shutil.rmtree(temp_dir)


class TestConfigFileSearch:
    """Test hierarchical config file search."""

    def test_find_config_in_current_dir(self):
        """Test finding .falkorrc in current directory."""
        temp_dir = tempfile.mkdtemp()
        config_path = Path(temp_dir) / ".falkorrc"
        config_path.write_text("{}")

        try:
            found = find_config_file(Path(temp_dir))
            assert found == config_path
        finally:
            import shutil
            shutil.rmtree(temp_dir)

    def test_find_toml_config_in_current_dir(self):
        """Test finding falkor.toml in current directory."""
        temp_dir = tempfile.mkdtemp()
        config_path = Path(temp_dir) / "falkor.toml"
        config_path.write_text("")

        try:
            found = find_config_file(Path(temp_dir))
            assert found == config_path
        finally:
            import shutil
            shutil.rmtree(temp_dir)

    def test_find_config_in_parent_dir(self):
        """Test finding config in parent directory."""
        temp_dir = tempfile.mkdtemp()
        parent = Path(temp_dir)
        child = parent / "subdir"
        child.mkdir()

        config_path = parent / ".falkorrc"
        config_path.write_text("{}")

        try:
            found = find_config_file(child)
            assert found == config_path
        finally:
            import shutil
            shutil.rmtree(temp_dir)

    def test_find_config_prefers_falkorrc(self):
        """Test that .falkorrc is preferred over falkor.toml."""
        temp_dir = tempfile.mkdtemp()
        falkorrc = Path(temp_dir) / ".falkorrc"
        toml_file = Path(temp_dir) / "falkor.toml"

        falkorrc.write_text("{}")
        toml_file.write_text("")

        try:
            found = find_config_file(Path(temp_dir))
            assert found == falkorrc
        finally:
            import shutil
            shutil.rmtree(temp_dir)

    def test_find_config_returns_none(self):
        """Test returns None when no config found."""
        temp_dir = tempfile.mkdtemp()

        try:
            found = find_config_file(Path(temp_dir))
            assert found is None
        finally:
            import shutil
            shutil.rmtree(temp_dir)


class TestLoadConfig:
    """Test high-level config loading."""

    def test_load_config_explicit_file(self):
        """Test loading with explicit config file path."""
        temp_dir = tempfile.mkdtemp()
        config_path = Path(temp_dir) / "myconfig.json"
        config_path.write_text('{"neo4j": {"uri": "bolt://custom:7687"}}')

        try:
            config = load_config(config_file=config_path)
            assert config.neo4j.uri == "bolt://custom:7687"
        finally:
            import shutil
            shutil.rmtree(temp_dir)

    def test_load_config_search(self):
        """Test loading via hierarchical search."""
        temp_dir = tempfile.mkdtemp()
        config_path = Path(temp_dir) / ".falkorrc"
        config_path.write_text('{"neo4j": {"user": "custom"}}')

        try:
            config = load_config(search_path=Path(temp_dir))
            assert config.neo4j.user == "custom"
        finally:
            import shutil
            shutil.rmtree(temp_dir)

    def test_load_config_defaults(self):
        """Test loading returns defaults when no config found."""
        temp_dir = tempfile.mkdtemp()

        try:
            config = load_config(search_path=Path(temp_dir))
            # Should have defaults
            assert config.neo4j.uri == "bolt://localhost:7687"
            assert config.ingestion.patterns == ["**/*.py"]
        finally:
            import shutil
            shutil.rmtree(temp_dir)

    def test_load_config_with_env_vars(self):
        """Test that environment variables are expanded."""
        temp_dir = tempfile.mkdtemp()
        config_path = Path(temp_dir) / ".falkorrc"
        config_path.write_text('{"neo4j": {"password": "${TEST_PASSWORD}"}}')

        try:
            with patch.dict("os.environ", {"TEST_PASSWORD": "secret123"}):
                config = load_config(config_file=config_path)
                assert config.neo4j.password == "secret123"
        finally:
            import shutil
            shutil.rmtree(temp_dir)


class TestGenerateConfigTemplate:
    """Test config template generation."""

    def test_generate_yaml_template(self):
        """Test generating YAML template."""
        pytest.importorskip("yaml")

        template = generate_config_template(format="yaml")

        assert "neo4j:" in template
        assert "ingestion:" in template
        assert "patterns:" in template
        assert "bolt://localhost:7687" in template

    def test_generate_json_template(self):
        """Test generating JSON template."""
        template = generate_config_template(format="json")

        assert '"neo4j"' in template
        assert '"ingestion"' in template
        assert '"patterns"' in template

        # Verify it's valid JSON
        data = json.loads(template)
        assert "neo4j" in data
        assert "_comment" in data  # Comment is stored as key

    def test_generate_toml_template(self):
        """Test generating TOML template."""
        template = generate_config_template(format="toml")

        assert "[neo4j]" in template
        assert "[ingestion]" in template
        assert 'uri = "bolt://localhost:7687"' in template

    def test_generate_invalid_format(self):
        """Test error with invalid format."""
        with pytest.raises(ValueError, match="Unsupported format"):
            generate_config_template(format="xml")


class TestEnvironmentVariableLoading:
    """Test loading configuration from environment variables."""

    def test_load_from_env_neo4j(self):
        """Test loading Neo4j config from environment."""
        with patch.dict("os.environ", {
            "FALKOR_NEO4J_URI": "bolt://prod:7687",
            "FALKOR_NEO4J_USER": "admin",
            "FALKOR_NEO4J_PASSWORD": "secret123",
        }):
            data = load_config_from_env()

            assert data["neo4j"]["uri"] == "bolt://prod:7687"
            assert data["neo4j"]["user"] == "admin"
            assert data["neo4j"]["password"] == "secret123"

    def test_load_from_env_ingestion(self):
        """Test loading ingestion config from environment."""
        with patch.dict("os.environ", {
            "FALKOR_INGESTION_PATTERNS": "**/*.py,**/*.js,**/*.ts",
            "FALKOR_INGESTION_FOLLOW_SYMLINKS": "true",
            "FALKOR_INGESTION_MAX_FILE_SIZE_MB": "20.5",
            "FALKOR_INGESTION_BATCH_SIZE": "200",
        }):
            data = load_config_from_env()

            assert data["ingestion"]["patterns"] == ["**/*.py", "**/*.js", "**/*.ts"]
            assert data["ingestion"]["follow_symlinks"] is True
            assert data["ingestion"]["max_file_size_mb"] == 20.5
            assert data["ingestion"]["batch_size"] == 200

    def test_load_from_env_logging(self):
        """Test loading logging config from environment."""
        with patch.dict("os.environ", {
            "FALKOR_LOG_LEVEL": "debug",
            "FALKOR_LOG_FORMAT": "json",
            "FALKOR_LOG_FILE": "logs/test.log",
        }):
            data = load_config_from_env()

            assert data["logging"]["level"] == "DEBUG"
            assert data["logging"]["format"] == "json"
            assert data["logging"]["file"] == "logs/test.log"

    def test_load_from_env_logging_unprefixed(self):
        """Test loading logging config from unprefixed env vars."""
        with patch.dict("os.environ", {
            "LOG_LEVEL": "warning",
            "LOG_FORMAT": "json",
            "LOG_FILE": "app.log",
        }):
            data = load_config_from_env()

            assert data["logging"]["level"] == "WARNING"
            assert data["logging"]["format"] == "json"
            assert data["logging"]["file"] == "app.log"

    def test_load_from_env_logging_prefix_takes_precedence(self):
        """Test that FALKOR_ prefix takes precedence over unprefixed."""
        with patch.dict("os.environ", {
            "FALKOR_LOG_LEVEL": "debug",
            "LOG_LEVEL": "info",
        }):
            data = load_config_from_env()
            assert data["logging"]["level"] == "DEBUG"

    def test_load_from_env_invalid_numbers(self):
        """Test handling of invalid numeric values."""
        with patch.dict("os.environ", {
            "FALKOR_INGESTION_MAX_FILE_SIZE_MB": "not_a_number",
            "FALKOR_INGESTION_BATCH_SIZE": "invalid",
        }):
            data = load_config_from_env()
            # Should not include invalid values
            assert "max_file_size_mb" not in data.get("ingestion", {})
            assert "batch_size" not in data.get("ingestion", {})

    def test_load_from_env_empty(self):
        """Test loading with no environment variables set."""
        with patch.dict("os.environ", {}, clear=True):
            data = load_config_from_env()
            assert data == {}


class TestFallbackChain:
    """Test configuration fallback chain."""

    def test_fallback_defaults_only(self):
        """Test config with only defaults."""
        temp_dir = tempfile.mkdtemp()

        try:
            with patch.dict("os.environ", {}, clear=True):
                config = load_config(search_path=Path(temp_dir))

                # Should have defaults
                assert config.neo4j.uri == "bolt://localhost:7687"
                assert config.neo4j.user == "neo4j"
                assert config.ingestion.patterns == ["**/*.py"]
        finally:
            import shutil
            shutil.rmtree(temp_dir)

    def test_fallback_file_overrides_defaults(self):
        """Test config file overrides defaults."""
        temp_dir = tempfile.mkdtemp()
        config_path = Path(temp_dir) / ".falkorrc"
        config_path.write_text('{"neo4j": {"uri": "bolt://file:7687"}}')

        try:
            with patch.dict("os.environ", {}, clear=True):
                config = load_config(search_path=Path(temp_dir))

                # File value should override default
                assert config.neo4j.uri == "bolt://file:7687"
                # Default should still be present
                assert config.neo4j.user == "neo4j"
        finally:
            import shutil
            shutil.rmtree(temp_dir)

    def test_fallback_env_overrides_file(self):
        """Test environment variables override config file."""
        temp_dir = tempfile.mkdtemp()
        config_path = Path(temp_dir) / ".falkorrc"
        config_path.write_text('{"neo4j": {"uri": "bolt://file:7687"}}')

        try:
            with patch.dict("os.environ", {"FALKOR_NEO4J_URI": "bolt://env:7687"}):
                config = load_config(search_path=Path(temp_dir))

                # Env value should override file
                assert config.neo4j.uri == "bolt://env:7687"
        finally:
            import shutil
            shutil.rmtree(temp_dir)

    def test_fallback_chain_complete(self):
        """Test complete fallback chain."""
        temp_dir = tempfile.mkdtemp()
        config_path = Path(temp_dir) / ".falkorrc"
        config_path.write_text(json.dumps({
            "neo4j": {"uri": "bolt://file:7687", "user": "file_user"},
            "ingestion": {"patterns": ["**/*.py", "**/*.js"]},
        }))

        try:
            with patch.dict("os.environ", {
                "FALKOR_NEO4J_URI": "bolt://env:7687",
                "FALKOR_INGESTION_BATCH_SIZE": "150",
            }):
                config = load_config(search_path=Path(temp_dir))

                # Env var overrides file
                assert config.neo4j.uri == "bolt://env:7687"

                # File overrides default
                assert config.neo4j.user == "file_user"
                assert config.ingestion.patterns == ["**/*.py", "**/*.js"]

                # Env var provides new value
                assert config.ingestion.batch_size == 150

                # Default remains
                assert config.ingestion.follow_symlinks is False
        finally:
            import shutil
            shutil.rmtree(temp_dir)

    def test_fallback_disable_env(self):
        """Test disabling environment variable loading."""
        with patch.dict("os.environ", {"FALKOR_NEO4J_URI": "bolt://env:7687"}):
            config = load_config(use_env=False)

            # Should not load from env
            assert config.neo4j.uri == "bolt://localhost:7687"  # default

    def test_fallback_chain_with_expansion(self):
        """Test that environment variable expansion works in config files."""
        temp_dir = tempfile.mkdtemp()
        config_path = Path(temp_dir) / ".falkorrc"
        config_path.write_text('{"neo4j": {"password": "${NEO4J_PASSWORD}"}}')

        try:
            with patch.dict("os.environ", {"NEO4J_PASSWORD": "expanded_secret"}):
                config = load_config(search_path=Path(temp_dir))

                # ${VAR} should be expanded
                assert config.neo4j.password == "expanded_secret"
        finally:
            import shutil
            shutil.rmtree(temp_dir)

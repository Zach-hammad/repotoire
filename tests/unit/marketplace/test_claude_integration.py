"""Tests for ClaudeConfigManager.

Tests the Claude Desktop/Code configuration management including:
- MCP server management
- Slash command management
- Hook management
- Backup creation
- Asset sync/unsync
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from repotoire.marketplace.claude_integration import (
    ClaudeConfigManager,
    ClaudeConfigError,
    ConfigBackupError,
    MCPServerConfig,
    HookConfig,
)


@pytest.fixture
def temp_claude_dir(tmp_path):
    """Create a temporary Claude directory structure."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "commands").mkdir()
    (claude_dir / "backups").mkdir()
    return claude_dir


@pytest.fixture
def config_manager(tmp_path, temp_claude_dir):
    """Create a ClaudeConfigManager with temp paths."""
    config_path = tmp_path / ".claude.json"

    manager = ClaudeConfigManager(config_path=config_path)
    # Override the class-level paths for testing
    manager.CLAUDE_DIR = temp_claude_dir
    manager.COMMANDS_DIR = temp_claude_dir / "commands"
    manager.SETTINGS_FILE = temp_claude_dir / "settings.json"
    manager.BACKUPS_DIR = temp_claude_dir / "backups"

    return manager


class TestMCPServerConfig:
    """Tests for MCPServerConfig dataclass."""

    def test_to_dict_basic(self):
        """Test basic to_dict conversion."""
        config = MCPServerConfig(
            name="test-server",
            command="node",
        )

        result = config.to_dict()

        assert result == {"command": "node"}

    def test_to_dict_with_args(self):
        """Test to_dict with arguments."""
        config = MCPServerConfig(
            name="test-server",
            command="npx",
            args=["-y", "@test/server"],
        )

        result = config.to_dict()

        assert result == {
            "command": "npx",
            "args": ["-y", "@test/server"],
        }

    def test_to_dict_with_env(self):
        """Test to_dict with environment variables."""
        config = MCPServerConfig(
            name="test-server",
            command="node",
            env={"API_KEY": "secret"},
        )

        result = config.to_dict()

        assert result == {
            "command": "node",
            "env": {"API_KEY": "secret"},
        }

    def test_to_dict_full(self):
        """Test to_dict with all fields."""
        config = MCPServerConfig(
            name="test-server",
            command="npx",
            args=["-y", "@test/server"],
            env={"KEY": "value"},
            enabled=True,
        )

        result = config.to_dict()

        assert result == {
            "command": "npx",
            "args": ["-y", "@test/server"],
            "env": {"KEY": "value"},
        }


class TestHookConfig:
    """Tests for HookConfig dataclass."""

    def test_to_dict_basic(self):
        """Test basic to_dict conversion."""
        hook = HookConfig(
            event="PostToolUse",
            command="echo 'done'",
        )

        result = hook.to_dict()

        assert result == {
            "type": "command",
            "command": "echo 'done'",
        }

    def test_to_dict_with_timeout(self):
        """Test to_dict with timeout."""
        hook = HookConfig(
            event="PreToolUse",
            command="validate",
            timeout=30,
        )

        result = hook.to_dict()

        assert result == {
            "type": "command",
            "command": "validate",
            "timeout": 30,
        }


class TestClaudeConfigManagerInit:
    """Tests for ClaudeConfigManager initialization."""

    def test_init_with_custom_path(self, tmp_path):
        """Test initialization with custom config path."""
        config_path = tmp_path / "custom.json"

        manager = ClaudeConfigManager(config_path=config_path)

        assert manager.config_path == config_path

    def test_config_path_defaults_to_home(self, tmp_path):
        """Test that config path defaults to home directory."""
        manager = ClaudeConfigManager()

        # Should default to ~/.claude.json if no config exists
        assert manager.config_path.name == ".claude.json"

    def test_find_config_path_returns_existing(self, tmp_path):
        """Test _find_config_path returns existing file."""
        config_path = tmp_path / ".claude.json"
        config_path.write_text("{}")

        manager = ClaudeConfigManager(config_path=config_path)

        assert manager.config_path == config_path


class TestMCPServerManagement:
    """Tests for MCP server management."""

    def test_add_mcp_server_basic(self, config_manager):
        """Test adding a basic MCP server."""
        config_manager.add_mcp_server(
            name="test-server",
            command="node",
            args=["server.js"],
        )

        config = json.loads(config_manager.config_path.read_text())

        assert "mcpServers" in config
        assert "test-server" in config["mcpServers"]
        assert config["mcpServers"]["test-server"]["command"] == "node"
        assert config["mcpServers"]["test-server"]["args"] == ["server.js"]

    def test_add_mcp_server_with_env(self, config_manager):
        """Test adding an MCP server with environment variables."""
        config_manager.add_mcp_server(
            name="api-server",
            command="npx",
            args=["-y", "@test/server"],
            env={"API_KEY": "${API_KEY}"},
        )

        config = json.loads(config_manager.config_path.read_text())

        assert config["mcpServers"]["api-server"]["env"]["API_KEY"] == "${API_KEY}"

    def test_add_mcp_server_replaces_existing(self, config_manager):
        """Test that adding a server with same name replaces it."""
        config_manager.add_mcp_server(name="test", command="old")
        config_manager.add_mcp_server(name="test", command="new")

        config = json.loads(config_manager.config_path.read_text())

        assert config["mcpServers"]["test"]["command"] == "new"

    def test_remove_mcp_server(self, config_manager):
        """Test removing an MCP server."""
        config_manager.add_mcp_server(name="to-remove", command="node")

        result = config_manager.remove_mcp_server("to-remove")

        assert result is True
        config = json.loads(config_manager.config_path.read_text())
        assert "to-remove" not in config["mcpServers"]

    def test_remove_mcp_server_not_found(self, config_manager):
        """Test removing a non-existent server."""
        result = config_manager.remove_mcp_server("non-existent")

        assert result is False

    def test_get_mcp_server(self, config_manager):
        """Test getting an MCP server configuration."""
        config_manager.add_mcp_server(
            name="get-test",
            command="npx",
            args=["-y", "test"],
            env={"KEY": "value"},
        )

        server = config_manager.get_mcp_server("get-test")

        assert server is not None
        assert server.name == "get-test"
        assert server.command == "npx"
        assert server.args == ["-y", "test"]
        assert server.env == {"KEY": "value"}

    def test_get_mcp_server_not_found(self, config_manager):
        """Test getting a non-existent server."""
        server = config_manager.get_mcp_server("non-existent")

        assert server is None

    def test_list_mcp_servers(self, config_manager):
        """Test listing all MCP servers."""
        config_manager.add_mcp_server(name="server1", command="cmd1")
        config_manager.add_mcp_server(name="server2", command="cmd2")

        servers = config_manager.list_mcp_servers()

        assert len(servers) == 2
        names = [s.name for s in servers]
        assert "server1" in names
        assert "server2" in names

    def test_list_mcp_servers_empty(self, config_manager):
        """Test listing servers when none exist."""
        servers = config_manager.list_mcp_servers()

        assert servers == []


class TestSlashCommandManagement:
    """Tests for slash command management."""

    def test_add_slash_command_copy(self, config_manager, tmp_path):
        """Test adding a slash command by copying."""
        source = tmp_path / "test-command.md"
        source.write_text("# Test Command\n\nDo something")

        result = config_manager.add_slash_command("test", source, symlink=False)

        assert result.exists()
        assert result.name == "test.md"
        assert result.read_text() == "# Test Command\n\nDo something"

    def test_add_slash_command_symlink(self, config_manager, tmp_path):
        """Test adding a slash command as symlink."""
        source = tmp_path / "linked-command.md"
        source.write_text("# Linked Command")

        result = config_manager.add_slash_command("linked", source, symlink=True)

        assert result.is_symlink()
        assert result.read_text() == "# Linked Command"

    def test_add_slash_command_strips_extension(self, config_manager, tmp_path):
        """Test that .md extension is stripped from name."""
        source = tmp_path / "cmd.md"
        source.write_text("content")

        result = config_manager.add_slash_command("test.md", source, symlink=False)

        assert result.name == "test.md"  # Only one .md

    def test_add_slash_command_replaces_existing(self, config_manager, tmp_path):
        """Test that adding a command replaces existing."""
        source1 = tmp_path / "v1.md"
        source1.write_text("version 1")
        source2 = tmp_path / "v2.md"
        source2.write_text("version 2")

        config_manager.add_slash_command("cmd", source1, symlink=False)
        config_manager.add_slash_command("cmd", source2, symlink=False)

        cmd_path = config_manager.COMMANDS_DIR / "cmd.md"
        assert cmd_path.read_text() == "version 2"

    def test_remove_slash_command(self, config_manager, tmp_path):
        """Test removing a slash command."""
        source = tmp_path / "remove.md"
        source.write_text("to remove")
        config_manager.add_slash_command("remove-me", source, symlink=False)

        result = config_manager.remove_slash_command("remove-me")

        assert result is True
        assert not (config_manager.COMMANDS_DIR / "remove-me.md").exists()

    def test_remove_slash_command_not_found(self, config_manager):
        """Test removing a non-existent command."""
        result = config_manager.remove_slash_command("non-existent")

        assert result is False

    def test_get_slash_command(self, config_manager, tmp_path):
        """Test getting a slash command path."""
        source = tmp_path / "get.md"
        source.write_text("content")
        config_manager.add_slash_command("get-cmd", source, symlink=False)

        path = config_manager.get_slash_command("get-cmd")

        assert path is not None
        assert path.name == "get-cmd.md"

    def test_get_slash_command_not_found(self, config_manager):
        """Test getting a non-existent command."""
        path = config_manager.get_slash_command("non-existent")

        assert path is None

    def test_list_slash_commands(self, config_manager, tmp_path):
        """Test listing all slash commands."""
        for name in ["cmd1", "cmd2", "cmd3"]:
            source = tmp_path / f"{name}.md"
            source.write_text(f"content for {name}")
            config_manager.add_slash_command(name, source, symlink=False)

        commands = config_manager.list_slash_commands()

        assert sorted(commands) == ["cmd1", "cmd2", "cmd3"]


class TestHookManagement:
    """Tests for hook management."""

    def test_add_hook(self, config_manager):
        """Test adding a hook."""
        hook = HookConfig(
            event="PostToolUse",
            command="echo 'tool used'",
        )

        config_manager.add_hook(hook)

        settings = json.loads(config_manager.SETTINGS_FILE.read_text())
        assert "hooks" in settings
        assert "PostToolUse" in settings["hooks"]
        assert settings["hooks"]["PostToolUse"]["command"] == "echo 'tool used'"

    def test_add_hook_with_timeout(self, config_manager):
        """Test adding a hook with timeout."""
        hook = HookConfig(
            event="PreToolUse",
            command="validate",
            timeout=60,
        )

        config_manager.add_hook(hook)

        settings = json.loads(config_manager.SETTINGS_FILE.read_text())
        assert settings["hooks"]["PreToolUse"]["timeout"] == 60

    def test_add_hook_replaces_existing(self, config_manager):
        """Test that adding a hook for same event replaces it."""
        hook1 = HookConfig(event="Test", command="old")
        hook2 = HookConfig(event="Test", command="new")

        config_manager.add_hook(hook1)
        config_manager.add_hook(hook2)

        settings = json.loads(config_manager.SETTINGS_FILE.read_text())
        assert settings["hooks"]["Test"]["command"] == "new"

    def test_remove_hook(self, config_manager):
        """Test removing a hook."""
        hook = HookConfig(event="ToRemove", command="test")
        config_manager.add_hook(hook)

        result = config_manager.remove_hook("ToRemove")

        assert result is True
        settings = json.loads(config_manager.SETTINGS_FILE.read_text())
        assert "ToRemove" not in settings["hooks"]

    def test_remove_hook_not_found(self, config_manager):
        """Test removing a non-existent hook."""
        result = config_manager.remove_hook("NonExistent")

        assert result is False

    def test_get_hook(self, config_manager):
        """Test getting a hook configuration."""
        hook = HookConfig(
            event="GetTest",
            command="echo 'test'",
            timeout=30,
        )
        config_manager.add_hook(hook)

        result = config_manager.get_hook("GetTest")

        assert result is not None
        assert result.event == "GetTest"
        assert result.command == "echo 'test'"
        assert result.timeout == 30

    def test_get_hook_not_found(self, config_manager):
        """Test getting a non-existent hook."""
        result = config_manager.get_hook("NonExistent")

        assert result is None

    def test_list_hooks(self, config_manager):
        """Test listing all hooks."""
        for event in ["Event1", "Event2"]:
            config_manager.add_hook(HookConfig(event=event, command=f"cmd-{event}"))

        hooks = config_manager.list_hooks()

        assert len(hooks) == 2
        events = [h.event for h in hooks]
        assert "Event1" in events
        assert "Event2" in events


class TestBackupManagement:
    """Tests for backup management."""

    def test_backup_created_on_save(self, config_manager):
        """Test that backup is created when saving config."""
        # Create initial config
        config_manager.add_mcp_server(name="initial", command="test")

        # Modify (should create backup)
        config_manager.add_mcp_server(name="second", command="test2")

        backups = list(config_manager.BACKUPS_DIR.glob("config_*.json"))
        assert len(backups) >= 1

    def test_old_backups_cleaned_up(self, config_manager):
        """Test that old backups are cleaned up."""
        # Create many backups
        for i in range(10):
            config_manager.add_mcp_server(name=f"server-{i}", command="test")

        backups = list(config_manager.BACKUPS_DIR.glob("config_*.json"))

        # Should only keep MAX_BACKUPS (5)
        assert len(backups) <= 5


class TestAssetSyncUnsync:
    """Tests for asset sync and unsync operations."""

    def test_sync_command_asset(self, config_manager, tmp_path):
        """Test syncing a command asset."""
        # Create command file
        asset_path = tmp_path / "assets" / "review-pr"
        asset_path.mkdir(parents=True)
        command_file = asset_path / "command.md"
        command_file.write_text("# Review PR\n\nReview the PR")

        config_manager.sync_asset(
            asset_type="command",
            publisher_slug="test-pub",
            slug="review-pr",
            version="1.0.0",
            local_path=asset_path,
        )

        cmd_path = config_manager.get_slash_command("review-pr")
        assert cmd_path is not None

    def test_sync_skill_asset_with_manifest(self, config_manager, tmp_path):
        """Test syncing a skill asset with manifest.json."""
        asset_path = tmp_path / "assets" / "code-review"
        asset_path.mkdir(parents=True)

        manifest = {
            "name": "code-review",
            "mcp": {
                "command": "npx",
                "args": ["-y", "@test/code-review"],
                "env": {"API_KEY": "${API_KEY}"},
            },
        }
        (asset_path / "manifest.json").write_text(json.dumps(manifest))

        config_manager.sync_asset(
            asset_type="skill",
            publisher_slug="test-pub",
            slug="code-review",
            version="1.0.0",
            local_path=asset_path,
        )

        server = config_manager.get_mcp_server("repotoire-code-review")
        assert server is not None
        assert server.command == "npx"
        assert server.args == ["-y", "@test/code-review"]

    def test_sync_hook_asset(self, config_manager, tmp_path):
        """Test syncing a hook asset."""
        asset_path = tmp_path / "assets" / "notify-hook"
        asset_path.mkdir(parents=True)

        manifest = {
            "name": "notify-hook",
            "hook": {
                "event": "PostToolUse",
                "type": "command",
                "command": "notify-send 'Done'",
                "timeout": 10,
            },
        }
        (asset_path / "manifest.json").write_text(json.dumps(manifest))

        config_manager.sync_asset(
            asset_type="hook",
            publisher_slug="test-pub",
            slug="notify-hook",
            version="1.0.0",
            local_path=asset_path,
        )

        hook = config_manager.get_hook("PostToolUse")
        assert hook is not None
        assert hook.command == "notify-send 'Done'"

    def test_unsync_command_asset(self, config_manager, tmp_path):
        """Test unsyncing a command asset."""
        # First sync
        asset_path = tmp_path / "cmd.md"
        asset_path.write_text("content")
        config_manager.add_slash_command("test-cmd", asset_path, symlink=False)

        config_manager.unsync_asset(
            asset_type="command",
            publisher_slug="pub",
            slug="test-cmd",
        )

        assert config_manager.get_slash_command("test-cmd") is None

    def test_unsync_skill_asset(self, config_manager):
        """Test unsyncing a skill asset."""
        config_manager.add_mcp_server(name="repotoire-skill", command="test")

        config_manager.unsync_asset(
            asset_type="skill",
            publisher_slug="pub",
            slug="skill",
        )

        assert config_manager.get_mcp_server("repotoire-skill") is None


class TestConfigStatus:
    """Tests for configuration status reporting."""

    def test_get_config_status(self, config_manager, tmp_path):
        """Test getting config status."""
        # Add some items
        config_manager.add_mcp_server(name="server1", command="test")
        source = tmp_path / "cmd.md"
        source.write_text("content")
        config_manager.add_slash_command("cmd1", source, symlink=False)
        config_manager.add_hook(HookConfig(event="Test", command="echo"))

        status = config_manager.get_config_status()

        assert status["mcp_servers_count"] == 1
        assert status["commands_count"] == 1
        assert status["hooks_count"] == 1
        assert "server1" in status["mcp_servers"]
        assert "cmd1" in status["commands"]
        assert "Test" in status["hooks"]

    def test_list_installed_assets(self, config_manager, tmp_path):
        """Test listing all installed assets."""
        config_manager.add_mcp_server(name="mcp1", command="test")
        source = tmp_path / "cmd.md"
        source.write_text("content")
        config_manager.add_slash_command("cmd1", source, symlink=False)

        assets = config_manager.list_installed_assets()

        assert "mcp_servers" in assets
        assert "commands" in assets
        assert "hooks" in assets
        assert "mcp1" in assets["mcp_servers"]
        assert "cmd1" in assets["commands"]

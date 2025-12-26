"""Claude Desktop and Claude Code configuration management.

This module provides the ClaudeConfigManager class for managing Claude Desktop/Code
configurations when installing marketplace assets. It handles:

- Locating Claude config files across platforms (macOS, Linux, Windows)
- Adding/removing MCP servers for skills
- Managing slash commands via symlinks
- Configuring hooks in settings.json
- Creating backups before modifications

Usage:
    from repotoire.marketplace import ClaudeConfigManager

    manager = ClaudeConfigManager()

    # Add an MCP server for a skill
    manager.add_mcp_server(
        name="my-skill",
        command="npx",
        args=["-y", "@publisher/skill-server"],
        env={"API_KEY": "secret"},
    )

    # Add a slash command
    manager.add_slash_command("review-pr", Path("/path/to/command.md"))

    # List installed assets
    installed = manager.list_installed_assets()
"""

from __future__ import annotations

import json
import os
import platform
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Maximum number of backups to keep
MAX_BACKUPS = 5


class ClaudeConfigError(Exception):
    """Base exception for Claude configuration errors."""

    pass


class ConfigBackupError(ClaudeConfigError):
    """Error creating or restoring config backup."""

    pass


class ConfigNotFoundError(ClaudeConfigError):
    """Claude config file not found."""

    pass


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server.

    Attributes:
        name: Server name (used as key in config).
        command: Command to run the server.
        args: Arguments to pass to the command.
        env: Environment variables for the server.
        enabled: Whether the server is enabled.
    """

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        config: dict[str, Any] = {
            "command": self.command,
        }

        if self.args:
            config["args"] = self.args

        if self.env:
            config["env"] = self.env

        return config


@dataclass
class HookConfig:
    """Configuration for a Claude hook.

    Attributes:
        event: Event name (e.g., "PreToolUse", "PostToolUse").
        type: Hook type ("command" for shell commands).
        command: Shell command to execute.
        timeout: Timeout in seconds (optional).
    """

    event: str
    type: str = "command"
    command: str = ""
    timeout: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        config: dict[str, Any] = {
            "type": self.type,
        }

        if self.type == "command":
            config["command"] = self.command

        if self.timeout is not None:
            config["timeout"] = self.timeout

        return config


class ClaudeConfigManager:
    """Manage Claude Desktop/Code configuration for marketplace assets.

    This class provides methods to:
    - Add/remove MCP servers (for skills)
    - Add/remove slash commands
    - Add/remove hooks
    - List installed marketplace assets

    Configuration is stored in:
    - ~/.claude.json (main config with MCP servers)
    - ~/.claude/commands/ (slash commands as .md files)
    - ~/.claude/settings.json (hooks configuration)
    """

    # Config file locations by platform
    CLAUDE_CONFIG_PATHS = {
        "Darwin": [
            Path.home() / ".claude.json",
            Path.home() / ".config" / "claude" / "config.json",
            Path.home() / "Library" / "Application Support" / "Claude" / "config.json",
        ],
        "Linux": [
            Path.home() / ".claude.json",
            Path.home() / ".config" / "claude" / "config.json",
        ],
        "Windows": [
            Path.home() / ".claude.json",
            Path.home() / "AppData" / "Roaming" / "Claude" / "config.json",
        ],
    }

    # Default config locations
    CLAUDE_DIR = Path.home() / ".claude"
    COMMANDS_DIR = CLAUDE_DIR / "commands"
    SETTINGS_FILE = CLAUDE_DIR / "settings.json"
    BACKUPS_DIR = CLAUDE_DIR / "backups"

    def __init__(self, config_path: Path | None = None):
        """Initialize the config manager.

        Args:
            config_path: Override the auto-detected config path.
        """
        self._config_path = config_path
        self._config_cache: dict[str, Any] | None = None
        self._settings_cache: dict[str, Any] | None = None

    @property
    def config_path(self) -> Path:
        """Get the Claude config file path.

        Returns:
            Path to the config file.

        Raises:
            ConfigNotFoundError: If no config file is found.
        """
        if self._config_path:
            return self._config_path

        path = self._find_config_path()
        if path:
            self._config_path = path
            return path

        # Default to ~/.claude.json (will be created)
        return Path.home() / ".claude.json"

    def _find_config_path(self) -> Path | None:
        """Find the Claude config file.

        Returns:
            Path to config file or None if not found.
        """
        system = platform.system()
        paths = self.CLAUDE_CONFIG_PATHS.get(system, self.CLAUDE_CONFIG_PATHS["Linux"])

        for path in paths:
            if path.exists():
                logger.debug(f"Found Claude config at {path}")
                return path

        return None

    def _ensure_directories(self) -> None:
        """Ensure required directories exist."""
        self.CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
        self.COMMANDS_DIR.mkdir(parents=True, exist_ok=True)
        self.BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

    def _create_backup(self, file_path: Path, prefix: str = "config") -> Path:
        """Create a backup of a config file.

        Args:
            file_path: Path to file to backup.
            prefix: Prefix for backup filename.

        Returns:
            Path to backup file.

        Raises:
            ConfigBackupError: If backup fails.
        """
        if not file_path.exists():
            return file_path  # Nothing to backup

        self._ensure_directories()

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{prefix}_{timestamp}.json"
            backup_path = self.BACKUPS_DIR / backup_name

            shutil.copy2(file_path, backup_path)
            logger.debug(f"Created backup at {backup_path}")

            # Clean up old backups
            self._cleanup_old_backups(prefix)

            return backup_path

        except OSError as e:
            raise ConfigBackupError(f"Failed to create backup: {e}") from e

    def _cleanup_old_backups(self, prefix: str) -> None:
        """Remove old backups, keeping only MAX_BACKUPS most recent.

        Args:
            prefix: Backup filename prefix to filter.
        """
        try:
            backups = sorted(
                [f for f in self.BACKUPS_DIR.iterdir() if f.name.startswith(prefix)],
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )

            for backup in backups[MAX_BACKUPS:]:
                backup.unlink()
                logger.debug(f"Removed old backup: {backup}")

        except OSError as e:
            logger.warning(f"Failed to cleanup backups: {e}")

    def _load_config(self) -> dict[str, Any]:
        """Load the Claude config file.

        Returns:
            Parsed config dictionary.
        """
        if self._config_cache is not None:
            return self._config_cache

        config_path = self.config_path

        if not config_path.exists():
            self._config_cache = {}
            return self._config_cache

        try:
            with open(config_path, "r") as f:
                self._config_cache = json.load(f)
                return self._config_cache

        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load config: {e}")
            self._config_cache = {}
            return self._config_cache

    def _save_config(self, config: dict[str, Any], backup: bool = True) -> None:
        """Save the Claude config file.

        Args:
            config: Config dictionary to save.
            backup: Whether to create a backup first.

        Raises:
            ClaudeConfigError: If save fails.
        """
        config_path = self.config_path
        self._ensure_directories()

        if backup and config_path.exists():
            self._create_backup(config_path, "config")

        try:
            # Ensure parent directory exists
            config_path.parent.mkdir(parents=True, exist_ok=True)

            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)

            # Update cache
            self._config_cache = config

            logger.debug(f"Saved config to {config_path}")

        except OSError as e:
            raise ClaudeConfigError(f"Failed to save config: {e}") from e

    def _load_settings(self) -> dict[str, Any]:
        """Load the Claude settings file (for hooks).

        Returns:
            Parsed settings dictionary.
        """
        if self._settings_cache is not None:
            return self._settings_cache

        if not self.SETTINGS_FILE.exists():
            self._settings_cache = {}
            return self._settings_cache

        try:
            with open(self.SETTINGS_FILE, "r") as f:
                self._settings_cache = json.load(f)
                return self._settings_cache

        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load settings: {e}")
            self._settings_cache = {}
            return self._settings_cache

    def _save_settings(self, settings: dict[str, Any], backup: bool = True) -> None:
        """Save the Claude settings file.

        Args:
            settings: Settings dictionary to save.
            backup: Whether to create a backup first.

        Raises:
            ClaudeConfigError: If save fails.
        """
        self._ensure_directories()

        if backup and self.SETTINGS_FILE.exists():
            self._create_backup(self.SETTINGS_FILE, "settings")

        try:
            with open(self.SETTINGS_FILE, "w") as f:
                json.dump(settings, f, indent=2)

            # Update cache
            self._settings_cache = settings

            logger.debug(f"Saved settings to {self.SETTINGS_FILE}")

        except OSError as e:
            raise ClaudeConfigError(f"Failed to save settings: {e}") from e

    # =========================================================================
    # MCP Server Management
    # =========================================================================

    def add_mcp_server(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        """Add an MCP server to the Claude config.

        Args:
            name: Server name (used as key in mcpServers).
            command: Command to run the server.
            args: Arguments to pass to the command.
            env: Environment variables for the server.

        Example:
            manager.add_mcp_server(
                name="repotoire-skill",
                command="npx",
                args=["-y", "@repotoire/skill-server"],
                env={"REPOTOIRE_API_KEY": "${REPOTOIRE_API_KEY}"},
            )
        """
        config = self._load_config()

        # Ensure mcpServers exists
        if "mcpServers" not in config:
            config["mcpServers"] = {}

        # Build server config
        server_config: dict[str, Any] = {
            "command": command,
        }

        if args:
            server_config["args"] = args

        if env:
            server_config["env"] = env

        # Add to config
        config["mcpServers"][name] = server_config

        self._save_config(config)
        logger.info(f"Added MCP server: {name}")

    def remove_mcp_server(self, name: str) -> bool:
        """Remove an MCP server from the Claude config.

        Args:
            name: Server name to remove.

        Returns:
            True if server was removed, False if not found.
        """
        config = self._load_config()

        if "mcpServers" not in config:
            return False

        if name not in config["mcpServers"]:
            return False

        del config["mcpServers"][name]

        self._save_config(config)
        logger.info(f"Removed MCP server: {name}")
        return True

    def get_mcp_server(self, name: str) -> MCPServerConfig | None:
        """Get an MCP server configuration.

        Args:
            name: Server name.

        Returns:
            Server configuration or None if not found.
        """
        config = self._load_config()
        servers = config.get("mcpServers", {})

        if name not in servers:
            return None

        server = servers[name]
        return MCPServerConfig(
            name=name,
            command=server.get("command", ""),
            args=server.get("args", []),
            env=server.get("env", {}),
        )

    def list_mcp_servers(self) -> list[MCPServerConfig]:
        """List all MCP servers in the config.

        Returns:
            List of MCP server configurations.
        """
        config = self._load_config()
        servers = config.get("mcpServers", {})

        return [
            MCPServerConfig(
                name=name,
                command=server.get("command", ""),
                args=server.get("args", []),
                env=server.get("env", {}),
            )
            for name, server in servers.items()
        ]

    # =========================================================================
    # Slash Command Management
    # =========================================================================

    def add_slash_command(
        self,
        name: str,
        source_path: Path,
        symlink: bool = True,
    ) -> Path:
        """Add a slash command to Claude.

        Commands are stored in ~/.claude/commands/ as .md files.

        Args:
            name: Command name (without slash).
            source_path: Path to the command file.
            symlink: If True, create symlink; if False, copy file.

        Returns:
            Path to the installed command file.

        Raises:
            ClaudeConfigError: If installation fails.
        """
        self._ensure_directories()

        # Ensure name doesn't have .md extension
        if name.endswith(".md"):
            name = name[:-3]

        target_path = self.COMMANDS_DIR / f"{name}.md"

        try:
            # Remove existing file/symlink
            if target_path.exists() or target_path.is_symlink():
                target_path.unlink()

            if symlink:
                # Create symlink
                target_path.symlink_to(source_path.resolve())
                logger.debug(f"Created symlink: {target_path} -> {source_path}")
            else:
                # Copy file
                shutil.copy2(source_path, target_path)
                logger.debug(f"Copied command to: {target_path}")

            logger.info(f"Added slash command: /{name}")
            return target_path

        except OSError as e:
            raise ClaudeConfigError(f"Failed to add command: {e}") from e

    def remove_slash_command(self, name: str) -> bool:
        """Remove a slash command from Claude.

        Args:
            name: Command name (without slash).

        Returns:
            True if command was removed, False if not found.
        """
        # Ensure name doesn't have .md extension
        if name.endswith(".md"):
            name = name[:-3]

        target_path = self.COMMANDS_DIR / f"{name}.md"

        if not target_path.exists() and not target_path.is_symlink():
            return False

        try:
            target_path.unlink()
            logger.info(f"Removed slash command: /{name}")
            return True

        except OSError as e:
            logger.warning(f"Failed to remove command: {e}")
            return False

    def get_slash_command(self, name: str) -> Path | None:
        """Get the path to a slash command.

        Args:
            name: Command name (without slash).

        Returns:
            Path to command file or None if not found.
        """
        if name.endswith(".md"):
            name = name[:-3]

        target_path = self.COMMANDS_DIR / f"{name}.md"

        if target_path.exists() or target_path.is_symlink():
            return target_path

        return None

    def list_slash_commands(self) -> list[str]:
        """List all slash commands.

        Returns:
            List of command names (without slash).
        """
        if not self.COMMANDS_DIR.exists():
            return []

        commands = []
        for path in self.COMMANDS_DIR.iterdir():
            if path.suffix == ".md":
                commands.append(path.stem)

        return sorted(commands)

    # =========================================================================
    # Hook Management
    # =========================================================================

    def add_hook(self, hook: HookConfig) -> None:
        """Add a hook to Claude settings.

        Args:
            hook: Hook configuration to add.

        Example:
            manager.add_hook(HookConfig(
                event="PostToolUse",
                type="command",
                command="notify-send 'Tool used: {{tool_name}}'",
            ))
        """
        settings = self._load_settings()

        # Ensure hooks exists
        if "hooks" not in settings:
            settings["hooks"] = {}

        # Add or replace hook for this event
        settings["hooks"][hook.event] = hook.to_dict()

        self._save_settings(settings)
        logger.info(f"Added hook for event: {hook.event}")

    def remove_hook(self, event: str) -> bool:
        """Remove a hook from Claude settings.

        Args:
            event: Event name to remove hook for.

        Returns:
            True if hook was removed, False if not found.
        """
        settings = self._load_settings()

        if "hooks" not in settings:
            return False

        if event not in settings["hooks"]:
            return False

        del settings["hooks"][event]

        self._save_settings(settings)
        logger.info(f"Removed hook for event: {event}")
        return True

    def get_hook(self, event: str) -> HookConfig | None:
        """Get a hook configuration.

        Args:
            event: Event name.

        Returns:
            Hook configuration or None if not found.
        """
        settings = self._load_settings()
        hooks = settings.get("hooks", {})

        if event not in hooks:
            return None

        hook_data = hooks[event]
        return HookConfig(
            event=event,
            type=hook_data.get("type", "command"),
            command=hook_data.get("command", ""),
            timeout=hook_data.get("timeout"),
        )

    def list_hooks(self) -> list[HookConfig]:
        """List all hooks.

        Returns:
            List of hook configurations.
        """
        settings = self._load_settings()
        hooks = settings.get("hooks", {})

        return [
            HookConfig(
                event=event,
                type=data.get("type", "command"),
                command=data.get("command", ""),
                timeout=data.get("timeout"),
            )
            for event, data in hooks.items()
        ]

    # =========================================================================
    # Asset Management
    # =========================================================================

    def list_installed_assets(self) -> dict[str, list[str]]:
        """List all marketplace assets in Claude config.

        Returns:
            Dictionary with keys: 'mcp_servers', 'commands', 'hooks'.
        """
        return {
            "mcp_servers": [s.name for s in self.list_mcp_servers()],
            "commands": self.list_slash_commands(),
            "hooks": [h.event for h in self.list_hooks()],
        }

    def sync_asset(
        self,
        asset_type: str,
        publisher_slug: str,
        slug: str,
        version: str,
        local_path: Path,
    ) -> None:
        """Sync a marketplace asset to Claude config.

        This is called after extracting an asset to ensure it's configured
        in Claude Desktop/Code.

        Args:
            asset_type: Type of asset (command, skill, hook, etc.).
            publisher_slug: Publisher's slug.
            slug: Asset slug.
            version: Asset version.
            local_path: Path where asset is installed locally.
        """
        asset_name = f"@{publisher_slug}/{slug}"

        if asset_type == "command":
            # Add slash command
            command_file = local_path if local_path.is_file() else local_path / "command.md"
            if command_file.exists():
                self.add_slash_command(slug, command_file)

        elif asset_type == "skill":
            # Add MCP server
            # Look for manifest.json or package.json to determine command
            manifest_path = local_path / "manifest.json"
            package_path = local_path / "package.json"

            if manifest_path.exists():
                with open(manifest_path, "r") as f:
                    manifest = json.load(f)

                command = manifest.get("mcp", {}).get("command", "")
                args = manifest.get("mcp", {}).get("args", [])
                env = manifest.get("mcp", {}).get("env", {})

                if command:
                    self.add_mcp_server(
                        name=f"repotoire-{slug}",
                        command=command,
                        args=args,
                        env=env,
                    )

            elif package_path.exists():
                # npm package - use npx
                with open(package_path, "r") as f:
                    package = json.load(f)

                package_name = package.get("name", asset_name)
                self.add_mcp_server(
                    name=f"repotoire-{slug}",
                    command="npx",
                    args=["-y", package_name],
                )

        elif asset_type == "hook":
            # Add hook from manifest
            manifest_path = local_path / "manifest.json"
            if manifest_path.exists():
                with open(manifest_path, "r") as f:
                    manifest = json.load(f)

                hook_config = manifest.get("hook", {})
                if hook_config:
                    self.add_hook(HookConfig(
                        event=hook_config.get("event", "PostToolUse"),
                        type=hook_config.get("type", "command"),
                        command=hook_config.get("command", ""),
                        timeout=hook_config.get("timeout"),
                    ))

        logger.info(f"Synced {asset_type} {asset_name} v{version} to Claude config")

    def unsync_asset(
        self,
        asset_type: str,
        publisher_slug: str,
        slug: str,
    ) -> None:
        """Remove a marketplace asset from Claude config.

        Args:
            asset_type: Type of asset (command, skill, hook, etc.).
            publisher_slug: Publisher's slug.
            slug: Asset slug.
        """
        if asset_type == "command":
            self.remove_slash_command(slug)

        elif asset_type == "skill":
            self.remove_mcp_server(f"repotoire-{slug}")

        elif asset_type == "hook":
            # We'd need to know the event name to remove the hook
            # For now, we skip this as hooks might be shared
            logger.debug(f"Hook uninstall skipped for {slug}")

        logger.info(f"Removed {asset_type} @{publisher_slug}/{slug} from Claude config")

    def get_config_status(self) -> dict[str, Any]:
        """Get the current Claude configuration status.

        Returns:
            Dictionary with configuration details.
        """
        config = self._load_config()
        settings = self._load_settings()

        mcp_servers = config.get("mcpServers", {})
        hooks = settings.get("hooks", {})
        commands = self.list_slash_commands()

        return {
            "config_path": str(self.config_path),
            "settings_path": str(self.SETTINGS_FILE),
            "commands_dir": str(self.COMMANDS_DIR),
            "mcp_servers_count": len(mcp_servers),
            "commands_count": len(commands),
            "hooks_count": len(hooks),
            "mcp_servers": list(mcp_servers.keys()),
            "commands": commands,
            "hooks": list(hooks.keys()),
        }

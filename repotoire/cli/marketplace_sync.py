"""Marketplace local sync and extraction logic.

This module handles:
- Extracting marketplace asset tarballs to local directories
- Managing the local manifest of installed assets
- Syncing local state with remote marketplace
"""

from __future__ import annotations

import gzip
import io
import json
import os
import shutil
import tarfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Default directories
MARKETPLACE_DIR = Path.home() / ".claude" / "marketplace"
MANIFEST_FILE = MARKETPLACE_DIR / "manifest.json"
COMMANDS_DIR = Path.home() / ".claude" / "commands"

# Asset type to directory mapping
ASSET_DIRECTORIES = {
    "command": COMMANDS_DIR,
    "skill": MARKETPLACE_DIR / "skills",
    "style": MARKETPLACE_DIR / "styles",
    "hook": MARKETPLACE_DIR / "hooks",
    "prompt": MARKETPLACE_DIR / "prompts",
}

# Manifest schema version
MANIFEST_VERSION = 1


@dataclass
class InstalledAsset:
    """Information about a locally installed asset."""

    version: str
    asset_type: str
    pinned: bool = False
    installed_at: str = ""
    publisher_slug: str = ""
    name: str = ""
    local_path: str = ""

    def __post_init__(self):
        if not self.installed_at:
            self.installed_at = datetime.now(timezone.utc).isoformat()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InstalledAsset":
        """Create from dictionary."""
        return cls(
            version=data.get("version", ""),
            asset_type=data.get("type", data.get("asset_type", "")),
            pinned=data.get("pinned", False),
            installed_at=data.get("installed_at", ""),
            publisher_slug=data.get("publisher_slug", ""),
            name=data.get("name", ""),
            local_path=data.get("local_path", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for manifest storage."""
        return {
            "version": self.version,
            "type": self.asset_type,
            "pinned": self.pinned,
            "installed_at": self.installed_at,
            "publisher_slug": self.publisher_slug,
            "name": self.name,
            "local_path": self.local_path,
        }


@dataclass
class LocalManifest:
    """Local manifest of installed marketplace assets."""

    version: int = MANIFEST_VERSION
    synced_at: str = ""
    assets: dict[str, InstalledAsset] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "LocalManifest":
        """Load manifest from disk.

        Args:
            path: Path to manifest file. Defaults to MANIFEST_FILE.

        Returns:
            Loaded manifest or empty manifest if file doesn't exist.
        """
        path = path or MANIFEST_FILE

        if not path.exists():
            return cls()

        try:
            with open(path, "r") as f:
                data = json.load(f)

            assets = {}
            for key, value in data.get("assets", {}).items():
                assets[key] = InstalledAsset.from_dict(value)

            return cls(
                version=data.get("version", MANIFEST_VERSION),
                synced_at=data.get("synced_at", ""),
                assets=assets,
            )

        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load manifest: {e}")
            return cls()

    def save(self, path: Path | None = None) -> None:
        """Save manifest to disk.

        Args:
            path: Path to manifest file. Defaults to MANIFEST_FILE.
        """
        path = path or MANIFEST_FILE

        # Ensure directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "version": self.version,
            "synced_at": self.synced_at,
            "assets": {key: asset.to_dict() for key, asset in self.assets.items()},
        }

        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def add_asset(
        self,
        full_name: str,
        version: str,
        asset_type: str,
        publisher_slug: str = "",
        name: str = "",
        local_path: str = "",
        pinned: bool = False,
    ) -> None:
        """Add or update an installed asset.

        Args:
            full_name: Full asset name (e.g., "@publisher/slug").
            version: Installed version.
            asset_type: Type of asset.
            publisher_slug: Publisher's slug.
            name: Asset display name.
            local_path: Path where asset is installed.
            pinned: Whether version is pinned.
        """
        self.assets[full_name] = InstalledAsset(
            version=version,
            asset_type=asset_type,
            pinned=pinned,
            publisher_slug=publisher_slug,
            name=name,
            local_path=local_path,
        )

    def remove_asset(self, full_name: str) -> bool:
        """Remove an installed asset.

        Args:
            full_name: Full asset name.

        Returns:
            True if asset was removed, False if not found.
        """
        if full_name in self.assets:
            del self.assets[full_name]
            return True
        return False

    def get_asset(self, full_name: str) -> InstalledAsset | None:
        """Get an installed asset.

        Args:
            full_name: Full asset name.

        Returns:
            Installed asset or None.
        """
        return self.assets.get(full_name)

    def update_sync_time(self) -> None:
        """Update the last sync timestamp."""
        self.synced_at = datetime.now(timezone.utc).isoformat()


def get_local_manifest() -> LocalManifest:
    """Get the current local manifest.

    Returns:
        Local manifest instance.
    """
    return LocalManifest.load()


def get_asset_path(
    publisher_slug: str,
    slug: str,
    asset_type: str,
) -> Path:
    """Get the local path where an asset is/will be installed.

    Args:
        publisher_slug: Publisher's slug.
        slug: Asset's slug.
        asset_type: Type of asset.

    Returns:
        Path to the asset's local directory/file.
    """
    base_dir = ASSET_DIRECTORIES.get(asset_type, MARKETPLACE_DIR / asset_type)

    if asset_type == "command":
        # Commands go directly in commands dir as .md files
        return base_dir / f"{slug}.md"

    elif asset_type == "skill":
        # Skills get their own directory: skills/@publisher/slug/
        return base_dir / f"@{publisher_slug}" / slug

    else:
        # Other types: type_dir/@publisher/slug/
        return base_dir / f"@{publisher_slug}" / slug


def extract_asset(
    publisher_slug: str,
    slug: str,
    asset_type: str,
    content: bytes,
) -> Path:
    """Extract a tarball to the appropriate local directory.

    Args:
        publisher_slug: Publisher's slug.
        slug: Asset's slug.
        asset_type: Type of asset.
        content: Gzipped tarball content.

    Returns:
        Path where the asset was extracted.

    Raises:
        ValueError: If extraction fails.
    """
    target_path = get_asset_path(publisher_slug, slug, asset_type)

    try:
        # Decompress gzip
        with gzip.GzipFile(fileobj=io.BytesIO(content)) as gz:
            tar_data = gz.read()

        # Extract tar
        files = {}
        with tarfile.open(fileobj=io.BytesIO(tar_data), mode="r:") as tar:
            for member in tar.getmembers():
                if member.isfile():
                    f = tar.extractfile(member)
                    if f:
                        # Sanitize filename (no path traversal)
                        safe_name = member.name.replace("..", "").lstrip("/")
                        if safe_name:
                            files[safe_name] = f.read()

        if not files:
            raise ValueError("Empty tarball or no files extracted")

        # Handle command type specially - extract just the command.md content
        if asset_type == "command":
            if "command.md" not in files:
                raise ValueError("Command asset missing command.md file")

            # Ensure parent directory exists
            target_path.parent.mkdir(parents=True, exist_ok=True)

            # Write command file
            with open(target_path, "wb") as f:
                f.write(files["command.md"])

            logger.info(f"Extracted command to {target_path}")
            return target_path

        # For other types, extract all files to directory
        if target_path.exists():
            shutil.rmtree(target_path)

        target_path.mkdir(parents=True, exist_ok=True)

        for filename, file_content in files.items():
            file_path = target_path / filename

            # Create subdirectories if needed
            file_path.parent.mkdir(parents=True, exist_ok=True)

            with open(file_path, "wb") as f:
                f.write(file_content)

        logger.info(f"Extracted {len(files)} files to {target_path}")
        return target_path

    except Exception as e:
        logger.exception(f"Failed to extract asset @{publisher_slug}/{slug}")
        raise ValueError(f"Failed to extract asset: {e}") from e


def remove_asset_files(
    publisher_slug: str,
    slug: str,
    asset_type: str,
) -> bool:
    """Remove locally installed asset files.

    Args:
        publisher_slug: Publisher's slug.
        slug: Asset's slug.
        asset_type: Type of asset.

    Returns:
        True if files were removed, False if not found.
    """
    target_path = get_asset_path(publisher_slug, slug, asset_type)

    if not target_path.exists():
        return False

    try:
        if target_path.is_file():
            target_path.unlink()
        else:
            shutil.rmtree(target_path)

        logger.info(f"Removed asset files at {target_path}")
        return True

    except OSError as e:
        logger.warning(f"Failed to remove asset files: {e}")
        return False


def update_manifest(
    full_name: str,
    version: str,
    asset_type: str,
    publisher_slug: str = "",
    name: str = "",
    local_path: Path | str = "",
    pinned: bool = False,
) -> None:
    """Update the local manifest with an installed asset.

    Args:
        full_name: Full asset name (e.g., "@publisher/slug").
        version: Installed version.
        asset_type: Type of asset.
        publisher_slug: Publisher's slug.
        name: Asset display name.
        local_path: Path where asset is installed.
        pinned: Whether version is pinned.
    """
    manifest = get_local_manifest()
    manifest.add_asset(
        full_name=full_name,
        version=version,
        asset_type=asset_type,
        publisher_slug=publisher_slug,
        name=name,
        local_path=str(local_path),
        pinned=pinned,
    )
    manifest.save()


def remove_from_manifest(full_name: str) -> bool:
    """Remove an asset from the local manifest.

    Args:
        full_name: Full asset name.

    Returns:
        True if asset was removed, False if not found.
    """
    manifest = get_local_manifest()
    removed = manifest.remove_asset(full_name)
    if removed:
        manifest.save()
    return removed


def ensure_directories_exist() -> None:
    """Ensure all marketplace directories exist."""
    MARKETPLACE_DIR.mkdir(parents=True, exist_ok=True)
    COMMANDS_DIR.mkdir(parents=True, exist_ok=True)

    for path in ASSET_DIRECTORIES.values():
        path.mkdir(parents=True, exist_ok=True)


def get_sync_status() -> dict[str, Any]:
    """Get the current sync status.

    Returns:
        Dictionary with sync status information.
    """
    manifest = get_local_manifest()

    return {
        "installed_count": len(manifest.assets),
        "last_synced": manifest.synced_at,
        "assets": {
            name: {
                "version": asset.version,
                "type": asset.asset_type,
                "pinned": asset.pinned,
            }
            for name, asset in manifest.assets.items()
        },
    }


@dataclass
class SyncResult:
    """Result of a sync operation."""

    updated: list[str]  # Assets that were updated
    unchanged: list[str]  # Assets that were already up-to-date
    failed: list[tuple[str, str]]  # (asset_name, error_message)
    removed: list[str]  # Assets that were removed

    @property
    def total_synced(self) -> int:
        """Total number of assets synced."""
        return len(self.updated) + len(self.unchanged)

    @property
    def has_failures(self) -> bool:
        """Whether any syncs failed."""
        return len(self.failed) > 0


def check_for_updates(
    installed: dict[str, InstalledAsset],
    remote_assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Check which installed assets need updates.

    Args:
        installed: Currently installed assets.
        remote_assets: Assets from remote sync response.

    Returns:
        List of assets that need updating.
    """
    updates_needed = []

    for remote in remote_assets:
        full_name = f"@{remote.get('publisher_slug', '')}/{remote.get('slug', '')}"
        local = installed.get(full_name)

        if local is None:
            # Not installed locally, skip
            continue

        if local.pinned:
            # Version is pinned, don't update
            continue

        remote_version = remote.get("latest_version", "")
        if remote_version and remote_version != local.version:
            updates_needed.append(remote)

    return updates_needed

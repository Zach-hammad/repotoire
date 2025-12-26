"""Asset packaging for marketplace.

This module packages different asset types into gzipped tarballs for storage.
Each tarball is self-contained and extractable.
"""

from __future__ import annotations

import gzip
import io
import json
import tarfile
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from repotoire.db.models.marketplace import AssetType
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class PackageResult:
    """Result of packaging an asset."""

    data: bytes
    checksum: str  # SHA-256 hex digest
    size: int  # Size in bytes
    files: list[str]  # List of files in the tarball


class AssetPackagingError(Exception):
    """Raised when asset packaging fails."""

    pass


class AssetPackager:
    """Packages marketplace assets into tarballs."""

    def package(self, asset_type: AssetType | str, content: dict[str, Any]) -> PackageResult:
        """Package asset content into a gzipped tarball.

        Args:
            asset_type: Type of asset (command, skill, style, hook, prompt).
            content: The asset content dictionary.

        Returns:
            PackageResult with tarball data, checksum, and size.

        Raises:
            AssetPackagingError: If packaging fails.
        """
        # Normalize asset type
        if isinstance(asset_type, str):
            try:
                asset_type = AssetType(asset_type.lower())
            except ValueError:
                raise AssetPackagingError(f"Invalid asset type: {asset_type}")

        try:
            if asset_type == AssetType.COMMAND:
                return self._package_command(content)
            elif asset_type == AssetType.SKILL:
                return self._package_skill(content)
            elif asset_type == AssetType.STYLE:
                return self._package_style(content)
            elif asset_type == AssetType.HOOK:
                return self._package_hook(content)
            elif asset_type == AssetType.PROMPT:
                return self._package_prompt(content)
            else:
                raise AssetPackagingError(f"Unsupported asset type: {asset_type}")
        except AssetPackagingError:
            raise
        except Exception as e:
            logger.exception(f"Failed to package {asset_type} asset")
            raise AssetPackagingError(f"Packaging failed: {e}") from e

    def unpackage(self, data: bytes) -> dict[str, bytes]:
        """Extract files from a packaged tarball.

        Args:
            data: Gzipped tarball data.

        Returns:
            Dictionary mapping filenames to their contents.
        """
        files = {}
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
                with tarfile.open(fileobj=gz, mode="r:") as tar:
                    for member in tar.getmembers():
                        if member.isfile():
                            f = tar.extractfile(member)
                            if f:
                                files[member.name] = f.read()
        except Exception as e:
            logger.exception("Failed to unpackage tarball")
            raise AssetPackagingError(f"Unpackaging failed: {e}") from e

        return files

    def _create_tarball(self, files: dict[str, str | bytes]) -> PackageResult:
        """Create a gzipped tarball from files.

        Args:
            files: Dictionary mapping filenames to content.

        Returns:
            PackageResult with tarball data.
        """
        # Create tar in memory
        tar_buffer = io.BytesIO()
        file_list = []

        with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
            for filename, content in files.items():
                # Convert string to bytes
                if isinstance(content, str):
                    content_bytes = content.encode("utf-8")
                else:
                    content_bytes = content

                # Create tarinfo
                info = tarfile.TarInfo(name=filename)
                info.size = len(content_bytes)

                # Add to tar
                tar.addfile(info, io.BytesIO(content_bytes))
                file_list.append(filename)

        # Get tar data
        tar_data = tar_buffer.getvalue()

        # Gzip the tar
        gz_buffer = io.BytesIO()
        with gzip.GzipFile(fileobj=gz_buffer, mode="wb") as gz:
            gz.write(tar_data)

        gz_data = gz_buffer.getvalue()

        # Calculate checksum
        checksum = sha256(gz_data).hexdigest()

        return PackageResult(
            data=gz_data,
            checksum=checksum,
            size=len(gz_data),
            files=file_list,
        )

    def _package_command(self, content: dict[str, Any]) -> PackageResult:
        """Package a command asset.

        Creates:
        - command.md: The command prompt
        - meta.json: Description and arguments
        """
        files = {}

        # Main command file
        prompt = content.get("prompt", "")
        files["command.md"] = prompt

        # Metadata
        meta = {
            "description": content.get("description", ""),
            "arguments": content.get("arguments", []),
        }
        files["meta.json"] = json.dumps(meta, indent=2)

        return self._create_tarball(files)

    def _package_skill(self, content: dict[str, Any]) -> PackageResult:
        """Package a skill asset.

        Creates:
        - skill.json: Main skill configuration
        - server.py: Server code (if provided)
        - requirements.txt: Dependencies (if provided)
        """
        files = {}

        # Main skill config (exclude server code for separate file)
        skill_config = {
            "name": content.get("name", ""),
            "description": content.get("description", ""),
            "tools": content.get("tools", []),
            "server": content.get("server", {}),
        }
        files["skill.json"] = json.dumps(skill_config, indent=2)

        # Server code (if provided as separate field)
        server_code = content.get("server_code")
        if server_code:
            files["server.py"] = server_code

        # Requirements (if provided)
        requirements = content.get("requirements")
        if requirements:
            if isinstance(requirements, list):
                files["requirements.txt"] = "\n".join(requirements)
            else:
                files["requirements.txt"] = requirements

        # Additional files (if provided)
        additional_files = content.get("files", {})
        for filename, file_content in additional_files.items():
            # Sanitize filename (no path traversal)
            safe_name = filename.replace("..", "").lstrip("/")
            if safe_name and not safe_name.startswith("."):
                files[safe_name] = file_content

        return self._create_tarball(files)

    def _package_style(self, content: dict[str, Any]) -> PackageResult:
        """Package a style asset.

        Creates:
        - style.md: CLAUDE.md style instructions
        - examples.json: Usage examples (if provided)
        """
        files = {}

        # Main style instructions
        instructions = content.get("instructions", "")
        files["style.md"] = instructions

        # Examples (if provided)
        examples = content.get("examples")
        if examples:
            files["examples.json"] = json.dumps(examples, indent=2)

        return self._create_tarball(files)

    def _package_hook(self, content: dict[str, Any]) -> PackageResult:
        """Package a hook asset.

        Creates:
        - hook.json: Hook configuration
        """
        files = {}

        # Hook config
        hook_config = {
            "event": content.get("event", ""),
            "matcher": content.get("matcher"),
            "command": content.get("command", ""),
        }

        # Include any additional config fields
        for key in content:
            if key not in hook_config:
                hook_config[key] = content[key]

        files["hook.json"] = json.dumps(hook_config, indent=2)

        return self._create_tarball(files)

    def _package_prompt(self, content: dict[str, Any]) -> PackageResult:
        """Package a prompt asset.

        Creates:
        - prompt.md: The prompt template
        - variables.json: Variable definitions
        """
        files = {}

        # Main prompt template
        template = content.get("template", "")
        files["prompt.md"] = template

        # Variables
        variables = content.get("variables", [])
        meta = {
            "description": content.get("description", ""),
            "variables": variables,
        }
        files["variables.json"] = json.dumps(meta, indent=2)

        return self._create_tarball(files)

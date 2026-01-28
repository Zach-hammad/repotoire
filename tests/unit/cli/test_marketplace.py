"""Unit tests for marketplace CLI commands and sync logic."""

import gzip
import io
import json
import sys
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

# Import the click group directly
from repotoire.cli.marketplace import marketplace

# Get the actual module (not the click group) for patching
# Due to namespace collision, we need to access via sys.modules
_marketplace_module = sys.modules["repotoire.cli.marketplace"]

from repotoire.cli.marketplace_sync import (
    InstalledAsset,
    LocalManifest,
    MANIFEST_VERSION,
    check_for_updates,
    extract_asset,
    get_asset_path,
    get_local_manifest,
    remove_asset_files,
    remove_from_manifest,
    update_manifest,
)


# =============================================================================
# Sync/Manifest Tests
# =============================================================================


class TestInstalledAsset:
    """Tests for InstalledAsset dataclass."""

    def test_from_dict(self):
        """Test creating from dictionary."""
        data = {
            "version": "1.0.0",
            "type": "command",
            "pinned": True,
            "installed_at": "2024-01-15T10:00:00Z",
            "publisher_slug": "acme",
            "name": "My Command",
            "local_path": "/path/to/command",
        }

        asset = InstalledAsset.from_dict(data)

        assert asset.version == "1.0.0"
        assert asset.asset_type == "command"
        assert asset.pinned is True
        assert asset.installed_at == "2024-01-15T10:00:00Z"
        assert asset.publisher_slug == "acme"

    def test_to_dict(self):
        """Test converting to dictionary."""
        asset = InstalledAsset(
            version="2.0.0",
            asset_type="skill",
            pinned=False,
            installed_at="2024-02-01T12:00:00Z",
        )

        data = asset.to_dict()

        assert data["version"] == "2.0.0"
        assert data["type"] == "skill"
        assert data["pinned"] is False

    def test_default_installed_at(self):
        """Test installed_at defaults to current time."""
        asset = InstalledAsset(
            version="1.0.0",
            asset_type="command",
        )

        assert asset.installed_at != ""
        assert "T" in asset.installed_at  # ISO format


class TestLocalManifest:
    """Tests for LocalManifest class."""

    def test_load_nonexistent_file(self, tmp_path):
        """Test loading from nonexistent file returns empty manifest."""
        manifest = LocalManifest.load(tmp_path / "nonexistent.json")

        assert manifest.version == MANIFEST_VERSION
        assert manifest.assets == {}
        assert manifest.synced_at == ""

    def test_save_and_load(self, tmp_path):
        """Test saving and loading manifest."""
        manifest_path = tmp_path / "manifest.json"

        manifest = LocalManifest()
        manifest.add_asset(
            full_name="@acme/tool",
            version="1.0.0",
            asset_type="command",
            publisher_slug="acme",
        )
        manifest.update_sync_time()
        manifest.save(manifest_path)

        loaded = LocalManifest.load(manifest_path)

        assert loaded.version == MANIFEST_VERSION
        assert "@acme/tool" in loaded.assets
        assert loaded.assets["@acme/tool"].version == "1.0.0"
        assert loaded.synced_at != ""

    def test_add_asset(self):
        """Test adding an asset."""
        manifest = LocalManifest()
        manifest.add_asset(
            full_name="@pub/slug",
            version="1.2.3",
            asset_type="skill",
            publisher_slug="pub",
            name="My Skill",
        )

        assert "@pub/slug" in manifest.assets
        assert manifest.assets["@pub/slug"].version == "1.2.3"
        assert manifest.assets["@pub/slug"].asset_type == "skill"

    def test_remove_asset(self):
        """Test removing an asset."""
        manifest = LocalManifest()
        manifest.add_asset("@test/asset", "1.0.0", "command")

        result = manifest.remove_asset("@test/asset")

        assert result is True
        assert "@test/asset" not in manifest.assets

    def test_remove_nonexistent_asset(self):
        """Test removing nonexistent asset returns False."""
        manifest = LocalManifest()

        result = manifest.remove_asset("@nonexistent/asset")

        assert result is False

    def test_get_asset(self):
        """Test getting an asset."""
        manifest = LocalManifest()
        manifest.add_asset("@test/asset", "1.0.0", "command")

        asset = manifest.get_asset("@test/asset")

        assert asset is not None
        assert asset.version == "1.0.0"

    def test_get_nonexistent_asset(self):
        """Test getting nonexistent asset returns None."""
        manifest = LocalManifest()

        asset = manifest.get_asset("@nonexistent/asset")

        assert asset is None

    def test_load_invalid_json(self, tmp_path):
        """Test loading invalid JSON returns empty manifest."""
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text("not valid json")

        manifest = LocalManifest.load(manifest_path)

        assert manifest.assets == {}


class TestAssetPath:
    """Tests for get_asset_path function."""

    def test_command_path(self):
        """Test command path is .md file in commands dir."""
        path = get_asset_path("acme", "review-pr", "command")

        assert path.name == "review-pr.md"
        assert "commands" in str(path)

    def test_skill_path(self):
        """Test skill path includes publisher prefix."""
        path = get_asset_path("acme", "my-skill", "skill")

        assert "@acme" in str(path)
        assert "my-skill" in str(path)
        assert "skills" in str(path)

    def test_style_path(self):
        """Test style path."""
        path = get_asset_path("pub", "my-style", "style")

        assert "styles" in str(path)

    def test_hook_path(self):
        """Test hook path."""
        path = get_asset_path("pub", "my-hook", "hook")

        assert "hooks" in str(path)

    def test_prompt_path(self):
        """Test prompt path."""
        path = get_asset_path("pub", "my-prompt", "prompt")

        assert "prompts" in str(path)


class TestExtractAsset:
    """Tests for extract_asset function."""

    def create_test_tarball(self, files: dict[str, str]) -> bytes:
        """Create a test gzipped tarball."""
        tar_buffer = io.BytesIO()

        with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
            for filename, content in files.items():
                content_bytes = content.encode("utf-8")
                info = tarfile.TarInfo(name=filename)
                info.size = len(content_bytes)
                tar.addfile(info, io.BytesIO(content_bytes))

        tar_data = tar_buffer.getvalue()

        gz_buffer = io.BytesIO()
        with gzip.GzipFile(fileobj=gz_buffer, mode="wb") as gz:
            gz.write(tar_data)

        return gz_buffer.getvalue()

    def test_extract_command(self, tmp_path):
        """Test extracting a command asset."""
        tarball = self.create_test_tarball({
            "command.md": "# My Command\nDo something useful",
            "meta.json": '{"description": "A test command"}',
        })

        with patch("repotoire.cli.marketplace_sync.COMMANDS_DIR", tmp_path):
            path = extract_asset("acme", "my-cmd", "command", tarball)

        assert path.exists()
        assert path.suffix == ".md"
        assert "My Command" in path.read_text()

    def test_extract_skill(self, tmp_path):
        """Test extracting a skill asset."""
        tarball = self.create_test_tarball({
            "skill.json": '{"name": "test-skill"}',
            "server.py": "print('hello')",
        })

        with patch("repotoire.cli.marketplace_sync.MARKETPLACE_DIR", tmp_path):
            with patch("repotoire.cli.marketplace_sync.ASSET_DIRECTORIES", {
                "skill": tmp_path / "skills"
            }):
                path = extract_asset("acme", "my-skill", "skill", tarball)

        assert path.exists()
        assert path.is_dir()
        assert (path / "skill.json").exists()
        assert (path / "server.py").exists()

    def test_extract_empty_tarball_raises(self, tmp_path):
        """Test that empty tarball raises ValueError."""
        # Create empty gzip
        gz_buffer = io.BytesIO()
        with gzip.GzipFile(fileobj=gz_buffer, mode="wb") as gz:
            tar_buffer = io.BytesIO()
            with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
                pass  # Empty tar
            gz.write(tar_buffer.getvalue())

        with pytest.raises(ValueError) as exc_info:
            extract_asset("acme", "empty", "command", gz_buffer.getvalue())

        assert "Empty tarball" in str(exc_info.value)

    def test_extract_command_missing_file_raises(self, tmp_path):
        """Test that command without command.md raises."""
        tarball = self.create_test_tarball({
            "meta.json": '{}',  # No command.md
        })

        with pytest.raises(ValueError) as exc_info:
            extract_asset("acme", "bad-cmd", "command", tarball)

        assert "missing command.md" in str(exc_info.value)


class TestRemoveAssetFiles:
    """Tests for remove_asset_files function."""

    def test_remove_command_file(self, tmp_path):
        """Test removing a command file."""
        cmd_path = tmp_path / "my-cmd.md"
        cmd_path.write_text("# Command")

        with patch("repotoire.cli.marketplace_sync.COMMANDS_DIR", tmp_path):
            with patch("repotoire.cli.marketplace_sync.ASSET_DIRECTORIES", {"command": tmp_path}):
                result = remove_asset_files("acme", "my-cmd", "command")

        assert result is True
        assert not cmd_path.exists()

    def test_remove_skill_directory(self, tmp_path):
        """Test removing a skill directory."""
        skill_dir = tmp_path / "@acme" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "skill.json").write_text("{}")

        with patch("repotoire.cli.marketplace_sync.MARKETPLACE_DIR", tmp_path):
            with patch("repotoire.cli.marketplace_sync.ASSET_DIRECTORIES", {
                "skill": tmp_path
            }):
                result = remove_asset_files("acme", "my-skill", "skill")

        assert result is True
        assert not skill_dir.exists()

    def test_remove_nonexistent_returns_false(self, tmp_path):
        """Test removing nonexistent asset returns False."""
        with patch("repotoire.cli.marketplace_sync.COMMANDS_DIR", tmp_path):
            with patch("repotoire.cli.marketplace_sync.ASSET_DIRECTORIES", {"command": tmp_path}):
                result = remove_asset_files("acme", "nonexistent", "command")

        assert result is False


class TestCheckForUpdates:
    """Tests for check_for_updates function."""

    def test_no_updates_needed(self):
        """Test when all assets are up to date."""
        installed = {
            "@acme/tool": InstalledAsset(version="1.0.0", asset_type="command"),
        }
        remote = [{"publisher_slug": "acme", "slug": "tool", "latest_version": "1.0.0"}]

        updates = check_for_updates(installed, remote)

        assert updates == []

    def test_update_available(self):
        """Test when update is available."""
        installed = {
            "@acme/tool": InstalledAsset(version="1.0.0", asset_type="command"),
        }
        remote = [{"publisher_slug": "acme", "slug": "tool", "latest_version": "2.0.0"}]

        updates = check_for_updates(installed, remote)

        assert len(updates) == 1
        assert updates[0]["latest_version"] == "2.0.0"

    def test_pinned_asset_not_updated(self):
        """Test that pinned assets are not included in updates."""
        installed = {
            "@acme/tool": InstalledAsset(version="1.0.0", asset_type="command", pinned=True),
        }
        remote = [{"publisher_slug": "acme", "slug": "tool", "latest_version": "2.0.0"}]

        updates = check_for_updates(installed, remote)

        assert updates == []

    def test_not_installed_asset_skipped(self):
        """Test that assets not installed locally are skipped."""
        installed = {}
        remote = [{"publisher_slug": "acme", "slug": "new-tool", "latest_version": "1.0.0"}]

        updates = check_for_updates(installed, remote)

        assert updates == []


# =============================================================================
# CLI Command Tests
# =============================================================================


class TestMarketplaceCLI:
    """Tests for marketplace CLI commands."""

    @pytest.fixture
    def runner(self):
        """Create CLI runner."""
        return CliRunner()

    @pytest.fixture
    def mock_client(self):
        """Create mock API client."""
        with patch.object(_marketplace_module, "_get_client") as mock:
            client = MagicMock()
            mock.return_value = client
            yield client

    def test_search_command(self, runner, mock_client):
        """Test search command."""
        from repotoire.cli.marketplace_client import AssetInfo

        mock_client.search.return_value = [
            AssetInfo(
                id="1",
                publisher_slug="acme",
                slug="tool",
                name="Tool",
                description="A tool",
                asset_type="command",
                latest_version="1.0.0",
                rating=4.5,
                install_count=1000,
                pricing="free",
            )
        ]

        result = runner.invoke(marketplace, ["search", "code review"])

        assert result.exit_code == 0
        assert "tool" in result.output.lower() or "Tool" in result.output

    def test_search_no_results(self, runner, mock_client):
        """Test search with no results."""
        mock_client.search.return_value = []

        result = runner.invoke(marketplace, ["search", "nonexistent"])

        assert result.exit_code == 0
        assert "No assets found" in result.output

    def test_browse_command(self, runner, mock_client):
        """Test browse command."""
        from repotoire.cli.marketplace_client import AssetInfo

        mock_client.browse.return_value = [
            AssetInfo(
                id="1",
                publisher_slug="acme",
                slug="popular-tool",
                name="Popular Tool",
                description="Very popular",
                asset_type="skill",
                latest_version="2.0.0",
                rating=4.8,
                install_count=50000,
                pricing="pro",
            )
        ]

        result = runner.invoke(marketplace, ["browse", "--sort=popular"])

        assert result.exit_code == 0
        assert "Popular" in result.output

    def test_info_command(self, runner, mock_client):
        """Test info command."""
        from repotoire.cli.marketplace_client import AssetInfo

        mock_client.get_asset.return_value = AssetInfo(
            id="1",
            publisher_slug="repotoire",
            slug="review-pr",
            name="Review PR",
            description="AI-powered PR review",
            asset_type="command",
            latest_version="1.5.0",
            rating=4.7,
            install_count=12500,
            pricing="free",
        )
        mock_client.get_asset_versions.return_value = []

        result = runner.invoke(marketplace, ["info", "@repotoire/review-pr"])

        assert result.exit_code == 0
        assert "Review PR" in result.output
        assert "1.5.0" in result.output

    def test_info_invalid_reference(self, runner):
        """Test info with invalid reference."""
        result = runner.invoke(marketplace, ["info", "invalid-ref"])

        assert result.exit_code != 0
        assert "Invalid asset reference" in result.output

    def test_list_empty(self, runner, tmp_path):
        """Test list when no assets installed."""
        with patch("repotoire.cli.marketplace_sync.MANIFEST_FILE", tmp_path / "manifest.json"):
            result = runner.invoke(marketplace, ["list"])

        assert result.exit_code == 0
        assert "No marketplace assets installed" in result.output

    def test_list_with_assets(self, runner, tmp_path):
        """Test list with installed assets."""
        manifest = LocalManifest()
        manifest.add_asset("@acme/tool", "1.0.0", "command")
        manifest.add_asset("@acme/skill", "2.0.0", "skill", pinned=True)

        manifest_file = tmp_path / "manifest.json"
        manifest.save(manifest_file)

        with patch("repotoire.cli.marketplace_sync.MANIFEST_FILE", manifest_file):
            result = runner.invoke(marketplace, ["list"])

        assert result.exit_code == 0
        assert "@acme/tool" in result.output
        assert "@acme/skill" in result.output

    def test_install_success(self, runner, mock_client, tmp_path):
        """Test successful install."""
        from repotoire.cli.marketplace_client import AssetInfo, InstallResult

        # Create test tarball
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
            content = b"# My Command"
            info = tarfile.TarInfo(name="command.md")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))

        gz_buffer = io.BytesIO()
        with gzip.GzipFile(fileobj=gz_buffer, mode="wb") as gz:
            gz.write(tar_buffer.getvalue())

        mock_client.install.return_value = InstallResult(
            asset=AssetInfo(
                id="1",
                publisher_slug="acme",
                slug="tool",
                name="Tool",
                description="",
                asset_type="command",
                latest_version="1.0.0",
                rating=None,
                install_count=0,
                pricing="free",
            ),
            version="1.0.0",
            download_url="https://example.com/download",
            checksum="abc123",
            dependencies=[],
        )
        mock_client.download_asset.return_value = gz_buffer.getvalue()

        # Must patch ASSET_DIRECTORIES as well since it's captured at module load
        patched_asset_dirs = {
            "command": tmp_path,
            "skill": tmp_path / "skills",
            "style": tmp_path / "styles",
            "hook": tmp_path / "hooks",
            "prompt": tmp_path / "prompts",
        }

        with patch("repotoire.cli.marketplace_sync.COMMANDS_DIR", tmp_path):
            with patch("repotoire.cli.marketplace_sync.MARKETPLACE_DIR", tmp_path):
                with patch("repotoire.cli.marketplace_sync.MANIFEST_FILE", tmp_path / "manifest.json"):
                    with patch("repotoire.cli.marketplace_sync.ASSET_DIRECTORIES", patched_asset_dirs):
                        result = runner.invoke(marketplace, ["install", "@acme/tool"])

        assert result.exit_code == 0
        assert "Installed" in result.output

    def test_uninstall_not_installed(self, runner, mock_client, tmp_path):
        """Test uninstall when asset not installed."""
        manifest_file = tmp_path / "manifest.json"
        LocalManifest().save(manifest_file)

        with patch("repotoire.cli.marketplace_sync.MANIFEST_FILE", manifest_file):
            result = runner.invoke(marketplace, ["uninstall", "@acme/tool"])

        assert result.exit_code != 0
        assert "not installed" in result.output

    def test_config_show(self, runner, tmp_path):
        """Test config command shows asset info."""
        manifest = LocalManifest()
        manifest.add_asset(
            "@test/asset",
            "1.0.0",
            "command",
            publisher_slug="test",
            local_path="/path/to/asset",
        )

        manifest_file = tmp_path / "manifest.json"
        manifest.save(manifest_file)

        with patch("repotoire.cli.marketplace_sync.MANIFEST_FILE", manifest_file):
            result = runner.invoke(marketplace, ["config", "@test/asset"])

        assert result.exit_code == 0
        assert "1.0.0" in result.output

    def test_config_pin(self, runner, tmp_path):
        """Test config --pin command."""
        manifest = LocalManifest()
        manifest.add_asset("@test/asset", "1.0.0", "command")

        manifest_file = tmp_path / "manifest.json"
        manifest.save(manifest_file)

        with patch("repotoire.cli.marketplace_sync.MANIFEST_FILE", manifest_file):
            result = runner.invoke(marketplace, ["config", "@test/asset", "--pin"])

        assert result.exit_code == 0
        assert "Pinned" in result.output

    def test_publish_no_publisher(self, runner, mock_client, tmp_path):
        """Test publish when user has no publisher profile."""
        content_file = tmp_path / "command.md"
        content_file.write_text("# My Command")

        mock_client.validate_asset.return_value = []
        mock_client.get_my_publisher.return_value = None

        result = runner.invoke(
            marketplace,
            ["publish", "@me/my-cmd", str(content_file), "1.0.0"],
        )

        assert result.exit_code != 0
        assert "publisher profile" in result.output.lower()

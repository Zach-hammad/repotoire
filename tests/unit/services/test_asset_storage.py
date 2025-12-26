"""Unit tests for asset storage service."""

import asyncio
import gzip
import io
import json
import os
import tarfile
from unittest.mock import MagicMock, patch

import pytest

from repotoire.api.services.asset_packager import (
    AssetPackager,
    AssetPackagingError,
    PackageResult,
)
from repotoire.api.services.asset_storage import (
    AssetNotFoundError,
    AssetStorageService,
    StorageError,
    StorageNotConfiguredError,
    UploadResult,
    _get_icon_key,
    _get_version_key,
    is_storage_configured,
)
from repotoire.db.models.marketplace import AssetType


def run_async(coro):
    """Helper to run async coroutines in sync tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


# =============================================================================
# Path generation tests
# =============================================================================


class TestPathGeneration:
    """Tests for S3 key generation."""

    def test_version_key_format(self):
        """Test version key follows convention."""
        key = _get_version_key("acme-corp", "code-review", "1.0.0")
        assert key == "assets/@acme-corp/code-review/1.0.0.tar.gz"

    def test_version_key_with_prerelease(self):
        """Test version key with prerelease version."""
        key = _get_version_key("publisher", "asset", "2.0.0-beta.1")
        assert key == "assets/@publisher/asset/2.0.0-beta.1.tar.gz"

    def test_icon_key_format(self):
        """Test icon key follows convention."""
        key = _get_icon_key("550e8400-e29b-41d4-a716-446655440000")
        assert key == "icons/550e8400-e29b-41d4-a716-446655440000.png"


# =============================================================================
# Configuration tests
# =============================================================================


class TestStorageConfiguration:
    """Tests for storage configuration."""

    def test_not_configured_missing_all(self):
        """Test not configured when all env vars missing."""
        with patch.dict(os.environ, {}, clear=True):
            # Need to reload module to pick up env changes
            with patch("repotoire.api.services.asset_storage.R2_ENDPOINT_URL", None):
                with patch("repotoire.api.services.asset_storage.R2_ACCOUNT_ID", None):
                    with patch("repotoire.api.services.asset_storage.R2_ACCESS_KEY_ID", None):
                        assert is_storage_configured() is False

    def test_configured_with_endpoint(self):
        """Test configured with endpoint URL."""
        with patch("repotoire.api.services.asset_storage.R2_ENDPOINT_URL", "https://test.r2.dev"):
            with patch("repotoire.api.services.asset_storage.R2_ACCESS_KEY_ID", "key"):
                with patch("repotoire.api.services.asset_storage.R2_SECRET_ACCESS_KEY", "secret"):
                    with patch("repotoire.api.services.asset_storage.R2_BUCKET", "bucket"):
                        assert is_storage_configured() is True

    def test_configured_with_account_id(self):
        """Test configured with account ID."""
        with patch("repotoire.api.services.asset_storage.R2_ENDPOINT_URL", None):
            with patch("repotoire.api.services.asset_storage.R2_ACCOUNT_ID", "account123"):
                with patch("repotoire.api.services.asset_storage.R2_ACCESS_KEY_ID", "key"):
                    with patch("repotoire.api.services.asset_storage.R2_SECRET_ACCESS_KEY", "secret"):
                        with patch("repotoire.api.services.asset_storage.R2_BUCKET", "bucket"):
                            assert is_storage_configured() is True


# =============================================================================
# AssetPackager tests
# =============================================================================


class TestAssetPackager:
    """Tests for AssetPackager class."""

    @pytest.fixture
    def packager(self):
        """Create packager instance."""
        return AssetPackager()

    def test_package_command(self, packager):
        """Test packaging a command asset."""
        content = {
            "prompt": "Review the PR changes and provide feedback",
            "description": "AI-powered PR review",
            "arguments": [{"name": "pr_number", "required": True}],
        }

        result = packager.package(AssetType.COMMAND, content)

        assert isinstance(result, PackageResult)
        assert result.size > 0
        assert len(result.checksum) == 64  # SHA-256 hex
        assert "command.md" in result.files
        assert "meta.json" in result.files

    def test_package_command_roundtrip(self, packager):
        """Test command can be packaged and unpackaged."""
        content = {
            "prompt": "Test prompt content",
            "description": "Test description",
            "arguments": [],
        }

        result = packager.package(AssetType.COMMAND, content)
        files = packager.unpackage(result.data)

        assert files["command.md"].decode("utf-8") == "Test prompt content"
        meta = json.loads(files["meta.json"])
        assert meta["description"] == "Test description"

    def test_package_skill(self, packager):
        """Test packaging a skill asset."""
        content = {
            "name": "test-skill",
            "description": "A test skill",
            "tools": [{"name": "test_tool", "description": "Does testing"}],
            "server": {"type": "stdio", "command": "python server.py"},
            "server_code": "print('Hello')",
            "requirements": ["requests", "pydantic"],
        }

        result = packager.package(AssetType.SKILL, content)

        assert "skill.json" in result.files
        assert "server.py" in result.files
        assert "requirements.txt" in result.files

    def test_package_skill_with_additional_files(self, packager):
        """Test skill with additional files."""
        content = {
            "name": "test-skill",
            "description": "A test skill",
            "files": {
                "utils.py": "# Utility functions",
                "config.yaml": "key: value",
            },
        }

        result = packager.package(AssetType.SKILL, content)

        assert "utils.py" in result.files
        assert "config.yaml" in result.files

    def test_package_skill_sanitizes_paths(self, packager):
        """Test skill sanitizes file paths."""
        content = {
            "name": "test-skill",
            "description": "A test skill",
            "files": {
                "../../../etc/passwd": "malicious",
                "/etc/shadow": "malicious",
                ".hidden": "hidden",
            },
        }

        result = packager.package(AssetType.SKILL, content)

        # Path traversal should be sanitized
        assert "../../../etc/passwd" not in result.files
        assert "etc/passwd" in result.files  # Stripped to safe path
        assert "etc/shadow" in result.files  # Leading slash stripped
        assert ".hidden" not in result.files  # Hidden files excluded

    def test_package_style(self, packager):
        """Test packaging a style asset."""
        content = {
            "instructions": "Always be concise and helpful.",
            "examples": [{"input": "Hi", "output": "Hello!"}],
        }

        result = packager.package(AssetType.STYLE, content)

        assert "style.md" in result.files
        assert "examples.json" in result.files

    def test_package_style_roundtrip(self, packager):
        """Test style can be packaged and unpackaged."""
        content = {
            "instructions": "Be helpful",
        }

        result = packager.package(AssetType.STYLE, content)
        files = packager.unpackage(result.data)

        assert files["style.md"].decode("utf-8") == "Be helpful"

    def test_package_hook(self, packager):
        """Test packaging a hook asset."""
        content = {
            "event": "PreToolCall",
            "matcher": {"tool": "Write"},
            "command": "python check.py",
        }

        result = packager.package(AssetType.HOOK, content)

        assert "hook.json" in result.files

    def test_package_hook_roundtrip(self, packager):
        """Test hook can be packaged and unpackaged."""
        content = {
            "event": "PostToolCall",
            "command": "echo done",
        }

        result = packager.package(AssetType.HOOK, content)
        files = packager.unpackage(result.data)

        hook = json.loads(files["hook.json"])
        assert hook["event"] == "PostToolCall"
        assert hook["command"] == "echo done"

    def test_package_prompt(self, packager):
        """Test packaging a prompt asset."""
        content = {
            "template": "Hello {{name}}!",
            "description": "Greeting template",
            "variables": [{"name": "name", "description": "User name"}],
        }

        result = packager.package(AssetType.PROMPT, content)

        assert "prompt.md" in result.files
        assert "variables.json" in result.files

    def test_package_prompt_roundtrip(self, packager):
        """Test prompt can be packaged and unpackaged."""
        content = {
            "template": "Hello {{name}}!",
            "variables": [{"name": "name"}],
        }

        result = packager.package(AssetType.PROMPT, content)
        files = packager.unpackage(result.data)

        assert files["prompt.md"].decode("utf-8") == "Hello {{name}}!"
        variables = json.loads(files["variables.json"])
        assert len(variables["variables"]) == 1

    def test_package_invalid_type_string(self, packager):
        """Test packaging with invalid type string."""
        with pytest.raises(AssetPackagingError) as exc_info:
            packager.package("invalid", {})
        assert "Invalid asset type" in str(exc_info.value)

    def test_checksum_consistency(self, packager):
        """Test checksum is consistent for same content."""
        content = {"prompt": "Test", "description": "Test"}

        result1 = packager.package(AssetType.COMMAND, content)
        result2 = packager.package(AssetType.COMMAND, content)

        assert result1.checksum == result2.checksum

    def test_unpackage_invalid_data(self, packager):
        """Test unpackaging invalid data raises error."""
        with pytest.raises(AssetPackagingError):
            packager.unpackage(b"not a tarball")


# =============================================================================
# AssetStorageService tests (with mocked S3)
# =============================================================================


class TestAssetStorageService:
    """Tests for AssetStorageService with mocked S3."""

    @pytest.fixture
    def mock_s3_client(self):
        """Create mocked S3 client."""
        client = MagicMock()
        return client

    @pytest.fixture
    def storage_service(self, mock_s3_client):
        """Create storage service with mocked client."""
        with patch("repotoire.api.services.asset_storage.is_storage_configured", return_value=True):
            with patch("repotoire.api.services.asset_storage._get_s3_client", return_value=mock_s3_client):
                service = AssetStorageService()
                service._client = mock_s3_client
                return service

    def test_upload_version(self, storage_service, mock_s3_client):
        """Test uploading a version."""
        content = b"test tarball content"

        result = run_async(storage_service.upload_version(
            publisher_slug="acme",
            asset_slug="review",
            version="1.0.0",
            content=content,
        ))

        assert isinstance(result, UploadResult)
        assert result.url == "assets/@acme/review/1.0.0.tar.gz"
        assert len(result.checksum) == 64
        assert result.size == len(content)
        mock_s3_client.put_object.assert_called_once()

    def test_download_version(self, storage_service, mock_s3_client):
        """Test downloading a version."""
        mock_s3_client.get_object.return_value = {
            "Body": MagicMock(read=lambda: b"tarball data")
        }

        data = run_async(storage_service.download_version("acme", "review", "1.0.0"))

        assert data == b"tarball data"
        mock_s3_client.get_object.assert_called_once()

    def test_download_version_not_found(self, storage_service, mock_s3_client):
        """Test downloading non-existent version."""
        mock_s3_client.get_object.side_effect = Exception("NoSuchKey")

        with pytest.raises(AssetNotFoundError):
            run_async(storage_service.download_version("acme", "review", "9.9.9"))

    def test_get_presigned_url(self, storage_service, mock_s3_client):
        """Test generating presigned URL."""
        mock_s3_client.generate_presigned_url.return_value = "https://presigned.url/path"

        url = run_async(storage_service.get_presigned_url("acme", "review", "1.0.0"))

        assert url == "https://presigned.url/path"
        mock_s3_client.generate_presigned_url.assert_called_once()

    def test_get_presigned_url_custom_expiry(self, storage_service, mock_s3_client):
        """Test presigned URL with custom expiry."""
        mock_s3_client.generate_presigned_url.return_value = "https://url"

        run_async(storage_service.get_presigned_url("acme", "review", "1.0.0", expires_in=7200))

        call_kwargs = mock_s3_client.generate_presigned_url.call_args
        assert call_kwargs[1]["ExpiresIn"] == 7200

    def test_delete_version(self, storage_service, mock_s3_client):
        """Test deleting a version."""
        run_async(storage_service.delete_version("acme", "review", "1.0.0"))

        mock_s3_client.delete_object.assert_called_once()

    def test_upload_icon(self, storage_service, mock_s3_client):
        """Test uploading an icon."""
        image_bytes = b"PNG image data"

        key = run_async(storage_service.upload_icon(
            asset_id="abc123",
            image_bytes=image_bytes,
            content_type="image/png",
        ))

        assert key == "icons/abc123.png"
        mock_s3_client.put_object.assert_called_once()
        call_kwargs = mock_s3_client.put_object.call_args[1]
        assert call_kwargs["CacheControl"] == "public, max-age=31536000, immutable"

    def test_delete_icon(self, storage_service, mock_s3_client):
        """Test deleting an icon."""
        run_async(storage_service.delete_icon("abc123"))

        mock_s3_client.delete_object.assert_called_once()

    def test_version_exists_true(self, storage_service, mock_s3_client):
        """Test version_exists returns True when exists."""
        mock_s3_client.head_object.return_value = {}

        exists = run_async(storage_service.version_exists("acme", "review", "1.0.0"))

        assert exists is True

    def test_version_exists_false(self, storage_service, mock_s3_client):
        """Test version_exists returns False when not found."""
        mock_s3_client.head_object.side_effect = Exception("Not found")

        exists = run_async(storage_service.version_exists("acme", "review", "9.9.9"))

        assert exists is False

    def test_get_version_metadata(self, storage_service, mock_s3_client):
        """Test getting version metadata."""
        mock_s3_client.head_object.return_value = {
            "ContentLength": 12345,
            "ContentType": "application/gzip",
            "LastModified": "2024-01-01",
            "Metadata": {"checksum": "abc123"},
        }

        meta = run_async(storage_service.get_version_metadata("acme", "review", "1.0.0"))

        assert meta["size"] == 12345
        assert meta["content_type"] == "application/gzip"
        assert meta["checksum"] == "abc123"


# =============================================================================
# Integration-style tests (packager + storage)
# =============================================================================


class TestPackagerStorageIntegration:
    """Tests for packager and storage working together."""

    @pytest.fixture
    def packager(self):
        """Create packager instance."""
        return AssetPackager()

    def test_package_produces_valid_gzip(self, packager):
        """Test packaged content is valid gzip."""
        content = {"prompt": "Test", "description": "Test"}
        result = packager.package(AssetType.COMMAND, content)

        # Should decompress without error
        with gzip.GzipFile(fileobj=io.BytesIO(result.data)) as gz:
            tar_data = gz.read()
            assert len(tar_data) > 0

    def test_package_produces_valid_tar(self, packager):
        """Test packaged content contains valid tar."""
        content = {"prompt": "Test", "description": "Test"}
        result = packager.package(AssetType.COMMAND, content)

        with gzip.GzipFile(fileobj=io.BytesIO(result.data)) as gz:
            with tarfile.open(fileobj=gz, mode="r:") as tar:
                members = tar.getmembers()
                assert len(members) == 2  # command.md and meta.json

    def test_full_roundtrip_all_types(self, packager):
        """Test all asset types can be packaged and unpackaged."""
        test_cases = [
            (AssetType.COMMAND, {"prompt": "Test prompt content", "description": "Test"}),
            (AssetType.SKILL, {"name": "test", "description": "Test skill"}),
            (AssetType.STYLE, {"instructions": "Be helpful"}),
            (AssetType.HOOK, {"event": "PreToolCall", "command": "echo"}),
            (AssetType.PROMPT, {"template": "Hello {{name}}", "variables": []}),
        ]

        for asset_type, content in test_cases:
            result = packager.package(asset_type, content)
            files = packager.unpackage(result.data)
            assert len(files) > 0, f"Failed for {asset_type}"

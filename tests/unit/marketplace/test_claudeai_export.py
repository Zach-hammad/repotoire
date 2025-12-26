"""Tests for Claude.ai export functions.

Tests the export functionality for creating Claude.ai-compatible
content from marketplace assets.
"""

import json
import pytest
from pathlib import Path

from repotoire.marketplace.claudeai_export import (
    ExportedAsset,
    export_as_project_instructions,
    export_as_artifact,
    export_style_instructions,
    export_prompt_template,
    generate_clipboard_text,
    load_asset_from_file,
)


@pytest.fixture
def sample_skill_asset():
    """Create a sample skill asset."""
    return ExportedAsset(
        name="Code Review Assistant",
        slug="code-review",
        publisher="repotoire",
        version="1.2.0",
        asset_type="skill",
        description="AI-powered code review tool",
        content={
            "capabilities": [
                {"name": "analyze", "description": "Analyze code for issues"},
                {"name": "suggest", "description": "Suggest improvements"},
            ],
            "usage": "Use when reviewing code changes.",
        },
    )


@pytest.fixture
def sample_command_asset():
    """Create a sample command asset."""
    return ExportedAsset(
        name="Review PR",
        slug="review-pr",
        publisher="community",
        version="2.0.0",
        asset_type="command",
        description="Review a GitHub pull request",
        content={
            "prompt": "Review the following PR:\n\n{{pr_url}}\n\nFocus on:\n- Code quality\n- Security issues\n- Performance",
            "variables": [
                {"name": "pr_url", "description": "URL of the pull request"},
            ],
        },
    )


@pytest.fixture
def sample_style_asset():
    """Create a sample style asset."""
    return ExportedAsset(
        name="Concise Expert",
        slug="concise-expert",
        publisher="styles",
        version="1.0.0",
        asset_type="style",
        description="Professional, concise responses",
        content={
            "rules": [
                "Be concise and direct",
                "Use technical language appropriately",
                "Provide code examples when helpful",
            ],
            "tone": "Professional and friendly",
            "examples": [
                {"type": "Good", "content": "Here's how to fix that..."},
                {"type": "Avoid", "content": "Well, you see, the thing is..."},
            ],
        },
    )


@pytest.fixture
def sample_prompt_asset():
    """Create a sample prompt asset."""
    return ExportedAsset(
        name="Bug Report Template",
        slug="bug-report",
        publisher="templates",
        version="1.0.0",
        asset_type="prompt",
        description="Generate bug reports",
        content={
            "template": "## Bug Report\n\n**Summary:** {{summary}}\n\n**Steps to Reproduce:**\n{{steps}}\n\n**Expected:** {{expected}}\n**Actual:** {{actual}}",
            "variables": [
                {"name": "summary", "description": "Brief bug summary"},
                {"name": "steps", "description": "Steps to reproduce"},
                {"name": "expected", "description": "Expected behavior"},
                {"name": "actual", "description": "Actual behavior"},
            ],
        },
    )


class TestExportedAsset:
    """Tests for ExportedAsset dataclass."""

    def test_create_asset(self):
        """Test creating an ExportedAsset."""
        asset = ExportedAsset(
            name="Test",
            slug="test",
            publisher="pub",
            version="1.0.0",
            asset_type="command",
            description="A test asset",
            content="test content",
        )

        assert asset.name == "Test"
        assert asset.slug == "test"
        assert asset.publisher == "pub"
        assert asset.version == "1.0.0"

    def test_content_can_be_dict(self):
        """Test that content can be a dictionary."""
        asset = ExportedAsset(
            name="Test",
            slug="test",
            publisher="pub",
            version="1.0.0",
            asset_type="skill",
            description="Test",
            content={"key": "value"},
        )

        assert asset.content == {"key": "value"}

    def test_content_can_be_string(self):
        """Test that content can be a string."""
        asset = ExportedAsset(
            name="Test",
            slug="test",
            publisher="pub",
            version="1.0.0",
            asset_type="prompt",
            description="Test",
            content="string content",
        )

        assert asset.content == "string content"


class TestExportAsProjectInstructions:
    """Tests for export_as_project_instructions function."""

    def test_export_single_skill(self, sample_skill_asset):
        """Test exporting a single skill asset."""
        result = export_as_project_instructions([sample_skill_asset])

        assert "# Custom Project Instructions" in result
        assert "Code Review Assistant" in result
        assert "@repotoire/code-review" in result
        assert "Available Skills" in result

    def test_export_single_command(self, sample_command_asset):
        """Test exporting a single command asset."""
        result = export_as_project_instructions([sample_command_asset])

        assert "Available Commands" in result
        assert "/review-pr" in result
        assert "Review a GitHub pull request" in result

    def test_export_single_style(self, sample_style_asset):
        """Test exporting a single style asset."""
        result = export_as_project_instructions([sample_style_asset])

        assert "Response Style" in result
        assert "Be concise and direct" in result
        assert "Professional and friendly" not in result  # Tone not in style section

    def test_export_multiple_assets(
        self, sample_skill_asset, sample_command_asset, sample_style_asset
    ):
        """Test exporting multiple assets."""
        assets = [sample_skill_asset, sample_command_asset, sample_style_asset]
        result = export_as_project_instructions(assets)

        assert "Response Style" in result
        assert "Available Skills" in result
        assert "Available Commands" in result

    def test_export_without_header(self, sample_skill_asset):
        """Test exporting without header."""
        result = export_as_project_instructions(
            [sample_skill_asset], include_header=False
        )

        assert "# Custom Project Instructions" not in result
        assert "Code Review Assistant" in result

    def test_export_prompt_assets(self, sample_prompt_asset):
        """Test exporting prompt assets."""
        result = export_as_project_instructions([sample_prompt_asset])

        assert "Prompt Templates" in result
        assert "Bug Report Template" in result
        assert "summary" in result

    def test_export_empty_list(self):
        """Test exporting empty list."""
        result = export_as_project_instructions([])

        # Should still have header
        assert "# Custom Project Instructions" in result


class TestExportAsArtifact:
    """Tests for export_as_artifact function."""

    def test_artifact_structure(self, sample_skill_asset):
        """Test that artifact has correct structure."""
        artifact = export_as_artifact(sample_skill_asset)

        assert artifact["type"] == "artifact"
        assert artifact["title"] == "Code Review Assistant"
        assert "content" in artifact
        assert "metadata" in artifact

    def test_artifact_metadata(self, sample_skill_asset):
        """Test artifact metadata."""
        artifact = export_as_artifact(sample_skill_asset)

        metadata = artifact["metadata"]
        assert metadata["source"] == "@repotoire/code-review"
        assert metadata["version"] == "1.2.0"
        assert metadata["asset_type"] == "skill"
        assert metadata["generator"] == "repotoire-marketplace"

    def test_skill_artifact_is_json(self, sample_skill_asset):
        """Test that skill artifacts use JSON format."""
        artifact = export_as_artifact(sample_skill_asset)

        assert artifact["language"] == "application/json"
        # Content should be valid JSON
        json.loads(artifact["content"])

    def test_command_artifact_is_markdown(self, sample_command_asset):
        """Test that command artifacts use markdown format."""
        artifact = export_as_artifact(sample_command_asset)

        assert artifact["language"] == "text/markdown"

    def test_custom_artifact_type(self, sample_skill_asset):
        """Test overriding artifact type."""
        artifact = export_as_artifact(
            sample_skill_asset, artifact_type="text/plain"
        )

        assert artifact["language"] == "text/plain"

    def test_style_artifact_is_json(self, sample_style_asset):
        """Test that style artifacts use JSON format."""
        artifact = export_as_artifact(sample_style_asset)

        assert artifact["language"] == "application/json"


class TestExportStyleInstructions:
    """Tests for export_style_instructions function."""

    def test_export_style(self, sample_style_asset):
        """Test exporting style instructions."""
        result = export_style_instructions(sample_style_asset)

        assert "# Response Style: Concise Expert" in result
        assert "Professional, concise responses" in result
        assert "## Rules" in result
        assert "1. Be concise and direct" in result
        assert "2. Use technical language appropriately" in result

    def test_export_style_with_examples(self, sample_style_asset):
        """Test that examples are included."""
        result = export_style_instructions(sample_style_asset)

        assert "## Examples" in result
        assert "Good" in result
        assert "Here's how to fix that" in result

    def test_export_non_style_raises_error(self, sample_skill_asset):
        """Test that non-style assets raise an error."""
        with pytest.raises(ValueError, match="Expected style asset"):
            export_style_instructions(sample_skill_asset)

    def test_export_style_string_content(self):
        """Test exporting style with string content."""
        asset = ExportedAsset(
            name="Simple Style",
            slug="simple",
            publisher="pub",
            version="1.0.0",
            asset_type="style",
            description="Simple style",
            content="Just be helpful and concise.",
        )

        result = export_style_instructions(asset)

        assert "Just be helpful and concise." in result


class TestExportPromptTemplate:
    """Tests for export_prompt_template function."""

    def test_export_prompt_no_substitution(self, sample_prompt_asset):
        """Test exporting prompt without variable substitution."""
        result = export_prompt_template(sample_prompt_asset)

        assert "## Bug Report" in result
        assert "{{summary}}" in result
        assert "{{steps}}" in result

    def test_export_prompt_with_substitution(self, sample_prompt_asset):
        """Test exporting prompt with variable substitution."""
        variables = {
            "summary": "App crashes on login",
            "steps": "1. Open app\n2. Click login",
            "expected": "Login succeeds",
            "actual": "App crashes",
        }

        result = export_prompt_template(sample_prompt_asset, variables=variables)

        assert "App crashes on login" in result
        assert "{{summary}}" not in result

    def test_export_command_as_prompt(self, sample_command_asset):
        """Test exporting command asset as prompt."""
        result = export_prompt_template(sample_command_asset)

        assert "Review the following PR" in result

    def test_export_invalid_asset_type(self, sample_style_asset):
        """Test that non-prompt assets raise error."""
        with pytest.raises(ValueError, match="Expected prompt or command"):
            export_prompt_template(sample_style_asset)

    def test_curly_brace_substitution(self):
        """Test both {{var}} and {var} substitution formats."""
        asset = ExportedAsset(
            name="Test",
            slug="test",
            publisher="pub",
            version="1.0.0",
            asset_type="prompt",
            description="Test",
            content={"template": "Hello {{name}} and {other}!"},
        )

        result = export_prompt_template(
            asset, variables={"name": "World", "other": "friends"}
        )

        assert "Hello World and friends!" in result


class TestGenerateClipboardText:
    """Tests for generate_clipboard_text function."""

    def test_clipboard_project_format(self, sample_skill_asset):
        """Test clipboard text in project format."""
        result = generate_clipboard_text([sample_skill_asset], format="project")

        assert "# Custom Project Instructions" in result

    def test_clipboard_artifact_format(self, sample_skill_asset):
        """Test clipboard text in artifact format."""
        result = generate_clipboard_text([sample_skill_asset], format="artifact")

        # Should be the artifact content
        assert "capabilities" in result or "Code Review" in result

    def test_clipboard_snippet_format(self, sample_command_asset):
        """Test clipboard text in snippet format."""
        result = generate_clipboard_text([sample_command_asset], format="snippet")

        assert "# Review PR" in result

    def test_clipboard_empty_assets(self):
        """Test clipboard with empty assets."""
        result = generate_clipboard_text([], format="artifact")

        assert result == ""

    def test_snippet_truncation(self):
        """Test that long snippets are truncated."""
        asset = ExportedAsset(
            name="Long",
            slug="long",
            publisher="pub",
            version="1.0.0",
            asset_type="prompt",
            description="Long content",
            content="x" * 2000,  # Very long content
        )

        result = generate_clipboard_text([asset], format="snippet")

        assert "..." in result
        assert len(result) < 2000


class TestLoadAssetFromFile:
    """Tests for load_asset_from_file function."""

    def test_load_markdown_file(self, tmp_path):
        """Test loading a markdown file."""
        md_file = tmp_path / "commands" / "test.md"
        md_file.parent.mkdir(parents=True)
        md_file.write_text("# Test Command\n\nDo something useful")

        asset = load_asset_from_file(md_file)

        assert asset is not None
        assert asset.name == "test"
        assert asset.asset_type == "command"
        assert asset.content == "# Test Command\n\nDo something useful"

    def test_load_json_file(self, tmp_path):
        """Test loading a JSON file."""
        json_file = tmp_path / "asset.json"
        json_file.write_text(json.dumps({
            "name": "Test Asset",
            "type": "skill",
            "version": "2.0.0",
            "description": "A test skill",
        }))

        asset = load_asset_from_file(json_file)

        assert asset is not None
        assert asset.name == "Test Asset"
        assert asset.asset_type == "skill"
        assert asset.version == "2.0.0"

    def test_load_directory_with_manifest(self, tmp_path):
        """Test loading a directory with manifest.json."""
        asset_dir = tmp_path / "my-skill"
        asset_dir.mkdir()

        manifest = {
            "name": "My Skill",
            "publisher": "test-pub",
            "version": "1.0.0",
            "type": "skill",
            "description": "A skill",
        }
        (asset_dir / "manifest.json").write_text(json.dumps(manifest))

        asset = load_asset_from_file(asset_dir)

        assert asset is not None
        assert asset.name == "My Skill"
        assert asset.publisher == "test-pub"
        assert asset.asset_type == "skill"

    def test_load_directory_without_manifest(self, tmp_path):
        """Test loading a directory without manifest."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        asset = load_asset_from_file(empty_dir)

        assert asset is None

    def test_load_nonexistent_file(self, tmp_path):
        """Test loading a non-existent file."""
        asset = load_asset_from_file(tmp_path / "nonexistent.md")

        assert asset is None

    def test_load_invalid_json(self, tmp_path):
        """Test loading an invalid JSON file."""
        bad_json = tmp_path / "bad.json"
        bad_json.write_text("{ invalid json }")

        asset = load_asset_from_file(bad_json)

        # Should handle gracefully
        assert asset is None or asset.content == "{ invalid json }"

    def test_load_prompt_from_path(self, tmp_path):
        """Test that non-command .md files are loaded as prompts."""
        prompt_file = tmp_path / "prompts" / "template.md"
        prompt_file.parent.mkdir(parents=True)
        prompt_file.write_text("# Prompt Template")

        asset = load_asset_from_file(prompt_file)

        assert asset is not None
        assert asset.asset_type == "prompt"

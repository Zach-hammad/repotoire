"""Unit tests for asset validator service."""

import pytest

from repotoire.api.services.asset_validator import (
    AssetValidationError,
    AssetValidator,
    ValidationError,
    ValidationResult,
)
from repotoire.db.models.marketplace import AssetType


class TestValidationResult:
    """Tests for ValidationResult dataclass."""

    def test_initial_valid(self):
        """Test that result starts valid."""
        result = ValidationResult(valid=True)
        assert result.valid is True
        assert result.errors == []
        assert result.warnings == []

    def test_add_error(self):
        """Test adding an error invalidates result."""
        result = ValidationResult(valid=True)
        result.add_error("field", "message")
        assert result.valid is False
        assert len(result.errors) == 1
        assert result.errors[0].field == "field"
        assert result.errors[0].message == "message"
        assert result.errors[0].severity == "error"

    def test_add_warning_keeps_valid(self):
        """Test adding a warning keeps result valid."""
        result = ValidationResult(valid=True)
        result.add_warning("field", "message")
        assert result.valid is True
        assert len(result.warnings) == 1
        assert result.warnings[0].severity == "warning"


class TestAssetValidator:
    """Tests for AssetValidator class."""

    @pytest.fixture
    def validator(self):
        """Create validator instance."""
        return AssetValidator()

    # ==========================================================================
    # Type validation
    # ==========================================================================

    def test_invalid_asset_type_string(self, validator):
        """Test invalid asset type string."""
        result = validator.validate("invalid", {})
        assert result.valid is False
        assert any("Invalid asset type" in e.message for e in result.errors)

    def test_valid_asset_type_string(self, validator):
        """Test valid asset type as string."""
        content = {
            "prompt": "This is a valid command prompt with enough text",
            "description": "Test command",
        }
        result = validator.validate("command", content)
        assert result.valid is True

    def test_valid_asset_type_enum(self, validator):
        """Test valid asset type as enum."""
        content = {
            "prompt": "This is a valid command prompt with enough text",
            "description": "Test command",
        }
        result = validator.validate(AssetType.COMMAND, content)
        assert result.valid is True

    # ==========================================================================
    # Size validation
    # ==========================================================================

    def test_content_not_dict(self, validator):
        """Test content must be a dictionary."""
        result = validator.validate("command", "not a dict")
        assert result.valid is False
        assert any("must be a dictionary" in e.message for e in result.errors)

    def test_content_too_large(self):
        """Test content size limit."""
        validator = AssetValidator(max_size_bytes=100)
        content = {"data": "x" * 200}
        result = validator.validate("command", content)
        assert result.valid is False
        assert any("exceeds maximum" in e.message for e in result.errors)

    def test_content_within_size_limit(self):
        """Test content within size limit."""
        validator = AssetValidator(max_size_bytes=1000)
        content = {
            "prompt": "Short prompt but valid",
            "description": "Test",
        }
        result = validator.validate("command", content)
        # May have other errors but not size
        assert not any("exceeds maximum" in e.message for e in result.errors)

    # ==========================================================================
    # Security validation
    # ==========================================================================

    def test_forbidden_pattern_eval(self, validator):
        """Test eval() is forbidden."""
        content = {"prompt": "Run eval('code')", "description": "Test"}
        result = validator.validate("command", content)
        assert result.valid is False
        assert any("Forbidden pattern" in e.message for e in result.errors)

    def test_forbidden_pattern_exec(self, validator):
        """Test exec() is forbidden."""
        content = {"prompt": "Use exec( 'code' )", "description": "Test"}
        result = validator.validate("command", content)
        assert result.valid is False
        assert any("Forbidden pattern" in e.message for e in result.errors)

    def test_forbidden_pattern_subprocess(self, validator):
        """Test subprocess module is forbidden."""
        content = {"prompt": "import subprocess\nsubprocess.call()", "description": "Test"}
        result = validator.validate("command", content)
        assert result.valid is False
        assert any("Forbidden pattern" in e.message for e in result.errors)

    def test_forbidden_pattern_os_system(self, validator):
        """Test os.system is forbidden."""
        content = {"prompt": "os.system('rm -rf /')", "description": "Test"}
        result = validator.validate("command", content)
        assert result.valid is False
        assert any("Forbidden pattern" in e.message for e in result.errors)

    def test_safe_content_allowed(self, validator):
        """Test safe content passes security check."""
        content = {
            "prompt": "This is a perfectly safe prompt that doesn't have any bad patterns",
            "description": "Test",
        }
        result = validator.validate("command", content)
        assert result.valid is True

    # ==========================================================================
    # Command validation
    # ==========================================================================

    def test_command_missing_prompt(self, validator):
        """Test command requires prompt."""
        result = validator.validate(AssetType.COMMAND, {"description": "Test"})
        assert result.valid is False
        assert any("must have a 'prompt'" in e.message for e in result.errors)

    def test_command_prompt_too_short(self, validator):
        """Test command prompt minimum length."""
        result = validator.validate(AssetType.COMMAND, {"prompt": "short"})
        assert result.valid is False
        assert any("at least 10 characters" in e.message for e in result.errors)

    def test_command_prompt_too_long(self, validator):
        """Test command prompt maximum length."""
        result = validator.validate(AssetType.COMMAND, {"prompt": "x" * 50001})
        assert result.valid is False
        assert any("at most 50,000 characters" in e.message for e in result.errors)

    def test_command_prompt_not_string(self, validator):
        """Test command prompt must be string."""
        result = validator.validate(AssetType.COMMAND, {"prompt": 123})
        assert result.valid is False
        assert any("must be a string" in e.message for e in result.errors)

    def test_command_arguments_not_list(self, validator):
        """Test command arguments must be list."""
        content = {
            "prompt": "Valid prompt text here",
            "arguments": "not a list",
        }
        result = validator.validate(AssetType.COMMAND, content)
        assert result.valid is False
        assert any("must be a list" in e.message for e in result.errors)

    def test_command_argument_missing_name(self, validator):
        """Test command arguments must have name."""
        content = {
            "prompt": "Valid prompt text here",
            "arguments": [{"required": True}],
        }
        result = validator.validate(AssetType.COMMAND, content)
        assert result.valid is False
        assert any("must have a 'name'" in e.message for e in result.errors)

    def test_command_valid(self, validator):
        """Test valid command passes."""
        content = {
            "prompt": "This is a valid command prompt with enough characters",
            "description": "Test command description",
            "arguments": [
                {"name": "arg1", "required": True, "description": "First arg"},
                {"name": "arg2", "required": False},
            ],
        }
        result = validator.validate(AssetType.COMMAND, content)
        assert result.valid is True

    # ==========================================================================
    # Skill validation
    # ==========================================================================

    def test_skill_missing_name(self, validator):
        """Test skill requires name."""
        result = validator.validate(AssetType.SKILL, {"description": "Test"})
        assert result.valid is False
        assert any("must have a 'name'" in e.message for e in result.errors)

    def test_skill_missing_description(self, validator):
        """Test skill requires description."""
        result = validator.validate(AssetType.SKILL, {"name": "test-skill"})
        assert result.valid is False
        assert any("must have a 'description'" in e.message for e in result.errors)

    def test_skill_invalid_name_format_warning(self, validator):
        """Test skill name format warning."""
        content = {"name": "Test Skill", "description": "Test description"}
        result = validator.validate(AssetType.SKILL, content)
        # Should have a warning about name format
        assert any("lowercase with hyphens" in w.message for w in result.warnings)

    def test_skill_tools_not_list(self, validator):
        """Test skill tools must be list."""
        content = {
            "name": "test-skill",
            "description": "Test",
            "tools": "not a list",
        }
        result = validator.validate(AssetType.SKILL, content)
        assert result.valid is False
        assert any("must be a list" in e.message for e in result.errors)

    def test_skill_tool_missing_name(self, validator):
        """Test skill tools must have name."""
        content = {
            "name": "test-skill",
            "description": "Test",
            "tools": [{"description": "A tool"}],
        }
        result = validator.validate(AssetType.SKILL, content)
        assert result.valid is False
        assert any("must have a 'name'" in e.message for e in result.errors)

    def test_skill_server_missing_type(self, validator):
        """Test skill server must have type."""
        content = {
            "name": "test-skill",
            "description": "Test",
            "server": {"command": "python server.py"},
        }
        result = validator.validate(AssetType.SKILL, content)
        assert result.valid is False
        assert any("must have a 'type'" in e.message for e in result.errors)

    def test_skill_valid(self, validator):
        """Test valid skill passes."""
        content = {
            "name": "test-skill",
            "description": "A test skill for testing",
            "tools": [{"name": "test_tool", "description": "Does testing"}],
            "server": {"type": "stdio", "command": "python server.py"},
        }
        result = validator.validate(AssetType.SKILL, content)
        assert result.valid is True

    # ==========================================================================
    # Style validation
    # ==========================================================================

    def test_style_missing_instructions(self, validator):
        """Test style requires instructions."""
        result = validator.validate(AssetType.STYLE, {})
        assert result.valid is False
        assert any("must have an 'instructions'" in e.message for e in result.errors)

    def test_style_instructions_too_short(self, validator):
        """Test style instructions minimum length."""
        result = validator.validate(AssetType.STYLE, {"instructions": "short"})
        assert result.valid is False
        assert any("at least 20 characters" in e.message for e in result.errors)

    def test_style_examples_not_list(self, validator):
        """Test style examples must be list."""
        content = {
            "instructions": "These are valid style instructions with enough text.",
            "examples": "not a list",
        }
        result = validator.validate(AssetType.STYLE, content)
        assert result.valid is False
        assert any("must be a list" in e.message for e in result.errors)

    def test_style_valid(self, validator):
        """Test valid style passes."""
        content = {
            "instructions": "Always be concise and helpful in responses. Use clear language.",
            "examples": [
                {"input": "How are you?", "output": "I'm doing well, thanks!"},
            ],
        }
        result = validator.validate(AssetType.STYLE, content)
        assert result.valid is True

    # ==========================================================================
    # Hook validation
    # ==========================================================================

    def test_hook_missing_event(self, validator):
        """Test hook requires event."""
        result = validator.validate(AssetType.HOOK, {"command": "echo test"})
        assert result.valid is False
        assert any("must have an 'event'" in e.message for e in result.errors)

    def test_hook_missing_command(self, validator):
        """Test hook requires command."""
        result = validator.validate(AssetType.HOOK, {"event": "PreToolCall"})
        assert result.valid is False
        assert any("must have a 'command'" in e.message for e in result.errors)

    def test_hook_unknown_event_warning(self, validator):
        """Test unknown hook event generates warning."""
        content = {
            "event": "UnknownEvent",
            "command": "echo test",
        }
        result = validator.validate(AssetType.HOOK, content)
        assert any("Unknown event type" in w.message for w in result.warnings)

    def test_hook_matcher_not_dict(self, validator):
        """Test hook matcher must be dict."""
        content = {
            "event": "PreToolCall",
            "command": "echo test",
            "matcher": "not a dict",
        }
        result = validator.validate(AssetType.HOOK, content)
        assert result.valid is False
        assert any("must be a dictionary" in e.message for e in result.errors)

    def test_hook_valid(self, validator):
        """Test valid hook passes."""
        content = {
            "event": "PreToolCall",
            "command": "python check.py",
            "matcher": {"tool": "Write"},
        }
        result = validator.validate(AssetType.HOOK, content)
        assert result.valid is True

    # ==========================================================================
    # Prompt validation
    # ==========================================================================

    def test_prompt_missing_template(self, validator):
        """Test prompt requires template."""
        result = validator.validate(AssetType.PROMPT, {})
        assert result.valid is False
        assert any("must have a 'template'" in e.message for e in result.errors)

    def test_prompt_template_too_short(self, validator):
        """Test prompt template minimum length."""
        result = validator.validate(AssetType.PROMPT, {"template": "short"})
        assert result.valid is False
        assert any("at least 10 characters" in e.message for e in result.errors)

    def test_prompt_variables_not_list(self, validator):
        """Test prompt variables must be list."""
        content = {
            "template": "This is a valid template text",
            "variables": "not a list",
        }
        result = validator.validate(AssetType.PROMPT, content)
        assert result.valid is False
        assert any("must be a list" in e.message for e in result.errors)

    def test_prompt_variable_missing_name(self, validator):
        """Test prompt variables must have name."""
        content = {
            "template": "This is a valid template text",
            "variables": [{"description": "A variable"}],
        }
        result = validator.validate(AssetType.PROMPT, content)
        assert result.valid is False
        assert any("must have a 'name'" in e.message for e in result.errors)

    def test_prompt_undefined_variables_warning(self, validator):
        """Test undefined template variables generate warning."""
        content = {
            "template": "Hello {{name}}, your order {{order_id}} is ready!",
            "variables": [{"name": "name", "description": "User name"}],
        }
        result = validator.validate(AssetType.PROMPT, content)
        assert any("undefined variables: order_id" in w.message for w in result.warnings)

    def test_prompt_valid(self, validator):
        """Test valid prompt passes."""
        content = {
            "template": "Hello {{name}}! Your order {{order_id}} is ready for pickup.",
            "description": "Order notification template",
            "variables": [
                {"name": "name", "description": "Customer name"},
                {"name": "order_id", "description": "Order identifier"},
            ],
        }
        result = validator.validate(AssetType.PROMPT, content)
        assert result.valid is True

    # ==========================================================================
    # validate_or_raise
    # ==========================================================================

    def test_validate_or_raise_valid(self, validator):
        """Test validate_or_raise with valid content."""
        content = {
            "prompt": "This is a valid command prompt",
            "description": "Test",
        }
        # Should not raise
        validator.validate_or_raise("command", content)

    def test_validate_or_raise_invalid(self, validator):
        """Test validate_or_raise raises on invalid content."""
        with pytest.raises(AssetValidationError) as exc_info:
            validator.validate_or_raise("command", {})

        assert "prompt" in str(exc_info.value)
        assert len(exc_info.value.errors) > 0

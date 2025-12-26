"""Asset content validation for marketplace.

This module validates asset content before upload, checking for:
- Size limits
- Security patterns (dangerous code)
- Type-specific validation
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from repotoire.db.models.marketplace import AssetType
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Maximum asset size: 5MB
MAX_ASSET_SIZE_BYTES = 5 * 1024 * 1024

# Forbidden patterns for security
FORBIDDEN_PATTERNS = [
    r"\beval\s*\(",
    r"\bexec\s*\(",
    r"__import__\s*\(",
    r"\bsubprocess\.",
    r"\bos\.system\s*\(",
    r"\bos\.popen\s*\(",
    r"\bos\.exec",
    r"\bos\.spawn",
    r"import\s+subprocess",
    r"from\s+subprocess\s+import",
]

# Compiled patterns for performance
_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in FORBIDDEN_PATTERNS]


@dataclass
class ValidationError:
    """A single validation error."""

    field: str
    message: str
    severity: str = "error"  # error, warning


@dataclass
class ValidationResult:
    """Result of content validation."""

    valid: bool
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)

    def add_error(self, field: str, message: str) -> None:
        """Add a validation error."""
        self.errors.append(ValidationError(field=field, message=message, severity="error"))
        self.valid = False

    def add_warning(self, field: str, message: str) -> None:
        """Add a validation warning."""
        self.warnings.append(ValidationError(field=field, message=message, severity="warning"))


class AssetValidationError(Exception):
    """Raised when asset validation fails."""

    def __init__(self, errors: list[ValidationError]):
        self.errors = errors
        messages = [f"{e.field}: {e.message}" for e in errors]
        super().__init__(f"Validation failed: {'; '.join(messages)}")


class AssetValidator:
    """Validates marketplace asset content before upload."""

    def __init__(self, max_size_bytes: int = MAX_ASSET_SIZE_BYTES):
        """Initialize validator.

        Args:
            max_size_bytes: Maximum content size in bytes.
        """
        self.max_size_bytes = max_size_bytes

    def validate(self, asset_type: AssetType | str, content: dict[str, Any]) -> ValidationResult:
        """Validate asset content.

        Args:
            asset_type: Type of asset (command, skill, style, hook, prompt).
            content: The asset content dictionary.

        Returns:
            ValidationResult with errors and warnings.
        """
        # Normalize asset type
        if isinstance(asset_type, str):
            try:
                asset_type = AssetType(asset_type.lower())
            except ValueError:
                result = ValidationResult(valid=False)
                result.add_error("type", f"Invalid asset type: {asset_type}")
                return result

        result = ValidationResult(valid=True)

        # Check content is a dict
        if not isinstance(content, dict):
            result.add_error("content", "Content must be a dictionary")
            return result

        # Check size
        self._validate_size(content, result)

        # Check for forbidden patterns
        self._validate_security(content, result)

        # Type-specific validation
        if asset_type == AssetType.COMMAND:
            self._validate_command(content, result)
        elif asset_type == AssetType.SKILL:
            self._validate_skill(content, result)
        elif asset_type == AssetType.STYLE:
            self._validate_style(content, result)
        elif asset_type == AssetType.HOOK:
            self._validate_hook(content, result)
        elif asset_type == AssetType.PROMPT:
            self._validate_prompt(content, result)

        return result

    def validate_or_raise(self, asset_type: AssetType | str, content: dict[str, Any]) -> None:
        """Validate and raise exception if invalid.

        Args:
            asset_type: Type of asset.
            content: The asset content dictionary.

        Raises:
            AssetValidationError: If validation fails.
        """
        result = self.validate(asset_type, content)
        if not result.valid:
            raise AssetValidationError(result.errors)

    def _validate_size(self, content: dict[str, Any], result: ValidationResult) -> None:
        """Validate content size."""
        try:
            content_str = json.dumps(content)
            size = len(content_str.encode("utf-8"))
            if size > self.max_size_bytes:
                result.add_error(
                    "size",
                    f"Content size ({size} bytes) exceeds maximum ({self.max_size_bytes} bytes)",
                )
        except (TypeError, ValueError) as e:
            result.add_error("content", f"Content is not JSON-serializable: {e}")

    def _validate_security(self, content: dict[str, Any], result: ValidationResult) -> None:
        """Check for forbidden security patterns."""
        try:
            content_str = json.dumps(content)
        except (TypeError, ValueError):
            return  # Already caught by size validation

        for pattern in _COMPILED_PATTERNS:
            match = pattern.search(content_str)
            if match:
                result.add_error(
                    "security",
                    f"Forbidden pattern detected: '{match.group()}'. "
                    "This pattern is not allowed for security reasons.",
                )

    def _validate_command(self, content: dict[str, Any], result: ValidationResult) -> None:
        """Validate command asset content.

        Expected structure:
        {
            "prompt": "The command prompt text...",
            "description": "Short description",
            "arguments": [{"name": "arg1", "required": True, "description": "..."}]
        }
        """
        # Prompt is required
        prompt = content.get("prompt")
        if not prompt:
            result.add_error("prompt", "Command must have a 'prompt' field")
        elif not isinstance(prompt, str):
            result.add_error("prompt", "Prompt must be a string")
        elif len(prompt) < 10:
            result.add_error("prompt", "Prompt must be at least 10 characters")
        elif len(prompt) > 50000:
            result.add_error("prompt", "Prompt must be at most 50,000 characters")

        # Description is optional but recommended
        description = content.get("description")
        if description and not isinstance(description, str):
            result.add_error("description", "Description must be a string")

        # Arguments validation
        arguments = content.get("arguments")
        if arguments is not None:
            if not isinstance(arguments, list):
                result.add_error("arguments", "Arguments must be a list")
            else:
                for i, arg in enumerate(arguments):
                    if not isinstance(arg, dict):
                        result.add_error(f"arguments[{i}]", "Each argument must be a dictionary")
                    elif "name" not in arg:
                        result.add_error(f"arguments[{i}]", "Argument must have a 'name' field")

    def _validate_skill(self, content: dict[str, Any], result: ValidationResult) -> None:
        """Validate skill asset content.

        Expected structure:
        {
            "name": "skill-name",
            "description": "What this skill does",
            "tools": [{"name": "tool_name", "description": "..."}],
            "server": {"type": "stdio", "command": "..."}  # Optional
        }
        """
        # Name is required
        name = content.get("name")
        if not name:
            result.add_error("name", "Skill must have a 'name' field")
        elif not isinstance(name, str):
            result.add_error("name", "Name must be a string")
        elif not re.match(r"^[a-z][a-z0-9-]*[a-z0-9]$", name) and len(name) > 1:
            result.add_warning(
                "name",
                "Name should be lowercase with hyphens (e.g., 'my-skill')",
            )

        # Description is required
        description = content.get("description")
        if not description:
            result.add_error("description", "Skill must have a 'description' field")
        elif not isinstance(description, str):
            result.add_error("description", "Description must be a string")

        # Tools validation
        tools = content.get("tools")
        if tools is not None:
            if not isinstance(tools, list):
                result.add_error("tools", "Tools must be a list")
            else:
                for i, tool in enumerate(tools):
                    if not isinstance(tool, dict):
                        result.add_error(f"tools[{i}]", "Each tool must be a dictionary")
                    elif "name" not in tool:
                        result.add_error(f"tools[{i}]", "Tool must have a 'name' field")

        # Server config validation (optional)
        server = content.get("server")
        if server is not None:
            if not isinstance(server, dict):
                result.add_error("server", "Server must be a dictionary")
            elif "type" not in server:
                result.add_error("server", "Server must have a 'type' field")

    def _validate_style(self, content: dict[str, Any], result: ValidationResult) -> None:
        """Validate style asset content.

        Expected structure:
        {
            "instructions": "CLAUDE.md style instructions...",
            "examples": [{"input": "...", "output": "..."}]  # Optional
        }
        """
        # Instructions are required
        instructions = content.get("instructions")
        if not instructions:
            result.add_error("instructions", "Style must have an 'instructions' field")
        elif not isinstance(instructions, str):
            result.add_error("instructions", "Instructions must be a string")
        elif len(instructions) < 20:
            result.add_error("instructions", "Instructions must be at least 20 characters")

        # Examples validation (optional)
        examples = content.get("examples")
        if examples is not None:
            if not isinstance(examples, list):
                result.add_error("examples", "Examples must be a list")
            else:
                for i, example in enumerate(examples):
                    if not isinstance(example, dict):
                        result.add_error(f"examples[{i}]", "Each example must be a dictionary")

    def _validate_hook(self, content: dict[str, Any], result: ValidationResult) -> None:
        """Validate hook asset content.

        Expected structure:
        {
            "event": "PreToolCall" | "PostToolCall" | "...",
            "matcher": {...},  # Optional
            "command": "shell command to run"
        }
        """
        # Event is required
        event = content.get("event")
        valid_events = [
            "PreToolCall",
            "PostToolCall",
            "Notification",
            "Stop",
        ]
        if not event:
            result.add_error("event", "Hook must have an 'event' field")
        elif not isinstance(event, str):
            result.add_error("event", "Event must be a string")
        elif event not in valid_events:
            result.add_warning(
                "event",
                f"Unknown event type: {event}. Valid types: {', '.join(valid_events)}",
            )

        # Command is required
        command = content.get("command")
        if not command:
            result.add_error("command", "Hook must have a 'command' field")
        elif not isinstance(command, str):
            result.add_error("command", "Command must be a string")

        # Matcher validation (optional)
        matcher = content.get("matcher")
        if matcher is not None:
            if not isinstance(matcher, dict):
                result.add_error("matcher", "Matcher must be a dictionary")

    def _validate_prompt(self, content: dict[str, Any], result: ValidationResult) -> None:
        """Validate prompt asset content.

        Expected structure:
        {
            "template": "The prompt template with {{variables}}...",
            "variables": [{"name": "var1", "description": "...", "default": "..."}],
            "description": "What this prompt does"
        }
        """
        # Template is required
        template = content.get("template")
        if not template:
            result.add_error("template", "Prompt must have a 'template' field")
        elif not isinstance(template, str):
            result.add_error("template", "Template must be a string")
        elif len(template) < 10:
            result.add_error("template", "Template must be at least 10 characters")

        # Variables validation (optional but recommended)
        variables = content.get("variables")
        if variables is not None:
            if not isinstance(variables, list):
                result.add_error("variables", "Variables must be a list")
            else:
                for i, var in enumerate(variables):
                    if not isinstance(var, dict):
                        result.add_error(f"variables[{i}]", "Each variable must be a dictionary")
                    elif "name" not in var:
                        result.add_error(f"variables[{i}]", "Variable must have a 'name' field")

        # Check for undefined variables in template
        if isinstance(template, str) and isinstance(variables, list):
            # Find all {{var}} patterns
            template_vars = set(re.findall(r"\{\{(\w+)\}\}", template))
            defined_vars = {v.get("name") for v in variables if isinstance(v, dict)}
            undefined = template_vars - defined_vars
            if undefined:
                result.add_warning(
                    "variables",
                    f"Template uses undefined variables: {', '.join(sorted(undefined))}",
                )

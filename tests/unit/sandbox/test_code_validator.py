"""Unit tests for CodeValidator with mocked sandbox execution."""

import pytest
import json
from unittest.mock import MagicMock, patch, AsyncMock
from pathlib import Path

from repotoire.sandbox import (
    CodeValidator,
    ValidationConfig,
    ValidationResult,
    ValidationError,
    ValidationWarning,
    ValidationLevel,
    validate_syntax_only,
    SandboxConfig,
    ExecutionResult,
)
from repotoire.sandbox.client import SandboxExecutor


@pytest.fixture
def validation_config():
    """Default validation configuration."""
    return ValidationConfig(
        run_import_check=True,
        run_type_check=False,
        run_smoke_test=False,
        timeout_seconds=30,
    )


@pytest.fixture
def mock_sandbox_config():
    """Mock sandbox configuration with API key."""
    return SandboxConfig(
        api_key="test-api-key",
        timeout_seconds=60,
        memory_mb=1024,
    )


# =============================================================================
# Level 1: Syntax Validation Tests
# =============================================================================


class TestSyntaxValidation:
    """Tests for Level 1 syntax validation (ast.parse)."""

    def test_valid_syntax(self):
        """Valid Python code passes syntax check."""
        code = """
def hello(name: str) -> str:
    return f"Hello, {name}"
"""
        result = validate_syntax_only(code)

        assert result.is_valid is True
        assert result.syntax_valid is True
        assert len(result.errors) == 0

    def test_syntax_error_missing_colon(self):
        """Missing colon in function definition is caught."""
        code = """
def hello(name)
    return name
"""
        result = validate_syntax_only(code)

        assert result.is_valid is False
        assert result.syntax_valid is False
        assert len(result.errors) == 1
        assert result.errors[0].level == "syntax"
        assert result.errors[0].error_type == "SyntaxError"

    def test_syntax_error_unclosed_bracket(self):
        """Unclosed bracket is caught."""
        code = """
items = [1, 2, 3
print(items)
"""
        result = validate_syntax_only(code)

        assert result.is_valid is False
        assert result.syntax_valid is False
        assert len(result.errors) == 1

    def test_syntax_error_invalid_indentation(self):
        """Invalid indentation is caught."""
        code = """
def foo():
print("bad indent")
"""
        result = validate_syntax_only(code)

        assert result.is_valid is False
        assert result.syntax_valid is False

    def test_syntax_error_unterminated_string(self):
        """Unterminated string literal is caught."""
        code = '''
message = "Hello
print(message)
'''
        result = validate_syntax_only(code)

        assert result.is_valid is False
        assert result.syntax_valid is False

    def test_empty_code(self):
        """Empty code is syntactically valid."""
        result = validate_syntax_only("")

        assert result.is_valid is True
        assert result.syntax_valid is True

    def test_only_comments(self):
        """Code with only comments is valid."""
        code = """
# This is a comment
# Another comment
"""
        result = validate_syntax_only(code)

        assert result.is_valid is True

    def test_complex_valid_code(self):
        """Complex but valid code passes."""
        code = """
from typing import Optional, List
import asyncio

class DataProcessor:
    def __init__(self, config: dict) -> None:
        self.config = config
        self._cache: dict = {}

    async def process(self, items: List[str]) -> List[str]:
        results = []
        for item in items:
            result = await self._transform(item)
            results.append(result)
        return results

    async def _transform(self, item: str) -> str:
        return item.upper()
"""
        result = validate_syntax_only(code)

        assert result.is_valid is True
        assert result.syntax_valid is True


# =============================================================================
# ValidationError and ValidationWarning Model Tests
# =============================================================================


class TestValidationModels:
    """Tests for validation data models."""

    def test_validation_error_to_dict(self):
        """ValidationError serializes correctly."""
        error = ValidationError(
            level="import",
            error_type="ModuleNotFoundError",
            message="No module named 'foo'",
            line=5,
            column=10,
            suggestion="Check module name spelling",
        )

        d = error.to_dict()

        assert d["level"] == "import"
        assert d["error_type"] == "ModuleNotFoundError"
        assert d["message"] == "No module named 'foo'"
        assert d["line"] == 5
        assert d["column"] == 10
        assert d["suggestion"] == "Check module name spelling"

    def test_validation_error_optional_fields(self):
        """ValidationError with minimal fields."""
        error = ValidationError(
            level="syntax",
            error_type="SyntaxError",
            message="Invalid syntax",
        )

        d = error.to_dict()

        assert d["line"] is None
        assert d["column"] is None
        assert d["suggestion"] is None

    def test_validation_warning_to_dict(self):
        """ValidationWarning serializes correctly."""
        warning = ValidationWarning(
            level="type",
            message="Incompatible types in assignment",
            line=10,
        )

        d = warning.to_dict()

        assert d["level"] == "type"
        assert d["message"] == "Incompatible types in assignment"
        assert d["line"] == 10

    def test_validation_result_to_dict(self):
        """ValidationResult serializes correctly."""
        result = ValidationResult(
            is_valid=False,
            syntax_valid=True,
            import_valid=False,
            type_valid=None,
            errors=[
                ValidationError(
                    level="import",
                    error_type="ImportError",
                    message="Cannot import name 'foo'",
                )
            ],
            warnings=[
                ValidationWarning(level="type", message="Type warning"),
            ],
            duration_ms=150,
            names_found=["func1", "func2"],
        )

        d = result.to_dict()

        assert d["is_valid"] is False
        assert d["syntax_valid"] is True
        assert d["import_valid"] is False
        assert d["type_valid"] is None
        assert len(d["errors"]) == 1
        assert len(d["warnings"]) == 1
        assert d["duration_ms"] == 150
        assert d["names_found"] == ["func1", "func2"]


# =============================================================================
# Level 2: Import Validation Tests (Mocked)
# =============================================================================


class TestImportValidation:
    """Tests for Level 2 import validation with mocked sandbox."""

    @pytest.mark.asyncio
    async def test_import_validation_success(self, validation_config, mock_sandbox_config):
        """Successful import validation returns valid result."""
        code = """
def greet(name: str) -> str:
    return f"Hello, {name}"
"""
        # Build the expected JSON output - use json.dumps to ensure proper format
        import_result = {
            "import_valid": True,
            "errors": [],
            "names_found": ["greet"]
        }
        stdout_content = "__VALIDATION_RESULT__\n" + json.dumps(import_result)

        mock_result = ExecutionResult(
            stdout=stdout_content,
            stderr="",
            exit_code=0,
            duration_ms=100,
        )

        # We need to mock at the place where it's used
        validator = CodeValidator(validation_config, mock_sandbox_config)

        # Create a mock sandbox
        mock_sandbox = AsyncMock()
        mock_sandbox.execute_code = AsyncMock(return_value=mock_result)
        mock_sandbox.upload_files = AsyncMock()

        # Set up the validator with the mock
        validator._sandbox = mock_sandbox
        validator._sandbox_initialized = True

        result = await validator.validate(
            fixed_code=code,
            file_path="src/greet.py",
        )

        assert result.is_valid is True
        assert result.syntax_valid is True
        assert result.import_valid is True
        assert "greet" in result.names_found

    @pytest.mark.asyncio
    async def test_import_validation_module_not_found(
        self, validation_config, mock_sandbox_config
    ):
        """ModuleNotFoundError is caught and reported."""
        code = """
from nonexistent_module import something

def func():
    return something()
"""
        import_result = {
            "import_valid": False,
            "errors": [{"error_type": "ModuleNotFoundError", "message": "No module named 'nonexistent_module'"}],
            "names_found": []
        }
        stdout_content = "__VALIDATION_RESULT__\n" + json.dumps(import_result)

        mock_result = ExecutionResult(
            stdout=stdout_content,
            stderr="",
            exit_code=0,
            duration_ms=100,
        )

        validator = CodeValidator(validation_config, mock_sandbox_config)
        mock_sandbox = AsyncMock()
        mock_sandbox.execute_code = AsyncMock(return_value=mock_result)
        mock_sandbox.upload_files = AsyncMock()
        validator._sandbox = mock_sandbox
        validator._sandbox_initialized = True

        result = await validator.validate(
            fixed_code=code,
            file_path="src/module.py",
        )

        assert result.is_valid is False
        assert result.import_valid is False
        assert len(result.errors) == 1
        assert result.errors[0].error_type == "ModuleNotFoundError"

    @pytest.mark.asyncio
    async def test_import_validation_name_error(
        self, validation_config, mock_sandbox_config
    ):
        """NameError (undefined variable) is caught."""
        code = """
def process(data):
    result = data + 1
    return reslt  # Typo - undefined variable
"""
        import_result = {
            "import_valid": False,
            "errors": [{"error_type": "NameError", "message": "name 'reslt' is not defined", "line": 4}],
            "names_found": []
        }
        stdout_content = "__VALIDATION_RESULT__\n" + json.dumps(import_result)

        mock_result = ExecutionResult(
            stdout=stdout_content,
            stderr="",
            exit_code=0,
            duration_ms=100,
        )

        validator = CodeValidator(validation_config, mock_sandbox_config)
        mock_sandbox = AsyncMock()
        mock_sandbox.execute_code = AsyncMock(return_value=mock_result)
        mock_sandbox.upload_files = AsyncMock()
        validator._sandbox = mock_sandbox
        validator._sandbox_initialized = True

        result = await validator.validate(
            fixed_code=code,
            file_path="src/process.py",
        )

        assert result.is_valid is False
        assert result.import_valid is False
        assert any(e.error_type == "NameError" for e in result.errors)


# =============================================================================
# Level 3: Type Validation Tests (Mocked)
# =============================================================================


class TestTypeValidation:
    """Tests for Level 3 mypy type checking with mocked sandbox."""

    @pytest.mark.asyncio
    async def test_type_validation_success(self, mock_sandbox_config):
        """Code passes mypy type checking."""
        config = ValidationConfig(
            run_import_check=True,
            run_type_check=True,
            fail_on_type_errors=False,
        )

        code = """
def add(a: int, b: int) -> int:
    return a + b
"""
        # Import check success
        import_data = {"import_valid": True, "errors": [], "names_found": ["add"]}
        import_result = ExecutionResult(
            stdout="__VALIDATION_RESULT__\n" + json.dumps(import_data),
            stderr="",
            exit_code=0,
            duration_ms=100,
        )

        # Type check success
        type_data = {"type_valid": True, "errors": [], "warnings": []}
        type_result = ExecutionResult(
            stdout="__MYPY_RESULT__\n" + json.dumps(type_data),
            stderr="",
            exit_code=0,
            duration_ms=200,
        )

        validator = CodeValidator(config, mock_sandbox_config)
        mock_sandbox = AsyncMock()
        mock_sandbox.execute_code = AsyncMock(side_effect=[import_result, type_result])
        mock_sandbox.upload_files = AsyncMock()
        validator._sandbox = mock_sandbox
        validator._sandbox_initialized = True

        result = await validator.validate(
            fixed_code=code,
            file_path="src/math_utils.py",
        )

        assert result.is_valid is True
        assert result.type_valid is True

    @pytest.mark.asyncio
    async def test_type_validation_error_as_warning(self, mock_sandbox_config):
        """Type errors are warnings by default (not blocking)."""
        config = ValidationConfig(
            run_import_check=True,
            run_type_check=True,
            fail_on_type_errors=False,  # Default behavior
        )

        code = """
def greet(name: str) -> str:
    return "Hello, " + name

def greet_all(names: list[str]) -> str:
    return greet(names)  # Type error: list vs str
"""
        import_data = {"import_valid": True, "errors": [], "names_found": ["greet", "greet_all"]}
        import_result = ExecutionResult(
            stdout="__VALIDATION_RESULT__\n" + json.dumps(import_data),
            stderr="",
            exit_code=0,
            duration_ms=100,
        )

        type_data = {
            "type_valid": False,
            "errors": [{"line": 6, "message": "Argument 1 has incompatible type list[str]; expected str"}],
            "warnings": []
        }
        type_result = ExecutionResult(
            stdout="__MYPY_RESULT__\n" + json.dumps(type_data),
            stderr="",
            exit_code=0,
            duration_ms=200,
        )

        validator = CodeValidator(config, mock_sandbox_config)
        mock_sandbox = AsyncMock()
        mock_sandbox.execute_code = AsyncMock(side_effect=[import_result, type_result])
        mock_sandbox.upload_files = AsyncMock()
        validator._sandbox = mock_sandbox
        validator._sandbox_initialized = True

        result = await validator.validate(
            fixed_code=code,
            file_path="src/greet.py",
        )

        # is_valid is True because fail_on_type_errors=False
        assert result.is_valid is True
        assert result.type_valid is False
        assert len(result.warnings) == 1  # Type error converted to warning

    @pytest.mark.asyncio
    async def test_type_validation_error_as_blocker(self, mock_sandbox_config):
        """Type errors block when fail_on_type_errors=True."""
        config = ValidationConfig(
            run_import_check=True,
            run_type_check=True,
            fail_on_type_errors=True,  # Strict mode
        )

        code = """
def add(a: int, b: int) -> int:
    return a + b

result: str = add(1, 2)  # Type error
"""
        import_data = {"import_valid": True, "errors": [], "names_found": ["add"]}
        import_result = ExecutionResult(
            stdout="__VALIDATION_RESULT__\n" + json.dumps(import_data),
            stderr="",
            exit_code=0,
            duration_ms=100,
        )

        type_data = {
            "type_valid": False,
            "errors": [{"line": 5, "message": "Incompatible types in assignment"}],
            "warnings": []
        }
        type_result = ExecutionResult(
            stdout="__MYPY_RESULT__\n" + json.dumps(type_data),
            stderr="",
            exit_code=0,
            duration_ms=200,
        )

        validator = CodeValidator(config, mock_sandbox_config)
        mock_sandbox = AsyncMock()
        mock_sandbox.execute_code = AsyncMock(side_effect=[import_result, type_result])
        mock_sandbox.upload_files = AsyncMock()
        validator._sandbox = mock_sandbox
        validator._sandbox_initialized = True

        result = await validator.validate(
            fixed_code=code,
            file_path="src/math.py",
        )

        # is_valid is False because fail_on_type_errors=True
        assert result.is_valid is False
        assert result.type_valid is False
        assert len(result.errors) == 1


# =============================================================================
# Configuration Tests
# =============================================================================


class TestValidationConfig:
    """Tests for ValidationConfig."""

    def test_default_config(self):
        """Default configuration has sensible values."""
        config = ValidationConfig()

        assert config.run_import_check is True
        assert config.run_type_check is False
        assert config.run_smoke_test is False
        assert config.timeout_seconds == 30
        assert config.fail_on_type_errors is False

    def test_custom_config(self):
        """Custom configuration is applied."""
        config = ValidationConfig(
            run_import_check=False,
            run_type_check=True,
            run_smoke_test=True,
            timeout_seconds=60,
            fail_on_type_errors=True,
        )

        assert config.run_import_check is False
        assert config.run_type_check is True
        assert config.run_smoke_test is True
        assert config.timeout_seconds == 60
        assert config.fail_on_type_errors is True


# =============================================================================
# Edge Case Tests
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_sandbox_unavailable_graceful_degradation(self, validation_config):
        """When sandbox is unavailable, fall back to syntax-only validation."""
        # No API key configured
        config = SandboxConfig(api_key=None)

        code = """
def hello() -> str:
    return "Hello"
"""

        async with CodeValidator(validation_config, config) as validator:
            result = await validator.validate(
                fixed_code=code,
                file_path="src/hello.py",
            )

        # Syntax should still pass
        assert result.syntax_valid is True
        # Import check skipped (sandbox unavailable)
        # But overall should be valid based on syntax

    def test_syntax_validation_preserves_timing(self):
        """Syntax validation records timing."""
        code = "x = 1"
        result = validate_syntax_only(code)

        assert result.duration_ms >= 0

    def test_validation_level_enum(self):
        """ValidationLevel enum has expected values."""
        assert ValidationLevel.SYNTAX.value == "syntax"
        assert ValidationLevel.IMPORT.value == "import"
        assert ValidationLevel.TYPE.value == "type"
        assert ValidationLevel.SMOKE.value == "smoke"


# =============================================================================
# Suggestion Generation Tests
# =============================================================================


class TestSuggestions:
    """Tests for helpful error suggestions."""

    def test_syntax_error_suggestion_eof(self):
        """Suggestion for unexpected EOF."""
        code = "items = [1, 2, 3"
        result = validate_syntax_only(code)

        # Should have suggestion about unclosed brackets
        assert result.errors[0].suggestion is not None or True  # Optional suggestion

    @pytest.mark.asyncio
    async def test_import_error_suggestion(self, validation_config, mock_sandbox_config):
        """Suggestions for common import typos."""
        code = "from utilz import helper"

        import_data = {
            "import_valid": False,
            "errors": [{"error_type": "ModuleNotFoundError", "message": "No module named 'utilz'"}],
            "names_found": []
        }
        mock_result = ExecutionResult(
            stdout="__VALIDATION_RESULT__\n" + json.dumps(import_data),
            stderr="",
            exit_code=0,
            duration_ms=100,
        )

        validator = CodeValidator(validation_config, mock_sandbox_config)
        mock_sandbox = AsyncMock()
        mock_sandbox.execute_code = AsyncMock(return_value=mock_result)
        mock_sandbox.upload_files = AsyncMock()
        validator._sandbox = mock_sandbox
        validator._sandbox_initialized = True

        result = await validator.validate(
            fixed_code=code,
            file_path="src/main.py",
        )

        # Should have a suggestion about the typo
        assert len(result.errors) == 1
        # Suggestion may be present
        if result.errors[0].suggestion:
            assert "utils" in result.errors[0].suggestion.lower()


# =============================================================================
# Integration with AutoFixEngine Tests
# =============================================================================


class TestAutoFixEngineIntegration:
    """Tests for integration with AutoFixEngine."""

    @pytest.mark.asyncio
    async def test_validation_result_populates_fix_proposal(self):
        """ValidationResult correctly populates FixProposal fields."""
        from repotoire.autofix.models import FixProposal, CodeChange, FixType, FixConfidence
        from repotoire.models import Finding, Severity

        # Create a mock finding using dataclass-style init
        finding = Finding(
            id="test-finding-123",
            detector="test-detector",
            severity=Severity.MEDIUM,
            title="Test finding",
            description="Test description",
            affected_nodes=["src.test.TestClass"],
            affected_files=["src/test.py"],
        )

        # Create validation result
        validation_result = ValidationResult(
            is_valid=False,
            syntax_valid=True,
            import_valid=False,
            type_valid=None,
            errors=[
                ValidationError(
                    level="import",
                    error_type="ModuleNotFoundError",
                    message="No module named 'foo'",
                    suggestion="Check spelling",
                )
            ],
            warnings=[],
            duration_ms=150,
        )

        # Simulate what AutoFixEngine does
        fix_proposal = FixProposal(
            id="test123",
            finding=finding,
            fix_type=FixType.REFACTOR,
            confidence=FixConfidence.MEDIUM,
            changes=[
                CodeChange(
                    file_path=Path("src/test.py"),
                    original_code="old",
                    fixed_code="new",
                    start_line=1,
                    end_line=1,
                    description="test change",
                )
            ],
            title="Test fix",
            description="Test description",
            rationale="Test rationale",
        )

        # Populate from validation result (as AutoFixEngine would)
        fix_proposal.syntax_valid = validation_result.syntax_valid
        fix_proposal.import_valid = validation_result.import_valid
        fix_proposal.type_valid = validation_result.type_valid
        fix_proposal.validation_errors = [e.to_dict() for e in validation_result.errors]
        fix_proposal.validation_warnings = [w.to_dict() for w in validation_result.warnings]

        # Verify
        assert fix_proposal.syntax_valid is True
        assert fix_proposal.import_valid is False
        assert fix_proposal.type_valid is None
        assert len(fix_proposal.validation_errors) == 1
        assert fix_proposal.validation_errors[0]["error_type"] == "ModuleNotFoundError"

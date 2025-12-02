"""Runtime code validation for AI-generated fixes.

This module provides multi-level validation for code fixes before they are
applied to the user's filesystem. Validation levels:

1. Syntax (Level 1): Fast, local ast.parse() check
2. Import (Level 2): Can the module be imported? (catches ImportError, NameError)
3. Type (Level 3): Does mypy pass? (optional, non-blocking)
4. Smoke (Level 4): Can key functions be called? (optional)

Usage:
    ```python
    from repotoire.sandbox import CodeValidator, ValidationConfig

    config = ValidationConfig(
        run_type_check=True,
        run_smoke_test=False,
    )

    async with CodeValidator(config) as validator:
        result = await validator.validate(
            fixed_code="def greet(name: str) -> str: return f'Hello, {name}'",
            file_path="src/greet.py",
            original_code="def greet(name): return 'Hello, ' + name",
            project_files=[Path("src/utils.py")],
        )

        if result.is_valid:
            print("Fix is safe to apply!")
        else:
            for error in result.errors:
                print(f"{error.level}: {error.message}")
    ```
"""

from __future__ import annotations

import ast
import difflib
import json
import re
import textwrap
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from repotoire.logging_config import get_logger
from repotoire.sandbox.config import SandboxConfig
from repotoire.sandbox.exceptions import (
    SandboxConfigurationError,
    SandboxError,
)

if TYPE_CHECKING:
    from repotoire.sandbox.client import SandboxExecutor

logger = get_logger(__name__)


class ValidationLevel(str, Enum):
    """Validation level identifiers."""

    SYNTAX = "syntax"
    IMPORT = "import"
    TYPE = "type"
    SMOKE = "smoke"


@dataclass
class ValidationError:
    """Details about a validation failure.

    Attributes:
        level: Which validation level failed
        error_type: Python exception type (e.g., "SyntaxError", "ImportError")
        message: Human-readable error description
        line: Line number where error occurred (if available)
        column: Column number (if available)
        suggestion: How to fix the error (if available)
    """

    level: str
    error_type: str
    message: str
    line: Optional[int] = None
    column: Optional[int] = None
    suggestion: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "level": self.level,
            "error_type": self.error_type,
            "message": self.message,
            "line": self.line,
            "column": self.column,
            "suggestion": self.suggestion,
        }


@dataclass
class ValidationWarning:
    """Non-blocking warning from validation.

    Attributes:
        level: Which validation level produced the warning
        message: Warning description
        line: Line number (if available)
    """

    level: str
    message: str
    line: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "level": self.level,
            "message": self.message,
            "line": self.line,
        }


@dataclass
class ValidationResult:
    """Result of code fix validation.

    Attributes:
        is_valid: Whether the fix passed all required validation levels
        syntax_valid: Level 1 - ast.parse() succeeded
        import_valid: Level 2 - Module can be imported (None if skipped)
        type_valid: Level 3 - mypy passes (None if skipped)
        smoke_valid: Level 4 - Functions callable (None if skipped)
        errors: List of blocking validation errors
        warnings: List of non-blocking warnings
        duration_ms: Total validation time in milliseconds
        names_found: Top-level names found in the module after import
    """

    is_valid: bool

    # Validation level results
    syntax_valid: bool
    import_valid: Optional[bool] = None
    type_valid: Optional[bool] = None
    smoke_valid: Optional[bool] = None

    # Error details
    errors: List[ValidationError] = field(default_factory=list)
    warnings: List[ValidationWarning] = field(default_factory=list)

    # Timing
    duration_ms: int = 0

    # Additional info
    names_found: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "is_valid": self.is_valid,
            "syntax_valid": self.syntax_valid,
            "import_valid": self.import_valid,
            "type_valid": self.type_valid,
            "smoke_valid": self.smoke_valid,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
            "duration_ms": self.duration_ms,
            "names_found": self.names_found,
        }


@dataclass
class ValidationConfig:
    """Configuration for code validation.

    Attributes:
        run_import_check: Run Level 2 import validation (default: True)
        run_type_check: Run Level 3 mypy validation (default: False)
        run_smoke_test: Run Level 4 smoke tests (default: False)
        timeout_seconds: Sandbox timeout for validation (default: 30)
        fail_on_type_errors: Treat type errors as failures (default: False)
    """

    run_import_check: bool = True
    run_type_check: bool = False
    run_smoke_test: bool = False
    timeout_seconds: int = 30
    fail_on_type_errors: bool = False


# Validation script template that runs in sandbox
# NOTE: Double curly braces {{ }} are needed to escape literal braces in format strings
VALIDATION_SCRIPT_TEMPLATE = '''
import sys
import json
import traceback
import importlib.util
import os

def validate_module(code: str, module_name: str, file_path: str, project_root: str):
    """Validate that code can be imported and executed."""
    results = {{
        "import_valid": False,
        "errors": [],
        "names_found": [],
    }}

    # Ensure project root is in Python path for project imports
    if project_root and project_root not in sys.path:
        sys.path.insert(0, project_root)

    try:
        # Ensure parent directories exist
        import os
        parent_dir = os.path.dirname(file_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
            # Create __init__.py files for package structure
            parts = parent_dir.split(os.sep)
            current = ""
            for part in parts:
                current = os.path.join(current, part) if current else part
                init_file = os.path.join(current, "__init__.py")
                if not os.path.exists(init_file):
                    open(init_file, "w").close()

        # Write code to temp file
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(code)

        # Try to import it
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None:
            results["errors"].append({{
                "error_type": "ModuleNotFoundError",
                "message": f"Could not create spec for {{file_path}}",
            }})
            print("__VALIDATION_RESULT__")
            print(json.dumps(results))
            return

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        # Success - gather exported names
        results["import_valid"] = True
        results["names_found"] = [n for n in dir(module) if not n.startswith('_')]

    except SyntaxError as e:
        results["errors"].append({{
            "error_type": "SyntaxError",
            "message": str(e.msg) if e.msg else str(e),
            "line": e.lineno,
            "column": e.offset,
        }})
    except ImportError as e:
        results["errors"].append({{
            "error_type": type(e).__name__,
            "message": str(e),
        }})
    except NameError as e:
        results["errors"].append({{
            "error_type": "NameError",
            "message": str(e),
        }})
    except AttributeError as e:
        results["errors"].append({{
            "error_type": "AttributeError",
            "message": str(e),
        }})
    except Exception as e:
        results["errors"].append({{
            "error_type": type(e).__name__,
            "message": str(e),
            "traceback": traceback.format_exc(),
        }})

    print("__VALIDATION_RESULT__")
    print(json.dumps(results))

# Injected values
code = {code!r}
module_name = {module_name!r}
file_path = {file_path!r}
project_root = {project_root!r}

validate_module(code, module_name, file_path, project_root)
'''

# Mypy validation script template
# NOTE: Double curly braces {{ }} are needed to escape literal braces in format strings
MYPY_SCRIPT_TEMPLATE = '''
import subprocess
import json
import tempfile
import os

def run_mypy(code: str, file_path: str):
    """Run mypy on code and return results."""
    results = {{
        "type_valid": True,
        "errors": [],
        "warnings": [],
    }}

    try:
        # Write code to temp file
        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(code)

        # Run mypy with JSON output
        proc = subprocess.run(
            ["python", "-m", "mypy", "--strict", "--no-error-summary", file_path],
            capture_output=True,
            text=True,
            timeout=20
        )

        # Parse mypy output (format: file:line:col: level: message)
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            # Pattern: file.py:10:5: error: Message [error-code]
            match = line.split(":", 3)
            if len(match) >= 4:
                try:
                    line_num = int(match[1])
                    col_num = int(match[2]) if match[2].strip().isdigit() else None
                    rest = match[3].strip()
                    if rest.startswith("error:"):
                        results["type_valid"] = False
                        results["errors"].append({{
                            "line": line_num,
                            "column": col_num,
                            "message": rest[6:].strip(),
                        }})
                    elif rest.startswith("warning:") or rest.startswith("note:"):
                        results["warnings"].append({{
                            "line": line_num,
                            "message": rest,
                        }})
                except (ValueError, IndexError):
                    pass

    except FileNotFoundError:
        results["warnings"].append({{
            "message": "mypy not installed in sandbox",
        }})
    except subprocess.TimeoutExpired:
        results["warnings"].append({{
            "message": "mypy timed out",
        }})
    except Exception as e:
        results["warnings"].append({{
            "message": f"mypy failed: {{{{e}}}}",
        }})

    print("__MYPY_RESULT__")
    print(json.dumps(results))

# Injected values
code = {code!r}
file_path = {file_path!r}

run_mypy(code, file_path)
'''


class CodeValidator:
    """Validate AI-generated code fixes before applying.

    This class provides multi-level validation:
    - Level 1 (Syntax): Local ast.parse() - always run, fast
    - Level 2 (Import): Sandbox import test - catches import/name errors
    - Level 3 (Type): Mypy in sandbox - optional, non-blocking by default
    - Level 4 (Smoke): Call functions with defaults - optional

    Usage:
        ```python
        async with CodeValidator() as validator:
            result = await validator.validate(
                fixed_code="def hello(): return 'hi'",
                file_path="src/hello.py",
            )
            if not result.is_valid:
                print("Fix has errors:", result.errors)
        ```
    """

    def __init__(
        self,
        config: Optional[ValidationConfig] = None,
        sandbox_config: Optional[SandboxConfig] = None,
    ):
        """Initialize validator.

        Args:
            config: Validation configuration
            sandbox_config: E2B sandbox configuration
        """
        self.config = config or ValidationConfig()
        self.sandbox_config = sandbox_config
        self._sandbox: Optional[SandboxExecutor] = None
        self._sandbox_initialized = False

    async def __aenter__(self) -> "CodeValidator":
        """Enter async context - initialize sandbox if needed."""
        if self.config.run_import_check or self.config.run_type_check:
            await self._ensure_sandbox()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit async context - cleanup sandbox."""
        if self._sandbox is not None:
            await self._sandbox.__aexit__(exc_type, exc_val, exc_tb)
            self._sandbox = None
            self._sandbox_initialized = False

    async def _ensure_sandbox(self) -> None:
        """Lazily initialize sandbox if not already done."""
        if self._sandbox_initialized:
            return

        # Import here to avoid circular imports
        from repotoire.sandbox.client import SandboxExecutor

        sandbox_config = self.sandbox_config or SandboxConfig.from_env()

        if not sandbox_config.is_configured:
            logger.warning(
                "E2B not configured - skipping sandbox validation levels. "
                "Set E2B_API_KEY to enable full validation."
            )
            self._sandbox_initialized = True
            return

        # Create sandbox with shorter timeout for validation
        validation_config = SandboxConfig(
            api_key=sandbox_config.api_key,
            timeout_seconds=min(self.config.timeout_seconds, sandbox_config.timeout_seconds),
            memory_mb=sandbox_config.memory_mb,
            cpu_count=sandbox_config.cpu_count,
            sandbox_template=sandbox_config.sandbox_template,
        )

        self._sandbox = SandboxExecutor(validation_config)
        await self._sandbox.__aenter__()
        self._sandbox_initialized = True
        logger.info("CodeValidator sandbox initialized")

    async def validate(
        self,
        fixed_code: str,
        file_path: str,
        original_code: Optional[str] = None,
        project_files: Optional[List[Path]] = None,
        project_root: Optional[Path] = None,
    ) -> ValidationResult:
        """Validate a code fix.

        Args:
            fixed_code: The fixed code to validate
            file_path: Path where the fix will be applied (for module naming)
            original_code: Original code being replaced (for context)
            project_files: Related project files to upload for import resolution
            project_root: Root of the project (for PYTHONPATH)

        Returns:
            ValidationResult with details about what passed/failed
        """
        start_time = time.time()
        errors: List[ValidationError] = []
        warnings: List[ValidationWarning] = []

        # Level 1: Syntax validation (always run, fast)
        syntax_valid = self._validate_syntax(fixed_code, errors)

        if not syntax_valid:
            # Don't proceed if syntax is broken
            duration_ms = int((time.time() - start_time) * 1000)
            return ValidationResult(
                is_valid=False,
                syntax_valid=False,
                errors=errors,
                warnings=warnings,
                duration_ms=duration_ms,
            )

        # Level 2: Import validation (run in sandbox)
        import_valid = None
        names_found: List[str] = []

        if self.config.run_import_check:
            import_valid, import_errors, names_found = await self._validate_import(
                fixed_code, file_path, project_files, project_root
            )
            errors.extend(import_errors)

        # Level 3: Type validation (mypy in sandbox)
        type_valid = None

        if self.config.run_type_check:
            type_valid, type_errors, type_warnings = await self._validate_types(
                fixed_code, file_path
            )
            if self.config.fail_on_type_errors:
                errors.extend(type_errors)
            else:
                # Convert to warnings
                for err in type_errors:
                    warnings.append(
                        ValidationWarning(
                            level="type",
                            message=err.message,
                            line=err.line,
                        )
                    )
            warnings.extend(type_warnings)

        # Level 4: Smoke test (call functions with defaults)
        smoke_valid = None

        if self.config.run_smoke_test and import_valid:
            smoke_valid, smoke_errors = await self._validate_smoke(
                fixed_code, file_path, names_found
            )
            errors.extend(smoke_errors)

        # Calculate final validity
        is_valid = syntax_valid

        if import_valid is not None:
            is_valid = is_valid and import_valid

        if type_valid is not None and self.config.fail_on_type_errors:
            is_valid = is_valid and type_valid

        if smoke_valid is not None:
            is_valid = is_valid and smoke_valid

        duration_ms = int((time.time() - start_time) * 1000)

        result = ValidationResult(
            is_valid=is_valid,
            syntax_valid=syntax_valid,
            import_valid=import_valid,
            type_valid=type_valid,
            smoke_valid=smoke_valid,
            errors=errors,
            warnings=warnings,
            duration_ms=duration_ms,
            names_found=names_found,
        )

        logger.info(
            f"Validation completed: valid={is_valid}, duration={duration_ms}ms",
            extra={
                "syntax_valid": syntax_valid,
                "import_valid": import_valid,
                "type_valid": type_valid,
                "smoke_valid": smoke_valid,
                "error_count": len(errors),
                "warning_count": len(warnings),
            },
        )

        return result

    def _validate_syntax(
        self, code: str, errors: List[ValidationError]
    ) -> bool:
        """Level 1: Validate Python syntax using ast.parse().

        Args:
            code: Code to validate
            errors: List to append errors to

        Returns:
            True if syntax is valid
        """
        try:
            dedented_code = textwrap.dedent(code)
            ast.parse(dedented_code)
            return True
        except SyntaxError as e:
            error = ValidationError(
                level=ValidationLevel.SYNTAX.value,
                error_type="SyntaxError",
                message=e.msg or str(e),
                line=e.lineno,
                column=e.offset,
                suggestion=self._get_syntax_suggestion(e),
            )
            errors.append(error)
            logger.debug(f"Syntax validation failed: {e}")
            return False

    def _get_syntax_suggestion(self, error: SyntaxError) -> Optional[str]:
        """Generate a suggestion for fixing a syntax error."""
        msg = str(error.msg or error).lower()

        if "unexpected eof" in msg:
            return "Check for unclosed brackets, parentheses, or string quotes"
        if "invalid syntax" in msg:
            return "Check for missing colons, parentheses, or operators"
        if "unexpected indent" in msg:
            return "Check indentation - Python uses consistent spacing"
        if "expected ':'" in msg:
            return "Add a colon after if/for/while/def/class statements"
        if "unterminated string" in msg:
            return "Close the string with matching quote character"

        return None

    async def _validate_import(
        self,
        code: str,
        file_path: str,
        project_files: Optional[List[Path]],
        project_root: Optional[Path],
    ) -> tuple[bool, List[ValidationError], List[str]]:
        """Level 2: Validate code can be imported.

        Args:
            code: Code to validate
            file_path: Target file path
            project_files: Related project files
            project_root: Project root for PYTHONPATH

        Returns:
            Tuple of (is_valid, errors, names_found)
        """
        errors: List[ValidationError] = []
        names_found: List[str] = []

        if self._sandbox is None:
            logger.debug("Sandbox not available, skipping import validation")
            return True, errors, names_found

        try:
            # Upload project files if provided
            if project_files:
                await self._sandbox.upload_files(project_files)

            # Create module name from file path
            module_name = self._path_to_module_name(file_path)
            sandbox_file_path = f"/code/{file_path}"
            sandbox_project_root = "/code" if project_root else ""

            # Build validation script
            script = VALIDATION_SCRIPT_TEMPLATE.format(
                code=code,
                module_name=module_name,
                file_path=sandbox_file_path,
                project_root=sandbox_project_root,
            )

            # Execute in sandbox
            result = await self._sandbox.execute_code(
                script, timeout=self.config.timeout_seconds
            )

            # Parse results
            if "__VALIDATION_RESULT__" in result.stdout:
                json_str = result.stdout.split("__VALIDATION_RESULT__")[1].strip()
                data = json.loads(json_str)

                if data.get("import_valid"):
                    names_found = data.get("names_found", [])
                    return True, errors, names_found

                # Extract errors with suggestions
                for err in data.get("errors", []):
                    error = ValidationError(
                        level=ValidationLevel.IMPORT.value,
                        error_type=err.get("error_type", "Error"),
                        message=err.get("message", "Unknown error"),
                        line=err.get("line"),
                        column=err.get("column"),
                        suggestion=self._get_import_suggestion(
                            err.get("error_type", ""),
                            err.get("message", ""),
                        ),
                    )
                    errors.append(error)

                return False, errors, names_found

            # Unexpected output
            if result.error:
                errors.append(
                    ValidationError(
                        level=ValidationLevel.IMPORT.value,
                        error_type="ExecutionError",
                        message=result.error,
                    )
                )
                return False, errors, names_found

            # Could not parse result - assume valid
            logger.warning("Could not parse validation result, assuming valid")
            return True, errors, names_found

        except SandboxError as e:
            logger.warning(f"Sandbox error during import validation: {e}")
            # Return None to indicate import validation was skipped due to sandbox issues
            return None, errors, names_found
        except Exception as e:
            logger.error(f"Unexpected error during import validation: {e}")
            # Return None to indicate import validation was skipped
            return None, errors, names_found

    def _get_import_suggestion(
        self, error_type: str, message: str
    ) -> Optional[str]:
        """Generate a suggestion for fixing an import error."""
        msg_lower = message.lower()

        if error_type == "ModuleNotFoundError":
            # Try to suggest similar module names
            match = re.search(r"no module named ['\"]?(\w+)", msg_lower)
            if match:
                module = match.group(1)
                # Common typos
                suggestions = {
                    "utilz": "Did you mean 'utils'?",
                    "numpuy": "Did you mean 'numpy'?",
                    "panda": "Did you mean 'pandas'?",
                    "reqeusts": "Did you mean 'requests'?",
                }
                return suggestions.get(module, f"Check if '{module}' is installed or spelled correctly")
            return "Check module name spelling and ensure it's installed"

        if error_type == "ImportError":
            if "circular" in msg_lower:
                return "Move import inside function or restructure modules"
            if "cannot import name" in msg_lower:
                return "Check the name exists in the module and isn't imported before definition"
            return "Check the imported name exists in the source module"

        if error_type == "NameError":
            match = re.search(r"name ['\"]?(\w+)", msg_lower)
            if match:
                name = match.group(1)
                return f"Variable '{name}' is used before being defined. Check for typos."
            return "A variable or function is used before being defined"

        if error_type == "AttributeError":
            return "Check the object has the attribute/method you're accessing"

        return None

    async def _validate_types(
        self, code: str, file_path: str
    ) -> tuple[Optional[bool], List[ValidationError], List[ValidationWarning]]:
        """Level 3: Validate code with mypy.

        Args:
            code: Code to validate
            file_path: Target file path

        Returns:
            Tuple of (is_valid, errors, warnings)
        """
        errors: List[ValidationError] = []
        warnings: List[ValidationWarning] = []

        if self._sandbox is None:
            logger.debug("Sandbox not available, skipping type validation")
            return None, errors, warnings

        try:
            sandbox_file_path = f"/code/{file_path}"

            script = MYPY_SCRIPT_TEMPLATE.format(
                code=code,
                file_path=sandbox_file_path,
            )

            result = await self._sandbox.execute_code(
                script, timeout=self.config.timeout_seconds
            )

            if "__MYPY_RESULT__" in result.stdout:
                json_str = result.stdout.split("__MYPY_RESULT__")[1].strip()
                data = json.loads(json_str)

                type_valid = data.get("type_valid", True)

                for err in data.get("errors", []):
                    errors.append(
                        ValidationError(
                            level=ValidationLevel.TYPE.value,
                            error_type="TypeError",
                            message=err.get("message", "Type error"),
                            line=err.get("line"),
                            column=err.get("column"),
                        )
                    )

                for warn in data.get("warnings", []):
                    warnings.append(
                        ValidationWarning(
                            level=ValidationLevel.TYPE.value,
                            message=warn.get("message", ""),
                            line=warn.get("line"),
                        )
                    )

                return type_valid, errors, warnings

            # Could not parse - return no errors
            return None, errors, warnings

        except Exception as e:
            logger.warning(f"Type validation failed: {e}")
            return None, errors, warnings

    async def _validate_smoke(
        self,
        code: str,
        file_path: str,
        names_found: List[str],
    ) -> tuple[bool, List[ValidationError]]:
        """Level 4: Smoke test - call functions with default args.

        Args:
            code: Code to validate
            file_path: Target file path
            names_found: Names exported from the module

        Returns:
            Tuple of (is_valid, errors)
        """
        errors: List[ValidationError] = []

        if self._sandbox is None or not names_found:
            return True, errors

        # TODO: Implement smoke testing
        # This would call each function with None/default values
        # to check for obvious crashes
        logger.debug("Smoke testing not yet implemented")
        return True, errors

    def _path_to_module_name(self, file_path: str) -> str:
        """Convert a file path to a Python module name.

        Args:
            file_path: File path like "src/utils/helper.py"

        Returns:
            Module name like "src.utils.helper"
        """
        # Remove extension
        path = Path(file_path)
        parts = list(path.parts)

        if parts and parts[-1].endswith(".py"):
            parts[-1] = parts[-1][:-3]

        # Remove __init__ from end
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]

        return ".".join(parts) if parts else "module"


def validate_syntax_only(code: str) -> ValidationResult:
    """Synchronous syntax-only validation (Level 1).

    This is a fast, synchronous helper for cases where only syntax
    validation is needed.

    Args:
        code: Code to validate

    Returns:
        ValidationResult with syntax_valid set
    """
    errors: List[ValidationError] = []
    start_time = time.time()

    try:
        dedented_code = textwrap.dedent(code)
        ast.parse(dedented_code)
        syntax_valid = True
    except SyntaxError as e:
        syntax_valid = False
        errors.append(
            ValidationError(
                level=ValidationLevel.SYNTAX.value,
                error_type="SyntaxError",
                message=e.msg or str(e),
                line=e.lineno,
                column=e.offset,
            )
        )

    duration_ms = int((time.time() - start_time) * 1000)

    return ValidationResult(
        is_valid=syntax_valid,
        syntax_valid=syntax_valid,
        errors=errors,
        duration_ms=duration_ms,
    )

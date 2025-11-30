"""Go language handler for auto-fix functionality."""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from repotoire.autofix.languages.base import LanguageHandler
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class GoHandler(LanguageHandler):
    """Handler for Go source code.

    Uses gofmt for syntax validation when available,
    with graceful fallback when not installed.
    """

    def __init__(self):
        """Initialize Go handler and detect available tools."""
        self._gofmt_path: Optional[str] = shutil.which("gofmt")
        self._go_path: Optional[str] = shutil.which("go")

    @property
    def language_name(self) -> str:
        return "Go"

    @property
    def file_extensions(self) -> List[str]:
        return [".go"]

    def validate_syntax(self, code: str) -> bool:
        """Validate Go syntax using gofmt.

        Args:
            code: Go source code to validate

        Returns:
            True if syntax is valid or gofmt unavailable, False on syntax error
        """
        # Try gofmt first (most reliable)
        if self._gofmt_path:
            try:
                return self._validate_with_gofmt(code)
            except Exception as e:
                logger.debug(f"gofmt validation failed: {e}")

        # Try `go fmt` as fallback
        if self._go_path:
            try:
                return self._validate_with_go_fmt(code)
            except Exception as e:
                logger.debug(f"go fmt validation failed: {e}")

        # No validation tool available - assume valid
        logger.debug("No Go validation tool available")
        return True

    def _validate_with_gofmt(self, code: str) -> bool:
        """Validate using gofmt.

        Args:
            code: Source code to validate

        Returns:
            True if valid, False if syntax error
        """
        try:
            result = subprocess.run(
                [self._gofmt_path, "-e"],
                input=code,
                capture_output=True,
                text=True,
                timeout=10,
            )
            # gofmt returns non-zero for syntax errors
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            logger.warning("gofmt validation timed out")
            return True  # Assume valid on timeout

    def _validate_with_go_fmt(self, code: str) -> bool:
        """Validate using `go fmt`.

        Args:
            code: Source code to validate

        Returns:
            True if valid, False if syntax error
        """
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".go", delete=False
        ) as tmp_file:
            tmp_file.write(code)
            tmp_path = tmp_file.name

        try:
            result = subprocess.run(
                [self._go_path, "fmt", tmp_path],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def extract_imports(self, content: str) -> List[str]:
        """Extract import statements from Go code.

        Args:
            content: Go source code

        Returns:
            List of import statements
        """
        imports = []

        # Single import: import "fmt"
        single_import = re.compile(r'^\s*import\s+"([^"]+)"', re.MULTILINE)
        for match in single_import.finditer(content):
            imports.append(f'import "{match.group(1)}"')

        # Grouped imports: import ( "fmt" \n "strings" )
        grouped_import = re.compile(
            r"import\s*\(\s*((?:[^)]+))\s*\)", re.MULTILINE | re.DOTALL
        )
        for match in grouped_import.finditer(content):
            block = match.group(1)
            # Extract individual imports from the block
            # Handles: "pkg", alias "pkg", . "pkg", _ "pkg"
            pkg_pattern = re.compile(r'(?:[\w_.]+\s+)?"([^"]+)"')
            for pkg_match in pkg_pattern.finditer(block):
                imports.append(f'import "{pkg_match.group(1)}"')

        return imports

    def get_system_prompt(self) -> str:
        """Return Go-specific LLM system prompt."""
        return """You are an expert Go developer specializing in code refactoring and quality improvements.

You follow these principles:
- Effective Go guidelines
- Go Code Review Comments
- Go Proverbs (Rob Pike)
- Standard library patterns
- Minimal dependency philosophy

When generating fixes:
- Preserve existing functionality
- Keep code simple and readable
- Handle errors explicitly (no silent failures)
- Use meaningful variable names (short but clear)
- Prefer composition over inheritance
- Use interfaces for abstraction
- Follow gofmt/goimports formatting
- Avoid global state"""

    def get_fix_template(self, fix_type: str) -> str:
        """Return Go-specific fix guidance based on fix type.

        Args:
            fix_type: Type of fix to generate

        Returns:
            Template/guidance string
        """
        templates = {
            "security": """Security Fix Guidelines:
- Use prepared statements for SQL (database/sql)
- Validate and sanitize all user inputs
- Use crypto/rand for random values (not math/rand)
- Set appropriate file permissions
- Use constant-time comparison for secrets
- Validate URLs and file paths
- Use context for cancellation and timeouts""",
            "simplify": """Simplification Guidelines:
- Use early returns to reduce nesting
- Extract complex conditions into functions
- Use switch statements for multiple conditions
- Apply table-driven tests/logic
- Use defer for cleanup operations
- Simplify error handling with helper functions""",
            "refactor": """Refactoring Guidelines:
- Extract functions for reusable logic
- Create interfaces for testability
- Use embedding for composition
- Apply the functional options pattern
- Consider using internal packages for encapsulation
- Split large packages into focused modules""",
            "extract": """Method Extraction Guidelines:
- Create focused, single-purpose functions
- Use receiver methods for type-specific behavior
- Extract to separate file if function is large
- Consider creating a new package for utilities
- Document exported functions with godoc comments
- Use meaningful function names (verb + noun)""",
            "remove": """Dead Code Removal Guidelines:
- Check for reflect-based calls before removing
- Remove unused exported functions carefully
- Clean up unused imports (goimports handles this)
- Remove TODO comments for completed work
- Update tests after removing code
- Check for build tags that might use the code""",
            "documentation": """Documentation Guidelines:
- Add godoc comments for exported items
- Start comments with the name being documented
- Include examples in test files (_test.go)
- Document package purpose in doc.go
- Use // for single-line, /* */ for multi-line
- Include error conditions in function docs""",
            "type_hint": """Type Guidelines:
- Define interfaces where behavior varies
- Use type aliases for clarity
- Create custom types for domain concepts
- Use generics (Go 1.18+) for reusable code
- Prefer small interfaces (1-3 methods)
- Use type assertions with ok pattern""",
        }
        return templates.get(fix_type, templates["refactor"])

    def get_code_block_marker(self) -> str:
        """Return the markdown code block language marker."""
        return "go"

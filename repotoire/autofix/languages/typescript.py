"""TypeScript/JavaScript language handler for auto-fix functionality."""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from repotoire.autofix.languages.base import LanguageHandler
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class TypeScriptHandler(LanguageHandler):
    """Handler for TypeScript and JavaScript source code.

    Uses external tools (esbuild, tsc) for syntax validation when available,
    with graceful fallback when tools are not installed.
    """

    def __init__(self):
        """Initialize TypeScript handler and detect available tools."""
        self._esbuild_path: Optional[str] = shutil.which("esbuild")
        self._tsc_path: Optional[str] = shutil.which("tsc")
        self._node_path: Optional[str] = shutil.which("node")

    @property
    def language_name(self) -> str:
        return "TypeScript"

    @property
    def file_extensions(self) -> List[str]:
        return [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]

    def validate_syntax(self, code: str) -> bool:
        """Validate TypeScript/JavaScript syntax.

        Attempts validation with esbuild first, then tsc.
        Returns True (assumes valid) if no validation tool is available.

        Args:
            code: TypeScript/JavaScript source code to validate

        Returns:
            True if syntax is valid or tool unavailable, False on syntax error
        """
        # Try esbuild first (fastest)
        if self._esbuild_path:
            try:
                return self._validate_with_esbuild(code)
            except Exception as e:
                logger.debug(f"esbuild validation failed: {e}")

        # Try tsc as fallback
        if self._tsc_path:
            try:
                return self._validate_with_tsc(code)
            except Exception as e:
                logger.debug(f"tsc validation failed: {e}")

        # No validation tool available - assume valid
        logger.debug("No TypeScript/JavaScript validation tool available")
        return True

    def _validate_with_esbuild(self, code: str) -> bool:
        """Validate using esbuild.

        Args:
            code: Source code to validate

        Returns:
            True if valid, False if syntax error
        """
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ts", delete=False
        ) as tmp_file:
            tmp_file.write(code)
            tmp_path = tmp_file.name

        try:
            result = subprocess.run(
                [
                    self._esbuild_path,
                    tmp_path,
                    "--bundle",
                    "--outfile=/dev/null",
                    "--log-level=error",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def _validate_with_tsc(self, code: str) -> bool:
        """Validate using TypeScript compiler.

        Args:
            code: Source code to validate

        Returns:
            True if valid, False if syntax error
        """
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ts", delete=False
        ) as tmp_file:
            tmp_file.write(code)
            tmp_path = tmp_file.name

        try:
            result = subprocess.run(
                [
                    self._tsc_path,
                    "--noEmit",
                    "--allowJs",
                    "--checkJs",
                    tmp_path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.returncode == 0
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def extract_imports(self, content: str) -> List[str]:
        """Extract import statements from TypeScript/JavaScript code.

        Args:
            content: Source code

        Returns:
            List of import statements (simplified format)
        """
        imports = []

        # ES6 import patterns
        # import X from 'module'
        # import { X, Y } from 'module'
        # import * as X from 'module'
        # import 'module'
        # import type { X } from 'module'
        import_pattern = re.compile(
            r"^\s*import\s+(?:type\s+)?(?:"
            r"(?:\*\s+as\s+\w+)|"  # import * as X
            r"(?:\{[^}]+\})|"  # import { X, Y }
            r"(?:\w+(?:\s*,\s*\{[^}]+\})?)"  # import X or import X, { Y }
            r")?\s*(?:from\s+)?['\"]([^'\"]+)['\"]",
            re.MULTILINE,
        )

        for match in import_pattern.finditer(content):
            module = match.group(1) if match.group(1) else "side-effect"
            imports.append(f"import from '{module}'")

        # CommonJS require patterns
        # const X = require('module')
        # require('module')
        require_pattern = re.compile(
            r"(?:const|let|var)?\s*(?:\w+|\{[^}]+\})?\s*=?\s*require\(['\"]([^'\"]+)['\"]\)",
            re.MULTILINE,
        )

        for match in require_pattern.finditer(content):
            module = match.group(1)
            imports.append(f"require('{module}')")

        return imports

    def get_system_prompt(self) -> str:
        """Return TypeScript-specific LLM system prompt."""
        return """You are an expert TypeScript/JavaScript developer specializing in code refactoring and quality improvements.

You follow these principles:
- TypeScript strict mode best practices
- ESLint and Prettier conventions
- Functional programming patterns where appropriate
- React/Vue/Angular best practices when applicable
- SOLID principles for object-oriented code

When generating fixes:
- Preserve existing functionality
- Use proper TypeScript types (avoid 'any')
- Prefer const over let, never use var
- Use async/await over raw Promises
- Use optional chaining (?.) and nullish coalescing (??)
- Prefer arrow functions for callbacks
- Use template literals over string concatenation
- Include proper error handling with typed errors"""

    def get_fix_template(self, fix_type: str) -> str:
        """Return TypeScript-specific fix guidance based on fix type.

        Args:
            fix_type: Type of fix to generate

        Returns:
            Template/guidance string
        """
        templates = {
            "security": """Security Fix Guidelines:
- Sanitize user inputs with DOMPurify or similar
- Use parameterized queries for database operations
- Validate URLs before fetch/redirect
- Use Content Security Policy headers
- Avoid eval(), Function(), and innerHTML with user data
- Use crypto.subtle for cryptographic operations
- Validate JWT tokens properly""",
            "simplify": """Simplification Guidelines:
- Use optional chaining (?.) to reduce null checks
- Apply array methods (map, filter, reduce) over loops
- Use destructuring for cleaner parameter handling
- Extract complex conditions into named constants
- Use early returns to reduce nesting""",
            "refactor": """Refactoring Guidelines:
- Extract reusable logic into custom hooks (React)
- Use composition over inheritance
- Apply the Strategy pattern for conditional behavior
- Extract interfaces for better abstraction
- Use dependency injection for testability""",
            "extract": """Method Extraction Guidelines:
- Create pure functions where possible
- Use TypeScript generics for reusable utilities
- Extract React components for UI reuse
- Create custom hooks for stateful logic
- Consider creating a utility module for shared code""",
            "remove": """Dead Code Removal Guidelines:
- Check for dynamic imports before removing
- Remove unused exports from modules
- Clean up unused type definitions
- Remove commented-out code
- Update barrel files (index.ts) after removal""",
            "documentation": """Documentation Guidelines:
- Use JSDoc comments for public APIs
- Document complex types with examples
- Add @param, @returns, @throws annotations
- Include usage examples in comments
- Document component props with descriptions""",
            "type_hint": """Type Guidelines:
- Avoid 'any' - use 'unknown' for truly unknown types
- Use union types for multiple accepted types
- Create interface for object shapes
- Use type guards for runtime checks
- Consider using branded types for validation""",
        }
        return templates.get(fix_type, templates["refactor"])

    def get_code_block_marker(self) -> str:
        """Return the markdown code block language marker."""
        return "typescript"

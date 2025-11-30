"""Python language handler for auto-fix functionality."""

import ast
import textwrap
from typing import List

from repotoire.autofix.languages.base import LanguageHandler


class PythonHandler(LanguageHandler):
    """Handler for Python source code.

    Uses Python's built-in ast module for syntax validation
    and import extraction.
    """

    @property
    def language_name(self) -> str:
        return "Python"

    @property
    def file_extensions(self) -> List[str]:
        return [".py", ".pyi", ".pyw"]

    def validate_syntax(self, code: str) -> bool:
        """Validate Python syntax using ast.parse().

        Args:
            code: Python source code to validate

        Returns:
            True if syntax is valid, False otherwise
        """
        try:
            dedented_code = textwrap.dedent(code)
            ast.parse(dedented_code)
            return True
        except SyntaxError:
            return False

    def extract_imports(self, content: str) -> List[str]:
        """Extract import statements from Python code.

        Args:
            content: Python source code

        Returns:
            List of import statements
        """
        imports = []
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append(f"import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    for alias in node.names:
                        imports.append(f"from {module} import {alias.name}")
        except SyntaxError:
            pass

        return imports

    def get_system_prompt(self) -> str:
        """Return Python-specific LLM system prompt."""
        return """You are an expert Python developer specializing in code refactoring and quality improvements.

You follow these principles:
- PEP 8 style guide for Python code
- PEP 257 for docstring conventions
- Type hints (PEP 484, 585) where appropriate
- SOLID principles for object-oriented design
- Clean code practices: meaningful names, small functions, DRY

When generating fixes:
- Preserve existing functionality
- Minimize changes to accomplish the goal
- Use modern Python idioms (3.9+)
- Include proper type hints for new/modified code
- Add docstrings for public functions/classes if missing
- Consider edge cases and error handling"""

    def get_fix_template(self, fix_type: str) -> str:
        """Return Python-specific fix guidance based on fix type.

        Args:
            fix_type: Type of fix to generate

        Returns:
            Template/guidance string
        """
        templates = {
            "security": """Security Fix Guidelines:
- Use parameterized queries for SQL (never string formatting)
- Use secrets module for cryptographic operations
- Validate and sanitize all user inputs
- Use pathlib for safe path operations
- Avoid pickle for untrusted data
- Use constant-time comparison for secrets (hmac.compare_digest)""",
            "simplify": """Simplification Guidelines:
- Break complex conditions into named boolean variables
- Extract nested logic into helper functions
- Use early returns to reduce nesting
- Replace complex comprehensions with explicit loops if clearer
- Consider using functools.reduce or itertools for complex iterations""",
            "refactor": """Refactoring Guidelines:
- Apply Extract Method for long functions
- Use dataclasses or NamedTuples for data containers
- Replace multiple related parameters with a class
- Apply Replace Conditional with Polymorphism where appropriate
- Consider the Single Responsibility Principle""",
            "extract": """Method Extraction Guidelines:
- Identify cohesive blocks of code
- Create functions with clear, descriptive names
- Pass only necessary parameters
- Return explicit values rather than modifying mutable arguments
- Consider creating a helper class for related extractions""",
            "remove": """Dead Code Removal Guidelines:
- Verify the code is truly unused (check for dynamic calls)
- Remove entire function/class definitions
- Clean up related imports
- Remove any orphaned comments
- Consider if tests exist for the removed code""",
            "documentation": """Documentation Guidelines:
- Use Google-style or NumPy-style docstrings consistently
- Document parameters, return values, and raised exceptions
- Include usage examples for complex functions
- Add module-level docstrings for purpose and usage
- Use inline comments sparingly, only for non-obvious logic""",
            "type_hint": """Type Hint Guidelines:
- Use Optional[T] for parameters that can be None
- Use Union for multiple accepted types
- Use TypeVar for generic functions
- Import types from typing or use built-in generics (3.9+)
- Use Protocol for structural subtyping
- Consider using TypedDict for dictionary shapes""",
        }
        return templates.get(fix_type, templates["refactor"])

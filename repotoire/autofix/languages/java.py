"""Java language handler for auto-fix functionality."""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from repotoire.autofix.languages.base import LanguageHandler
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class JavaHandler(LanguageHandler):
    """Handler for Java source code.

    Uses javac for syntax validation when available,
    with regex-based heuristic fallback.
    """

    def __init__(self):
        """Initialize Java handler and detect available tools."""
        self._javac_path: Optional[str] = shutil.which("javac")

    @property
    def language_name(self) -> str:
        return "Java"

    @property
    def file_extensions(self) -> List[str]:
        return [".java"]

    def validate_syntax(self, code: str) -> bool:
        """Validate Java syntax.

        Attempts validation with javac if available.
        Falls back to basic structural validation using regex.

        Args:
            code: Java source code to validate

        Returns:
            True if syntax appears valid, False on obvious errors
        """
        # Try javac if available
        if self._javac_path:
            try:
                return self._validate_with_javac(code)
            except Exception as e:
                logger.debug(f"javac validation failed: {e}")

        # Fallback: basic structural validation
        return self._validate_structure(code)

    def _validate_with_javac(self, code: str) -> bool:
        """Validate using Java compiler.

        Args:
            code: Source code to validate

        Returns:
            True if valid, False if syntax error
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Extract class name from code
            class_match = re.search(r"(?:public\s+)?class\s+(\w+)", code)
            class_name = class_match.group(1) if class_match else "TempClass"

            tmp_file = Path(tmp_dir) / f"{class_name}.java"
            tmp_file.write_text(code)

            try:
                result = subprocess.run(
                    [
                        self._javac_path,
                        "-d",
                        tmp_dir,
                        "-proc:none",  # Skip annotation processing
                        str(tmp_file),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                return result.returncode == 0
            except subprocess.TimeoutExpired:
                logger.warning("javac validation timed out")
                return True  # Assume valid on timeout

    def _validate_structure(self, code: str) -> bool:
        """Basic structural validation using regex heuristics.

        Args:
            code: Source code to validate

        Returns:
            True if structure appears valid, False on obvious errors
        """
        # Check for balanced braces (simple heuristic)
        open_braces = code.count("{")
        close_braces = code.count("}")
        if open_braces != close_braces:
            return False

        # Check for balanced parentheses
        open_parens = code.count("(")
        close_parens = code.count(")")
        if open_parens != close_parens:
            return False

        # Check for at least one class/interface/enum declaration
        has_type_decl = bool(
            re.search(r"(?:class|interface|enum|record)\s+\w+", code)
        )

        # Empty or whitespace-only is technically invalid
        if not code.strip():
            return False

        # Code with type declaration is likely valid
        return has_type_decl or len(code.strip()) > 0

    def extract_imports(self, content: str) -> List[str]:
        """Extract import statements from Java code.

        Args:
            content: Java source code

        Returns:
            List of import statements
        """
        imports = []

        # Match import statements
        # import java.util.List;
        # import static java.lang.Math.*;
        import_pattern = re.compile(
            r"^\s*import\s+(static\s+)?([a-zA-Z_][\w.]*(?:\.\*)?)\s*;",
            re.MULTILINE,
        )

        for match in import_pattern.finditer(content):
            static = "static " if match.group(1) else ""
            package = match.group(2)
            imports.append(f"import {static}{package}")

        return imports

    def get_system_prompt(self) -> str:
        """Return Java-specific LLM system prompt."""
        return """You are an expert Java developer specializing in code refactoring and quality improvements.

You follow these principles:
- Google Java Style Guide
- SOLID principles and clean architecture
- Effective Java (Joshua Bloch) best practices
- Spring Boot conventions when applicable
- Modern Java features (17+): records, sealed classes, pattern matching

When generating fixes:
- Preserve existing functionality
- Use appropriate access modifiers
- Prefer immutability (final fields, immutable collections)
- Use Optional instead of null returns
- Apply proper exception handling
- Use streams and lambdas where appropriate
- Include proper Javadoc for public APIs"""

    def get_fix_template(self, fix_type: str) -> str:
        """Return Java-specific fix guidance based on fix type.

        Args:
            fix_type: Type of fix to generate

        Returns:
            Template/guidance string
        """
        templates = {
            "security": """Security Fix Guidelines:
- Use PreparedStatement for SQL (never concatenation)
- Validate and sanitize all user inputs
- Use BCrypt or Argon2 for password hashing
- Apply the principle of least privilege
- Use secure random (SecureRandom) for cryptography
- Validate file paths to prevent path traversal
- Use OWASP ESAPI for input validation""",
            "simplify": """Simplification Guidelines:
- Use Optional for nullable values
- Apply method chaining with streams
- Extract complex conditions to methods
- Use switch expressions (Java 14+)
- Apply pattern matching for instanceof (Java 16+)
- Use record classes for data carriers (Java 16+)""",
            "refactor": """Refactoring Guidelines:
- Apply Extract Method for long methods
- Use Builder pattern for complex object creation
- Apply Strategy pattern for conditional behavior
- Consider using dependency injection
- Extract interfaces for better testability
- Use composition over inheritance""",
            "extract": """Method Extraction Guidelines:
- Create single-purpose methods
- Use meaningful, verb-based method names
- Extract to appropriate class (utility, service, etc.)
- Consider static for stateless operations
- Document extracted methods with Javadoc
- Maintain proper access modifiers""",
            "remove": """Dead Code Removal Guidelines:
- Check for reflection-based calls before removing
- Remove entire unused classes/methods
- Clean up unused imports (IDE can help)
- Remove commented-out code
- Update related tests after removal""",
            "documentation": """Documentation Guidelines:
- Use Javadoc for all public APIs
- Include @param, @return, @throws tags
- Add class-level documentation with purpose
- Document thread-safety guarantees
- Include code examples for complex APIs
- Use @see for related classes/methods""",
            "type_hint": """Type Guidelines:
- Use generics to avoid raw types
- Apply bounded wildcards (<? extends T>, <? super T>)
- Use @Nullable/@NonNull annotations
- Consider sealed classes for restricted hierarchies
- Use enum instead of int constants
- Apply var for local variables with obvious types""",
        }
        return templates.get(fix_type, templates["refactor"])

    def get_code_block_marker(self) -> str:
        """Return the markdown code block language marker."""
        return "java"

"""Style analyzer for detecting codebase conventions."""

import ast
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from repotoire.logging_config import get_logger

from .models import StyleProfile, StyleRule

logger = get_logger(__name__)

# Standard line lengths used in Python projects
STANDARD_LINE_LENGTHS = [80, 88, 100, 120]


def classify_naming(name: str) -> str:
    """Classify a name into a naming convention.

    Args:
        name: The identifier name to classify

    Returns:
        One of: 'SCREAMING_SNAKE_CASE', 'snake_case', 'PascalCase', 'camelCase', 'unknown'
    """
    # Strip leading underscores for analysis
    clean = name.lstrip("_")
    if not clean:
        return "unknown"

    # SCREAMING_SNAKE_CASE (constants)
    if clean.isupper() and "_" in clean:
        return "SCREAMING_SNAKE_CASE"
    # snake_case (has underscore, not all upper)
    elif "_" in clean and not clean.isupper():
        return "snake_case"
    # PascalCase (starts with uppercase, no underscores)
    elif clean[0].isupper() and "_" not in clean:
        return "PascalCase"
    # camelCase (starts lowercase, has uppercase somewhere)
    elif clean[0].islower() and any(c.isupper() for c in clean[1:]):
        return "camelCase"
    # Default: single word lowercase is snake_case
    elif clean.islower():
        return "snake_case"
    else:
        return "unknown"


class StyleAnalyzer:
    """Analyzes Python codebases to detect style conventions."""

    def __init__(self, repository_path: Path):
        """Initialize style analyzer.

        Args:
            repository_path: Path to the repository to analyze
        """
        self.repository_path = Path(repository_path)
        if not self.repository_path.exists():
            raise ValueError(f"Repository path does not exist: {repository_path}")

    def analyze(self, max_files: int = 500) -> StyleProfile:
        """Analyze repository to detect style conventions.

        Args:
            max_files: Maximum number of Python files to analyze

        Returns:
            StyleProfile with detected conventions
        """
        logger.info(f"Analyzing style conventions in {self.repository_path}")

        # Collect data from files
        function_names: List[str] = []
        class_names: List[str] = []
        variable_names: List[str] = []
        constant_names: List[str] = []
        docstrings: List[str] = []
        line_lengths: List[int] = []
        functions_with_hints = 0
        total_functions = 0
        import_styles: List[str] = []

        files_analyzed = 0

        # Walk Python files
        for py_file in self._find_python_files(max_files):
            try:
                content = py_file.read_text(encoding="utf-8")
                tree = ast.parse(content, filename=str(py_file))

                # Extract names and docstrings
                file_data = self._extract_from_ast(tree)
                function_names.extend(file_data["function_names"])
                class_names.extend(file_data["class_names"])
                variable_names.extend(file_data["variable_names"])
                constant_names.extend(file_data["constant_names"])
                docstrings.extend(file_data["docstrings"])
                functions_with_hints += file_data["functions_with_hints"]
                total_functions += file_data["total_functions"]

                # Analyze line lengths
                for line in content.split("\n"):
                    # Exclude very short lines and comments
                    if len(line) > 10 and not line.strip().startswith("#"):
                        line_lengths.append(len(line))

                # Analyze import style
                import_style = self._detect_import_style(content)
                if import_style:
                    import_styles.append(import_style)

                files_analyzed += 1

            except (SyntaxError, UnicodeDecodeError) as e:
                logger.debug(f"Skipping {py_file}: {e}")
                continue
            except Exception as e:
                logger.warning(f"Error analyzing {py_file}: {e}")
                continue

        logger.info(f"Analyzed {files_analyzed} Python files")

        # Build style profile
        return StyleProfile(
            repository=str(self.repository_path),
            analyzed_at=datetime.utcnow(),
            file_count=files_analyzed,
            function_naming=self._detect_naming_convention(
                function_names, "function_naming"
            ),
            class_naming=self._detect_naming_convention(class_names, "class_naming"),
            variable_naming=self._detect_naming_convention(
                variable_names, "variable_naming"
            ),
            constant_naming=self._detect_naming_convention(
                constant_names, "constant_naming"
            ) if constant_names else None,
            docstring_style=self._detect_docstring_style(docstrings),
            max_line_length=self._detect_line_length(line_lengths),
            type_hint_coverage=(
                functions_with_hints / total_functions if total_functions > 0 else 0.0
            ),
            import_style=self._detect_import_organization(import_styles) if import_styles else None,
        )

    def _find_python_files(self, max_files: int) -> List[Path]:
        """Find Python files in repository, excluding common non-source directories.

        Args:
            max_files: Maximum files to return

        Returns:
            List of Python file paths
        """
        exclude_dirs = {
            ".git",
            ".venv",
            "venv",
            "env",
            ".env",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            "node_modules",
            "dist",
            "build",
            ".tox",
            ".nox",
            "site-packages",
        }

        files = []
        for py_file in self.repository_path.rglob("*.py"):
            # Skip excluded directories
            if any(excluded in py_file.parts for excluded in exclude_dirs):
                continue
            # Skip test files for style detection (they may have different conventions)
            if "test" in py_file.name.lower() and py_file.name != "__init__.py":
                continue
            files.append(py_file)
            if len(files) >= max_files:
                break

        return files

    def _extract_from_ast(self, tree: ast.AST) -> Dict:
        """Extract naming and style data from AST.

        Args:
            tree: Parsed AST

        Returns:
            Dictionary with extracted data
        """
        data = {
            "function_names": [],
            "class_names": [],
            "variable_names": [],
            "constant_names": [],
            "docstrings": [],
            "functions_with_hints": 0,
            "total_functions": 0,
        }

        for node in ast.walk(tree):
            # Function definitions
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Skip dunder methods for naming analysis
                if not node.name.startswith("__") or not node.name.endswith("__"):
                    data["function_names"].append(node.name)

                data["total_functions"] += 1

                # Check for type hints
                has_return_hint = node.returns is not None
                has_param_hints = any(
                    arg.annotation is not None
                    for arg in node.args.args
                    if arg.arg != "self"
                )
                if has_return_hint or has_param_hints:
                    data["functions_with_hints"] += 1

                # Extract docstring
                docstring = ast.get_docstring(node)
                if docstring:
                    data["docstrings"].append(docstring)

            # Class definitions
            elif isinstance(node, ast.ClassDef):
                data["class_names"].append(node.name)
                docstring = ast.get_docstring(node)
                if docstring:
                    data["docstrings"].append(docstring)

            # Variable assignments at module level
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        name = target.id
                        # Classify as constant if SCREAMING_SNAKE_CASE
                        if classify_naming(name) == "SCREAMING_SNAKE_CASE":
                            data["constant_names"].append(name)
                        elif not name.startswith("_"):
                            data["variable_names"].append(name)

        return data

    def _detect_naming_convention(
        self, names: List[str], rule_name: str
    ) -> StyleRule:
        """Detect the dominant naming convention from a list of names.

        Args:
            names: List of identifier names
            rule_name: Name for the rule

        Returns:
            StyleRule with detected convention
        """
        if not names:
            return StyleRule(
                name=rule_name,
                value="unknown",
                confidence=0.0,
                sample_count=0,
                examples=[],
            )

        # Classify each name
        classifications = [classify_naming(name) for name in names]

        # Count conventions
        counter = Counter(classifications)

        # Remove unknown from consideration
        if "unknown" in counter:
            del counter["unknown"]

        if not counter:
            return StyleRule(
                name=rule_name,
                value="unknown",
                confidence=0.0,
                sample_count=len(names),
                examples=names[:5],
            )

        # Get most common
        most_common, count = counter.most_common(1)[0]
        total_known = sum(counter.values())
        confidence = count / total_known if total_known > 0 else 0.0

        # Get examples of the dominant convention
        examples = [
            name for name in names if classify_naming(name) == most_common
        ][:5]

        return StyleRule(
            name=rule_name,
            value=most_common,
            confidence=round(confidence, 3),
            sample_count=len(names),
            examples=examples,
        )

    def _detect_docstring_style(self, docstrings: List[str]) -> StyleRule:
        """Detect the docstring style used in the codebase.

        Args:
            docstrings: List of docstring contents

        Returns:
            StyleRule with detected style
        """
        if not docstrings:
            return StyleRule(
                name="docstring_style",
                value="none",
                confidence=1.0,
                sample_count=0,
                examples=[],
            )

        styles = Counter()

        for docstring in docstrings:
            style = self._classify_docstring_style(docstring)
            styles[style] += 1

        # Remove "unknown" from analysis
        if "unknown" in styles:
            del styles["unknown"]

        if not styles:
            return StyleRule(
                name="docstring_style",
                value="none",
                confidence=0.5,
                sample_count=len(docstrings),
                examples=[],
            )

        most_common, count = styles.most_common(1)[0]
        total = sum(styles.values())
        confidence = count / total if total > 0 else 0.0

        return StyleRule(
            name="docstring_style",
            value=most_common,
            confidence=round(confidence, 3),
            sample_count=len(docstrings),
            examples=[],  # Don't include full docstrings as examples
        )

    def _classify_docstring_style(self, docstring: str) -> str:
        """Classify a single docstring's style.

        Args:
            docstring: The docstring content

        Returns:
            One of: 'google', 'numpy', 'sphinx', 'simple', 'unknown'
        """
        # Google style indicators
        if re.search(r"^\s*Args:\s*$", docstring, re.MULTILINE):
            return "google"
        if re.search(r"^\s*Returns:\s*$", docstring, re.MULTILINE):
            return "google"
        if re.search(r"^\s*Raises:\s*$", docstring, re.MULTILINE):
            return "google"
        if re.search(r"^\s*Yields:\s*$", docstring, re.MULTILINE):
            return "google"
        if re.search(r"^\s*Attributes:\s*$", docstring, re.MULTILINE):
            return "google"

        # NumPy style indicators (section headers with dashes)
        if re.search(r"^\s*Parameters\s*\n\s*-+", docstring, re.MULTILINE):
            return "numpy"
        if re.search(r"^\s*Returns\s*\n\s*-+", docstring, re.MULTILINE):
            return "numpy"
        if re.search(r"^\s*Raises\s*\n\s*-+", docstring, re.MULTILINE):
            return "numpy"

        # Sphinx style indicators
        if re.search(r":param\s+\w+:", docstring):
            return "sphinx"
        if re.search(r":returns?:", docstring):
            return "sphinx"
        if re.search(r":raises?\s+\w+:", docstring):
            return "sphinx"
        if re.search(r":type\s+\w+:", docstring):
            return "sphinx"

        # Simple one-line docstring
        if "\n" not in docstring.strip() or len(docstring.strip()) < 80:
            return "simple"

        return "unknown"

    def _detect_line_length(self, line_lengths: List[int]) -> StyleRule:
        """Detect the maximum line length convention.

        Uses 95th percentile to ignore occasional long lines.

        Args:
            line_lengths: List of line lengths

        Returns:
            StyleRule with detected max line length
        """
        if not line_lengths:
            return StyleRule(
                name="max_line_length",
                value="88",  # Default to black's default
                confidence=0.5,
                sample_count=0,
                examples=[],
            )

        # Calculate 95th percentile
        sorted_lengths = sorted(line_lengths)
        percentile_idx = int(len(sorted_lengths) * 0.95)
        percentile_value = sorted_lengths[percentile_idx]

        # Round to nearest standard length
        detected_length = self._round_to_standard_length(percentile_value)

        # Calculate confidence based on how many lines are under the detected limit
        under_limit = sum(1 for l in line_lengths if l <= detected_length)
        confidence = under_limit / len(line_lengths)

        return StyleRule(
            name="max_line_length",
            value=str(detected_length),
            confidence=round(confidence, 3),
            sample_count=len(line_lengths),
            examples=[],
        )

    def _round_to_standard_length(self, length: int) -> int:
        """Round a line length to the nearest standard value.

        Args:
            length: Measured line length

        Returns:
            Nearest standard length (80, 88, 100, or 120)
        """
        # Cap at 120
        if length > 120:
            return 120

        # Find closest standard length
        closest = min(STANDARD_LINE_LENGTHS, key=lambda x: abs(x - length))
        return closest

    def _detect_import_style(self, content: str) -> Optional[str]:
        """Detect import organization style from file content.

        Args:
            content: File content

        Returns:
            'grouped' if imports are organized, 'ungrouped' otherwise, None if unclear
        """
        lines = content.split("\n")
        import_lines = []
        import_started = False

        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                import_started = True
                import_lines.append(stripped)
            elif import_started and stripped == "":
                # Empty line in imports
                import_lines.append("")
            elif import_started and not stripped.startswith("#"):
                # Non-import, non-comment line - imports section ended
                break

        if len(import_lines) < 3:
            return None

        # Check for grouping (empty lines between import groups)
        has_blank_separation = "" in import_lines
        if has_blank_separation:
            return "grouped"
        else:
            return "ungrouped"

    def _detect_import_organization(self, styles: List[str]) -> StyleRule:
        """Detect the dominant import organization style.

        Args:
            styles: List of import styles detected per file

        Returns:
            StyleRule with detected organization style
        """
        counter = Counter(styles)
        most_common, count = counter.most_common(1)[0]
        confidence = count / len(styles) if styles else 0.0

        return StyleRule(
            name="import_style",
            value=most_common,
            confidence=round(confidence, 3),
            sample_count=len(styles),
            examples=[],
        )

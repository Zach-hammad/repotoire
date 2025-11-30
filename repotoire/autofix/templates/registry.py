"""Template registry for loading and matching fix templates."""

import fnmatch
import re
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import ValidationError

from repotoire.logging_config import get_logger
from repotoire.autofix.templates.models import (
    FixTemplate,
    PatternType,
    TemplateEvidence,
    TemplateFile,
    TemplateMatch,
)

logger = get_logger(__name__)

# Default template directories (searched in order)
DEFAULT_TEMPLATE_DIRS = [
    Path(".repotoire/fix-templates"),
    Path.home() / ".config/repotoire/fix-templates",
]

# Global registry instance
_registry: Optional["TemplateRegistry"] = None


class TemplateLoadError(Exception):
    """Error loading a template file."""

    def __init__(self, file_path: Path, message: str):
        self.file_path = file_path
        self.message = message
        super().__init__(f"Error loading {file_path}: {message}")


class TemplateRegistry:
    """Registry for loading and matching fix templates."""

    def __init__(self):
        """Initialize empty template registry."""
        self._templates: List[FixTemplate] = []
        self._loaded_files: List[Path] = []

    @property
    def templates(self) -> List[FixTemplate]:
        """Get all loaded templates."""
        return self._templates.copy()

    @property
    def loaded_files(self) -> List[Path]:
        """Get list of loaded template files."""
        return self._loaded_files.copy()

    def load_from_directory(self, path: Path) -> int:
        """Load all YAML template files from a directory.

        Args:
            path: Directory to load templates from

        Returns:
            Number of templates loaded

        Raises:
            TemplateLoadError: If a template file is invalid
        """
        if not path.exists():
            logger.debug(f"Template directory does not exist: {path}")
            return 0

        if not path.is_dir():
            raise TemplateLoadError(path, "Path is not a directory")

        count = 0
        for yaml_file in sorted(path.glob("*.yaml")):
            count += self.load_from_file(yaml_file)

        for yml_file in sorted(path.glob("*.yml")):
            count += self.load_from_file(yml_file)

        return count

    def load_from_file(self, file_path: Path) -> int:
        """Load templates from a single YAML file.

        Args:
            file_path: Path to YAML file

        Returns:
            Number of templates loaded

        Raises:
            TemplateLoadError: If the file is invalid
        """
        if not file_path.exists():
            raise TemplateLoadError(file_path, "File does not exist")

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            if data is None:
                logger.warning(f"Empty template file: {file_path}")
                return 0

            # Parse as TemplateFile
            template_file = TemplateFile(**data)

            # Validate regex patterns before adding
            for template in template_file.templates:
                if template.pattern_type == PatternType.REGEX:
                    try:
                        re.compile(template.pattern)
                    except re.error as e:
                        raise TemplateLoadError(
                            file_path,
                            f"Invalid regex in template '{template.name}': {e}",
                        )

            # Add templates, sorted by priority (descending)
            self._templates.extend(template_file.templates)
            self._sort_by_priority()
            self._loaded_files.append(file_path)

            logger.info(
                f"Loaded {len(template_file.templates)} templates from {file_path}"
            )
            return len(template_file.templates)

        except yaml.YAMLError as e:
            raise TemplateLoadError(file_path, f"YAML parse error: {e}")
        except ValidationError as e:
            # Format pydantic errors helpfully
            errors = []
            for error in e.errors():
                loc = ".".join(str(x) for x in error["loc"])
                errors.append(f"  - {loc}: {error['msg']}")
            raise TemplateLoadError(
                file_path, f"Validation errors:\n" + "\n".join(errors)
            )

    def _sort_by_priority(self) -> None:
        """Sort templates by priority (descending)."""
        self._templates.sort(key=lambda t: t.priority, reverse=True)

    def match(
        self,
        code: str,
        file_path: str,
        language: str = "python",
    ) -> Optional[TemplateMatch]:
        """Find first matching template for given code.

        Args:
            code: Source code to match against
            file_path: Path to the source file (for file_pattern filtering)
            language: Programming language of the code

        Returns:
            TemplateMatch if a template matches, None otherwise
        """
        for template in self._templates:
            match = self._try_match(template, code, file_path, language)
            if match is not None:
                return match

        return None

    def match_all(
        self,
        code: str,
        file_path: str,
        language: str = "python",
    ) -> List[TemplateMatch]:
        """Find all matching templates for given code.

        Args:
            code: Source code to match against
            file_path: Path to the source file
            language: Programming language of the code

        Returns:
            List of all TemplateMatch results
        """
        matches = []
        for template in self._templates:
            match = self._try_match(template, code, file_path, language)
            if match is not None:
                matches.append(match)
        return matches

    def _try_match(
        self,
        template: FixTemplate,
        code: str,
        file_path: str,
        language: str,
    ) -> Optional[TemplateMatch]:
        """Try to match a single template against code.

        Args:
            template: Template to try
            code: Source code
            file_path: Path to the source file
            language: Programming language

        Returns:
            TemplateMatch if successful, None otherwise
        """
        # Check language filter
        if language.lower() not in [lang.lower() for lang in template.languages]:
            return None

        # Check file pattern filter
        if template.file_pattern:
            if not fnmatch.fnmatch(file_path, template.file_pattern):
                return None

        # Perform pattern matching based on type
        if template.pattern_type == PatternType.LITERAL:
            return self._match_literal(template, code)
        elif template.pattern_type == PatternType.REGEX:
            return self._match_regex(template, code)
        elif template.pattern_type == PatternType.AST:
            logger.warning(f"AST pattern type not yet implemented for {template.name}")
            return None

        return None

    def _match_literal(
        self,
        template: FixTemplate,
        code: str,
    ) -> Optional[TemplateMatch]:
        """Match using literal string search.

        Args:
            template: Template with literal pattern
            code: Source code

        Returns:
            TemplateMatch if found, None otherwise
        """
        if template.pattern not in code:
            return None

        # Find position
        start = code.find(template.pattern)
        end = start + len(template.pattern)

        # Replace the matched portion
        fixed_code = code[:start] + template.replacement + code[end:]

        return TemplateMatch(
            template=template,
            original_code=template.pattern,
            fixed_code=template.replacement,
            match_start=start,
            match_end=end,
            capture_groups={},
        )

    def _match_regex(
        self,
        template: FixTemplate,
        code: str,
    ) -> Optional[TemplateMatch]:
        """Match using regex pattern.

        Args:
            template: Template with regex pattern
            code: Source code

        Returns:
            TemplateMatch if found, None otherwise
        """
        try:
            match = re.search(template.pattern, code, re.MULTILINE | re.DOTALL)
            if match is None:
                return None

            # Extract capture groups
            capture_groups = {}
            for i, group in enumerate(match.groups(), start=1):
                if group is not None:
                    capture_groups[str(i)] = group

            # Apply replacement with capture group substitution
            fixed_portion = self._substitute_capture_groups(
                template.replacement, capture_groups
            )

            # Calculate full fixed code
            fixed_code = code[: match.start()] + fixed_portion + code[match.end() :]

            return TemplateMatch(
                template=template,
                original_code=match.group(0),
                fixed_code=fixed_portion,
                match_start=match.start(),
                match_end=match.end(),
                capture_groups=capture_groups,
            )

        except re.error as e:
            logger.error(f"Regex error in template {template.name}: {e}")
            return None

    def _substitute_capture_groups(
        self,
        replacement: str,
        groups: dict[str, str],
    ) -> str:
        """Substitute capture groups in replacement string.

        Supports $1, $2, ..., $9 and ${1}, ${2}, ..., ${9} syntax.

        Args:
            replacement: Replacement string with capture group references
            groups: Dict mapping group numbers to captured values

        Returns:
            Replacement string with groups substituted
        """
        result = replacement

        # Handle ${N} syntax first (more specific)
        for i in range(1, 10):
            key = str(i)
            if key in groups:
                result = result.replace(f"${{{key}}}", groups[key])

        # Handle $N syntax
        for i in range(1, 10):
            key = str(i)
            if key in groups:
                result = result.replace(f"${key}", groups[key])

        return result

    def clear(self) -> None:
        """Clear all loaded templates."""
        self._templates.clear()
        self._loaded_files.clear()


def get_registry(
    template_dirs: Optional[List[Path]] = None,
    force_reload: bool = False,
) -> TemplateRegistry:
    """Get or create the global template registry.

    Lazy-loads templates from default directories on first call.

    Args:
        template_dirs: Optional custom directories to load from
        force_reload: Force reload even if already loaded

    Returns:
        TemplateRegistry instance
    """
    global _registry

    if _registry is None or force_reload:
        _registry = TemplateRegistry()

        dirs_to_load = template_dirs or DEFAULT_TEMPLATE_DIRS

        total = 0
        for template_dir in dirs_to_load:
            try:
                count = _registry.load_from_directory(template_dir)
                total += count
                if count > 0:
                    logger.info(f"Loaded {count} templates from {template_dir}")
            except TemplateLoadError as e:
                logger.error(str(e))

        logger.info(f"Template registry initialized with {total} templates")

    return _registry


def reset_registry() -> None:
    """Reset the global registry (mainly for testing)."""
    global _registry
    _registry = None

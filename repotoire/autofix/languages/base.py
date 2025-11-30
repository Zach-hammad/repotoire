"""Abstract base class for language-specific handlers."""

from abc import ABC, abstractmethod
from typing import List


class LanguageHandler(ABC):
    """Abstract base class for language-specific code handling.

    Provides a common interface for syntax validation, import extraction,
    and LLM prompt generation across different programming languages.
    """

    @property
    @abstractmethod
    def language_name(self) -> str:
        """Return the human-readable name of the language."""
        pass

    @property
    @abstractmethod
    def file_extensions(self) -> List[str]:
        """Return list of file extensions this handler supports."""
        pass

    @abstractmethod
    def validate_syntax(self, code: str) -> bool:
        """Validate that the code is syntactically correct.

        Args:
            code: Source code to validate

        Returns:
            True if syntax is valid, False otherwise
        """
        pass

    @abstractmethod
    def extract_imports(self, content: str) -> List[str]:
        """Extract import statements from the code.

        Args:
            content: Full file content

        Returns:
            List of import statement strings
        """
        pass

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the LLM system prompt for this language.

        Returns:
            System prompt string for GPT-4 or similar LLM
        """
        pass

    @abstractmethod
    def get_fix_template(self, fix_type: str) -> str:
        """Return fix-type-specific guidance for the LLM.

        Args:
            fix_type: Type of fix (e.g., 'refactor', 'security', 'simplify')

        Returns:
            Template/guidance string for the fix type
        """
        pass

    def get_code_block_marker(self) -> str:
        """Return the markdown code block language marker.

        Returns:
            Language identifier for markdown code blocks
        """
        return self.language_name.lower()

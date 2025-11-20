"""Base parser interface."""

from abc import ABC, abstractmethod
from typing import Any, List, Tuple

from repotoire.models import Entity, Relationship


class CodeParser(ABC):
    """Abstract base class for language-specific code parsers."""

    @abstractmethod
    def parse(self, file_path: str) -> Any:
        """Parse a source file into an AST.

        Args:
            file_path: Path to the source file

        Returns:
            Abstract Syntax Tree representation
        """
        pass

    @abstractmethod
    def extract_entities(self, ast: Any, file_path: str) -> List[Entity]:
        """Extract code entities (classes, functions, etc.) from AST.

        Args:
            ast: Abstract Syntax Tree
            file_path: Path to the source file

        Returns:
            List of extracted entities
        """
        pass

    @abstractmethod
    def extract_relationships(
        self, ast: Any, file_path: str, entities: List[Entity]
    ) -> List[Relationship]:
        """Extract relationships (calls, imports, etc.) from AST.

        Args:
            ast: Abstract Syntax Tree
            file_path: Path to the source file
            entities: Previously extracted entities

        Returns:
            List of relationships
        """
        pass

    def process_file(self, file_path: str) -> Tuple[List[Entity], List[Relationship]]:
        """Complete parsing workflow for a single file.

        Args:
            file_path: Path to source file

        Returns:
            Tuple of (entities, relationships)
        """
        ast = self.parse(file_path)
        entities = self.extract_entities(ast, file_path)
        relationships = self.extract_relationships(ast, file_path, entities)
        return entities, relationships

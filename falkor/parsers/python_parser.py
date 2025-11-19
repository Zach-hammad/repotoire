"""Python code parser using AST module."""

import ast
from pathlib import Path
from typing import Any, List, Dict, Optional
import hashlib

from falkor.parsers.base import CodeParser
from falkor.models import (
    Entity,
    FileEntity,
    ClassEntity,
    FunctionEntity,
    Relationship,
    NodeType,
    RelationshipType,
)


class PythonParser(CodeParser):
    """Parser for Python source files."""

    def __init__(self) -> None:
        """Initialize Python parser."""
        self.file_entity: Optional[FileEntity] = None
        self.entity_map: Dict[str, str] = {}  # qualified_name -> entity_id

    def parse(self, file_path: str) -> ast.AST:
        """Parse Python file into AST.

        Args:
            file_path: Path to Python file

        Returns:
            Python AST
        """
        with open(file_path, "r", encoding="utf-8") as f:
            source = f.read()
            return ast.parse(source, filename=file_path)

    def extract_entities(self, tree: ast.AST, file_path: str) -> List[Entity]:
        """Extract entities from Python AST.

        Args:
            tree: Python AST
            file_path: Path to source file

        Returns:
            List of entities (File, Class, Function)
        """
        entities: List[Entity] = []

        # Create file entity
        file_entity = self._create_file_entity(file_path, tree)
        entities.append(file_entity)
        self.file_entity = file_entity

        # Extract classes and functions
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                class_entity = self._extract_class(node, file_path)
                entities.append(class_entity)

                # Extract methods from class
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_entity = self._extract_function(
                            item, file_path, class_name=node.name
                        )
                        entities.append(method_entity)

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Only top-level functions (not methods)
                if self._is_top_level(node, tree):
                    func_entity = self._extract_function(node, file_path)
                    entities.append(func_entity)

        return entities

    def extract_relationships(
        self, tree: ast.AST, file_path: str, entities: List[Entity]
    ) -> List[Relationship]:
        """Extract relationships from Python AST.

        Args:
            tree: Python AST
            file_path: Path to source file
            entities: Extracted entities

        Returns:
            List of relationships (IMPORTS, CALLS, CONTAINS, etc.)
        """
        relationships: List[Relationship] = []

        # Build entity lookup map
        entity_map = {e.qualified_name: e for e in entities}

        # Extract imports
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    # TODO: Create import relationships
                    pass

            elif isinstance(node, ast.ImportFrom):
                # TODO: Handle 'from X import Y' statements
                pass

            elif isinstance(node, ast.Call):
                # TODO: Extract function calls
                pass

        # Create CONTAINS relationships
        file_qualified_name = file_path
        for entity in entities:
            if entity.node_type != NodeType.FILE:
                relationships.append(
                    Relationship(
                        source_id=file_qualified_name,
                        target_id=entity.qualified_name,
                        rel_type=RelationshipType.CONTAINS,
                    )
                )

        return relationships

    def _create_file_entity(self, file_path: str, tree: ast.AST) -> FileEntity:
        """Create file entity.

        Args:
            file_path: Path to file
            tree: AST

        Returns:
            FileEntity
        """
        path_obj = Path(file_path)

        # Calculate file hash
        with open(file_path, "rb") as f:
            file_hash = hashlib.md5(f.read()).hexdigest()

        # Count lines of code
        with open(file_path, "r") as f:
            loc = len([line for line in f if line.strip()])

        return FileEntity(
            name=path_obj.name,
            qualified_name=file_path,
            file_path=file_path,
            line_start=1,
            line_end=loc,
            language="python",
            loc=loc,
            hash=file_hash,
        )

    def _extract_class(self, node: ast.ClassDef, file_path: str) -> ClassEntity:
        """Extract class entity from AST node.

        Args:
            node: ClassDef AST node
            file_path: Path to source file

        Returns:
            ClassEntity
        """
        qualified_name = f"{file_path}::{node.name}"
        docstring = ast.get_docstring(node)

        # Check if abstract
        is_abstract = any(
            isinstance(base, ast.Name) and base.id == "ABC" for base in node.bases
        )

        return ClassEntity(
            name=node.name,
            qualified_name=qualified_name,
            file_path=file_path,
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
            docstring=docstring,
            is_abstract=is_abstract,
            complexity=self._calculate_complexity(node),
        )

    def _extract_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, file_path: str, class_name: Optional[str] = None
    ) -> FunctionEntity:
        """Extract function/method entity from AST node.

        Args:
            node: FunctionDef AST node
            file_path: Path to source file
            class_name: Parent class name if this is a method

        Returns:
            FunctionEntity
        """
        if class_name:
            qualified_name = f"{file_path}::{class_name}.{node.name}"
        else:
            qualified_name = f"{file_path}::{node.name}"

        docstring = ast.get_docstring(node)

        # Extract parameters
        parameters = [arg.arg for arg in node.args.args]

        # Extract return type if annotated
        return_type = None
        if node.returns:
            return_type = ast.unparse(node.returns)

        return FunctionEntity(
            name=node.name,
            qualified_name=qualified_name,
            file_path=file_path,
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
            docstring=docstring,
            parameters=parameters,
            return_type=return_type,
            complexity=self._calculate_complexity(node),
            is_async=isinstance(node, ast.AsyncFunctionDef),
        )

    def _calculate_complexity(self, node: ast.AST) -> int:
        """Calculate cyclomatic complexity of a code block.

        Args:
            node: AST node

        Returns:
            Complexity score
        """
        complexity = 1  # Base complexity

        for child in ast.walk(node):
            # Each decision point adds 1 to complexity
            if isinstance(
                child,
                (
                    ast.If,
                    ast.While,
                    ast.For,
                    ast.ExceptHandler,
                    ast.With,
                    ast.Assert,
                    ast.BoolOp,
                ),
            ):
                complexity += 1
            elif isinstance(child, ast.BoolOp):
                # Each boolean operator adds complexity
                complexity += len(child.values) - 1

        return complexity

    def _is_top_level(self, node: ast.FunctionDef | ast.AsyncFunctionDef, tree: ast.AST) -> bool:
        """Check if function is top-level (not a method).

        Args:
            node: FunctionDef node
            tree: Full AST

        Returns:
            True if top-level function
        """
        # Simple heuristic: if function is in module body, it's top-level
        if hasattr(tree, "body"):
            return node in tree.body
        return False

    def _get_docstring(self, node: ast.AST) -> Optional[str]:
        """Extract docstring from AST node.

        Args:
            node: AST node

        Returns:
            Docstring or None
        """
        return ast.get_docstring(node)

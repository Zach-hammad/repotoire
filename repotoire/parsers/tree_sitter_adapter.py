"""Tree-sitter universal AST adapter for multi-language support.

This module provides a language-agnostic abstraction over tree-sitter parsers,
enabling code reuse across different programming languages.
"""

from dataclasses import dataclass, field
from typing import Any, Iterator, List, Optional


@dataclass
class UniversalASTNode:
    """Language-agnostic AST node wrapper.

    Wraps tree-sitter nodes to provide a uniform API across all programming
    languages, eliminating the need to learn language-specific parser APIs.

    Example:
        >>> adapter = TreeSitterAdapter(python_language())
        >>> tree = adapter.parse("def foo(): pass")
        >>> func_nodes = tree.find_all("function_definition")
        >>> func_nodes[0].get_field("name").text
        'foo'
    """

    node_type: str  # e.g., "function_definition", "class_definition"
    text: str
    start_line: int
    end_line: int
    start_column: int
    end_column: int
    children: List['UniversalASTNode'] = field(default_factory=list)
    language: str = ""
    _raw_node: Any = None  # Language-specific tree-sitter node
    _fields: dict = field(default_factory=dict)  # Named fields cache

    def get_field(self, field_name: str) -> Optional['UniversalASTNode']:
        """Get child node by field name.

        Tree-sitter provides named fields for important node components.
        For example, a function_definition has 'name', 'parameters', and 'body' fields.

        Args:
            field_name: Name of the field (e.g., 'name', 'body', 'parameters')

        Returns:
            Child node if field exists, None otherwise

        Example:
            >>> func_node = tree.find_all("function_definition")[0]
            >>> name_node = func_node.get_field("name")
            >>> name_node.text
            'my_function'
        """
        if not self._fields and self._raw_node:
            # Lazy-load named fields from tree-sitter node
            self._populate_fields()

        return self._fields.get(field_name)

    def _populate_fields(self) -> None:
        """Populate named fields cache from raw tree-sitter node."""
        if not self._raw_node:
            return

        # Iterate through all children and store named fields
        for i, child in enumerate(self._raw_node.children):
            field_name = self._raw_node.field_name_for_child(i)
            if field_name:
                # Find matching wrapped child
                for wrapped_child in self.children:
                    if wrapped_child._raw_node == child:
                        self._fields[field_name] = wrapped_child
                        break

    def find_all(self, node_type: str) -> List['UniversalASTNode']:
        """Find all descendant nodes of a given type.

        Performs a depth-first search through the tree to find all nodes
        matching the specified type.

        Args:
            node_type: Type of node to find (e.g., "function_definition")

        Returns:
            List of all matching descendant nodes

        Example:
            >>> # Find all function definitions in a file
            >>> functions = tree.find_all("function_definition")
            >>> [f.get_field("name").text for f in functions]
            ['foo', 'bar', 'baz']
        """
        results = []

        if self.node_type == node_type:
            results.append(self)

        for child in self.children:
            results.extend(child.find_all(node_type))

        return results

    def find_first(self, node_type: str) -> Optional['UniversalASTNode']:
        """Find first descendant node of a given type.

        Args:
            node_type: Type of node to find

        Returns:
            First matching node or None
        """
        if self.node_type == node_type:
            return self

        for child in self.children:
            result = child.find_first(node_type)
            if result:
                return result

        return None

    def walk(self) -> Iterator['UniversalASTNode']:
        """Iterate through all nodes in the tree (depth-first).

        Yields:
            Each node in the tree in depth-first order

        Example:
            >>> for node in tree.walk():
            ...     print(node.node_type, node.start_line)
        """
        yield self
        for child in self.children:
            yield from child.walk()

    def get_text_range(self, source: str) -> str:
        """Extract the text for this node from source code.

        Args:
            source: Full source code string

        Returns:
            Text content of this node
        """
        lines = source.split('\n')

        if self.start_line == self.end_line:
            # Single line
            line = lines[self.start_line]
            return line[self.start_column:self.end_column]
        else:
            # Multiple lines
            result = []
            for i in range(self.start_line, self.end_line + 1):
                if i >= len(lines):
                    break

                line = lines[i]
                if i == self.start_line:
                    result.append(line[self.start_column:])
                elif i == self.end_line:
                    result.append(line[:self.end_column])
                else:
                    result.append(line)

            return '\n'.join(result)

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"UniversalASTNode(type={self.node_type!r}, "
            f"line={self.start_line}, "
            f"text={self.text[:30]!r}...)"
        )


class TreeSitterAdapter:
    """Wraps tree-sitter Language and translates to UniversalASTNode.

    This adapter provides a uniform interface for parsing source code across
    different programming languages using tree-sitter.

    Example:
        >>> from tree_sitter_python import language
        >>> adapter = TreeSitterAdapter(language())
        >>> tree = adapter.parse("def foo(): pass")
        >>> tree.node_type
        'module'
    """

    def __init__(self, language: Any):
        """Initialize adapter with a tree-sitter language.

        Args:
            language: Tree-sitter Language object or PyCapsule (e.g., from tree_sitter_python.language())
        """
        try:
            from tree_sitter import Language, Parser
        except ImportError:
            raise ImportError(
                "tree-sitter is required for the universal AST adapter. "
                "Install with: pip install tree-sitter"
            )

        # Wrap PyCapsule in Language object if needed (new tree-sitter API)
        if not isinstance(language, Language):
            language = Language(language)

        self.language = language
        # New API: pass language to Parser constructor
        self.parser = Parser(language)
        self._language_name = self._get_language_name()

    def _get_language_name(self) -> str:
        """Extract language name from tree-sitter language object."""
        # Try to get language name from various attributes
        if hasattr(self.language, 'name'):
            return self.language.name
        elif hasattr(self.language, 'language_name'):
            return self.language.language_name
        else:
            # Fallback to parsing from repr or module name
            return "unknown"

    def parse(self, source: str) -> UniversalASTNode:
        """Parse source code and return universal AST.

        Args:
            source: Source code string to parse

        Returns:
            Root UniversalASTNode of the parsed tree

        Example:
            >>> tree = adapter.parse("def foo(): pass")
            >>> tree.find_all("function_definition")
            [UniversalASTNode(type='function_definition', ...)]
        """
        tree = self.parser.parse(bytes(source, 'utf8'))
        return self._wrap_node(tree.root_node, source)

    def parse_file(self, file_path: str) -> UniversalASTNode:
        """Parse a source file and return universal AST.

        Args:
            file_path: Path to source file

        Returns:
            Root UniversalASTNode of the parsed tree
        """
        with open(file_path, 'r', encoding='utf-8') as f:
            source = f.read()

        return self.parse(source)

    def _wrap_node(self, ts_node: Any, source: str) -> UniversalASTNode:
        """Convert tree-sitter node to UniversalASTNode.

        Args:
            ts_node: Tree-sitter node
            source: Original source code (for text extraction)

        Returns:
            Wrapped UniversalASTNode
        """
        # Extract text for this node
        node_text = ts_node.text.decode('utf8') if ts_node.text else ""

        # Wrap all children
        children = [self._wrap_node(child, source) for child in ts_node.children]

        return UniversalASTNode(
            node_type=ts_node.type,
            text=node_text,
            start_line=ts_node.start_point[0],
            end_line=ts_node.end_point[0],
            start_column=ts_node.start_point[1],
            end_column=ts_node.end_point[1],
            children=children,
            language=self._language_name,
            _raw_node=ts_node,
        )

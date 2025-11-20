"""Tree-sitter based Python parser using universal AST adapter.

This module demonstrates the tree-sitter adapter pattern for Python,
serving as a reference implementation for other languages.
"""

from typing import List, Optional
from repotoire.parsers.base_tree_sitter_parser import BaseTreeSitterParser
from repotoire.parsers.tree_sitter_adapter import TreeSitterAdapter, UniversalASTNode
from repotoire.models import Entity, Relationship
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class TreeSitterPythonParser(BaseTreeSitterParser):
    """Python parser using tree-sitter with universal AST adapter.

    Extends BaseTreeSitterParser with Python-specific node type mappings
    and extraction logic. Demonstrates the adapter pattern for easy
    multi-language support.

    Example:
        >>> parser = TreeSitterPythonParser()
        >>> tree = parser.parse("example.py")
        >>> entities = parser.extract_entities(tree, "example.py")
        >>> len(entities)
        10
    """

    def __init__(self):
        """Initialize Python parser with tree-sitter adapter."""
        try:
            from tree_sitter_python import language as python_language
        except ImportError:
            raise ImportError(
                "tree-sitter-python is required for Python parsing. "
                "Install with: pip install tree-sitter-python"
            )

        # Create adapter for Python
        adapter = TreeSitterAdapter(python_language())

        # Python-specific node type mappings
        node_mappings = {
            "class": "class_definition",
            "function": "function_definition",
            "import": "import_statement",
            "import_from": "import_from_statement",
            "call": "call",
        }

        super().__init__(
            adapter=adapter,
            language_name="python",
            node_mappings=node_mappings
        )

    def _extract_docstring(self, node: UniversalASTNode) -> Optional[str]:
        """Extract Python docstring from function or class.

        Python docstrings are the first expression_statement containing
        a string in the function/class body.

        Args:
            node: Function or class node

        Returns:
            Docstring text or None
        """
        body = node.get_field("body")
        if not body:
            return None

        # Look for first expression_statement with a string
        for child in body.children:
            if child.node_type == "expression_statement":
                # Check if it contains a string
                for subchild in child.children:
                    if subchild.node_type == "string":
                        text = subchild.text.strip()
                        # Remove quotes (handles ''', """, ', ")
                        if text.startswith('"""') or text.startswith("'''"):
                            return text[3:-3].strip()
                        elif text.startswith('"') or text.startswith("'"):
                            return text[1:-1].strip()
                        return text

        return None

    def _extract_base_classes(self, class_node: UniversalASTNode) -> List[str]:
        """Extract Python base class names.

        Args:
            class_node: Class definition node

        Returns:
            List of base class names
        """
        # Python uses "argument_list" field for base classes
        bases_node = class_node.get_field("superclasses")
        if not bases_node:
            return []

        base_names = []

        # Iterate through arguments (base classes)
        for child in bases_node.children:
            if child.node_type == "identifier":
                base_names.append(child.text)
            elif child.node_type == "attribute":
                # Handle qualified names like "module.ClassName"
                base_names.append(child.text)

        return base_names

    def _is_async_function(self, func_node: UniversalASTNode) -> bool:
        """Check if Python function is async.

        Args:
            func_node: Function definition node

        Returns:
            True if function uses 'async def'
        """
        # In Python tree-sitter, async functions have "async" modifier
        # or the node type includes "async"
        if "async" in func_node.node_type:
            return True

        # Check for async keyword in children
        for child in func_node.children:
            if child.node_type == "async" or child.text == "async":
                return True

        return False

    def _extract_import_names(self, import_node: UniversalASTNode) -> List[str]:
        """Extract module names from Python import statements.

        Handles:
        - `import foo` -> ["foo"]
        - `import foo as bar` -> ["foo"]
        - `import foo.bar` -> ["foo.bar"]
        - `from foo import bar` -> ["foo.bar"]
        - `from foo import bar, baz` -> ["foo.bar", "foo.baz"]
        - `from . import foo` -> [".<current_package>.foo"]
        - `from ..module import foo` -> ["..<parent_package>.module.foo"]

        Args:
            import_node: Import statement or import_from_statement node

        Returns:
            List of fully qualified module names
        """
        module_names = []

        if import_node.node_type == "import_statement":
            # Handle: import foo, import foo as bar, import foo.bar
            for child in import_node.children:
                if child.node_type == "dotted_name":
                    # import foo.bar.baz
                    module_names.append(child.text)
                elif child.node_type == "aliased_import":
                    # import foo as bar - we want the real name (foo)
                    name_node = child.get_field("name")
                    if name_node:
                        module_names.append(name_node.text)
                elif child.node_type == "identifier":
                    # import foo
                    module_names.append(child.text)

        elif import_node.node_type == "import_from_statement":
            # Handle: from foo import bar
            module_node = import_node.get_field("module_name")
            module_name = ""

            if module_node:
                if module_node.node_type == "dotted_name":
                    module_name = module_node.text
                elif module_node.node_type == "identifier":
                    module_name = module_node.text
            else:
                # Handle relative imports: from . import foo, from .. import foo
                # Look for relative_import field or dots
                for child in import_node.children:
                    if child.node_type == "relative_import":
                        # This is a relative import - mark it but we can't fully resolve it
                        module_name = child.text  # Will be ".", "..", etc.

            # Get imported names
            # Look for name or alias fields
            for child in import_node.children:
                if child.node_type == "dotted_name" and child != module_node:
                    # from foo import bar.baz
                    imported = child.text
                    if module_name:
                        module_names.append(f"{module_name}.{imported}")
                    else:
                        module_names.append(imported)
                elif child.node_type == "identifier" and "import" not in child.text:
                    # from foo import bar
                    if module_name:
                        module_names.append(f"{module_name}.{child.text}")
                    else:
                        module_names.append(child.text)
                elif child.node_type == "aliased_import":
                    # from foo import bar as baz - we want bar, not baz
                    name_node = child.get_field("name")
                    if name_node:
                        if module_name:
                            module_names.append(f"{module_name}.{name_node.text}")
                        else:
                            module_names.append(name_node.text)

            # If we only got the module name (from foo import *), use it
            if not module_names and module_name:
                module_names.append(module_name)

        return list(set(module_names))

    def _extract_class(self, class_node: UniversalASTNode, file_path: str):
        """Extract ClassEntity from class node (handles decorated_definition).

        Python tree-sitter wraps decorated classes in decorated_definition nodes.
        Extract decorators from there if present.

        Args:
            class_node: Class definition node (might be decorated_definition or class_definition)
            file_path: Path to source file

        Returns:
            ClassEntity or None if extraction fails
        """
        actual_class_node = class_node

        # If this is a decorated_definition, extract decorators and get the actual class
        decorators = []
        if class_node.node_type == "decorated_definition":
            # Extract decorators from decorated_definition
            for child in class_node.children:
                if child.node_type == "decorator":
                    decorator_text = child.text.strip()
                    if decorator_text.startswith("@"):
                        decorator_text = decorator_text[1:]
                    if "(" in decorator_text:
                        decorator_text = decorator_text.split("(")[0].strip()
                    decorators.append(decorator_text)
                elif child.node_type == "class_definition":
                    actual_class_node = child

        # Now use base class extraction with the actual class node
        result = super()._extract_class(actual_class_node, file_path)

        # Override decorators if we found any
        if result and decorators:
            result.decorators = decorators
            result.is_dataclass = "dataclass" in decorators

        return result

    def _extract_function(self, func_node: UniversalASTNode, file_path: str, parent_class=None):
        """Extract FunctionEntity from function node (handles decorated_definition).

        Python tree-sitter wraps decorated functions in decorated_definition nodes.

        Args:
            func_node: Function definition node (might be decorated_definition or function_definition)
            file_path: Path to source file
            parent_class: Qualified name of parent class if this is a method

        Returns:
            FunctionEntity or None if extraction fails
        """
        actual_func_node = func_node
        decorators = []

        # If this is a decorated_definition, extract decorators
        if func_node.node_type == "decorated_definition":
            for child in func_node.children:
                if child.node_type == "decorator":
                    decorator_text = child.text.strip()
                    if decorator_text.startswith("@"):
                        decorator_text = decorator_text[1:]
                    if "(" in decorator_text:
                        decorator_text = decorator_text.split("(")[0].strip()
                    decorators.append(decorator_text)
                elif child.node_type == "function_definition":
                    actual_func_node = child

        # Use base extraction
        result = super()._extract_function(actual_func_node, file_path, parent_class)

        # Override decorators if we found any
        if result and decorators:
            result.decorators = decorators
            result.is_static = "staticmethod" in decorators
            result.is_classmethod = "classmethod" in decorators
            result.is_property = "property" in decorators

        return result

    def _find_classes(self, tree: UniversalASTNode) -> List[UniversalASTNode]:
        """Find all class definitions, including decorated ones.

        Returns decorated_definition nodes when present, otherwise class_definition.

        Args:
            tree: UniversalASTNode tree

        Returns:
            List of class nodes (decorated_definition or class_definition)
        """
        classes = []
        seen_class_definitions = set()

        # First pass: find decorated_definition nodes containing classes
        for node in tree.walk():
            if node.node_type == "decorated_definition":
                # Check if it contains a class_definition
                for child in node.children:
                    if child.node_type == "class_definition":
                        classes.append(node)  # Return the decorated_definition wrapper
                        seen_class_definitions.add(id(child))  # Mark this class as seen
                        break

        # Second pass: find standalone class_definition nodes (not wrapped)
        for node in tree.walk():
            if node.node_type == "class_definition" and id(node) not in seen_class_definitions:
                classes.append(node)

        return classes

    def _find_methods(self, class_node: UniversalASTNode) -> List[UniversalASTNode]:
        """Find all functions/methods in a class, including decorated ones.

        Overrides base implementation to handle Python's decorated_definition wrappers.

        Args:
            class_node: Class node (might be decorated_definition or class_definition)

        Returns:
            List of function nodes (decorated_definition or function_definition)
        """
        # If this is a decorated_definition, get the actual class_definition
        if class_node.node_type == "decorated_definition":
            for child in class_node.children:
                if child.node_type == "class_definition":
                    class_node = child
                    break

        functions = []
        seen_function_definitions = set()

        # Walk the class body looking for functions
        for node in class_node.walk():
            if node.node_type == "decorated_definition":
                # Check if it contains a function_definition
                for child in node.children:
                    if child.node_type == "function_definition":
                        functions.append(node)  # Return decorated wrapper
                        seen_function_definitions.add(id(child))
                        break
            elif node.node_type == "function_definition" and id(node) not in seen_function_definitions:
                # Only add if not part of a decorated_definition
                functions.append(node)

        return functions

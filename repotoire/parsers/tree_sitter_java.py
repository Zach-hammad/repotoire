"""Tree-sitter based Java parser using universal AST adapter.

This module provides Java parsing support using tree-sitter,
following the same adapter pattern as the Python and TypeScript parsers.
"""

from typing import List, Optional
from repotoire.parsers.base_tree_sitter_parser import BaseTreeSitterParser
from repotoire.parsers.tree_sitter_adapter import TreeSitterAdapter, UniversalASTNode
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class TreeSitterJavaParser(BaseTreeSitterParser):
    """Java parser using tree-sitter with universal AST adapter.

    Extends BaseTreeSitterParser with Java-specific node type mappings
    and extraction logic. Supports both class files (.java) and handles
    Java-specific constructs like interfaces, annotations, and Javadoc.

    Example:
        >>> parser = TreeSitterJavaParser()
        >>> tree = parser.parse("Example.java")
        >>> entities = parser.extract_entities(tree, "Example.java")
        >>> len(entities)
        10
    """

    def __init__(self):
        """Initialize Java parser with tree-sitter adapter."""
        try:
            from tree_sitter_java import language as java_language
        except ImportError:
            raise ImportError(
                "tree-sitter-java is required for Java parsing. "
                "Install with: pip install tree-sitter-java"
            )

        # Create adapter for Java
        adapter = TreeSitterAdapter(java_language())

        # Java-specific node type mappings
        node_mappings = {
            "class": "class_declaration",
            "interface": "interface_declaration",
            "function": "method_declaration",  # Java has methods, not functions
            "method": "method_declaration",
            "constructor": "constructor_declaration",
            "annotation": "annotation",
            "import": "import_declaration",
            "call": "method_invocation",
        }

        super().__init__(
            adapter=adapter,
            language_name="java",
            node_mappings=node_mappings
        )

    def _find_classes(self, tree: UniversalASTNode) -> List[UniversalASTNode]:
        """Find all class and interface declarations in Java.

        Java classes can be:
        - class_declaration: `class Foo {}`
        - interface_declaration: `interface IFoo {}`
        - enum_declaration: `enum Status {}`
        - record_declaration (Java 14+): `record Point(int x, int y) {}`

        Args:
            tree: UniversalASTNode tree

        Returns:
            List of class/interface declaration nodes
        """
        classes = []
        seen = set()

        # Java class-like constructs
        class_types = {
            "class_declaration",
            "interface_declaration",
            "enum_declaration",
            "record_declaration",
        }

        for node in tree.walk():
            if node.node_type in class_types and id(node) not in seen:
                classes.append(node)
                seen.add(id(node))

        return classes

    def _find_functions(self, tree: UniversalASTNode) -> List[UniversalASTNode]:
        """Find all method declarations in Java.

        In Java, there are no top-level functions - all methods must be
        inside classes. This method returns an empty list as methods are
        extracted via _find_methods when processing classes.

        Args:
            tree: UniversalASTNode tree

        Returns:
            Empty list (Java doesn't have top-level functions)
        """
        # Java doesn't have top-level functions - all methods are inside classes
        return []

    def _find_methods(self, class_node: UniversalASTNode) -> List[UniversalASTNode]:
        """Find all method definitions inside a Java class.

        Includes:
        - method_declaration: regular methods
        - constructor_declaration: constructors

        Args:
            class_node: Class declaration node

        Returns:
            List of method/constructor definition nodes
        """
        methods = []

        # Get class body (handles class_body, interface_body, enum_body)
        body = class_node.get_field("body")
        if not body:
            return methods

        for child in body.children:
            if child.node_type in ("method_declaration", "constructor_declaration"):
                methods.append(child)
            # Handle enum body declarations (methods in enums are nested in enum_body_declarations)
            elif child.node_type == "enum_body_declarations":
                for subchild in child.children:
                    if subchild.node_type in ("method_declaration", "constructor_declaration"):
                        methods.append(subchild)

        return methods

    def _extract_docstring(self, node: UniversalASTNode) -> Optional[str]:
        """Extract Javadoc comment from Java method or class.

        Javadoc comments precede the class/method definition and look like:
        /**
         * Description here
         * @param name Description
         * @return Description
         */

        Args:
            node: Method or class node

        Returns:
            Javadoc text or None
        """
        # Access the raw tree-sitter node to find preceding comments
        raw_node = node._raw_node
        if not raw_node:
            return None

        # Look for comment node immediately preceding this node
        prev_sibling = raw_node.prev_sibling
        while prev_sibling:
            node_type = prev_sibling.type

            # In Java, Javadoc is a "block_comment" starting with /**
            if node_type == "block_comment":
                comment_text = prev_sibling.text
                if isinstance(comment_text, bytes):
                    comment_text = comment_text.decode("utf-8")

                # Check if it's a Javadoc comment (starts with /**)
                if comment_text.strip().startswith("/**"):
                    return self._clean_javadoc(comment_text)

                # Regular block comment, keep looking
                prev_sibling = prev_sibling.prev_sibling
                continue

            # Skip line comments
            elif node_type == "line_comment":
                prev_sibling = prev_sibling.prev_sibling
                continue

            # Hit a non-comment node, stop searching
            else:
                break

        return None

    def _clean_javadoc(self, javadoc: str) -> str:
        """Clean up Javadoc comment text.

        Removes comment markers and normalizes whitespace.

        Args:
            javadoc: Raw Javadoc comment text

        Returns:
            Cleaned Javadoc text
        """
        lines = javadoc.strip().split("\n")
        cleaned_lines = []

        for line in lines:
            line = line.strip()
            # Remove comment markers
            if line.startswith("/**"):
                line = line[3:].strip()
            elif line.startswith("*/"):
                continue
            elif line.startswith("*"):
                line = line[1:].strip()

            if line:
                cleaned_lines.append(line)

        return "\n".join(cleaned_lines)

    def _extract_base_classes(self, class_node: UniversalASTNode) -> List[str]:
        """Extract Java base class and interfaces.

        Handles:
        - `class Foo extends Bar {}`
        - `class Foo implements IBar, IBaz {}`
        - `interface IFoo extends IBar, IBaz {}`

        Args:
            class_node: Class declaration node

        Returns:
            List of base class/interface names
        """
        base_names = []

        # Look for superclass (extends clause)
        superclass = class_node.get_field("superclass")
        if superclass:
            # Extract the type name
            type_name = self._extract_type_name(superclass)
            if type_name:
                base_names.append(type_name)

        # Look for super_interfaces (implements clause) - node type is super_interfaces, not interfaces
        for child in class_node.children:
            if child.node_type == "super_interfaces":
                for subchild in child.children:
                    if subchild.node_type == "type_identifier":
                        base_names.append(subchild.text)
                    elif subchild.node_type == "type_list":
                        for type_child in subchild.children:
                            if type_child.node_type == "type_identifier":
                                base_names.append(type_child.text)
                            elif type_child.node_type == "generic_type":
                                name_node = self._get_generic_type_name(type_child)
                                if name_node:
                                    base_names.append(name_node)
                    elif subchild.node_type == "generic_type":
                        name_node = self._get_generic_type_name(subchild)
                        if name_node:
                            base_names.append(name_node)

        # For interfaces, check extends_interfaces clause (can extend multiple interfaces)
        for child in class_node.children:
            if child.node_type == "extends_interfaces":
                for subchild in child.children:
                    if subchild.node_type == "type_identifier":
                        base_names.append(subchild.text)
                    elif subchild.node_type == "type_list":
                        for type_child in subchild.children:
                            if type_child.node_type == "type_identifier":
                                base_names.append(type_child.text)

        return base_names

    def _extract_type_name(self, type_node: UniversalASTNode) -> Optional[str]:
        """Extract type name from a type node.

        Args:
            type_node: Type node (type_identifier, generic_type, etc.)

        Returns:
            Type name or None
        """
        if type_node.node_type == "type_identifier":
            return type_node.text
        elif type_node.node_type == "generic_type":
            return self._get_generic_type_name(type_node)

        # Try to find type_identifier child
        for child in type_node.children:
            if child.node_type == "type_identifier":
                return child.text

        return None

    def _get_generic_type_name(self, generic_node: UniversalASTNode) -> Optional[str]:
        """Extract the base type name from a generic type.

        E.g., `List<String>` -> "List"

        Args:
            generic_node: generic_type node

        Returns:
            Base type name or None
        """
        for child in generic_node.children:
            if child.node_type == "type_identifier":
                return child.text
        return None

    def _extract_decorators(self, node: UniversalASTNode) -> List[str]:
        """Extract Java annotations from class or method.

        Handles:
        - `@Override`
        - `@Deprecated`
        - `@SuppressWarnings("unchecked")`

        Args:
            node: Class or method node

        Returns:
            List of annotation names (without @ symbol)
        """
        decorators = []

        # Look for modifiers child which contains annotations
        # In tree-sitter-java, modifiers is a child node, not a field
        for child in node.children:
            if child.node_type == "modifiers":
                for modifier_child in child.children:
                    if modifier_child.node_type in ("annotation", "marker_annotation"):
                        # Extract annotation name from identifier child
                        for annotation_child in modifier_child.children:
                            if annotation_child.node_type == "identifier":
                                decorators.append(annotation_child.text)
                                break
            # Also check direct annotation children (less common structure)
            elif child.node_type in ("annotation", "marker_annotation"):
                for annotation_child in child.children:
                    if annotation_child.node_type == "identifier":
                        decorators.append(annotation_child.text)
                        break

        return decorators

    def _find_imports(self, tree: UniversalASTNode) -> List[UniversalASTNode]:
        """Find import statements in Java.

        Handles:
        - `import java.util.List;`
        - `import java.util.*;` (wildcard)
        - `import static java.lang.Math.PI;`

        Args:
            tree: Root UniversalASTNode

        Returns:
            List of import declaration nodes
        """
        imports = []

        for node in tree.walk():
            if node.node_type == "import_declaration":
                imports.append(node)

        return imports

    def _extract_import_names(self, import_node: UniversalASTNode) -> List[str]:
        """Extract module names from Java import statements.

        Handles:
        - `import java.util.List;` -> ["java.util.List"]
        - `import java.util.*;` -> ["java.util.*"]
        - `import static java.lang.Math.PI;` -> ["java.lang.Math.PI"]

        Args:
            import_node: Import declaration node

        Returns:
            List of imported module/class names
        """
        module_names = []

        # Find the scoped_identifier which contains the full import path
        for child in import_node.children:
            if child.node_type == "scoped_identifier":
                # This is a dotted import like java.util.List
                module_names.append(child.text)
            elif child.node_type == "identifier":
                # Simple import
                module_names.append(child.text)
            elif child.node_type == "asterisk":
                # Wildcard import - append to previous
                if module_names:
                    module_names[-1] = module_names[-1] + ".*"

        return list(set(module_names))

    def _calculate_complexity(self, func_node: UniversalASTNode) -> int:
        """Calculate cyclomatic complexity for Java methods.

        Args:
            func_node: Method declaration node

        Returns:
            Cyclomatic complexity score
        """
        complexity = 1  # Base complexity

        # Java decision node types
        decision_types = {
            "if_statement",
            "else_clause",
            "for_statement",
            "enhanced_for_statement",  # for-each
            "while_statement",
            "do_statement",
            "switch_expression",
            "switch_block_statement_group",  # case labels
            "catch_clause",
            "ternary_expression",
            "binary_expression",  # Will check for && and ||
        }

        for node in func_node.walk():
            if node.node_type in decision_types:
                if node.node_type == "binary_expression":
                    # Only count && and || as decision points
                    for child in node.children:
                        if child.text in ("&&", "||"):
                            complexity += 1
                            break
                else:
                    complexity += 1

        return complexity

    def _is_async_function(self, func_node: UniversalASTNode) -> bool:
        """Check if Java method is async.

        Java doesn't have native async/await, but we can detect
        methods returning CompletableFuture or other async types.

        Args:
            func_node: Method declaration node

        Returns:
            True if method appears to be async
        """
        # Check return type for async patterns
        return_type = func_node.get_field("type")
        if return_type:
            type_text = return_type.text
            async_types = {
                "CompletableFuture",
                "Future",
                "Mono",  # Reactor
                "Flux",  # Reactor
                "Single",  # RxJava
                "Observable",  # RxJava
            }
            for async_type in async_types:
                if async_type in type_text:
                    return True

        return False

    def _extract_call_name(self, call_node: UniversalASTNode) -> Optional[str]:
        """Extract method name from Java method invocation.

        Handles various call patterns:
        - Simple calls: `foo()`
        - Method calls: `obj.method()`
        - Chained calls: `obj.method1().method2()` -> returns "method2"

        Args:
            call_node: method_invocation node

        Returns:
            Called method name or None
        """
        # Get the method name field
        name_node = call_node.get_field("name")
        if name_node and name_node.node_type == "identifier":
            return name_node.text

        # Fallback: look for identifier children
        for child in call_node.children:
            if child.node_type == "identifier":
                return child.text

        return None

    def _has_return_statement(self, func_node: UniversalASTNode) -> bool:
        """Check if Java method has a return statement.

        Args:
            func_node: Method declaration node

        Returns:
            True if method contains return statement
        """
        # Also check if return type is void
        return_type = func_node.get_field("type")
        if return_type and return_type.text == "void":
            return False

        for node in func_node.walk():
            if node.node_type == "return_statement":
                return True

        # If not void, assume it returns (might throw instead)
        if return_type and return_type.text != "void":
            return True

        return False

    def _has_yield_statement(self, func_node: UniversalASTNode) -> bool:
        """Check if Java method has a yield statement.

        Java 13+ supports yield in switch expressions.

        Args:
            func_node: Method declaration node

        Returns:
            True if method contains yield statement
        """
        for node in func_node.walk():
            if node.node_type == "yield_statement":
                return True
        return False

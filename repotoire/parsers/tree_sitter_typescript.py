"""Tree-sitter based TypeScript/JavaScript parser using universal AST adapter.

This module provides TypeScript and JavaScript parsing support using tree-sitter,
following the same adapter pattern as the Python parser.
"""

from typing import List, Optional
from repotoire.parsers.base_tree_sitter_parser import BaseTreeSitterParser
from repotoire.parsers.tree_sitter_adapter import TreeSitterAdapter, UniversalASTNode
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class TreeSitterTypeScriptParser(BaseTreeSitterParser):
    """TypeScript/JavaScript parser using tree-sitter with universal AST adapter.

    Extends BaseTreeSitterParser with TypeScript-specific node type mappings
    and extraction logic. Supports both TypeScript (.ts, .tsx) and JavaScript
    (.js, .jsx) files.

    Example:
        >>> parser = TreeSitterTypeScriptParser()
        >>> tree = parser.parse("example.ts")
        >>> entities = parser.extract_entities(tree, "example.ts")
        >>> len(entities)
        10
    """

    def __init__(self, use_tsx: bool = False):
        """Initialize TypeScript parser with tree-sitter adapter.

        Args:
            use_tsx: If True, use TSX grammar (for React files). Default False.
        """
        try:
            if use_tsx:
                from tree_sitter_typescript import language_tsx as ts_language
                language_name = "tsx"
            else:
                from tree_sitter_typescript import language_typescript as ts_language
        except ImportError:
            raise ImportError(
                "tree-sitter-typescript is required for TypeScript parsing. "
                "Install with: pip install tree-sitter-typescript"
            )

        # Create adapter for TypeScript
        adapter = TreeSitterAdapter(ts_language())
        language_name = "tsx" if use_tsx else "typescript"

        # TypeScript-specific node type mappings
        node_mappings = {
            "class": "class_declaration",
            "function": "function_declaration",
            "method": "method_definition",
            "arrow_function": "arrow_function",
            "import": "import_statement",
            "call": "call_expression",
        }

        super().__init__(
            adapter=adapter,
            language_name=language_name,
            node_mappings=node_mappings
        )

    def _find_classes(self, tree: UniversalASTNode) -> List[UniversalASTNode]:
        """Find all class declarations in TypeScript.

        TypeScript classes can be:
        - class_declaration: `class Foo {}`
        - Also handles exported classes: `export class Foo {}`

        Args:
            tree: UniversalASTNode tree

        Returns:
            List of class declaration nodes
        """
        classes = []
        seen = set()

        for node in tree.walk():
            if node.node_type == "class_declaration" and id(node) not in seen:
                classes.append(node)
                seen.add(id(node))

        return classes

    def _find_functions(self, tree: UniversalASTNode) -> List[UniversalASTNode]:
        """Find all top-level function definitions in TypeScript.

        TypeScript functions can be:
        - function_declaration: `function foo() {}`
        - arrow_function in variable_declarator: `const foo = () => {}`
        - export function: `export function foo() {}`

        Args:
            tree: UniversalASTNode tree

        Returns:
            List of function definition nodes (excluding methods)
        """
        functions = []
        seen = set()

        for node in tree.walk():
            # Skip if inside a class
            if self._is_inside_class(node, tree):
                continue

            if node.node_type == "function_declaration" and id(node) not in seen:
                functions.append(node)
                seen.add(id(node))

            # Handle arrow functions assigned to variables: const foo = () => {}
            elif node.node_type == "lexical_declaration":
                for child in node.children:
                    if child.node_type == "variable_declarator":
                        # Check if value is an arrow function
                        value_node = child.get_field("value")
                        if value_node and value_node.node_type == "arrow_function":
                            # Use the variable_declarator as the "function" node
                            # so we can extract the name
                            if id(child) not in seen:
                                functions.append(child)
                                seen.add(id(child))

            # Handle exported functions
            elif node.node_type == "export_statement":
                for child in node.children:
                    if child.node_type == "function_declaration" and id(child) not in seen:
                        functions.append(child)
                        seen.add(id(child))

        return functions

    def _find_methods(self, class_node: UniversalASTNode) -> List[UniversalASTNode]:
        """Find all method definitions inside a TypeScript class.

        Args:
            class_node: Class declaration node

        Returns:
            List of method definition nodes
        """
        methods = []

        # Get class body
        body = class_node.get_field("body")
        if not body:
            return methods

        for child in body.children:
            if child.node_type == "method_definition":
                methods.append(child)
            # Handle property with arrow function: foo = () => {}
            elif child.node_type == "public_field_definition":
                value_node = child.get_field("value")
                if value_node and value_node.node_type == "arrow_function":
                    methods.append(child)

        return methods

    def _extract_function(
        self,
        func_node: UniversalASTNode,
        file_path: str,
        parent_class: Optional[str] = None
    ):
        """Extract FunctionEntity from TypeScript function node.

        Handles various TypeScript function forms:
        - function_declaration
        - method_definition
        - arrow_function in variable_declarator

        Args:
            func_node: Function node
            file_path: Path to source file
            parent_class: Qualified name of parent class if this is a method

        Returns:
            FunctionEntity or None if extraction fails
        """
        # Handle variable_declarator with arrow function
        if func_node.node_type == "variable_declarator":
            return self._extract_arrow_function(func_node, file_path, parent_class)

        # Handle public_field_definition (class property arrow function)
        if func_node.node_type == "public_field_definition":
            return self._extract_class_arrow_method(func_node, file_path, parent_class)

        # Standard function/method extraction
        return super()._extract_function(func_node, file_path, parent_class)

    def _extract_arrow_function(
        self,
        var_node: UniversalASTNode,
        file_path: str,
        parent_class: Optional[str] = None
    ):
        """Extract FunctionEntity from arrow function assigned to variable.

        Handles: `const foo = () => {}`

        Args:
            var_node: variable_declarator node
            file_path: Path to source file
            parent_class: Qualified name of parent class if this is a method

        Returns:
            FunctionEntity or None
        """
        from repotoire.models import FunctionEntity

        # Get function name from variable name
        name_node = var_node.get_field("name")
        if not name_node:
            return None

        func_name = name_node.text

        # Get the arrow function node
        arrow_func = var_node.get_field("value")
        if not arrow_func:
            return None

        # Build qualified name
        if parent_class:
            qualified_name = f"{parent_class}.{func_name}"
        else:
            qualified_name = f"{file_path}::{func_name}"

        # Calculate complexity from arrow function body
        complexity = self._calculate_complexity(arrow_func)

        # Check for async
        is_async = self._is_async_function(arrow_func)

        return FunctionEntity(
            name=func_name,
            qualified_name=qualified_name,
            file_path=file_path,
            line_start=var_node.start_line + 1,
            line_end=var_node.end_line + 1,
            docstring=None,  # Arrow functions typically don't have JSDoc attached
            complexity=complexity,
            is_async=is_async,
            decorators=[],
            is_method=parent_class is not None,
            is_static=False,
            is_classmethod=False,
            is_property=False,
            has_return=self._has_return_statement(arrow_func),
            has_yield=False  # Arrow functions can't be generators
        )

    def _extract_class_arrow_method(
        self,
        field_node: UniversalASTNode,
        file_path: str,
        parent_class: Optional[str] = None
    ):
        """Extract FunctionEntity from class field with arrow function.

        Handles: `class Foo { bar = () => {} }`

        Args:
            field_node: public_field_definition node
            file_path: Path to source file
            parent_class: Qualified name of parent class

        Returns:
            FunctionEntity or None
        """
        from repotoire.models import FunctionEntity

        # Get method name
        name_node = field_node.get_field("name")
        if not name_node:
            return None

        method_name = name_node.text

        # Get the arrow function
        arrow_func = field_node.get_field("value")
        if not arrow_func:
            return None

        # Build qualified name
        if parent_class:
            qualified_name = f"{parent_class}.{method_name}"
        else:
            qualified_name = f"{file_path}::{method_name}"

        return FunctionEntity(
            name=method_name,
            qualified_name=qualified_name,
            file_path=file_path,
            line_start=field_node.start_line + 1,
            line_end=field_node.end_line + 1,
            docstring=None,
            complexity=self._calculate_complexity(arrow_func),
            is_async=self._is_async_function(arrow_func),
            decorators=[],
            is_method=True,
            is_static=False,
            is_classmethod=False,
            is_property=False,
            has_return=self._has_return_statement(arrow_func),
            has_yield=False
        )

    def _is_async_function(self, func_node: UniversalASTNode) -> bool:
        """Check if TypeScript function is async.

        Args:
            func_node: Function node

        Returns:
            True if function uses 'async' keyword
        """
        # Check for async in node type
        if "async" in func_node.node_type:
            return True

        # Check for async keyword in children
        for child in func_node.children:
            if child.text == "async":
                return True

        return False

    def _extract_docstring(self, node: UniversalASTNode) -> Optional[str]:
        """Extract JSDoc comment from TypeScript function or class.

        JSDoc comments precede the function/class definition and look like:
        /**
         * Description here
         */

        Args:
            node: Function or class node

        Returns:
            JSDoc text or None
        """
        # Look for comment node immediately preceding this node
        # In tree-sitter, comments are usually siblings, not children
        # For now, return None - proper JSDoc extraction requires looking at siblings
        # which would need access to parent context

        # Alternative: look for string/comment in the body (less common in TS)
        return None

    def _extract_base_classes(self, class_node: UniversalASTNode) -> List[str]:
        """Extract TypeScript base class and interfaces.

        Handles:
        - `class Foo extends Bar {}`
        - `class Foo implements IBar, IBaz {}`

        Args:
            class_node: Class declaration node

        Returns:
            List of base class/interface names
        """
        base_names = []

        # Look for heritage clause (extends/implements)
        for child in class_node.children:
            if child.node_type == "class_heritage":
                for heritage_child in child.children:
                    if heritage_child.node_type in ("extends_clause", "implements_clause"):
                        # Extract type identifiers
                        for type_child in heritage_child.children:
                            if type_child.node_type in ("type_identifier", "identifier"):
                                base_names.append(type_child.text)
                            elif type_child.node_type == "generic_type":
                                # Handle generic types like `extends Map<K, V>`
                                name_node = type_child.get_field("name")
                                if name_node:
                                    base_names.append(name_node.text)

        return base_names

    def _extract_decorators(self, node: UniversalASTNode) -> List[str]:
        """Extract TypeScript decorators from class or method.

        Handles: `@decorator` syntax

        Args:
            node: Class or method node

        Returns:
            List of decorator names
        """
        decorators = []

        for child in node.children:
            if child.node_type == "decorator":
                # Extract decorator name
                for subchild in child.children:
                    if subchild.node_type == "call_expression":
                        # @decorator() with args
                        func_node = subchild.get_field("function")
                        if func_node:
                            decorators.append(func_node.text)
                    elif subchild.node_type == "identifier":
                        # @decorator without args
                        decorators.append(subchild.text)

        return decorators

    def _find_imports(self, tree: UniversalASTNode) -> List[UniversalASTNode]:
        """Find import statements in TypeScript.

        Handles:
        - `import foo from 'bar'`
        - `import { foo } from 'bar'`
        - `import * as foo from 'bar'`
        - `import 'bar'` (side effect import)

        Args:
            tree: Root UniversalASTNode

        Returns:
            List of import statement nodes
        """
        imports = []

        for node in tree.walk():
            if node.node_type == "import_statement":
                imports.append(node)

        return imports

    def _extract_import_names(self, import_node: UniversalASTNode) -> List[str]:
        """Extract module names from TypeScript import statements.

        Handles:
        - `import foo from 'bar'` -> ["bar"]
        - `import { foo, bar } from 'baz'` -> ["baz.foo", "baz.bar"]
        - `import * as foo from 'bar'` -> ["bar"]

        Args:
            import_node: Import statement node

        Returns:
            List of imported module/symbol names
        """
        module_names = []

        # Find the source module (string literal)
        source_node = import_node.get_field("source")
        if not source_node:
            # Try to find string child
            for child in import_node.children:
                if child.node_type == "string":
                    source_node = child
                    break

        if not source_node:
            return module_names

        # Extract module name from string literal
        module_name = source_node.text.strip('"').strip("'")

        # Check for named imports
        for child in import_node.children:
            if child.node_type == "import_clause":
                # Look for named imports
                for subchild in child.children:
                    if subchild.node_type == "named_imports":
                        # { foo, bar }
                        for import_specifier in subchild.children:
                            if import_specifier.node_type == "import_specifier":
                                name_node = import_specifier.get_field("name")
                                if name_node:
                                    module_names.append(f"{module_name}.{name_node.text}")
                    elif subchild.node_type == "identifier":
                        # Default import: import foo from 'bar'
                        module_names.append(module_name)
                    elif subchild.node_type == "namespace_import":
                        # import * as foo from 'bar'
                        module_names.append(module_name)

        # If no named imports found, just use the module name
        if not module_names:
            module_names.append(module_name)

        return list(set(module_names))

    def _calculate_complexity(self, func_node: UniversalASTNode) -> int:
        """Calculate cyclomatic complexity for TypeScript functions.

        Args:
            func_node: Function node

        Returns:
            Cyclomatic complexity score
        """
        complexity = 1  # Base complexity

        # TypeScript/JavaScript decision node types
        decision_types = {
            "if_statement",
            "else_clause",
            "for_statement",
            "for_in_statement",
            "while_statement",
            "do_statement",
            "switch_case",
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


class TreeSitterJavaScriptParser(TreeSitterTypeScriptParser):
    """JavaScript parser using tree-sitter.

    Uses the TypeScript parser since TypeScript is a superset of JavaScript.
    For pure JavaScript files, all TypeScript-specific features will simply
    not be present in the AST.
    """

    def __init__(self):
        """Initialize JavaScript parser."""
        try:
            from tree_sitter_javascript import language as js_language
        except ImportError:
            raise ImportError(
                "tree-sitter-javascript is required for JavaScript parsing. "
                "Install with: pip install tree-sitter-javascript"
            )

        # Create adapter for JavaScript
        adapter = TreeSitterAdapter(js_language())

        # JavaScript node mappings (same as TypeScript)
        node_mappings = {
            "class": "class_declaration",
            "function": "function_declaration",
            "method": "method_definition",
            "arrow_function": "arrow_function",
            "import": "import_statement",
            "call": "call_expression",
        }

        # Call grandparent init directly to avoid TypeScript language loading
        BaseTreeSitterParser.__init__(
            self,
            adapter=adapter,
            language_name="javascript",
            node_mappings=node_mappings
        )

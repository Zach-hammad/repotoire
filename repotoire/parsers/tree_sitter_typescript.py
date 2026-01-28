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

    def _extract_class(
        self,
        class_node: UniversalASTNode,
        file_path: str,
        tree: Optional[UniversalASTNode] = None
    ):
        """Extract ClassEntity from TypeScript class declaration.

        Handles TypeScript 5+ features including const type parameters
        on generic classes.

        Args:
            class_node: Class declaration node
            file_path: Path to source file
            tree: Root tree node for nesting level calculation

        Returns:
            ClassEntity or None if extraction fails
        """
        from repotoire.models import ClassEntity

        # Get class name
        name_node = class_node.get_field("name")
        if not name_node:
            logger.warning(f"Class node missing 'name' field in {file_path}")
            return None

        class_name = name_node.text

        # Get docstring (JSDoc)
        docstring = self._extract_docstring(class_node)

        # Get base classes/interfaces
        base_classes = self._extract_base_classes(class_node)

        # Extract decorators
        decorators = self._extract_decorators(class_node)

        # Extract type parameters (with TS 5+ const support)
        type_parameters = self._extract_type_parameters(class_node)

        # Calculate nesting level
        nesting_level = self._calculate_nesting_level(class_node, tree) if tree else 0

        # Build metadata
        metadata = {}
        if type_parameters:
            metadata["type_parameters"] = type_parameters
            metadata["is_generic"] = True
            # Check if any type param has const modifier
            if any(tp.get("is_const") for tp in type_parameters):
                metadata["has_const_type_params"] = True

        # Check for abstract class
        is_abstract = any(child.text == "abstract" for child in class_node.children)
        if is_abstract:
            metadata["is_abstract"] = True

        return ClassEntity(
            name=class_name,
            qualified_name=f"{file_path}::{class_name}",
            file_path=file_path,
            line_start=class_node.start_line + 1,
            line_end=class_node.end_line + 1,
            docstring=docstring,
            decorators=decorators,
            is_dataclass=False,
            is_exception=any("Error" in base or "Exception" in base for base in base_classes),
            nesting_level=nesting_level,
            metadata=metadata if metadata else None
        )

    def _extract_arrow_function(
        self,
        var_node: UniversalASTNode,
        file_path: str,
        parent_class: Optional[str] = None
    ):
        """Extract FunctionEntity from arrow function assigned to variable.

        Handles: `const foo = () => {}`
        Handles TS 5+ generic arrow functions: `const foo = <const T>(x: T) => x`

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

        # Extract type parameters (with TS 5+ const support)
        type_parameters = self._extract_type_parameters(arrow_func)

        # Build metadata
        metadata = {}
        if type_parameters:
            metadata["type_parameters"] = type_parameters
            metadata["is_generic"] = True
            if any(tp.get("is_const") for tp in type_parameters):
                metadata["has_const_type_params"] = True

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
            has_yield=False,  # Arrow functions can't be generators
            metadata=metadata if metadata else None
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
         * @param name Description
         * @returns Description
         */

        Args:
            node: Function or class node

        Returns:
            JSDoc text or None
        """
        # Access the raw tree-sitter node to find preceding comments
        raw_node = node._raw_node
        if not raw_node:
            return None

        # Look for comment node immediately preceding this node
        # In tree-sitter, comments are siblings
        prev_sibling = raw_node.prev_sibling
        while prev_sibling:
            node_type = prev_sibling.type

            # JSDoc comments are "comment" nodes starting with /**
            if node_type == "comment":
                comment_text = prev_sibling.text
                if isinstance(comment_text, bytes):
                    comment_text = comment_text.decode("utf-8")

                # Check if it's a JSDoc comment (starts with /**)
                if comment_text.strip().startswith("/**"):
                    return self._clean_jsdoc(comment_text)

                # Regular comment, keep looking
                prev_sibling = prev_sibling.prev_sibling
                continue

            # Skip whitespace/newlines if they exist as nodes
            elif node_type in ("", "newline", "whitespace"):
                prev_sibling = prev_sibling.prev_sibling
                continue

            # Hit a non-comment node, stop searching
            else:
                break

        return None

    def _clean_jsdoc(self, jsdoc: str) -> str:
        """Clean up JSDoc comment text.

        Removes comment markers and normalizes whitespace.

        Args:
            jsdoc: Raw JSDoc comment text

        Returns:
            Cleaned JSDoc text
        """
        lines = jsdoc.strip().split("\n")
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
            # TypeScript 5+ satisfies adds type checking branches
            "satisfies_expression",
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

    def _extract_type_parameters(self, func_node: UniversalASTNode) -> List[dict]:
        """Extract type parameters from TypeScript generic function/class.

        Handles TypeScript 5+ features:
        - `<T>` -> [{"name": "T", "constraint": null, "is_const": false}]
        - `<T extends string>` -> [{"name": "T", "constraint": "string", "is_const": false}]
        - `<const T>` (TS 5+) -> [{"name": "T", "constraint": null, "is_const": true}]
        - `<const T extends readonly string[]>` -> [{"name": "T", "constraint": "readonly string[]", "is_const": true}]

        Args:
            func_node: Function or class node

        Returns:
            List of type parameter dictionaries
        """
        type_params = []

        # Find type_parameters node
        for child in func_node.children:
            if child.node_type == "type_parameters":
                for param_child in child.children:
                    if param_child.node_type == "type_parameter":
                        param = self._extract_single_type_parameter(param_child)
                        if param:
                            type_params.append(param)

        return type_params

    def _extract_single_type_parameter(self, param_node: UniversalASTNode) -> Optional[dict]:
        """Extract a single type parameter's details.

        Handles TypeScript 5+ const type parameters.

        Args:
            param_node: type_parameter node

        Returns:
            Dict with name, constraint, is_const or None
        """
        param = {"name": None, "constraint": None, "is_const": False, "default": None}

        for child in param_node.children:
            if child.node_type == "type_identifier":
                param["name"] = child.text
            elif child.node_type == "constraint":
                # Extract constraint type
                for constraint_child in child.children:
                    if constraint_child.node_type not in ("extends",):
                        param["constraint"] = constraint_child.text
            elif child.node_type == "default_type":
                # Default type parameter value
                for default_child in child.children:
                    if default_child.node_type != "=":
                        param["default"] = default_child.text
            # TypeScript 5+ const modifier
            elif child.text == "const":
                param["is_const"] = True

        if param["name"]:
            return param
        return None

    def _extract_satisfies_relationships(self, tree: UniversalASTNode, file_path: str) -> List[dict]:
        """Extract satisfies type relationships (TypeScript 5+).

        The satisfies operator: `const obj = { a: 1 } satisfies Record<string, number>`
        creates a type constraint relationship without widening the type.

        Args:
            tree: Root UniversalASTNode
            file_path: Path to source file

        Returns:
            List of satisfies relationship dicts with expression and type
        """
        relationships = []

        for node in tree.walk():
            if node.node_type == "satisfies_expression":
                rel = {
                    "line": node.start_line + 1,
                    "type": None,
                    "expression": None
                }

                for child in node.children:
                    if child.node_type == "type_identifier":
                        rel["type"] = child.text
                    elif child.node_type == "generic_type":
                        # Handle generic types like Record<string, number>
                        name_node = None
                        for gc in child.children:
                            if gc.node_type == "type_identifier":
                                name_node = gc.text
                                break
                        if name_node:
                            rel["type"] = child.text  # Full generic type

                if rel["type"]:
                    relationships.append(rel)

        return relationships


    def _detect_react_hooks(self, func_node: UniversalASTNode) -> List[str]:
        """Detect React hooks used in a function.

        React hooks are functions starting with 'use' that are called
        at the top level of a component function.

        Built-in hooks detected:
        - useState, useEffect, useContext, useReducer
        - useCallback, useMemo, useRef, useImperativeHandle
        - useLayoutEffect, useDebugValue, useDeferredValue
        - useTransition, useId, useSyncExternalStore

        Also detects custom hooks (functions starting with 'use').

        Args:
            func_node: Function node to analyze

        Returns:
            List of hook names used (e.g., ["useState", "useEffect"])
        """
        hooks_found = []
        seen_hooks = set()

        # Built-in React hooks for reference
        builtin_hooks = {
            "useState", "useEffect", "useContext", "useReducer",
            "useCallback", "useMemo", "useRef", "useImperativeHandle",
            "useLayoutEffect", "useDebugValue", "useDeferredValue",
            "useTransition", "useId", "useSyncExternalStore",
            "useInsertionEffect"
        }

        for node in func_node.walk():
            if node.node_type == "call_expression":
                # Get the function being called
                func_field = node.get_field("function")
                if not func_field:
                    continue

                # Extract the hook name
                hook_name = None
                if func_field.node_type == "identifier":
                    hook_name = func_field.text
                elif func_field.node_type == "member_expression":
                    # Handle React.useState pattern
                    property_node = func_field.get_field("property")
                    if property_node:
                        hook_name = property_node.text

                # Check if it's a hook (starts with 'use')
                if hook_name and hook_name.startswith("use") and hook_name not in seen_hooks:
                    # Validate it looks like a hook (camelCase after 'use')
                    if len(hook_name) > 3 and hook_name[3].isupper():
                        hooks_found.append(hook_name)
                        seen_hooks.add(hook_name)

        return hooks_found

    def _is_react_component(self, func_node: UniversalASTNode) -> bool:
        """Detect if a function is a React functional component.

        A function is considered a React component if:
        1. It returns JSX elements (detected by jsx_element, jsx_self_closing_element)
        2. Its name starts with an uppercase letter (convention)

        Args:
            func_node: Function node to analyze

        Returns:
            True if function appears to be a React component
        """
        # Check for JSX return
        has_jsx_return = False

        for node in func_node.walk():
            if node.node_type in (
                "jsx_element",
                "jsx_self_closing_element",
                "jsx_fragment"
            ):
                has_jsx_return = True
                break

        return has_jsx_return

    def _extract_function(
        self,
        func_node: UniversalASTNode,
        file_path: str,
        parent_class: Optional[str] = None
    ):
        """Extract FunctionEntity from TypeScript function node.

        Handles various TypeScript function forms and adds React pattern detection:
        - function_declaration
        - method_definition
        - arrow_function in variable_declarator
        - React hooks detection
        - React component detection

        Args:
            func_node: Function node
            file_path: Path to source file
            parent_class: Qualified name of parent class if this is a method

        Returns:
            FunctionEntity or None if extraction fails
        """
        # Handle variable_declarator with arrow function
        if func_node.node_type == "variable_declarator":
            entity = self._extract_arrow_function(func_node, file_path, parent_class)
            if entity:
                self._add_react_metadata(entity, func_node)
            return entity

        # Handle public_field_definition (class property arrow function)
        if func_node.node_type == "public_field_definition":
            entity = self._extract_class_arrow_method(func_node, file_path, parent_class)
            if entity:
                self._add_react_metadata(entity, func_node)
            return entity

        # Standard function/method extraction
        entity = super()._extract_function(func_node, file_path, parent_class)
        if entity:
            self._add_react_metadata(entity, func_node)
        return entity

    def _add_react_metadata(self, entity, func_node: UniversalASTNode) -> None:
        """Add React-specific metadata to a function entity.

        Args:
            entity: FunctionEntity to update
            func_node: Function node to analyze
        """
        # Ensure metadata is initialized
        if entity.metadata is None:
            entity.metadata = {}

        # Get the actual function node for analysis (might be wrapped)
        analysis_node = func_node
        if func_node.node_type == "variable_declarator":
            value = func_node.get_field("value")
            if value:
                analysis_node = value
        elif func_node.node_type == "public_field_definition":
            value = func_node.get_field("value")
            if value:
                analysis_node = value

        # Detect React hooks
        hooks = self._detect_react_hooks(analysis_node)
        if hooks:
            entity.metadata["react_hooks"] = hooks

        # Detect if it's a React component
        if self._is_react_component(analysis_node):
            entity.metadata["is_react_component"] = True

            # Check if it's a functional component with hooks (common pattern)
            if hooks:
                entity.metadata["component_type"] = "functional_with_hooks"
            else:
                entity.metadata["component_type"] = "functional"

    def _extract_call_name(self, call_node: UniversalASTNode) -> Optional[str]:
        """Extract function/method name from TypeScript call expression.

        Handles various call patterns:
        - Simple calls: `foo()`
        - Method calls: `obj.method()`
        - Chained calls: `obj.method1().method2()` -> returns "method2"
        - Nested calls: `func1(func2())` -> handled by finding all calls

        Args:
            call_node: call_expression node

        Returns:
            Called function/method name or None
        """
        func_field = call_node.get_field("function")
        if not func_field:
            # Try first child as fallback
            if call_node.children:
                func_field = call_node.children[0]
            else:
                return None

        # Simple identifier call: foo()
        if func_field.node_type == "identifier":
            return func_field.text

        # Member expression: obj.method() or obj.prop.method()
        if func_field.node_type == "member_expression":
            return self._extract_member_call_name(func_field)

        # Call expression (chained): obj.method1().method2()
        if func_field.node_type == "call_expression":
            # This is the object being called on, get its name
            # The actual method being called is in the parent member_expression
            # This case is handled when we process the outer call_expression
            return None

        return None

    def _extract_member_call_name(self, member_node: UniversalASTNode) -> Optional[str]:
        """Extract method name from member expression.

        Handles:
        - obj.method -> "method"
        - obj.prop.method -> "method"
        - obj.method1().method2 -> "method2"

        Args:
            member_node: member_expression node

        Returns:
            The called method name
        """
        # Get the property (rightmost part)
        property_node = member_node.get_field("property")
        if property_node and property_node.node_type == "property_identifier":
            return property_node.text

        # Try children if field access doesn't work
        for child in reversed(member_node.children):
            if child.node_type in ("identifier", "property_identifier"):
                return child.text

        return None

    def _find_all_calls(self, func_node: UniversalASTNode) -> List[UniversalASTNode]:
        """Find all call expressions including nested calls.

        This finds both top-level and nested calls:
        - `foo()` - found
        - `foo(bar())` - both foo and bar found
        - `obj.method1().method2()` - both calls found

        Args:
            func_node: Function node to search within

        Returns:
            List of all call_expression nodes
        """
        calls = []
        for node in func_node.walk():
            if node.node_type == "call_expression":
                calls.append(node)
        return calls

    def _find_calls_in_function(
        self,
        tree: UniversalASTNode,
        entity
    ) -> List[UniversalASTNode]:
        """Find function call nodes inside a specific function.

        Override base implementation to find all calls including nested ones.

        Args:
            tree: Root UniversalASTNode
            entity: FunctionEntity to search within

        Returns:
            List of call nodes found in the function
        """
        # Find function nodes to search
        func_nodes = []

        # Check function declarations
        for node in tree.walk():
            if node.node_type == "function_declaration":
                func_nodes.append(node)
            elif node.node_type == "lexical_declaration":
                # Arrow functions in const/let
                for child in node.children:
                    if child.node_type == "variable_declarator":
                        value = child.get_field("value")
                        if value and value.node_type == "arrow_function":
                            func_nodes.append(child)
            elif node.node_type == "method_definition":
                func_nodes.append(node)

        for func_node in func_nodes:
            # Check if this is the right function by line numbers
            if func_node.node_type == "variable_declarator":
                start_line = func_node.start_line + 1
                end_line = func_node.end_line + 1
            else:
                start_line = func_node.start_line + 1
                end_line = func_node.end_line + 1

            if start_line == entity.line_start and end_line == entity.line_end:
                # Found the function, get all calls including nested ones
                return self._find_all_calls(func_node)

        return []


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

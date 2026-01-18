"""Tree-sitter based Go parser using universal AST adapter.

This module provides Go parsing support using tree-sitter,
following the same adapter pattern as the Python, TypeScript, and Java parsers.
"""

from typing import List, Optional
from repotoire.parsers.base_tree_sitter_parser import BaseTreeSitterParser
from repotoire.parsers.tree_sitter_adapter import TreeSitterAdapter, UniversalASTNode
from repotoire.models import ClassEntity, FunctionEntity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class TreeSitterGoParser(BaseTreeSitterParser):
    """Go parser using tree-sitter with universal AST adapter.

    Extends BaseTreeSitterParser with Go-specific node type mappings
    and extraction logic. Supports Go source files (.go) and handles
    Go-specific constructs like structs, interfaces, methods with receivers,
    goroutines, and channels.

    Example:
        >>> parser = TreeSitterGoParser()
        >>> tree = parser.parse("main.go")
        >>> entities = parser.extract_entities(tree, "main.go")
        >>> len(entities)
        10
    """

    def __init__(self):
        """Initialize Go parser with tree-sitter adapter."""
        try:
            from tree_sitter_go import language as go_language
        except ImportError:
            raise ImportError(
                "tree-sitter-go is required for Go parsing. "
                "Install with: pip install tree-sitter-go"
            )

        # Create adapter for Go
        adapter = TreeSitterAdapter(go_language())

        # Go-specific node type mappings
        node_mappings = {
            "class": "type_declaration",  # Go uses type declarations for structs/interfaces
            "struct": "struct_type",
            "interface": "interface_type",
            "function": "function_declaration",
            "method": "method_declaration",
            "import": "import_declaration",
            "call": "call_expression",
        }

        super().__init__(
            adapter=adapter,
            language_name="go",
            node_mappings=node_mappings
        )

    def _find_classes(self, tree: UniversalASTNode) -> List[UniversalASTNode]:
        """Find all type declarations (structs, interfaces) in Go.

        Go types include:
        - type_declaration with struct_type: `type User struct {}`
        - type_declaration with interface_type: `type Reader interface {}`

        Args:
            tree: UniversalASTNode tree

        Returns:
            List of type declaration nodes for structs and interfaces
        """
        classes = []
        seen = set()

        for node in tree.walk():
            if node.node_type == "type_declaration" and id(node) not in seen:
                # Check if this type declaration contains a struct or interface
                for child in node.children:
                    if child.node_type == "type_spec":
                        for type_child in child.children:
                            if type_child.node_type in ("struct_type", "interface_type"):
                                classes.append(node)
                                seen.add(id(node))
                                break

        return classes

    def _find_functions(self, tree: UniversalASTNode) -> List[UniversalASTNode]:
        """Find all function declarations in Go.

        Go functions:
        - function_declaration: `func foo() {}`
        - Does NOT include method_declaration (methods with receivers)

        Args:
            tree: UniversalASTNode tree

        Returns:
            List of function declaration nodes (excluding methods)
        """
        functions = []
        seen = set()

        for node in tree.walk():
            if node.node_type == "function_declaration" and id(node) not in seen:
                functions.append(node)
                seen.add(id(node))

        return functions

    def _find_methods(self, class_node: UniversalASTNode) -> List[UniversalASTNode]:
        """Find all methods for a Go struct/interface.

        For Go, methods are declared outside the type declaration using
        method_declaration with a receiver. Interface methods are declared
        inside the interface_type.

        This method finds:
        1. Interface methods (method_elem inside interface_type)
        2. Struct methods are found separately via _find_struct_methods()

        Args:
            class_node: Type declaration node

        Returns:
            List of method nodes (for interfaces only - struct methods handled separately)
        """
        methods = []

        # For interfaces, find method_elem nodes inside interface_type
        for child in class_node.walk():
            if child.node_type == "interface_type":
                for interface_child in child.children:
                    if interface_child.node_type == "method_elem":
                        methods.append(interface_child)

        return methods

    def extract_entities(self, tree: UniversalASTNode, file_path: str) -> List:
        """Extract entities from Go source file.

        Overrides base to handle Go's method declaration pattern where
        methods are declared separately from their receiver types.

        Args:
            tree: Parsed UniversalASTNode tree
            file_path: Path to source file

        Returns:
            List of extracted entities
        """
        entities = []

        # Create file entity
        file_entity = self._create_file_entity(tree, file_path)
        entities.append(file_entity)

        # Build a map of type names to their entities for method association
        type_entities = {}

        # Extract structs and interfaces
        for class_node in self._find_classes(tree):
            class_entity = self._extract_class(class_node, file_path, tree)
            if class_entity:
                entities.append(class_entity)
                # Store by short name for method receiver matching
                type_entities[class_entity.name] = class_entity

                # Extract interface methods
                for method_node in self._find_methods(class_node):
                    method_entity = self._extract_interface_method(
                        method_node,
                        file_path,
                        parent_class=class_entity.qualified_name
                    )
                    if method_entity:
                        entities.append(method_entity)

        # Extract top-level functions
        for func_node in self._find_functions(tree):
            func_entity = self._extract_function(func_node, file_path)
            if func_entity:
                entities.append(func_entity)

        # Extract methods with receivers (declared outside structs)
        for method_node in self._find_all_methods(tree):
            method_entity = self._extract_method_with_receiver(
                method_node, file_path, type_entities
            )
            if method_entity:
                entities.append(method_entity)

        logger.debug(f"Extracted {len(entities)} entities from {file_path}")
        return entities

    def _find_all_methods(self, tree: UniversalASTNode) -> List[UniversalASTNode]:
        """Find all method declarations (functions with receivers) in Go.

        Args:
            tree: UniversalASTNode tree

        Returns:
            List of method_declaration nodes
        """
        methods = []
        seen = set()

        for node in tree.walk():
            if node.node_type == "method_declaration" and id(node) not in seen:
                methods.append(node)
                seen.add(id(node))

        return methods

    def _extract_class(
        self,
        class_node: UniversalASTNode,
        file_path: str,
        tree: Optional[UniversalASTNode] = None
    ) -> Optional[ClassEntity]:
        """Extract ClassEntity from Go type declaration.

        Args:
            class_node: Type declaration node
            file_path: Path to source file
            tree: Root tree node for nesting level calculation

        Returns:
            ClassEntity or None if extraction fails
        """
        # Get type name from type_spec
        type_name = None
        is_interface = False

        for child in class_node.children:
            if child.node_type == "type_spec":
                for spec_child in child.children:
                    if spec_child.node_type == "type_identifier":
                        type_name = spec_child.text
                    elif spec_child.node_type == "interface_type":
                        is_interface = True

        if not type_name:
            logger.warning(f"Type declaration missing name in {file_path}")
            return None

        # Get docstring (Go doc comment)
        docstring = self._extract_docstring(class_node)

        # Get embedded types (for interfaces)
        base_classes = self._extract_base_classes(class_node)

        # Calculate nesting level
        nesting_level = self._calculate_nesting_level(class_node, tree) if tree else 0

        return ClassEntity(
            name=type_name,
            qualified_name=f"{file_path}::{type_name}",
            file_path=file_path,
            line_start=class_node.start_line + 1,
            line_end=class_node.end_line + 1,
            docstring=docstring,
            decorators=[],  # Go doesn't have decorators
            is_dataclass=not is_interface,  # Structs are data-like
            is_exception=False,
            nesting_level=nesting_level
        )

    def _extract_function(
        self,
        func_node: UniversalASTNode,
        file_path: str,
        parent_class: Optional[str] = None
    ) -> Optional[FunctionEntity]:
        """Extract FunctionEntity from Go function declaration.

        Args:
            func_node: Function declaration node
            file_path: Path to source file
            parent_class: Qualified name of parent (not used for Go functions)

        Returns:
            FunctionEntity or None if extraction fails
        """
        # Get function name
        func_name = None
        for child in func_node.children:
            if child.node_type == "identifier":
                func_name = child.text
                break

        if not func_name:
            logger.warning(f"Function declaration missing name in {file_path}")
            return None

        # Build qualified name
        qualified_name = f"{file_path}::{func_name}"

        # Get docstring
        docstring = self._extract_docstring(func_node)

        # Calculate complexity
        complexity = self._calculate_complexity(func_node)

        # Check for async patterns (channel return type, goroutine spawning)
        is_async = self._is_async_function(func_node)

        # Check for return/yield
        has_return = self._has_return_statement(func_node)

        return FunctionEntity(
            name=func_name,
            qualified_name=qualified_name,
            file_path=file_path,
            line_start=func_node.start_line + 1,
            line_end=func_node.end_line + 1,
            docstring=docstring,
            complexity=complexity,
            is_async=is_async,
            decorators=[],
            is_method=False,
            is_static=False,
            is_classmethod=False,
            is_property=False,
            has_return=has_return,
            has_yield=False  # Go doesn't have yield
        )

    def _extract_method_with_receiver(
        self,
        method_node: UniversalASTNode,
        file_path: str,
        type_entities: dict
    ) -> Optional[FunctionEntity]:
        """Extract FunctionEntity from Go method declaration with receiver.

        Go methods look like: `func (r *Receiver) MethodName() {}`

        Args:
            method_node: Method declaration node
            file_path: Path to source file
            type_entities: Map of type names to their entities

        Returns:
            FunctionEntity or None if extraction fails
        """
        # Get method name (field_identifier)
        method_name = None
        receiver_type = None

        for child in method_node.children:
            if child.node_type == "field_identifier":
                method_name = child.text
            elif child.node_type == "parameter_list":
                # First parameter_list is the receiver
                if receiver_type is None:
                    receiver_type = self._extract_receiver_type(child)

        if not method_name:
            logger.warning(f"Method declaration missing name in {file_path}")
            return None

        # Determine parent class from receiver type
        parent_class = None
        if receiver_type and receiver_type in type_entities:
            parent_class = type_entities[receiver_type].qualified_name

        # Build qualified name
        if parent_class:
            qualified_name = f"{parent_class}.{method_name}"
        else:
            qualified_name = f"{file_path}::{method_name}"

        # Get docstring
        docstring = self._extract_docstring(method_node)

        # Calculate complexity
        complexity = self._calculate_complexity(method_node)

        # Check for async patterns
        is_async = self._is_async_function(method_node)

        # Check for return
        has_return = self._has_return_statement(method_node)

        return FunctionEntity(
            name=method_name,
            qualified_name=qualified_name,
            file_path=file_path,
            line_start=method_node.start_line + 1,
            line_end=method_node.end_line + 1,
            docstring=docstring,
            complexity=complexity,
            is_async=is_async,
            decorators=[],
            is_method=True,
            is_static=False,
            is_classmethod=False,
            is_property=False,
            has_return=has_return,
            has_yield=False
        )

    def _extract_receiver_type(self, param_list: UniversalASTNode) -> Optional[str]:
        """Extract the receiver type name from a parameter list.

        Handles both value receivers `(r Receiver)` and pointer receivers `(r *Receiver)`.

        Args:
            param_list: Parameter list node containing the receiver

        Returns:
            Receiver type name or None
        """
        for child in param_list.children:
            if child.node_type == "parameter_declaration":
                for param_child in child.children:
                    if param_child.node_type == "type_identifier":
                        return param_child.text
                    elif param_child.node_type == "pointer_type":
                        # Handle *Receiver
                        for ptr_child in param_child.children:
                            if ptr_child.node_type == "type_identifier":
                                return ptr_child.text

        return None

    def _extract_interface_method(
        self,
        method_node: UniversalASTNode,
        file_path: str,
        parent_class: str
    ) -> Optional[FunctionEntity]:
        """Extract FunctionEntity from Go interface method element.

        Interface methods look like: `Read(p []byte) (n int, err error)`

        Args:
            method_node: method_elem node from interface
            file_path: Path to source file
            parent_class: Qualified name of parent interface

        Returns:
            FunctionEntity or None if extraction fails
        """
        # Get method name (field_identifier)
        method_name = None
        for child in method_node.children:
            if child.node_type == "field_identifier":
                method_name = child.text
                break

        if not method_name:
            return None

        qualified_name = f"{parent_class}.{method_name}"

        return FunctionEntity(
            name=method_name,
            qualified_name=qualified_name,
            file_path=file_path,
            line_start=method_node.start_line + 1,
            line_end=method_node.end_line + 1,
            docstring=None,  # Interface methods typically don't have individual docs
            complexity=1,  # Interface methods have no implementation
            is_async=False,
            decorators=[],
            is_method=True,
            is_static=False,
            is_classmethod=False,
            is_property=False,
            has_return=True,  # Assumed
            has_yield=False
        )

    def _extract_docstring(self, node: UniversalASTNode) -> Optional[str]:
        """Extract Go doc comment from a declaration.

        Go doc comments are single-line comments (//) immediately
        preceding the declaration.

        Args:
            node: Declaration node

        Returns:
            Doc comment text or None
        """
        raw_node = node._raw_node
        if not raw_node:
            return None

        # Collect all preceding comment lines
        comment_lines = []
        prev_sibling = raw_node.prev_sibling

        while prev_sibling:
            if prev_sibling.type == "comment":
                comment_text = prev_sibling.text
                if isinstance(comment_text, bytes):
                    comment_text = comment_text.decode("utf-8")

                # Go doc comments start with //
                if comment_text.startswith("//"):
                    # Remove // and leading space
                    clean_line = comment_text[2:].strip()
                    comment_lines.insert(0, clean_line)  # Insert at beginning

                prev_sibling = prev_sibling.prev_sibling
            else:
                break

        if comment_lines:
            return "\n".join(comment_lines)

        return None

    def _extract_base_classes(self, class_node: UniversalASTNode) -> List[str]:
        """Extract embedded types from Go interface.

        Go interfaces can embed other interfaces:
        `type ReadCloser interface { Reader; Closer }`

        Args:
            class_node: Type declaration node

        Returns:
            List of embedded type names
        """
        base_names = []

        for child in class_node.walk():
            if child.node_type == "interface_type":
                for interface_child in child.children:
                    if interface_child.node_type == "type_elem":
                        # Embedded interface
                        for elem_child in interface_child.children:
                            if elem_child.node_type == "type_identifier":
                                base_names.append(elem_child.text)

        return base_names

    def _extract_decorators(self, node: UniversalASTNode) -> List[str]:
        """Extract decorators from Go code.

        Go doesn't have decorators, but we could potentially extract
        build tags or other special comments in the future.

        Args:
            node: Declaration node

        Returns:
            Empty list (Go doesn't have decorators)
        """
        return []

    def _find_imports(self, tree: UniversalASTNode) -> List[UniversalASTNode]:
        """Find import declarations in Go.

        Handles:
        - `import "fmt"`
        - `import ("fmt"; "os")`
        - `import alias "package/path"`

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
        """Extract package paths from Go import declarations.

        Handles:
        - `import "fmt"` -> ["fmt"]
        - `import ("fmt"; "os")` -> ["fmt", "os"]
        - `import f "fmt"` -> ["fmt"]

        Args:
            import_node: Import declaration node

        Returns:
            List of imported package paths
        """
        module_names = []

        for child in import_node.walk():
            if child.node_type == "import_spec":
                for spec_child in child.children:
                    if spec_child.node_type == "interpreted_string_literal":
                        # Extract the string content (remove quotes)
                        path = spec_child.text.strip('"')
                        module_names.append(path)

        return list(set(module_names))

    def _calculate_complexity(self, func_node: UniversalASTNode) -> int:
        """Calculate cyclomatic complexity for Go functions.

        Args:
            func_node: Function/method declaration node

        Returns:
            Cyclomatic complexity score
        """
        complexity = 1  # Base complexity

        # Go decision node types
        decision_types = {
            "if_statement",
            "else_clause",
            "for_statement",
            "range_clause",  # for-range
            "expression_switch_statement",
            "type_switch_statement",
            "expression_case",  # case in switch
            "type_case",
            "select_statement",
            "communication_case",  # case in select
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
        """Check if Go function uses async patterns.

        Detects:
        - Channel return types (`<-chan`, `chan<-`, `chan`)
        - Goroutine spawning (`go func()`)

        Args:
            func_node: Function/method declaration node

        Returns:
            True if function appears to be async
        """
        # Check for channel return type
        for child in func_node.children:
            if child.node_type == "channel_type":
                return True
            # Check result parameters for channel types
            elif child.node_type == "parameter_list":
                for param_child in child.walk():
                    if param_child.node_type == "channel_type":
                        return True

        # Check for goroutine spawning in body
        for node in func_node.walk():
            if node.node_type == "go_statement":
                return True

        return False

    def _has_return_statement(self, func_node: UniversalASTNode) -> bool:
        """Check if Go function has a return statement.

        Args:
            func_node: Function/method declaration node

        Returns:
            True if function contains return statement
        """
        for node in func_node.walk():
            if node.node_type == "return_statement":
                return True

        return False

    def _extract_call_name(self, call_node: UniversalASTNode) -> Optional[str]:
        """Extract function/method name from Go call expression.

        Handles:
        - Simple calls: `foo()`
        - Method calls: `obj.Method()`
        - Package calls: `fmt.Println()`

        Args:
            call_node: call_expression node

        Returns:
            Called function/method name or None
        """
        # Get the function being called (first child before argument_list)
        for child in call_node.children:
            if child.node_type == "identifier":
                return child.text
            elif child.node_type == "selector_expression":
                # Get the method/function name (field_identifier)
                for sel_child in child.children:
                    if sel_child.node_type == "field_identifier":
                        return sel_child.text

        return None

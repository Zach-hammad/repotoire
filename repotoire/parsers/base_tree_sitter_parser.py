"""Base parser class with shared tree-sitter logic for all languages.

This module provides reusable entity extraction logic that works across
all programming languages supported by tree-sitter.
"""

from typing import List, Optional, Dict
from pathlib import Path
from datetime import datetime
import hashlib

from repotoire.parsers.base import CodeParser
from repotoire.parsers.tree_sitter_adapter import UniversalASTNode, TreeSitterAdapter
from repotoire.models import (
    Entity,
    FileEntity,
    ClassEntity,
    FunctionEntity,
    Relationship,
    RelationshipType,
)
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class BaseTreeSitterParser(CodeParser):
    """Base class for tree-sitter based parsers with shared extraction logic.

    Provides common functionality for extracting entities and relationships
    from UniversalASTNode trees. Language-specific parsers extend this class
    and provide node type mappings.

    Example:
        >>> class TreeSitterPythonParser(BaseTreeSitterParser):
        ...     def __init__(self):
        ...         from tree_sitter_python import language
        ...         adapter = TreeSitterAdapter(language())
        ...         super().__init__(adapter, language_name="python")
    """

    def __init__(
        self,
        adapter: TreeSitterAdapter,
        language_name: str,
        node_mappings: Optional[Dict[str, str]] = None
    ):
        """Initialize base tree-sitter parser.

        Args:
            adapter: Configured TreeSitterAdapter for the language
            language_name: Name of the language (e.g., "python", "typescript")
            node_mappings: Optional custom node type mappings
        """
        self.adapter = adapter
        self.language_name = language_name
        self.node_mappings = node_mappings or {}

    def parse(self, file_path: str) -> UniversalASTNode:
        """Parse a source file into UniversalASTNode tree.

        Args:
            file_path: Path to source file

        Returns:
            Root UniversalASTNode
        """
        return self.adapter.parse_file(file_path)

    def extract_entities(self, tree: UniversalASTNode, file_path: str) -> List[Entity]:
        """Extract entities from UniversalASTNode tree.

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

        # Extract classes
        for class_node in self._find_classes(tree):
            class_entity = self._extract_class(class_node, file_path)
            if class_entity:
                entities.append(class_entity)

                # Extract methods from class
                for method_node in self._find_methods(class_node):
                    method_entity = self._extract_function(
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

        logger.debug(f"Extracted {len(entities)} entities from {file_path}")
        return entities

    def extract_relationships(
        self,
        tree: UniversalASTNode,
        file_path: str,
        entities: List[Entity]
    ) -> List[Relationship]:
        """Extract relationships from UniversalASTNode tree.

        Args:
            tree: Parsed UniversalASTNode tree
            file_path: Path to source file
            entities: Extracted entities for relationship mapping

        Returns:
            List of extracted relationships
        """
        relationships = []

        # Create entity lookup by qualified name
        entity_map = {e.qualified_name: e for e in entities}

        # CONTAINS relationships (hierarchical: file→top-level, class→members)
        file_qname = file_path
        for entity in entities:
            if entity.qualified_name == file_qname:
                continue  # Skip the file itself

            # Determine parent from entity type and qualified name structure
            parent_qname = None

            if isinstance(entity, FunctionEntity) and entity.is_method:
                # Method: parent is the class
                # qualified_name format: "file.py::ClassName.method_name"
                # Extract "file.py::ClassName" from the qualified name
                if '.' in entity.qualified_name:
                    parent_qname = entity.qualified_name.rsplit('.', 1)[0]

            elif isinstance(entity, AttributeEntity):
                # Class attribute: parent is the class
                # Similar format: "file.py::ClassName.attribute_name"
                if '.' in entity.qualified_name:
                    parent_qname = entity.qualified_name.rsplit('.', 1)[0]

            else:
                # Top-level entity (class, module, top-level function)
                parent_qname = file_qname

            if parent_qname:
                relationships.append(Relationship(
                    source_id=parent_qname,
                    target_id=entity.qualified_name,
                    rel_type=RelationshipType.CONTAINS
                ))

        # Extract IMPORTS relationships
        # Note: This is a naive implementation that doesn't resolve relative imports
        # or handle package hierarchies correctly - you'll need language-specific logic
        import_nodes = self._find_imports(tree)
        for import_node in import_nodes:
            imported_modules = self._extract_import_names(import_node)
            for module_name in imported_modules:
                # Extract base module for indexing
                # e.g., "os.path.join" -> module="os.path", target="os.path.join"
                # e.g., "os" -> module="os", target="os"
                if "." in module_name:
                    # For qualified imports, module is everything except the last part
                    base_module = ".".join(module_name.split(".")[:-1])
                else:
                    # For simple imports, module is the whole name
                    base_module = module_name

                relationships.append(Relationship(
                    source_id=file_qname,
                    target_id=module_name,
                    rel_type=RelationshipType.IMPORTS,
                    properties={
                        "import_type": "module",
                        "module": base_module  # Base module for query performance
                    }
                ))

        # Extract INHERITS relationships
        for entity in entities:
            if isinstance(entity, ClassEntity):
                class_node = self._find_class_node(tree, entity)
                if class_node:
                    base_classes = self._extract_base_classes(class_node)
                    for order, base_class_name in enumerate(base_classes):
                        # Try to find the base class in our entities
                        # First, try exact match (same file)
                        base_qname = f"{file_path}::{base_class_name}"
                        if base_qname in entity_map:
                            relationships.append(Relationship(
                                source_id=entity.qualified_name,
                                target_id=base_qname,
                                rel_type=RelationshipType.INHERITS,
                                properties={"order": order}  # MRO order (0=first parent)
                            ))
                        else:
                            # Base class might be imported - create relationship anyway
                            # The base class node might be created later from another file
                            relationships.append(Relationship(
                                source_id=entity.qualified_name,
                                target_id=base_class_name,
                                rel_type=RelationshipType.INHERITS,
                                properties={"unresolved": True, "order": order}
                            ))

        # Extract CALLS relationships
        for entity in entities:
            if isinstance(entity, FunctionEntity):
                call_nodes = self._find_calls_in_function(tree, entity)
                for call_node in call_nodes:
                    called_name = self._extract_call_name(call_node)
                    if called_name:
                        # Try to resolve the qualified name
                        resolved_name = self._resolve_call_target(
                            called_name, entity, entity_map, file_path
                        )
                        relationships.append(Relationship(
                            source_id=entity.qualified_name,
                            target_id=resolved_name,
                            rel_type=RelationshipType.CALLS,
                            properties={
                                "call_type": "function_call",
                                "line_number": call_node.start_line + 1  # +1 for 1-based line numbering
                            }
                        ))

        return relationships

    def _create_file_entity(self, tree: UniversalASTNode, file_path: str) -> FileEntity:
        """Create FileEntity from tree.

        Args:
            tree: Root UniversalASTNode
            file_path: Path to file

        Returns:
            FileEntity with metadata
        """
        # Calculate file hash
        with open(file_path, 'rb') as f:
            file_hash = hashlib.md5(f.read()).hexdigest()

        # Count lines
        with open(file_path, 'r') as f:
            lines_of_code = len([line for line in f if line.strip()])

        # Get file modification time
        file_stats = Path(file_path).stat()

        # Calculate module path (e.g., "falkor/parsers/base.py" -> "falkor.parsers.base")
        # Remove file extension and replace path separators with dots
        module_path = str(Path(file_path)).replace("/", ".").replace("\\", ".")
        if module_path.endswith(".py"):
            module_path = module_path[:-3]  # Remove .py extension

        # Detect test files - check filename and immediate directory only
        # Don't check full path to avoid false positives from temp dirs like pytest-*/
        file_name_lower = Path(file_path).name.lower()
        parent_dir = Path(file_path).parent.name.lower()
        is_test = (
            file_name_lower.startswith("test_") or
            file_name_lower.endswith("_test.py") or
            parent_dir == "tests" or
            parent_dir == "test"
        )

        return FileEntity(
            name=Path(file_path).name,
            qualified_name=file_path,
            file_path=file_path,
            line_start=0,
            line_end=tree.end_line,
            language=self.language_name,
            hash=file_hash,
            loc=lines_of_code,
            last_modified=datetime.fromtimestamp(file_stats.st_mtime),
            module_path=module_path,
            is_test=is_test
        )

    def _find_classes(self, tree: UniversalASTNode) -> List[UniversalASTNode]:
        """Find all class definitions in the tree.

        Override this method for language-specific class node types.

        Args:
            tree: UniversalASTNode tree

        Returns:
            List of class definition nodes
        """
        # Default: look for "class_definition" node type
        return tree.find_all(self.node_mappings.get("class", "class_definition"))

    def _find_functions(self, tree: UniversalASTNode) -> List[UniversalASTNode]:
        """Find all top-level function definitions in the tree.

        Override this method for language-specific function node types.

        Args:
            tree: UniversalASTNode tree

        Returns:
            List of function definition nodes (excluding methods)
        """
        all_funcs = tree.find_all(
            self.node_mappings.get("function", "function_definition")
        )

        # Filter out methods (functions inside classes)
        top_level_funcs = []
        for func in all_funcs:
            # Check if function is inside a class
            if not self._is_inside_class(func, tree):
                top_level_funcs.append(func)

        return top_level_funcs

    def _find_methods(self, class_node: UniversalASTNode) -> List[UniversalASTNode]:
        """Find all method definitions inside a class.

        Args:
            class_node: Class definition node

        Returns:
            List of method definition nodes
        """
        return class_node.find_all(
            self.node_mappings.get("function", "function_definition")
        )

    def _is_inside_class(
        self,
        node: UniversalASTNode,
        tree: UniversalASTNode
    ) -> bool:
        """Check if node is inside a class definition.

        Args:
            node: Node to check
            tree: Root tree

        Returns:
            True if node is inside a class
        """
        # Simple heuristic: check if any class node contains this node
        for class_node in self._find_classes(tree):
            if self._contains_node(class_node, node):
                return True
        return False

    def _contains_node(
        self,
        parent: UniversalASTNode,
        child: UniversalASTNode
    ) -> bool:
        """Check if parent node contains child node.

        Args:
            parent: Potential parent node
            child: Potential child node

        Returns:
            True if parent contains child
        """
        if parent == child:
            return False

        for descendant in parent.walk():
            if descendant == child:
                return True

        return False

    def _extract_class(
        self,
        class_node: UniversalASTNode,
        file_path: str
    ) -> Optional[ClassEntity]:
        """Extract ClassEntity from class node.

        Args:
            class_node: Class definition node
            file_path: Path to source file

        Returns:
            ClassEntity or None if extraction fails
        """
        # Get class name
        name_node = class_node.get_field("name")
        if not name_node:
            logger.warning(f"Class node missing 'name' field in {file_path}")
            return None

        class_name = name_node.text

        # Get docstring
        docstring = self._extract_docstring(class_node)

        # Get base classes
        base_classes = self._extract_base_classes(class_node)

        # Extract decorators from class
        decorators = self._extract_decorators(class_node)

        # Check if dataclass
        is_dataclass = "dataclass" in decorators

        # Check if exception (inherits from Exception)
        is_exception = any("Exception" in base for base in base_classes)

        # Calculate nesting level (0 for top-level classes)
        # TODO: Implement proper nesting detection by traversing parent nodes
        nesting_level = 0  # Default to 0 for now

        return ClassEntity(
            name=class_name,
            qualified_name=f"{file_path}::{class_name}",
            file_path=file_path,
            line_start=class_node.start_line + 1,  # 1-indexed
            line_end=class_node.end_line + 1,
            docstring=docstring,
            decorators=decorators,
            is_dataclass=is_dataclass,
            is_exception=is_exception,
            nesting_level=nesting_level
        )

    def _extract_function(
        self,
        func_node: UniversalASTNode,
        file_path: str,
        parent_class: Optional[str] = None
    ) -> Optional[FunctionEntity]:
        """Extract FunctionEntity from function node.

        Args:
            func_node: Function definition node
            file_path: Path to source file
            parent_class: Qualified name of parent class (if method)

        Returns:
            FunctionEntity or None if extraction fails
        """
        # Get function name
        name_node = func_node.get_field("name")
        if not name_node:
            logger.warning(f"Function node missing 'name' field in {file_path}")
            return None

        func_name = name_node.text

        # Build qualified name
        if parent_class:
            qualified_name = f"{parent_class}.{func_name}"
        else:
            qualified_name = f"{file_path}::{func_name}"

        # Get docstring
        docstring = self._extract_docstring(func_node)

        # Calculate complexity
        complexity = self._calculate_complexity(func_node)

        # Extract decorators
        decorators = self._extract_decorators(func_node)

        # Determine function type from decorators
        is_static = "staticmethod" in decorators
        is_classmethod = "classmethod" in decorators
        is_property = "property" in decorators

        # Determine if method (has parent class)
        is_method = parent_class is not None

        # Check for return/yield statements
        has_return = self._has_return_statement(func_node)
        has_yield = self._has_yield_statement(func_node)

        return FunctionEntity(
            name=func_name,
            qualified_name=qualified_name,
            file_path=file_path,
            line_start=func_node.start_line + 1,
            line_end=func_node.end_line + 1,
            docstring=docstring,
            complexity=complexity,
            is_async=self._is_async_function(func_node),
            decorators=decorators,
            is_method=is_method,
            is_static=is_static,
            is_classmethod=is_classmethod,
            is_property=is_property,
            has_return=has_return,
            has_yield=has_yield
        )

    def _extract_docstring(self, node: UniversalASTNode) -> Optional[str]:
        """Extract docstring from a node (works for any language).

        Override this for language-specific docstring extraction.

        Args:
            node: Function or class node

        Returns:
            Docstring text or None
        """
        # Generic approach: look for first string literal child
        body = node.get_field("body")
        if not body:
            return None

        # Look for string node types
        for child in body.children:
            if "string" in child.node_type.lower():
                # Clean up quotes
                text = child.text.strip()
                if text.startswith('"""') or text.startswith("'''"):
                    return text[3:-3].strip()
                elif text.startswith('"') or text.startswith("'"):
                    return text[1:-1].strip()
                return text

        return None

    def _extract_base_classes(self, class_node: UniversalASTNode) -> List[str]:
        """Extract base class names.

        Override this for language-specific inheritance syntax.

        Args:
            class_node: Class definition node

        Returns:
            List of base class names
        """
        # Generic approach: look for superclass or bases field
        bases_node = class_node.get_field("superclasses") or class_node.get_field("bases")
        if not bases_node:
            return []

        base_names = []
        for child in bases_node.children:
            if child.node_type == "identifier":
                base_names.append(child.text)

        return base_names

    def _calculate_complexity(self, func_node: UniversalASTNode) -> int:
        """Calculate cyclomatic complexity (language-agnostic).

        Counts decision points: if/elif/else, for, while, and, or, try/except.

        Args:
            func_node: Function definition node

        Returns:
            Cyclomatic complexity score
        """
        complexity = 1  # Base complexity

        # Decision node types (common across languages)
        decision_types = {
            "if_statement", "elif_clause", "else_clause",
            "for_statement", "while_statement",
            "case_statement", "switch_statement",
            "catch_clause", "except_clause",
            "conditional_expression",
            "boolean_operator"
        }

        for node in func_node.walk():
            if node.node_type in decision_types:
                complexity += 1

        return complexity

    def _is_async_function(self, func_node: UniversalASTNode) -> bool:
        """Check if function is async.

        Override this for language-specific async detection.

        Args:
            func_node: Function definition node

        Returns:
            True if function is async
        """
        # Generic: check for "async" in node type or modifiers
        return "async" in func_node.node_type.lower()

    def _extract_decorators(self, node: UniversalASTNode) -> List[str]:
        """Extract decorator names from a class or function.

        Override this for language-specific decorator extraction.

        Args:
            node: Class or function node

        Returns:
            List of decorator names (without @ symbol)
        """
        # Generic approach: look for decorator nodes
        decorators = []
        for child in node.children:
            if "decorator" in child.node_type.lower():
                # Extract decorator name (skip @ symbol)
                decorator_text = child.text.strip()
                if decorator_text.startswith("@"):
                    decorator_text = decorator_text[1:]
                # Handle decorator calls like @dataclass(frozen=True) -> just "dataclass"
                if "(" in decorator_text:
                    decorator_text = decorator_text.split("(")[0]
                decorators.append(decorator_text)
        return decorators

    def _has_return_statement(self, func_node: UniversalASTNode) -> bool:
        """Check if function has a return statement.

        Args:
            func_node: Function definition node

        Returns:
            True if function contains return statement
        """
        for node in func_node.walk():
            if node.node_type in ("return_statement", "return"):
                return True
        return False

    def _has_yield_statement(self, func_node: UniversalASTNode) -> bool:
        """Check if function has a yield statement (generator).

        Args:
            func_node: Function definition node

        Returns:
            True if function contains yield statement
        """
        for node in func_node.walk():
            if node.node_type in ("yield_statement", "yield", "yield_expression"):
                return True
        return False

    def _find_imports(self, tree: UniversalASTNode) -> List[UniversalASTNode]:
        """Find import statements in the tree.

        Override this for language-specific import node types.

        Args:
            tree: Root UniversalASTNode

        Returns:
            List of import statement nodes
        """
        # Look for common import node types
        import_types = [
            "import_statement",
            "import_from_statement",
            "import_declaration",
            "import_clause",
        ]

        imports = []
        for import_type in import_types:
            imports.extend(tree.find_all(import_type))

        return imports

    def _extract_import_names(self, import_node: UniversalASTNode) -> List[str]:
        """Extract module names from an import node.

        This is a naive implementation that just grabs identifier text.
        Override for language-specific import parsing.

        Args:
            import_node: Import statement node

        Returns:
            List of imported module names
        """
        # Naive approach: find all identifiers in the import statement
        # This will break on complex imports like "from x import y as z"
        module_names = []

        for child in import_node.walk():
            # Look for identifier, dotted_name, or module_name nodes
            if child.node_type in {"identifier", "dotted_name", "module_name", "string"}:
                text = child.text.strip().strip('"').strip("'")
                if text and not text in {"import", "from", "as"}:
                    module_names.append(text)

        return list(set(module_names))  # Remove duplicates

    def _find_calls_in_function(
        self,
        tree: UniversalASTNode,
        entity: FunctionEntity
    ) -> List[UniversalASTNode]:
        """Find function call nodes inside a specific function.

        Args:
            tree: Root UniversalASTNode
            entity: FunctionEntity to search within

        Returns:
            List of call nodes found in the function
        """
        # Find the function node that matches this entity
        func_nodes = tree.find_all(
            self.node_mappings.get("function", "function_definition")
        )

        for func_node in func_nodes:
            # Check if this is the right function by line numbers
            if (func_node.start_line + 1 == entity.line_start and
                func_node.end_line + 1 == entity.line_end):
                # Found the function, now find calls within it
                return func_node.find_all(self.node_mappings.get("call", "call"))

        return []

    def _extract_call_name(self, call_node: UniversalASTNode) -> Optional[str]:
        """Extract the called function name from a call node.

        This is overly simplistic and won't handle:
        - Method calls (obj.method())
        - Chained calls (obj.method1().method2())
        - Lambda calls
        - Dynamic calls

        Args:
            call_node: Call expression node

        Returns:
            Function name or None
        """
        # Look for function field (the thing being called)
        func_field = call_node.get_field("function")
        if func_field:
            # Simple case: identifier
            if func_field.node_type == "identifier":
                return func_field.text

            # Method call: get the last identifier
            # This is wrong for complex expressions but better than nothing
            identifiers = func_field.find_all("identifier")
            if identifiers:
                return identifiers[-1].text

        # Fallback: first child might be the callable
        if call_node.children:
            first_child = call_node.children[0]
            if first_child.node_type == "identifier":
                return first_child.text

        return None

    def _find_class_node(
        self,
        tree: UniversalASTNode,
        class_entity: ClassEntity
    ) -> Optional[UniversalASTNode]:
        """Find the AST node for a given ClassEntity.

        Args:
            tree: Root UniversalASTNode
            class_entity: ClassEntity to find

        Returns:
            Class node or None if not found
        """
        for class_node in self._find_classes(tree):
            # Match by line numbers
            if (class_node.start_line + 1 == class_entity.line_start and
                class_node.end_line + 1 == class_entity.line_end):
                return class_node
        return None

    def _resolve_call_target(
        self,
        call_name: str,
        caller_entity: FunctionEntity,
        entity_map: dict,
        file_path: str
    ) -> str:
        """Try to resolve a call name to a qualified name.

        This is a best-effort resolution that:
        1. Checks if it's a function in the same file
        2. Checks if it's a method in the same class
        3. Falls back to the unresolved name

        Args:
            call_name: Simple function name from call site
            caller_entity: Entity making the call
            entity_map: Map of qualified names to entities
            file_path: Current file path

        Returns:
            Resolved qualified name (or original if can't resolve)
        """
        # Try same-file function
        file_level_qname = f"{file_path}::{call_name}"
        if file_level_qname in entity_map:
            return file_level_qname

        # Try same-class method (if caller is a method)
        if "." in caller_entity.qualified_name:
            # Extract class name from qualified name
            # e.g., "file.py::ClassName.method_name" -> "file.py::ClassName"
            parts = caller_entity.qualified_name.rsplit(".", 1)
            if len(parts) == 2:
                class_qname = parts[0]
                method_qname = f"{class_qname}.{call_name}"
                if method_qname in entity_map:
                    return method_qname

        # Can't resolve - return as-is
        # This will create an unresolved relationship
        return call_name

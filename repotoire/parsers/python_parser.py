"""Python code parser using AST module."""

import ast
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import hashlib

from repotoire.parsers.base import CodeParser
from repotoire.models import (
    Entity,
    FileEntity,
    ModuleEntity,
    ClassEntity,
    FunctionEntity,
    AttributeEntity,
    Relationship,
    NodeType,
    RelationshipType,
    SecretsPolicy,
)
from repotoire.security import SecretsScanner
from repotoire.security.secrets_scanner import apply_secrets_policy


class PythonASTVisitor(ast.NodeVisitor):
    """Single-pass AST visitor for extracting entities and relationships.

    Consolidates multiple ast.walk() traversals into a single pass through the AST,
    extracting all entities (classes, functions, methods, attributes) and relationships
    (inheritance, overrides, attribute usage, decorators) in one traversal.
    """

    def __init__(
        self,
        file_path: str,
        secrets_scanner: Optional["SecretsScanner"],
        secrets_policy: "SecretsPolicy",
    ) -> None:
        """Initialize the visitor.

        Args:
            file_path: Path to the source file
            secrets_scanner: Scanner for detecting secrets in docstrings
            secrets_policy: Policy for handling detected secrets
        """
        self.file_path = file_path
        self.secrets_scanner = secrets_scanner
        self.secrets_policy = secrets_policy

        # Collected results
        self.entities: List[Entity] = []
        self.relationships: List[Relationship] = []

        # Context tracking for nested structures
        self._class_stack: List[Tuple[ast.ClassDef, "ClassEntity"]] = []

        # Maps for relationship extraction (built during traversal)
        # class_name -> (class_node, {method_name -> qualified_name})
        self._class_methods: Dict[str, Tuple[ast.ClassDef, Dict[str, str]]] = {}
        # (class_name, line) -> base_names for inheritance lookup
        self._class_bases: Dict[Tuple[str, int], List[str]] = {}

    @property
    def current_class(self) -> Optional[Tuple[ast.ClassDef, "ClassEntity"]]:
        """Get the current class context (innermost class in stack)."""
        return self._class_stack[-1] if self._class_stack else None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Extract class entity, methods, inheritance, attributes, and overrides."""
        # Create class entity
        class_entity = self._create_class_entity(node)
        self.entities.append(class_entity)

        # Track class for inheritance/override resolution
        class_key = f"{node.name}:{node.lineno}"
        methods_map: Dict[str, str] = {}

        # Push class onto stack for nested class/method tracking
        self._class_stack.append((node, class_entity))

        # Extract base class names for later inheritance relationship creation
        base_names = []
        for base in node.bases:
            base_name = self._get_base_class_name(base)
            if base_name:
                base_names.append(base_name)
        self._class_bases[(node.name, node.lineno)] = base_names

        # Process class body (methods, nested classes handled by visitor)
        # Track attributes found in this class
        class_attributes: Dict[str, int] = {}  # attr_name -> first_seen_line

        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Extract method entity
                method_entity = self._create_method_entity(item, node)
                self.entities.append(method_entity)
                methods_map[item.name] = method_entity.qualified_name

                # Extract attributes (self.x) from this method
                self._extract_method_attributes(item, node, class_attributes)

                # Create attribute USES relationships for this method
                self._create_attribute_usage_relationships(item, node, method_entity)

                # Extract decorates relationships for this method
                self._create_decorator_relationships(item, method_entity)

                # Extract nested functions within this method
                nested_funcs = self._extract_nested_functions(
                    item, parent_qualified=method_entity.qualified_name
                )
                self.entities.extend(nested_funcs)

        # Store class methods for override detection
        self._class_methods[class_key] = (node, methods_map)

        # Create attribute entities for this class
        for attr_name, first_line in class_attributes.items():
            attr_qualified = f"{self.file_path}::{node.name}:{node.lineno}.{attr_name}"
            attr_entity = AttributeEntity(
                name=attr_name,
                qualified_name=attr_qualified,
                file_path=self.file_path,
                line_start=first_line,
                line_end=first_line,
                is_class_attribute=False,
            )
            self.entities.append(attr_entity)

        # Continue visiting nested classes (they will be handled by recursive visit_ClassDef)
        for item in node.body:
            if isinstance(item, ast.ClassDef):
                self.visit(item)

        # Pop class from stack
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Extract top-level functions (methods handled in visit_ClassDef)."""
        # Skip if inside a class (methods are handled in visit_ClassDef)
        if self._class_stack:
            return

        func_entity = self._create_function_entity(node)
        self.entities.append(func_entity)

        # Extract decorates relationships for this function
        self._create_decorator_relationships(node, func_entity)

        # Extract nested functions
        nested_funcs = self._extract_nested_functions(
            node, parent_qualified=func_entity.qualified_name
        )
        self.entities.extend(nested_funcs)

        # Continue visiting children (but skip inner functions, they're handled above)
        # Don't call generic_visit to avoid processing function body twice

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Handle async functions same as regular functions."""
        # Skip if inside a class (async methods handled in visit_ClassDef)
        if self._class_stack:
            return

        func_entity = self._create_function_entity(node)
        self.entities.append(func_entity)

        # Extract decorates relationships for this function
        self._create_decorator_relationships(node, func_entity)

        # Extract nested functions
        nested_funcs = self._extract_nested_functions(
            node, parent_qualified=func_entity.qualified_name
        )
        self.entities.extend(nested_funcs)

    def finalize(self) -> None:
        """Finalize extraction by creating cross-entity relationships.

        Call this after visiting the entire tree to create relationships that
        require knowledge of all entities (inheritance, overrides).
        """
        self._create_inheritance_relationships()
        self._create_override_relationships()

    # =========================================================================
    # Entity Creation Methods
    # =========================================================================

    def _create_class_entity(self, node: ast.ClassDef) -> ClassEntity:
        """Create a ClassEntity from an AST ClassDef node."""
        qualified_name = f"{self.file_path}::{node.name}:{node.lineno}"
        docstring = ast.get_docstring(node)

        # Scan docstring for secrets
        if docstring:
            docstring = self._scan_and_redact_text(docstring, node.lineno)

        # Check if abstract
        is_abstract = any(
            isinstance(base, ast.Name) and base.id == "ABC" for base in node.bases
        )

        # Extract decorators
        decorators = [self._get_decorator_name(dec) for dec in node.decorator_list]

        return ClassEntity(
            name=node.name,
            qualified_name=qualified_name,
            file_path=self.file_path,
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
            docstring=docstring,
            is_abstract=is_abstract,
            complexity=self._calculate_complexity(node),
            decorators=decorators,
        )

    def _create_method_entity(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, class_node: ast.ClassDef
    ) -> FunctionEntity:
        """Create a FunctionEntity for a method inside a class."""
        class_qualified = f"{class_node.name}:{class_node.lineno}"
        return self._create_function_entity(node, class_name=class_qualified)

    def _create_function_entity(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        class_name: Optional[str] = None,
    ) -> FunctionEntity:
        """Create a FunctionEntity from an AST FunctionDef node."""
        # Extract all decorators
        decorators: List[str] = []
        decorator_suffix = ""

        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Name):
                decorator_name = decorator.id
                decorators.append(decorator_name)
                if decorator_name == "property":
                    decorator_suffix = "@property"
            elif isinstance(decorator, ast.Attribute):
                decorator_name = ast.unparse(decorator)
                decorators.append(decorator_name)
                if decorator.attr in ("setter", "deleter", "getter"):
                    decorator_suffix = f"@{decorator.attr}"
            elif isinstance(decorator, ast.Call):
                decorator_name = ast.unparse(decorator)
                decorators.append(decorator_name)
            else:
                try:
                    decorator_name = ast.unparse(decorator)
                    decorators.append(decorator_name)
                except:
                    decorators.append("unknown_decorator")

        # Build qualified name
        if class_name:
            base_name = f"{self.file_path}::{class_name}.{node.name}"
        else:
            base_name = f"{self.file_path}::{node.name}"

        if decorator_suffix:
            qualified_name = f"{base_name}{decorator_suffix}:{node.lineno}"
        else:
            qualified_name = f"{base_name}:{node.lineno}"

        docstring = ast.get_docstring(node)
        if docstring:
            docstring = self._scan_and_redact_text(docstring, node.lineno)

        # Extract parameters
        parameters = [arg.arg for arg in node.args.args]
        parameter_types = {}
        for arg in node.args.args:
            if arg.annotation:
                parameter_types[arg.arg] = ast.unparse(arg.annotation)

        return_type = ast.unparse(node.returns) if node.returns else None

        is_static = "staticmethod" in decorators
        is_classmethod = "classmethod" in decorators
        is_property = any(
            d in decorators
            for d in ["property", "property.setter", "property.deleter", "property.getter"]
        )

        return FunctionEntity(
            name=node.name,
            qualified_name=qualified_name,
            file_path=self.file_path,
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
            docstring=docstring,
            parameters=parameters,
            parameter_types=parameter_types,
            return_type=return_type,
            complexity=self._calculate_complexity(node),
            is_async=isinstance(node, ast.AsyncFunctionDef),
            decorators=decorators,
            is_method=class_name is not None,
            is_static=is_static,
            is_classmethod=is_classmethod,
            is_property=is_property,
        )

    def _extract_nested_functions(
        self,
        parent_node: ast.FunctionDef | ast.AsyncFunctionDef,
        parent_qualified: str,
    ) -> List[FunctionEntity]:
        """Extract nested functions from within a function body."""
        nested_entities = []

        for node in parent_node.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified_name = f"{parent_qualified}.{node.name}:{node.lineno}"

                docstring = ast.get_docstring(node)
                if docstring:
                    docstring = self._scan_and_redact_text(docstring, node.lineno)

                parameters = [arg.arg for arg in node.args.args]
                parameter_types = {}
                for arg in node.args.args:
                    if arg.annotation:
                        parameter_types[arg.arg] = ast.unparse(arg.annotation)

                return_type = ast.unparse(node.returns) if node.returns else None
                decorators = [self._get_decorator_name(dec) for dec in node.decorator_list]

                is_static = "staticmethod" in decorators
                is_classmethod = "classmethod" in decorators
                is_property = any(
                    d in decorators
                    for d in ["property", "property.setter", "property.deleter", "property.getter"]
                )

                nested_func = FunctionEntity(
                    name=node.name,
                    qualified_name=qualified_name,
                    file_path=self.file_path,
                    line_start=node.lineno,
                    line_end=node.end_lineno or node.lineno,
                    docstring=docstring,
                    parameters=parameters,
                    parameter_types=parameter_types,
                    return_type=return_type,
                    complexity=self._calculate_complexity(node),
                    is_async=isinstance(node, ast.AsyncFunctionDef),
                    decorators=decorators,
                    is_method=False,
                    is_static=is_static,
                    is_classmethod=is_classmethod,
                    is_property=is_property,
                )
                nested_entities.append(nested_func)

                # Recursively extract deeper nested functions
                deeper = self._extract_nested_functions(node, qualified_name)
                nested_entities.extend(deeper)

        return nested_entities

    # =========================================================================
    # Relationship Creation Methods
    # =========================================================================

    def _extract_method_attributes(
        self,
        method_node: ast.FunctionDef | ast.AsyncFunctionDef,
        class_node: ast.ClassDef,
        class_attributes: Dict[str, int],
    ) -> None:
        """Extract self.x attribute accesses from a method, updating class_attributes."""
        for child in ast.walk(method_node):
            if isinstance(child, ast.Attribute):
                if isinstance(child.value, ast.Name) and child.value.id == "self":
                    attr_name = child.attr
                    if attr_name not in class_attributes:
                        class_attributes[attr_name] = class_node.lineno

    def _create_attribute_usage_relationships(
        self,
        method_node: ast.FunctionDef | ast.AsyncFunctionDef,
        class_node: ast.ClassDef,
        method_entity: FunctionEntity,
    ) -> None:
        """Create USES relationships from method to attributes it accesses."""
        accessed_attrs: set[str] = set()

        for child in ast.walk(method_node):
            if isinstance(child, ast.Attribute):
                if isinstance(child.value, ast.Name) and child.value.id == "self":
                    accessed_attrs.add(child.attr)

        for attr_name in accessed_attrs:
            attr_qualified = f"{self.file_path}::{class_node.name}:{class_node.lineno}.{attr_name}"
            self.relationships.append(
                Relationship(
                    source_id=method_entity.qualified_name,
                    target_id=attr_qualified,
                    rel_type=RelationshipType.USES,
                    properties={
                        "attribute_name": attr_name,
                        "class_name": class_node.name,
                    },
                )
            )

    def _create_decorator_relationships(
        self,
        func_node: ast.FunctionDef | ast.AsyncFunctionDef,
        func_entity: FunctionEntity,
    ) -> None:
        """Create DECORATES relationships from decorators to functions."""
        for decorator in func_node.decorator_list:
            decorator_name = self._resolve_decorator_name(decorator)
            if decorator_name:
                self.relationships.append(
                    Relationship(
                        source_id=decorator_name,
                        target_id=func_entity.qualified_name,
                        rel_type=RelationshipType.DECORATES,
                        properties={
                            "line": decorator.lineno,
                            "decorator_type": type(decorator).__name__,
                        },
                    )
                )

    def _create_inheritance_relationships(self) -> None:
        """Create INHERITS relationships between classes.

        Called during finalize() after all classes have been visited.
        """
        # Build a map of class_name -> (line_number) for local classes
        local_classes: Dict[str, int] = {}
        for (class_name, line), _ in self._class_bases.items():
            local_classes[class_name] = line

        # Now create inheritance relationships
        for (class_name, class_line), base_names in self._class_bases.items():
            child_qualified = f"{self.file_path}::{class_name}:{class_line}"

            for idx, base_name in enumerate(base_names):
                # Determine the target qualified name
                if base_name in local_classes:
                    # Intra-file inheritance
                    base_lineno = local_classes[base_name]
                    base_qualified = f"{self.file_path}::{base_name}:{base_lineno}"
                else:
                    # External base class
                    base_qualified = base_name

                self.relationships.append(
                    Relationship(
                        source_id=child_qualified,
                        target_id=base_qualified,
                        rel_type=RelationshipType.INHERITS,
                        properties={
                            "base_class": base_name,
                            "line": class_line,
                            "order": idx,
                        },
                    )
                )

    def _create_override_relationships(self) -> None:
        """Create OVERRIDES relationships for methods that override parent methods.

        Called during finalize() after all classes have been visited.
        """
        # Build lookup: class_name -> (line, methods_map)
        class_lookup: Dict[str, Tuple[int, Dict[str, str]]] = {}
        for class_key, (class_node, methods_map) in self._class_methods.items():
            # class_key is "ClassName:line"
            name, line_str = class_key.rsplit(":", 1)
            class_lookup[name] = (int(line_str), methods_map)

        # For each class, check if its methods override parent methods
        for (class_name, class_line), base_names in self._class_bases.items():
            if class_name not in class_lookup:
                continue

            _, child_methods = class_lookup[class_name]

            for base_name in base_names:
                if base_name not in class_lookup:
                    continue  # Parent not in this file

                _, parent_methods = class_lookup[base_name]

                for method_name, child_method_qualified in child_methods.items():
                    if method_name in parent_methods:
                        # Found an override
                        if method_name.startswith("__") and method_name.endswith("__"):
                            continue  # Skip dunder methods

                        parent_method_qualified = parent_methods[method_name]
                        self.relationships.append(
                            Relationship(
                                source_id=child_method_qualified,
                                target_id=parent_method_qualified,
                                rel_type=RelationshipType.OVERRIDES,
                                properties={
                                    "method_name": method_name,
                                    "child_class": class_name,
                                    "parent_class": base_name,
                                },
                            )
                        )

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _scan_and_redact_text(self, text: str, line_number: int) -> Optional[str]:
        """Scan text for secrets and apply policy."""
        if not self.secrets_scanner:
            return text

        scan_result = self.secrets_scanner.scan_string(
            text,
            context=f"{self.file_path}:{line_number}",
            filename=self.file_path,
            line_offset=line_number,
        )
        return apply_secrets_policy(scan_result, self.secrets_policy, self.file_path)

    def _get_decorator_name(self, decorator: ast.expr) -> str:
        """Extract decorator name from decorator AST node."""
        if isinstance(decorator, ast.Name):
            return decorator.id
        elif isinstance(decorator, ast.Attribute):
            return ast.unparse(decorator)
        elif isinstance(decorator, ast.Call):
            return ast.unparse(decorator)
        else:
            try:
                return ast.unparse(decorator)
            except:
                return "unknown_decorator"

    def _resolve_decorator_name(self, decorator: ast.expr) -> Optional[str]:
        """Resolve decorator name for relationship creation."""
        if isinstance(decorator, ast.Name):
            return decorator.id
        elif isinstance(decorator, ast.Attribute):
            parts = []
            node = decorator
            while isinstance(node, ast.Attribute):
                parts.append(node.attr)  # O(1) append instead of O(n) insert(0)
                node = node.value
            if isinstance(node, ast.Name):
                parts.append(node.id)
            return ".".join(reversed(parts)) if parts else None
        elif isinstance(decorator, ast.Call):
            return self._resolve_decorator_name(decorator.func)
        return None

    def _get_base_class_name(self, node: ast.expr) -> Optional[str]:
        """Extract base class name from AST node."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            parts = []
            current = node
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            return ".".join(reversed(parts))
        elif isinstance(node, ast.Subscript):
            return self._get_base_class_name(node.value)
        return None

    def _calculate_complexity(self, node: ast.AST) -> int:
        """Calculate cyclomatic complexity of a code block."""
        try:
            from repotoire_fast import calculate_complexity_fast

            source = ast.unparse(node)
            result = calculate_complexity_fast(source)
            if result is not None:
                return result
        except (ImportError, Exception):
            pass

        # Fallback to Python implementation
        complexity = 1
        for child in ast.walk(node):
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
                complexity += len(child.values) - 1
        return complexity


class PythonParser(CodeParser):
    """Parser for Python source files."""

    def __init__(self, secrets_policy: SecretsPolicy = SecretsPolicy.REDACT) -> None:
        """Initialize Python parser.

        Args:
            secrets_policy: Policy for handling detected secrets (default: REDACT)
        """
        self.file_entity: Optional[FileEntity] = None
        self.entity_map: Dict[str, str] = {}  # qualified_name -> entity_id
        self.secrets_policy = secrets_policy
        self.secrets_scanner = SecretsScanner() if secrets_policy != SecretsPolicy.WARN else None
        # Cached content from pipeline (avoids redundant file reads)
        self._cached_content: Optional[Tuple[str, bytes, str]] = None  # (path, content_bytes, hash)
        # Relationships extracted by visitor (used by extract_relationships)
        self._visitor_relationships: List[Relationship] = []

    def set_cached_content(self, file_path: str, content: bytes, file_hash: str) -> None:
        """Set cached content for next file processing.

        When set, the parser will use this cached content instead of reading
        from disk. The cache is automatically cleared after processing.

        Args:
            file_path: Path to the file (for validation)
            content: Raw file content as bytes
            file_hash: Pre-computed MD5 hash of the content
        """
        self._cached_content = (file_path, content, file_hash)

    def _clear_cached_content(self) -> None:
        """Clear the cached content after use."""
        self._cached_content = None

    def parse(self, file_path: str) -> ast.AST:
        """Parse Python file into AST.

        Uses cached content if available (set via set_cached_content),
        otherwise reads from disk.

        Args:
            file_path: Path to Python file

        Returns:
            Python AST
        """
        # Use cached content if available and matches this file
        if self._cached_content and self._cached_content[0] == file_path:
            source = self._cached_content[1].decode("utf-8")
        else:
            with open(file_path, "r", encoding="utf-8") as f:
                source = f.read()
        return ast.parse(source, filename=file_path)

    def _scan_and_redact_text(self, text: Optional[str], context: str, line_number: int) -> Optional[str]:
        """Scan text for secrets and apply policy.

        Args:
            text: Text to scan (docstring, comment, etc.)
            context: Context for logging (e.g., "file.py")
            line_number: Line number where text appears

        Returns:
            Redacted text, or None if should skip this entity

        Raises:
            ValueError: If policy is FAIL and secrets were found
        """
        if not text or not self.secrets_scanner:
            return text

        # Scan the text
        scan_result = self.secrets_scanner.scan_string(
            text,
            context=f"{context}:{line_number}",
            filename=context,
            line_offset=line_number
        )

        # Apply policy
        return apply_secrets_policy(scan_result, self.secrets_policy, context)

    def extract_entities(self, tree: ast.AST, file_path: str) -> List[Entity]:
        """Extract entities from Python AST using single-pass visitor.

        Args:
            tree: Python AST
            file_path: Path to source file

        Returns:
            List of entities (File, Class, Function, Attribute)
        """
        entities: List[Entity] = []

        # Create file entity (not part of visitor since it needs file metadata)
        file_entity = self._create_file_entity(file_path, tree)
        entities.append(file_entity)
        self.file_entity = file_entity

        # Use single-pass visitor for classes, functions, methods, and attributes
        # This consolidates 7+ ast.walk() traversals into a single pass
        visitor = PythonASTVisitor(
            file_path=file_path,
            secrets_scanner=self.secrets_scanner,
            secrets_policy=self.secrets_policy,
        )

        # Visit only module-level nodes (visitor handles nesting internally)
        for node in tree.body:
            visitor.visit(node)

        # Finalize to create cross-entity relationships (inheritance, overrides)
        visitor.finalize()

        # Collect entities from visitor
        entities.extend(visitor.entities)

        # Store visitor relationships for use by extract_relationships()
        self._visitor_relationships = visitor.relationships

        # Extract module entities from imports (only tree.body, not ast.walk)
        module_entities = self._extract_modules(tree, file_path)
        entities.extend(module_entities)

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

        # Extract imports (only module-level, not nested in functions/classes)
        file_entity_name = file_path  # Use file path as qualified name for File node

        # Use tree.body to only get module-level statements
        for node in tree.body:
            if isinstance(node, ast.Import):
                # Handle: import module [as alias]
                for alias in node.names:
                    module_name = alias.name
                    # Create IMPORTS relationship from File to module
                    relationships.append(
                        Relationship(
                            source_id=file_entity_name,
                            target_id=module_name,  # Will be mapped to Module node
                            rel_type=RelationshipType.IMPORTS,
                            properties={
                                "alias": alias.asname if alias.asname else None,
                                "line": node.lineno,
                            },
                        )
                    )

            elif isinstance(node, ast.ImportFrom):
                # Handle: from module import name [as alias]
                module_name = node.module or ""  # node.module can be None for "from . import"
                level = node.level  # Relative import level (0 = absolute, 1+ = relative)

                for alias in node.names:
                    imported_name = alias.name

                    # Try to resolve to actual entity
                    target_qualified_name = None

                    # Strategy 1: Check if entity is in current file
                    for entity_qname, entity in entity_map.items():
                        if entity.name == imported_name:
                            target_qualified_name = entity_qname
                            break

                    # Strategy 2: For cross-file imports, store both the import-style name
                    # and mark it for later resolution. The pipeline will need to fix these up.
                    # For now, we create the relationship with a special format that includes
                    # both the module path and the imported name, which Neo4j can match later.
                    if not target_qualified_name:
                        if module_name:
                            # Create a combined identifier: "module.name|imported_name"
                            # This allows matching against entities with that name in the right module
                            target_qualified_name = f"{module_name}.{imported_name}"
                        else:
                            target_qualified_name = imported_name

                    relationships.append(
                        Relationship(
                            source_id=file_entity_name,
                            target_id=target_qualified_name,
                            rel_type=RelationshipType.IMPORTS,
                            properties={
                                "alias": alias.asname if alias.asname else None,
                                "from_module": module_name,
                                "imported_name": imported_name,
                                "relative_level": level,
                                "line": node.lineno,
                                "is_cross_file": target_qualified_name.startswith(module_name) if module_name else False,
                            },
                        )
                    )

        # Extract function calls - need to track which function makes each call
        self._extract_calls(tree, file_path, entity_map, relationships)

        # Add relationships extracted by visitor (inheritance, overrides, attribute usage, decorates)
        # These were collected during extract_entities() in a single-pass AST traversal
        relationships.extend(self._visitor_relationships)

        # Create CONTAINS relationships (hierarchical: file→top-level, class→members)
        file_qualified_name = file_path
        for entity in entities:
            if entity.node_type == NodeType.FILE:
                continue  # Skip the file itself

            # Determine parent from entity type and qualified name structure
            parent_qname = None

            if isinstance(entity, FunctionEntity) and entity.is_method:
                # Method: parent is the class
                # qualified_name format: "file.py::ClassName:line.method_name:line"
                # Extract parent by splitting on last '.' and handling line number suffix
                if '.' in entity.qualified_name:
                    parent_qname = entity.qualified_name.rsplit('.', 1)[0]

            elif isinstance(entity, AttributeEntity):
                # Class attribute: parent is the class
                # Similar format: "file.py::ClassName:line.attribute_name"
                if '.' in entity.qualified_name:
                    parent_qname = entity.qualified_name.rsplit('.', 1)[0]

            else:
                # Top-level entity (class, module, top-level function)
                parent_qname = file_qualified_name

            if parent_qname:
                relationships.append(
                    Relationship(
                        source_id=parent_qname,
                        target_id=entity.qualified_name,
                        rel_type=RelationshipType.CONTAINS,
                    )
                )

        return relationships

    def _create_file_entity(self, file_path: str, tree: ast.AST) -> FileEntity:
        """Create file entity.

        Uses cached content/hash if available (set via set_cached_content),
        otherwise reads from disk. Clears the cache after creating the entity.

        Args:
            file_path: Path to file
            tree: AST

        Returns:
            FileEntity
        """
        path_obj = Path(file_path)

        # Use cached content/hash if available and matches this file
        if self._cached_content and self._cached_content[0] == file_path:
            content_bytes = self._cached_content[1]
            file_hash = self._cached_content[2]
            # Decode for LOC counting
            content_text = content_bytes.decode("utf-8")
            loc = len([line for line in content_text.split("\n") if line.strip()])
            # Clear cache after use
            self._clear_cached_content()
        else:
            # Fallback: read from disk (for non-pipeline usage)
            with open(file_path, "rb") as f:
                content_bytes = f.read()
                file_hash = hashlib.md5(content_bytes).hexdigest()
            # Count lines of code
            content_text = content_bytes.decode("utf-8")
            loc = len([line for line in content_text.split("\n") if line.strip()])

        # Extract __all__ exports
        exports = self._extract_exports(tree)

        # Get last modification time
        from datetime import datetime
        last_modified = datetime.fromtimestamp(path_obj.stat().st_mtime)

        return FileEntity(
            name=path_obj.name,
            qualified_name=file_path,
            file_path=file_path,
            line_start=1,
            line_end=loc,
            language="python",
            loc=loc,
            hash=file_hash,
            last_modified=last_modified,
            exports=exports,
        )

    def _extract_calls(
        self,
        tree: ast.AST,
        file_path: str,
        entity_map: Dict[str, Entity],
        relationships: List[Relationship],
    ) -> None:
        """Extract function call relationships from AST.

        Args:
            tree: Python AST
            file_path: Path to source file
            entity_map: Map of qualified_name to Entity
            relationships: List to append relationships to
        """

        class CallVisitor(ast.NodeVisitor):
            """AST visitor to track function calls and references within their scope."""

            def __init__(self, file_path: str):
                self.file_path = file_path
                self.current_class: Optional[str] = None
                self.current_class_line: Optional[int] = None
                self.function_stack: List[tuple[str, int]] = []  # Stack for nested functions (name, line)
                self.calls: List[tuple[str, str, int, bool]] = []  # (caller, callee, line, is_self_call)
                self.uses: List[tuple[str, str, int]] = []  # (user, used_function, line) for function references

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                """Visit class definition."""
                old_class = self.current_class
                old_class_line = self.current_class_line
                self.current_class = node.name
                self.current_class_line = node.lineno
                self.generic_visit(node)
                self.current_class = old_class
                self.current_class_line = old_class_line

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                """Visit function definition."""
                self.function_stack.append((node.name, node.lineno))
                self.generic_visit(node)
                self.function_stack.pop()

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                """Visit async function definition."""
                self.function_stack.append((node.name, node.lineno))
                self.generic_visit(node)
                self.function_stack.pop()

            def visit_Call(self, node: ast.Call) -> None:
                """Visit function call."""
                if self.function_stack:
                    # Build function qualified name from stack with line numbers
                    # For nested functions, build full chain: parent.child:line
                    if self.current_class and self.current_class_line:
                        # Method (possibly nested): file.py::ClassName:line.method:line[.nested:line...]
                        caller = f"{self.file_path}::{self.current_class}:{self.current_class_line}"
                        for func_name, func_line in self.function_stack:
                            caller += f".{func_name}:{func_line}"
                    else:
                        # Top-level function (possibly nested): file.py::func:line[.nested:line...]
                        first_func_name, first_func_line = self.function_stack[0]
                        caller = f"{self.file_path}::{first_func_name}:{first_func_line}"
                        # Add nested functions if any
                        for func_name, func_line in self.function_stack[1:]:
                            caller += f".{func_name}:{func_line}"

                    # Determine callee name (best effort)
                    result = self._get_call_name(node)
                    if result:
                        callee, is_self_call = result
                        self.calls.append((caller, callee, node.lineno, is_self_call))

                    # Track function references passed as arguments
                    for arg in node.args:
                        if isinstance(arg, ast.Name):
                            # Function passed as argument: some_func(my_function)
                            self.uses.append((caller, arg.id, node.lineno))

                self.generic_visit(node)

            def visit_Name(self, node: ast.Name) -> None:
                """Visit name reference - track function references (not calls)."""
                # Only track if we're inside a function and the name is being loaded/used
                if self.function_stack and isinstance(node.ctx, (ast.Load, ast.Store)):
                    # Check if this Name node is part of a Call node
                    # We do this by checking parent context (simplified heuristic)
                    # Skip if it's the function being called in a Call expression
                    # This is detected in visit_Call, so we don't duplicate
                    pass  # Name tracking happens in visit_Return and visit_arg

                self.generic_visit(node)

            def visit_Return(self, node: ast.Return) -> None:
                """Visit return statement - track functions being returned."""
                if self.function_stack and node.value:
                    # Build current function qualified name
                    if self.current_class and self.current_class_line:
                        user = f"{self.file_path}::{self.current_class}:{self.current_class_line}"
                        for func_name, func_line in self.function_stack:
                            user += f".{func_name}:{func_line}"
                    else:
                        first_func_name, first_func_line = self.function_stack[0]
                        user = f"{self.file_path}::{first_func_name}:{first_func_line}"
                        for func_name, func_line in self.function_stack[1:]:
                            user += f".{func_name}:{func_line}"

                    # Check if returning a function reference
                    if isinstance(node.value, ast.Name):
                        # return some_function
                        self.uses.append((user, node.value.id, node.lineno))

                self.generic_visit(node)

            def visit_arg(self, node: ast.arg) -> None:
                """Visit function argument in a Call node."""
                # Track function references passed as arguments
                # This is complex to track perfectly, so we use a simplified approach
                # The visit method for arguments inside Call will help track these
                self.generic_visit(node)

            def _get_call_name(self, node: ast.Call) -> Optional[tuple[str, bool]]:
                """Extract the name of what's being called.

                Args:
                    node: Call AST node

                Returns:
                    Tuple of (called name, is_self_call) or None
                """
                func = node.func
                if isinstance(func, ast.Name):
                    # Simple call: foo()
                    return (func.id, False)
                elif isinstance(func, ast.Attribute):
                    # Method call: obj.method()
                    # Check if it's a self call
                    if isinstance(func.value, ast.Name) and func.value.id == "self":
                        # self.method() -> return just method name and flag as self call
                        return (func.attr, True)

                    # Try to build qualified name
                    parts = []
                    current = func
                    while isinstance(current, ast.Attribute):
                        parts.append(current.attr)
                        current = current.value
                    if isinstance(current, ast.Name):
                        parts.append(current.id)
                    return (".".join(reversed(parts)), False)
                return None

        # Visit tree and collect calls
        visitor = CallVisitor(file_path)
        visitor.visit(tree)

        # Create CALLS relationships
        for caller, callee, line, is_self_call in visitor.calls:
            # Try to resolve callee to a qualified name in our entity map
            callee_qualified = None

            if is_self_call:
                # For self.method() calls, resolve to the method in the current class
                # Extract class from caller: "file.py::ClassName:123.method_name:456"
                if "::" in caller and "." in caller:
                    # Get the part between :: and the first .
                    parts = caller.split("::")
                    if len(parts) > 1:
                        class_part = parts[1].split(".")[0]  # "ClassName:123"
                        # Look for method in this class
                        method_pattern = f"::{class_part}.{callee}:"
                        for qname in entity_map.keys():
                            if method_pattern in qname:
                                callee_qualified = qname
                                break

            if not callee_qualified:
                # Try different matching strategies
                # 1. Exact name match - prioritize classes for capitalized names
                is_likely_class = callee and callee[0].isupper()

                if is_likely_class:
                    # First try to match a Class entity
                    for qname, entity in entity_map.items():
                        if entity.node_type == NodeType.CLASS and entity.name == callee:
                            callee_qualified = qname
                            break

                if not callee_qualified:
                    # Then try any entity with matching name
                    for qname, entity in entity_map.items():
                        if entity.name == callee:
                            callee_qualified = qname
                            break

            if not callee_qualified:
                # 2. Check if callee ends with the qualified name pattern
                for qname in entity_map.keys():
                    if qname.endswith(f"::{callee}:") or qname.endswith(f".{callee}:"):
                        callee_qualified = qname
                        break

            if not callee_qualified:
                # 3. For simple function names, check if it's in the same file
                for qname, entity in entity_map.items():
                    if qname.startswith(file_path) and entity.name == callee:
                        callee_qualified = qname
                        break

            # If not found, use the callee name as-is (might be external)
            if not callee_qualified:
                callee_qualified = callee

            relationships.append(
                Relationship(
                    source_id=caller,
                    target_id=callee_qualified,
                    rel_type=RelationshipType.CALLS,
                    properties={"line": line, "call_name": callee, "is_self_call": is_self_call},
                )
            )

        # Create USES relationships for function references (not calls)
        for user, used_func_name, line in visitor.uses:
            # Try to resolve the function reference
            used_qualified = None

            # Try to find the function in entity_map
            for qname, entity in entity_map.items():
                if entity.node_type == NodeType.FUNCTION and entity.name == used_func_name:
                    # Prefer functions in the same file
                    if qname.startswith(file_path):
                        used_qualified = qname
                        break

            # If not found in same file, try any file
            if not used_qualified:
                for qname, entity in entity_map.items():
                    if entity.node_type == NodeType.FUNCTION and entity.name == used_func_name:
                        used_qualified = qname
                        break

            if used_qualified:
                relationships.append(
                    Relationship(
                        source_id=user,
                        target_id=used_qualified,
                        rel_type=RelationshipType.USES,
                        properties={"line": line, "reference_type": "function_reference"},
                    )
                )

    def _extract_modules(self, tree: ast.AST, file_path: str) -> List[ModuleEntity]:
        """Extract Module entities from import statements.

        Args:
            tree: Python AST
            file_path: Path to source file

        Returns:
            List of ModuleEntity objects
        """
        modules: Dict[str, ModuleEntity] = {}  # Deduplicate by qualified name

        # Only scan module-level imports
        for node in tree.body:
            if isinstance(node, ast.Import):
                # import foo, bar
                for alias in node.names:
                    module_name = alias.name
                    if module_name not in modules:
                        modules[module_name] = ModuleEntity(
                            name=module_name.split(".")[-1],  # Last component
                            qualified_name=module_name,
                            file_path=file_path,  # Source file that imports it
                            line_start=node.lineno,
                            line_end=node.lineno,
                            is_external=True,  # Assume external for now
                            package=self._get_package_name(module_name),
                        )

            elif isinstance(node, ast.ImportFrom):
                # from foo import bar
                module_name = node.module or ""  # Can be None for relative imports

                # Create module entity for the "from" module if it exists
                if module_name and module_name not in modules:
                    modules[module_name] = ModuleEntity(
                        name=module_name.split(".")[-1],
                        qualified_name=module_name,
                        file_path=file_path,
                        line_start=node.lineno,
                        line_end=node.lineno,
                        is_external=True,
                        package=self._get_package_name(module_name),
                    )

                # Also create entities for imported items if they look like modules
                # (e.g., "from typing import List" - List is not a module)
                # For now, we'll skip this and only create the parent module

        # Detect dynamic imports (importlib.import_module, __import__)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                module_name = self._extract_dynamic_import(node)
                if module_name and module_name not in modules:
                    modules[module_name] = ModuleEntity(
                        name=module_name.split(".")[-1],
                        qualified_name=module_name,
                        file_path=file_path,
                        line_start=node.lineno,
                        line_end=node.lineno,
                        is_external=True,
                        package=self._get_package_name(module_name),
                        is_dynamic_import=True,
                    )

        return list(modules.values())

    def _extract_dynamic_import(self, node: ast.Call) -> Optional[str]:
        """Extract module name from dynamic import call.

        Handles:
        - importlib.import_module("module_name")
        - __import__("module_name")

        Args:
            node: Call AST node

        Returns:
            Module name if dynamic import detected, None otherwise
        """
        # Check for importlib.import_module()
        if isinstance(node.func, ast.Attribute):
            if node.func.attr == "import_module":
                # Check if it's importlib.import_module
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "importlib":
                    # Get the module name from first argument
                    if node.args and isinstance(node.args[0], ast.Constant):
                        return node.args[0].value

        # Check for __import__()
        elif isinstance(node.func, ast.Name) and node.func.id == "__import__":
            # Get the module name from first argument
            if node.args and isinstance(node.args[0], ast.Constant):
                return node.args[0].value

        return None

    def _get_package_name(self, module_name: str) -> Optional[str]:
        """Extract parent package name from module name.

        Args:
            module_name: Fully qualified module name (e.g., "os.path")

        Returns:
            Parent package name (e.g., "os") or None
        """
        if "." in module_name:
            return module_name.rsplit(".", 1)[0]
        return None

    def _extract_exports(self, tree: ast.AST) -> List[str]:
        """Extract __all__ exports from module.

        Args:
            tree: Python AST

        Returns:
            List of exported names
        """
        exports: List[str] = []

        # Look for __all__ assignment at module level
        if hasattr(tree, "body"):
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    # Check if target is __all__
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == "__all__":
                            # Extract the list of names
                            if isinstance(node.value, ast.List):
                                for elt in node.value.elts:
                                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                        exports.append(elt.value)
                                    elif isinstance(elt, ast.Str):  # Python 3.7 compatibility
                                        exports.append(elt.s)
                elif isinstance(node, ast.AnnAssign):
                    # Typed assignment: __all__: List[str] = [...]
                    if isinstance(node.target, ast.Name) and node.target.id == "__all__":
                        if isinstance(node.value, ast.List):
                            for elt in node.value.elts:
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    exports.append(elt.value)
                                elif isinstance(elt, ast.Str):
                                    exports.append(elt.s)

        return exports

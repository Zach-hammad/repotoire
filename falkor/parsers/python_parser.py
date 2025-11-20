"""Python code parser using AST module."""

import ast
from pathlib import Path
from typing import Any, List, Dict, Optional
import hashlib

from falkor.parsers.base import CodeParser
from falkor.models import (
    Entity,
    FileEntity,
    ModuleEntity,
    ClassEntity,
    FunctionEntity,
    VariableEntity,
    AttributeEntity,
    Relationship,
    NodeType,
    RelationshipType,
    SecretsPolicy,
)
from falkor.security import SecretsScanner
from falkor.security.secrets_scanner import apply_secrets_policy


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

                # Extract methods from class - pass class qualified name with line number
                class_qualified_name = f"{node.name}:{node.lineno}"
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_entity = self._extract_function(
                            item, file_path, class_name=class_qualified_name
                        )
                        entities.append(method_entity)

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Only top-level functions (not methods)
                if self._is_top_level(node, tree):
                    func_entity = self._extract_function(node, file_path)
                    entities.append(func_entity)

        # Extract module entities from imports
        module_entities = self._extract_modules(tree, file_path)
        entities.extend(module_entities)

        # Extract attributes (self.x) from class methods
        attribute_entities = self._extract_attributes(tree, file_path)
        entities.extend(attribute_entities)

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
                    # For "from foo import bar", create qualified name "foo.bar"
                    if module_name:
                        qualified_import = f"{module_name}.{imported_name}"
                    else:
                        qualified_import = imported_name

                    relationships.append(
                        Relationship(
                            source_id=file_entity_name,
                            target_id=qualified_import,
                            rel_type=RelationshipType.IMPORTS,
                            properties={
                                "alias": alias.asname if alias.asname else None,
                                "from_module": module_name,
                                "imported_name": imported_name,
                                "relative_level": level,
                                "line": node.lineno,
                            },
                        )
                    )

        # Extract function calls - need to track which function makes each call
        self._extract_calls(tree, file_path, entity_map, relationships)

        # Extract class inheritance relationships
        self._extract_inheritance(tree, file_path, relationships)

        # Extract method override relationships
        self._extract_overrides(tree, file_path, entity_map, relationships)

        # Extract USES relationships (methods accessing attributes)
        self._extract_attribute_usage(tree, file_path, entity_map, relationships)

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

    def _get_decorator_name(self, decorator: ast.expr) -> str:
        """Extract decorator name from decorator AST node.

        Args:
            decorator: Decorator AST node

        Returns:
            Decorator name as string
        """
        if isinstance(decorator, ast.Name):
            # Simple decorator: @property, @staticmethod
            return decorator.id
        elif isinstance(decorator, ast.Attribute):
            # Attribute decorator: @property.setter
            return ast.unparse(decorator)
        elif isinstance(decorator, ast.Call):
            # Decorator with arguments: @decorator(arg1, arg2)
            return ast.unparse(decorator)
        else:
            # Other decorator types
            try:
                return ast.unparse(decorator)
            except:
                return "unknown_decorator"

    def _extract_class(self, node: ast.ClassDef, file_path: str) -> ClassEntity:
        """Extract class entity from AST node.

        Args:
            node: ClassDef AST node
            file_path: Path to source file

        Returns:
            ClassEntity
        """
        # Include line number to handle nested classes with same name
        qualified_name = f"{file_path}::{node.name}:{node.lineno}"
        docstring = ast.get_docstring(node)

        # Scan docstring for secrets
        if docstring:
            docstring = self._scan_and_redact_text(docstring, file_path, node.lineno)

        # Check if abstract
        is_abstract = any(
            isinstance(base, ast.Name) and base.id == "ABC" for base in node.bases
        )

        # Extract decorators
        decorators = [self._get_decorator_name(dec) for dec in node.decorator_list]

        return ClassEntity(
            name=node.name,
            qualified_name=qualified_name,
            file_path=file_path,
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
            docstring=docstring,
            is_abstract=is_abstract,
            complexity=self._calculate_complexity(node),
            decorators=decorators,
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
        # Extract all decorators
        decorators: List[str] = []
        decorator_suffix = ""

        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Name):
                # Simple decorator: @property, @staticmethod, etc.
                decorator_name = decorator.id
                decorators.append(decorator_name)
                if decorator_name == "property":
                    decorator_suffix = "@property"
            elif isinstance(decorator, ast.Attribute):
                # Attribute decorator: @property.setter, @functools.lru_cache
                decorator_name = ast.unparse(decorator)
                decorators.append(decorator_name)
                if decorator.attr in ("setter", "deleter", "getter"):
                    decorator_suffix = f"@{decorator.attr}"
            elif isinstance(decorator, ast.Call):
                # Decorator with arguments: @decorator(arg1, arg2)
                decorator_name = ast.unparse(decorator)
                decorators.append(decorator_name)
            else:
                # Other decorator types
                try:
                    decorator_name = ast.unparse(decorator)
                    decorators.append(decorator_name)
                except:
                    decorators.append("unknown_decorator")

        # Build qualified name with line number to ensure uniqueness
        # Format: file::class.function@decorator:line
        if class_name:
            base_name = f"{file_path}::{class_name}.{node.name}"
        else:
            base_name = f"{file_path}::{node.name}"

        # Add decorator suffix if present
        if decorator_suffix:
            qualified_name = f"{base_name}{decorator_suffix}:{node.lineno}"
        else:
            # Add line number to handle same-name functions/methods
            qualified_name = f"{base_name}:{node.lineno}"

        docstring = ast.get_docstring(node)

        # Scan docstring for secrets
        if docstring:
            docstring = self._scan_and_redact_text(docstring, file_path, node.lineno)

        # Extract parameters
        parameters = [arg.arg for arg in node.args.args]

        # Extract parameter type annotations
        parameter_types = {}
        for arg in node.args.args:
            if arg.annotation:
                parameter_types[arg.arg] = ast.unparse(arg.annotation)

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
            parameter_types=parameter_types,
            return_type=return_type,
            complexity=self._calculate_complexity(node),
            is_async=isinstance(node, ast.AsyncFunctionDef),
            decorators=decorators,
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
            """AST visitor to track function calls within their scope."""

            def __init__(self, file_path: str):
                self.file_path = file_path
                self.current_class: Optional[str] = None
                self.current_class_line: Optional[int] = None
                self.function_stack: List[tuple[str, int]] = []  # Stack for nested functions (name, line)
                self.calls: List[tuple[str, str, int]] = []  # (caller, callee, line)

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
                    # For nested functions, use just the innermost function
                    func_name, func_line = self.function_stack[-1]

                    # Determine caller qualified name with line number
                    if self.current_class and self.current_class_line:
                        caller = f"{self.file_path}::{self.current_class}:{self.current_class_line}.{func_name}:{func_line}"
                    else:
                        caller = f"{self.file_path}::{func_name}:{func_line}"

                    # Determine callee name (best effort)
                    callee = self._get_call_name(node)
                    if callee:
                        self.calls.append((caller, callee, node.lineno))

                self.generic_visit(node)

            def _get_call_name(self, node: ast.Call) -> Optional[str]:
                """Extract the name of what's being called.

                Args:
                    node: Call AST node

                Returns:
                    Called name or None
                """
                func = node.func
                if isinstance(func, ast.Name):
                    # Simple call: foo()
                    return func.id
                elif isinstance(func, ast.Attribute):
                    # Method call: obj.method()
                    # Try to build qualified name
                    parts = []
                    current = func
                    while isinstance(current, ast.Attribute):
                        parts.append(current.attr)
                        current = current.value
                    if isinstance(current, ast.Name):
                        parts.append(current.id)
                    return ".".join(reversed(parts))
                return None

        # Visit tree and collect calls
        visitor = CallVisitor(file_path)
        visitor.visit(tree)

        # Create CALLS relationships
        for caller, callee, line in visitor.calls:
            # Try to resolve callee to a qualified name in our entity map
            callee_qualified = None

            # Check if it's a direct reference to an entity in this file
            for qname, entity in entity_map.items():
                if entity.name == callee or qname.endswith(f"::{callee}"):
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
                    properties={"line": line, "call_name": callee},
                )
            )

    def _extract_inheritance(
        self,
        tree: ast.AST,
        file_path: str,
        relationships: List[Relationship],
    ) -> None:
        """Extract class inheritance relationships from AST.

        Args:
            tree: Python AST
            file_path: Path to source file
            relationships: List to append relationships to
        """
        # Build a map of class names to their line numbers
        local_classes = {}  # {class_name: line_number}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                local_classes[node.name] = node.lineno

        # Now extract inheritance relationships
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                # Use qualified name with line number
                child_class_qualified = f"{file_path}::{node.name}:{node.lineno}"

                # Extract base classes
                for idx, base in enumerate(node.bases):
                    # Try to get the base class name
                    base_name = self._get_base_class_name(base)
                    if base_name:
                        # Determine the target qualified name
                        # If base class is defined in this file, need to find its line number
                        if base_name in local_classes:
                            # Intra-file inheritance - include line number to match qualified name format
                            base_lineno = local_classes[base_name]
                            base_qualified = f"{file_path}::{base_name}:{base_lineno}"
                        else:
                            # Imported or external base class
                            # Use the name as extracted (e.g., "ABC", "typing.Generic", etc.)
                            base_qualified = base_name

                        relationships.append(
                            Relationship(
                                source_id=child_class_qualified,
                                target_id=base_qualified,
                                rel_type=RelationshipType.INHERITS,
                                properties={
                                    "base_class": base_name,
                                    "line": node.lineno,
                                    "order": idx,  # MRO order (important for multiple inheritance)
                                },
                            )
                        )

    def _get_base_class_name(self, node: ast.expr) -> Optional[str]:
        """Extract base class name from AST node.

        Args:
            node: AST expression node representing base class

        Returns:
            Base class name or None
        """
        if isinstance(node, ast.Name):
            # Simple inheritance: class Foo(Bar)
            return node.id
        elif isinstance(node, ast.Attribute):
            # Qualified inheritance: class Foo(module.Bar)
            parts = []
            current = node
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            return ".".join(reversed(parts))
        elif isinstance(node, ast.Subscript):
            # Generic inheritance: class Foo(Generic[T])
            # Extract the base type without the subscript
            return self._get_base_class_name(node.value)
        return None

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

    def _extract_overrides(
        self,
        tree: ast.AST,
        file_path: str,
        entity_map: Dict[str, Entity],
        relationships: List[Relationship],
    ) -> None:
        """Extract method override relationships from AST.

        Detects when a method in a child class overrides a method in a parent class.
        Only works for intra-file inheritance (both classes in same file).

        Args:
            tree: Python AST
            file_path: Path to source file
            entity_map: Map of qualified_name to Entity
            relationships: List to append relationships to
        """
        # Build a map of class_name -> class_node -> methods
        # Use (class_name, line_number) as key to handle nested classes with same name
        class_info: Dict[tuple[str, int], tuple[ast.ClassDef, Dict[str, str]]] = {}

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                # Extract method names for this class
                methods: Dict[str, str] = {}  # method_name -> qualified_name
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        # Use new qualified name format with class line number
                        method_qualified = f"{file_path}::{node.name}:{node.lineno}.{item.name}:{item.lineno}"
                        methods[item.name] = method_qualified

                class_info[(node.name, node.lineno)] = (node, methods)

        # Now check for overrides
        for (class_name, class_line), (class_node, child_methods) in class_info.items():
            # Check each base class
            for base in class_node.bases:
                base_name = self._get_base_class_name(base)
                if not base_name:
                    continue

                # Check if base class is defined in this file
                # Need to find the base class by name in class_info
                parent_info = None
                for (cname, cline), (cnode, cmethods) in class_info.items():
                    if cname == base_name:
                        parent_info = (cnode, cmethods)
                        break

                if parent_info:
                    parent_node, parent_methods = parent_info

                    # Check for method overrides
                    for method_name, child_method_qualified in child_methods.items():
                        if method_name in parent_methods:
                            # Found an override!
                            parent_method_qualified = parent_methods[method_name]

                            # Skip special methods like __init__ (common but not interesting)
                            if method_name.startswith("__") and method_name.endswith("__"):
                                continue

                            relationships.append(
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

    def _extract_attributes(self, tree: ast.AST, file_path: str) -> List[AttributeEntity]:
        """Extract attributes (self.x) accessed in class methods.

        Args:
            tree: Python AST
            file_path: Path to source file

        Returns:
            List of AttributeEntity objects
        """
        attributes: Dict[str, AttributeEntity] = {}  # qualified_name -> entity

        # Walk through all classes
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                class_name = node.name
                class_line = node.lineno

                # Find all self.attribute accesses in this class's methods
                class_attributes = set()

                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        # Walk through method body to find self.x accesses
                        for child in ast.walk(item):
                            if isinstance(child, ast.Attribute):
                                # Check if it's self.something
                                if isinstance(child.value, ast.Name) and child.value.id == "self":
                                    attr_name = child.attr
                                    class_attributes.add(attr_name)

                # Create AttributeEntity for each unique attribute
                for attr_name in class_attributes:
                    # qualified_name: file::ClassName:line.attribute_name
                    qualified_name = f"{file_path}::{class_name}:{class_line}.{attr_name}"

                    if qualified_name not in attributes:
                        attributes[qualified_name] = AttributeEntity(
                            name=attr_name,
                            qualified_name=qualified_name,
                            file_path=file_path,
                            line_start=class_line,  # Use class line since we don't know exact attribute definition line
                            line_end=class_line,
                            is_class_attribute=False,  # These are instance attributes
                        )

        return list(attributes.values())

    def _extract_attribute_usage(
        self,
        tree: ast.AST,
        file_path: str,
        entity_map: Dict[str, Entity],
        relationships: List[Relationship],
    ) -> None:
        """Extract USES relationships from methods to attributes.

        Args:
            tree: Python AST
            file_path: Path to source file
            entity_map: Map of qualified_name to Entity
            relationships: List to append relationships to
        """
        # Walk through all classes
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                class_name = node.name
                class_line = node.lineno

                # Process each method
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_name = item.name
                        method_line = item.lineno

                        # Method qualified name with line number
                        method_qualified = f"{file_path}::{class_name}:{class_line}.{method_name}:{method_line}"

                        # Find all self.attribute accesses in this method
                        accessed_attributes = set()
                        for child in ast.walk(item):
                            if isinstance(child, ast.Attribute):
                                if isinstance(child.value, ast.Name) and child.value.id == "self":
                                    attr_name = child.attr
                                    accessed_attributes.add(attr_name)

                        # Create USES relationship for each accessed attribute
                        for attr_name in accessed_attributes:
                            attr_qualified = f"{file_path}::{class_name}:{class_line}.{attr_name}"

                            relationships.append(
                                Relationship(
                                    source_id=method_qualified,
                                    target_id=attr_qualified,
                                    rel_type=RelationshipType.USES,
                                    properties={
                                        "attribute_name": attr_name,
                                        "class_name": class_name,
                                    },
                                )
                            )

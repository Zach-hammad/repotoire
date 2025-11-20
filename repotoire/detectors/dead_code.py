"""Dead code detector - finds unused functions and classes."""

import uuid
from typing import List, Set
from datetime import datetime

from repotoire.detectors.base import CodeSmellDetector
from repotoire.models import Finding, Severity


class DeadCodeDetector(CodeSmellDetector):
    """Detects dead code (functions/classes with zero incoming references)."""

    # Common entry points that should not be flagged as dead code
    ENTRY_POINTS = {
        "main",
        "__main__",
        "__init__",
        "setUp",
        "tearDown",
        "test_",  # Prefix for test functions
    }

    # Common decorator patterns that indicate a function is used
    DECORATOR_PATTERNS = {
        "route",  # Flask/FastAPI routes
        "app",  # General app decorators
        "task",  # Celery/background tasks
        "api",  # API endpoints
        "endpoint",  # API endpoints
        "command",  # CLI commands
        "listener",  # Event listeners
        "handler",  # Event handlers
        "callback",  # Callbacks
        "register",  # Registration decorators
        "property",  # Properties
        "classmethod",  # Class methods
        "staticmethod",  # Static methods
    }

    # Special methods that are called implicitly
    MAGIC_METHODS = {
        "__str__",
        "__repr__",
        "__enter__",
        "__exit__",
        "__call__",
        "__len__",
        "__iter__",
        "__next__",
        "__getitem__",
        "__setitem__",
        "__delitem__",
        "__eq__",
        "__ne__",
        "__lt__",
        "__le__",
        "__gt__",
        "__ge__",
        "__hash__",
        "__bool__",
        "__add__",
        "__sub__",
        "__mul__",
        "__truediv__",
        "__floordiv__",
        "__mod__",
        "__pow__",
        "__post_init__",  # dataclass post-initialization
        "__init_subclass__",  # subclass initialization
        "__set_name__",  # descriptor protocol
    }

    def detect(self) -> List[Finding]:
        """Find dead code (unused functions and classes).

        Looks for Function and Class nodes with zero incoming CALLS relationships
        and not imported by any file.

        Returns:
            List of findings for dead code
        """
        findings: List[Finding] = []

        # Find unused functions
        function_findings = self._find_dead_functions()
        findings.extend(function_findings)

        # Find unused classes
        class_findings = self._find_dead_classes()
        findings.extend(class_findings)

        return findings

    def _find_dead_functions(self) -> List[Finding]:
        """Find functions that are never called.

        Returns:
            List of findings for dead functions
        """
        findings: List[Finding] = []

        query = """
        MATCH (f:Function)
        WHERE NOT (f)<-[:CALLS]-()
          AND NOT (f)<-[:USES]-()
          AND NOT (f.name STARTS WITH 'test_')
          AND NOT f.name IN ['main', '__main__', '__init__', 'setUp', 'tearDown']
          // Filter out methods that override base class methods (polymorphism)
          AND NOT EXISTS {
              MATCH (c:Class)-[:CONTAINS]->(f)
              MATCH (c)-[:INHERITS*]->(base:Class)
              MATCH (base)-[:CONTAINS]->(base_method:Function {name: f.name})
          }
          // Filter out public API methods (not starting with _)
          AND (f.is_method = false OR f.name STARTS WITH '_')
          // Filter out functions that are imported (check by name in import properties)
          AND NOT EXISTS {
              MATCH ()-[imp:IMPORTS]->()
              WHERE imp.imported_name = f.name
          }
        OPTIONAL MATCH (file:File)-[:CONTAINS]->(f)
        WITH f, file, COALESCE(f.decorators, []) AS decorators
        // Filter out functions with decorators or in __all__
        WHERE size(decorators) = 0
          AND NOT (file.exports IS NOT NULL AND f.name IN file.exports)
          // Filter out test fixtures and examples
          AND NOT (file.filePath STARTS WITH 'tests/fixtures/' OR file.filePath CONTAINS '/tests/fixtures/')
          AND NOT (file.filePath STARTS WITH 'examples/' OR file.filePath CONTAINS '/examples/')
          AND NOT (file.filePath STARTS WITH 'test_fixtures/' OR file.filePath CONTAINS '/test_fixtures/')
        RETURN f.qualifiedName AS qualified_name,
               f.name AS name,
               f.filePath AS file_path,
               f.lineStart AS line_start,
               f.complexity AS complexity,
               file.filePath AS containing_file,
               decorators
        ORDER BY f.complexity DESC
        LIMIT 100
        """

        results = self.db.execute_query(query)

        for record in results:
            # Filter out magic methods
            name = record["name"]
            if name in self.MAGIC_METHODS:
                continue

            # Check if it's an entry point (exact match or prefix)
            if name in self.ENTRY_POINTS or any(name.startswith(ep) for ep in ["test_"]):
                continue

            # Additional check: filter out common decorator patterns in the name
            # (e.g., handle_event, on_click, etc.)
            if any(pattern in name.lower() for pattern in ["handle", "on_", "callback"]):
                continue

            # Filter out loader/factory pattern methods (often called dynamically)
            if any(pattern in name.lower() for pattern in ["load_data", "loader", "_loader", "load_", "create_", "build_", "make_"]):
                continue

            # Filter out parse/process methods that might be called via registry
            if name.startswith("_parse_") or name.startswith("_process_"):
                continue

            # Filter out common public API functions (config, setup, validation)
            if any(pattern in name.lower() for pattern in ["load_config", "generate_", "validate_", "setup_", "initialize_"]):
                continue

            # Filter out converter/transformation methods
            if any(pattern in name.lower() for pattern in ["to_dict", "to_json", "from_dict", "from_json", "serialize", "deserialize"]):
                continue

            # Filter out common internal helper method patterns
            # These are private methods that are almost always called internally
            # but may not have CALLS relationships due to incomplete extraction
            if name.startswith("_extract_") or name.startswith("_find_") or name.startswith("_calculate_"):
                continue

            # Filter out other common internal patterns
            if name.startswith("_get_") or name.startswith("_set_") or name.startswith("_check_"):
                continue

            finding_id = str(uuid.uuid4())
            qualified_name = record["qualified_name"]
            file_path = record["containing_file"] or record["file_path"]
            complexity = record["complexity"] or 0

            finding = Finding(
                id=finding_id,
                detector="DeadCodeDetector",
                severity=self._calculate_function_severity(complexity),
                title=f"Unused function: {name}",
                description=(
                    f"Function '{name}' is never called in the codebase. "
                    f"It has complexity {complexity} and may be safe to remove."
                ),
                affected_nodes=[qualified_name],
                affected_files=[file_path],
                graph_context={
                    "type": "function",
                    "name": name,
                    "complexity": complexity,
                    "line_start": record["line_start"],
                },
                suggested_fix=(
                    f"If this function is truly unused:\n"
                    f"1. Remove the function from {file_path.split('/')[-1]}\n"
                    f"2. Check for dynamic calls (getattr, eval) that might use it\n"
                    f"3. Verify it's not an API endpoint or callback"
                ),
                estimated_effort="Small (15-30 minutes)",
                created_at=datetime.now(),
            )

            findings.append(finding)

        return findings

    def _find_dead_classes(self) -> List[Finding]:
        """Find classes that are never instantiated or inherited from.

        Returns:
            List of findings for dead classes
        """
        findings: List[Finding] = []

        query = """
        MATCH (file:File)-[:CONTAINS]->(c:Class)
        WHERE NOT (c)<-[:CALLS]-()  // Not instantiated directly
          AND NOT (c)<-[:INHERITS]-()  // Not inherited from
          AND NOT (c)<-[:USES]-()  // Not used in type hints
          // Check for CALLS via call_name property (cross-file calls)
          AND NOT EXISTS {
              MATCH ()-[call:CALLS]->()
              WHERE call.call_name = c.name
          }
          // Filter out classes that are imported (check by name in import properties)
          AND NOT EXISTS {
              MATCH ()-[imp:IMPORTS]->()
              WHERE imp.imported_name = c.name
          }
        OPTIONAL MATCH (file)-[:CONTAINS]->(m:Function)
        WHERE m.qualifiedName STARTS WITH c.qualifiedName + '.'
        WITH c, file, count(m) AS method_count, COALESCE(c.decorators, []) AS decorators
        // Filter out classes with decorators or in __all__
        WHERE size(decorators) = 0
          AND NOT (file.exports IS NOT NULL AND c.name IN file.exports)
          // Filter out test fixtures and examples
          AND NOT (file.filePath STARTS WITH 'tests/fixtures/' OR file.filePath CONTAINS '/tests/fixtures/')
          AND NOT (file.filePath STARTS WITH 'examples/' OR file.filePath CONTAINS '/examples/')
          AND NOT (file.filePath STARTS WITH 'test_fixtures/' OR file.filePath CONTAINS '/test_fixtures/')
        RETURN c.qualifiedName AS qualified_name,
               c.name AS name,
               c.filePath AS file_path,
               c.complexity AS complexity,
               file.filePath AS containing_file,
               method_count
        ORDER BY method_count DESC, c.complexity DESC
        LIMIT 50
        """

        results = self.db.execute_query(query)

        for record in results:
            name = record["name"]

            # Skip common base classes
            if name in ["ABC", "Enum", "Exception", "BaseException"]:
                continue

            # Skip exception classes (often raised without instantiation)
            if name.endswith("Error") or name.endswith("Exception"):
                continue

            # Skip mixin classes (used for multiple inheritance)
            if name.endswith("Mixin") or "Mixin" in name:
                continue

            # Skip test classes (test classes often have fixtures that aren't "called")
            if name.startswith("Test") or name.endswith("Test"):
                continue

            finding_id = str(uuid.uuid4())
            qualified_name = record["qualified_name"]
            file_path = record["containing_file"] or record["file_path"]
            complexity = record["complexity"] or 0
            method_count = record["method_count"] or 0

            finding = Finding(
                id=finding_id,
                detector="DeadCodeDetector",
                severity=self._calculate_class_severity(method_count, complexity),
                title=f"Unused class: {name}",
                description=(
                    f"Class '{name}' is never instantiated or inherited from. "
                    f"It has {method_count} methods and complexity {complexity}."
                ),
                affected_nodes=[qualified_name],
                affected_files=[file_path],
                graph_context={
                    "type": "class",
                    "name": name,
                    "complexity": complexity,
                    "method_count": method_count,
                },
                suggested_fix=(
                    f"If this class is truly unused:\n"
                    f"1. Remove the class and its {method_count} methods\n"
                    f"2. Check for dynamic instantiation (factory patterns, reflection)\n"
                    f"3. Verify it's not used in configuration or plugins"
                ),
                estimated_effort=self._estimate_class_removal_effort(method_count),
                created_at=datetime.now(),
            )

            findings.append(finding)

        return findings

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity based on complexity and size.

        Args:
            finding: Finding to assess

        Returns:
            Severity level
        """
        context = finding.graph_context
        complexity = context.get("complexity", 0)
        method_count = context.get("method_count", 0)

        if context.get("type") == "class":
            return self._calculate_class_severity(method_count, complexity)
        else:
            return self._calculate_function_severity(complexity)

    def _calculate_function_severity(self, complexity: int) -> Severity:
        """Calculate severity for dead function.

        Higher complexity = higher severity (more wasted code).

        Args:
            complexity: Cyclomatic complexity

        Returns:
            Severity level
        """
        if complexity >= 20:
            return Severity.HIGH
        elif complexity >= 10:
            return Severity.MEDIUM
        else:
            return Severity.LOW

    def _calculate_class_severity(self, method_count: int, complexity: int) -> Severity:
        """Calculate severity for dead class.

        Args:
            method_count: Number of methods in class
            complexity: Total complexity

        Returns:
            Severity level
        """
        if method_count >= 10 or complexity >= 50:
            return Severity.HIGH
        elif method_count >= 5 or complexity >= 20:
            return Severity.MEDIUM
        else:
            return Severity.LOW

    def _estimate_class_removal_effort(self, method_count: int) -> str:
        """Estimate effort to remove a class.

        Args:
            method_count: Number of methods

        Returns:
            Effort estimate
        """
        if method_count >= 10:
            return "Medium (2-4 hours)"
        elif method_count >= 5:
            return "Small (1-2 hours)"
        else:
            return "Small (30 minutes)"

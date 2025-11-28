"""Dead code detector - finds unused functions and classes.

Supports cross-detector validation with VultureDetector (REPO-153).
When both graph-based and AST-based detection agree, confidence exceeds 95%.
"""

import uuid
from typing import List, Optional, Dict, Set
from datetime import datetime

from repotoire.detectors.base import CodeSmellDetector
from repotoire.models import CollaborationMetadata, Finding, Severity
from repotoire.graph.enricher import GraphEnricher


class DeadCodeDetector(CodeSmellDetector):
    """Detects dead code (functions/classes with zero incoming references).

    Supports cross-validation with VultureDetector for high-confidence findings.
    When both detectors agree, confidence reaches 95%+ enabling safe auto-removal.
    """

    def __init__(self, neo4j_client, detector_config: Optional[dict] = None, enricher: Optional[GraphEnricher] = None):
        """Initialize dead code detector.

        Args:
            neo4j_client: Neo4j database client
            detector_config: Optional detector configuration
            enricher: Optional GraphEnricher for cross-detector collaboration
        """
        super().__init__(neo4j_client)
        self.enricher = enricher

        # Cross-validation confidence thresholds
        self.base_confidence = 0.70  # Graph-only confidence
        self.validated_confidence = 0.95  # When Vulture confirms

        # FalkorDB doesn't support EXISTS {} subqueries
        self.is_falkordb = type(neo4j_client).__name__ == "FalkorDBClient"

    @property
    def needs_previous_findings(self) -> bool:
        """DeadCodeDetector needs VultureDetector findings for cross-validation.

        When both graph-based and AST-based (Vulture) detection agree,
        confidence exceeds 95%, enabling safe auto-removal recommendations.
        """
        return True

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

    def detect(self, previous_findings: Optional[List[Finding]] = None) -> List[Finding]:
        """Find dead code (unused functions and classes).

        Looks for Function and Class nodes with zero incoming CALLS relationships
        and not imported by any file.

        Args:
            previous_findings: Optional list of findings from previous detectors
                             (used for cross-validation with VultureDetector)

        Returns:
            List of findings for dead code
        """
        # Build set of Vulture-confirmed unused items for cross-validation
        vulture_unused = self._extract_vulture_unused(previous_findings)

        findings: List[Finding] = []

        # Find unused functions
        function_findings = self._find_dead_functions(vulture_unused)
        findings.extend(function_findings)

        # Find unused classes
        class_findings = self._find_dead_classes(vulture_unused)
        findings.extend(class_findings)

        return findings

    def _extract_vulture_unused(
        self,
        previous_findings: Optional[List[Finding]]
    ) -> Dict[str, Dict]:
        """Extract Vulture-confirmed unused items from previous findings.

        Args:
            previous_findings: List of findings from previous detectors

        Returns:
            Dict mapping (file_path, name) -> vulture finding info
        """
        vulture_unused: Dict[str, Dict] = {}

        if not previous_findings:
            return vulture_unused

        for finding in previous_findings:
            if finding.detector != "VultureDetector":
                continue

            # Extract item info from graph_context
            ctx = finding.graph_context or {}
            item_name = ctx.get("item_name")
            item_type = ctx.get("item_type")
            vulture_confidence = ctx.get("confidence", 0)

            if not item_name:
                continue

            # Get file path from affected_files
            file_path = finding.affected_files[0] if finding.affected_files else None
            if not file_path:
                continue

            # Create lookup key
            key = f"{file_path}:{item_name}"
            vulture_unused[key] = {
                "name": item_name,
                "type": item_type,
                "confidence": vulture_confidence,
                "file": file_path,
                "line": ctx.get("line"),
            }

            # Also store by just name for fuzzy matching
            vulture_unused[item_name] = vulture_unused[key]

        return vulture_unused

    def _check_vulture_confirms(
        self,
        name: str,
        file_path: str,
        vulture_unused: Dict[str, Dict]
    ) -> Optional[Dict]:
        """Check if Vulture also flagged this item as unused.

        Args:
            name: Function/class name
            file_path: File path
            vulture_unused: Dict of Vulture-confirmed unused items

        Returns:
            Vulture finding info if confirmed, None otherwise
        """
        # Try exact match first
        key = f"{file_path}:{name}"
        if key in vulture_unused:
            return vulture_unused[key]

        # Try name-only match (less precise but catches more)
        if name in vulture_unused:
            return vulture_unused[name]

        return None

    def _find_dead_functions(self, vulture_unused: Dict[str, Dict]) -> List[Finding]:
        """Find functions that are never called.

        Args:
            vulture_unused: Dict of Vulture-confirmed unused items for cross-validation

        Returns:
            List of findings for dead functions
        """
        findings: List[Finding] = []

        if self.is_falkordb:
            # FalkorDB-compatible query (no EXISTS subqueries)
            query = """
            MATCH (f:Function)
            WHERE NOT (f)<-[:CALLS]-()
              AND NOT (f)<-[:USES]-()
              AND NOT (f.name STARTS WITH 'test_')
              AND NOT f.name IN ['main', '__main__', '__init__', 'setUp', 'tearDown']
              AND (f.is_method = false OR f.name STARTS WITH '_')
            OPTIONAL MATCH (file:File)-[:CONTAINS]->(f)
            WITH f, file, COALESCE(f.decorators, []) AS decorators
            WHERE size(decorators) = 0
              AND NOT (file.exports IS NOT NULL AND f.name IN file.exports)
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
        else:
            # Neo4j query with EXISTS subqueries for better accuracy
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

            # Filter out pytest/mock side_effect functions (common pattern in tests)
            # These are assigned to mock.side_effect which the detector doesn't track
            if name.endswith("_side_effect") or name.endswith("_effect"):
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

            # Cross-validation with Vulture (REPO-153)
            vulture_match = self._check_vulture_confirms(name, file_path, vulture_unused)
            vulture_confirmed = vulture_match is not None

            # Calculate confidence based on validation
            if vulture_confirmed:
                confidence = self.validated_confidence  # 95% when both agree
                vulture_conf = vulture_match.get("confidence", 0)
                validators = ["graph_analysis", "vulture"]
                safe_to_remove = True
            else:
                confidence = self.base_confidence  # 70% graph-only
                vulture_conf = 0
                validators = ["graph_analysis"]
                safe_to_remove = False

            severity = self._calculate_function_severity(complexity)

            # Build description with validation info
            description = f"Function '{name}' is never called in the codebase. "
            description += f"It has complexity {complexity}."
            if vulture_confirmed:
                description += f"\n\n**Cross-validated**: Both graph analysis and Vulture agree this is unused."
                description += f"\n**Confidence**: {confidence*100:.0f}% ({len(validators)} validators agree)"
                description += f"\n**Safe to remove**: Yes"
            else:
                description += f"\n\n**Confidence**: {confidence*100:.0f}% (graph analysis only)"
                description += f"\n**Recommendation**: Review before removing"

            # Build suggested fix based on confidence
            if safe_to_remove:
                suggested_fix = (
                    f"**SAFE TO REMOVE** (confidence: {confidence*100:.0f}%)\n"
                    f"Both graph analysis and Vulture confirm this function is unused.\n"
                    f"1. Delete the function from {file_path.split('/')[-1]}\n"
                    f"2. Run tests to verify nothing breaks"
                )
            else:
                suggested_fix = (
                    f"**REVIEW REQUIRED** (confidence: {confidence*100:.0f}%)\n"
                    f"1. Remove the function from {file_path.split('/')[-1]}\n"
                    f"2. Check for dynamic calls (getattr, eval) that might use it\n"
                    f"3. Verify it's not an API endpoint or callback"
                )

            finding = Finding(
                id=finding_id,
                detector="DeadCodeDetector",
                severity=severity,
                title=f"Unused function: {name}",
                description=description,
                affected_nodes=[qualified_name],
                affected_files=[file_path],
                graph_context={
                    "type": "function",
                    "name": name,
                    "complexity": complexity,
                    "line_start": record["line_start"],
                    "vulture_confirmed": vulture_confirmed,
                    "vulture_confidence": vulture_conf,
                    "validators": validators,
                    "safe_to_remove": safe_to_remove,
                    "confidence": confidence,
                },
                suggested_fix=suggested_fix,
                estimated_effort="Small (15-30 minutes)" if safe_to_remove else "Small (30-60 minutes)",
                created_at=datetime.now(),
            )

            # Add collaboration metadata (REPO-150 Phase 1) with cross-validation info
            evidence = ["unused_function", "no_calls"]
            if vulture_confirmed:
                evidence.append("vulture_confirmed")
            tags = ["dead_code", "unused_code", "maintenance"]
            if safe_to_remove:
                tags.append("safe_to_remove")
            else:
                tags.append("review_required")

            finding.add_collaboration_metadata(CollaborationMetadata(
                detector="DeadCodeDetector",
                confidence=confidence,
                evidence=evidence,
                tags=tags
            ))

            # Flag entity in graph for cross-detector collaboration (REPO-151 Phase 2)
            if self.enricher:
                try:
                    self.enricher.flag_entity(
                        entity_qualified_name=qualified_name,
                        detector="DeadCodeDetector",
                        severity=severity.value,
                        issues=["unused_function"],
                        confidence=confidence,
                        metadata={k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v for k, v in {"complexity": complexity, "type": "function"}.items()}
                    )
                except Exception:
                    pass

            findings.append(finding)

        return findings

    def _find_dead_classes(self, vulture_unused: Dict[str, Dict]) -> List[Finding]:
        """Find classes that are never instantiated or inherited from.

        Args:
            vulture_unused: Dict of Vulture-confirmed unused items for cross-validation

        Returns:
            List of findings for dead classes
        """
        findings: List[Finding] = []

        if self.is_falkordb:
            # FalkorDB-compatible query (no EXISTS subqueries)
            query = """
            MATCH (file:File)-[:CONTAINS]->(c:Class)
            WHERE NOT (c)<-[:CALLS]-()
              AND NOT (c)<-[:INHERITS]-()
              AND NOT (c)<-[:USES]-()
            OPTIONAL MATCH (file)-[:CONTAINS]->(m:Function)
            WHERE m.qualifiedName STARTS WITH c.qualifiedName + '.'
            WITH c, file, count(m) AS method_count, COALESCE(c.decorators, []) AS decorators
            WHERE size(decorators) = 0
              AND NOT (file.exports IS NOT NULL AND c.name IN file.exports)
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
        else:
            # Neo4j query with EXISTS subqueries for better accuracy
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

            # Cross-validation with Vulture (REPO-153)
            vulture_match = self._check_vulture_confirms(name, file_path, vulture_unused)
            vulture_confirmed = vulture_match is not None

            # Calculate confidence based on validation
            if vulture_confirmed:
                confidence = self.validated_confidence  # 95% when both agree
                vulture_conf = vulture_match.get("confidence", 0)
                validators = ["graph_analysis", "vulture"]
                safe_to_remove = True
            else:
                confidence = self.base_confidence  # 70% graph-only
                vulture_conf = 0
                validators = ["graph_analysis"]
                safe_to_remove = False

            severity = self._calculate_class_severity(method_count, complexity)

            # Build description with validation info
            description = f"Class '{name}' is never instantiated or inherited from. "
            description += f"It has {method_count} methods and complexity {complexity}."
            if vulture_confirmed:
                description += f"\n\n**Cross-validated**: Both graph analysis and Vulture agree this is unused."
                description += f"\n**Confidence**: {confidence*100:.0f}% ({len(validators)} validators agree)"
                description += f"\n**Safe to remove**: Yes"
            else:
                description += f"\n\n**Confidence**: {confidence*100:.0f}% (graph analysis only)"
                description += f"\n**Recommendation**: Review before removing"

            # Build suggested fix based on confidence
            if safe_to_remove:
                suggested_fix = (
                    f"**SAFE TO REMOVE** (confidence: {confidence*100:.0f}%)\n"
                    f"Both graph analysis and Vulture confirm this class is unused.\n"
                    f"1. Delete the class and its {method_count} methods\n"
                    f"2. Run tests to verify nothing breaks"
                )
            else:
                suggested_fix = (
                    f"**REVIEW REQUIRED** (confidence: {confidence*100:.0f}%)\n"
                    f"1. Remove the class and its {method_count} methods\n"
                    f"2. Check for dynamic instantiation (factory patterns, reflection)\n"
                    f"3. Verify it's not used in configuration or plugins"
                )

            finding = Finding(
                id=finding_id,
                detector="DeadCodeDetector",
                severity=severity,
                title=f"Unused class: {name}",
                description=description,
                affected_nodes=[qualified_name],
                affected_files=[file_path],
                graph_context={
                    "type": "class",
                    "name": name,
                    "complexity": complexity,
                    "method_count": method_count,
                    "vulture_confirmed": vulture_confirmed,
                    "vulture_confidence": vulture_conf,
                    "validators": validators,
                    "safe_to_remove": safe_to_remove,
                    "confidence": confidence,
                },
                suggested_fix=suggested_fix,
                estimated_effort=self._estimate_class_removal_effort(method_count),
                created_at=datetime.now(),
            )

            # Add collaboration metadata (REPO-150 Phase 1) with cross-validation info
            evidence = ["unused_class", "no_instantiation"]
            if vulture_confirmed:
                evidence.append("vulture_confirmed")
            tags = ["dead_code", "unused_code", "maintenance"]
            if safe_to_remove:
                tags.append("safe_to_remove")
            else:
                tags.append("review_required")

            finding.add_collaboration_metadata(CollaborationMetadata(
                detector="DeadCodeDetector",
                confidence=confidence,
                evidence=evidence,
                tags=tags
            ))

            # Flag entity in graph for cross-detector collaboration (REPO-151 Phase 2)
            if self.enricher:
                try:
                    self.enricher.flag_entity(
                        entity_qualified_name=qualified_name,
                        detector="DeadCodeDetector",
                        severity=severity.value,
                        issues=["unused_class"],
                        confidence=confidence,
                        metadata={k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v for k, v in {"complexity": complexity, "method_count": method_count, "type": "class"}.items()}
                    )
                except Exception:
                    pass

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

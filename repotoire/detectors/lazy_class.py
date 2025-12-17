"""Lazy class detector - identifies underutilized classes.

REPO-222: Detects classes that do minimal work and may be unnecessary abstraction.
The opposite of god classes - these classes have very few methods that do very little.
"""

from typing import List, Dict, Any, Optional

from repotoire.detectors.base import CodeSmellDetector
from repotoire.models import CollaborationMetadata, Finding, Severity
from repotoire.graph.enricher import GraphEnricher
from repotoire.graph.client import Neo4jClient
from repotoire.logging_config import get_logger


class LazyClassDetector(CodeSmellDetector):
    """Detects classes that do minimal work.

    A "lazy class" has very few methods that do very little,
    suggesting the class may be unnecessary abstraction that
    could be inlined into its callers.
    """

    # Detection thresholds
    DEFAULT_THRESHOLDS = {
        "max_methods": 3,              # Few methods
        "max_avg_loc_per_method": 5,   # Short methods
        "min_total_loc": 10,           # Not trivially small (avoid flagging empty classes)
    }

    # Exclude common design patterns that are intentionally thin
    DEFAULT_EXCLUDE_PATTERNS = [
        # Design patterns
        "Adapter", "Wrapper", "Proxy", "Decorator", "Facade", "Bridge",
        # Configuration classes
        "Config", "Settings", "Options", "Preferences",
        # Data transfer objects
        "Request", "Response", "DTO", "Entity", "Model",
        # Exceptions
        "Exception", "Error",
        # Base/abstract classes
        "Base", "Abstract", "Interface", "Mixin",
        # Test classes
        "Test", "Mock", "Stub", "Fake",
        # Protocols and type hints
        "Protocol",
    ]

    def __init__(
        self,
        neo4j_client: Neo4jClient,
        detector_config: Optional[Dict[str, Any]] = None,
        enricher: Optional[GraphEnricher] = None,
    ):
        """Initialize lazy class detector.

        Args:
            neo4j_client: Neo4j database client
            detector_config: Optional configuration dict with thresholds
            enricher: Optional GraphEnricher for cross-detector collaboration
        """
        super().__init__(neo4j_client)
        self.enricher = enricher
        self.logger = get_logger(__name__)

        config = detector_config or {}
        self.max_methods = config.get("max_methods", self.DEFAULT_THRESHOLDS["max_methods"])
        self.max_avg_loc = config.get("max_avg_loc_per_method", self.DEFAULT_THRESHOLDS["max_avg_loc_per_method"])
        self.min_total_loc = config.get("min_total_loc", self.DEFAULT_THRESHOLDS["min_total_loc"])
        self.exclude_patterns = config.get("exclude_patterns", self.DEFAULT_EXCLUDE_PATTERNS)

    def detect(self) -> List[Finding]:
        """Detect lazy classes in the codebase.

        Returns:
            List of findings for lazy classes
        """
        query = """
        MATCH (c:Class)
        WHERE c.name IS NOT NULL

        // Get method count and LOC
        OPTIONAL MATCH (c)-[:CONTAINS]->(m:Function)
        WITH c,
             count(m) AS method_count,
             coalesce(sum(m.loc), 0) AS total_loc,
             collect(m.loc) AS method_locs

        // Filter for lazy class criteria
        WHERE method_count > 0
          AND method_count <= $max_methods
          AND total_loc >= $min_total_loc

        // Calculate average method LOC
        WITH c, method_count, total_loc,
             toFloat(total_loc) / method_count AS avg_method_loc

        WHERE avg_method_loc <= $max_avg_loc

        // Get file path
        OPTIONAL MATCH (c)<-[:CONTAINS*]-(f:File)

        RETURN c.qualifiedName AS qualified_name,
               c.name AS class_name,
               c.lineStart AS line_start,
               c.lineEnd AS line_end,
               method_count,
               total_loc,
               avg_method_loc,
               f.filePath AS file_path
        ORDER BY method_count ASC, total_loc ASC
        LIMIT 50
        """

        try:
            results = self.db.execute_query(
                query,
                {
                    "max_methods": self.max_methods,
                    "max_avg_loc": self.max_avg_loc,
                    "min_total_loc": self.min_total_loc,
                },
            )
        except Exception as e:
            self.logger.error(f"Error executing Lazy Class detection query: {e}")
            return []

        findings = []
        for row in results:
            class_name = row.get("class_name", "")
            qualified_name = row.get("qualified_name", "")

            # Skip excluded patterns
            if self._should_exclude(class_name):
                continue

            finding = self._create_finding(row)
            findings.append(finding)

            # Flag entity in graph for cross-detector collaboration
            if self.enricher and qualified_name:
                try:
                    self.enricher.flag_entity(
                        entity_qualified_name=qualified_name,
                        detector="LazyClassDetector",
                        severity=finding.severity.value,
                        issues=["minimal_functionality"],
                        confidence=0.75,
                        metadata={
                            "method_count": row.get("method_count", 0),
                            "total_loc": row.get("total_loc", 0),
                        },
                    )
                except Exception:
                    pass  # Don't fail detection if enrichment fails

        self.logger.info(f"LazyClassDetector found {len(findings)} lazy classes")
        return findings

    def _should_exclude(self, class_name: str) -> bool:
        """Check if class matches an exclusion pattern.

        Args:
            class_name: Name of the class to check

        Returns:
            True if class should be excluded, False otherwise
        """
        if not class_name:
            return True

        class_name_lower = class_name.lower()
        for pattern in self.exclude_patterns:
            if pattern.lower() in class_name_lower:
                return True

        return False

    def _create_finding(self, row: dict) -> Finding:
        """Create a finding from query result.

        Args:
            row: Query result row

        Returns:
            Finding object
        """
        qualified_name = row.get("qualified_name", "unknown")
        class_name = row.get("class_name", qualified_name.split(".")[-1])
        method_count = row.get("method_count", 0)
        total_loc = row.get("total_loc", 0)
        avg_loc = row.get("avg_method_loc", 0) or 0
        file_path = row.get("file_path", "unknown")
        line_start = row.get("line_start")
        line_end = row.get("line_end")

        description = (
            f"Class '{class_name}' has only {method_count} method(s) "
            f"with an average of {avg_loc:.1f} lines each ({total_loc} total LOC). "
            f"This may indicate unnecessary abstraction."
        )

        recommendation = (
            "Consider one of the following:\n"
            "1. Inline this class's functionality into its callers\n"
            "2. Expand the class with additional functionality\n"
            "3. If this is a deliberate design pattern (Adapter, Facade), "
            "add a docstring explaining its purpose"
        )

        finding = Finding(
            id=f"lazy_class_{qualified_name}",
            detector="LazyClassDetector",
            severity=Severity.LOW,
            title=f"Lazy class: {class_name}",
            description=description,
            affected_nodes=[qualified_name],
            affected_files=[file_path] if file_path != "unknown" else [],
            line_start=line_start,
            line_end=line_end,
            suggested_fix=recommendation,
            estimated_effort="Small (15-30 minutes)",
            graph_context={
                "method_count": method_count,
                "total_loc": total_loc,
                "avg_method_loc": round(avg_loc, 1),
            },
        )

        # Add collaboration metadata
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="LazyClassDetector",
            confidence=0.75,
            evidence=["few_methods", "low_loc"],
            tags=["lazy_class", "design", "refactoring_candidate"],
        ))

        return finding

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity (always LOW for lazy classes).

        Args:
            finding: Finding to assess

        Returns:
            Severity level
        """
        return Severity.LOW

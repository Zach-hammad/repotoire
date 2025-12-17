"""Refused bequest detector - identifies improper inheritance.

REPO-230: Detects classes that inherit but don't use parent functionality.
A "refused bequest" occurs when a child class overrides parent methods
without calling super() or using parent functionality.
"""

from typing import List, Dict, Any, Optional

from repotoire.detectors.base import CodeSmellDetector
from repotoire.models import CollaborationMetadata, Finding, Severity
from repotoire.graph.enricher import GraphEnricher
from repotoire.graph.client import Neo4jClient
from repotoire.logging_config import get_logger


class RefusedBequestDetector(CodeSmellDetector):
    """Detects classes that inherit but don't use parent functionality.

    A "refused bequest" occurs when a child class overrides parent methods
    without calling super() or using parent functionality. This often
    indicates that composition should be used instead of inheritance.
    """

    # Detection thresholds
    DEFAULT_THRESHOLDS = {
        "min_overrides": 2,            # Minimum overrides to consider
        "max_parent_call_ratio": 0.3,  # Flag if <30% call parent
    }

    # Exclude abstract base classes and interfaces
    DEFAULT_EXCLUDE_PARENT_PATTERNS = [
        "ABC", "Abstract", "Interface", "Base", "Mixin",
        "Protocol",  # Python Protocol classes
    ]

    def __init__(
        self,
        neo4j_client: Neo4jClient,
        detector_config: Optional[Dict[str, Any]] = None,
        enricher: Optional[GraphEnricher] = None,
    ):
        """Initialize refused bequest detector.

        Args:
            neo4j_client: Neo4j database client
            detector_config: Optional configuration dict with thresholds
            enricher: Optional GraphEnricher for cross-detector collaboration
        """
        super().__init__(neo4j_client)
        self.enricher = enricher
        self.logger = get_logger(__name__)

        config = detector_config or {}
        self.min_overrides = config.get("min_overrides", self.DEFAULT_THRESHOLDS["min_overrides"])
        self.max_parent_call_ratio = config.get("max_parent_call_ratio", self.DEFAULT_THRESHOLDS["max_parent_call_ratio"])
        self.exclude_parent_patterns = config.get("exclude_parent_patterns", self.DEFAULT_EXCLUDE_PARENT_PATTERNS)

    def detect(self) -> List[Finding]:
        """Detect refused bequest in the codebase.

        Returns:
            List of findings for refused bequest violations
        """
        query = """
        // Find classes that inherit from another class
        MATCH (child:Class)-[:INHERITS]->(parent:Class)
        WHERE parent.name IS NOT NULL
          AND child.name IS NOT NULL

        // Find overridden methods (same name in both child and parent)
        MATCH (child)-[:CONTAINS]->(method:Function)
        WHERE method.name IS NOT NULL

        // Check if method overrides a parent method
        OPTIONAL MATCH (parent)-[:CONTAINS]->(parent_method:Function)
        WHERE parent_method.name = method.name

        // Check if override calls the parent method (super() call)
        OPTIONAL MATCH (method)-[:CALLS]->(parent_method)

        WITH child, parent, method, parent_method,
             CASE WHEN parent_method IS NOT NULL THEN 1 ELSE 0 END AS is_override,
             CASE WHEN (method)-[:CALLS]->(parent_method) THEN 1 ELSE 0 END AS calls_parent

        // Aggregate per child class
        WITH child, parent,
             sum(is_override) AS total_overrides,
             sum(calls_parent) AS overrides_calling_parent

        // Filter for classes with enough overrides
        WHERE total_overrides >= $min_overrides

        // Calculate parent call ratio
        WITH child, parent, total_overrides, overrides_calling_parent,
             CASE WHEN total_overrides > 0
                  THEN toFloat(overrides_calling_parent) / total_overrides
                  ELSE 0 END AS parent_call_ratio

        // Flag classes where most overrides don't call parent
        WHERE parent_call_ratio <= $max_parent_call_ratio

        // Get file path
        OPTIONAL MATCH (child)<-[:CONTAINS*]-(f:File)

        RETURN child.qualifiedName AS child_name,
               child.name AS child_class,
               child.lineStart AS line_start,
               child.lineEnd AS line_end,
               parent.qualifiedName AS parent_name,
               parent.name AS parent_class,
               total_overrides,
               overrides_calling_parent,
               parent_call_ratio,
               f.filePath AS file_path
        ORDER BY total_overrides DESC
        LIMIT 50
        """

        try:
            results = self.db.execute_query(
                query,
                {
                    "min_overrides": self.min_overrides,
                    "max_parent_call_ratio": self.max_parent_call_ratio,
                },
            )
        except Exception as e:
            self.logger.error(f"Error executing Refused Bequest detection query: {e}")
            return []

        findings = []
        for row in results:
            parent_class = row.get("parent_class", "")

            # Skip if parent is an abstract base class
            if self._is_abstract_parent(parent_class):
                continue

            finding = self._create_finding(row)
            findings.append(finding)

            # Flag entity in graph for cross-detector collaboration
            child_name = row.get("child_name", "")
            if self.enricher and child_name:
                try:
                    self.enricher.flag_entity(
                        entity_qualified_name=child_name,
                        detector="RefusedBequestDetector",
                        severity=finding.severity.value,
                        issues=["improper_inheritance"],
                        confidence=0.8,
                        metadata={
                            "parent_class": row.get("parent_name", ""),
                            "total_overrides": row.get("total_overrides", 0),
                        },
                    )
                except Exception:
                    pass  # Don't fail detection if enrichment fails

        self.logger.info(f"RefusedBequestDetector found {len(findings)} refused bequest violations")
        return findings

    def _is_abstract_parent(self, parent_name: str) -> bool:
        """Check if parent is an abstract class or interface.

        Args:
            parent_name: Name of the parent class

        Returns:
            True if parent is abstract, False otherwise
        """
        if not parent_name:
            return False

        parent_name_lower = parent_name.lower()
        for pattern in self.exclude_parent_patterns:
            if pattern.lower() in parent_name_lower:
                return True

        return False

    def _create_finding(self, row: dict) -> Finding:
        """Create a finding from query result.

        Args:
            row: Query result row

        Returns:
            Finding object
        """
        child_name = row.get("child_name", "unknown")
        child_class = row.get("child_class", child_name.split(".")[-1])
        parent_name = row.get("parent_name", "unknown")
        parent_class = row.get("parent_class", parent_name.split(".")[-1])
        total_overrides = row.get("total_overrides", 0)
        calls_parent = row.get("overrides_calling_parent", 0)
        ratio = row.get("parent_call_ratio", 0) or 0
        file_path = row.get("file_path", "unknown")
        line_start = row.get("line_start")
        line_end = row.get("line_end")

        # Determine severity based on ratio
        if ratio == 0:
            severity = Severity.HIGH
            severity_reason = "No overrides call parent"
        elif ratio < 0.2:
            severity = Severity.MEDIUM
            severity_reason = f"Only {ratio:.0%} of overrides call parent"
        else:
            severity = Severity.LOW
            severity_reason = f"{ratio:.0%} of overrides call parent"

        description = (
            f"Class '{child_class}' inherits from '{parent_class}' but "
            f"overrides {total_overrides} method(s) with only {calls_parent} "
            f"calling the parent ({ratio:.0%}). {severity_reason}. "
            f"This suggests inheritance may be misused."
        )

        parent_class_lower = parent_class.lower() if parent_class else "parent"
        recommendation = (
            "Consider refactoring to use composition instead of inheritance:\n"
            f"1. Replace 'class {child_class}({parent_class})' with "
            f"'class {child_class}:'\n"
            f"2. Add '{parent_class_lower}' as a member: "
            f"'self.{parent_class_lower} = {parent_class}()'\n"
            "3. Delegate only the methods you actually need\n\n"
            "Benefits: Looser coupling, clearer intent, easier testing"
        )

        # Estimate effort based on severity - changing inheritance to composition takes time
        if severity == Severity.HIGH:
            estimated_effort = "Medium (2-4 hours)"
        elif severity == Severity.MEDIUM:
            estimated_effort = "Medium (1-2 hours)"
        else:
            estimated_effort = "Small (30-60 minutes)"

        finding = Finding(
            id=f"refused_bequest_{child_name}",
            detector="RefusedBequestDetector",
            severity=severity,
            title=f"Refused bequest: {child_class} inherits {parent_class}",
            description=description,
            affected_nodes=[child_name],
            affected_files=[file_path] if file_path != "unknown" else [],
            line_start=line_start,
            line_end=line_end,
            suggested_fix=recommendation,
            estimated_effort=estimated_effort,
            graph_context={
                "parent_class": parent_name,
                "total_overrides": total_overrides,
                "overrides_calling_parent": calls_parent,
                "parent_call_ratio": round(ratio, 2),
            },
        )

        # Add collaboration metadata
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="RefusedBequestDetector",
            confidence=0.8,
            evidence=["improper_inheritance", "missing_super_calls"],
            tags=["refused_bequest", "design", "inheritance", "composition"],
        ))

        return finding

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity from finding's parent call ratio.

        Args:
            finding: Finding to assess

        Returns:
            Severity level based on parent call ratio
        """
        return finding.severity

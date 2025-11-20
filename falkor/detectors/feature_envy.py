"""
Feature Envy Detector.

Detects methods that use other classes more than their own class,
indicating the method might belong in the other class.

This is a code smell that traditional linters cannot detect because it requires
understanding cross-class relationships via the knowledge graph.

Addresses: FAL-110
"""

from typing import List, Dict, Any, Optional
from falkor.detectors.base import CodeSmellDetector
from falkor.models import Finding, Severity
from falkor.graph.client import Neo4jClient
from falkor.logging_config import get_logger


class FeatureEnvyDetector(CodeSmellDetector):
    """Detect methods that use other classes more than their own."""

    def __init__(self, neo4j_client: Neo4jClient, detector_config: Optional[Dict[str, Any]] = None):
        super().__init__(neo4j_client)
        config = detector_config or {}
        self.threshold_ratio = config.get("threshold_ratio", 2.0)
        self.min_external_uses = config.get("min_external_uses", 3)
        self.logger = get_logger(__name__)

    def detect(self) -> List[Finding]:
        """
        Detect methods with feature envy using graph analysis.

        Returns:
            List of Finding objects for methods that use external classes
            more than their own class.
        """
        query = """
        // Find methods and count internal vs external usage
        MATCH (c:Class)-[:CONTAINS]->(m:Function)
        WHERE m.is_method = true

        // Count internal uses (same class)
        OPTIONAL MATCH (m)-[r_internal:USES|CALLS]->()-[:CONTAINS*0..1]-(c)
        WITH m, c, count(DISTINCT r_internal) as internal_uses

        // Count external uses (other classes)
        OPTIONAL MATCH (m)-[r_external:USES|CALLS]->(target)
        WHERE NOT (target)-[:CONTAINS*0..1]-(c)
          AND NOT target:File
        WITH m, c, internal_uses, count(DISTINCT r_external) as external_uses

        // Filter based on thresholds
        WHERE external_uses >= $min_external_uses
          AND (internal_uses = 0 OR external_uses > internal_uses * $threshold_ratio)

        RETURN m.qualifiedName as method,
               m.name as method_name,
               c.qualifiedName as owner_class,
               m.filePath as file_path,
               m.lineStart as line_start,
               m.lineEnd as line_end,
               internal_uses,
               external_uses
        ORDER BY external_uses DESC
        LIMIT 100
        """

        try:
            results = self.db.execute_query(
                query,
                {
                    "threshold_ratio": self.threshold_ratio,
                    "min_external_uses": self.min_external_uses,
                },
            )
        except Exception as e:
            self.logger.error(f"Error executing Feature Envy detection query: {e}")
            return []

        findings = []
        for result in results:
            ratio = (
                result["external_uses"] / result["internal_uses"]
                if result["internal_uses"] > 0
                else float("inf")
            )

            # Determine severity based on ratio
            if ratio > 5.0 or result["internal_uses"] == 0:
                severity = Severity.HIGH
            elif ratio > 3.0:
                severity = Severity.MEDIUM
            else:
                severity = Severity.LOW

            # Create suggested fix
            if result["internal_uses"] == 0:
                suggestion = (
                    f"Method '{result['method_name']}' uses external classes "
                    f"{result['external_uses']} times but never uses its own class. "
                    f"Consider moving this method to the class it uses most, "
                    f"or making it a standalone utility function."
                )
            else:
                suggestion = (
                    f"Method '{result['method_name']}' uses external classes "
                    f"{result['external_uses']} times vs its own class "
                    f"{result['internal_uses']} times (ratio: {ratio:.1f}x). "
                    f"Consider moving to the most-used external class or refactoring "
                    f"to reduce external dependencies."
                )

            finding = Finding(
                id=f"feature_envy_{result['method']}",
                detector=self.__class__.__name__,
                severity=severity,
                title=f"Feature Envy: {result['method_name']}",
                description=(
                    f"Method '{result['method_name']}' in class '{result['owner_class']}' "
                    f"shows feature envy by using external classes {result['external_uses']} times "
                    f"compared to {result['internal_uses']} internal uses."
                ),
                affected_nodes=[result["method"], result["owner_class"]],
                affected_files=[result["file_path"]],
                line_start=result.get("line_start"),
                line_end=result.get("line_end"),
                suggested_fix=suggestion,
                graph_context={
                    "internal_uses": result["internal_uses"],
                    "external_uses": result["external_uses"],
                    "ratio": ratio if ratio != float("inf") else None,
                    "owner_class": result["owner_class"],
                },
            )
            findings.append(finding)

        self.logger.info(
            f"FeatureEnvyDetector found {len(findings)} methods with feature envy"
        )
        return findings

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity (already set during detection)."""
        return finding.severity

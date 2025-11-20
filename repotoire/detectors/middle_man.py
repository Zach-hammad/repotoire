"""
Middle Man Detector.

Detects classes that mostly delegate to other classes without adding value,
indicating unnecessary indirection.

Traditional linters cannot detect this pattern as it requires analyzing
method call patterns across classes.

Addresses: FAL-112
"""

from typing import List, Dict, Any, Optional
from repotoire.detectors.base import CodeSmellDetector
from repotoire.models import Finding, Severity
from repotoire.graph.client import Neo4jClient
from repotoire.logging_config import get_logger


class MiddleManDetector(CodeSmellDetector):
    """Detect classes that mostly delegate to other classes."""

    def __init__(self, neo4j_client: Neo4jClient, detector_config: Optional[Dict[str, Any]] = None):
        super().__init__(neo4j_client)
        config = detector_config or {}
        self.min_delegation_methods = config.get("min_delegation_methods", 3)
        self.delegation_threshold = config.get("delegation_threshold", 0.7)
        self.max_complexity = config.get("max_complexity", 2)
        self.logger = get_logger(__name__)

    def detect(self) -> List[Finding]:
        """
        Detect middle man classes using graph analysis.

        Returns:
            List of Finding objects for classes that mostly delegate.
        """
        query = """
        // Find classes where most methods delegate to one other class
        MATCH (c:Class)-[:CONTAINS]->(m:Function)
        WHERE m.is_method = true
          AND (m.complexity IS NULL OR m.complexity <= $max_complexity)

        // Find delegation patterns
        MATCH (m)-[:CALLS]->(delegated:Function)
        MATCH (delegated)<-[:CONTAINS]-(target:Class)
        WHERE c <> target

        WITH c, target,
             count(DISTINCT m) as delegation_count,
             size([(c)-[:CONTAINS]->(all_m:Function) WHERE all_m.is_method = true | all_m]) as total_methods

        // Filter based on thresholds
        WHERE delegation_count >= $min_delegation_methods
          AND total_methods > 0
          AND toFloat(delegation_count) / total_methods >= $delegation_threshold

        RETURN c.qualifiedName as middle_man,
               c.name as class_name,
               c.filePath as file_path,
               c.lineStart as line_start,
               c.lineEnd as line_end,
               target.qualifiedName as delegates_to,
               target.name as target_name,
               delegation_count,
               total_methods,
               toFloat(delegation_count * 100) / total_methods as delegation_percentage
        ORDER BY delegation_percentage DESC
        LIMIT 50
        """

        try:
            results = self.db.execute_query(
                query,
                {
                    "min_delegation_methods": self.min_delegation_methods,
                    "delegation_threshold": self.delegation_threshold,
                    "max_complexity": self.max_complexity,
                },
            )
        except Exception as e:
            self.logger.error(f"Error executing Middle Man detection query: {e}")
            return []

        findings = []
        for result in results:
            delegation_pct = result["delegation_percentage"]

            # Determine severity based on delegation percentage
            if delegation_pct >= 90:
                severity = Severity.HIGH
            elif delegation_pct >= 70:
                severity = Severity.MEDIUM
            else:
                severity = Severity.LOW

            # Create contextual suggested fix
            if delegation_pct >= 90:
                suggestion = (
                    f"Class '{result['class_name']}' delegates {delegation_pct:.0f}% of methods "
                    f"({result['delegation_count']}/{result['total_methods']}) to '{result['target_name']}'. "
                    f"Consider:\n"
                    f"  1. Remove the middle man and use '{result['target_name']}' directly\n"
                    f"  2. If this is a facade, add value by combining operations\n"
                    f"  3. Document the architectural reason if delegation is intentional"
                )
            else:
                suggestion = (
                    f"Class '{result['class_name']}' delegates {delegation_pct:.0f}% of methods "
                    f"to '{result['target_name']}'. Consider whether this indirection adds value."
                )

            finding = Finding(
                id=f"middle_man_{result['middle_man']}",
                detector=self.__class__.__name__,
                severity=severity,
                title=f"Middle Man: {result['class_name']}",
                description=(
                    f"Class '{result['class_name']}' acts as a middle man, delegating "
                    f"{result['delegation_count']} out of {result['total_methods']} methods "
                    f"({delegation_pct:.0f}%) to '{result['target_name']}' without adding significant value.\n\n"
                    f"This pattern adds unnecessary indirection and increases maintenance burden. "
                    f"Simple delegation methods with low complexity suggest the class may not be needed."
                ),
                affected_nodes=[result["middle_man"]],
                affected_files=[result["file_path"]],
                line_start=result.get("line_start"),
                line_end=result.get("line_end"),
                suggested_fix=suggestion,
                metadata={
                    "delegation_count": result["delegation_count"],
                    "total_methods": result["total_methods"],
                    "delegation_percentage": delegation_pct,
                    "delegates_to": result["delegates_to"],
                    "target_name": result["target_name"],
                },
            )
            findings.append(finding)

        self.logger.info(
            f"MiddleManDetector found {len(findings)} classes acting as middle men"
        )
        return findings

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity (already set during detection)."""
        return finding.severity

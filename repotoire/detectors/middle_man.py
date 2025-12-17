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
from repotoire.models import CollaborationMetadata, Finding, Severity
from repotoire.graph.enricher import GraphEnricher
from repotoire.graph.client import Neo4jClient
from repotoire.logging_config import get_logger


class MiddleManDetector(CodeSmellDetector):
    """Detect classes that mostly delegate to other classes."""

    def __init__(self, neo4j_client: Neo4jClient, detector_config: Optional[Dict[str, Any]] = None, enricher: Optional[GraphEnricher] = None):
        super().__init__(neo4j_client)
        self.enricher = enricher
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

            # Estimate effort - removing a middle man is usually straightforward
            if severity == Severity.HIGH:
                estimated_effort = "Medium (1-2 hours)"
            elif severity == Severity.MEDIUM:
                estimated_effort = "Small (30-60 minutes)"
            else:
                estimated_effort = "Small (15-30 minutes)"

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
                estimated_effort=estimated_effort,
                metadata={k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v for k, v in {
                    "delegation_count": result["delegation_count"],
                    "total_methods": result["total_methods"],
                    "delegation_percentage": delegation_pct,
                    "delegates_to": result["delegates_to"],
                    "target_name": result["target_name"],
                }.items()},
            )
            # Add collaboration metadata (REPO-150 Phase 1)
            finding.add_collaboration_metadata(CollaborationMetadata(
                detector="MiddleManDetector",
                confidence=0.8,
                evidence=['delegation_only'],
                tags=['middle_man', 'code_quality', 'maintenance']
            ))

            # Flag entity in graph for cross-detector collaboration (REPO-151 Phase 2)
            if self.enricher and finding.affected_nodes:
                for entity_qname in finding.affected_nodes:
                    try:
                        self.enricher.flag_entity(
                            entity_qualified_name=entity_qname,
                            detector="MiddleManDetector",
                            severity=finding.severity.value,
                            issues=['delegation_only'],
                            confidence=0.8,
                            metadata={k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v) for k, v in (finding.graph_context or {}).items()}
                        )
                    except Exception:
                        pass


            findings.append(finding)

        self.logger.info(
            f"MiddleManDetector found {len(findings)} classes acting as middle men"
        )
        return findings

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity (already set during detection)."""
        return finding.severity

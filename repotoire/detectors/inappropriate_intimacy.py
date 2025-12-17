"""
Inappropriate Intimacy Detector.

Detects pairs of classes that are too tightly coupled, accessing each other's
internals excessively. This violates encapsulation and makes changes difficult.

Traditional linters cannot detect this pattern as it requires tracking
bidirectional relationships between classes.

Addresses: FAL-113
"""

from typing import List, Dict, Any, Optional
from repotoire.detectors.base import CodeSmellDetector
from repotoire.models import CollaborationMetadata, Finding, Severity
from repotoire.graph.enricher import GraphEnricher
from repotoire.graph.client import Neo4jClient
from repotoire.logging_config import get_logger


class InappropriateIntimacyDetector(CodeSmellDetector):
    """Detect classes that are too tightly coupled."""

    def __init__(self, neo4j_client: Neo4jClient, detector_config: Optional[Dict[str, Any]] = None, enricher: Optional[GraphEnricher] = None):
        super().__init__(neo4j_client)
        self.enricher = enricher
        config = detector_config or {}
        thresholds = config.get("thresholds", {})
        self.threshold_high = thresholds.get("high", 20)
        self.threshold_medium = thresholds.get("medium", 10)
        self.min_mutual_access = config.get("min_mutual_access", 5)
        self.logger = get_logger(__name__)
        # FalkorDB uses id() while Neo4j uses elementId()
        self.is_falkordb = type(neo4j_client).__name__ == "FalkorDBClient"
        self.id_func = "id" if self.is_falkordb else "elementId"

    def detect(self) -> List[Finding]:
        """
        Detect inappropriately intimate class pairs using graph analysis.

        Returns:
            List of Finding objects for mutually coupled class pairs.
        """
        query = f"""
        // Find pairs of classes with excessive mutual access
        MATCH (c1:Class)-[:CONTAINS]->(m1:Function)
        MATCH (m1)-[r:USES|CALLS]->()-[:CONTAINS*0..1]-(c2:Class)
        WHERE c1 <> c2
        WITH c1, c2, count(r) as c1_to_c2

        // Get the reverse direction
        MATCH (c2)-[:CONTAINS]->(m2:Function)
        MATCH (m2)-[r:USES|CALLS]->()-[:CONTAINS*0..1]-(c1)
        WITH c1, c2, c1_to_c2, count(r) as c2_to_c1

        // Filter for mutual high coupling
        WHERE c1_to_c2 >= $min_mutual_access
          AND c2_to_c1 >= $min_mutual_access
          AND {self.id_func}(c1) < {self.id_func}(c2)  // Avoid duplicates

        RETURN c1.qualifiedName as class1,
               c1.name as class1_name,
               c2.qualifiedName as class2,
               c2.name as class2_name,
               c1.filePath as file1,
               c2.filePath as file2,
               c1_to_c2,
               c2_to_c1,
               (c1_to_c2 + c2_to_c1) as total_coupling
        ORDER BY total_coupling DESC
        LIMIT 50
        """

        try:
            results = self.db.execute_query(
                query,
                {"min_mutual_access": self.min_mutual_access},
            )
        except Exception as e:
            self.logger.error(f"Error executing Inappropriate Intimacy detection query: {e}")
            return []

        findings = []
        for result in results:
            total_coupling = result["total_coupling"]

            # Determine severity based on total coupling
            if total_coupling >= self.threshold_high:
                severity = Severity.HIGH
            elif total_coupling >= self.threshold_medium:
                severity = Severity.MEDIUM
            else:
                severity = Severity.LOW

            # Create context-aware suggested fix
            if severity == Severity.HIGH:
                suggestion = (
                    f"Classes '{result['class1_name']}' and '{result['class2_name']}' "
                    f"have excessive mutual access ({total_coupling} total accesses: "
                    f"{result['c1_to_c2']} and {result['c2_to_c1']} respectively).\n\n"
                    f"This tight coupling violates encapsulation. Consider:\n"
                    f"  1. Merge the classes if they truly belong together\n"
                    f"  2. Extract common data into a shared class\n"
                    f"  3. Apply the Law of Demeter - don't access internals directly\n"
                    f"  4. Introduce interfaces or abstract base classes to reduce coupling"
                )
            else:
                suggestion = (
                    f"Classes '{result['class1_name']}' and '{result['class2_name']}' "
                    f"show inappropriate intimacy ({total_coupling} mutual accesses). "
                    f"Consider refactoring to reduce coupling."
                )

            # Determine if classes are in same file
            same_file = result["file1"] == result["file2"]
            same_file_note = " (same file)" if same_file else " (different files)"

            # Estimate effort based on coupling severity
            if severity == Severity.HIGH:
                estimated_effort = "Large (4-8 hours)"
            elif severity == Severity.MEDIUM:
                estimated_effort = "Medium (2-4 hours)"
            else:
                estimated_effort = "Medium (1-2 hours)"

            finding = Finding(
                id=f"inappropriate_intimacy_{result['class1']}_{result['class2']}",
                detector=self.__class__.__name__,
                severity=severity,
                title=f"Inappropriate Intimacy: {result['class1_name']} ↔ {result['class2_name']}",
                description=(
                    f"Classes '{result['class1_name']}' and '{result['class2_name']}' are too tightly coupled{same_file_note}:\n"
                    f"  • {result['class1_name']} → {result['class2_name']}: {result['c1_to_c2']} accesses\n"
                    f"  • {result['class2_name']} → {result['class1_name']}: {result['c2_to_c1']} accesses\n"
                    f"  • Total coupling: {total_coupling} mutual accesses\n\n"
                    f"This bidirectional coupling makes both classes difficult to change independently "
                    f"and violates encapsulation principles."
                ),
                affected_nodes=[result["class1"], result["class2"]],
                affected_files=[result["file1"], result["file2"]],
                suggested_fix=suggestion,
                estimated_effort=estimated_effort,
                graph_context={
                    "class1": result["class1"],
                    "class2": result["class2"],
                    "class1_to_class2": result["c1_to_c2"],
                    "class2_to_class1": result["c2_to_c1"],
                    "total_coupling": total_coupling,
                    "same_file": same_file,
                },
            )
            # Add collaboration metadata (REPO-150 Phase 1)
            finding.add_collaboration_metadata(CollaborationMetadata(
                detector="InappropriateIntimacyDetector",
                confidence=0.85,
                evidence=['tight_coupling'],
                tags=['inappropriate_intimacy', 'coupling', 'architecture']
            ))

            # Flag entity in graph for cross-detector collaboration (REPO-151 Phase 2)
            if self.enricher and finding.affected_nodes:
                for entity_qname in finding.affected_nodes:
                    try:
                        self.enricher.flag_entity(
                            entity_qualified_name=entity_qname,
                            detector="InappropriateIntimacyDetector",
                            severity=finding.severity.value,
                            issues=['tight_coupling'],
                            confidence=0.85,
                            metadata={k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v) for k, v in (finding.graph_context or {}).items()}
                        )
                    except Exception:
                        pass


            findings.append(finding)

        self.logger.info(
            f"InappropriateIntimacyDetector found {len(findings)} tightly coupled class pairs"
        )
        return findings

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity (already set during detection)."""
        return finding.severity

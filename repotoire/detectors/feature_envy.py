"""
Feature Envy Detector.

Detects methods that use other classes more than their own class,
indicating the method might belong in the other class.

This is a code smell that traditional linters cannot detect because it requires
understanding cross-class relationships via the knowledge graph.

Addresses: FAL-110
"""

from typing import List, Dict, Any, Optional
from repotoire.detectors.base import CodeSmellDetector
from repotoire.models import CollaborationMetadata, Finding, Severity
from repotoire.graph.enricher import GraphEnricher
from repotoire.graph.client import Neo4jClient
from repotoire.logging_config import get_logger


class FeatureEnvyDetector(CodeSmellDetector):
    """Detect methods that use other classes more than their own."""

    def __init__(self, neo4j_client: Neo4jClient, detector_config: Optional[Dict[str, Any]] = None, enricher: Optional[GraphEnricher] = None):
        super().__init__(neo4j_client)
        self.enricher = enricher
        config = detector_config or {}

        # Updated thresholds for v1.0 (REPO-116 tuning)
        # Reduced false positives by requiring higher thresholds
        self.threshold_ratio = config.get("threshold_ratio", 3.0)  # Was 2.0
        self.min_external_uses = config.get("min_external_uses", 15)  # Was 3

        # Severity-specific thresholds
        self.critical_ratio = config.get("critical_ratio", 10.0)
        self.critical_min_uses = config.get("critical_min_uses", 30)
        self.high_ratio = config.get("high_ratio", 5.0)
        self.high_min_uses = config.get("high_min_uses", 20)
        self.medium_ratio = config.get("medium_ratio", 3.0)
        self.medium_min_uses = config.get("medium_min_uses", 10)

        self.logger = get_logger(__name__)

    @property
    def needs_previous_findings(self) -> bool:
        """FeatureEnvyDetector needs GodClassDetector findings for cross-correlation.

        When feature envy is detected in a god class, severity is downgraded
        because it's a symptom of the god class, not a root cause.
        """
        return True

    def detect(self, previous_findings: Optional[List[Finding]] = None) -> List[Finding]:
        """
        Detect methods with feature envy using graph analysis.

        Args:
            previous_findings: Optional list of findings from previous detectors
                             (used for cross-detector collaboration)

        Returns:
            List of Finding objects for methods that use external classes
            more than their own class.
        """
        # Build set of god classes from previous findings for quick lookup
        god_classes = set()
        if previous_findings:
            for prev_finding in previous_findings:
                if prev_finding.has_tag("god_class"):
                    # Extract class name from affected nodes
                    for node in prev_finding.affected_nodes:
                        god_classes.add(node)
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

            # Determine severity based on ratio AND absolute external uses
            # This reduces false positives from methods with few external dependencies
            external = result["external_uses"]

            if ratio >= self.critical_ratio and external >= self.critical_min_uses:
                severity = Severity.CRITICAL
            elif ratio >= self.high_ratio and external >= self.high_min_uses:
                severity = Severity.HIGH
            elif ratio >= self.medium_ratio and external >= self.medium_min_uses:
                severity = Severity.MEDIUM
            else:
                severity = Severity.LOW

            # Check if owner class is a god class (cross-detector collaboration)
            owner_class = result["owner_class"]
            is_god_class_symptom = owner_class in god_classes

            # Downgrade severity if this is a symptom of a god class (not root cause)
            original_severity = severity
            if is_god_class_symptom:
                # Downgrade one level (symptoms should be addressed by fixing root cause)
                if severity == Severity.CRITICAL:
                    severity = Severity.HIGH
                elif severity == Severity.HIGH:
                    severity = Severity.MEDIUM
                elif severity == Severity.MEDIUM:
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

            # Add note if this is a god class symptom
            if is_god_class_symptom:
                suggestion += (
                    f"\n\nNOTE: Owner class '{owner_class}' is a god class. "
                    f"This feature envy is likely a symptom - refactor the god class first."
                )

            # Estimate effort based on severity and external dependencies
            if severity == Severity.CRITICAL:
                estimated_effort = "Large (2-4 hours)"
            elif severity == Severity.HIGH:
                estimated_effort = "Medium (1-2 hours)"
            elif severity == Severity.MEDIUM:
                estimated_effort = "Small (30-60 minutes)"
            else:
                estimated_effort = "Small (15-30 minutes)"

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
                estimated_effort=estimated_effort,
                graph_context={
                    "internal_uses": result["internal_uses"],
                    "external_uses": result["external_uses"],
                    "ratio": ratio if ratio != float("inf") else None,
                    "owner_class": result["owner_class"],
                    "is_god_class_symptom": is_god_class_symptom,
                },
            )

            # Add collaboration metadata
            evidence = ["high_external_usage"]
            if result["internal_uses"] == 0:
                evidence.append("no_internal_usage")
            if ratio >= self.high_ratio:
                evidence.append("very_high_ratio")

            confidence = min(0.7 + (ratio / 10), 0.95)
            tags = ["feature_envy"]
            if is_god_class_symptom:
                tags.append("symptom")  # Mark as symptom, not root cause
            else:
                tags.append("standalone_issue")

            finding.add_collaboration_metadata(CollaborationMetadata(
                detector="FeatureEnvyDetector",
                confidence=confidence,
                evidence=evidence,
                tags=tags
            ))
            # Add collaboration metadata (REPO-150 Phase 1)
            finding.add_collaboration_metadata(CollaborationMetadata(
                detector="FeatureEnvyDetector",
                confidence=0.85,
                evidence=['feature_envy', 'external_field_access'],
                tags=['feature_envy', 'coupling', 'code_quality']
            ))

            # Flag entity in graph for cross-detector collaboration (REPO-151 Phase 2)
            if self.enricher and finding.affected_nodes:
                for entity_qname in finding.affected_nodes:
                    try:
                        self.enricher.flag_entity(
                            entity_qualified_name=entity_qname,
                            detector="FeatureEnvyDetector",
                            severity=finding.severity.value,
                            issues=['feature_envy', 'external_field_access'],
                            confidence=0.85,
                            metadata={k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v) for k, v in (finding.graph_context or {}).items()}
                        )
                    except Exception:
                        pass



            findings.append(finding)

        self.logger.info(
            f"FeatureEnvyDetector found {len(findings)} methods with feature envy"
        )
        return findings

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity (already set during detection)."""
        return finding.severity

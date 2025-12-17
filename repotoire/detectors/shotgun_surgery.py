"""
Shotgun Surgery Detector.

Detects classes that are used by many other classes, indicating that changes
to these classes will require updates across the codebase (shotgun surgery).

This represents high fan-in coupling that traditional linters cannot detect.

Addresses: FAL-111
"""

from typing import List, Dict, Any, Optional
from repotoire.detectors.base import CodeSmellDetector
from repotoire.models import CollaborationMetadata, Finding, Severity
from repotoire.graph.enricher import GraphEnricher
from repotoire.graph.client import Neo4jClient
from repotoire.logging_config import get_logger


class ShotgunSurgeryDetector(CodeSmellDetector):
    """Detect classes with too many dependents (high fan-in)."""

    def __init__(self, neo4j_client: Neo4jClient, detector_config: Optional[Dict[str, Any]] = None, enricher: Optional[GraphEnricher] = None):
        super().__init__(neo4j_client)
        self.enricher = enricher
        config = detector_config or {}
        thresholds = config.get("thresholds", {})
        self.threshold_critical = thresholds.get("critical", 25)
        self.threshold_high = thresholds.get("high", 15)
        self.threshold_medium = thresholds.get("medium", 8)
        self.logger = get_logger(__name__)

    def detect(self) -> List[Finding]:
        """
        Detect classes with high fan-in using graph analysis.

        Returns:
            List of Finding objects for classes used by many others.
        """
        query = """
        // Find classes with many incoming dependencies
        MATCH (c:Class)<-[:USES|CALLS]-(caller:Function)
        WITH c,
             count(DISTINCT caller) as caller_count,
             collect(DISTINCT caller.filePath) as affected_files
        WHERE caller_count >= $min_threshold

        RETURN c.qualifiedName as class_name,
               c.name as short_name,
               c.filePath as file_path,
               c.lineStart as line_start,
               c.lineEnd as line_end,
               caller_count,
               size(affected_files) as files_affected,
               affected_files[0..5] as sample_files
        ORDER BY caller_count DESC
        LIMIT 50
        """

        try:
            results = self.db.execute_query(
                query,
                {"min_threshold": self.threshold_medium},
            )
        except Exception as e:
            self.logger.error(f"Error executing Shotgun Surgery detection query: {e}")
            return []

        findings = []
        for result in results:
            caller_count = result["caller_count"]
            files_affected = result["files_affected"]

            # Determine severity based on caller count
            if caller_count >= self.threshold_critical:
                severity = Severity.CRITICAL
            elif caller_count >= self.threshold_high:
                severity = Severity.HIGH
            else:
                severity = Severity.MEDIUM

            # Format sample files list
            sample_files_str = "\n  - ".join(result["sample_files"])
            if files_affected > 5:
                sample_files_str += f"\n  ... and {files_affected - 5} more files"

            # Create suggested fix based on severity
            if severity == Severity.CRITICAL:
                suggestion = (
                    f"URGENT: Class '{result['short_name']}' is used by {caller_count} "
                    f"functions across {files_affected} files. Any change will require "
                    f"widespread modifications. Consider:\n"
                    f"  1. Create a facade or wrapper to isolate changes\n"
                    f"  2. Split responsibilities into multiple focused classes\n"
                    f"  3. Use dependency injection to reduce direct coupling\n"
                    f"  4. Introduce interfaces to decouple implementations"
                )
            else:
                suggestion = (
                    f"Class '{result['short_name']}' is used by {caller_count} functions "
                    f"across {files_affected} files. Consider:\n"
                    f"  - Creating a facade to limit surface area\n"
                    f"  - Splitting into smaller, more focused classes\n"
                    f"  - Using the Strategy or Bridge pattern to reduce coupling"
                )

            # Estimate effort based on number of affected files
            if severity == Severity.CRITICAL:
                estimated_effort = "Large (1-2 days)"
            elif severity == Severity.HIGH:
                estimated_effort = "Large (4-8 hours)"
            else:
                estimated_effort = "Medium (2-4 hours)"

            finding = Finding(
                id=f"shotgun_surgery_{result['class_name']}",
                detector=self.__class__.__name__,
                severity=severity,
                title=f"Shotgun Surgery Risk: {result['short_name']}",
                description=(
                    f"Class '{result['short_name']}' is used by {caller_count} different functions "
                    f"across {files_affected} files. Changes to this class will require updates "
                    f"in many places across the codebase.\n\n"
                    f"Affected files (sample):\n  - {sample_files_str}"
                ),
                affected_nodes=[result["class_name"]],
                affected_files=[result["file_path"]],
                line_start=result.get("line_start"),
                line_end=result.get("line_end"),
                suggested_fix=suggestion,
                estimated_effort=estimated_effort,
                graph_context={
                    "caller_count": caller_count,
                    "files_affected": files_affected,
                    "sample_files": result["sample_files"],
                },
            )
            # Add collaboration metadata (REPO-150 Phase 1)
            finding.add_collaboration_metadata(CollaborationMetadata(
                detector="ShotgunSurgeryDetector",
                confidence=0.85,
                evidence=['high_fan_in'],
                tags=['shotgun_surgery', 'coupling', 'maintenance']
            ))

            # Flag entity in graph for cross-detector collaboration (REPO-151 Phase 2)
            if self.enricher and finding.affected_nodes:
                for entity_qname in finding.affected_nodes:
                    try:
                        self.enricher.flag_entity(
                            entity_qualified_name=entity_qname,
                            detector="ShotgunSurgeryDetector",
                            severity=finding.severity.value,
                            issues=['high_fan_in'],
                            confidence=0.85,
                            metadata={k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v) for k, v in (finding.graph_context or {}).items()}
                        )
                    except Exception:
                        pass


            findings.append(finding)

        self.logger.info(
            f"ShotgunSurgeryDetector found {len(findings)} classes with high fan-in"
        )
        return findings

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity (already set during detection)."""
        return finding.severity

"""Degree centrality detector (REPO-171, REPO-204).

Uses pure Cypher to detect:
- God Classes: High in-degree (many dependents) + high complexity
- Feature Envy: High out-degree (reaching into many modules)
- Coupling hotspots: Both high in and out degree

No GDS or plugins required - works with both Neo4j and FalkorDB.
"""

from typing import List, Optional
from repotoire.detectors.base import CodeSmellDetector
from repotoire.detectors.graph_algorithms import GraphAlgorithms
from repotoire.graph.client import Neo4jClient
from repotoire.models import CollaborationMetadata, Finding, Severity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class DegreeCentralityDetector(CodeSmellDetector):
    """Detects coupling issues using degree centrality.

    Degree centrality measures direct connections:
    - In-degree: How many files import this file
    - Out-degree: How many files this file imports

    Detects:
    - God Classes: High in-degree + complexity (many depend on complex code)
    - Feature Envy: High out-degree (reaching into too many modules)
    - Coupling Hotspots: Both high in and out degree
    """

    # Complexity threshold for God Class detection
    HIGH_COMPLEXITY_THRESHOLD = 15

    # Percentile for "high" degree
    HIGH_PERCENTILE = 95.0

    # Minimum degree thresholds
    MIN_INDEGREE = 5
    MIN_OUTDEGREE = 10

    def __init__(self, neo4j_client: Neo4jClient):
        """Initialize detector with Neo4j client.

        Args:
            neo4j_client: Neo4j database client
        """
        super().__init__(neo4j_client)

    def detect(self) -> List[Finding]:
        """Detect coupling issues using degree centrality.

        Uses pure Cypher queries - works with both Neo4j and FalkorDB.
        No GDS plugin required.

        Returns:
            List of findings
        """
        findings = []

        graph_algo = GraphAlgorithms(self.db)

        try:
            # Calculate degree centrality using pure Cypher (no GDS needed)
            result = graph_algo.calculate_degree_centrality()
            if not result:
                logger.warning("Failed to calculate degree centrality")
                return findings

            # Get statistics for context
            stats = graph_algo.get_degree_statistics()
            logger.info(
                f"Degree stats: avg_in={stats.get('avg_in_degree', 0):.1f}, "
                f"max_in={stats.get('max_in_degree', 0)}, "
                f"avg_out={stats.get('avg_out_degree', 0):.1f}, "
                f"max_out={stats.get('max_out_degree', 0)}"
            )

            # Detect God Classes (high in-degree + complexity)
            high_indegree = graph_algo.get_high_indegree_nodes(
                percentile=self.HIGH_PERCENTILE,
                min_degree=self.MIN_INDEGREE
            )
            for node in high_indegree:
                complexity = node.get("complexity") or 0
                if complexity >= self.HIGH_COMPLEXITY_THRESHOLD:
                    finding = self._create_god_class_finding(node, stats)
                    if finding:
                        findings.append(finding)

            # Detect Feature Envy (high out-degree)
            high_outdegree = graph_algo.get_high_outdegree_nodes(
                percentile=self.HIGH_PERCENTILE,
                min_degree=self.MIN_OUTDEGREE
            )
            for node in high_outdegree:
                finding = self._create_feature_envy_finding(node, stats)
                if finding:
                    findings.append(finding)

            # Detect Coupling Hotspots (both high)
            hotspots = self._find_coupling_hotspots(high_indegree, high_outdegree)
            for node in hotspots:
                finding = self._create_coupling_hotspot_finding(node, stats)
                if finding:
                    findings.append(finding)

            return findings

        except Exception as e:
            logger.error(f"Error in degree centrality detection: {e}", exc_info=True)
            return findings

    def _find_coupling_hotspots(
        self,
        high_indegree: List[dict],
        high_outdegree: List[dict]
    ) -> List[dict]:
        """Find nodes that appear in both high in-degree and out-degree lists.

        These are coupling hotspots - both heavily depended on and heavily
        dependent on others.

        Args:
            high_indegree: Nodes with high in-degree
            high_outdegree: Nodes with high out-degree

        Returns:
            List of hotspot nodes with combined metrics
        """
        in_names = {n["qualified_name"]: n for n in high_indegree}
        hotspots = []

        for out_node in high_outdegree:
            name = out_node["qualified_name"]
            if name in in_names:
                # Merge metrics from both
                in_node = in_names[name]
                hotspot = {
                    **out_node,
                    "in_degree": in_node.get("in_degree", 0),
                    "in_threshold": in_node.get("threshold", 0),
                }
                hotspots.append(hotspot)

        return hotspots

    def _create_god_class_finding(
        self,
        node: dict,
        stats: dict
    ) -> Optional[Finding]:
        """Create finding for God Class (high in-degree + complexity).

        A God Class is complex code that many other parts depend on.
        Changes here are risky and affect many dependents.
        """
        qualified_name = node.get("qualified_name", "unknown")
        file_path = node.get("file_path", "unknown")
        in_degree = node.get("in_degree", 0)
        out_degree = node.get("out_degree", 0)
        complexity = node.get("complexity", 0)
        loc = node.get("line_count", 0)
        threshold = node.get("threshold", 0)

        # Calculate severity based on degree and complexity
        max_in = stats.get("max_in_degree", 1) or 1
        percentile = (in_degree / max_in) * 100

        if complexity >= self.HIGH_COMPLEXITY_THRESHOLD * 2 or percentile >= 99:
            severity = Severity.CRITICAL
        elif complexity >= self.HIGH_COMPLEXITY_THRESHOLD * 1.5 or percentile >= 97:
            severity = Severity.HIGH
        else:
            severity = Severity.MEDIUM

        name = qualified_name.split(".")[-1]

        description = (
            f"File `{name}` is a potential **God Class**: high in-degree "
            f"({in_degree} dependents) combined with high complexity ({complexity}).\n\n"
            f"**What this means:**\n"
            f"- Many files depend on this code ({in_degree} importers)\n"
            f"- The code itself is complex (complexity: {complexity})\n"
            f"- Changes are high-risk with wide blast radius\n"
            f"- This is a maintainability bottleneck\n\n"
            f"**Metrics:**\n"
            f"- In-degree: {in_degree} (threshold: {threshold})\n"
            f"- Complexity: {complexity}\n"
            f"- Lines of code: {loc}\n"
            f"- Out-degree: {out_degree}"
        )

        suggested_fix = (
            "**For God Classes:**\n\n"
            "1. **Extract interfaces**: Define contracts to reduce coupling\n\n"
            "2. **Split responsibilities**: Break into focused modules using SRP\n\n"
            "3. **Use dependency injection**: Reduce direct imports\n\n"
            "4. **Add abstraction layers**: Shield dependents from changes\n\n"
            "5. **Prioritize test coverage**: High-risk code needs safety net"
        )

        # God classes require significant refactoring effort
        if severity == Severity.CRITICAL:
            estimated_effort = "Large (1-2 days)"
        elif severity == Severity.HIGH:
            estimated_effort = "Large (4-8 hours)"
        else:
            estimated_effort = "Medium (2-4 hours)"

        finding = Finding(
            id=f"god_class_{hash(qualified_name) % 100000}",
            detector="DegreeCentralityDetector",
            severity=severity,
            title=f"God Class: {name}",
            description=description,
            affected_nodes=[qualified_name],
            affected_files=[file_path],
            suggested_fix=suggested_fix,
            estimated_effort=estimated_effort,
            graph_context={
                "in_degree": in_degree,
                "out_degree": out_degree,
                "complexity": complexity,
                "loc": loc,
                "percentile": percentile,
                "threshold": threshold,
            }
        )

        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="DegreeCentralityDetector",
            confidence=0.85,
            evidence=["high_in_degree", "high_complexity"],
            tags=["god_class", "architecture", "coupling"]
        ))

        return finding

    def _create_feature_envy_finding(
        self,
        node: dict,
        stats: dict
    ) -> Optional[Finding]:
        """Create finding for Feature Envy (high out-degree).

        Feature Envy occurs when a module depends on too many other modules,
        reaching into others' responsibilities instead of handling its own.
        """
        qualified_name = node.get("qualified_name", "unknown")
        file_path = node.get("file_path", "unknown")
        out_degree = node.get("out_degree", 0)
        in_degree = node.get("in_degree", 0)
        complexity = node.get("complexity", 0)
        loc = node.get("line_count", 0)
        threshold = node.get("threshold", 0)

        # Calculate severity
        max_out = stats.get("max_out_degree", 1) or 1
        percentile = (out_degree / max_out) * 100

        if percentile >= 99:
            severity = Severity.HIGH
        elif percentile >= 97:
            severity = Severity.MEDIUM
        else:
            severity = Severity.LOW

        name = qualified_name.split(".")[-1]

        description = (
            f"File `{name}` shows **Feature Envy**: imports {out_degree} other files, "
            f"suggesting it reaches into too many modules.\n\n"
            f"**What this means:**\n"
            f"- This file depends on {out_degree} other files\n"
            f"- May be handling responsibilities that belong elsewhere\n"
            f"- Tight coupling makes changes cascade\n"
            f"- Could be a 'God Module' orchestrating everything\n\n"
            f"**Metrics:**\n"
            f"- Out-degree: {out_degree} (threshold: {threshold})\n"
            f"- In-degree: {in_degree}\n"
            f"- Complexity: {complexity}\n"
            f"- Lines of code: {loc}"
        )

        suggested_fix = (
            "**For Feature Envy:**\n\n"
            "1. **Move logic to data**: Put behavior where data lives\n\n"
            "2. **Extract classes**: Group related functionality\n\n"
            "3. **Use delegation**: Have other modules handle their own logic\n\n"
            "4. **Review module boundaries**: This may be misplaced code\n\n"
            "5. **Apply facade pattern**: If orchestration is needed, make it explicit"
        )

        # Feature envy requires moving or refactoring code
        if severity == Severity.HIGH:
            estimated_effort = "Medium (2-4 hours)"
        elif severity == Severity.MEDIUM:
            estimated_effort = "Medium (1-2 hours)"
        else:
            estimated_effort = "Small (30-60 minutes)"

        finding = Finding(
            id=f"feature_envy_{hash(qualified_name) % 100000}",
            detector="DegreeCentralityDetector",
            severity=severity,
            title=f"Feature Envy: {name}",
            description=description,
            affected_nodes=[qualified_name],
            affected_files=[file_path],
            suggested_fix=suggested_fix,
            estimated_effort=estimated_effort,
            graph_context={
                "out_degree": out_degree,
                "in_degree": in_degree,
                "complexity": complexity,
                "loc": loc,
                "percentile": percentile,
                "threshold": threshold,
            }
        )

        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="DegreeCentralityDetector",
            confidence=0.75,
            evidence=["high_out_degree"],
            tags=["feature_envy", "coupling", "refactoring"]
        ))

        return finding

    def _create_coupling_hotspot_finding(
        self,
        node: dict,
        stats: dict
    ) -> Optional[Finding]:
        """Create finding for coupling hotspot (both high in and out degree).

        These are the most problematic files - they both depend on many files
        AND are depended on by many files, creating a coupling bottleneck.
        """
        qualified_name = node.get("qualified_name", "unknown")
        file_path = node.get("file_path", "unknown")
        in_degree = node.get("in_degree", 0)
        out_degree = node.get("out_degree", 0)
        complexity = node.get("complexity", 0)
        loc = node.get("line_count", 0)

        # Coupling hotspots are always high severity
        if complexity >= self.HIGH_COMPLEXITY_THRESHOLD:
            severity = Severity.CRITICAL
        else:
            severity = Severity.HIGH

        name = qualified_name.split(".")[-1]

        total_coupling = in_degree + out_degree

        description = (
            f"File `{name}` is a **Coupling Hotspot**: high in-degree ({in_degree}) "
            f"AND high out-degree ({out_degree}).\n\n"
            f"**What this means:**\n"
            f"- Both heavily depended ON ({in_degree} importers)\n"
            f"- AND heavily dependent ON others ({out_degree} imports)\n"
            f"- Total coupling: {total_coupling} connections\n"
            f"- Changes here cascade in both directions\n"
            f"- This is a critical architectural risk\n\n"
            f"**Metrics:**\n"
            f"- In-degree: {in_degree}\n"
            f"- Out-degree: {out_degree}\n"
            f"- Total coupling: {total_coupling}\n"
            f"- Complexity: {complexity}\n"
            f"- Lines of code: {loc}"
        )

        suggested_fix = (
            "**For Coupling Hotspots (Critical):**\n\n"
            "1. **Architectural review**: This file is a design bottleneck\n\n"
            "2. **Split by responsibility**: Extract into focused modules\n\n"
            "3. **Introduce layers**: Create abstraction boundaries\n\n"
            "4. **Apply SOLID principles**:\n"
            "   - Single Responsibility (split concerns)\n"
            "   - Interface Segregation (smaller interfaces)\n"
            "   - Dependency Inversion (depend on abstractions)\n\n"
            "5. **Consider strangler pattern**: Gradually replace with better design"
        )

        # Coupling hotspots are critical architectural issues requiring significant effort
        estimated_effort = "Large (1-2 days)" if severity == Severity.CRITICAL else "Large (4-8 hours)"

        finding = Finding(
            id=f"coupling_hotspot_{hash(qualified_name) % 100000}",
            detector="DegreeCentralityDetector",
            severity=severity,
            title=f"Coupling Hotspot: {name}",
            description=description,
            affected_nodes=[qualified_name],
            affected_files=[file_path],
            suggested_fix=suggested_fix,
            estimated_effort=estimated_effort,
            graph_context={
                "in_degree": in_degree,
                "out_degree": out_degree,
                "total_coupling": total_coupling,
                "complexity": complexity,
                "loc": loc,
            }
        )

        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="DegreeCentralityDetector",
            confidence=0.9,
            evidence=["high_in_degree", "high_out_degree", "coupling_hotspot"],
            tags=["coupling", "architecture", "critical"]
        ))

        return finding

    def severity(self, finding: Finding) -> Severity:
        """Return severity (already set in detect)."""
        return finding.severity

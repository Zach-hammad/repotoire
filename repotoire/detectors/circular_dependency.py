"""Circular dependency detector using graph algorithms (REPO-170, REPO-200).

Uses Rust-based Strongly Connected Components (Tarjan's SCC) for O(V+E) cycle
detection - 10-100x faster than pairwise path queries. No GDS plugin required.

REPO-200: Updated to use Rust algorithms directly (no GDS dependency).
"""

import uuid
from typing import List, Set, Optional
from datetime import datetime

from repotoire.detectors.base import CodeSmellDetector
from repotoire.detectors.graph_algorithms import GraphAlgorithms
from repotoire.models import CollaborationMetadata, Finding, Severity
from repotoire.graph.enricher import GraphEnricher
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class CircularDependencyDetector(CodeSmellDetector):
    """Detects circular dependencies in import graph using Tarjan's algorithm.

    Uses GDS SCC (Strongly Connected Components) algorithm when available,
    which is O(V+E) - much faster than pairwise path queries O(V²).
    """

    def __init__(
        self,
        neo4j_client,
        detector_config: Optional[dict] = None,
        enricher: Optional[GraphEnricher] = None
    ):
        """Initialize circular dependency detector.

        Args:
            neo4j_client: Neo4j database client
            detector_config: Optional detector configuration
            enricher: Optional GraphEnricher for cross-detector collaboration
        """
        super().__init__(neo4j_client)
        self.enricher = enricher
        # FalkorDB uses id() while Neo4j uses elementId()
        self.is_falkordb = type(neo4j_client).__name__ == "FalkorDBClient"
        self.id_func = "id" if self.is_falkordb else "elementId"

    def detect(self) -> List[Finding]:
        """Find circular dependencies in the codebase.

        Uses Rust SCC algorithm (Tarjan's) for O(V+E) cycle detection.
        No GDS plugin required - runs entirely with Rust algorithms.

        Returns:
            List of findings, one per circular dependency cycle
        """
        graph_algo = GraphAlgorithms(self.db)

        # Use Rust-based SCC detection (no GDS required)
        logger.info("Using Rust SCC algorithm for circular dependency detection")
        findings = self._detect_with_scc(graph_algo)
        if findings is not None:
            return findings

        # Fall back to traditional path-based detection only if Rust fails
        logger.warning("Rust SCC detection failed, falling back to path queries")
        return self._detect_with_path_queries()

    def _detect_with_scc(self, graph_algo: GraphAlgorithms) -> Optional[List[Finding]]:
        """Detect circular dependencies using Rust SCC algorithm.

        SCC (Strongly Connected Components) finds all cycles in O(V+E) time.
        Components with size > 1 represent circular dependencies.

        Uses Rust Tarjan's algorithm - no GDS projection required.

        Args:
            graph_algo: GraphAlgorithms instance

        Returns:
            List of findings, or None if SCC detection failed
        """
        findings: List[Finding] = []

        try:
            # Calculate SCC using Rust algorithm (no projection needed)
            result = graph_algo.calculate_scc()
            if not result:
                logger.warning("Failed to calculate SCC")
                return None

            component_count = result.get("componentCount", 0)
            logger.info(f"SCC found {component_count} components")

            # Get cycles (components with size > 1)
            cycles = graph_algo.get_scc_cycles(min_cycle_size=2)

            for cycle_data in cycles:
                cycle_size = cycle_data.get("cycle_size", 0)
                file_paths = cycle_data.get("file_paths", [])
                file_names = cycle_data.get("file_names", [])
                edges = cycle_data.get("edges", [])
                component_id = cycle_data.get("component_id")

                if not file_paths or cycle_size < 2:
                    continue

                finding = self._create_finding(
                    cycle_files=file_paths,
                    cycle_length=cycle_size,
                    cycle_names=file_names,
                    edges=edges,
                    detection_method="SCC"
                )
                findings.append(finding)

            logger.info(f"SCC detection found {len(findings)} circular dependencies")
            return findings

        except Exception as e:
            logger.error(f"Error in SCC detection: {e}", exc_info=True)
            return None

    def _detect_with_path_queries(self) -> List[Finding]:
        """Detect circular dependencies using traditional path queries.

        This is the fallback method when GDS is not available.
        Uses bounded shortestPath queries to find cycles.

        Returns:
            List of findings
        """
        findings: List[Finding] = []

        # Original optimized query using bounded path traversal
        query = f"""
        MATCH (f1:File)
        MATCH (f2:File)
        WHERE {self.id_func}(f1) < {self.id_func}(f2) AND f1 <> f2
        MATCH path = shortestPath((f1)-[:IMPORTS*1..15]->(f2))
        MATCH cyclePath = shortestPath((f2)-[:IMPORTS*1..15]->(f1))
        WITH DISTINCT [node IN nodes(path) + nodes(cyclePath) WHERE node:File | node.filePath] AS cycle
        WHERE size(cycle) > 1
        RETURN cycle, size(cycle) AS cycle_length
        ORDER BY cycle_length DESC
        """

        results = self.db.execute_query(query)

        # Deduplicate cycles
        seen_cycles: Set[tuple] = set()

        for record in results:
            cycle = record["cycle"]
            cycle_length = record["cycle_length"]

            # Normalize to canonical form
            normalized = self._normalize_cycle(cycle)
            if normalized in seen_cycles:
                continue
            seen_cycles.add(normalized)

            finding = self._create_finding(
                cycle_files=cycle,
                cycle_length=cycle_length,
                detection_method="path_query"
            )
            findings.append(finding)

        return findings

    def _create_finding(
        self,
        cycle_files: List[str],
        cycle_length: int,
        cycle_names: Optional[List[str]] = None,
        edges: Optional[List[dict]] = None,
        detection_method: str = "unknown"
    ) -> Finding:
        """Create a finding for a circular dependency.

        Args:
            cycle_files: List of file paths in the cycle
            cycle_length: Number of files in the cycle
            cycle_names: Optional list of qualified names
            edges: Optional list of edge relationships
            detection_method: How the cycle was detected

        Returns:
            Finding object
        """
        finding_id = str(uuid.uuid4())

        # Format cycle for description
        display_files = [f.split("/")[-1] for f in cycle_files[:5]]
        cycle_display = " -> ".join(display_files)
        if cycle_length > 5:
            cycle_display += f" ... ({cycle_length} files total)"

        severity = self._calculate_severity(cycle_length)

        # Build edge description if available
        edge_description = ""
        if edges:
            edge_lines = []
            for edge in edges[:10]:
                from_file = edge.get("from", "").split(".")[-1]
                to_file = edge.get("to", "").split(".")[-1]
                if from_file and to_file:
                    edge_lines.append(f"  {from_file} imports {to_file}")
            if edge_lines:
                edge_description = "\n\n**Import chain:**\n" + "\n".join(edge_lines)
                if len(edges) > 10:
                    edge_description += f"\n  ... and {len(edges) - 10} more imports"

        description = (
            f"Found circular import chain: {cycle_display}"
            f"{edge_description}"
        )

        finding = Finding(
            id=finding_id,
            detector="CircularDependencyDetector",
            severity=severity,
            title=f"Circular dependency involving {cycle_length} files",
            description=description,
            affected_nodes=cycle_names or cycle_files,
            affected_files=cycle_files,
            graph_context={
                "cycle_length": cycle_length,
                "cycle_files": cycle_files,
                "detection_method": detection_method,
                "edges": edges or [],
            },
            suggested_fix=self._suggest_fix(cycle_length),
            estimated_effort=self._estimate_effort(cycle_length),
            created_at=datetime.now(),
        )

        # Add collaboration metadata
        confidence = 0.95  # High confidence - direct graph query
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="CircularDependencyDetector",
            confidence=confidence,
            evidence=["circular_import", f"cycle_length_{cycle_length}", detection_method],
            tags=["circular_dependency", "architecture", "imports"]
        ))

        # Flag entities for cross-detector collaboration
        if self.enricher:
            for file_path in cycle_files:
                try:
                    self.enricher.flag_entity(
                        entity_qualified_name=file_path,
                        detector="CircularDependencyDetector",
                        severity=severity.value,
                        issues=["circular_dependency"],
                        confidence=confidence,
                        metadata={
                            "cycle_length": cycle_length,
                            "detection_method": detection_method
                        }
                    )
                except Exception:
                    pass  # Don't fail detection if enrichment fails

        return finding

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity based on cycle length.

        Args:
            finding: Finding to assess

        Returns:
            Severity level
        """
        cycle_length = finding.graph_context.get("cycle_length", 0)
        return self._calculate_severity(cycle_length)

    def _calculate_severity(self, cycle_length: int) -> Severity:
        """Calculate severity based on cycle characteristics.

        Args:
            cycle_length: Number of files in the cycle

        Returns:
            Severity level
        """
        if cycle_length >= 10:
            return Severity.CRITICAL
        elif cycle_length >= 5:
            return Severity.HIGH
        elif cycle_length >= 3:
            return Severity.MEDIUM
        else:
            return Severity.LOW

    def _suggest_fix(self, cycle_length: int) -> str:
        """Suggest how to fix the circular dependency.

        Args:
            cycle_length: Number of files in the cycle

        Returns:
            Fix suggestion
        """
        if cycle_length >= 5:
            return (
                "Large circular dependency detected. Consider:\n"
                "1. Extract shared interfaces/types into a separate module\n"
                "2. Use dependency injection to break tight coupling\n"
                "3. Refactor into layers with clear dependency direction\n"
                "4. Apply the Dependency Inversion Principle"
            )
        else:
            return (
                "Small circular dependency. Consider:\n"
                "1. Merge the circular modules if they're tightly coupled\n"
                "2. Extract common dependencies to a third module\n"
                "3. Use forward references (TYPE_CHECKING) for type hints"
            )

    def _estimate_effort(self, cycle_length: int) -> str:
        """Estimate effort to fix based on cycle size.

        Args:
            cycle_length: Number of files in the cycle

        Returns:
            Effort estimate
        """
        if cycle_length >= 10:
            return "Large (2-4 days)"
        elif cycle_length >= 5:
            return "Medium (1-2 days)"
        else:
            return "Small (2-4 hours)"

    def _normalize_cycle(self, cycle: List[str]) -> tuple:
        """Normalize cycle to canonical form by rotating to start with minimum element.

        This preserves the directionality of the cycle while ensuring the same
        cycle is always represented the same way.

        Args:
            cycle: List of file paths in the cycle

        Returns:
            Normalized tuple representation
        """
        if not cycle:
            return tuple()

        # Find the index of the minimum element
        min_idx = cycle.index(min(cycle))

        # Rotate the cycle to start with the minimum element
        rotated = cycle[min_idx:] + cycle[:min_idx]

        return tuple(rotated)

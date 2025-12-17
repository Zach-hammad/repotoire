"""Core utility detector using Harmonic Centrality (REPO-173, REPO-200).

Uses Rust-based Harmonic Centrality to identify central coordinator functions
and isolated/dead code. Harmonic centrality handles disconnected graphs
better than closeness centrality.

No GDS plugin required - runs entirely with Rust algorithms.

REPO-200: Updated to use Rust algorithms directly (no GDS dependency).
"""

from typing import List, Optional
from repotoire.detectors.base import CodeSmellDetector
from repotoire.detectors.graph_algorithms import GraphAlgorithms
from repotoire.graph.client import Neo4jClient
from repotoire.models import CollaborationMetadata, Finding, Severity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class CoreUtilityDetector(CodeSmellDetector):
    """Detects central coordinators and isolated code using Harmonic Centrality.

    Harmonic centrality measures how close a function is to all other functions,
    handling disconnected graphs gracefully (unlike closeness centrality).

    Detects:
    - Central coordinators: High harmonic + high complexity (bottleneck risk)
    - Isolated code: Low harmonic + few connections (potential dead code)
    """

    # Complexity threshold for escalating central coordinator severity
    HIGH_COMPLEXITY_THRESHOLD = 20

    # Minimum callers to not be considered isolated
    MIN_CALLERS_THRESHOLD = 2

    def __init__(self, neo4j_client: Neo4jClient):
        """Initialize detector with Neo4j client.

        Args:
            neo4j_client: Neo4j database client
        """
        super().__init__(neo4j_client)

    def detect(self) -> List[Finding]:
        """Detect central coordinators and isolated code.

        Uses Rust harmonic centrality algorithm - no GDS plugin required.

        Returns:
            List of findings
        """
        findings = []

        # Initialize graph algorithms (uses Rust - no GDS required)
        graph_algo = GraphAlgorithms(self.db)

        try:
            # Calculate harmonic centrality using Rust algorithm
            logger.info("Calculating harmonic centrality using Rust algorithm")
            result = graph_algo.calculate_harmonic_centrality()
            if not result:
                logger.warning("Failed to calculate harmonic centrality")
                return findings

            # Get statistics for dynamic thresholds
            stats = graph_algo.get_harmonic_statistics()
            if not stats:
                logger.warning("Failed to get harmonic statistics")
                return findings

            total_functions = stats.get("total_functions", 0)
            p95 = stats.get("p95_harmonic") or 0.8
            p10 = stats.get("p10_harmonic") or 0.2
            avg = stats.get("avg_harmonic") or 0.5

            logger.info(
                f"Harmonic centrality stats: avg={avg:.3f}, p10={p10:.3f}, p95={p95:.3f}"
            )

            # Find central coordinators (top 5% by harmonic)
            central_functions = graph_algo.get_high_harmonic_functions(
                threshold=p95,
                limit=50
            )

            for func in central_functions:
                finding = self._create_central_coordinator_finding(func, stats)
                if finding:
                    findings.append(finding)

            # Find isolated code (bottom 10% by harmonic + few callers)
            isolated_functions = graph_algo.get_low_harmonic_functions(
                threshold=p10,
                limit=100
            )

            for func in isolated_functions:
                # Only flag if also has few callers
                if func.get("caller_count", 0) < self.MIN_CALLERS_THRESHOLD:
                    finding = self._create_isolated_code_finding(func, stats)
                    if finding:
                        findings.append(finding)

            return findings

        except Exception as e:
            logger.error(f"Error in harmonic centrality detection: {e}", exc_info=True)
            return findings

    def _create_central_coordinator_finding(
        self,
        func: dict,
        stats: dict
    ) -> Optional[Finding]:
        """Create finding for central coordinator function."""
        qualified_name = func.get("qualified_name", "unknown")
        name = func.get("name", "unknown")
        harmonic = func.get("harmonic_score", 0)
        complexity = func.get("complexity") or 0
        loc = func.get("loc") or 0
        file_path = func.get("file_path", "unknown")
        line_number = func.get("line_number", 0)
        caller_count = func.get("caller_count", 0)
        callee_count = func.get("callee_count", 0)

        max_harmonic = stats.get("max_harmonic", 1)
        percentile = (harmonic / max_harmonic * 100) if max_harmonic > 0 else 0

        # Determine severity based on complexity
        if complexity > self.HIGH_COMPLEXITY_THRESHOLD:
            severity = Severity.HIGH
            title = f"Central coordinator with high complexity: {name}"
        else:
            severity = Severity.MEDIUM
            title = f"Central coordinator: {name}"

        description = (
            f"Function `{name}` has high harmonic centrality "
            f"(score: {harmonic:.3f}, {percentile:.0f}th percentile).\n\n"
            f"**What this means:**\n"
            f"- Can reach most functions in the codebase quickly\n"
            f"- Acts as a coordination point for execution flow\n"
            f"- Changes here have wide-reaching effects\n\n"
            f"**Metrics:**\n"
            f"- Harmonic centrality: {harmonic:.3f}\n"
            f"- Complexity: {complexity}\n"
            f"- Lines of code: {loc}\n"
            f"- Callers: {caller_count}\n"
            f"- Callees: {callee_count}"
        )

        if complexity > self.HIGH_COMPLEXITY_THRESHOLD:
            description += (
                f"\n\n**Warning:** High complexity ({complexity}) combined with "
                f"central position creates significant risk."
            )

        suggested_fix = (
            "**For central coordinators:**\n\n"
            "1. **Ensure test coverage**: This function affects many code paths\n\n"
            "2. **Add monitoring**: Track performance and errors here\n\n"
            "3. **Review complexity**: Consider splitting if too complex\n\n"
            "4. **Document thoroughly**: Others need to understand this code\n\n"
            "5. **Consider patterns**:\n"
            "   - Facade pattern to simplify interface\n"
            "   - Mediator pattern to manage interactions\n"
            "   - Event-driven design to reduce coupling"
        )

        # Estimate effort based on complexity and caller count
        if complexity > self.HIGH_COMPLEXITY_THRESHOLD * 2 or caller_count > 20:
            estimated_effort = "Large (2-4 hours)"
        elif complexity > self.HIGH_COMPLEXITY_THRESHOLD or caller_count > 10:
            estimated_effort = "Medium (1-2 hours)"
        else:
            estimated_effort = "Small (30-60 minutes)"

        finding = Finding(
            id=f"central_coordinator_{hash(qualified_name) % 100000}",
            detector="CoreUtilityDetector",
            severity=severity,
            title=title,
            description=description,
            affected_nodes=[qualified_name],
            affected_files=[file_path],
            suggested_fix=suggested_fix,
            estimated_effort=estimated_effort,
            graph_context={
                "harmonic_score": harmonic,
                "percentile": percentile,
                "complexity": complexity,
                "loc": loc,
                "caller_count": caller_count,
                "callee_count": callee_count,
                "line_number": line_number,
            }
        )

        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="CoreUtilityDetector",
            confidence=0.85,
            evidence=["high_harmonic_centrality"],
            tags=["architecture", "centrality", "coordinator"]
        ))

        return finding

    def _create_isolated_code_finding(
        self,
        func: dict,
        stats: dict
    ) -> Optional[Finding]:
        """Create finding for isolated/dead code."""
        qualified_name = func.get("qualified_name", "unknown")
        name = func.get("name", "unknown")
        harmonic = func.get("harmonic_score", 0)
        complexity = func.get("complexity") or 0
        loc = func.get("loc") or 0
        file_path = func.get("file_path", "unknown")
        line_number = func.get("line_number", 0)
        caller_count = func.get("caller_count", 0)
        callee_count = func.get("callee_count", 0)

        # Skip very small functions (likely utilities or stubs)
        if loc and loc < 5:
            return None

        max_harmonic = stats.get("max_harmonic", 1)
        percentile = (harmonic / max_harmonic * 100) if max_harmonic > 0 else 0

        # Determine severity
        if caller_count == 0 and callee_count == 0:
            severity = Severity.MEDIUM
            isolation_level = "completely isolated"
        elif caller_count == 0:
            severity = Severity.LOW
            isolation_level = "never called"
        else:
            severity = Severity.LOW
            isolation_level = "barely connected"

        description = (
            f"Function `{name}` has very low harmonic centrality "
            f"(score: {harmonic:.3f}, {percentile:.0f}th percentile).\n\n"
            f"**Status:** {isolation_level}\n\n"
            f"**What this means:**\n"
            f"- Disconnected from most of the codebase\n"
            f"- May be dead code or unused functionality\n"
            f"- Could be misplaced or poorly integrated\n\n"
            f"**Metrics:**\n"
            f"- Harmonic centrality: {harmonic:.3f}\n"
            f"- Callers: {caller_count}\n"
            f"- Callees: {callee_count}\n"
            f"- Lines of code: {loc}"
        )

        suggested_fix = (
            "**Investigate isolated code:**\n\n"
            "1. **Check if dead code**: Search for usages across the codebase\n\n"
            "2. **Check if test-only**: May be called only from tests\n\n"
            "3. **Check if entry point**: CLI commands, API endpoints, etc.\n\n"
            "4. **Consider removal**: If truly unused, delete it\n\n"
            "5. **Consider integration**: If needed, integrate properly with the codebase"
        )

        # Isolated code is usually easy to remove or integrate
        estimated_effort = "Small (15-30 minutes)" if loc and loc < 50 else "Small (30-60 minutes)"

        finding = Finding(
            id=f"isolated_code_{hash(qualified_name) % 100000}",
            detector="CoreUtilityDetector",
            severity=severity,
            title=f"Isolated code: {name} ({isolation_level})",
            description=description,
            affected_nodes=[qualified_name],
            affected_files=[file_path],
            suggested_fix=suggested_fix,
            estimated_effort=estimated_effort,
            graph_context={
                "harmonic_score": harmonic,
                "percentile": percentile,
                "caller_count": caller_count,
                "callee_count": callee_count,
                "loc": loc,
                "line_number": line_number,
            }
        )

        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="CoreUtilityDetector",
            confidence=0.7,
            evidence=["low_harmonic_centrality", "few_callers"],
            tags=["dead_code", "isolated", "cleanup"]
        ))

        return finding

    def severity(self, finding: Finding) -> Severity:
        """Return severity (already set in detect)."""
        return finding.severity

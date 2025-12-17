"""Influential code detector using PageRank (REPO-169, REPO-200).

Uses Rust-based PageRank to identify truly important code components based on
incoming dependencies. Distinguishes legitimate core infrastructure from
bloated god classes.

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


class InfluentialCodeDetector(CodeSmellDetector):
    """Detects influential code and potential god classes using PageRank.

    PageRank measures the importance of a function/class based on how many
    other components depend on it (and how important those dependents are).

    Detects:
    - High influence code: High PageRank indicates core infrastructure
    - Bloated god classes: Low PageRank + high complexity = refactor target
    - Critical bottlenecks: High PageRank + high complexity = high risk
    """

    # Complexity threshold for flagging as complex
    HIGH_COMPLEXITY_THRESHOLD = 15

    # Lines of code threshold for being "large"
    HIGH_LOC_THRESHOLD = 200

    # Minimum PageRank to be considered "high" (relative to threshold)
    MIN_PAGERANK_PERCENTILE = 90

    def __init__(self, neo4j_client: Neo4jClient):
        """Initialize detector with Neo4j client.

        Args:
            neo4j_client: Neo4j database client
        """
        super().__init__(neo4j_client)

    def detect(self) -> List[Finding]:
        """Detect influential code and potential god classes.

        Uses Rust PageRank algorithm - no GDS plugin required.

        Returns:
            List of findings
        """
        findings = []

        # Initialize graph algorithms (uses Rust - no GDS required)
        graph_algo = GraphAlgorithms(self.db)

        try:
            # Calculate PageRank using Rust algorithm
            logger.info("Calculating PageRank using Rust algorithm")
            result = graph_algo.calculate_pagerank()
            if not result:
                logger.warning("Failed to calculate PageRank")
                return findings

            # Get high PageRank functions (core infrastructure)
            high_pagerank = self._get_high_pagerank_functions(graph_algo)
            for func in high_pagerank:
                finding = self._create_influential_code_finding(func)
                if finding:
                    findings.append(finding)

            # Get low PageRank + high complexity (bloated code)
            bloated_code = self._get_bloated_code(graph_algo)
            for func in bloated_code:
                finding = self._create_bloated_code_finding(func)
                if finding:
                    findings.append(finding)

            return findings

        except Exception as e:
            logger.error(f"Error in PageRank detection: {e}", exc_info=True)
            return findings

    def _get_high_pagerank_functions(
        self,
        graph_algo: GraphAlgorithms,
        limit: int = 50
    ) -> List[dict]:
        """Get functions with high PageRank scores.

        Args:
            graph_algo: GraphAlgorithms instance
            limit: Maximum results

        Returns:
            List of high PageRank functions with metrics
        """
        query = """
        MATCH (f:Function)
        WHERE f.pagerank IS NOT NULL
        WITH f, f.pagerank AS pr
        ORDER BY pr DESC
        WITH collect({func: f, pagerank: pr}) AS all_funcs
        WITH all_funcs,
             toInteger(size(all_funcs) * 0.1) AS top_10_percent_idx
        WITH all_funcs,
             CASE WHEN top_10_percent_idx < size(all_funcs)
                  THEN all_funcs[top_10_percent_idx].pagerank
                  ELSE 0 END AS threshold
        UNWIND all_funcs AS item
        WITH item.func AS f, item.pagerank AS pr, threshold
        WHERE pr >= threshold AND pr > 0
        OPTIONAL MATCH (caller:Function)-[:CALLS]->(f)
        WITH f, pr, threshold, count(DISTINCT caller) AS caller_count
        OPTIONAL MATCH (f)-[:CALLS]->(callee:Function)
        WITH f, pr, threshold, caller_count, count(DISTINCT callee) AS callee_count
        RETURN
            f.qualifiedName AS qualified_name,
            f.name AS name,
            f.filePath AS file_path,
            f.lineNumber AS line_number,
            pr AS pagerank,
            threshold AS pagerank_threshold,
            coalesce(f.complexity, 0) AS complexity,
            coalesce(f.loc, 0) AS loc,
            caller_count,
            callee_count
        ORDER BY pr DESC
        LIMIT $limit
        """
        return self.db.execute_query(query, parameters={"limit": limit})

    def _get_bloated_code(
        self,
        graph_algo: GraphAlgorithms,
        limit: int = 50
    ) -> List[dict]:
        """Get functions with low PageRank but high complexity (bloated code).

        These are candidates for refactoring - they're complex but not
        legitimately important to the codebase.

        Args:
            graph_algo: GraphAlgorithms instance
            limit: Maximum results

        Returns:
            List of bloated functions
        """
        query = """
        MATCH (f:Function)
        WHERE f.pagerank IS NOT NULL
          AND (f.complexity IS NOT NULL OR f.loc IS NOT NULL)
        WITH f, f.pagerank AS pr
        ORDER BY pr DESC
        WITH collect({func: f, pagerank: pr}) AS all_funcs
        WITH all_funcs,
             // Get bottom 50% by PageRank
             toInteger(size(all_funcs) * 0.5) AS median_idx
        WITH all_funcs,
             CASE WHEN median_idx < size(all_funcs)
                  THEN all_funcs[median_idx].pagerank
                  ELSE 0 END AS median_pr
        UNWIND all_funcs AS item
        WITH item.func AS f, item.pagerank AS pr, median_pr
        WHERE pr <= median_pr
          AND (coalesce(f.complexity, 0) >= $complexity_threshold
               OR coalesce(f.loc, 0) >= $loc_threshold)
        OPTIONAL MATCH (caller:Function)-[:CALLS]->(f)
        WITH f, pr, median_pr, count(DISTINCT caller) AS caller_count
        RETURN
            f.qualifiedName AS qualified_name,
            f.name AS name,
            f.filePath AS file_path,
            f.lineNumber AS line_number,
            pr AS pagerank,
            median_pr AS median_pagerank,
            coalesce(f.complexity, 0) AS complexity,
            coalesce(f.loc, 0) AS loc,
            caller_count
        ORDER BY f.complexity DESC, f.loc DESC
        LIMIT $limit
        """
        return self.db.execute_query(query, parameters={
            "complexity_threshold": self.HIGH_COMPLEXITY_THRESHOLD,
            "loc_threshold": self.HIGH_LOC_THRESHOLD,
            "limit": limit
        })

    def _create_influential_code_finding(self, func: dict) -> Optional[Finding]:
        """Create finding for influential code (high PageRank).

        High PageRank indicates legitimate importance. Flag for awareness,
        especially if also complex (bottleneck risk).
        """
        qualified_name = func.get("qualified_name", "unknown")
        name = func.get("name", "unknown")
        pagerank = func.get("pagerank", 0)
        complexity = func.get("complexity", 0)
        loc = func.get("loc", 0)
        file_path = func.get("file_path", "unknown")
        line_number = func.get("line_number", 0)
        caller_count = func.get("caller_count", 0)
        callee_count = func.get("callee_count", 0)
        threshold = func.get("pagerank_threshold", 0)

        # Calculate percentile (approximate)
        percentile = min(99, 90 + (pagerank / max(threshold, 0.001)) * 5)

        # Determine severity based on complexity combined with influence
        if complexity >= self.HIGH_COMPLEXITY_THRESHOLD:
            severity = Severity.HIGH
            title = f"Critical bottleneck: {name}"
            risk_note = (
                f"\n\n**⚠️ High Risk:** High influence ({pagerank:.4f}) combined "
                f"with high complexity ({complexity}) creates significant risk. "
                f"Changes here affect many dependents."
            )
        else:
            severity = Severity.MEDIUM
            title = f"Core infrastructure: {name}"
            risk_note = ""

        description = (
            f"Function `{name}` has high PageRank score "
            f"({pagerank:.4f}, ~{percentile:.0f}th percentile).\n\n"
            f"**What this means:**\n"
            f"- Many other functions depend on this (directly or transitively)\n"
            f"- Changes here have wide-reaching effects across the codebase\n"
            f"- This is legitimately important infrastructure code\n\n"
            f"**Metrics:**\n"
            f"- PageRank: {pagerank:.4f}\n"
            f"- Complexity: {complexity}\n"
            f"- Lines of code: {loc}\n"
            f"- Direct callers: {caller_count}\n"
            f"- Direct callees: {callee_count}"
            f"{risk_note}"
        )

        suggested_fix = (
            "**For core infrastructure code:**\n\n"
            "1. **Ensure comprehensive test coverage**: This code affects "
            "many other components\n\n"
            "2. **Add monitoring and observability**: Track performance and errors\n\n"
            "3. **Document thoroughly**: Others depend on understanding this code\n\n"
            "4. **Review before changes**: Consider impact on dependents\n\n"
            "5. **Consider stability**: Avoid breaking changes; deprecate gradually"
        )

        if complexity >= self.HIGH_COMPLEXITY_THRESHOLD:
            suggested_fix += (
                "\n\n**For high-complexity bottlenecks:**\n\n"
                "6. **Consider refactoring**: Break into smaller, focused functions\n\n"
                "7. **Extract interfaces**: Reduce coupling through abstraction\n\n"
                "8. **Use feature flags**: De-risk changes with gradual rollout"
            )

        # Estimate effort based on complexity - core infrastructure requires careful changes
        if complexity >= self.HIGH_COMPLEXITY_THRESHOLD:
            estimated_effort = "Large (4-8 hours)"
        else:
            estimated_effort = "Medium (1-2 hours)"

        finding = Finding(
            id=f"influential_code_{hash(qualified_name) % 100000}",
            detector="InfluentialCodeDetector",
            severity=severity,
            title=title,
            description=description,
            affected_nodes=[qualified_name],
            affected_files=[file_path],
            suggested_fix=suggested_fix,
            estimated_effort=estimated_effort,
            graph_context={
                "pagerank": pagerank,
                "percentile": percentile,
                "complexity": complexity,
                "loc": loc,
                "caller_count": caller_count,
                "callee_count": callee_count,
                "line_number": line_number,
            }
        )

        confidence = 0.9 if complexity >= self.HIGH_COMPLEXITY_THRESHOLD else 0.8
        evidence = ["high_pagerank"]
        if complexity >= self.HIGH_COMPLEXITY_THRESHOLD:
            evidence.append("high_complexity")

        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="InfluentialCodeDetector",
            confidence=confidence,
            evidence=evidence,
            tags=["architecture", "pagerank", "core_infrastructure"]
        ))

        return finding

    def _create_bloated_code_finding(self, func: dict) -> Optional[Finding]:
        """Create finding for bloated code (low PageRank + high complexity).

        These are prime refactoring targets - complex but not widely depended on.
        """
        qualified_name = func.get("qualified_name", "unknown")
        name = func.get("name", "unknown")
        pagerank = func.get("pagerank", 0)
        complexity = func.get("complexity", 0)
        loc = func.get("loc", 0)
        file_path = func.get("file_path", "unknown")
        line_number = func.get("line_number", 0)
        caller_count = func.get("caller_count", 0)
        median_pr = func.get("median_pagerank", 0)

        # Determine severity based on how bloated
        if complexity >= self.HIGH_COMPLEXITY_THRESHOLD * 2:  # Very complex
            severity = Severity.HIGH
            bloat_level = "severely bloated"
        elif complexity >= self.HIGH_COMPLEXITY_THRESHOLD:
            severity = Severity.MEDIUM
            bloat_level = "bloated"
        elif loc >= self.HIGH_LOC_THRESHOLD * 2:  # Very large
            severity = Severity.MEDIUM
            bloat_level = "oversized"
        else:
            severity = Severity.LOW
            bloat_level = "potentially bloated"

        description = (
            f"Function `{name}` is {bloat_level}: high complexity/size "
            f"but low influence (PageRank {pagerank:.4f}).\n\n"
            f"**What this means:**\n"
            f"- This code is complex but few other parts depend on it\n"
            f"- Not legitimately important infrastructure\n"
            f"- Prime candidate for refactoring or removal\n\n"
            f"**Metrics:**\n"
            f"- PageRank: {pagerank:.4f} (median: {median_pr:.4f})\n"
            f"- Complexity: {complexity}\n"
            f"- Lines of code: {loc}\n"
            f"- Direct callers: {caller_count}"
        )

        suggested_fix = (
            "**For bloated code:**\n\n"
            "1. **Consider removal**: If truly unused, delete it\n\n"
            "2. **Simplify**: Break into smaller, focused functions\n\n"
            "3. **Extract reusable parts**: Move useful logic to shared utilities\n\n"
            "4. **Review necessity**: Challenge whether this complexity is needed\n\n"
            "5. **Add tests first**: Before refactoring, ensure test coverage"
        )

        # Bloated code with few dependents is easier to refactor
        if severity == Severity.HIGH:
            estimated_effort = "Medium (2-4 hours)"
        elif severity == Severity.MEDIUM:
            estimated_effort = "Medium (1-2 hours)"
        else:
            estimated_effort = "Small (30-60 minutes)"

        finding = Finding(
            id=f"bloated_code_{hash(qualified_name) % 100000}",
            detector="InfluentialCodeDetector",
            severity=severity,
            title=f"Bloated code: {name} ({bloat_level})",
            description=description,
            affected_nodes=[qualified_name],
            affected_files=[file_path],
            suggested_fix=suggested_fix,
            estimated_effort=estimated_effort,
            graph_context={
                "pagerank": pagerank,
                "median_pagerank": median_pr,
                "complexity": complexity,
                "loc": loc,
                "caller_count": caller_count,
                "line_number": line_number,
                "bloat_level": bloat_level,
            }
        )

        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="InfluentialCodeDetector",
            confidence=0.75,
            evidence=["low_pagerank", "high_complexity"],
            tags=["refactoring", "god_class", "bloat"]
        ))

        return finding

    def severity(self, finding: Finding) -> Severity:
        """Return severity (already set in detect)."""
        return finding.severity

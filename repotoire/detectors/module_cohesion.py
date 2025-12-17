"""Module cohesion detector using Leiden community detection (REPO-172, REPO-200).

Uses Rust-based Leiden algorithm to identify natural module boundaries
and detect modularity issues like misplaced files, god modules, and
poor overall architecture.

No GDS plugin required - runs entirely with Rust algorithms.

REPO-200: Updated to use Rust Leiden algorithm (no GDS dependency).
"""

from typing import List, Optional
from repotoire.detectors.base import CodeSmellDetector
from repotoire.detectors.graph_algorithms import GraphAlgorithms
from repotoire.graph.client import Neo4jClient
from repotoire.models import CollaborationMetadata, Finding, Severity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class ModuleCohesionDetector(CodeSmellDetector):
    """Detects modularity issues using Leiden community detection.

    Leiden algorithm identifies natural module boundaries by maximizing
    modularity - finding groups of files that are densely connected internally
    but sparsely connected externally. Uses Rust implementation for speed.

    Detects:
    - Poor global modularity (monolithic architecture)
    - God modules (communities > 20% of codebase)
    - Misplaced files (files in wrong community)
    - High inter-community coupling
    """

    # Modularity score interpretation
    MODULARITY_POOR = 0.3
    MODULARITY_MODERATE = 0.5
    MODULARITY_GOOD = 0.7

    # God module threshold (% of total files)
    GOD_MODULE_THRESHOLD = 20.0

    def __init__(self, neo4j_client: Neo4jClient):
        """Initialize detector with Neo4j client.

        Args:
            neo4j_client: Neo4j database client
        """
        super().__init__(neo4j_client)
        self.modularity_score: Optional[float] = None
        self.community_count: int = 0

    def detect(self) -> List[Finding]:
        """Detect modularity issues using Leiden community detection.

        Uses Rust Leiden algorithm - no GDS plugin required.

        Returns:
            List of findings for modularity issues
        """
        findings = []

        # Initialize graph algorithms (uses Rust - no GDS required)
        graph_algo = GraphAlgorithms(self.db)

        try:
            # Run Leiden community detection using Rust algorithm
            logger.info("Running Leiden community detection using Rust algorithm")
            result = graph_algo.calculate_file_communities()
            if not result:
                logger.warning("Failed to run Leiden community detection")
                return findings

            self.modularity_score = result.get("modularity", 0.0)
            self.community_count = result.get("communityCount", 0)

            logger.info(
                f"Louvain analysis: modularity={self.modularity_score:.3f}, "
                f"communities={self.community_count}"
            )

            # Check global modularity
            if self.modularity_score < self.MODULARITY_POOR:
                findings.append(self._create_poor_modularity_finding())

            # Check for god modules
            god_modules = graph_algo.get_god_modules(self.GOD_MODULE_THRESHOLD)
            for gm in god_modules:
                findings.append(self._create_god_module_finding(gm))

            # Check for misplaced files
            misplaced = graph_algo.get_misplaced_files()
            for mp in misplaced:
                findings.append(self._create_misplaced_file_finding(mp))

            # Check inter-community coupling
            inter_edges = graph_algo.get_inter_community_edges()
            high_coupling = [e for e in inter_edges if e.get("edge_count", 0) >= 5]
            if high_coupling:
                findings.append(self._create_coupling_finding(high_coupling))

            return findings

        except Exception as e:
            logger.error(f"Error in module cohesion detection: {e}", exc_info=True)
            return findings

    def _create_poor_modularity_finding(self) -> Finding:
        """Create finding for poor global modularity."""
        if self.modularity_score < 0.2:
            severity = Severity.HIGH
            level = "very poor"
        else:
            severity = Severity.MEDIUM
            level = "poor"

        description = (
            f"The codebase has {level} modularity (score: {self.modularity_score:.3f}). "
            f"Leiden algorithm detected {self.community_count} natural module boundaries.\n\n"
            f"**Modularity Score Interpretation:**\n"
            f"- < 0.3: Poor (monolithic, tightly coupled)\n"
            f"- 0.3-0.5: Moderate (some structure, room for improvement)\n"
            f"- 0.5-0.7: Good (well-organized)\n"
            f"- > 0.7: Excellent (clear boundaries)\n\n"
            f"**Impact:**\n"
            f"- Changes have high blast radius\n"
            f"- Difficult to test in isolation\n"
            f"- Hard to understand and navigate"
        )

        suggested_fix = (
            "**Improve modularity:**\n\n"
            "1. **Identify coupling hotspots**: Use `repotoire analyze` to find "
            "files with excessive cross-module dependencies\n\n"
            "2. **Extract cohesive modules**: Group related functionality into "
            "dedicated packages\n\n"
            "3. **Define clear interfaces**: Create facade classes or APIs "
            "between modules\n\n"
            "4. **Apply dependency inversion**: Use abstractions to reduce "
            "direct coupling\n\n"
            "5. **Consider domain boundaries**: Align modules with business "
            "domains (DDD approach)"
        )

        # Poor modularity is a significant architectural issue requiring sustained effort
        estimated_effort = "Large (1-2 weeks)" if severity == Severity.HIGH else "Large (3-5 days)"

        finding = Finding(
            id="modularity_poor_global",
            detector="ModuleCohesionDetector",
            severity=severity,
            title=f"Poor codebase modularity (score: {self.modularity_score:.2f})",
            description=description,
            affected_nodes=[],
            affected_files=[],
            suggested_fix=suggested_fix,
            estimated_effort=estimated_effort,
            graph_context={
                "modularity_score": self.modularity_score,
                "community_count": self.community_count,
                "threshold": self.MODULARITY_POOR,
            }
        )

        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="ModuleCohesionDetector",
            confidence=0.85,
            evidence=["louvain_modularity"],
            tags=["architecture", "modularity", "coupling"]
        ))

        return finding

    def _create_god_module_finding(self, god_module: dict) -> Finding:
        """Create finding for oversized community (god module)."""
        community_id = god_module.get("community_id")
        size = god_module.get("community_size", 0)
        percentage = god_module.get("percentage", 0)
        total_files = god_module.get("total_files", 0)

        if percentage >= 40:
            severity = Severity.HIGH
        else:
            severity = Severity.MEDIUM

        description = (
            f"Community {community_id} contains {size} files ({percentage:.1f}% of codebase).\n\n"
            f"A single module containing >20% of files indicates:\n"
            f"- Multiple responsibilities crammed together\n"
            f"- Missing abstraction layers\n"
            f"- Organic growth without refactoring\n\n"
            f"**Statistics:**\n"
            f"- Files in this community: {size}\n"
            f"- Total files: {total_files}\n"
            f"- Percentage: {percentage:.1f}%"
        )

        suggested_fix = (
            "**Split god module:**\n\n"
            "1. **Analyze internal structure**: Look for natural sub-groupings\n\n"
            "2. **Identify responsibility boundaries**: Each sub-module should "
            "have a single purpose\n\n"
            "3. **Extract incrementally**: Move cohesive file groups to new packages\n\n"
            "4. **Update imports**: Establish clear dependency direction\n\n"
            "5. **Add facade**: Create a module-level API if needed for backward "
            "compatibility"
        )

        # God modules require significant refactoring effort
        estimated_effort = "Large (1-2 days)" if severity == Severity.HIGH else "Large (4-8 hours)"

        finding = Finding(
            id=f"modularity_god_module_{community_id}",
            detector="ModuleCohesionDetector",
            severity=severity,
            title=f"God module detected: Community {community_id} ({percentage:.0f}% of files)",
            description=description,
            affected_nodes=[],
            affected_files=[],
            suggested_fix=suggested_fix,
            estimated_effort=estimated_effort,
            graph_context={
                "community_id": community_id,
                "community_size": size,
                "percentage": percentage,
                "total_files": total_files,
            }
        )

        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="ModuleCohesionDetector",
            confidence=0.9,
            evidence=["community_size", "percentage_threshold"],
            tags=["architecture", "god_module", "refactoring"]
        ))

        return finding

    def _create_misplaced_file_finding(self, misplaced: dict) -> Finding:
        """Create finding for potentially misplaced file."""
        qualified_name = misplaced.get("qualified_name", "unknown")
        file_path = misplaced.get("file_path", "unknown")
        current_community = misplaced.get("current_community")
        same_imports = misplaced.get("same_community_imports", 0)
        other_imports = misplaced.get("other_community_imports", 0)
        external_ratio = misplaced.get("external_ratio", 0)

        severity = Severity.LOW if external_ratio < 0.8 else Severity.MEDIUM

        description = (
            f"File `{file_path}` imports more from other communities than its own.\n\n"
            f"**Import analysis:**\n"
            f"- Imports from same community: {same_imports}\n"
            f"- Imports from other communities: {other_imports}\n"
            f"- External ratio: {external_ratio:.1%}\n\n"
            f"This suggests the file may be in the wrong location or has "
            f"responsibilities that belong elsewhere."
        )

        suggested_fix = (
            "**Options:**\n\n"
            "1. **Move file**: Relocate to the package where most of its "
            "dependencies live\n\n"
            "2. **Refactor dependencies**: If the file should stay, refactor "
            "to use local dependencies\n\n"
            "3. **Extract shared code**: If multiple modules need this code, "
            "extract to a shared utilities module\n\n"
            "4. **Review design**: The file may be doing too much - consider "
            "splitting responsibilities"
        )

        # Moving a misplaced file is relatively straightforward
        estimated_effort = "Small (30-60 minutes)" if severity == Severity.LOW else "Medium (1-2 hours)"

        finding = Finding(
            id=f"modularity_misplaced_{hash(qualified_name) % 10000}",
            detector="ModuleCohesionDetector",
            severity=severity,
            title=f"Potentially misplaced file: {file_path}",
            description=description,
            affected_nodes=[qualified_name],
            affected_files=[file_path],
            suggested_fix=suggested_fix,
            estimated_effort=estimated_effort,
            graph_context={
                "current_community": current_community,
                "same_community_imports": same_imports,
                "other_community_imports": other_imports,
                "external_ratio": external_ratio,
            }
        )

        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="ModuleCohesionDetector",
            confidence=0.7,
            evidence=["import_analysis", "community_membership"],
            tags=["architecture", "misplaced", "organization"]
        ))

        return finding

    def _create_coupling_finding(self, high_coupling_edges: List[dict]) -> Finding:
        """Create finding for high inter-community coupling."""
        total_cross_edges = sum(e.get("edge_count", 0) for e in high_coupling_edges)
        top_3 = high_coupling_edges[:3]

        top_pairs_desc = "\n".join([
            f"- Communities {e['source_community']} ↔ {e['target_community']}: "
            f"{e['edge_count']} imports"
            for e in top_3
        ])

        description = (
            f"High coupling detected between module communities.\n\n"
            f"**Top coupled community pairs:**\n{top_pairs_desc}\n\n"
            f"Total cross-community imports: {total_cross_edges}\n\n"
            f"High inter-module coupling indicates:\n"
            f"- Unclear module boundaries\n"
            f"- Potential circular dependencies\n"
            f"- Difficulty testing modules independently"
        )

        suggested_fix = (
            "**Reduce coupling:**\n\n"
            "1. **Introduce interfaces**: Define abstract APIs between modules\n\n"
            "2. **Apply facade pattern**: Create single entry points to modules\n\n"
            "3. **Use dependency injection**: Decouple modules through abstractions\n\n"
            "4. **Consolidate shared code**: Move commonly-used code to a shared module\n\n"
            "5. **Review boundaries**: Consider if modules should be merged or split"
        )

        finding = Finding(
            id="modularity_high_coupling",
            detector="ModuleCohesionDetector",
            severity=Severity.MEDIUM,
            title=f"High inter-module coupling ({total_cross_edges} cross-boundary imports)",
            description=description,
            affected_nodes=[],
            affected_files=[],
            suggested_fix=suggested_fix,
            estimated_effort="Large (2-4 days)",
            graph_context={
                "total_cross_edges": total_cross_edges,
                "top_coupled_pairs": [
                    {
                        "source": e.get("source_community"),
                        "target": e.get("target_community"),
                        "count": e.get("edge_count"),
                    }
                    for e in top_3
                ],
            }
        )

        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="ModuleCohesionDetector",
            confidence=0.8,
            evidence=["inter_community_edges"],
            tags=["architecture", "coupling", "dependencies"]
        ))

        return finding

    def severity(self, finding: Finding) -> Severity:
        """Return severity (already set in detect)."""
        return finding.severity

    def get_modularity_score(self) -> Optional[float]:
        """Get the calculated modularity score.

        Returns:
            Modularity score (0-1) or None if not calculated
        """
        return self.modularity_score

    def get_community_count(self) -> int:
        """Get the number of detected communities.

        Returns:
            Number of communities
        """
        return self.community_count

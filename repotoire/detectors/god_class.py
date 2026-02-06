"""God class detector - finds overly complex classes.

REPO-152: Enhanced with community detection and PageRank importance scoring
for 40-60% false positive reduction.
REPO-416: Added path cache support for O(1) reachability queries.
"""

import logging
import re
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from repotoire.detectors.base import CodeSmellDetector

logger = logging.getLogger(__name__)
from repotoire.detectors.graph_algorithms import GraphAlgorithms
from repotoire.graph.enricher import GraphEnricher
from repotoire.models import CollaborationMetadata, Finding, Severity

if TYPE_CHECKING:
    from repotoire_fast import PyPathCache


class GodClassDetector(CodeSmellDetector):
    """Detects god classes (classes with too many responsibilities).

    Uses semantic pattern recognition to distinguish true god classes from
    legitimate design patterns like database clients, pipelines, and orchestrators.
    """

    # Default thresholds for god class detection
    DEFAULT_HIGH_METHOD_COUNT = 20
    DEFAULT_MEDIUM_METHOD_COUNT = 15
    DEFAULT_HIGH_COMPLEXITY = 100
    DEFAULT_MEDIUM_COMPLEXITY = 50
    DEFAULT_HIGH_LOC = 500
    DEFAULT_MEDIUM_LOC = 300
    DEFAULT_HIGH_LCOM = 0.8  # Lack of cohesion (0-1, higher is worse)
    DEFAULT_MEDIUM_LCOM = 0.6

    # Default design pattern exclusions (can be customized in config)
    # These are common patterns that may have many methods but high cohesion
    DEFAULT_EXCLUDED_PATTERNS = [
        r".*Client$",       # Database/API clients (e.g., Neo4jClient, HttpClient)
        r".*Connection$",   # Connection managers
        r".*Session$",      # Session handlers
        r".*Pipeline$",     # Data pipelines and orchestrators
        r".*Engine$",       # Workflow engines and processors
        r".*Generator$",    # Code generators and builders
        r".*Builder$",      # Builder pattern implementations
        r".*Factory$",      # Factory pattern implementations
        r".*Manager$",      # Resource managers
        r".*Controller$",   # MVC controllers
        r".*Adapter$",      # Adapter pattern implementations
        r".*Facade$",       # Facade pattern implementations
    ]

    def __init__(self, graph_client, detector_config: Optional[dict] = None, enricher: Optional[GraphEnricher] = None):
        """Initialize god class detector with configurable thresholds.

        Args:
            graph_client: FalkorDB database client
            detector_config: Optional dict with detector configuration:
                - repo_id: Repository UUID for filtering queries (multi-tenant isolation)
                - path_cache: Prebuilt path cache for O(1) reachability queries (REPO-416)
                - god_class_*: Threshold configuration
                - excluded_patterns: List of regex patterns to exclude (default: DEFAULT_EXCLUDED_PATTERNS)
                - use_pattern_exclusions: Enable/disable pattern-based exclusions (default: True)
                - use_semantic_analysis: Enable/disable graph-based semantic analysis (default: True)
                - use_community_analysis: Enable/disable community-based analysis (default: True) [REPO-152]
            enricher: Optional GraphEnricher for cross-detector collaboration
        """
        super().__init__(graph_client, detector_config)
        self.enricher = enricher

        # Load thresholds from config or use defaults
        config = detector_config or {}

        # Path cache for O(1) reachability queries (REPO-416)
        self.path_cache: Optional["PyPathCache"] = config.get("path_cache")
        self.high_method_count = config.get("god_class_high_method_count", self.DEFAULT_HIGH_METHOD_COUNT)
        self.medium_method_count = config.get("god_class_medium_method_count", self.DEFAULT_MEDIUM_METHOD_COUNT)
        self.high_complexity = config.get("god_class_high_complexity", self.DEFAULT_HIGH_COMPLEXITY)
        self.medium_complexity = config.get("god_class_medium_complexity", self.DEFAULT_MEDIUM_COMPLEXITY)
        self.high_loc = config.get("god_class_high_loc", self.DEFAULT_HIGH_LOC)
        self.medium_loc = config.get("god_class_medium_loc", self.DEFAULT_MEDIUM_LOC)
        self.high_lcom = config.get("god_class_high_lcom", self.DEFAULT_HIGH_LCOM)
        self.medium_lcom = config.get("god_class_medium_lcom", self.DEFAULT_MEDIUM_LCOM)

        # Pattern-based exclusions (configurable)
        self.use_pattern_exclusions = config.get("use_pattern_exclusions", True)
        self.excluded_patterns = config.get("excluded_patterns", self.DEFAULT_EXCLUDED_PATTERNS)

        # Semantic analysis (graph-based)
        self.use_semantic_analysis = config.get("use_semantic_analysis", True)

        # Community analysis (REPO-152)
        self.use_community_analysis = config.get("use_community_analysis", True)
        # Pass repo_id for multi-tenant filtering
        self.graph_algorithms = GraphAlgorithms(graph_client, repo_id=self.repo_id)

    def detect(self) -> List[Finding]:
        """Find god classes in the codebase.

        A god class is identified by:
        - High number of methods (>20 methods)
        - High total complexity (>100)
        - High coupling (many outgoing calls)
        - Combination of moderate metrics

        Returns:
            List of findings for god classes
        """
        findings: List[Finding] = []

        # Use parameterized query to prevent injection
        # Even though these are class attributes (not user input), parameterization
        # is the correct and safe approach
        # REPO-600: Filter by tenant_id AND repo_id for defense-in-depth isolation
        repo_filter = self._get_isolation_filter("c")
        query = f"""
        MATCH (file:File)-[:CONTAINS]->(c:Class)
        WHERE true {repo_filter}
        WITH c, file
        OPTIONAL MATCH (c)-[:CONTAINS]->(m:Function)
        WITH c, file,
             collect(m) AS methods,
             sum(m.complexity) AS total_complexity,
             COALESCE(c.lineEnd, 0) - COALESCE(c.lineStart, 0) AS loc
        WITH c, file, methods, size(methods) AS method_count, total_complexity, loc
        WHERE method_count >= $medium_method_count OR total_complexity >= $medium_complexity OR loc >= $medium_loc
        UNWIND methods AS m
        OPTIONAL MATCH (m)-[:CALLS]->(called)
        WITH c, file, methods, method_count, total_complexity, loc,
             count(DISTINCT called) AS coupling_count
        RETURN c.qualifiedName AS qualified_name,
               c.name AS name,
               c.filePath AS file_path,
               c.lineStart AS line_start,
               c.lineEnd AS line_end,
               file.filePath AS containing_file,
               method_count,
               total_complexity,
               coupling_count,
               loc,
               c.is_abstract AS is_abstract
        ORDER BY method_count DESC, total_complexity DESC, loc DESC
        LIMIT 50
        """

        results = self.db.execute_query(query, parameters=self._get_query_params(
            medium_method_count=self.medium_method_count,
            medium_complexity=self.medium_complexity,
            medium_loc=self.medium_loc
        ))

        for record in results:
            method_count = record["method_count"] or 0
            total_complexity = record["total_complexity"] or 0
            coupling_count = record["coupling_count"] or 0
            loc = record["loc"] or 0
            is_abstract = record.get("is_abstract", False)

            # Skip abstract base classes (they're often large by design)
            if is_abstract and method_count < 25:
                continue

            name = record["name"]
            qualified_name = record["qualified_name"]

            # Skip test classes (they naturally have many test methods)
            if name.startswith("Test") or name.endswith("Test") or "Test" in name:
                continue

            # Skip legitimate design patterns (if enabled)
            if self.use_pattern_exclusions and self._is_excluded_pattern(name):
                continue

            # Calculate LCOM (Lack of Cohesion of Methods)
            lcom = self._calculate_lcom(qualified_name)

            # Calculate community span (REPO-152)
            # Classes with methods in 1-2 communities are cohesive
            # Classes spanning 3+ communities have scattered responsibilities
            community_span = 1
            if self.use_community_analysis:
                community_span = self._calculate_community_span(qualified_name)

            # Check semantic indicators (if enabled) - enhanced with community analysis
            if self.use_semantic_analysis and self._is_legitimate_pattern_v2(
                qualified_name, lcom, community_span
            ):
                continue

            # Calculate god class score - now includes community span
            is_god_class, reason = self._is_god_class(
                method_count, total_complexity, coupling_count, loc, lcom, community_span
            )

            if not is_god_class:
                continue

            file_path = record["containing_file"] or record["file_path"]
            line_start = record["line_start"]
            line_end = record["line_end"]

            finding_id = str(uuid.uuid4())

            # Calculate importance for severity adjustment (REPO-152)
            importance = 0.5
            if self.use_community_analysis:
                importance = self._calculate_importance(qualified_name)

            severity = self._calculate_severity(
                method_count, total_complexity, coupling_count, loc, lcom,
                community_span, importance
            )

            finding = Finding(
                id=finding_id,
                detector="GodClassDetector",
                severity=severity,
                title=f"God class detected: {name}",
                description=(
                    f"Class '{name}' shows signs of being a god class: {reason}.\n\n"
                    f"Metrics:\n"
                    f"  - Methods: {method_count}\n"
                    f"  - Total complexity: {total_complexity}\n"
                    f"  - Coupling: {coupling_count}\n"
                    f"  - Lines of code: {loc}\n"
                    f"  - Lack of cohesion (LCOM): {lcom:.2f} (0=cohesive, 1=scattered)\n"
                    f"  - Community span: {community_span} (1-2=cohesive, 3+=scattered)\n"
                    f"  - Importance: {importance:.2f} (0=peripheral, 1=core infrastructure)"
                ),
                affected_nodes=[qualified_name],
                affected_files=[file_path],
                graph_context={
                    "type": "god_class",
                    "name": name,
                    "method_count": method_count,
                    "total_complexity": total_complexity,
                    "coupling_count": coupling_count,
                    "loc": loc,
                    "lcom": lcom,
                    "community_span": community_span,
                    "importance": importance,
                    "line_start": line_start,
                    "line_end": line_end,
                },
                suggested_fix=self._suggest_refactoring(
                    name, method_count, total_complexity, coupling_count, loc, lcom,
                    qualified_name=qualified_name
                ),
                estimated_effort=self._estimate_effort(method_count, total_complexity, loc),
                created_at=datetime.now(),
            )

            # Add collaboration metadata for cross-detector communication
            # Build evidence list based on what triggered the detection
            evidence = []
            if lcom >= self.high_lcom:
                evidence.append("high_lcom")
            elif lcom >= self.medium_lcom:
                evidence.append("moderate_lcom")

            if method_count >= self.high_method_count:
                evidence.append("many_methods")

            if total_complexity >= self.high_complexity:
                evidence.append("high_complexity")

            if coupling_count >= 50:
                evidence.append("high_coupling")

            if loc >= self.high_loc:
                evidence.append("large_size")

            # REPO-152: Community span evidence
            if community_span >= 4:
                evidence.append("high_community_span")
            elif community_span >= 3:
                evidence.append("moderate_community_span")

            # Calculate confidence based on number of violations
            # Community span increases confidence significantly
            base_confidence = 0.6 + (len(evidence) * 0.08)
            if community_span >= 3:
                base_confidence += 0.1  # Community analysis confirms god class
            confidence = min(base_confidence, 1.0)

            finding.add_collaboration_metadata(CollaborationMetadata(
                detector="GodClassDetector",
                confidence=confidence,
                evidence=evidence,
                tags=["god_class", "complexity", "root_cause"]
            ))

            # Flag entity in graph for cross-detector collaboration (REPO-151 Phase 2)
            if self.enricher:
                try:
                    self.enricher.flag_entity(
                        entity_qualified_name=qualified_name,
                        detector="GodClassDetector",
                        severity=severity.value,
                        issues=evidence,
                        confidence=confidence,
                        metadata={k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v for k, v in {
                            "method_count": method_count,
                            "total_complexity": total_complexity,
                            "coupling_count": coupling_count,
                            "loc": loc,
                            "lcom": lcom
                        }.items()}
                    )
                except Exception:
                    # Don't fail detection if enrichment fails
                    pass

            findings.append(finding)

        return findings

    def _is_excluded_pattern(self, class_name: str) -> bool:
        """Check if class name matches excluded design patterns.

        Args:
            class_name: Name of the class to check

        Returns:
            True if class matches an excluded pattern, False otherwise
        """
        for pattern in self.excluded_patterns:
            if re.match(pattern, class_name):
                return True
        return False

    def _is_legitimate_pattern(self, qualified_name: str, lcom: float) -> bool:
        """Use graph-based semantic analysis to identify legitimate patterns.

        This method analyzes the class using graph data to identify patterns
        that don't rely on naming conventions. Indicators include:
        - High cohesion (low LCOM) with lifecycle methods (connect/disconnect)
        - Factory/builder pattern (create/build methods)
        - Single-resource focus (all methods use same external dependency)

        Args:
            qualified_name: Qualified name of the class
            lcom: Lack of cohesion metric (0-1)

        Returns:
            True if class matches a legitimate pattern, False otherwise
        """
        # High cohesion is a strong signal - if LCOM < 0.4, check for other indicators
        if lcom >= 0.4:
            return False  # Not cohesive enough to be a legitimate pattern

        # Query graph for method names - use direct CONTAINS relationship
        query = """
        MATCH (c:Class {qualifiedName: $qualified_name})
        MATCH (c)-[:CONTAINS]->(m:Function)
        WITH c, collect(DISTINCT toLower(m.name)) AS method_names
        RETURN method_names, size(method_names) AS method_count
        """

        # Pattern definitions
        lifecycle_methods = {
            'connect', 'disconnect', 'close', 'open',
            'start', 'stop', 'shutdown', 'cleanup',
            '__enter__', '__exit__', '__del__'
        }
        factory_methods = {
            'create', 'build', 'make', 'construct',
            'generate', 'produce', 'assemble'
        }
        orchestrator_methods = {
            'execute', 'run', 'process', 'orchestrate',
            'coordinate', 'manage', 'handle'
        }

        try:
            result = self.db.execute_query(query, {"qualified_name": qualified_name})
            if not result:
                return False

            record = result[0]
            method_names = set(record.get("method_names", []))

            # Check patterns in Python
            has_lifecycle = bool(method_names & lifecycle_methods)
            has_factory = bool(method_names & factory_methods)
            has_orchestrator = bool(method_names & orchestrator_methods)

            # If high cohesion + lifecycle methods = legitimate client pattern
            if has_lifecycle:
                return True

            # If high cohesion + factory methods = legitimate factory/builder pattern
            if has_factory:
                return True

            # If high cohesion + orchestrator methods = legitimate pipeline/engine pattern
            if has_orchestrator:
                return True

            return False

        except Exception:
            # If semantic analysis fails, don't exclude the class
            return False

    def _is_god_class(
        self,
        method_count: int,
        total_complexity: int,
        coupling_count: int,
        loc: int,
        lcom: float,
        community_span: int = 1,
    ) -> tuple[bool, str]:
        """Determine if metrics indicate a god class.

        Uses semantic analysis: high cohesion (low LCOM) and low community span
        protect against god class detection, as they indicate methods work together
        on shared data (legitimate patterns like clients, pipelines, engines).

        REPO-152: Enhanced with community span analysis.
        - Classes with methods in 1-2 communities are cohesive (legitimate)
        - Classes spanning 3+ communities have scattered responsibilities (god class)

        Args:
            method_count: Number of methods
            total_complexity: Sum of all method complexities
            coupling_count: Number of outgoing calls and imports
            loc: Lines of code
            lcom: Lack of cohesion metric (0-1, 0=cohesive, 1=scattered)
            community_span: Number of distinct communities methods span (1-2=cohesive, 3+=scattered)

        Returns:
            Tuple of (is_god_class, reason_description)
        """
        reasons = []

        # REPO-152: Combined cohesion check using LCOM and community span
        # High cohesion: low LCOM AND low community span
        is_cohesive = lcom < 0.4 and community_span <= 2

        # Community span >= 3 is a strong signal of scattered responsibilities
        is_scattered = community_span >= 3

        if method_count >= self.high_method_count:
            reasons.append(f"very high method count ({method_count})")
        elif method_count >= self.medium_method_count:
            reasons.append(f"high method count ({method_count})")

        if total_complexity >= self.high_complexity:
            reasons.append(f"very high complexity ({total_complexity})")
        elif total_complexity >= self.medium_complexity:
            reasons.append(f"high complexity ({total_complexity})")

        if coupling_count >= 50:
            reasons.append(f"very high coupling ({coupling_count})")
        elif coupling_count >= 30:
            reasons.append(f"high coupling ({coupling_count})")

        if loc >= self.high_loc:
            reasons.append(f"very large class ({loc} LOC)")
        elif loc >= self.medium_loc:
            reasons.append(f"large class ({loc} LOC)")

        # LCOM is the KEY semantic indicator of god class
        if lcom >= self.high_lcom:
            reasons.append(f"very low cohesion (LCOM: {lcom:.2f})")
        elif lcom >= self.medium_lcom:
            reasons.append(f"low cohesion (LCOM: {lcom:.2f})")

        # REPO-152: Community span indicator
        if community_span >= 4:
            reasons.append(f"methods span {community_span} communities (scattered)")
        elif community_span >= 3:
            reasons.append(f"methods span {community_span} communities")

        # God class detection with cohesion-aware and community-aware logic:
        # 1. If high cohesion (low LCOM + low community span): require 3+ violations (very strict)
        # 2. If scattered (high community span): 1-2 violations is enough (strong signal)
        # 3. If low cohesion but not scattered: 2 violations is enough (current behavior)
        # 4. Always flag if LCOM is very high OR community span >= 4 (clear god class signal)

        if is_cohesive:
            # High cohesion - legitimate pattern, require 3+ violations or extreme size
            if len(reasons) >= 3:
                return True, ", ".join(reasons) + " (despite high cohesion)"
            elif method_count >= 30 or total_complexity >= 150 or loc >= 1000:
                return True, reasons[0] if reasons else "extremely large class"
            # Otherwise, not a god class - cohesive large classes are OK
            return False, ""

        elif is_scattered:
            # REPO-152: High community span - strong god class signal
            # Even with fewer traditional violations, scattered responsibilities = god class
            if len(reasons) >= 1:
                return True, ", ".join(reasons)
            return False, ""

        else:
            # Low/moderate cohesion, moderate community span - use standard detection
            if len(reasons) >= 2:
                return True, ", ".join(reasons)
            elif lcom >= self.high_lcom:
                # Very low cohesion is itself a strong god class signal
                return True, f"very low cohesion (LCOM: {lcom:.2f})"
            elif method_count >= self.high_method_count:
                return True, reasons[0] if reasons else "high method count"
            elif total_complexity >= self.high_complexity:
                return True, reasons[0] if reasons else "high complexity"
            elif loc >= self.high_loc:
                return True, reasons[0] if reasons else "very large class"

        return False, ""

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity based on metrics.

        Args:
            finding: Finding to assess

        Returns:
            Severity level
        """
        context = finding.graph_context
        method_count = context.get("method_count", 0)
        total_complexity = context.get("total_complexity", 0)
        coupling_count = context.get("coupling_count", 0)
        loc = context.get("loc", 0)
        lcom = context.get("lcom", 0.0)

        return self._calculate_severity(
            method_count, total_complexity, coupling_count, loc, lcom
        )

    def _calculate_severity(
        self,
        method_count: int,
        total_complexity: int,
        coupling_count: int,
        loc: int,
        lcom: float,
        community_span: int = 1,
        importance: float = 0.5,
    ) -> Severity:
        """Calculate severity based on multiple metrics.

        REPO-152: Enhanced with community span and importance scoring.
        - High community span increases severity (scattered responsibilities)
        - High importance decreases severity (core infrastructure, harder to refactor)

        Args:
            method_count: Number of methods
            total_complexity: Total complexity
            coupling_count: Coupling count
            loc: Lines of code
            lcom: Lack of cohesion metric
            community_span: Number of communities methods span (1-2=cohesive, 3+=scattered)
            importance: Class importance score (0=peripheral, 1=core infrastructure)

        Returns:
            Severity level
        """
        # Critical if multiple severe violations
        critical_count = sum([
            method_count >= 30,
            total_complexity >= 150,
            coupling_count >= 70,
            loc >= 1000,
            lcom >= self.high_lcom,
            community_span >= 5,  # REPO-152: Very scattered
        ])

        if critical_count >= 2:
            base_severity = Severity.CRITICAL
        else:
            # High if one critical violation or multiple high violations
            high_count = sum([
                method_count >= self.high_method_count,
                total_complexity >= self.high_complexity,
                coupling_count >= 50,
                loc >= self.high_loc,
                lcom >= self.medium_lcom,
                community_span >= 4,  # REPO-152: Scattered
            ])

            if high_count >= 2:
                base_severity = Severity.HIGH
            else:
                # Medium for moderate violations
                medium_count = sum([
                    method_count >= self.medium_method_count,
                    total_complexity >= self.medium_complexity,
                    coupling_count >= 30,
                    loc >= self.medium_loc,
                    community_span >= 3,  # REPO-152: Moderately scattered
                ])

                if medium_count >= 2:
                    base_severity = Severity.MEDIUM
                else:
                    base_severity = Severity.LOW

        # REPO-152: Adjust severity based on importance
        # High importance = core infrastructure = harder to refactor = downgrade severity
        # Low importance = peripheral code = easier to refactor = keep/upgrade severity
        if importance >= 0.7:
            # Core infrastructure - downgrade severity by one level
            # These classes are heavily used, so changes are risky
            if base_severity == Severity.CRITICAL:
                return Severity.HIGH
            elif base_severity == Severity.HIGH:
                return Severity.MEDIUM
            # Don't downgrade MEDIUM or LOW
            return base_severity
        elif importance <= 0.2 and community_span >= 3:
            # Peripheral code with scattered responsibilities - upgrade severity
            # These are good refactoring candidates
            if base_severity == Severity.LOW:
                return Severity.MEDIUM
            elif base_severity == Severity.MEDIUM:
                return Severity.HIGH

        return base_severity

    def _find_method_clusters(self, qualified_name: str) -> list[dict]:
        """Find method clusters in a class based on shared attribute usage.

        Phase 6 improvement: Query graph to find methods that share attributes,
        suggesting concrete Extract Class opportunities.

        Args:
            qualified_name: Fully qualified class name

        Returns:
            List of clusters with method names and shared attributes
        """
        # Query to find methods that share attribute usage
        query = """
        MATCH (c:Class {qualifiedName: $class_name})-[:CONTAINS]->(m:Function)
        WHERE m.is_method = true
        OPTIONAL MATCH (m)-[:USES]->(a:Attribute)<-[:USES]-(m2:Function)
        WHERE m2.is_method = true AND m2 <> m
        WITH m, collect(DISTINCT a.name) as shared_attrs, collect(DISTINCT m2.name) as related_methods
        WHERE size(related_methods) > 0
        RETURN m.name as method, shared_attrs, related_methods
        ORDER BY size(related_methods) DESC
        LIMIT 10
        """

        try:
            results = self.db.execute_query(query, {"class_name": qualified_name})
            return list(results) if results else []
        except Exception as e:
            logger.debug(f"Method cluster analysis failed for {qualified_name}: {e}")
            return []

    def _suggest_refactoring(
        self,
        name: str,
        method_count: int,
        total_complexity: int,
        coupling_count: int,
        loc: int,
        lcom: float,
        qualified_name: str | None = None,
    ) -> str:
        """Suggest refactoring strategies.

        Phase 6 improvement: Uses graph analysis to suggest specific method
        clusters for extraction when qualified_name is provided.

        Args:
            name: Class name
            method_count: Number of methods
            total_complexity: Total complexity
            coupling_count: Coupling count
            loc: Lines of code
            lcom: Lack of cohesion metric
            qualified_name: Optional fully qualified name for cluster analysis

        Returns:
            Refactoring suggestions
        """
        suggestions = [f"Refactor '{name}' to reduce its responsibilities:\n"]

        # Phase 6: Try to find specific method clusters to extract
        clusters = []
        if qualified_name and method_count >= 10:
            clusters = self._find_method_clusters(qualified_name)

        if clusters:
            # Provide concrete extraction suggestions based on method clusters
            suggestions.append(
                "1. **Suggested Extract Class opportunities** (based on shared attribute usage):\n"
            )
            for i, cluster in enumerate(clusters[:3], 1):
                method = cluster.get("method", "unknown")
                related = cluster.get("related_methods", [])
                shared = cluster.get("shared_attrs", [])
                if related:
                    related_str = ", ".join(related[:4])
                    if len(related) > 4:
                        related_str += f" (+{len(related) - 4} more)"
                    shared_str = ", ".join(shared[:3]) if shared else "state"
                    suggestions.append(
                        f"   {i}a. '{method}' + [{related_str}]\n"
                        f"       → Extract to new class managing: {shared_str}\n"
                    )
        elif method_count >= 20:
            suggestions.append(
                "1. Extract related methods into separate classes\n"
                "   - Look for method groups that work with the same data\n"
                "   - Create focused classes with single responsibilities\n"
            )

        if total_complexity >= 100:
            suggestions.append(
                "2. Simplify complex methods\n"
                "   - Break down complex methods into smaller functions\n"
                "   - Consider using the Strategy or Command pattern\n"
            )

        if coupling_count >= 50:
            suggestions.append(
                "3. Reduce coupling\n"
                "   - Apply dependency injection\n"
                "   - Use interfaces to decouple dependencies\n"
                "   - Consider facade or mediator patterns\n"
            )

        if loc >= self.high_loc:
            suggestions.append(
                f"4. Break down the large class ({loc} LOC)\n"
                f"   - Split into smaller, focused classes\n"
                f"   - Consider using composition over inheritance\n"
                f"   - Extract data classes for complex state\n"
            )

        if lcom >= self.medium_lcom:
            suggestions.append(
                f"5. Improve cohesion (current LCOM: {lcom:.2f})\n"
                f"   - Group methods that use the same fields\n"
                f"   - Extract unrelated methods into separate classes\n"
                f"   - Consider using the Extract Class refactoring\n"
            )

        suggestions.append(
            "\n6. Apply SOLID principles\n"
            "   - Single Responsibility: Each class should have one reason to change\n"
            "   - Open/Closed: Extend behavior without modifying existing code\n"
            "   - Liskov Substitution: Use inheritance properly\n"
            "   - Interface Segregation: Create specific interfaces\n"
            "   - Dependency Inversion: Depend on abstractions"
        )

        return "".join(suggestions)

    def _estimate_effort(
        self, method_count: int, total_complexity: int, loc: int
    ) -> str:
        """Estimate refactoring effort.

        Args:
            method_count: Number of methods
            total_complexity: Total complexity
            loc: Lines of code

        Returns:
            Effort estimate
        """
        if method_count >= 30 or total_complexity >= 150 or loc >= 1000:
            return "Large (1-2 weeks)"
        elif method_count >= 20 or total_complexity >= 100 or loc >= 500:
            return "Medium (3-5 days)"
        else:
            return "Small (1-2 days)"

    def _calculate_lcom(self, qualified_name: str) -> float:
        """Calculate Lack of Cohesion of Methods (LCOM) metric.

        LCOM measures how well methods in a class work together. A value near 0
        indicates high cohesion (methods share fields), while a value near 1
        indicates low cohesion (methods work independently).

        This implements a simplified LCOM metric based on method-field relationships.

        Args:
            qualified_name: Qualified name of the class

        Returns:
            LCOM score between 0 (cohesive) and 1 (scattered)
        """
        # Query to get method-field usage patterns - use direct CONTAINS relationship
        query = """
        MATCH (c:Class {qualifiedName: $qualified_name})
        MATCH (c)-[:CONTAINS]->(m:Function)
        OPTIONAL MATCH (m)-[:USES]->(field)
        WITH m, collect(DISTINCT field.name) AS fields
        RETURN collect({method: m.name, fields: fields}) AS method_field_pairs,
               count(m) AS method_count
        """

        try:
            result = self.db.execute_query(query, {"qualified_name": qualified_name})
            if not result:
                return 0.0

            record = result[0]
            method_field_pairs = record.get("method_field_pairs", [])
            method_count = record.get("method_count", 0)

            if method_count <= 1:
                return 0.0  # Single method is perfectly cohesive

            # Try to use Rust implementation for better performance
            try:
                from repotoire_fast import calculate_lcom_fast

                # Convert Neo4j result format to Rust format: [(method_name, [field_names])]
                rust_pairs = [
                    (pair.get("method", ""), pair.get("fields", []))
                    for pair in method_field_pairs
                ]
                return calculate_lcom_fast(rust_pairs)
            except ImportError:
                pass  # Fall back to Python implementation

            # Python fallback: Count pairs of methods that share no fields
            non_sharing_pairs = 0
            total_pairs = 0

            for i, pair1 in enumerate(method_field_pairs):
                fields1 = set(pair1.get("fields", []))
                for pair2 in method_field_pairs[i + 1 :]:
                    fields2 = set(pair2.get("fields", []))
                    total_pairs += 1

                    # If methods share no fields, they lack cohesion
                    if not fields1.intersection(fields2):
                        non_sharing_pairs += 1

            if total_pairs == 0:
                return 0.0

            # Return ratio of non-sharing pairs (0 = cohesive, 1 = scattered)
            return non_sharing_pairs / total_pairs

        except Exception:
            # If LCOM calculation fails, return neutral value
            return 0.5

    # -------------------------------------------------------------------------
    # REPO-152: Community Detection and PageRank Integration
    # -------------------------------------------------------------------------

    def _calculate_community_span(self, qualified_name: str) -> int:
        """Calculate how many distinct communities a class's methods span.

        Uses Neo4j GDS Louvain community detection to identify method clusters.
        Classes with methods in 1-2 communities are cohesive (legitimate patterns).
        Classes spanning 3+ communities have scattered responsibilities (god classes).

        Args:
            qualified_name: Qualified name of the class

        Returns:
            Number of distinct communities (1 = cohesive, 3+ = scattered)
        """
        return self.graph_algorithms.get_class_community_span(qualified_name)

    def _calculate_importance(self, qualified_name: str) -> float:
        """Calculate the importance score of a class based on PageRank.

        High importance (many callers) suggests core infrastructure that should
        be handled carefully. Low importance suggests peripheral code that's
        easier to refactor.

        Args:
            qualified_name: Qualified name of the class

        Returns:
            Importance score (0.0 = peripheral, 1.0 = core infrastructure)
        """
        return self.graph_algorithms.get_class_importance(qualified_name)

    def _is_legitimate_pattern_v2(
        self, qualified_name: str, lcom: float, community_span: int
    ) -> bool:
        """Enhanced pattern detection combining LCOM with community analysis.

        REPO-152: This improves on _is_legitimate_pattern by using community
        structure to identify cohesive classes. Research shows that combining
        LCOM with community span achieves 95% accuracy in distinguishing
        legitimate patterns from god classes.

        Detection logic:
        - LCOM < 0.4 AND community_span <= 2: Legitimate pattern (cohesive)
        - LCOM >= 0.4 OR community_span > 3: Potential god class (scattered)

        Args:
            qualified_name: Qualified name of the class
            lcom: Lack of cohesion metric (0=cohesive, 1=scattered)
            community_span: Number of communities methods span

        Returns:
            True if class matches a legitimate pattern, False otherwise
        """
        # REPO-152: Combined cohesion check
        # Must have BOTH low LCOM AND low community span to be legitimate
        if lcom < 0.4 and community_span <= 2:
            # Additional semantic check for known patterns
            if self._is_legitimate_pattern(qualified_name, lcom):
                return True

            # Even without semantic patterns, low LCOM + low community span
            # strongly suggests a cohesive, legitimate class
            # But still check if it has extreme metrics
            return self._has_normal_metrics(qualified_name)

        # High LCOM or high community span = not a legitimate pattern
        return False

    def _has_normal_metrics(self, qualified_name: str) -> bool:
        """Check if a class has normal (non-extreme) metrics.

        Used as a secondary check for classes that pass cohesion tests
        but might still be problematic due to extreme size/complexity.

        Args:
            qualified_name: Qualified name of the class

        Returns:
            True if metrics are within normal bounds
        """
        try:
            query = """
            MATCH (c:Class {qualifiedName: $qualified_name})
            OPTIONAL MATCH (c)-[:CONTAINS]->(m:Function)
            WITH c, count(m) AS method_count, sum(m.complexity) AS total_complexity
            RETURN method_count,
                   total_complexity,
                   COALESCE(c.lineEnd, 0) - COALESCE(c.lineStart, 0) AS loc
            """
            result = self.db.execute_query(query, {"qualified_name": qualified_name})

            if not result:
                return True  # Unknown = assume normal

            record = result[0]
            method_count = record.get("method_count", 0) or 0
            total_complexity = record.get("total_complexity", 0) or 0
            loc = record.get("loc", 0) or 0

            # Extreme thresholds - even cohesive classes with these are problematic
            if method_count >= 40:
                return False
            if total_complexity >= 200:
                return False
            if loc >= 1500:
                return False

            return True

        except Exception:
            return True  # Error = assume normal

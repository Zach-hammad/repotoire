"""Architectural bottleneck detector using betweenness centrality (REPO-200).

Identifies functions that sit on many execution paths (high betweenness),
indicating architectural bottlenecks that are critical points of failure.

Uses Rust Brandes algorithm for betweenness centrality - no GDS plugin required.

REPO-154: Enhanced with cross-detector risk amplification to escalate
severity when bottlenecks combine with high complexity and security issues.

REPO-200: Updated to use Rust algorithms directly (no GDS dependency).
"""

import json
from typing import Dict, List, Optional

from repotoire.detectors.base import CodeSmellDetector
from repotoire.detectors.graph_algorithms import GraphAlgorithms
from repotoire.detectors.risk_analyzer import BottleneckRiskAnalyzer, RiskAssessment
from repotoire.graph.client import Neo4jClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.logging_config import get_logger
from repotoire.models import CollaborationMetadata, Finding, Severity

logger = get_logger(__name__)


class ArchitecturalBottleneckDetector(CodeSmellDetector):
    """Detects architectural bottlenecks using betweenness centrality.

    Functions with high betweenness centrality appear on many shortest paths
    between other functions, making them critical architectural components.
    Changes to these functions have high blast radius.
    """

    def __init__(self, neo4j_client: Neo4jClient, enricher: Optional[GraphEnricher] = None):
        """Initialize detector with Neo4j client.

        Args:
            neo4j_client: Neo4j database client
        """
        super().__init__(neo4j_client)
        self.enricher = enricher

        # Thresholds for betweenness centrality
        # These are relative to the graph size - will be adjusted dynamically
        self.high_betweenness_percentile = 0.95  # Top 5%
        self.critical_betweenness_percentile = 0.99  # Top 1%

        # Complexity thresholds for severity escalation
        self.high_complexity_threshold = 20

        # REPO-154: Store risk assessments for reporting
        self._last_risk_assessments: List[RiskAssessment] = []

    @property
    def needs_previous_findings(self) -> bool:
        """ArchitecturalBottleneckDetector needs RadonDetector and BanditDetector findings.

        REPO-154: When bottlenecks combine with high complexity (Radon) and
        security issues (Bandit), severity is escalated for compound risk factors.
        """
        return True

    def detect(
        self,
        previous_findings: Optional[List[Finding]] = None,
    ) -> List[Finding]:
        """Detect architectural bottlenecks using betweenness centrality.

        Uses Rust Brandes algorithm - no GDS plugin required.

        REPO-154: Now supports cross-detector risk amplification via
        previous_findings parameter. When radon/bandit findings are passed,
        severity is escalated for bottlenecks with compound risk factors.

        Args:
            previous_findings: Optional findings from other detectors
                (RadonDetector, BanditDetector) for risk correlation

        Returns:
            List of findings for high-betweenness functions
        """
        findings = []
        previous_findings = previous_findings or []

        # Separate previous findings by type for risk analysis
        radon_findings = [
            f for f in previous_findings
            if "radon" in f.detector.lower() or "complexity" in f.detector.lower()
        ]
        bandit_findings = [
            f for f in previous_findings
            if "bandit" in f.detector.lower() or "security" in f.detector.lower()
        ]
        other_findings = [
            f for f in previous_findings
            if f not in radon_findings and f not in bandit_findings
        ]

        # Initialize graph algorithms (uses Rust - no GDS required)
        graph_algo = GraphAlgorithms(self.db)

        try:
            # Calculate betweenness centrality using Rust Brandes algorithm
            logger.info("Calculating betweenness centrality using Rust algorithm")
            result = graph_algo.calculate_betweenness_centrality()
            if not result:
                logger.error("Failed to calculate betweenness centrality")
                return findings

            # Get betweenness statistics to determine thresholds
            stats = graph_algo.get_betweenness_statistics()
            if not stats:
                logger.error("Failed to get betweenness statistics")
                return findings

            total_functions = stats['total_functions']
            max_betweenness = stats.get('max_betweenness') or 0
            avg_betweenness = stats.get('avg_betweenness') or 0
            stdev_betweenness = stats.get('stdev_betweenness') or 0

            logger.info(
                f"Betweenness statistics: "
                f"max={max_betweenness:.4f}, "
                f"avg={avg_betweenness:.4f}, "
                f"stdev={stdev_betweenness:.4f}"
            )

            # Calculate dynamic thresholds
            # High: mean + 2*stdev (roughly top 5% in normal distribution)
            # Critical: mean + 3*stdev (roughly top 1%)
            high_threshold = avg_betweenness + (2 * stdev_betweenness) if stdev_betweenness else avg_betweenness * 2
            critical_threshold = avg_betweenness + (3 * stdev_betweenness) if stdev_betweenness else avg_betweenness * 3

            # Get high-betweenness functions
            bottlenecks = graph_algo.get_high_betweenness_functions(
                threshold=high_threshold,
                limit=100
            )

            logger.info(f"Found {len(bottlenecks)} architectural bottlenecks")

            # Create findings
            for bottleneck in bottlenecks:
                qualified_name = bottleneck['qualified_name']
                betweenness = bottleneck['betweenness']
                complexity = bottleneck.get('complexity', 0)
                file_path = bottleneck.get('file_path', 'unknown')
                line_number = bottleneck.get('line_number', 0)

                # Determine severity
                if betweenness >= critical_threshold:
                    # Critical bottleneck
                    if complexity > self.high_complexity_threshold:
                        severity = Severity.CRITICAL
                        title = f"Critical architectural bottleneck with high complexity: {qualified_name}"
                    else:
                        severity = Severity.HIGH
                        title = f"Critical architectural bottleneck: {qualified_name}"
                else:
                    # High bottleneck
                    if complexity > self.high_complexity_threshold:
                        severity = Severity.HIGH
                        title = f"Architectural bottleneck with high complexity: {qualified_name}"
                    else:
                        severity = Severity.MEDIUM
                        title = f"Architectural bottleneck: {qualified_name}"

                # Calculate percentile for description
                percentile = (betweenness / max_betweenness) * 100 if max_betweenness > 0 else 0

                description = (
                    f"Function '{qualified_name}' has high betweenness centrality "
                    f"(score: {betweenness:.4f}, {percentile:.1f}th percentile). "
                    f"This indicates it sits on many execution paths between other functions, "
                    f"making it a critical architectural component.\n\n"
                    f"**Risk factors:**\n"
                    f"- High blast radius: Changes here affect many code paths\n"
                    f"- Single point of failure: If this breaks, cascading failures likely\n"
                    f"- Refactoring risk: Difficult to change safely\n"
                )

                if complexity > self.high_complexity_threshold:
                    description += f"\n**Additional concern:** High complexity ({complexity}) makes this bottleneck especially risky."

                suggested_fix = (
                    f"**Immediate actions:**\n"
                    f"1. Ensure comprehensive test coverage for '{qualified_name}'\n"
                    f"2. Add defensive error handling and logging\n"
                    f"3. Consider circuit breaker pattern for failure isolation\n\n"
                    f"**Long-term refactoring:**\n"
                    f"1. Analyze why so many paths flow through this function\n"
                    f"2. Consider splitting into multiple specialized functions\n"
                    f"3. Introduce abstraction layers to reduce coupling\n"
                    f"4. Evaluate if functionality can be distributed\n\n"
                    f"**Monitoring:**\n"
                    f"- Add performance monitoring (this is a hot path)\n"
                    f"- Track error rates (failures here cascade)\n"
                    f"- Alert on anomalies"
                )

                # Estimate effort based on severity - bottlenecks require careful refactoring
                if severity == Severity.CRITICAL:
                    estimated_effort = "Large (1-2 days)"
                elif severity == Severity.HIGH:
                    estimated_effort = "Large (4-8 hours)"
                else:
                    estimated_effort = "Medium (2-4 hours)"

                finding = Finding(
                    id=f"architectural_bottleneck_{qualified_name}",
                    detector="ArchitecturalBottleneckDetector",
                    severity=severity,
                    title=title,
                    description=description,
                    affected_nodes=[qualified_name],
                    affected_files=[file_path],
                    suggested_fix=suggested_fix,
                    estimated_effort=estimated_effort,
                    graph_context={
                        "betweenness_score": betweenness,
                        "complexity": complexity,
                        "percentile": percentile,
                        "line_number": line_number,
                        "avg_betweenness": avg_betweenness,
                        "max_betweenness": max_betweenness,
                    }
                )
                # Add collaboration metadata (REPO-150 Phase 1)
                finding.add_collaboration_metadata(CollaborationMetadata(
                    detector="ArchitecturalBottleneckDetector",
                    confidence=0.9,
                    evidence=['high_betweenness'],
                    tags=['bottleneck', 'architecture', 'performance']
                ))

                # Flag entity in graph for cross-detector collaboration (REPO-151 Phase 2)
                if self.enricher and finding.affected_nodes:
                    for entity_qname in finding.affected_nodes:
                        try:
                            self.enricher.flag_entity(
                                entity_qualified_name=entity_qname,
                                detector="ArchitecturalBottleneckDetector",
                                severity=finding.severity.value,
                                issues=['high_betweenness'],
                                confidence=0.9,
                                metadata={k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v) for k, v in (finding.graph_context or {}).items()}
                            )
                        except Exception:
                            pass

                findings.append(finding)

            # REPO-154: Apply cross-detector risk amplification
            if radon_findings or bandit_findings:
                logger.info(
                    f"Applying risk amplification with {len(radon_findings)} complexity "
                    f"and {len(bandit_findings)} security findings"
                )
                risk_analyzer = BottleneckRiskAnalyzer(
                    complexity_threshold=self.high_complexity_threshold,
                )
                findings, risk_assessments = risk_analyzer.analyze(
                    bottleneck_findings=findings,
                    radon_findings=radon_findings,
                    bandit_findings=bandit_findings,
                    other_findings=other_findings,
                )

                # Log risk escalations
                critical_risks = [a for a in risk_assessments if a.is_critical_risk]
                if critical_risks:
                    logger.warning(
                        f"Found {len(critical_risks)} critical compound risks "
                        "(bottleneck + complexity + security)"
                    )

                # Store assessments in instance for reporting
                self._last_risk_assessments = risk_assessments

            return findings

        except Exception as e:
            logger.error(f"Error in architectural bottleneck detection: {e}", exc_info=True)
            return findings

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity based on betweenness score and complexity.

        Args:
            finding: Finding to assess

        Returns:
            Severity level (already set in detect(), just return it)
        """
        # Severity is already calculated in detect() method
        # This method is required by base class but not used
        return finding.severity

    def get_risk_assessments(self) -> List[RiskAssessment]:
        """Get risk assessments from the last detection run.

        REPO-154: Returns detailed risk assessments including compound
        risk factors and mitigation plans.

        Returns:
            List of RiskAssessment objects from last detect() call
        """
        return self._last_risk_assessments

    def get_critical_risks(self) -> List[RiskAssessment]:
        """Get only critical compound risks from the last detection run.

        Returns:
            List of RiskAssessment objects where is_critical_risk is True
        """
        return [a for a in self._last_risk_assessments if a.is_critical_risk]

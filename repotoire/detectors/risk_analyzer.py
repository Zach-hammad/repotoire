"""Risk Analyzer for cross-detector risk amplification (REPO-154).

Analyzes findings from multiple detectors to identify compound risk factors
and escalate severity when architectural bottlenecks combine with complexity
and security issues.

Risk Matrix:
- Bottleneck alone: Original severity
- Bottleneck + High Complexity: +1 severity level
- Bottleneck + Security Issue: +1 severity level
- Bottleneck + High Complexity + Security: CRITICAL

The "Complexity Amplifier" pattern: High-centrality nodes with high complexity
and security vulnerabilities represent critical risk that requires immediate
attention.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from repotoire.models import CollaborationMetadata, Finding, Severity


@dataclass
class RiskFactor:
    """Represents a risk factor from another detector."""

    factor_type: str  # "complexity", "security", "dead_code", etc.
    detector: str  # Source detector name
    severity: Severity
    confidence: float
    evidence: List[str] = field(default_factory=list)
    finding_id: Optional[str] = None


@dataclass
class RiskAssessment:
    """Complete risk assessment for an entity."""

    entity: str  # Qualified name or file path
    risk_factors: List[RiskFactor] = field(default_factory=list)
    original_severity: Optional[Severity] = None
    escalated_severity: Optional[Severity] = None
    risk_score: float = 0.0  # 0.0 to 1.0
    mitigation_plan: List[str] = field(default_factory=list)

    @property
    def is_critical_risk(self) -> bool:
        """Check if this represents critical compound risk."""
        return len(self.risk_factors) >= 2 and self.escalated_severity == Severity.CRITICAL

    @property
    def factor_types(self) -> Set[str]:
        """Get set of risk factor types."""
        return {rf.factor_type for rf in self.risk_factors}


class BottleneckRiskAnalyzer:
    """Analyzes bottleneck findings for compound risk factors.

    Correlates findings from:
    - ArchitecturalBottleneckDetector (centrality, coupling)
    - RadonDetector (complexity metrics)
    - BanditDetector (security vulnerabilities)

    And escalates severity when multiple risk factors combine.
    """

    # Risk factor weights for scoring
    RISK_WEIGHTS = {
        "bottleneck": 0.4,
        "high_complexity": 0.3,
        "security_vulnerability": 0.3,
        "dead_code": 0.1,
    }

    # Severity escalation matrix
    SEVERITY_ORDER = [Severity.INFO, Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]

    def __init__(
        self,
        complexity_threshold: int = 15,
        security_severity_threshold: Severity = Severity.MEDIUM,
    ):
        """Initialize the risk analyzer.

        Args:
            complexity_threshold: Minimum cyclomatic complexity to consider high
            security_severity_threshold: Minimum security severity to consider
        """
        self.complexity_threshold = complexity_threshold
        self.security_severity_threshold = security_severity_threshold

    def analyze(
        self,
        bottleneck_findings: List[Finding],
        radon_findings: Optional[List[Finding]] = None,
        bandit_findings: Optional[List[Finding]] = None,
        other_findings: Optional[List[Finding]] = None,
    ) -> Tuple[List[Finding], List[RiskAssessment]]:
        """Analyze bottleneck findings for compound risk factors.

        Args:
            bottleneck_findings: Findings from ArchitecturalBottleneckDetector
            radon_findings: Findings from RadonDetector (complexity)
            bandit_findings: Findings from BanditDetector (security)
            other_findings: Additional findings for correlation

        Returns:
            Tuple of (modified bottleneck findings, risk assessments)
        """
        radon_findings = radon_findings or []
        bandit_findings = bandit_findings or []
        other_findings = other_findings or []

        # Index findings by affected entities for fast lookup
        complexity_by_entity = self._index_by_entity(radon_findings)
        security_by_entity = self._index_by_entity(bandit_findings)
        other_by_entity = self._index_by_entity(other_findings)

        assessments: List[RiskAssessment] = []
        modified_findings: List[Finding] = []

        for finding in bottleneck_findings:
            assessment = self._assess_bottleneck_risk(
                finding,
                complexity_by_entity,
                security_by_entity,
                other_by_entity,
            )
            assessments.append(assessment)

            # Apply risk escalation to finding
            modified_finding = self._apply_risk_escalation(finding, assessment)
            modified_findings.append(modified_finding)

        return modified_findings, assessments

    def _index_by_entity(self, findings: List[Finding]) -> Dict[str, List[Finding]]:
        """Index findings by their affected entities (nodes and files).

        Args:
            findings: List of findings to index

        Returns:
            Dict mapping entity names to their findings
        """
        index: Dict[str, List[Finding]] = {}

        for finding in findings:
            # Index by affected nodes (qualified names)
            for node in finding.affected_nodes:
                if node not in index:
                    index[node] = []
                index[node].append(finding)

            # Index by affected files
            for file_path in finding.affected_files:
                if file_path not in index:
                    index[file_path] = []
                index[file_path].append(finding)

                # Also index by filename without path for broader matching
                filename = file_path.split("/")[-1] if "/" in file_path else file_path
                if filename not in index:
                    index[filename] = []
                index[filename].append(finding)

        return index

    def _assess_bottleneck_risk(
        self,
        bottleneck: Finding,
        complexity_index: Dict[str, List[Finding]],
        security_index: Dict[str, List[Finding]],
        other_index: Dict[str, List[Finding]],
    ) -> RiskAssessment:
        """Assess compound risk for a bottleneck finding.

        Args:
            bottleneck: The bottleneck finding to assess
            complexity_index: Index of complexity findings
            security_index: Index of security findings
            other_index: Index of other findings

        Returns:
            RiskAssessment with identified risk factors
        """
        assessment = RiskAssessment(
            entity=bottleneck.affected_nodes[0] if bottleneck.affected_nodes else "",
            original_severity=bottleneck.severity,
        )

        # Add bottleneck as base risk factor
        bottleneck_factor = RiskFactor(
            factor_type="bottleneck",
            detector="ArchitecturalBottleneckDetector",
            severity=bottleneck.severity,
            confidence=bottleneck.graph_context.get("confidence", 0.8),
            evidence=self._extract_bottleneck_evidence(bottleneck),
            finding_id=bottleneck.id,
        )
        assessment.risk_factors.append(bottleneck_factor)

        # Check for complexity risk factors
        complexity_factors = self._find_complexity_factors(bottleneck, complexity_index)
        assessment.risk_factors.extend(complexity_factors)

        # Check for security risk factors
        security_factors = self._find_security_factors(bottleneck, security_index)
        assessment.risk_factors.extend(security_factors)

        # Check for other risk factors (dead code, etc.)
        other_factors = self._find_other_factors(bottleneck, other_index)
        assessment.risk_factors.extend(other_factors)

        # Calculate risk score
        assessment.risk_score = self._calculate_risk_score(assessment.risk_factors)

        # Determine escalated severity
        assessment.escalated_severity = self._calculate_escalated_severity(assessment)

        # Generate mitigation plan
        assessment.mitigation_plan = self._generate_mitigation_plan(assessment)

        return assessment

    def _extract_bottleneck_evidence(self, finding: Finding) -> List[str]:
        """Extract evidence strings from bottleneck finding."""
        evidence = []
        ctx = finding.graph_context

        if "centrality" in ctx:
            evidence.append(f"betweenness_centrality={ctx['centrality']:.3f}")
        if "afferent_coupling" in ctx:
            evidence.append(f"afferent_coupling={ctx['afferent_coupling']}")
        if "efferent_coupling" in ctx:
            evidence.append(f"efferent_coupling={ctx['efferent_coupling']}")
        if "complexity" in ctx:
            evidence.append(f"complexity={ctx['complexity']}")

        return evidence

    def _find_complexity_factors(
        self,
        bottleneck: Finding,
        complexity_index: Dict[str, List[Finding]],
    ) -> List[RiskFactor]:
        """Find complexity risk factors related to the bottleneck.

        Args:
            bottleneck: The bottleneck finding
            complexity_index: Index of complexity findings

        Returns:
            List of complexity risk factors
        """
        factors = []

        # Check all entities associated with the bottleneck
        entities_to_check = set(bottleneck.affected_nodes + bottleneck.affected_files)

        for entity in entities_to_check:
            if entity in complexity_index:
                for complexity_finding in complexity_index[entity]:
                    # Check if complexity exceeds threshold
                    complexity_value = complexity_finding.graph_context.get("complexity", 0)
                    if complexity_value >= self.complexity_threshold:
                        factor = RiskFactor(
                            factor_type="high_complexity",
                            detector="RadonDetector",
                            severity=complexity_finding.severity,
                            confidence=0.95,  # Radon is highly accurate
                            evidence=[
                                f"cyclomatic_complexity={complexity_value}",
                                f"rank={complexity_finding.graph_context.get('rank', 'unknown')}",
                            ],
                            finding_id=complexity_finding.id,
                        )
                        factors.append(factor)
                        break  # One complexity factor per bottleneck is enough

        return factors

    def _find_security_factors(
        self,
        bottleneck: Finding,
        security_index: Dict[str, List[Finding]],
    ) -> List[RiskFactor]:
        """Find security risk factors related to the bottleneck.

        Args:
            bottleneck: The bottleneck finding
            security_index: Index of security findings

        Returns:
            List of security risk factors
        """
        factors = []

        entities_to_check = set(bottleneck.affected_nodes + bottleneck.affected_files)

        for entity in entities_to_check:
            if entity in security_index:
                for security_finding in security_index[entity]:
                    # Check if severity meets threshold
                    if self._severity_meets_threshold(
                        security_finding.severity,
                        self.security_severity_threshold,
                    ):
                        # Extract security-specific evidence
                        evidence = []
                        if "test_id" in security_finding.graph_context:
                            evidence.append(f"test_id={security_finding.graph_context['test_id']}")
                        if "issue_text" in security_finding.graph_context:
                            evidence.append(security_finding.graph_context["issue_text"][:100])

                        factor = RiskFactor(
                            factor_type="security_vulnerability",
                            detector="BanditDetector",
                            severity=security_finding.severity,
                            confidence=security_finding.graph_context.get("confidence", 0.8),
                            evidence=evidence,
                            finding_id=security_finding.id,
                        )
                        factors.append(factor)

        return factors

    def _find_other_factors(
        self,
        bottleneck: Finding,
        other_index: Dict[str, List[Finding]],
    ) -> List[RiskFactor]:
        """Find other risk factors (dead code, etc.) related to the bottleneck.

        Args:
            bottleneck: The bottleneck finding
            other_index: Index of other findings

        Returns:
            List of other risk factors
        """
        factors = []

        entities_to_check = set(bottleneck.affected_nodes + bottleneck.affected_files)

        for entity in entities_to_check:
            if entity in other_index:
                for other_finding in other_index[entity]:
                    # Determine factor type from detector name
                    factor_type = self._determine_factor_type(other_finding.detector)

                    factor = RiskFactor(
                        factor_type=factor_type,
                        detector=other_finding.detector,
                        severity=other_finding.severity,
                        confidence=other_finding.graph_context.get("confidence", 0.7),
                        evidence=[f"from_{other_finding.detector}"],
                        finding_id=other_finding.id,
                    )
                    factors.append(factor)

        return factors

    def _determine_factor_type(self, detector_name: str) -> str:
        """Determine risk factor type from detector name."""
        detector_lower = detector_name.lower()
        if "dead" in detector_lower or "vulture" in detector_lower:
            return "dead_code"
        elif "complexity" in detector_lower or "radon" in detector_lower:
            return "high_complexity"
        elif "security" in detector_lower or "bandit" in detector_lower:
            return "security_vulnerability"
        else:
            return "other"

    def _severity_meets_threshold(self, severity: Severity, threshold: Severity) -> bool:
        """Check if severity meets or exceeds threshold."""
        return self.SEVERITY_ORDER.index(severity) >= self.SEVERITY_ORDER.index(threshold)

    def _calculate_risk_score(self, factors: List[RiskFactor]) -> float:
        """Calculate overall risk score from factors.

        Args:
            factors: List of risk factors

        Returns:
            Risk score between 0.0 and 1.0
        """
        if not factors:
            return 0.0

        score = 0.0
        for factor in factors:
            weight = self.RISK_WEIGHTS.get(factor.factor_type, 0.1)
            severity_multiplier = (self.SEVERITY_ORDER.index(factor.severity) + 1) / len(
                self.SEVERITY_ORDER
            )
            score += weight * severity_multiplier * factor.confidence

        # Normalize to 0-1 range
        return min(1.0, score)

    def _calculate_escalated_severity(self, assessment: RiskAssessment) -> Severity:
        """Calculate escalated severity based on risk factors.

        Risk Matrix:
        - 1 factor (bottleneck only): Original severity
        - 2 factors: +1 severity level
        - 3+ factors: CRITICAL
        """
        original_idx = self.SEVERITY_ORDER.index(assessment.original_severity)
        factor_count = len(assessment.risk_factors)

        # Get unique factor types (excluding base bottleneck)
        additional_factors = len(assessment.factor_types) - 1  # Subtract bottleneck

        if additional_factors >= 2:
            # 3+ different factor types -> CRITICAL
            return Severity.CRITICAL
        elif additional_factors == 1:
            # 2 factor types -> escalate by 1
            new_idx = min(original_idx + 1, len(self.SEVERITY_ORDER) - 1)
            return self.SEVERITY_ORDER[new_idx]
        else:
            # Bottleneck only -> keep original
            return assessment.original_severity

    def _generate_mitigation_plan(self, assessment: RiskAssessment) -> List[str]:
        """Generate prioritized mitigation plan based on risk factors.

        Args:
            assessment: The risk assessment

        Returns:
            List of mitigation steps in priority order
        """
        plan = []
        factor_types = assessment.factor_types

        # Priority 1: Security vulnerabilities (most urgent)
        if "security_vulnerability" in factor_types:
            plan.append(
                "1. [URGENT] Address security vulnerabilities first - "
                "review and fix identified security issues before other changes"
            )

        # Priority 2: Reduce bottleneck impact
        if "bottleneck" in factor_types:
            plan.append(
                "2. Reduce architectural coupling - consider extracting "
                "interfaces or introducing dependency injection"
            )

        # Priority 3: Simplify complexity
        if "high_complexity" in factor_types:
            plan.append(
                "3. Reduce cyclomatic complexity - break down complex methods "
                "into smaller, focused functions"
            )

        # Priority 4: Clean up dead code
        if "dead_code" in factor_types:
            plan.append(
                "4. Remove dead code - eliminate unused functions and classes "
                "to reduce maintenance burden"
            )

        # Add compound risk warning if critical
        if assessment.is_critical_risk:
            plan.insert(
                0,
                "!!! CRITICAL COMPOUND RISK: Multiple risk factors combine "
                "to create systemic risk. Address all factors together.",
            )

        return plan

    def _apply_risk_escalation(
        self,
        finding: Finding,
        assessment: RiskAssessment,
    ) -> Finding:
        """Apply risk escalation to a finding based on assessment.

        Args:
            finding: Original bottleneck finding
            assessment: Risk assessment

        Returns:
            Modified finding with escalated severity and metadata
        """
        # Update severity if escalated
        if assessment.escalated_severity != assessment.original_severity:
            finding.severity = assessment.escalated_severity

        # Add risk analysis to graph context
        finding.graph_context["risk_score"] = assessment.risk_score
        finding.graph_context["risk_factors"] = [rf.factor_type for rf in assessment.risk_factors]
        finding.graph_context["is_compound_risk"] = len(assessment.risk_factors) >= 2
        finding.graph_context["original_severity"] = assessment.original_severity.value

        # Add collaboration metadata for each contributing detector
        for factor in assessment.risk_factors:
            if factor.detector != "ArchitecturalBottleneckDetector":
                finding.add_collaboration_metadata(
                    CollaborationMetadata(
                        detector=factor.detector,
                        confidence=factor.confidence,
                        evidence=factor.evidence,
                        tags=[factor.factor_type, "risk_amplification"],
                        related_findings=[factor.finding_id] if factor.finding_id else [],
                    )
                )

        # Update description if escalated
        if assessment.is_critical_risk:
            finding.description = (
                f"**CRITICAL COMPOUND RISK**: {finding.description}\n\n"
                f"Risk factors: {', '.join(assessment.factor_types)}\n"
                f"Risk score: {assessment.risk_score:.2f}"
            )

        # Update suggested fix with mitigation plan
        if assessment.mitigation_plan:
            finding.suggested_fix = "\n".join(assessment.mitigation_plan)

        return finding


def analyze_compound_risks(
    all_findings: List[Finding],
    complexity_threshold: int = 15,
    security_severity_threshold: Severity = Severity.MEDIUM,
) -> Tuple[List[Finding], List[RiskAssessment]]:
    """Convenience function to analyze compound risks from mixed findings.

    Args:
        all_findings: All findings from various detectors
        complexity_threshold: Minimum complexity for risk factor
        security_severity_threshold: Minimum security severity for risk factor

    Returns:
        Tuple of (modified findings, risk assessments)
    """
    # Separate findings by detector type
    bottleneck_findings = []
    radon_findings = []
    bandit_findings = []
    other_findings = []

    for finding in all_findings:
        detector_lower = finding.detector.lower()
        if "bottleneck" in detector_lower or "centrality" in detector_lower:
            bottleneck_findings.append(finding)
        elif "radon" in detector_lower or "complexity" in detector_lower:
            radon_findings.append(finding)
        elif "bandit" in detector_lower or "security" in detector_lower:
            bandit_findings.append(finding)
        else:
            other_findings.append(finding)

    # Run analysis
    analyzer = BottleneckRiskAnalyzer(
        complexity_threshold=complexity_threshold,
        security_severity_threshold=security_severity_threshold,
    )

    return analyzer.analyze(
        bottleneck_findings,
        radon_findings,
        bandit_findings,
        other_findings,
    )

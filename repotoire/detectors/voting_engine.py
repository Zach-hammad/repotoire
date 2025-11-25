"""Voting and Consensus Engine for Multi-Detector Validation.

REPO-156: Aggregates findings from multiple detectors to determine consensus
and confidence scores using configurable voting strategies.

Voting Strategies:
    - MAJORITY: 2/3+ detectors agree = consensus
    - WEIGHTED: Detectors have different weights based on accuracy
    - THRESHOLD: Only include findings above confidence threshold
    - UNANIMOUS: All detectors must agree

Confidence Scoring:
    - Simple average
    - Weighted average (by detector accuracy)
    - Bayesian (prior + evidence)
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple
from collections import defaultdict

from repotoire.models import Finding, Severity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class VotingStrategy(Enum):
    """Available voting strategies for consensus."""
    MAJORITY = "majority"       # 2/3+ detectors agree
    WEIGHTED = "weighted"       # Weight by detector accuracy
    THRESHOLD = "threshold"     # Only high-confidence findings
    UNANIMOUS = "unanimous"     # All detectors must agree


class ConfidenceMethod(Enum):
    """Methods for calculating aggregate confidence."""
    AVERAGE = "average"         # Simple average
    WEIGHTED = "weighted"       # Weighted by detector accuracy
    BAYESIAN = "bayesian"       # Prior + evidence strength
    MAX = "max"                 # Maximum (aggressive)
    MIN = "min"                 # Minimum (conservative)


class SeverityResolution(Enum):
    """Methods for resolving severity conflicts."""
    HIGHEST = "highest"         # Use highest severity
    LOWEST = "lowest"           # Use lowest (conservative)
    MAJORITY = "majority"       # Most common severity
    WEIGHTED = "weighted"       # Weight by confidence


@dataclass
class ConsensusResult:
    """Result of consensus calculation for a finding group."""
    has_consensus: bool
    confidence: float
    severity: Severity
    contributing_detectors: List[str]
    vote_count: int
    total_detectors: int
    agreement_ratio: float


@dataclass
class DetectorWeight:
    """Weight configuration for a detector."""
    name: str
    weight: float = 1.0
    accuracy: float = 0.85  # Historical accuracy


# Default detector weights based on typical accuracy
DEFAULT_DETECTOR_WEIGHTS = {
    # Graph-based detectors (lower false positive rate)
    "CircularDependencyDetector": DetectorWeight("CircularDependencyDetector", 1.2, 0.95),
    "GodClassDetector": DetectorWeight("GodClassDetector", 1.1, 0.85),
    "FeatureEnvyDetector": DetectorWeight("FeatureEnvyDetector", 1.0, 0.80),
    "ShotgunSurgeryDetector": DetectorWeight("ShotgunSurgeryDetector", 1.0, 0.85),
    "InappropriateIntimacyDetector": DetectorWeight("InappropriateIntimacyDetector", 1.0, 0.80),
    "ArchitecturalBottleneckDetector": DetectorWeight("ArchitecturalBottleneckDetector", 1.1, 0.90),

    # Hybrid detectors (external tool + graph)
    "RuffLintDetector": DetectorWeight("RuffLintDetector", 1.3, 0.98),  # Very accurate
    "RuffImportDetector": DetectorWeight("RuffImportDetector", 1.2, 0.95),
    "MypyDetector": DetectorWeight("MypyDetector", 1.3, 0.99),  # Type errors are definite
    "BanditDetector": DetectorWeight("BanditDetector", 1.1, 0.85),
    "SemgrepDetector": DetectorWeight("SemgrepDetector", 1.2, 0.90),
    "RadonDetector": DetectorWeight("RadonDetector", 1.0, 0.95),
    "JscpdDetector": DetectorWeight("JscpdDetector", 1.1, 0.90),
    "VultureDetector": DetectorWeight("VultureDetector", 0.9, 0.75),  # Higher false positives
    "PylintDetector": DetectorWeight("PylintDetector", 1.0, 0.85),

    # Default for unknown detectors
    "default": DetectorWeight("default", 1.0, 0.80),
}


class VotingEngine:
    """Engine for aggregating findings and determining consensus.

    Supports multiple voting strategies and confidence scoring methods
    to determine when multiple detectors agree on an issue.

    Example:
        >>> engine = VotingEngine(
        ...     strategy=VotingStrategy.WEIGHTED,
        ...     confidence_threshold=0.7
        ... )
        >>> consensus_findings = engine.vote(all_findings)
    """

    def __init__(
        self,
        strategy: VotingStrategy = VotingStrategy.WEIGHTED,
        confidence_method: ConfidenceMethod = ConfidenceMethod.WEIGHTED,
        severity_resolution: SeverityResolution = SeverityResolution.HIGHEST,
        confidence_threshold: float = 0.6,
        min_detectors_for_boost: int = 2,
        detector_weights: Optional[Dict[str, DetectorWeight]] = None,
    ):
        """Initialize voting engine.

        Args:
            strategy: Voting strategy to use
            confidence_method: Method for calculating aggregate confidence
            severity_resolution: Method for resolving severity conflicts
            confidence_threshold: Minimum confidence to include finding
            min_detectors_for_boost: Minimum detectors for confidence boost
            detector_weights: Custom detector weights (uses defaults if None)
        """
        self.strategy = strategy
        self.confidence_method = confidence_method
        self.severity_resolution = severity_resolution
        self.confidence_threshold = confidence_threshold
        self.min_detectors_for_boost = min_detectors_for_boost
        self.detector_weights = detector_weights or DEFAULT_DETECTOR_WEIGHTS

        # Statistics
        self.stats: Dict = {}

    def vote(self, findings: List[Finding]) -> Tuple[List[Finding], Dict]:
        """Apply voting to findings and return consensus findings.

        Args:
            findings: All findings from detectors

        Returns:
            Tuple of (consensus findings, voting statistics)
        """
        if not findings:
            return [], {"total_input": 0, "total_output": 0}

        # Group findings by entity
        groups = self._group_by_entity(findings)

        # Apply voting to each group
        consensus_findings = []
        rejected_count = 0
        boosted_count = 0

        for entity_key, group_findings in groups.items():
            if len(group_findings) == 1:
                # Single detector - check threshold
                finding = group_findings[0]
                confidence = self._get_finding_confidence(finding)

                if confidence >= self.confidence_threshold:
                    consensus_findings.append(finding)
                else:
                    rejected_count += 1
            else:
                # Multiple detectors - calculate consensus
                consensus = self._calculate_consensus(group_findings)

                if consensus.has_consensus and consensus.confidence >= self.confidence_threshold:
                    merged = self._create_consensus_finding(group_findings, consensus)
                    consensus_findings.append(merged)
                    boosted_count += 1
                else:
                    rejected_count += 1

        # Calculate statistics
        self.stats = {
            "total_input": len(findings),
            "total_output": len(consensus_findings),
            "groups_analyzed": len(groups),
            "single_detector_findings": sum(1 for g in groups.values() if len(g) == 1),
            "multi_detector_findings": sum(1 for g in groups.values() if len(g) > 1),
            "boosted_by_consensus": boosted_count,
            "rejected_low_confidence": rejected_count,
            "strategy": self.strategy.value,
            "confidence_method": self.confidence_method.value,
            "threshold": self.confidence_threshold,
        }

        logger.info(
            f"VotingEngine: {len(findings)} -> {len(consensus_findings)} findings "
            f"({boosted_count} boosted, {rejected_count} rejected)"
        )

        return consensus_findings, self.stats

    def _group_by_entity(self, findings: List[Finding]) -> Dict[str, List[Finding]]:
        """Group findings by the entity they target.

        Uses affected_nodes and affected_files to identify the same entity.
        """
        groups: Dict[str, List[Finding]] = defaultdict(list)

        for finding in findings:
            key = self._get_entity_key(finding)
            groups[key].append(finding)

        return groups

    def _get_entity_key(self, finding: Finding) -> str:
        """Generate unique key for entity identification.

        Groups findings by:
        1. Issue category (so only same-type issues get merged)
        2. Entity location (nodes, files, line range)

        This ensures detectors only "vote" on the same type of issue,
        not different issues that happen to be in the same location.
        """
        nodes = tuple(sorted(finding.affected_nodes or []))
        files = tuple(sorted(finding.affected_files or []))

        # Include line range for more precise grouping
        line_key = ""
        if finding.line_start:
            # Bucket into ranges of 10 lines
            bucket = (finding.line_start // 10) * 10
            line_key = f"|L{bucket}"

        # CRITICAL: Include issue category so only same-type issues merge
        # Different detectors finding different problems should NOT be merged
        category = self._get_issue_category(finding)

        return f"{category}|{nodes}|{files}{line_key}"

    def _get_issue_category(self, finding: Finding) -> str:
        """Determine the category/type of issue for grouping.

        Only findings in the same category can be merged via voting.
        This prevents merging unrelated issues just because they're
        in the same location.
        """
        detector = finding.detector

        # Map detectors to issue categories
        category_map = {
            # Structural issues (can corroborate each other)
            "GodClassDetector": "structural_complexity",
            "RadonDetector": "structural_complexity",

            # Coupling issues
            "CircularDependencyDetector": "coupling",
            "ShotgunSurgeryDetector": "coupling",
            "InappropriateIntimacyDetector": "coupling",
            "FeatureEnvyDetector": "coupling",

            # Dead/unused code
            "DeadCodeDetector": "dead_code",
            "VultureDetector": "dead_code",

            # Import issues
            "RuffImportDetector": "imports",

            # Linting/style (same rules from different linters)
            "RuffLintDetector": "linting",
            "PylintDetector": "linting",

            # Type issues
            "MypyDetector": "type_errors",

            # Security issues
            "BanditDetector": "security",
            "SemgrepDetector": "security",

            # Duplication
            "JscpdDetector": "duplication",

            # Architecture
            "ArchitecturalBottleneckDetector": "architecture",
            "MiddleManDetector": "architecture",
        }

        # Check for known detector
        if detector in category_map:
            return category_map[detector]

        # Handle merged/consensus detector names
        if detector.startswith("Consensus[") or detector.startswith("Merged["):
            # Extract first detector name from merged name
            inner = detector.split("[")[1].split("]")[0]
            first_detector = inner.split("+")[0]
            if first_detector in category_map:
                return category_map[first_detector]

        # Check collaboration metadata tags for category hints
        if finding.collaboration_metadata:
            tags = finding.get_collaboration_tags()
            if "security" in tags:
                return "security"
            if "complexity" in tags or "god_class" in tags:
                return "structural_complexity"
            if "coupling" in tags:
                return "coupling"
            if "dead_code" in tags or "unused" in tags:
                return "dead_code"

        # Default: use detector name as category (no merging with others)
        return f"detector_{detector}"

    def _calculate_consensus(self, findings: List[Finding]) -> ConsensusResult:
        """Calculate consensus for a group of findings.

        Args:
            findings: Findings targeting the same entity

        Returns:
            ConsensusResult with consensus details
        """
        detectors = [f.detector for f in findings]
        unique_detectors = list(set(detectors))

        # Calculate confidence
        confidence = self._calculate_confidence(findings)

        # Resolve severity
        severity = self._resolve_severity(findings)

        # Check if consensus achieved based on strategy
        has_consensus = self._check_consensus(findings, unique_detectors)

        agreement_ratio = len(unique_detectors) / max(len(findings), 1)

        return ConsensusResult(
            has_consensus=has_consensus,
            confidence=confidence,
            severity=severity,
            contributing_detectors=unique_detectors,
            vote_count=len(unique_detectors),
            total_detectors=len(findings),
            agreement_ratio=agreement_ratio,
        )

    def _check_consensus(self, findings: List[Finding], unique_detectors: List[str]) -> bool:
        """Check if consensus is achieved based on voting strategy."""
        detector_count = len(unique_detectors)

        if self.strategy == VotingStrategy.UNANIMOUS:
            # All findings must be from different detectors (no duplicates within same detector)
            return detector_count >= 2 and detector_count == len(findings)

        elif self.strategy == VotingStrategy.MAJORITY:
            # At least 2 detectors agree
            return detector_count >= 2

        elif self.strategy == VotingStrategy.WEIGHTED:
            # Calculate weighted vote score
            total_weight = sum(
                self._get_detector_weight(f.detector)
                for f in findings
            )
            # Need combined weight >= 2.0 for consensus
            return total_weight >= 2.0

        elif self.strategy == VotingStrategy.THRESHOLD:
            # Check if aggregate confidence meets threshold
            confidence = self._calculate_confidence(findings)
            return confidence >= self.confidence_threshold

        return detector_count >= 2

    def _calculate_confidence(self, findings: List[Finding]) -> float:
        """Calculate aggregate confidence using configured method."""
        confidences = []
        weights = []

        for finding in findings:
            conf = self._get_finding_confidence(finding)
            weight = self._get_detector_weight(finding.detector)
            confidences.append(conf)
            weights.append(weight)

        if not confidences:
            return 0.0

        if self.confidence_method == ConfidenceMethod.AVERAGE:
            base = sum(confidences) / len(confidences)

        elif self.confidence_method == ConfidenceMethod.WEIGHTED:
            total_weight = sum(weights)
            if total_weight > 0:
                base = sum(c * w for c, w in zip(confidences, weights)) / total_weight
            else:
                base = sum(confidences) / len(confidences)

        elif self.confidence_method == ConfidenceMethod.MAX:
            base = max(confidences)

        elif self.confidence_method == ConfidenceMethod.MIN:
            base = min(confidences)

        elif self.confidence_method == ConfidenceMethod.BAYESIAN:
            # Bayesian: Start with prior (0.5), update with evidence
            prior = 0.5
            for conf in confidences:
                # Simplified Bayesian update
                likelihood = conf
                prior = (prior * likelihood) / (prior * likelihood + (1 - prior) * (1 - likelihood))
            base = prior

        else:
            base = sum(confidences) / len(confidences)

        # Apply consensus boost if multiple detectors agree
        unique_detectors = len(set(f.detector for f in findings))
        if unique_detectors >= self.min_detectors_for_boost:
            # Boost: +5% per additional detector, max +20%
            boost = min(0.20, (unique_detectors - 1) * 0.05)
            base = min(1.0, base + boost)

        return base

    def _resolve_severity(self, findings: List[Finding]) -> Severity:
        """Resolve severity conflicts between detectors."""
        severities = [f.severity for f in findings]

        if not severities:
            return Severity.MEDIUM

        if self.severity_resolution == SeverityResolution.HIGHEST:
            return max(severities, key=self._severity_rank)

        elif self.severity_resolution == SeverityResolution.LOWEST:
            return min(severities, key=self._severity_rank)

        elif self.severity_resolution == SeverityResolution.MAJORITY:
            # Most common severity
            from collections import Counter
            counts = Counter(severities)
            return counts.most_common(1)[0][0]

        elif self.severity_resolution == SeverityResolution.WEIGHTED:
            # Weight by confidence
            severity_scores = defaultdict(float)
            for finding in findings:
                conf = self._get_finding_confidence(finding)
                weight = self._get_detector_weight(finding.detector)
                severity_scores[finding.severity] += conf * weight

            return max(severity_scores.keys(), key=lambda s: severity_scores[s])

        return max(severities, key=self._severity_rank)

    def _severity_rank(self, severity: Severity) -> int:
        """Convert severity to numeric rank."""
        ranks = {
            Severity.CRITICAL: 5,
            Severity.HIGH: 4,
            Severity.MEDIUM: 3,
            Severity.LOW: 2,
            Severity.INFO: 1,
        }
        return ranks.get(severity, 0)

    def _get_finding_confidence(self, finding: Finding) -> float:
        """Get confidence score for a finding."""
        if finding.collaboration_metadata:
            return finding.get_average_confidence()
        return 0.7  # Default confidence

    def _get_detector_weight(self, detector_name: str) -> float:
        """Get weight for a detector."""
        if detector_name in self.detector_weights:
            return self.detector_weights[detector_name].weight
        return self.detector_weights.get("default", DetectorWeight("default")).weight

    def _create_consensus_finding(
        self,
        findings: List[Finding],
        consensus: ConsensusResult
    ) -> Finding:
        """Create merged finding from consensus."""
        # Use highest severity finding as base
        sorted_findings = sorted(
            findings,
            key=lambda f: (self._severity_rank(f.severity), -self._get_finding_confidence(f)),
            reverse=True
        )
        base = sorted_findings[0]

        # Merge metadata
        all_metadata = []
        for f in findings:
            all_metadata.extend(f.collaboration_metadata)

        # Create descriptive detector name
        detector_names = consensus.contributing_detectors[:3]
        if len(consensus.contributing_detectors) > 3:
            detector_str = f"Consensus[{'+'.join(detector_names)}+{len(consensus.contributing_detectors)-3}more]"
        else:
            detector_str = f"Consensus[{'+'.join(detector_names)}]"

        merged = Finding(
            id=base.id,
            detector=detector_str,
            severity=consensus.severity,
            title=f"{base.title} [{consensus.vote_count} detectors]",
            description=self._merge_descriptions(findings, consensus),
            affected_nodes=base.affected_nodes,
            affected_files=base.affected_files,
            line_start=base.line_start,
            line_end=base.line_end,
            graph_context={
                **(base.graph_context or {}),
                "consensus_confidence": consensus.confidence,
                "detector_agreement": consensus.vote_count,
                "contributing_detectors": consensus.contributing_detectors,
            },
            suggested_fix=self._merge_suggestions(findings),
            estimated_effort=base.estimated_effort,
            collaboration_metadata=all_metadata,
            is_duplicate=True,
            detector_agreement_count=consensus.vote_count,
            aggregate_confidence=consensus.confidence,
            merged_from=consensus.contributing_detectors,
        )

        return merged

    def _merge_descriptions(self, findings: List[Finding], consensus: ConsensusResult) -> str:
        """Merge descriptions with consensus information."""
        base_desc = findings[0].description or ""

        consensus_note = (
            f"\n\n**Consensus Analysis**\n"
            f"- {consensus.vote_count} detectors agree on this issue\n"
            f"- Confidence: {consensus.confidence:.0%}\n"
            f"- Detectors: {', '.join(consensus.contributing_detectors)}"
        )

        return base_desc + consensus_note

    def _merge_suggestions(self, findings: List[Finding]) -> str:
        """Merge fix suggestions from multiple findings."""
        suggestions = []
        seen = set()

        for f in findings:
            if f.suggested_fix and f.suggested_fix not in seen:
                suggestions.append(f"[{f.detector}] {f.suggested_fix}")
                seen.add(f.suggested_fix)

        if suggestions:
            return "\n\n".join(suggestions)
        return findings[0].suggested_fix or ""

    def get_stats(self) -> Dict:
        """Get voting statistics from last run."""
        return self.stats

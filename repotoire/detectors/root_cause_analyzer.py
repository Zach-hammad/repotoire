"""Root Cause Analyzer for cross-detector pattern recognition.

REPO-155: Identifies root causes of issues by analyzing relationships between
findings from multiple detectors. Enables prioritized refactoring by showing
that fixing one issue (e.g., god class) resolves many cascading issues.

The "God Class Cascade" pattern:
    GodClass → CircularDependency (imports everything)
             → FeatureEnvy (methods use external classes)
             → ShotgunSurgery (everyone imports it)
             → InappropriateIntimacy (bidirectional coupling)
             → CodeDuplication (copy-paste instead of import)
"""

from dataclasses import dataclass, field
from typing import List, Dict, Set, Optional, Any
from repotoire.models import Finding, Severity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class RootCauseAnalysis:
    """Analysis result showing root cause and cascading issues."""

    root_cause_finding: Finding
    root_cause_type: str  # "god_class", "circular_dependency", etc.
    cascading_findings: List[Finding] = field(default_factory=list)
    impact_score: float = 0.0  # Higher = more impact if fixed
    estimated_resolved_count: int = 0
    refactoring_priority: str = "MEDIUM"  # LOW, MEDIUM, HIGH, CRITICAL
    suggested_approach: str = ""


class RootCauseAnalyzer:
    """Analyzes findings to identify root causes and cascading issues.

    Uses cross-detector patterns to find:
    1. God classes causing circular dependencies
    2. Feature envy caused by god classes
    3. Shotgun surgery linked to high-coupling classes
    4. Inappropriate intimacy from bidirectional god class dependencies
    """

    # Detector names for categorization
    GOD_CLASS_DETECTOR = "GodClassDetector"
    CIRCULAR_DEP_DETECTOR = "CircularDependencyDetector"
    FEATURE_ENVY_DETECTOR = "FeatureEnvyDetector"
    SHOTGUN_SURGERY_DETECTOR = "ShotgunSurgeryDetector"
    INTIMACY_DETECTOR = "InappropriateIntimacyDetector"
    MIDDLE_MAN_DETECTOR = "MiddleManDetector"

    def __init__(self):
        """Initialize the root cause analyzer."""
        self.analyses: List[RootCauseAnalysis] = []

    def analyze(self, findings: List[Finding]) -> List[Finding]:
        """Analyze findings and enrich them with root cause information.

        Args:
            findings: List of all findings from detectors

        Returns:
            Enriched findings with root cause analysis
        """
        if not findings:
            return findings

        # Group findings by detector
        by_detector = self._group_by_detector(findings)

        # Group findings by file
        by_file = self._group_by_file(findings)

        # Analyze god class cascade pattern
        self._analyze_god_class_cascade(by_detector, by_file)

        # Analyze circular dependency root causes
        self._analyze_circular_dep_causes(by_detector, by_file)

        # Enrich original findings with root cause info
        enriched = self._enrich_findings(findings)

        logger.info(
            f"RootCauseAnalyzer found {len(self.analyses)} root cause patterns "
            f"affecting {sum(a.estimated_resolved_count for a in self.analyses)} findings"
        )

        return enriched

    def _group_by_detector(self, findings: List[Finding]) -> Dict[str, List[Finding]]:
        """Group findings by detector name."""
        grouped: Dict[str, List[Finding]] = {}
        for finding in findings:
            detector = finding.detector
            if detector not in grouped:
                grouped[detector] = []
            grouped[detector].append(finding)
        return grouped

    def _group_by_file(self, findings: List[Finding]) -> Dict[str, List[Finding]]:
        """Group findings by affected file."""
        grouped: Dict[str, List[Finding]] = {}
        for finding in findings:
            for file_path in finding.affected_files or []:
                if file_path not in grouped:
                    grouped[file_path] = []
                grouped[file_path].append(finding)
        return grouped

    def _analyze_god_class_cascade(
        self,
        by_detector: Dict[str, List[Finding]],
        by_file: Dict[str, List[Finding]]
    ) -> None:
        """Identify god classes that cause cascading issues.

        Pattern: A god class often causes:
        - Circular dependencies (it imports/is imported by everything)
        - Feature envy (external methods want its data)
        - Shotgun surgery (changes ripple everywhere)
        - Inappropriate intimacy (tight coupling with many classes)
        """
        god_classes = by_detector.get(self.GOD_CLASS_DETECTOR, [])

        for god_class in god_classes:
            cascading = []
            god_class_files = set(god_class.affected_files or [])
            god_class_nodes = set(god_class.affected_nodes or [])

            # Check for circular dependencies involving this god class
            for circ_dep in by_detector.get(self.CIRCULAR_DEP_DETECTOR, []):
                circ_files = set(circ_dep.affected_files or [])
                if god_class_files & circ_files:
                    cascading.append(circ_dep)

            # Check for feature envy related to this god class
            for envy in by_detector.get(self.FEATURE_ENVY_DETECTOR, []):
                envy_context = envy.graph_context or {}
                target_class = envy_context.get("target_class", "")
                # Check if the envy target is in the god class
                for node in god_class_nodes:
                    if target_class and node in target_class:
                        cascading.append(envy)
                        break

            # Check for shotgun surgery (god class is widely used)
            for shotgun in by_detector.get(self.SHOTGUN_SURGERY_DETECTOR, []):
                shotgun_nodes = set(shotgun.affected_nodes or [])
                if god_class_nodes & shotgun_nodes:
                    cascading.append(shotgun)

            # Check for inappropriate intimacy
            for intimacy in by_detector.get(self.INTIMACY_DETECTOR, []):
                intimacy_nodes = set(intimacy.affected_nodes or [])
                if god_class_nodes & intimacy_nodes:
                    cascading.append(intimacy)

            # Check for findings in same files
            for file_path in god_class_files:
                for finding in by_file.get(file_path, []):
                    if finding.id != god_class.id and finding not in cascading:
                        # Only add if it's a related detector type
                        if finding.detector in [
                            self.CIRCULAR_DEP_DETECTOR,
                            self.FEATURE_ENVY_DETECTOR,
                            self.SHOTGUN_SURGERY_DETECTOR,
                            self.INTIMACY_DETECTOR,
                            self.MIDDLE_MAN_DETECTOR,
                        ]:
                            cascading.append(finding)

            if cascading:
                # Calculate impact score
                impact = self._calculate_impact_score(god_class, cascading)
                priority = self._calculate_priority(god_class, cascading)

                analysis = RootCauseAnalysis(
                    root_cause_finding=god_class,
                    root_cause_type="god_class",
                    cascading_findings=cascading,
                    impact_score=impact,
                    estimated_resolved_count=len(cascading) + 1,  # +1 for the god class itself
                    refactoring_priority=priority,
                    suggested_approach=self._suggest_god_class_refactoring(god_class, cascading)
                )
                self.analyses.append(analysis)

    def _analyze_circular_dep_causes(
        self,
        by_detector: Dict[str, List[Finding]],
        by_file: Dict[str, List[Finding]]
    ) -> None:
        """Identify root causes of circular dependencies.

        Circular dependencies can be caused by:
        - God classes (already handled above)
        - Inappropriate intimacy (bidirectional coupling)
        - Feature envy (reaching into other modules)
        """
        circular_deps = by_detector.get(self.CIRCULAR_DEP_DETECTOR, [])
        god_class_files = set()

        # Collect files already identified as god class root causes
        for analysis in self.analyses:
            if analysis.root_cause_type == "god_class":
                god_class_files.update(analysis.root_cause_finding.affected_files or [])

        for circ_dep in circular_deps:
            # Skip if already linked to a god class
            circ_files = set(circ_dep.affected_files or [])
            if circ_files & god_class_files:
                continue

            # Check for inappropriate intimacy as root cause
            cascading = []
            for intimacy in by_detector.get(self.INTIMACY_DETECTOR, []):
                intimacy_files = set(intimacy.affected_files or [])
                if circ_files & intimacy_files:
                    cascading.append(intimacy)

            if cascading:
                # Circular dep is the root cause, intimacy is related
                impact = self._calculate_impact_score(circ_dep, cascading)
                priority = self._calculate_priority(circ_dep, cascading)

                analysis = RootCauseAnalysis(
                    root_cause_finding=circ_dep,
                    root_cause_type="circular_dependency",
                    cascading_findings=cascading,
                    impact_score=impact,
                    estimated_resolved_count=len(cascading) + 1,
                    refactoring_priority=priority,
                    suggested_approach=self._suggest_circular_dep_refactoring(circ_dep)
                )
                self.analyses.append(analysis)

    def _calculate_impact_score(
        self,
        root_cause: Finding,
        cascading: List[Finding]
    ) -> float:
        """Calculate impact score for fixing the root cause.

        Higher score = fixing this has more impact.

        Args:
            root_cause: The root cause finding
            cascading: List of cascading findings

        Returns:
            Impact score (0.0 to 10.0)
        """
        # Base score from severity
        severity_scores = {
            Severity.CRITICAL: 4.0,
            Severity.HIGH: 3.0,
            Severity.MEDIUM: 2.0,
            Severity.LOW: 1.0,
            Severity.INFO: 0.5,
        }

        base_score = severity_scores.get(root_cause.severity, 1.0)

        # Add score for each cascading issue
        cascade_score = sum(
            severity_scores.get(f.severity, 1.0) * 0.5
            for f in cascading
        )

        # Bonus for number of cascading issues
        count_bonus = min(len(cascading) * 0.3, 2.0)

        total = base_score + cascade_score + count_bonus

        # Normalize to 0-10 scale
        return min(total, 10.0)

    def _calculate_priority(
        self,
        root_cause: Finding,
        cascading: List[Finding]
    ) -> str:
        """Calculate refactoring priority.

        Args:
            root_cause: The root cause finding
            cascading: List of cascading findings

        Returns:
            Priority string: LOW, MEDIUM, HIGH, CRITICAL
        """
        # Count high-severity cascading issues
        critical_count = sum(1 for f in cascading if f.severity == Severity.CRITICAL)
        high_count = sum(1 for f in cascading if f.severity == Severity.HIGH)

        if root_cause.severity == Severity.CRITICAL or critical_count >= 1:
            return "CRITICAL"
        elif root_cause.severity == Severity.HIGH or high_count >= 2:
            return "HIGH"
        elif len(cascading) >= 3:
            return "HIGH"
        elif len(cascading) >= 1:
            return "MEDIUM"
        else:
            return "LOW"

    def _suggest_god_class_refactoring(
        self,
        god_class: Finding,
        cascading: List[Finding]
    ) -> str:
        """Generate refactoring suggestion for god class root cause."""
        class_name = god_class.graph_context.get("name", "the class") if god_class.graph_context else "the class"
        method_count = god_class.graph_context.get("method_count", 0) if god_class.graph_context else 0

        # Count cascading issue types
        has_circular = any(f.detector == self.CIRCULAR_DEP_DETECTOR for f in cascading)
        has_envy = any(f.detector == self.FEATURE_ENVY_DETECTOR for f in cascading)
        has_shotgun = any(f.detector == self.SHOTGUN_SURGERY_DETECTOR for f in cascading)

        suggestions = [f"ROOT CAUSE: God class '{class_name}' is causing {len(cascading)} cascading issues.\n"]
        suggestions.append("RECOMMENDED REFACTORING APPROACH:\n")

        step = 1
        if has_circular:
            suggestions.append(f"  {step}. Extract interfaces to break circular dependencies\n")
            step += 1

        if method_count > 20:
            suggestions.append(f"  {step}. Split into focused classes by responsibility:\n")
            suggestions.append(f"     - Group related methods (look at shared field access)\n")
            suggestions.append(f"     - Extract each group into a dedicated class\n")
            step += 1

        if has_envy:
            suggestions.append(f"  {step}. Move envious methods to their target classes\n")
            step += 1

        if has_shotgun:
            suggestions.append(f"  {step}. Create a facade to limit external coupling\n")
            step += 1

        suggestions.append(f"\nEXPECTED RESULT: Fixing '{class_name}' will resolve ~{len(cascading)} related issues.")

        return "".join(suggestions)

    def _suggest_circular_dep_refactoring(self, circ_dep: Finding) -> str:
        """Generate refactoring suggestion for circular dependency root cause."""
        cycle_length = circ_dep.graph_context.get("cycle_length", 0) if circ_dep.graph_context else 0

        suggestions = ["ROOT CAUSE: Circular dependency creating tight coupling.\n"]
        suggestions.append("RECOMMENDED REFACTORING APPROACH:\n")

        if cycle_length <= 3:
            suggestions.append("  1. Consider merging tightly coupled modules\n")
            suggestions.append("  2. Or extract shared types to a common module\n")
            suggestions.append("  3. Use TYPE_CHECKING for type-only imports\n")
        else:
            suggestions.append("  1. Identify the module with most incoming imports\n")
            suggestions.append("  2. Extract its dependencies into interface module\n")
            suggestions.append("  3. Apply Dependency Inversion Principle\n")
            suggestions.append("  4. Consider using dependency injection\n")

        return "".join(suggestions)

    def _enrich_findings(self, findings: List[Finding]) -> List[Finding]:
        """Enrich findings with root cause analysis information."""
        # Build lookup of finding ID to analysis
        root_cause_ids: Dict[str, RootCauseAnalysis] = {}
        cascading_ids: Dict[str, RootCauseAnalysis] = {}

        for analysis in self.analyses:
            root_cause_ids[analysis.root_cause_finding.id] = analysis
            for cascading in analysis.cascading_findings:
                cascading_ids[cascading.id] = analysis

        # Enrich each finding
        for finding in findings:
            if finding.graph_context is None:
                finding.graph_context = {}

            # Check if this is a root cause
            if finding.id in root_cause_ids:
                analysis = root_cause_ids[finding.id]
                finding.graph_context["is_root_cause"] = True
                finding.graph_context["root_cause_type"] = analysis.root_cause_type
                finding.graph_context["cascading_count"] = len(analysis.cascading_findings)
                finding.graph_context["impact_score"] = analysis.impact_score
                finding.graph_context["refactoring_priority"] = analysis.refactoring_priority

                # Update suggested fix with root cause approach
                if analysis.suggested_approach:
                    finding.suggested_fix = analysis.suggested_approach

                # Add to collaboration metadata
                if finding.collaboration_metadata:
                    # Add tag to last metadata entry
                    for meta in finding.collaboration_metadata:
                        if "root_cause" not in meta.tags:
                            meta.tags.append("root_cause")
                        meta.evidence.append(
                            f"causes_{analysis.estimated_resolved_count}_issues"
                        )

            # Check if this is caused by a root cause
            elif finding.id in cascading_ids:
                analysis = cascading_ids[finding.id]
                root_name = analysis.root_cause_finding.graph_context.get("name", "unknown") if analysis.root_cause_finding.graph_context else "unknown"

                finding.graph_context["caused_by_root_cause"] = True
                finding.graph_context["root_cause_detector"] = analysis.root_cause_finding.detector
                finding.graph_context["root_cause_id"] = analysis.root_cause_finding.id

                # Add note about root cause to description
                if analysis.root_cause_type == "god_class":
                    root_note = f"\n\n📍 ROOT CAUSE: This issue is linked to god class '{root_name}'. Fixing the god class may resolve this issue."
                else:
                    root_note = f"\n\n📍 ROOT CAUSE: This issue is linked to {analysis.root_cause_type.replace('_', ' ')}. Fixing the root cause may resolve this issue."

                finding.description = (finding.description or "") + root_note

                # Add to collaboration metadata
                if finding.collaboration_metadata:
                    for meta in finding.collaboration_metadata:
                        if "cascading_issue" not in meta.tags:
                            meta.tags.append("cascading_issue")

        return findings

    def get_analyses(self) -> List[RootCauseAnalysis]:
        """Get all root cause analyses."""
        return self.analyses

    def get_summary(self) -> Dict[str, Any]:
        """Get summary statistics of root cause analysis."""
        total_root_causes = len(self.analyses)
        total_cascading = sum(len(a.cascading_findings) for a in self.analyses)

        by_type: Dict[str, int] = {}
        for analysis in self.analyses:
            by_type[analysis.root_cause_type] = by_type.get(analysis.root_cause_type, 0) + 1

        avg_impact = (
            sum(a.impact_score for a in self.analyses) / total_root_causes
            if total_root_causes > 0 else 0
        )

        return {
            "total_root_causes": total_root_causes,
            "total_cascading_issues": total_cascading,
            "root_causes_by_type": by_type,
            "average_impact_score": round(avg_impact, 2),
            "high_priority_count": sum(
                1 for a in self.analyses
                if a.refactoring_priority in ("HIGH", "CRITICAL")
            ),
        }

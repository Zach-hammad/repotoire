"""Finding deduplication engine for cross-detector collaboration.

This module provides intelligent deduplication of findings from multiple detectors,
reducing noise and improving signal by identifying when multiple detectors agree
on the same issue.

Part of REPO-152: Phase 3 - Query Engine for Finding Deduplication
"""

from typing import Dict, List, Tuple, Set
from collections import defaultdict

from repotoire.models import Finding, Severity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class FindingDeduplicator:
    """Deduplicates findings from multiple detectors.

    Identifies when multiple detectors flag the same entity and merges
    them into a single high-confidence finding with aggregated metadata.

    Key Features:
        - Entity-based deduplication (same affected nodes)
        - Location-based grouping (within proximity threshold)
        - Confidence aggregation from multiple detectors
        - Preserves all detector context via merged_from

    Example:
        >>> deduplicator = FindingDeduplicator()
        >>> deduplicated = deduplicator.merge_duplicates(findings)
        >>> print(f"Reduced {len(findings)} to {len(deduplicated)} findings")
        >>>
        >>> # Find high-confidence findings (multiple detectors agree)
        >>> high_confidence = [f for f in deduplicated if f.detector_agreement_count >= 2]
    """

    def __init__(self, line_proximity_threshold: int = 5):
        """Initialize deduplicator.

        Args:
            line_proximity_threshold: Max line distance to consider findings as duplicates
                                     (default: 5 lines)
        """
        self.line_proximity_threshold = line_proximity_threshold

    def merge_duplicates(self, findings: List[Finding]) -> List[Finding]:
        """Merge duplicate findings from multiple detectors.

        Identifies findings that target the same entity and location, then merges
        them into single findings with aggregated confidence and metadata.

        Args:
            findings: List of findings from all detectors

        Returns:
            Deduplicated list of findings with merged metadata

        Example:
            >>> # Before: 10 findings from different detectors on same entity
            >>> # After: 1 finding with detector_agreement_count=3, aggregate_confidence=0.9
            >>> deduplicated = deduplicator.merge_duplicates(findings)
        """
        if not findings:
            return []

        # Group findings by duplicate key
        duplicate_groups = self._identify_duplicates(findings)

        # Merge each group
        merged_findings = []
        original_count = len(findings)

        for group_key, group_findings in duplicate_groups.items():
            if len(group_findings) == 1:
                # Not a duplicate, keep as-is
                merged_findings.append(group_findings[0])
            else:
                # Merge duplicate findings
                merged = self._merge_finding_group(group_findings)
                merged_findings.append(merged)

        deduplicated_count = len(merged_findings)
        duplicate_count = original_count - deduplicated_count
        reduction_pct = (duplicate_count / original_count * 100) if original_count > 0 else 0.0

        logger.debug(
            f"Deduplicated {original_count} findings to {deduplicated_count} "
            f"({duplicate_count} duplicates removed, {reduction_pct:.1f}%)"
        )

        # Calculate deduplication statistics
        stats = self._calculate_stats(merged_findings, original_count, deduplicated_count, duplicate_count)

        return merged_findings, stats

    def _identify_duplicates(self, findings: List[Finding]) -> Dict[str, List[Finding]]:
        """Identify duplicate findings by grouping them.

        Findings are considered duplicates if they:
        1. Target the same entity (affected_nodes)
        2. Are in the same file(s)
        3. Are within proximity threshold (if line numbers present)

        Args:
            findings: List of findings to analyze

        Returns:
            Dictionary mapping duplicate keys to lists of duplicate findings
        """
        groups: Dict[str, List[Finding]] = defaultdict(list)

        for finding in findings:
            key = self._get_duplicate_key(finding)
            groups[key].append(finding)

        return groups

    def _get_duplicate_key(self, finding: Finding) -> str:
        """Generate unique key for identifying duplicate findings.

        Key components:
        - Sorted affected nodes (to catch same entity)
        - Sorted affected files
        - Line range bucket (groups nearby lines)

        Args:
            finding: Finding to generate key for

        Returns:
            Unique key string for grouping duplicates
        """
        # Sort to ensure consistent keys
        nodes = tuple(sorted(finding.affected_nodes))
        files = tuple(sorted(finding.affected_files))

        # Bucket line numbers into ranges (e.g., lines 10-14 -> bucket 10)
        if finding.line_start is not None:
            line_bucket = (finding.line_start // self.line_proximity_threshold) * self.line_proximity_threshold
        else:
            line_bucket = None

        return f"{nodes}|{files}|{line_bucket}"

    def _merge_finding_group(self, findings: List[Finding]) -> Finding:
        """Merge a group of duplicate findings into one.

        Merging strategy:
        1. Use highest severity finding as base
        2. Aggregate confidence scores
        3. Combine collaboration metadata
        4. Track which detectors contributed
        5. Merge descriptions and fix suggestions

        Args:
            findings: List of duplicate findings to merge

        Returns:
            Merged finding with aggregated metadata
        """
        # Sort by severity (highest first), then by confidence
        sorted_findings = sorted(
            findings,
            key=lambda f: (
                self._severity_rank(f.severity),
                -f.get_average_confidence()
            ),
            reverse=True
        )

        # Use highest severity finding as base
        base_finding = sorted_findings[0]

        # Track all contributing detectors
        all_detectors = [f.detector for f in findings]
        unique_detectors = list(set(all_detectors))

        # Merge collaboration metadata from all findings
        all_metadata = []
        for finding in findings:
            all_metadata.extend(finding.collaboration_metadata)

        # Calculate aggregate confidence
        aggregate_confidence = self._calculate_aggregate_confidence(findings)

        # Create merged finding
        merged = Finding(
            id=base_finding.id,  # Keep base ID
            detector=f"Merged[{'+'.join(unique_detectors[:3])}]",  # Show detectors
            severity=base_finding.severity,  # Highest severity
            title=self._merge_titles(findings),
            description=self._merge_descriptions(findings),
            affected_nodes=base_finding.affected_nodes,
            affected_files=base_finding.affected_files,
            line_start=base_finding.line_start,
            line_end=base_finding.line_end,
            graph_context=base_finding.graph_context,
            suggested_fix=self._merge_fix_suggestions(findings),
            estimated_effort=base_finding.estimated_effort,
            created_at=base_finding.created_at,
            collaboration_metadata=all_metadata,
            # Deduplication fields
            is_duplicate=True,
            detector_agreement_count=len(unique_detectors),
            aggregate_confidence=aggregate_confidence,
            merged_from=unique_detectors
        )

        logger.debug(
            f"Merged {len(findings)} findings from {len(unique_detectors)} detectors: "
            f"{unique_detectors} (aggregate confidence: {aggregate_confidence:.2f})"
        )

        return merged

    def _calculate_aggregate_confidence(self, findings: List[Finding]) -> float:
        """Calculate aggregate confidence from multiple detectors.

        Uses weighted average based on detector count and individual confidences.
        More detectors = higher confidence boost.

        Args:
            findings: List of findings to aggregate

        Returns:
            Aggregate confidence score (0.0-1.0)
        """
        if not findings:
            return 0.0

        # Get all confidence scores
        all_confidences = []
        for finding in findings:
            if finding.collaboration_metadata:
                avg_confidence = finding.get_average_confidence()
                all_confidences.append(avg_confidence)
            else:
                # Default confidence if no metadata
                all_confidences.append(0.7)

        # Base confidence: average of all confidences
        base_confidence = sum(all_confidences) / len(all_confidences)

        # Boost factor: more detectors = higher confidence
        # Formula: boost = min(0.2, detector_count * 0.05)
        unique_detectors = len(set(f.detector for f in findings))
        confidence_boost = min(0.2, unique_detectors * 0.05)

        # Aggregate = base + boost, capped at 1.0
        aggregate = min(1.0, base_confidence + confidence_boost)

        return aggregate

    def _merge_titles(self, findings: List[Finding]) -> str:
        """Merge titles from multiple findings.

        Args:
            findings: Findings to merge titles from

        Returns:
            Merged title string
        """
        base = findings[0].title
        detector_count = len(set(f.detector for f in findings))

        if detector_count > 1:
            return f"{base} [{detector_count} detectors agree]"
        return base

    def _merge_descriptions(self, findings: List[Finding]) -> str:
        """Merge descriptions from multiple findings.

        Args:
            findings: Findings to merge descriptions from

        Returns:
            Combined description with detector context
        """
        base_description = findings[0].description

        # Add note about multiple detectors
        unique_detectors = list(set(f.detector for f in findings))
        if len(unique_detectors) > 1:
            detector_list = ", ".join(unique_detectors)
            note = (
                f"\n\n**🔍 Multiple Detector Agreement**\n"
                f"This issue was identified by {len(unique_detectors)} detectors: {detector_list}\n"
                f"High confidence due to multi-detector agreement."
            )
            return base_description + note

        return base_description

    def _merge_fix_suggestions(self, findings: List[Finding]) -> str:
        """Merge fix suggestions from multiple findings.

        Args:
            findings: Findings to merge suggestions from

        Returns:
            Combined fix suggestion
        """
        # Collect unique fix suggestions
        suggestions = []
        for finding in findings:
            if finding.suggested_fix and finding.suggested_fix not in suggestions:
                suggestions.append(f"• {finding.detector}: {finding.suggested_fix}")

        if suggestions:
            return "\n".join(suggestions)

        return findings[0].suggested_fix or "Review and refactor"

    def _severity_rank(self, severity: Severity) -> int:
        """Convert severity to numeric rank for sorting.

        Args:
            severity: Severity enum value

        Returns:
            Numeric rank (higher = more severe)
        """
        rank_map = {
            Severity.CRITICAL: 5,
            Severity.HIGH: 4,
            Severity.MEDIUM: 3,
            Severity.LOW: 2,
            Severity.INFO: 1
        }
        return rank_map.get(severity, 0)

    def _calculate_stats(
        self,
        merged_findings: List[Finding],
        original_count: int,
        deduplicated_count: int,
        duplicate_count: int
    ) -> Dict[str, any]:
        """Calculate detailed deduplication statistics.

        Args:
            merged_findings: List of deduplicated findings
            original_count: Original number of findings before deduplication
            deduplicated_count: Number of findings after deduplication
            duplicate_count: Number of duplicates removed

        Returns:
            Dictionary with deduplication statistics
        """
        reduction_pct = (duplicate_count / original_count * 100) if original_count > 0 else 0.0

        # Find top merged findings (by detector agreement)
        top_merged = sorted(
            [f for f in merged_findings if f.detector_agreement_count > 1],
            key=lambda x: (x.detector_agreement_count, x.aggregate_confidence),
            reverse=True
        )[:10]

        # Group merged findings by type/category
        merged_by_category = defaultdict(int)
        for finding in merged_findings:
            if finding.detector_agreement_count > 1:
                # Extract category from collaboration metadata or title
                category = "unknown"
                if finding.collaboration_metadata:
                    # Get most common tag from all detectors
                    all_tags = [tag for meta in finding.collaboration_metadata for tag in meta.tags]
                    if all_tags:
                        from collections import Counter
                        category = Counter(all_tags).most_common(1)[0][0]
                merged_by_category[category] += 1

        return {
            "original_count": original_count,
            "deduplicated_count": deduplicated_count,
            "duplicate_count": duplicate_count,
            "reduction_percentage": reduction_pct,
            "top_merged_findings": [
                {
                    "title": f.title,
                    "detector_agreement_count": f.detector_agreement_count,
                    "aggregate_confidence": f.aggregate_confidence,
                    "severity": f.severity.value,
                    "detectors": f.merged_from[:5] if f.merged_from else []
                }
                for f in top_merged
            ],
            "merged_by_category": dict(merged_by_category)
        }

"""Base detector interface."""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from repotoire.graph import FalkorDBClient
from repotoire.models import Finding, Severity


class CodeSmellDetector(ABC):
    """Abstract base class for code smell detectors.

    Phase 6 improvement: Added support for confidence filtering and minimum
    severity threshold to allow per-detector configuration of output quality.
    """

    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict] = None,
    ):
        """Initialize detector.

        Args:
            graph_client: FalkorDB database client
            detector_config: Optional configuration dict. May include:
                - repo_id: Repository UUID for filtering queries (multi-tenant isolation)
                - confidence_threshold: Minimum confidence (0.0-1.0) to include findings
                - min_severity: Minimum severity level (e.g., "medium" to exclude low/info)
                - weight: Detector weight for multi-detector voting (0.0-1.0, default: 1.0)
        """
        self.db = graph_client
        self.config = detector_config or {}
        self.repo_id = self.config.get("repo_id")

        # Phase 6: Confidence and severity filtering
        self.confidence_threshold = self.config.get("confidence_threshold", 0.0)
        self.min_severity = self._parse_min_severity(self.config.get("min_severity"))
        self.weight = self.config.get("weight", 1.0)

    def _parse_min_severity(self, severity_str: Optional[str]) -> Severity:
        """Parse minimum severity from string.

        Args:
            severity_str: Severity name (e.g., "medium", "high") or None

        Returns:
            Severity enum value, defaults to INFO (include all)
        """
        if not severity_str:
            return Severity.INFO

        severity_map = {
            "critical": Severity.CRITICAL,
            "high": Severity.HIGH,
            "medium": Severity.MEDIUM,
            "low": Severity.LOW,
            "info": Severity.INFO,
        }
        return severity_map.get(severity_str.lower(), Severity.INFO)

    def _passes_filters(self, finding: Finding) -> bool:
        """Check if a finding passes confidence and severity filters.

        Args:
            finding: Finding to check

        Returns:
            True if finding should be included, False if filtered out
        """
        # Check severity threshold
        severity_order = {
            Severity.CRITICAL: 0,
            Severity.HIGH: 1,
            Severity.MEDIUM: 2,
            Severity.LOW: 3,
            Severity.INFO: 4,
        }

        if severity_order.get(finding.severity, 4) > severity_order.get(self.min_severity, 4):
            return False

        # Check confidence threshold (from collaboration metadata if available)
        if self.confidence_threshold > 0 and finding.collaboration_metadata:
            finding_confidence = finding.collaboration_metadata.confidence
            if finding_confidence < self.confidence_threshold:
                return False

        return True

    def filter_findings(self, findings: List[Finding]) -> List[Finding]:
        """Filter findings based on confidence and severity thresholds.

        Phase 6 improvement: Allows callers to filter detector output based on
        configurable quality thresholds.

        Args:
            findings: List of findings to filter

        Returns:
            Filtered list of findings
        """
        if self.confidence_threshold == 0.0 and self.min_severity == Severity.INFO:
            return findings  # No filtering needed

        return [f for f in findings if self._passes_filters(f)]

    def _get_repo_filter(self, node_alias: str = "n") -> str:
        """Get Cypher WHERE clause fragment for repo_id filtering.

        Args:
            node_alias: The node alias to filter (default: 'n')

        Returns:
            Empty string if no repo_id, otherwise 'AND n.repoId = $repo_id'
        """
        if self.repo_id:
            return f"AND {node_alias}.repoId = $repo_id"
        return ""

    def _get_query_params(self, **extra_params) -> Dict:
        """Get query parameters including repo_id if set.

        Args:
            **extra_params: Additional parameters to include

        Returns:
            Dict with repo_id (if set) and any extra parameters
        """
        params = {}
        if self.repo_id:
            params["repo_id"] = self.repo_id
        params.update(extra_params)
        return params

    @property
    def needs_previous_findings(self) -> bool:
        """Whether this detector requires findings from other detectors.

        Override to return True for detectors that depend on other detectors'
        results (e.g., DeadCodeDetector needs VultureDetector findings for
        cross-validation, ArchitecturalBottleneckDetector needs RadonDetector
        findings for risk amplification).

        Detectors that need previous findings will run in phase 2 (sequentially)
        after all independent detectors complete in phase 1 (parallel).

        Returns:
            True if detector needs previous findings, False otherwise (default)
        """
        return False

    @abstractmethod
    def detect(self) -> List[Finding]:
        """Run detection algorithm on the graph.

        Returns:
            List of findings
        """
        pass

    @abstractmethod
    def severity(self, finding: Finding) -> Severity:
        """Calculate severity of a finding.

        Args:
            finding: Finding to assess

        Returns:
            Severity level
        """
        pass

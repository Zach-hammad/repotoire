"""Base detector interface.

REPO-600: Multi-tenant data isolation support via tenant_id filtering.
"""

from abc import ABC, abstractmethod
import logging
from typing import Dict, List, Optional

from repotoire.graph import FalkorDBClient
from repotoire.models import Finding, Severity

logger = logging.getLogger(__name__)


class CodeSmellDetector(ABC):
    """Abstract base class for code smell detectors.

    Phase 6 improvement: Added support for confidence filtering and minimum
    severity threshold to allow per-detector configuration of output quality.

    REPO-600: Added tenant_id support for multi-tenant data isolation. Detectors
    now automatically filter by tenant_id from TenantContext in addition to repo_id.
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
                - repo_id: Repository UUID for filtering queries (repo-level isolation)
                - tenant_id: Organization UUID for filtering queries (tenant-level isolation)
                - confidence_threshold: Minimum confidence (0.0-1.0) to include findings
                - min_severity: Minimum severity level (e.g., "medium" to exclude low/info)
                - weight: Detector weight for multi-detector voting (0.0-1.0, default: 1.0)
        """
        self.db = graph_client
        self.config = detector_config or {}
        self.repo_id = self.config.get("repo_id")
        # REPO-600: Support explicit tenant_id from config
        self._tenant_id_override = self.config.get("tenant_id")

        # Phase 6: Confidence and severity filtering
        self.confidence_threshold = self.config.get("confidence_threshold", 0.0)
        self.min_severity = self._parse_min_severity(self.config.get("min_severity"))
        self.weight = self.config.get("weight", 1.0)

    @property
    def tenant_id(self) -> Optional[str]:
        """Get tenant_id for query filtering.

        REPO-600: Multi-tenant data isolation.

        Priority:
        1. Explicit tenant_id from config (for CLI/background tasks)
        2. tenant_id from TenantContext (for API requests)

        Returns:
            Tenant ID string or None if not available
        """
        if self._tenant_id_override:
            return self._tenant_id_override

        # Try to get from TenantContext (set by middleware)
        try:
            from repotoire.tenant.context import get_current_org_id_str
            return get_current_org_id_str()
        except Exception:
            return None

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

    def _get_tenant_filter(self, node_alias: str = "n") -> str:
        """Get Cypher WHERE clause fragment for tenant_id filtering.

        REPO-600: Multi-tenant data isolation.

        Args:
            node_alias: The node alias to filter (default: 'n')

        Returns:
            Empty string if no tenant_id, otherwise 'AND n.tenantId = $tenant_id'
        """
        if self.tenant_id:
            return f"AND {node_alias}.tenantId = $tenant_id"
        return ""

    def _get_isolation_filter(self, node_alias: str = "n") -> str:
        """Get combined tenant + repo isolation filter.

        REPO-600: Multi-tenant data isolation.

        This combines both tenant-level (org) and repo-level isolation filters.
        Use this method for complete data isolation in detector queries.

        Args:
            node_alias: The node alias to filter (default: 'n')

        Returns:
            Combined WHERE clause fragments for tenant and repo isolation
        """
        filters = []
        tenant_filter = self._get_tenant_filter(node_alias)
        repo_filter = self._get_repo_filter(node_alias)

        if tenant_filter:
            filters.append(tenant_filter)
        if repo_filter:
            filters.append(repo_filter)

        return " ".join(filters)

    def _get_query_params(self, **extra_params) -> Dict:
        """Get query parameters including tenant_id and repo_id if set.

        REPO-600: Now includes tenant_id for multi-tenant isolation.

        Args:
            **extra_params: Additional parameters to include

        Returns:
            Dict with tenant_id, repo_id (if set) and any extra parameters
        """
        params = {}
        if self.tenant_id:
            params["tenant_id"] = self.tenant_id
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

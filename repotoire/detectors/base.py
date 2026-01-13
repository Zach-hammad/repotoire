"""Base detector interface."""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from repotoire.graph import FalkorDBClient
from repotoire.models import Finding, Severity


class CodeSmellDetector(ABC):
    """Abstract base class for code smell detectors."""

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
        """
        self.db = graph_client
        self.config = detector_config or {}
        self.repo_id = self.config.get("repo_id")

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

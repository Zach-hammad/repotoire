"""Base detector interface."""

from abc import ABC, abstractmethod
from typing import List

from repotoire.graph import Neo4jClient
from repotoire.models import Finding, Severity


class CodeSmellDetector(ABC):
    """Abstract base class for code smell detectors."""

    def __init__(self, neo4j_client: Neo4jClient):
        """Initialize detector.

        Args:
            neo4j_client: Neo4j database client
        """
        self.db = neo4j_client

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

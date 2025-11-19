"""Base detector interface."""

from abc import ABC, abstractmethod
from typing import List

from falkor.graph import Neo4jClient
from falkor.models import Finding, Severity


class CodeSmellDetector(ABC):
    """Abstract base class for code smell detectors."""

    def __init__(self, neo4j_client: Neo4jClient):
        """Initialize detector.

        Args:
            neo4j_client: Neo4j database client
        """
        self.db = neo4j_client

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

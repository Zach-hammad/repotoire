"""Circular dependency detector using graph algorithms."""

import uuid
from typing import List, Dict, Set
from datetime import datetime

from falkor.detectors.base import CodeSmellDetector
from falkor.models import Finding, Severity


class CircularDependencyDetector(CodeSmellDetector):
    """Detects circular dependencies in import graph using Tarjan's algorithm."""

    def detect(self) -> List[Finding]:
        """Find circular dependencies in the codebase.

        Uses optimized Cypher to find strongly connected components (SCCs) in the
        IMPORTS relationship graph. Handles both File and Module level imports.

        Returns:
            List of findings, one per circular dependency cycle
        """
        findings: List[Finding] = []

        # Optimized query using bounded path traversal
        # Finds cycles in the import graph (both direct and via modules)
        query = """
        MATCH (f1:File)
        MATCH (f2:File)
        WHERE id(f1) < id(f2) AND f1 <> f2
        MATCH path = shortestPath((f1)-[:IMPORTS*1..15]->(f2))
        MATCH cyclePath = shortestPath((f2)-[:IMPORTS*1..15]->(f1))
        WITH DISTINCT [node IN nodes(path) + nodes(cyclePath) WHERE node:File | node.filePath] AS cycle
        WHERE size(cycle) > 1
        RETURN cycle, size(cycle) AS cycle_length
        ORDER BY cycle_length DESC
        """

        results = self.db.execute_query(query)

        # Deduplicate cycles (same cycle can be found from different starting points)
        seen_cycles: Set[tuple] = set()

        for record in results:
            cycle = record["cycle"]
            cycle_length = record["cycle_length"]

            # Normalize cycle to canonical form (rotate to start with minimum element)
            # This preserves cycle directionality unlike sorting
            normalized = self._normalize_cycle(cycle)
            if normalized in seen_cycles:
                continue
            seen_cycles.add(normalized)

            # Create finding for this cycle
            finding_id = str(uuid.uuid4())

            # Format cycle for description
            cycle_display = " -> ".join([f.split("/")[-1] for f in cycle[:5]])
            if len(cycle) > 5:
                cycle_display += f" ... ({len(cycle)} files total)"

            finding = Finding(
                id=finding_id,
                detector="CircularDependencyDetector",
                severity=self._calculate_severity(cycle_length),
                title=f"Circular dependency involving {cycle_length} files",
                description=f"Found circular import chain: {cycle_display}",
                affected_nodes=cycle,
                affected_files=cycle,
                graph_context={
                    "cycle_length": cycle_length,
                    "cycle_files": cycle,
                },
                suggested_fix=self._suggest_fix(cycle_length),
                estimated_effort=self._estimate_effort(cycle_length),
                created_at=datetime.now(),
            )

            findings.append(finding)

        return findings

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity based on cycle length.

        Args:
            finding: Finding to assess

        Returns:
            Severity level
        """
        cycle_length = finding.graph_context.get("cycle_length", 0)
        return self._calculate_severity(cycle_length)

    def _calculate_severity(self, cycle_length: int) -> Severity:
        """Calculate severity based on cycle characteristics.

        Args:
            cycle_length: Number of files in the cycle

        Returns:
            Severity level
        """
        if cycle_length >= 10:
            return Severity.CRITICAL
        elif cycle_length >= 5:
            return Severity.HIGH
        elif cycle_length >= 3:
            return Severity.MEDIUM
        else:
            return Severity.LOW

    def _suggest_fix(self, cycle_length: int) -> str:
        """Suggest how to fix the circular dependency.

        Args:
            cycle_length: Number of files in the cycle

        Returns:
            Fix suggestion
        """
        if cycle_length >= 5:
            return (
                "Large circular dependency detected. Consider:\n"
                "1. Extract shared interfaces/types into a separate module\n"
                "2. Use dependency injection to break tight coupling\n"
                "3. Refactor into layers with clear dependency direction\n"
                "4. Apply the Dependency Inversion Principle"
            )
        else:
            return (
                "Small circular dependency. Consider:\n"
                "1. Merge the circular modules if they're tightly coupled\n"
                "2. Extract common dependencies to a third module\n"
                "3. Use forward references (TYPE_CHECKING) for type hints"
            )

    def _estimate_effort(self, cycle_length: int) -> str:
        """Estimate effort to fix based on cycle size.

        Args:
            cycle_length: Number of files in the cycle

        Returns:
            Effort estimate
        """
        if cycle_length >= 10:
            return "Large (2-4 days)"
        elif cycle_length >= 5:
            return "Medium (1-2 days)"
        else:
            return "Small (2-4 hours)"

    def _normalize_cycle(self, cycle: List[str]) -> tuple:
        """Normalize cycle to canonical form by rotating to start with minimum element.

        This preserves the directionality of the cycle (A->B->C is different from C->B->A)
        while ensuring the same cycle is always represented the same way.

        Args:
            cycle: List of file paths in the cycle

        Returns:
            Normalized tuple representation
        """
        if not cycle:
            return tuple()

        # Find the index of the minimum element
        min_idx = cycle.index(min(cycle))

        # Rotate the cycle to start with the minimum element
        rotated = cycle[min_idx:] + cycle[:min_idx]

        return tuple(rotated)

"""God class detector - finds overly complex classes."""

import uuid
from typing import List
from datetime import datetime

from falkor.detectors.base import CodeSmellDetector
from falkor.models import Finding, Severity


class GodClassDetector(CodeSmellDetector):
    """Detects god classes (classes with too many responsibilities)."""

    # Thresholds for god class detection
    HIGH_METHOD_COUNT = 20
    MEDIUM_METHOD_COUNT = 15
    HIGH_COMPLEXITY = 100
    MEDIUM_COMPLEXITY = 50
    HIGH_LOC = 500
    MEDIUM_LOC = 300
    HIGH_LCOM = 0.8  # Lack of cohesion (0-1, higher is worse)
    MEDIUM_LCOM = 0.6

    def detect(self) -> List[Finding]:
        """Find god classes in the codebase.

        A god class is identified by:
        - High number of methods (>20 methods)
        - High total complexity (>100)
        - High coupling (many outgoing calls)
        - Combination of moderate metrics

        Returns:
            List of findings for god classes
        """
        findings: List[Finding] = []

        query = """
        MATCH (c:Class)
        OPTIONAL MATCH (c)-[:CONTAINS]->(m:Function)
        OPTIONAL MATCH (m)-[:CALLS]->(called)
        OPTIONAL MATCH (c)-[:IMPORTS]->(imported:Class)
        WITH c,
             count(DISTINCT m) AS method_count,
             sum(COALESCE(m.complexity, 0)) AS total_complexity,
             count(DISTINCT called) + count(DISTINCT imported) AS coupling_count,
             COALESCE(c.lineEnd, 0) - COALESCE(c.lineStart, 0) AS loc
        WHERE method_count >= 10 OR total_complexity >= 30 OR loc >= 200
        OPTIONAL MATCH (file:File)-[:CONTAINS]->(c)
        RETURN c.qualifiedName AS qualified_name,
               c.name AS name,
               c.filePath AS file_path,
               c.lineStart AS line_start,
               c.lineEnd AS line_end,
               file.filePath AS containing_file,
               method_count,
               total_complexity,
               coupling_count,
               loc,
               c.is_abstract AS is_abstract
        ORDER BY method_count DESC, total_complexity DESC, loc DESC
        LIMIT 50
        """

        results = self.db.execute_query(query)

        for record in results:
            method_count = record["method_count"] or 0
            total_complexity = record["total_complexity"] or 0
            coupling_count = record["coupling_count"] or 0
            loc = record["loc"] or 0
            is_abstract = record.get("is_abstract", False)

            # Skip abstract base classes (they're often large by design)
            if is_abstract and method_count < 25:
                continue

            # Calculate LCOM (Lack of Cohesion of Methods)
            qualified_name = record["qualified_name"]
            lcom = self._calculate_lcom(qualified_name)

            # Calculate god class score
            is_god_class, reason = self._is_god_class(
                method_count, total_complexity, coupling_count, loc, lcom
            )

            if not is_god_class:
                continue

            name = record["name"]
            file_path = record["containing_file"] or record["file_path"]
            line_start = record["line_start"]
            line_end = record["line_end"]

            finding_id = str(uuid.uuid4())

            severity = self._calculate_severity(
                method_count, total_complexity, coupling_count, loc, lcom
            )

            finding = Finding(
                id=finding_id,
                detector="GodClassDetector",
                severity=severity,
                title=f"God class detected: {name}",
                description=(
                    f"Class '{name}' shows signs of being a god class: {reason}.\n\n"
                    f"Metrics:\n"
                    f"  - Methods: {method_count}\n"
                    f"  - Total complexity: {total_complexity}\n"
                    f"  - Coupling: {coupling_count}\n"
                    f"  - Lines of code: {loc}\n"
                    f"  - Lack of cohesion (LCOM): {lcom:.2f} (0=cohesive, 1=scattered)"
                ),
                affected_nodes=[qualified_name],
                affected_files=[file_path],
                graph_context={
                    "type": "god_class",
                    "name": name,
                    "method_count": method_count,
                    "total_complexity": total_complexity,
                    "coupling_count": coupling_count,
                    "loc": loc,
                    "lcom": lcom,
                    "line_start": line_start,
                    "line_end": line_end,
                },
                suggested_fix=self._suggest_refactoring(
                    name, method_count, total_complexity, coupling_count, loc, lcom
                ),
                estimated_effort=self._estimate_effort(method_count, total_complexity, loc),
                created_at=datetime.now(),
            )

            findings.append(finding)

        return findings

    def _is_god_class(
        self,
        method_count: int,
        total_complexity: int,
        coupling_count: int,
        loc: int,
        lcom: float,
    ) -> tuple[bool, str]:
        """Determine if metrics indicate a god class.

        Args:
            method_count: Number of methods
            total_complexity: Sum of all method complexities
            coupling_count: Number of outgoing calls and imports
            loc: Lines of code
            lcom: Lack of cohesion metric (0-1)

        Returns:
            Tuple of (is_god_class, reason_description)
        """
        reasons = []

        if method_count >= self.HIGH_METHOD_COUNT:
            reasons.append(f"very high method count ({method_count})")
        elif method_count >= self.MEDIUM_METHOD_COUNT:
            reasons.append(f"high method count ({method_count})")

        if total_complexity >= self.HIGH_COMPLEXITY:
            reasons.append(f"very high complexity ({total_complexity})")
        elif total_complexity >= self.MEDIUM_COMPLEXITY:
            reasons.append(f"high complexity ({total_complexity})")

        if coupling_count >= 50:
            reasons.append(f"very high coupling ({coupling_count})")
        elif coupling_count >= 30:
            reasons.append(f"high coupling ({coupling_count})")

        if loc >= self.HIGH_LOC:
            reasons.append(f"very large class ({loc} LOC)")
        elif loc >= self.MEDIUM_LOC:
            reasons.append(f"large class ({loc} LOC)")

        if lcom >= self.HIGH_LCOM:
            reasons.append(f"very low cohesion (LCOM: {lcom:.2f})")
        elif lcom >= self.MEDIUM_LCOM:
            reasons.append(f"low cohesion (LCOM: {lcom:.2f})")

        # God class if multiple moderate issues or one severe issue
        if len(reasons) >= 2:
            return True, ", ".join(reasons)
        elif method_count >= self.HIGH_METHOD_COUNT:
            return True, reasons[0] if reasons else "high method count"
        elif total_complexity >= self.HIGH_COMPLEXITY:
            return True, reasons[0] if reasons else "high complexity"
        elif loc >= self.HIGH_LOC:
            return True, reasons[0] if reasons else "very large class"

        return False, ""

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity based on metrics.

        Args:
            finding: Finding to assess

        Returns:
            Severity level
        """
        context = finding.graph_context
        method_count = context.get("method_count", 0)
        total_complexity = context.get("total_complexity", 0)
        coupling_count = context.get("coupling_count", 0)
        loc = context.get("loc", 0)
        lcom = context.get("lcom", 0.0)

        return self._calculate_severity(
            method_count, total_complexity, coupling_count, loc, lcom
        )

    def _calculate_severity(
        self,
        method_count: int,
        total_complexity: int,
        coupling_count: int,
        loc: int,
        lcom: float,
    ) -> Severity:
        """Calculate severity based on multiple metrics.

        Args:
            method_count: Number of methods
            total_complexity: Total complexity
            coupling_count: Coupling count
            loc: Lines of code
            lcom: Lack of cohesion metric

        Returns:
            Severity level
        """
        # Critical if multiple severe violations
        critical_count = sum([
            method_count >= 30,
            total_complexity >= 150,
            coupling_count >= 70,
            loc >= 1000,
            lcom >= self.HIGH_LCOM,
        ])

        if critical_count >= 2:
            return Severity.CRITICAL

        # High if one critical violation or multiple high violations
        high_count = sum([
            method_count >= self.HIGH_METHOD_COUNT,
            total_complexity >= self.HIGH_COMPLEXITY,
            coupling_count >= 50,
            loc >= self.HIGH_LOC,
            lcom >= self.MEDIUM_LCOM,
        ])

        if high_count >= 2:
            return Severity.HIGH

        # Medium for moderate violations
        medium_count = sum([
            method_count >= self.MEDIUM_METHOD_COUNT,
            total_complexity >= self.MEDIUM_COMPLEXITY,
            coupling_count >= 30,
            loc >= self.MEDIUM_LOC,
        ])

        if medium_count >= 2:
            return Severity.MEDIUM

        return Severity.LOW

    def _suggest_refactoring(
        self,
        name: str,
        method_count: int,
        total_complexity: int,
        coupling_count: int,
        loc: int,
        lcom: float,
    ) -> str:
        """Suggest refactoring strategies.

        Args:
            name: Class name
            method_count: Number of methods
            total_complexity: Total complexity
            coupling_count: Coupling count
            loc: Lines of code
            lcom: Lack of cohesion metric

        Returns:
            Refactoring suggestions
        """
        suggestions = [f"Refactor '{name}' to reduce its responsibilities:\n"]

        if method_count >= 20:
            suggestions.append(
                f"1. Extract related methods into separate classes\n"
                f"   - Look for method groups that work with the same data\n"
                f"   - Create focused classes with single responsibilities"
            )

        if total_complexity >= 100:
            suggestions.append(
                f"2. Simplify complex methods\n"
                f"   - Break down complex methods into smaller functions\n"
                f"   - Consider using the Strategy or Command pattern"
            )

        if coupling_count >= 50:
            suggestions.append(
                f"3. Reduce coupling\n"
                f"   - Apply dependency injection\n"
                f"   - Use interfaces to decouple dependencies\n"
                f"   - Consider facade or mediator patterns"
            )

        if loc >= self.HIGH_LOC:
            suggestions.append(
                f"4. Break down the large class ({loc} LOC)\n"
                f"   - Split into smaller, focused classes\n"
                f"   - Consider using composition over inheritance\n"
                f"   - Extract data classes for complex state"
            )

        if lcom >= self.MEDIUM_LCOM:
            suggestions.append(
                f"5. Improve cohesion (current LCOM: {lcom:.2f})\n"
                f"   - Group methods that use the same fields\n"
                f"   - Extract unrelated methods into separate classes\n"
                f"   - Consider using the Extract Class refactoring"
            )

        suggestions.append(
            f"\n6. Apply SOLID principles\n"
            f"   - Single Responsibility: Each class should have one reason to change\n"
            f"   - Open/Closed: Extend behavior without modifying existing code\n"
            f"   - Liskov Substitution: Use inheritance properly\n"
            f"   - Interface Segregation: Create specific interfaces\n"
            f"   - Dependency Inversion: Depend on abstractions"
        )

        return "".join(suggestions)

    def _estimate_effort(
        self, method_count: int, total_complexity: int, loc: int
    ) -> str:
        """Estimate refactoring effort.

        Args:
            method_count: Number of methods
            total_complexity: Total complexity
            loc: Lines of code

        Returns:
            Effort estimate
        """
        if method_count >= 30 or total_complexity >= 150 or loc >= 1000:
            return "Large (1-2 weeks)"
        elif method_count >= 20 or total_complexity >= 100 or loc >= 500:
            return "Medium (3-5 days)"
        else:
            return "Small (1-2 days)"

    def _calculate_lcom(self, qualified_name: str) -> float:
        """Calculate Lack of Cohesion of Methods (LCOM) metric.

        LCOM measures how well methods in a class work together. A value near 0
        indicates high cohesion (methods share fields), while a value near 1
        indicates low cohesion (methods work independently).

        This implements a simplified LCOM metric based on method-field relationships.

        Args:
            qualified_name: Qualified name of the class

        Returns:
            LCOM score between 0 (cohesive) and 1 (scattered)
        """
        # Query to get method-field usage patterns
        query = """
        MATCH (c:Class {qualifiedName: $qualified_name})-[:CONTAINS]->(m:Function)
        OPTIONAL MATCH (m)-[:USES]->(field)
        WHERE field:Variable OR field:Attribute
        WITH m, collect(DISTINCT field.name) AS fields
        RETURN collect({method: m.name, fields: fields}) AS method_field_pairs,
               count(m) AS method_count
        """

        try:
            result = self.db.execute_query(query, {"qualified_name": qualified_name})
            if not result:
                return 0.0

            record = result[0]
            method_field_pairs = record.get("method_field_pairs", [])
            method_count = record.get("method_count", 0)

            if method_count <= 1:
                return 0.0  # Single method is perfectly cohesive

            # Count pairs of methods that share no fields
            non_sharing_pairs = 0
            total_pairs = 0

            for i, pair1 in enumerate(method_field_pairs):
                fields1 = set(pair1.get("fields", []))
                for pair2 in method_field_pairs[i + 1 :]:
                    fields2 = set(pair2.get("fields", []))
                    total_pairs += 1

                    # If methods share no fields, they lack cohesion
                    if not fields1.intersection(fields2):
                        non_sharing_pairs += 1

            if total_pairs == 0:
                return 0.0

            # Return ratio of non-sharing pairs (0 = cohesive, 1 = scattered)
            return non_sharing_pairs / total_pairs

        except Exception as e:
            # If LCOM calculation fails, return neutral value
            return 0.5

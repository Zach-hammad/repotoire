"""Type Hint Coverage detector - identifies functions with missing type annotations (REPO-229).

Detects functions and methods that lack proper type hints, which reduces
code maintainability, IDE support, and static analysis effectiveness.

Leverages parameter_types and return_type properties captured by the parser.
"""

import uuid
from datetime import datetime
from typing import Dict, List, Optional

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph.base import DatabaseClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.models import CollaborationMetadata, Finding, Severity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class TypeHintCoverageDetector(CodeSmellDetector):
    """Detects functions with missing or incomplete type hints.

    Type hints improve:
    - Code documentation and readability
    - IDE auto-completion and navigation
    - Static analysis with tools like mypy
    - Refactoring safety

    This detector identifies:
    - Functions with no parameter type hints
    - Functions with no return type hints
    - Partial type hint coverage (some params typed, others not)
    """

    THRESHOLDS = {
        "min_params_for_warning": 1,  # Warn if function has params but no types
        "min_complexity_for_high": 10,  # HIGH severity if complex AND untyped
        "min_public_methods_untyped": 3,  # File-level warning threshold
    }

    # Functions/methods that commonly don't need return type hints
    NO_RETURN_NEEDED = {
        "__init__",
        "__del__",
        "__setattr__",
        "__delattr__",
        "setUp",
        "tearDown",
        "setUpClass",
        "tearDownClass",
    }

    # Parameters that don't need type hints (handled by convention)
    SKIP_PARAMS = {"self", "cls", "*args", "**kwargs", "args", "kwargs"}

    def __init__(
        self,
        neo4j_client: DatabaseClient,
        detector_config: Optional[dict] = None,
        enricher: Optional[GraphEnricher] = None
    ):
        """Initialize type hint coverage detector.

        Args:
            neo4j_client: Neo4j database client
            detector_config: Optional detector configuration
            enricher: Optional GraphEnricher for cross-detector collaboration
        """
        super().__init__(neo4j_client)
        self.enricher = enricher
        self.is_falkordb = type(neo4j_client).__name__ == "FalkorDBClient"

        # Allow config to override thresholds
        config = detector_config or {}
        self.min_params = config.get(
            "min_params_for_warning",
            self.THRESHOLDS["min_params_for_warning"]
        )
        self.min_complexity_for_high = config.get(
            "min_complexity_for_high",
            self.THRESHOLDS["min_complexity_for_high"]
        )

    def detect(self) -> List[Finding]:
        """Detect functions with missing type hints.

        Returns:
            List of findings for type hint coverage issues
        """
        logger.info("Running TypeHintCoverageDetector")
        findings: List[Finding] = []

        # Find functions with missing type hints
        missing_hints = self._find_functions_missing_hints()
        findings.extend(missing_hints)
        logger.debug(f"Found {len(missing_hints)} functions with missing type hints")

        # Find files with low type hint coverage
        file_coverage = self._analyze_file_coverage()
        findings.extend(file_coverage)
        logger.debug(f"Found {len(file_coverage)} files with low type hint coverage")

        logger.info(f"Found {len(findings)} type hint coverage issue(s)")
        return findings

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity based on complexity and coverage.

        Args:
            finding: Finding to assess

        Returns:
            Severity level
        """
        coverage_type = finding.graph_context.get("coverage_type", "")
        complexity = finding.graph_context.get("complexity", 0)

        if coverage_type == "file_coverage":
            coverage_pct = finding.graph_context.get("typed_percentage", 100)
            if coverage_pct < 25:
                return Severity.HIGH
            elif coverage_pct < 50:
                return Severity.MEDIUM
            return Severity.LOW

        # Function-level severity
        if complexity >= self.min_complexity_for_high:
            return Severity.HIGH
        elif finding.graph_context.get("is_public", False):
            return Severity.MEDIUM
        return Severity.LOW

    def _find_functions_missing_hints(self) -> List[Finding]:
        """Find functions with missing parameter or return type hints.

        Returns:
            List of findings for functions with missing type hints
        """
        findings: List[Finding] = []

        # Query for functions with parameters but missing type info
        query = """
        MATCH (f:Function)
        WHERE f.parameters IS NOT NULL
          AND size(f.parameters) > 0
        OPTIONAL MATCH (file:File)-[:CONTAINS*]->(f)
        WITH f, file,
             f.parameters AS params,
             COALESCE(f.parameter_types, {}) AS param_types,
             f.return_type AS return_type
        RETURN f.qualifiedName AS func_name,
               f.name AS func_simple_name,
               f.filePath AS func_file,
               f.lineStart AS func_line,
               f.complexity AS complexity,
               f.is_method AS is_method,
               file.filePath AS containing_file,
               params,
               param_types,
               return_type
        ORDER BY f.complexity DESC
        LIMIT 200
        """

        results = self.db.execute_query(query)

        for record in results:
            func_name = record.get("func_name", "")
            func_simple_name = record.get("func_simple_name", "")

            if not func_name:
                continue

            # Skip test functions
            if func_simple_name.startswith("test_"):
                continue

            # Analyze type hint coverage for this function
            params = record.get("params") or []
            param_types = record.get("param_types") or {}
            return_type = record.get("return_type")

            # Count parameters that should have type hints
            meaningful_params = self._get_meaningful_params(params)
            typed_params = self._count_typed_params(meaningful_params, param_types)

            # Determine what's missing
            missing_param_hints = len(meaningful_params) - typed_params
            missing_return = (
                return_type is None
                and func_simple_name not in self.NO_RETURN_NEEDED
            )

            # Skip if fully typed
            if missing_param_hints == 0 and not missing_return:
                continue

            # Skip if no meaningful parameters and no missing return
            if len(meaningful_params) == 0 and not missing_return:
                continue

            finding = self._create_function_finding(
                record,
                meaningful_params,
                typed_params,
                missing_return
            )
            findings.append(finding)

        return findings

    def _get_meaningful_params(self, params: List) -> List[str]:
        """Extract parameter names that should have type hints.

        Args:
            params: List of parameters (strings or dicts)

        Returns:
            List of parameter names that need type hints
        """
        meaningful = []
        for p in params:
            if isinstance(p, dict):
                name = p.get("name", "")
            else:
                name = str(p)

            # Skip self, cls, *args, **kwargs
            if name not in self.SKIP_PARAMS and not name.startswith("*"):
                meaningful.append(name)

        return meaningful

    def _count_typed_params(
        self,
        params: List[str],
        param_types: Dict
    ) -> int:
        """Count how many parameters have type hints.

        Args:
            params: List of parameter names
            param_types: Dict mapping param names to types

        Returns:
            Number of typed parameters
        """
        typed = 0
        for param in params:
            if param in param_types and param_types[param]:
                typed += 1
        return typed

    def _create_function_finding(
        self,
        record: Dict,
        meaningful_params: List[str],
        typed_params: int,
        missing_return: bool
    ) -> Finding:
        """Create a finding for a function with missing type hints.

        Args:
            record: Query result record
            meaningful_params: List of params needing type hints
            typed_params: Number of params with type hints
            missing_return: Whether return type is missing

        Returns:
            Finding object
        """
        finding_id = str(uuid.uuid4())
        func_name = record.get("func_name", "")
        func_simple_name = record.get("func_simple_name", "")
        file_path = record.get("containing_file") or record.get("func_file", "")
        complexity = record.get("complexity", 0)
        is_method = record.get("is_method", False)

        # Calculate coverage percentage
        total_items = len(meaningful_params) + (1 if func_simple_name not in self.NO_RETURN_NEEDED else 0)
        typed_items = typed_params + (0 if missing_return else 1)
        coverage_pct = (typed_items / total_items * 100) if total_items > 0 else 100

        # Determine if public (doesn't start with _)
        is_public = not func_simple_name.startswith("_")

        # Build description
        missing_parts = []
        if typed_params < len(meaningful_params):
            missing_count = len(meaningful_params) - typed_params
            missing_parts.append(f"{missing_count} parameter(s) missing type hints")
        if missing_return:
            missing_parts.append("return type missing")

        description = (
            f"Function `{func_simple_name}` has incomplete type hints: "
            f"{', '.join(missing_parts)}.\n\n"
            f"**Coverage**: {coverage_pct:.0f}% ({typed_items}/{total_items} type hints)\n"
            f"**Complexity**: {complexity}\n"
            f"**Public API**: {'Yes' if is_public else 'No'}\n\n"
            "Type hints improve code documentation, IDE support, and enable "
            "static analysis with tools like mypy."
        )

        # Determine severity
        if complexity >= self.min_complexity_for_high and coverage_pct < 50:
            severity = Severity.HIGH
        elif is_public and coverage_pct < 50:
            severity = Severity.MEDIUM
        else:
            severity = Severity.LOW

        # Build suggestion
        suggestion = self._generate_type_hint_suggestion(
            func_simple_name,
            meaningful_params,
            record.get("param_types", {}),
            missing_return
        )

        finding = Finding(
            id=finding_id,
            detector="TypeHintCoverageDetector",
            severity=severity,
            title=f"Missing type hints: {func_simple_name}",
            description=description,
            affected_nodes=[func_name],
            affected_files=[file_path] if file_path else [],
            line_start=record.get("func_line"),
            graph_context={
                "coverage_type": "function_coverage",
                "function_name": func_simple_name,
                "complexity": complexity,
                "is_method": is_method,
                "is_public": is_public,
                "total_params": len(meaningful_params),
                "typed_params": typed_params,
                "missing_return": missing_return,
                "typed_percentage": coverage_pct,
            },
            suggested_fix=suggestion,
            estimated_effort="Small (15-30 minutes)",
            created_at=datetime.now(),
        )

        # Add collaboration metadata
        confidence = 0.95  # High confidence - objective measurement
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="TypeHintCoverageDetector",
            confidence=confidence,
            evidence=[
                "missing_type_hints",
                f"coverage_{int(coverage_pct)}pct",
                "public_api" if is_public else "private"
            ],
            tags=["type_hints", "code_quality", "documentation"]
        ))

        # Flag entity for cross-detector collaboration
        if self.enricher:
            try:
                self.enricher.flag_entity(
                    entity_qualified_name=func_name,
                    detector="TypeHintCoverageDetector",
                    severity=severity.value,
                    issues=["missing_type_hints"],
                    confidence=confidence,
                    metadata={
                        "typed_percentage": coverage_pct,
                        "complexity": complexity
                    }
                )
            except Exception:
                pass

        return finding

    def _generate_type_hint_suggestion(
        self,
        func_name: str,
        params: List[str],
        param_types: Dict,
        missing_return: bool
    ) -> str:
        """Generate a suggestion for adding type hints.

        Args:
            func_name: Function name
            params: List of parameter names
            param_types: Existing type hints
            missing_return: Whether return type is missing

        Returns:
            Suggestion string with example
        """
        lines = ["Add type hints to improve code quality:\n"]

        # Build example signature
        param_parts = []
        for p in params:
            existing_type = param_types.get(p)
            if existing_type:
                param_parts.append(f"{p}: {existing_type}")
            else:
                param_parts.append(f"{p}: <type>")

        params_str = ", ".join(param_parts)
        return_str = " -> <return_type>" if missing_return else ""

        lines.append(f"```python")
        lines.append(f"def {func_name}({params_str}){return_str}:")
        lines.append(f"```")
        lines.append("")
        lines.append("Common type hints:")
        lines.append("- `str`, `int`, `float`, `bool` for primitives")
        lines.append("- `List[T]`, `Dict[K, V]`, `Set[T]` for collections")
        lines.append("- `Optional[T]` for nullable values")
        lines.append("- `Any` when type is truly dynamic")
        lines.append("- `None` for functions that don't return a value")

        return "\n".join(lines)

    def _analyze_file_coverage(self) -> List[Finding]:
        """Analyze type hint coverage at the file level.

        Returns:
            List of findings for files with low coverage
        """
        findings: List[Finding] = []

        # Query for file-level type hint coverage
        query = """
        MATCH (file:File)-[:CONTAINS]->(f:Function)
        WHERE file.language = 'python'
          AND NOT file.is_test = true
          AND NOT file.filePath CONTAINS '/tests/'
          AND NOT file.filePath CONTAINS 'test_'
        WITH file, f,
             CASE
               WHEN f.return_type IS NOT NULL THEN 1
               ELSE 0
             END AS has_return_type,
             CASE
               WHEN size(keys(COALESCE(f.parameter_types, {}))) > 0 THEN 1
               ELSE 0
             END AS has_param_types
        WITH file,
             count(f) AS total_functions,
             sum(has_return_type) AS typed_returns,
             sum(has_param_types) AS typed_params,
             sum(CASE WHEN has_return_type = 1 AND has_param_types = 1 THEN 1 ELSE 0 END) AS fully_typed
        WHERE total_functions >= 3
        RETURN file.filePath AS file_path,
               total_functions,
               typed_returns,
               typed_params,
               fully_typed,
               toFloat(fully_typed) / total_functions * 100 AS coverage_pct
        ORDER BY coverage_pct ASC
        LIMIT 20
        """

        results = self.db.execute_query(query)

        for record in results:
            coverage_pct = record.get("coverage_pct", 100)

            # Only report files with less than 50% coverage
            if coverage_pct >= 50:
                continue

            finding = self._create_file_finding(record)
            findings.append(finding)

        return findings

    def _create_file_finding(self, record: Dict) -> Finding:
        """Create a finding for a file with low type hint coverage.

        Args:
            record: Query result record

        Returns:
            Finding object
        """
        finding_id = str(uuid.uuid4())
        file_path = record.get("file_path", "")
        total_functions = record.get("total_functions", 0)
        fully_typed = record.get("fully_typed", 0)
        coverage_pct = record.get("coverage_pct", 0)

        description = (
            f"File has low type hint coverage: {coverage_pct:.0f}% "
            f"({fully_typed}/{total_functions} functions fully typed).\n\n"
            "Consider adding type hints to improve code quality and enable "
            "static analysis. Start with public functions and complex logic."
        )

        # Determine severity based on coverage
        if coverage_pct < 25:
            severity = Severity.HIGH
        elif coverage_pct < 50:
            severity = Severity.MEDIUM
        else:
            severity = Severity.LOW

        finding = Finding(
            id=finding_id,
            detector="TypeHintCoverageDetector",
            severity=severity,
            title=f"Low type hint coverage: {file_path.split('/')[-1]} ({coverage_pct:.0f}%)",
            description=description,
            affected_nodes=[file_path],
            affected_files=[file_path],
            graph_context={
                "coverage_type": "file_coverage",
                "total_functions": total_functions,
                "fully_typed": fully_typed,
                "typed_percentage": coverage_pct,
            },
            suggested_fix=(
                "Prioritize type hints for:\n"
                "1. Public API functions (no leading underscore)\n"
                "2. Complex functions (high cyclomatic complexity)\n"
                "3. Functions with many parameters\n"
                "4. Entry points (main, CLI commands)\n\n"
                "Use mypy with --strict to catch missing hints."
            ),
            estimated_effort=self._estimate_file_effort(total_functions - fully_typed),
            created_at=datetime.now(),
        )

        # Add collaboration metadata
        confidence = 0.90
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="TypeHintCoverageDetector",
            confidence=confidence,
            evidence=[
                "low_file_coverage",
                f"coverage_{int(coverage_pct)}pct",
                f"functions_{total_functions}"
            ],
            tags=["type_hints", "code_quality", "file_level"]
        ))

        return finding

    def _estimate_file_effort(self, untyped_count: int) -> str:
        """Estimate effort to add type hints to a file.

        Args:
            untyped_count: Number of untyped functions

        Returns:
            Effort estimate string
        """
        if untyped_count >= 20:
            return "Large (1-2 days)"
        elif untyped_count >= 10:
            return "Medium (4-8 hours)"
        elif untyped_count >= 5:
            return "Small (2-4 hours)"
        else:
            return "Small (1 hour)"

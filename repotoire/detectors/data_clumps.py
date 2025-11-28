"""Data Clumps detector - identifies parameter groups for extraction (REPO-216).

Data clumps are groups of parameters that frequently appear together across
multiple functions. This indicates a missing abstraction that should be
extracted into a dataclass or named tuple.
"""

import uuid
from collections import Counter
from datetime import datetime
from itertools import combinations
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph.base import DatabaseClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.models import CollaborationMetadata, Finding, Severity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class DataClumpsDetector(CodeSmellDetector):
    """Detects groups of parameters that frequently appear together.

    Data clumps indicate missing abstractions - parameters that travel
    together should often be extracted into a dataclass or named tuple.

    Example finding:
        Parameters (first_name, last_name, email) appear together in 6 functions.
        Suggestion: Extract to dataclass 'PersonInfo' or 'UserDetails'.
    """

    THRESHOLDS = {
        "min_params": 3,        # Minimum params to form a clump
        "min_occurrences": 4,   # Minimum functions sharing the clump
    }

    # Common parameter patterns to suggest class names
    NAME_PATTERNS: Dict[FrozenSet[str], str] = {
        frozenset({"x", "y"}): "Point",
        frozenset({"x", "y", "z"}): "Point3D",
        frozenset({"width", "height"}): "Size",
        frozenset({"start", "end"}): "Range",
        frozenset({"host", "port"}): "Address",
        frozenset({"first_name", "last_name"}): "Name",
        frozenset({"first_name", "last_name", "email"}): "PersonInfo",
        frozenset({"latitude", "longitude"}): "Coordinates",
        frozenset({"lat", "lng"}): "Coordinates",
        frozenset({"lat", "lon"}): "Coordinates",
        frozenset({"red", "green", "blue"}): "RGB",
        frozenset({"r", "g", "b"}): "RGB",
        frozenset({"min", "max"}): "Range",
        frozenset({"start_date", "end_date"}): "DateRange",
        frozenset({"username", "password"}): "Credentials",
        frozenset({"user", "password"}): "Credentials",
        frozenset({"path", "filename"}): "FilePath",
        frozenset({"name", "email"}): "Contact",
        frozenset({"name", "email", "phone"}): "Contact",
    }

    def __init__(
        self,
        neo4j_client: DatabaseClient,
        detector_config: Optional[dict] = None,
        enricher: Optional[GraphEnricher] = None
    ):
        """Initialize data clumps detector.

        Args:
            neo4j_client: Neo4j database client
            detector_config: Optional detector configuration
            enricher: Optional GraphEnricher for cross-detector collaboration
        """
        super().__init__(neo4j_client)
        self.enricher = enricher
        # FalkorDB uses id() while Neo4j uses elementId()
        self.is_falkordb = type(neo4j_client).__name__ == "FalkorDBClient"

        # Allow config to override thresholds
        config = detector_config or {}
        self.min_params = config.get("min_params", self.THRESHOLDS["min_params"])
        self.min_occurrences = config.get("min_occurrences", self.THRESHOLDS["min_occurrences"])

    def detect(self) -> List[Finding]:
        """Detect data clumps across the codebase.

        Returns:
            List of findings for detected data clumps
        """
        logger.info("Running DataClumpsDetector")

        # Get all functions with their parameters
        functions_params = self._get_function_parameters()

        if not functions_params:
            logger.info("No functions with sufficient parameters found")
            return []

        logger.debug(f"Found {len(functions_params)} functions with {self.min_params}+ parameters")

        # Find parameter clumps
        clumps = self._find_clumps(functions_params)

        # Convert to findings
        findings = []
        for param_set, functions in clumps:
            severity = Severity.HIGH if len(functions) >= 7 else Severity.MEDIUM
            finding = self._create_finding(param_set, functions, severity)
            findings.append(finding)

        logger.info(f"Found {len(findings)} data clump(s)")
        return findings

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity based on function count.

        Args:
            finding: Finding to assess

        Returns:
            Severity level
        """
        function_count = finding.graph_context.get("function_count", 0)
        if function_count >= 7:
            return Severity.HIGH
        return Severity.MEDIUM

    def _get_function_parameters(self) -> List[Tuple[str, Set[str], Optional[str]]]:
        """Get all functions with their parameter names.

        Returns:
            List of tuples: (function_name, parameter_set, file_path)
        """
        query = """
        MATCH (f:Function)
        WHERE f.parameters IS NOT NULL
          AND size(f.parameters) >= $min_params
        OPTIONAL MATCH (file:File)-[:CONTAINS*]->(f)
        RETURN f.qualifiedName AS name, f.parameters AS params, file.filePath AS filePath
        """

        results = self.db.execute_query(query, {
            "min_params": self.min_params
        })

        functions = []
        for row in results:
            params = row.get("params", [])
            if isinstance(params, list):
                # Extract parameter names from stored format
                # Params might be dicts with 'name' key or just strings
                param_names = set()
                for p in params:
                    if isinstance(p, dict):
                        name = p.get("name", "")
                        # Skip self, cls, *args, **kwargs
                        if name and name not in ("self", "cls") and not name.startswith("*"):
                            param_names.add(name)
                    elif isinstance(p, str):
                        if p not in ("self", "cls") and not p.startswith("*"):
                            param_names.add(p)

                if len(param_names) >= self.min_params:
                    functions.append((
                        row["name"],
                        param_names,
                        row.get("filePath")
                    ))

        return functions

    def _find_clumps(
        self,
        functions_params: List[Tuple[str, Set[str], Optional[str]]]
    ) -> List[Tuple[Set[str], Set[str]]]:
        """Find parameter sets that appear in multiple functions.

        Args:
            functions_params: List of (function_name, param_set, file_path) tuples

        Returns:
            List of (parameter_set, function_names) tuples meeting threshold
        """
        # Count occurrences of each parameter combination
        param_to_functions: Dict[FrozenSet[str], Set[str]] = {}

        for func_name, params, _ in functions_params:
            param_list = list(params)
            # Check all subsets of size >= min_params
            for size in range(self.min_params, len(param_list) + 1):
                for combo in combinations(param_list, size):
                    key = frozenset(combo)
                    if key not in param_to_functions:
                        param_to_functions[key] = set()
                    param_to_functions[key].add(func_name)

        # Filter to clumps meeting threshold
        clumps = []
        for param_set, functions in param_to_functions.items():
            if len(functions) >= self.min_occurrences:
                clumps.append((set(param_set), functions))

        # Remove subsets (if {a,b,c} is a clump, don't also report {a,b})
        clumps = self._remove_subsets(clumps)

        # Sort by function count (descending), then by param count (descending)
        clumps.sort(key=lambda x: (len(x[1]), len(x[0])), reverse=True)

        return clumps

    def _remove_subsets(
        self,
        clumps: List[Tuple[Set[str], Set[str]]]
    ) -> List[Tuple[Set[str], Set[str]]]:
        """Remove clumps that are subsets of larger clumps with same functions.

        If {a,b,c} appears in the same functions as {a,b}, we only report
        the larger clump {a,b,c}.

        Args:
            clumps: List of (param_set, function_set) tuples

        Returns:
            Filtered list without redundant subsets
        """
        result = []
        # Sort by param set size descending so we process largest first
        clumps_sorted = sorted(clumps, key=lambda x: len(x[0]), reverse=True)

        for param_set, functions in clumps_sorted:
            is_subset = False
            for existing_params, existing_funcs in result:
                # If this param set is a subset of existing, and functions overlap significantly
                if param_set < existing_params and functions <= existing_funcs:
                    is_subset = True
                    break

            if not is_subset:
                result.append((param_set, functions))

        return result

    def _create_finding(
        self,
        param_set: Set[str],
        functions: Set[str],
        severity: Severity
    ) -> Finding:
        """Create a finding for a data clump.

        Args:
            param_set: Set of parameter names that form the clump
            functions: Set of function qualified names that share the clump
            severity: Severity level for the finding

        Returns:
            Finding object
        """
        finding_id = str(uuid.uuid4())
        params_display = ", ".join(sorted(param_set))

        # Get file paths from function names
        file_paths = self._get_file_paths_for_functions(functions)

        # Generate suggestion with dataclass template
        suggestion = self._generate_suggestion(param_set)

        description = (
            f"Parameters ({params_display}) appear together in {len(functions)} functions. "
            f"This data clump suggests a missing abstraction that should be extracted "
            f"into a dataclass or named tuple to reduce parameter passing and improve "
            f"code maintainability."
        )

        # List affected functions (truncated if too many)
        func_list = sorted(functions)
        if len(func_list) > 5:
            func_display = ", ".join(func_list[:5]) + f" ... and {len(func_list) - 5} more"
        else:
            func_display = ", ".join(func_list)

        description += f"\n\nAffected functions: {func_display}"

        finding = Finding(
            id=finding_id,
            detector="DataClumpsDetector",
            severity=severity,
            title=f"Data clump: ({params_display})",
            description=description,
            affected_nodes=list(functions),
            affected_files=file_paths,
            graph_context={
                "parameter_count": len(param_set),
                "function_count": len(functions),
                "parameters": sorted(param_set),
                "functions": sorted(functions),
            },
            suggested_fix=suggestion,
            estimated_effort=self._estimate_effort(len(functions)),
            created_at=datetime.now(),
        )

        # Add collaboration metadata for cross-detector support
        confidence = 0.85  # High confidence based on parameter matching
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="DataClumpsDetector",
            confidence=confidence,
            evidence=["parameter_clump", f"shared_by_{len(functions)}_functions"],
            tags=["data_clump", "refactoring", "abstraction"]
        ))

        # Flag entities for cross-detector collaboration
        if self.enricher:
            for func_name in functions:
                try:
                    self.enricher.flag_entity(
                        entity_qualified_name=func_name,
                        detector="DataClumpsDetector",
                        severity=severity.value,
                        issues=["data_clump"],
                        confidence=confidence,
                        metadata={
                            "clump_parameters": sorted(param_set),
                            "function_count": len(functions)
                        }
                    )
                except Exception:
                    pass  # Don't fail detection if enrichment fails

        return finding

    def _get_file_paths_for_functions(self, functions: Set[str]) -> List[str]:
        """Get file paths for a set of functions.

        Args:
            functions: Set of function qualified names

        Returns:
            List of unique file paths
        """
        if not functions:
            return []

        query = """
        MATCH (file:File)-[:CONTAINS*]->(f:Function)
        WHERE f.qualifiedName IN $functions
        RETURN DISTINCT file.filePath AS filePath
        """

        results = self.db.execute_query(query, {"functions": list(functions)})
        return [r["filePath"] for r in results if r.get("filePath")]

    def _generate_suggestion(self, param_set: Set[str]) -> str:
        """Generate a refactoring suggestion with class name.

        Args:
            param_set: Set of parameter names

        Returns:
            Refactoring suggestion with dataclass template
        """
        frozen = frozenset(param_set)

        # Check known patterns
        class_name = self.NAME_PATTERNS.get(frozen)

        if not class_name:
            # Check if param_set is a superset of a known pattern
            for pattern_set, pattern_name in self.NAME_PATTERNS.items():
                if pattern_set <= frozen:
                    class_name = pattern_name
                    break

        if not class_name:
            # Generate name from parameters
            class_name = self._suggest_class_name(param_set)

        # Build dataclass template
        params_sorted = sorted(param_set)
        fields = "\n".join(f"    {p}: Any  # TODO: add correct type" for p in params_sorted)

        return (
            f"Extract into dataclass:\n\n"
            f"from dataclasses import dataclass\n"
            f"from typing import Any\n\n"
            f"@dataclass\n"
            f"class {class_name}:\n"
            f'    """TODO: Add description for {class_name}."""\n'
            f"{fields}\n\n"
            f"Then refactor functions to accept a single {class_name} parameter "
            f"instead of {len(param_set)} separate parameters."
        )

    def _suggest_class_name(self, param_set: Set[str]) -> str:
        """Suggest a class name based on parameter names.

        Args:
            param_set: Set of parameter names

        Returns:
            Suggested class name
        """
        # Common suffixes that indicate a grouping
        words = []
        for param in param_set:
            # Split on underscore and take meaningful parts
            parts = param.replace("_", " ").split()
            words.extend(parts)

        # Find common themes
        word_counts = Counter(words)
        common = word_counts.most_common(2)

        if common:
            # Use the most common word(s), filtering out very short ones
            name_parts = [w[0].title() for w in common if len(w[0]) > 2]
            if name_parts:
                return "".join(name_parts[:2]) + "Data"

        # Fallback: use first param's meaningful part
        first = sorted(param_set)[0]
        name_base = first.replace("_", " ").title().replace(" ", "")
        return name_base + "Info"

    def _estimate_effort(self, function_count: int) -> str:
        """Estimate effort to fix based on number of affected functions.

        Args:
            function_count: Number of functions to refactor

        Returns:
            Effort estimate string
        """
        if function_count >= 10:
            return "Large (1-2 days)"
        elif function_count >= 6:
            return "Medium (4-8 hours)"
        else:
            return "Small (1-4 hours)"

"""Long Parameter List detector - identifies functions with too many parameters (REPO-231).

Long parameter lists are a code smell indicating:
1. The function is doing too much (violates SRP)
2. Related parameters should be grouped into objects
3. The function has poor API design

This detector complements DataClumpsDetector by focusing on individual functions
rather than patterns across functions.
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


class LongParameterListDetector(CodeSmellDetector):
    """Detects functions with too many parameters.

    Long parameter lists:
    - Make functions hard to call correctly
    - Indicate the function may be doing too much
    - Often signal missing abstractions (parameter objects)
    - Reduce code readability and maintainability

    Configurable thresholds allow tuning for different coding styles.
    """

    THRESHOLDS = {
        "max_params": 5,          # Standard: more than 5 is suspicious
        "critical_params": 10,    # More than 10 is almost always problematic
        "high_params": 7,         # More than 7 is usually problematic
    }

    # Parameters that don't count toward the limit
    SKIP_PARAMS = {"self", "cls"}

    def __init__(
        self,
        neo4j_client: DatabaseClient,
        detector_config: Optional[dict] = None,
        enricher: Optional[GraphEnricher] = None
    ):
        """Initialize long parameter list detector.

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
        self.max_params = config.get("max_params", self.THRESHOLDS["max_params"])
        self.critical_params = config.get("critical_params", self.THRESHOLDS["critical_params"])
        self.high_params = config.get("high_params", self.THRESHOLDS["high_params"])

    def detect(self) -> List[Finding]:
        """Detect functions with long parameter lists.

        Returns:
            List of findings for functions with too many parameters
        """
        logger.info("Running LongParameterListDetector")
        findings: List[Finding] = []

        # Find functions with too many parameters
        long_param_findings = self._find_long_parameter_lists()
        findings.extend(long_param_findings)

        logger.info(f"Found {len(findings)} long parameter list issue(s)")
        return findings

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity based on parameter count.

        Args:
            finding: Finding to assess

        Returns:
            Severity level
        """
        param_count = finding.graph_context.get("param_count", 0)
        return self._calculate_severity(param_count)

    def _calculate_severity(self, param_count: int) -> Severity:
        """Calculate severity based on parameter count.

        Args:
            param_count: Number of parameters

        Returns:
            Severity level
        """
        if param_count >= self.critical_params:
            return Severity.CRITICAL
        elif param_count >= self.high_params:
            return Severity.HIGH
        elif param_count > self.max_params:
            return Severity.MEDIUM
        return Severity.LOW

    def _find_long_parameter_lists(self) -> List[Finding]:
        """Find functions with more parameters than threshold.

        Returns:
            List of findings for functions with long parameter lists
        """
        findings: List[Finding] = []

        # Query for functions with many parameters
        query = """
        MATCH (f:Function)
        WHERE f.parameters IS NOT NULL
          AND size(f.parameters) > $max_params
        OPTIONAL MATCH (file:File)-[:CONTAINS*]->(f)
        OPTIONAL MATCH (c:Class)-[:CONTAINS]->(f)
        RETURN f.qualifiedName AS func_name,
               f.name AS func_simple_name,
               f.filePath AS func_file,
               f.lineStart AS func_line,
               f.complexity AS complexity,
               f.is_method AS is_method,
               f.parameters AS params,
               file.filePath AS containing_file,
               c.name AS class_name
        ORDER BY size(f.parameters) DESC
        LIMIT 100
        """

        results = self.db.execute_query(query, {"max_params": self.max_params})

        for record in results:
            func_name = record.get("func_name", "")
            if not func_name:
                continue

            # Get meaningful parameter count (excluding self, cls)
            params = record.get("params") or []
            meaningful_params = self._get_meaningful_params(params)
            param_count = len(meaningful_params)

            # Skip if under threshold after filtering self/cls
            if param_count <= self.max_params:
                continue

            finding = self._create_finding(record, meaningful_params, param_count)
            findings.append(finding)

        return findings

    def _get_meaningful_params(self, params: List) -> List[str]:
        """Extract parameter names excluding self/cls.

        Args:
            params: List of parameters (strings or dicts)

        Returns:
            List of meaningful parameter names
        """
        meaningful = []
        for p in params:
            if isinstance(p, dict):
                name = p.get("name", "")
            else:
                name = str(p)

            # Skip self, cls
            if name and name not in self.SKIP_PARAMS:
                meaningful.append(name)

        return meaningful

    def _create_finding(
        self,
        record: Dict,
        params: List[str],
        param_count: int
    ) -> Finding:
        """Create a finding for a function with long parameter list.

        Args:
            record: Query result record
            params: List of meaningful parameter names
            param_count: Count of meaningful parameters

        Returns:
            Finding object
        """
        finding_id = str(uuid.uuid4())
        func_name = record.get("func_name", "")
        func_simple_name = record.get("func_simple_name", "")
        file_path = record.get("containing_file") or record.get("func_file", "")
        complexity = record.get("complexity", 0)
        is_method = record.get("is_method", False)
        class_name = record.get("class_name")

        # Calculate severity
        severity = self._calculate_severity(param_count)

        # Format parameters for display
        params_display = ", ".join(params[:8])
        if len(params) > 8:
            params_display += f", ... ({len(params)} total)"

        # Build description
        description = (
            f"Function `{func_simple_name}` has {param_count} parameters: "
            f"`{params_display}`\n\n"
            f"**Threshold**: >{self.max_params} parameters\n"
            f"**Complexity**: {complexity}\n\n"
        )

        if param_count >= self.critical_params:
            description += (
                "This is a critical issue. Such long parameter lists:\n"
                "- Are nearly impossible to use correctly\n"
                "- Indicate the function is doing way too much\n"
                "- Should be split into multiple smaller functions"
            )
        elif param_count >= self.high_params:
            description += (
                "Consider refactoring to:\n"
                "- Group related parameters into a data class\n"
                "- Split the function into smaller functions\n"
                "- Use the Builder pattern for complex construction"
            )
        else:
            description += (
                "Consider whether some parameters can be grouped "
                "into a single configuration object or data class."
            )

        # Generate suggestion with example
        suggestion = self._generate_suggestion(
            func_simple_name,
            params,
            is_method,
            class_name
        )

        finding = Finding(
            id=finding_id,
            detector="LongParameterListDetector",
            severity=severity,
            title=f"Long parameter list: {func_simple_name} ({param_count} params)",
            description=description,
            affected_nodes=[func_name],
            affected_files=[file_path] if file_path else [],
            line_start=record.get("func_line"),
            graph_context={
                "function_name": func_simple_name,
                "param_count": param_count,
                "parameters": params,
                "complexity": complexity,
                "is_method": is_method,
                "class_name": class_name,
            },
            suggested_fix=suggestion,
            estimated_effort=self._estimate_effort(param_count),
            created_at=datetime.now(),
        )

        # Add collaboration metadata
        confidence = 0.95  # High confidence - objective count
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="LongParameterListDetector",
            confidence=confidence,
            evidence=[
                "long_parameter_list",
                f"params_{param_count}",
                f"threshold_{self.max_params}"
            ],
            tags=["long_parameter_list", "code_smell", "refactoring"]
        ))

        # Flag entity for cross-detector collaboration
        if self.enricher:
            try:
                self.enricher.flag_entity(
                    entity_qualified_name=func_name,
                    detector="LongParameterListDetector",
                    severity=severity.value,
                    issues=["long_parameter_list"],
                    confidence=confidence,
                    metadata={
                        "param_count": param_count,
                        "complexity": complexity
                    }
                )
            except Exception:
                pass

        return finding

    def _generate_suggestion(
        self,
        func_name: str,
        params: List[str],
        is_method: bool,
        class_name: Optional[str]
    ) -> str:
        """Generate refactoring suggestion for long parameter list.

        Args:
            func_name: Function name
            params: List of parameter names
            is_method: Whether this is a method
            class_name: Class name if method

        Returns:
            Suggestion string
        """
        lines = ["**Refactoring Options:**\n"]

        # Option 1: Parameter Object
        lines.append("**1. Introduce Parameter Object:**")
        lines.append("```python")
        lines.append("from dataclasses import dataclass")
        lines.append("")
        lines.append("@dataclass")

        # Generate a sensible config class name
        config_name = self._suggest_config_name(func_name, params)
        lines.append(f"class {config_name}:")

        # Add parameters as fields (first 6)
        for p in params[:6]:
            lines.append(f"    {p}: Any  # TODO: add type")
        if len(params) > 6:
            lines.append(f"    # ... and {len(params) - 6} more fields")

        lines.append("")
        lines.append(f"def {func_name}(config: {config_name}):")
        lines.append("    ...")
        lines.append("```")
        lines.append("")

        # Option 2: Builder pattern (for many params)
        if len(params) >= 8:
            lines.append("**2. Use Builder Pattern:**")
            lines.append("```python")
            builder_name = f"{func_name.title().replace('_', '')}Builder"
            lines.append(f"class {builder_name}:")
            lines.append(f"    def with_{params[0]}(self, value): ...")
            lines.append(f"    def with_{params[1]}(self, value): ...")
            lines.append("    # ... more setters")
            lines.append(f"    def build(self): return {func_name}(...)")
            lines.append("```")
            lines.append("")

        # Option 3: Split function
        lines.append(f"**{'3' if len(params) >= 8 else '2'}. Split Into Smaller Functions:**")
        lines.append(f"- Break `{func_name}` into functions with focused responsibilities")
        lines.append("- Each function handles a subset of the original task")

        return "\n".join(lines)

    def _suggest_config_name(self, func_name: str, params: List[str]) -> str:
        """Suggest a name for the parameter object.

        Args:
            func_name: Function name
            params: Parameter names

        Returns:
            Suggested class name
        """
        # Try to derive from function name
        if func_name.startswith("create_"):
            base = func_name[7:]
            return f"{base.title().replace('_', '')}Config"
        elif func_name.startswith("init_") or func_name.startswith("initialize_"):
            base = func_name.split("_", 1)[1] if "_" in func_name else func_name
            return f"{base.title().replace('_', '')}Options"
        elif func_name.startswith("process_"):
            base = func_name[8:]
            return f"{base.title().replace('_', '')}Params"
        elif func_name.startswith("configure_"):
            base = func_name[10:]
            return f"{base.title().replace('_', '')}Config"

        # Look for common parameter patterns
        param_set = set(p.lower() for p in params)

        if {"host", "port"} <= param_set or {"url", "timeout"} <= param_set:
            return "ConnectionConfig"
        if {"username", "password"} <= param_set or {"user", "password"} <= param_set:
            return "Credentials"
        if {"width", "height"} <= param_set:
            return "Dimensions"
        if {"x", "y"} <= param_set:
            return "Position"
        if {"start", "end"} <= param_set:
            return "Range"

        # Default: use function name
        base = func_name.replace("_", " ").title().replace(" ", "")
        return f"{base}Config"

    def _estimate_effort(self, param_count: int) -> str:
        """Estimate refactoring effort based on parameter count.

        Args:
            param_count: Number of parameters

        Returns:
            Effort estimate string
        """
        if param_count >= 12:
            return "Large (1-2 days)"
        elif param_count >= 8:
            return "Medium (4-8 hours)"
        elif param_count >= 6:
            return "Small (2-4 hours)"
        else:
            return "Small (1 hour)"

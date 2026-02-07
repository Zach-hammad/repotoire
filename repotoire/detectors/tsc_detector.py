"""TypeScript Compiler (tsc) type checking detector with FalkorDB graph enrichment.

This hybrid detector uses the TypeScript compiler for type checking, similar to
how MypyDetector works for Python.

Architecture:
    1. Run tsc --noEmit on repository (type check without emitting files)
    2. Parse tsc error output
    3. Enrich findings with FalkorDB graph data
    4. Generate detailed findings with context

This approach achieves:
    - Comprehensive TypeScript type checking
    - Catches type errors before runtime
    - Rich context (graph-based metadata)
    - Actionable fix suggestions
"""

import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from repotoire.detectors.base import CodeSmellDetector
from repotoire.detectors.external_tool_runner import (
    batch_get_graph_context,
    get_graph_context,
    run_js_tool,
)
from repotoire.graph import FalkorDBClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.logging_config import get_logger
from repotoire.models import CollaborationMetadata, Finding, Severity

logger = get_logger(__name__)


class TscDetector(CodeSmellDetector):
    """Detects type errors in TypeScript using tsc with graph enrichment.

    Uses the TypeScript compiler for type checking and FalkorDB for context enrichment.

    Configuration:
        repository_path: Path to repository root (required)
        max_findings: Maximum findings to report (default: 100)
        strict: Enable strict mode (default: True)
        tsconfig_path: Path to tsconfig.json (optional, auto-detected)
    """

    # Error code to severity mapping
    # See: https://github.com/microsoft/TypeScript/blob/main/src/compiler/diagnosticMessages.json
    SEVERITY_MAP = {
        # Critical errors - code won't compile
        "TS1005": Severity.HIGH,  # Expected token
        "TS1009": Severity.HIGH,  # Trailing comma not allowed
        "TS1128": Severity.HIGH,  # Declaration or statement expected
        "TS1136": Severity.HIGH,  # Property assignment expected

        # Type errors - high severity
        "TS2304": Severity.HIGH,  # Cannot find name
        "TS2305": Severity.HIGH,  # Module has no exported member
        "TS2307": Severity.HIGH,  # Cannot find module
        "TS2314": Severity.HIGH,  # Generic type requires type arguments
        "TS2322": Severity.MEDIUM,  # Type not assignable
        "TS2339": Severity.MEDIUM,  # Property does not exist
        "TS2345": Severity.MEDIUM,  # Argument type not assignable
        "TS2349": Severity.MEDIUM,  # Cannot invoke expression
        "TS2351": Severity.MEDIUM,  # Cannot use 'new' with expression
        "TS2352": Severity.MEDIUM,  # Conversion may be mistake
        "TS2355": Severity.MEDIUM,  # Function must return a value
        "TS2365": Severity.MEDIUM,  # Operator cannot be applied
        "TS2531": Severity.MEDIUM,  # Object is possibly 'null'
        "TS2532": Severity.MEDIUM,  # Object is possibly 'undefined'
        "TS2533": Severity.MEDIUM,  # Object is possibly 'null' or 'undefined'

        # Warnings - medium severity
        "TS2554": Severity.MEDIUM,  # Expected N arguments, got M
        "TS2555": Severity.MEDIUM,  # Expected at least N arguments
        "TS2571": Severity.MEDIUM,  # Object is of type 'unknown'
        "TS2683": Severity.MEDIUM,  # 'this' implicitly has type 'any'
        "TS2769": Severity.MEDIUM,  # No overload matches

        # Style/best practices - low severity
        "TS6133": Severity.LOW,  # Declared but never used
        "TS6196": Severity.LOW,  # Declared but never read
        "TS7006": Severity.LOW,  # Parameter implicitly has 'any' type
        "TS7016": Severity.LOW,  # Could not find declaration file
        "TS7031": Severity.LOW,  # Binding element implicitly has 'any' type
        "TS7053": Severity.LOW,  # Element implicitly has 'any' type

        # Suggestions - info severity
        "TS80001": Severity.INFO,  # File is a CommonJS module
        "TS80005": Severity.INFO,  # 'require' call may be converted to import
    }

    # Regex to parse tsc output: file(line,col): error TSxxxx: message
    TSC_ERROR_PATTERN = re.compile(
        r"^(.+?)\((\d+),(\d+)\):\s+(error|warning)\s+(TS\d+):\s+(.+)$"
    )

    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict] = None,
        enricher: Optional[GraphEnricher] = None,
    ):
        """Initialize tsc detector.

        Args:
            graph_client: FalkorDB database client
            detector_config: Configuration dictionary with:
                - repository_path: Path to repository root (required)
                - max_findings: Max findings to report (default: 100)
                - strict: Enable strict mode (default: True)
                - tsconfig_path: Path to tsconfig.json (optional)
                - changed_files: List of relative file paths to analyze (for incremental analysis)
            enricher: Optional GraphEnricher for persistent collaboration
        """
        super().__init__(graph_client)

        config = detector_config or {}
        self.repository_path = Path(config.get("repository_path", "."))
        self.max_findings = config.get("max_findings", 100)
        self.strict = config.get("strict", True)
        self.tsconfig_path = config.get("tsconfig_path")
        self.enricher = enricher
        self.changed_files = config.get("changed_files", None)

        if not self.repository_path.exists():
            raise ValueError(f"Repository path does not exist: {self.repository_path}")

    def detect(self) -> List[Finding]:
        """Run tsc and enrich findings with graph data.

        Returns:
            List of type checking findings
        """
        # Fast path: if incremental mode with no changed files, skip entirely
        if self.changed_files is not None and len(self.changed_files) == 0:
            logger.debug("No changed files provided, skipping tsc (incremental cache hit)")
            return []

        logger.info(f"Running tsc type check on {self.repository_path}")

        # Check if TypeScript files exist (include all TS variants)
        ts_files = (
            list(self.repository_path.rglob("*.ts")) +
            list(self.repository_path.rglob("*.tsx")) +
            list(self.repository_path.rglob("*.mts")) +  # ES module TypeScript
            list(self.repository_path.rglob("*.cts"))    # CommonJS TypeScript
        )
        if not ts_files:
            logger.info("No TypeScript files found, skipping tsc")
            return []

        # Run tsc and get results
        tsc_errors = self._run_tsc()

        if not tsc_errors:
            logger.info("No tsc type errors found")
            return []

        # Collect unique file paths for batch graph context fetching (N+1 optimization)
        unique_files = set()
        for error in tsc_errors[:self.max_findings]:
            file_path = error.get("file", "")
            if file_path:
                unique_files.add(file_path)  # Already normalized in _run_tsc()

        # Batch fetch graph context for all files at once (instead of N queries)
        file_contexts = batch_get_graph_context(self.db, list(unique_files))
        logger.debug(f"Batch fetched graph context for {len(file_contexts)} files")

        # Create findings with pre-fetched context
        findings = []
        for error in tsc_errors[:self.max_findings]:
            finding = self._create_finding(error, file_contexts)
            if finding:
                findings.append(finding)

        logger.info(f"Created {len(findings)} type checking findings")
        return findings

    def _run_tsc(self) -> List[Dict[str, Any]]:
        """Run tsc and parse output.

        Uses bun if available for faster execution, falls back to npx.

        Returns:
            List of error dictionaries
        """
        # Build tsc arguments
        args = ["--noEmit", "--pretty", "false"]

        # Use tsconfig if available (provides compiler options and module resolution)
        if self.tsconfig_path:
            args.extend(["--project", str(self.tsconfig_path)])
        elif (self.repository_path / "tsconfig.json").exists():
            args.extend(["--project", str(self.repository_path / "tsconfig.json")])
        else:
            # No tsconfig - use strict mode and scan all files
            if self.strict:
                args.append("--strict")
            args.extend(["--allowJs", "--checkJs", "false"])

        # Incremental analysis: pass specific files to tsc for faster checking
        # tsc still uses tsconfig for compiler options but only checks specified files
        ts_extensions = ('.ts', '.tsx', '.mts', '.cts')
        if self.changed_files:
            ts_files = [
                f for f in self.changed_files
                if f.endswith(ts_extensions) and (self.repository_path / f).exists()
            ]
            if not ts_files:
                logger.debug("No TypeScript files in changed_files, skipping tsc")
                return []
            logger.info(f"Running tsc on {len(ts_files)} changed files (incremental)")
            args.extend(ts_files)

        # Run tsc (uses bun if available, falls back to npx)
        result = run_js_tool(
            package="tsc",
            args=args,
            tool_name="tsc",
            timeout=120,
            cwd=self.repository_path,
        )

        # tsc returns non-zero exit code when there are errors
        # but we still want to parse the output
        errors = []

        output = result.stdout + "\n" + result.stderr
        for line in output.split("\n"):
            line = line.strip()
            if not line:
                continue

            match = self.TSC_ERROR_PATTERN.match(line)
            if match:
                file_path, line_num, col, level, code, message = match.groups()

                # Normalize path separators for cross-platform compatibility
                # Windows paths may have backslashes, tsc may output mixed separators
                normalized_file = file_path.replace("\\", "/")
                normalized_repo = str(self.repository_path).replace("\\", "/")

                # Convert to relative path
                try:
                    # Try direct relative path calculation
                    rel_path = str(Path(file_path).relative_to(self.repository_path))
                except ValueError:
                    # Try with normalized paths (handles Windows drive letters)
                    if normalized_file.startswith(normalized_repo):
                        rel_path = normalized_file[len(normalized_repo):].lstrip("/")
                    else:
                        rel_path = normalized_file

                # Normalize to forward slashes for graph queries
                rel_path = rel_path.replace("\\", "/")

                errors.append({
                    "file": rel_path,
                    "line": int(line_num),
                    "column": int(col),
                    "level": level,
                    "code": code,
                    "message": message,
                })

        logger.info(f"tsc found {len(errors)} type errors")
        return errors

    def _create_finding(
        self,
        error: Dict[str, Any],
        file_contexts: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Optional[Finding]:
        """Create finding from tsc error with graph enrichment.

        Args:
            error: tsc error dictionary
            file_contexts: Pre-fetched graph contexts keyed by file path (batch optimization)

        Returns:
            Finding object or None
        """
        file_path = error["file"]
        line = error["line"]
        column = error["column"]
        code = error["code"]
        message = error["message"]

        # Enrich with graph data - use pre-fetched context if available (batch optimization)
        if file_contexts and file_path in file_contexts:
            # Use pre-fetched file context (no additional query needed)
            base_context = file_contexts[file_path]
            graph_data = {
                "file_loc": base_context.get("file_loc"),
                "language": base_context.get("language", "typescript"),
                "nodes": base_context.get("affected_nodes", []),
                "complexity": max(base_context.get("complexities", [0]) or [0]),
            }
        else:
            # Fallback to individual query (for backwards compatibility)
            graph_data = self._get_graph_context(file_path, line)

        # Determine severity
        severity = self._get_severity(code)

        # Create finding
        finding_id = str(uuid.uuid4())

        finding = Finding(
            id=finding_id,
            detector="TscDetector",
            severity=severity,
            title=f"Type error: {code}",
            description=self._build_description(error, graph_data),
            affected_nodes=graph_data.get("nodes", []),
            affected_files=[file_path],
            graph_context={
                "code": code,
                "line": line,
                "column": column,
                "message": message,
                **graph_data,
            },
            suggested_fix=self._suggest_fix(code, message),
            estimated_effort="Small (5-30 minutes)",
            created_at=datetime.now(),
            language="typescript",
        )

        # Flag entities in graph for cross-detector collaboration
        if self.enricher and graph_data.get("nodes"):
            for node in graph_data["nodes"]:
                try:
                    self.enricher.flag_entity(
                        entity_qualified_name=node,
                        detector="TscDetector",
                        severity=severity.value,
                        issues=[code],
                        confidence=0.95,  # tsc is very accurate
                        metadata={
                            "code": code,
                            "message": message,
                            "file": file_path,
                            "line": line,
                        },
                    )
                except Exception as e:
                    logger.warning(f"Failed to flag entity {node} in graph: {e}")

        # Add collaboration metadata
        finding.add_collaboration_metadata(
            CollaborationMetadata(
                detector="TscDetector",
                confidence=0.95,
                evidence=[code, "external_tool", "tsc"],
                tags=["tsc", "type_checking", self._get_tag_from_code(code)],
            )
        )

        return finding

    def _get_graph_context(self, file_path: str, line: int) -> Dict[str, Any]:
        """Get context from FalkorDB graph.

        Args:
            file_path: Relative file path
            line: Line number

        Returns:
            Dictionary with graph context
        """
        context = get_graph_context(self.db, file_path, line)

        return {
            "file_loc": context.get("file_loc", 0),
            "language": context.get("language", "typescript"),
            "nodes": context.get("affected_nodes", []),
            "complexity": max(context.get("complexities", [0]) or [0]),
        }

    def _get_severity(self, code: str) -> Severity:
        """Determine severity from tsc error code.

        Args:
            code: tsc error code (e.g., "TS2322")

        Returns:
            Severity enum value
        """
        return self.SEVERITY_MAP.get(code, Severity.MEDIUM)

    def _build_description(
        self,
        error: Dict[str, Any],
        graph_data: Dict[str, Any],
    ) -> str:
        """Build detailed description with context.

        Args:
            error: tsc error data
            graph_data: Graph enrichment data

        Returns:
            Formatted description
        """
        file_path = error["file"]
        line = error["line"]
        column = error["column"]
        code = error["code"]
        message = error["message"]

        desc = f"{message}\n\n"
        desc += f"**Location**: {file_path}:{line}:{column}\n"
        desc += f"**Error Code**: {code}\n"
        desc += f"**Documentation**: https://typescript.tv/errors/#{code.lower()}\n"

        if graph_data.get("file_loc"):
            desc += f"**File Size**: {graph_data['file_loc']} LOC\n"

        if graph_data.get("complexity"):
            desc += f"**Complexity**: {graph_data['complexity']}\n"

        if graph_data.get("nodes"):
            desc += f"**Affected**: {', '.join(graph_data['nodes'][:3])}\n"

        return desc

    def _suggest_fix(self, code: str, message: str) -> str:
        """Suggest fix based on error code.

        Args:
            code: tsc error code
            message: Error message

        Returns:
            Fix suggestion
        """
        fixes = {
            "TS2304": "Import or declare the missing identifier",
            "TS2305": "Check the module exports and import statement",
            "TS2307": "Install the missing module with npm/yarn or check the path",
            "TS2322": "Check type compatibility or add explicit type assertion",
            "TS2339": "Add the property to the type definition or use type assertion",
            "TS2345": "Check argument types match the expected parameter types",
            "TS2531": "Add null check: `if (obj !== null)` or use optional chaining `?.`",
            "TS2532": "Add undefined check or use optional chaining `?.`",
            "TS2533": "Add null/undefined check or use optional chaining `?.`",
            "TS2554": "Check the function signature and provide correct number of arguments",
            "TS2571": "Add type guard or type assertion for unknown values",
            "TS6133": "Remove unused variable or prefix with underscore",
            "TS7006": "Add explicit type annotation to the parameter",
            "TS7016": "Install @types package or create type declaration file",
        }

        return fixes.get(code, f"Review TypeScript error: {message}")

    def _get_tag_from_code(self, code: str) -> str:
        """Get semantic tag from tsc error code.

        Args:
            code: tsc error code

        Returns:
            Semantic tag for collaboration
        """
        code_num = int(code[2:]) if code.startswith("TS") else 0

        if code_num < 2000:
            return "syntax"
        elif code_num < 3000:
            return "type_error"
        elif code_num < 5000:
            return "semantic"
        elif code_num < 7000:
            return "declaration"
        elif code_num < 8000:
            return "suggestion"
        else:
            return "general"

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity for a tsc finding.

        Args:
            finding: Finding to assess

        Returns:
            Severity level (already determined during creation)
        """
        return finding.severity

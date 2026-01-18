"""CFG-based infinite loop and unreachable code detector using Rust.

This hybrid detector uses Rust's control flow graph analysis to detect:
- Infinite loops (while True without break, bare while True, etc.)
- Unreachable code after return/raise/break/continue
- Cyclomatic complexity outliers

Architecture:
    1. Collect all Python files from repository
    2. Run Rust CFG analysis in parallel (analyze_cfg_batch)
    3. Process results to identify issues
    4. Enrich findings with FalkorDB graph data
    5. Generate detailed findings with context

Performance: ~1-5 seconds for large codebases (Rust parallel processing)
"""

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from repotoire.detectors.base import CodeSmellDetector
from repotoire.detectors.external_tool_runner import get_graph_context
from repotoire.graph import FalkorDBClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.logging_config import get_logger
from repotoire.models import CollaborationMetadata, Finding, Severity

logger = get_logger(__name__)

# Try to import Rust CFG analysis
try:
    from repotoire_fast import analyze_cfg_batch
    RUST_CFG_AVAILABLE = True
    logger.debug("Rust CFG analysis available")
except ImportError:
    RUST_CFG_AVAILABLE = False
    logger.debug("Rust CFG analysis not available, detector disabled")


class InfiniteLoopDetector(CodeSmellDetector):
    """Detects infinite loops and unreachable code using CFG analysis.

    Uses Rust's control flow graph analysis for fast, accurate detection.

    Configuration:
        repository_path: Path to repository root (required)
        max_findings: Maximum findings to report (default: 100)
        complexity_threshold: Report functions above this complexity (default: 15)
        detect_unreachable: Whether to detect unreachable code (default: True)
        detect_infinite_loops: Whether to detect infinite loops (default: True)
    """

    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict] = None,
        enricher: Optional[GraphEnricher] = None,
    ):
        """Initialize infinite loop detector.

        Args:
            graph_client: FalkorDB database client
            detector_config: Configuration dictionary with:
                - repository_path: Path to repository root (required)
                - max_findings: Max findings to report
                - complexity_threshold: Cyclomatic complexity threshold
                - detect_unreachable: Enable unreachable code detection
                - detect_infinite_loops: Enable infinite loop detection
                - changed_files: List of relative file paths (incremental analysis)
            enricher: Optional GraphEnricher for cross-detector collaboration
        """
        super().__init__(graph_client)

        config = detector_config or {}
        self.repository_path = Path(config.get("repository_path", "."))
        self.max_findings = config.get("max_findings", 100)
        self.complexity_threshold = config.get("complexity_threshold", 15)
        self.detect_unreachable = config.get("detect_unreachable", True)
        self.detect_infinite_loops = config.get("detect_infinite_loops", True)
        self.enricher = enricher
        self.changed_files = config.get("changed_files", None)

        if not self.repository_path.exists():
            raise ValueError(f"Repository path does not exist: {self.repository_path}")

    def detect(self) -> List[Finding]:
        """Run CFG analysis and create findings.

        Returns:
            List of control flow findings
        """
        if not RUST_CFG_AVAILABLE:
            logger.warning("Rust CFG analysis not available, skipping detector")
            return []

        logger.info(f"Running CFG analysis on {self.repository_path}")

        # Collect Python files
        if self.changed_files:
            python_files = [
                self.repository_path / f
                for f in self.changed_files
                if f.endswith('.py') and (self.repository_path / f).exists()
            ]
            if not python_files:
                logger.debug("No Python files in changed_files, skipping CFG analysis")
                return []
            logger.info(f"Running CFG analysis on {len(python_files)} changed files (incremental)")
        else:
            python_files = list(self.repository_path.rglob("*.py"))
            logger.info(f"Running CFG analysis on {len(python_files)} Python files")

        # Prepare files for batch processing
        files_to_check = []
        for file_path in python_files:
            path_str = str(file_path)
            if any(skip in path_str for skip in [".venv", "venv", "__pycache__", ".git", "node_modules", ".tox"]):
                continue

            try:
                source = file_path.read_text(encoding="utf-8")
                rel_path = str(file_path.relative_to(self.repository_path))
                files_to_check.append((rel_path, source))
            except Exception as e:
                logger.debug(f"Failed to read {file_path}: {e}")
                continue

        if not files_to_check:
            logger.debug("No valid Python files to analyze")
            return []

        # Run batch CFG analysis (parallel Rust processing)
        batch_results = analyze_cfg_batch(files_to_check)

        # Process results
        findings = []
        for rel_path, analyses in batch_results:
            for analysis in analyses:
                function_name = analysis.get("function_name", "")
                has_infinite_loop = analysis.get("has_infinite_loop", False)
                unreachable_lines = analysis.get("unreachable_lines", [])
                cyclomatic_complexity = analysis.get("cyclomatic_complexity", 0)
                infinite_loop_types = analysis.get("infinite_loop_types", [])

                # Create finding for infinite loops
                if self.detect_infinite_loops and has_infinite_loop:
                    for loop_info in infinite_loop_types:
                        finding = self._create_infinite_loop_finding(
                            rel_path, function_name, loop_info
                        )
                        if finding:
                            findings.append(finding)

                # Create finding for unreachable code
                if self.detect_unreachable and unreachable_lines:
                    finding = self._create_unreachable_finding(
                        rel_path, function_name, unreachable_lines
                    )
                    if finding:
                        findings.append(finding)

                # Create finding for high complexity
                if cyclomatic_complexity > self.complexity_threshold:
                    finding = self._create_complexity_finding(
                        rel_path, function_name, cyclomatic_complexity
                    )
                    if finding:
                        findings.append(finding)

                if len(findings) >= self.max_findings:
                    break

            if len(findings) >= self.max_findings:
                break

        logger.info(f"Created {len(findings)} control flow findings")
        return findings[:self.max_findings]

    def _create_infinite_loop_finding(
        self,
        file_path: str,
        function_name: str,
        loop_info: Dict[str, Any],
    ) -> Optional[Finding]:
        """Create finding for infinite loop detection.

        Args:
            file_path: Relative file path
            function_name: Name of function containing the loop
            loop_info: Dict with line, type, description

        Returns:
            Finding object or None
        """
        line = loop_info.get("line", 0)
        loop_type = loop_info.get("type", "unknown")
        description = loop_info.get("description", "Infinite loop detected")

        # Get graph context
        graph_data = self._get_graph_context(file_path, line)

        finding_id = str(uuid.uuid4())

        finding = Finding(
            id=finding_id,
            detector="InfiniteLoopDetector",
            severity=Severity.HIGH,
            title=f"Potential infinite loop in {function_name}",
            description=self._build_infinite_loop_description(
                file_path, function_name, loop_info, graph_data
            ),
            affected_nodes=[f"{file_path}:{function_name}"] if function_name else [],
            affected_files=[file_path],
            graph_context={
                "loop_type": loop_type,
                "line": line,
                "function": function_name,
                **graph_data,
            },
            suggested_fix=self._suggest_infinite_loop_fix(loop_type),
            estimated_effort="Medium (1-2 hours)",
            created_at=datetime.now(),
            language="python",
        )

        # Flag in graph for collaboration
        if self.enricher and function_name:
            try:
                self.enricher.flag_entity(
                    entity_qualified_name=f"{file_path}:{function_name}",
                    detector="InfiniteLoopDetector",
                    severity=Severity.HIGH.value,
                    issues=["infinite_loop", loop_type],
                    confidence=0.90,
                    metadata={
                        "loop_type": loop_type,
                        "line": line,
                        "file": file_path,
                    },
                )
            except Exception as e:
                logger.warning(f"Failed to flag entity: {e}")

        finding.add_collaboration_metadata(
            CollaborationMetadata(
                detector="InfiniteLoopDetector",
                confidence=0.90,
                evidence=["cfg_analysis", loop_type, "rust"],
                tags=["infinite_loop", "control_flow", "potential_bug"],
            )
        )

        return finding

    def _create_unreachable_finding(
        self,
        file_path: str,
        function_name: str,
        unreachable_lines: List[int],
    ) -> Optional[Finding]:
        """Create finding for unreachable code.

        Args:
            file_path: Relative file path
            function_name: Name of function
            unreachable_lines: List of unreachable line numbers

        Returns:
            Finding object or None
        """
        first_line = min(unreachable_lines) if unreachable_lines else 0
        graph_data = self._get_graph_context(file_path, first_line)

        finding_id = str(uuid.uuid4())

        finding = Finding(
            id=finding_id,
            detector="InfiniteLoopDetector",
            severity=Severity.MEDIUM,
            title=f"Unreachable code in {function_name}",
            description=self._build_unreachable_description(
                file_path, function_name, unreachable_lines, graph_data
            ),
            affected_nodes=[f"{file_path}:{function_name}"] if function_name else [],
            affected_files=[file_path],
            graph_context={
                "unreachable_lines": unreachable_lines,
                "function": function_name,
                **graph_data,
            },
            suggested_fix="Remove unreachable code or fix the control flow logic",
            estimated_effort="Small (15-30 minutes)",
            created_at=datetime.now(),
            language="python",
        )

        finding.add_collaboration_metadata(
            CollaborationMetadata(
                detector="InfiniteLoopDetector",
                confidence=0.95,  # Very high confidence for unreachable code
                evidence=["cfg_analysis", "unreachable_code", "rust"],
                tags=["unreachable_code", "dead_code", "control_flow"],
            )
        )

        return finding

    def _create_complexity_finding(
        self,
        file_path: str,
        function_name: str,
        complexity: int,
    ) -> Optional[Finding]:
        """Create finding for high cyclomatic complexity.

        Args:
            file_path: Relative file path
            function_name: Name of function
            complexity: Cyclomatic complexity value

        Returns:
            Finding object or None
        """
        graph_data = self._get_graph_context(file_path, 0)

        finding_id = str(uuid.uuid4())

        if complexity > 25:
            severity = Severity.HIGH
        elif complexity > 20:
            severity = Severity.MEDIUM
        else:
            severity = Severity.LOW

        finding = Finding(
            id=finding_id,
            detector="InfiniteLoopDetector",
            severity=severity,
            title=f"High complexity in {function_name}: {complexity}",
            description=self._build_complexity_description(
                file_path, function_name, complexity, graph_data
            ),
            affected_nodes=[f"{file_path}:{function_name}"] if function_name else [],
            affected_files=[file_path],
            graph_context={
                "cyclomatic_complexity": complexity,
                "function": function_name,
                **graph_data,
            },
            suggested_fix=f"Refactor function to reduce complexity from {complexity} to below {self.complexity_threshold}",
            estimated_effort="Medium (1-4 hours)" if complexity > 25 else "Small (30 min - 1 hour)",
            created_at=datetime.now(),
            language="python",
        )

        finding.add_collaboration_metadata(
            CollaborationMetadata(
                detector="InfiniteLoopDetector",
                confidence=1.0,  # Cyclomatic complexity is deterministic
                evidence=["cfg_analysis", f"complexity_{complexity}", "rust"],
                tags=["complexity", "maintainability", "refactoring"],
            )
        )

        return finding

    def _get_graph_context(self, file_path: str, line: int) -> Dict[str, Any]:
        """Get context from FalkorDB graph."""
        context = get_graph_context(self.db, file_path, line)
        return {
            "file_loc": context.get("file_loc", 0),
            "language": context.get("language", "python"),
            "nodes": context.get("affected_nodes", []),
            "complexity": max(context.get("complexities", [0]) or [0]),
        }

    def _build_infinite_loop_description(
        self,
        file_path: str,
        function_name: str,
        loop_info: Dict[str, Any],
        graph_data: Dict[str, Any],
    ) -> str:
        """Build description for infinite loop finding."""
        line = loop_info.get("line", 0)
        loop_type = loop_info.get("type", "unknown")
        description = loop_info.get("description", "Infinite loop detected")

        desc = f"{description}\n\n"
        desc += f"**Location**: {file_path}:{line}\n"
        desc += f"**Function**: {function_name}\n"
        desc += f"**Loop Type**: {loop_type}\n"

        if graph_data.get("file_loc"):
            desc += f"**File Size**: {graph_data['file_loc']} LOC\n"

        desc += "\n**Risk**: Infinite loops can cause application hangs and resource exhaustion.\n"

        return desc

    def _build_unreachable_description(
        self,
        file_path: str,
        function_name: str,
        unreachable_lines: List[int],
        graph_data: Dict[str, Any],
    ) -> str:
        """Build description for unreachable code finding."""
        lines_str = ", ".join(str(l) for l in unreachable_lines[:10])
        if len(unreachable_lines) > 10:
            lines_str += f" (and {len(unreachable_lines) - 10} more)"

        desc = f"Found {len(unreachable_lines)} lines of unreachable code.\n\n"
        desc += f"**Location**: {file_path}:{min(unreachable_lines)}\n"
        desc += f"**Function**: {function_name}\n"
        desc += f"**Unreachable Lines**: {lines_str}\n"

        if graph_data.get("file_loc"):
            desc += f"**File Size**: {graph_data['file_loc']} LOC\n"

        desc += "\n**Impact**: Unreachable code is dead code that can never execute.\n"

        return desc

    def _build_complexity_description(
        self,
        file_path: str,
        function_name: str,
        complexity: int,
        graph_data: Dict[str, Any],
    ) -> str:
        """Build description for complexity finding."""
        desc = f"Function has cyclomatic complexity of {complexity} (threshold: {self.complexity_threshold}).\n\n"
        desc += f"**Location**: {file_path}\n"
        desc += f"**Function**: {function_name}\n"
        desc += f"**Complexity**: {complexity}\n"

        if graph_data.get("file_loc"):
            desc += f"**File Size**: {graph_data['file_loc']} LOC\n"

        desc += "\n**Impact**: High complexity makes code harder to understand, test, and maintain.\n"

        return desc

    def _suggest_infinite_loop_fix(self, loop_type: str) -> str:
        """Suggest fix based on loop type."""
        fixes = {
            "bare_while_true": "Add a break condition or use a different loop structure",
            "while_true_no_break": "Add a break statement or use a bounded loop",
            "for_infinite": "Ensure the iterator has a finite length",
            "recursive_no_base": "Add a base case to the recursive function",
        }
        return fixes.get(loop_type, "Add proper termination condition to the loop")

    def severity(self, finding: Finding) -> Severity:
        """Return finding severity."""
        return finding.severity

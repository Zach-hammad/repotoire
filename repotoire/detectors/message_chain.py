"""Message Chain detector for Law of Demeter violations (REPO-221).

Detects long method chains that violate the Law of Demeter principle:
- Deep coupling through chains of 4+ method calls
- Excessive knowledge of object internals
- Tight coupling that makes code brittle to changes

Example violation:
    user.get_profile().get_settings().get_notifications().is_email_enabled()

Better approach:
    user.wants_email_notifications()
"""

import ast
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph.base import DatabaseClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.models import CollaborationMetadata, Finding, Severity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class MessageChainDetector(CodeSmellDetector):
    """Detects Law of Demeter violations through long method chains.

    The Law of Demeter (principle of least knowledge) states that a method
    should only call methods on:
    1. Its own object (self)
    2. Objects passed as parameters
    3. Objects it creates
    4. Its direct component objects

    Long method chains like a.b().c().d() indicate tight coupling and
    excessive knowledge of object internals.

    Thresholds:
        - HIGH: 5+ levels of chaining
        - MEDIUM: 4 levels of chaining
        - LOW: 3 levels of chaining (optional, disabled by default)
    """

    THRESHOLDS = {
        "min_chain_depth": 4,  # Minimum chain depth to report
        "high_severity_depth": 5,  # Chain depth for HIGH severity
        "critical_severity_depth": 7,  # Chain depth for CRITICAL severity
        "report_low_severity": False,  # Report chains of depth 3
    }

    def __init__(
        self,
        neo4j_client: DatabaseClient,
        detector_config: Optional[dict] = None,
        enricher: Optional[GraphEnricher] = None
    ):
        """Initialize message chain detector.

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
        self.min_chain_depth = config.get(
            "min_chain_depth",
            self.THRESHOLDS["min_chain_depth"]
        )
        self.high_severity_depth = config.get(
            "high_severity_depth",
            self.THRESHOLDS["high_severity_depth"]
        )
        self.critical_severity_depth = config.get(
            "critical_severity_depth",
            self.THRESHOLDS["critical_severity_depth"]
        )
        self.report_low_severity = config.get(
            "report_low_severity",
            self.THRESHOLDS["report_low_severity"]
        )

    def detect(self) -> List[Finding]:
        """Detect Law of Demeter violations through method chain analysis.

        Uses a hybrid approach:
        1. Query graph for functions with stored max_chain_depth property
        2. For files without the property, analyze source directly

        Returns:
            List of findings for detected message chain violations
        """
        logger.info("Running MessageChainDetector")
        findings: List[Finding] = []

        # First, try to get chain depths from graph (if parser has stored them)
        graph_findings = self._find_chains_from_graph()
        findings.extend(graph_findings)

        # If no graph findings, analyze source files directly
        if not findings:
            source_findings = self._find_chains_from_source()
            findings.extend(source_findings)

        logger.info(f"Found {len(findings)} message chain violation(s)")
        return findings

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity based on chain depth.

        Args:
            finding: Finding to assess

        Returns:
            Severity level
        """
        chain_depth = finding.graph_context.get("chain_depth", 0)

        if chain_depth >= self.critical_severity_depth:
            return Severity.CRITICAL
        elif chain_depth >= self.high_severity_depth:
            return Severity.HIGH
        elif chain_depth >= self.min_chain_depth:
            return Severity.MEDIUM
        return Severity.LOW

    def _find_chains_from_graph(self) -> List[Finding]:
        """Find method chains using stored graph properties.

        Queries for functions with max_chain_depth property >= threshold.

        Returns:
            List of findings from graph analysis
        """
        findings: List[Finding] = []

        # Query for functions with high chain depth
        # This property would be set by the parser if it extracts chain depths
        query = """
        MATCH (f:Function)
        WHERE COALESCE(f.max_chain_depth, 0) >= $min_depth
        OPTIONAL MATCH (file:File)-[:CONTAINS*]->(f)
        RETURN f.qualifiedName AS func_name,
               f.name AS func_simple_name,
               f.filePath AS func_file,
               f.lineStart AS func_line,
               f.max_chain_depth AS chain_depth,
               f.chain_example AS chain_example,
               file.filePath AS containing_file
        ORDER BY f.max_chain_depth DESC
        LIMIT 100
        """

        try:
            results = self.db.execute_query(query, {"min_depth": self.min_chain_depth})

            for record in results:
                func_name = record.get("func_name", "")
                chain_depth = record.get("chain_depth", 0)

                if not func_name or not chain_depth:
                    continue

                finding = self._create_finding(
                    func_name=func_name,
                    func_simple_name=record.get("func_simple_name", ""),
                    file_path=record.get("containing_file") or record.get("func_file", ""),
                    line_number=record.get("func_line"),
                    chain_depth=chain_depth,
                    chain_example=record.get("chain_example"),
                )
                findings.append(finding)

        except Exception as e:
            logger.debug(f"Graph query for chain depth failed (property may not exist): {e}")

        return findings

    def _find_chains_from_source(self) -> List[Finding]:
        """Find method chains by analyzing source files directly.

        Falls back to AST analysis when graph doesn't have chain_depth property.

        Returns:
            List of findings from source analysis
        """
        findings: List[Finding] = []

        # Get all Python files from the graph
        query = """
        MATCH (f:File)
        WHERE f.language = 'python' OR f.filePath ENDS WITH '.py'
        RETURN f.filePath AS file_path
        LIMIT 1000
        """

        try:
            results = self.db.execute_query(query)

            for record in results:
                file_path = record.get("file_path", "")
                if not file_path:
                    continue

                file_findings = self._analyze_file_for_chains(file_path)
                findings.extend(file_findings)

        except Exception as e:
            logger.warning(f"Failed to query files for chain analysis: {e}")

        return findings

    def _analyze_file_for_chains(self, file_path: str) -> List[Finding]:
        """Analyze a single file for method chain violations.

        Args:
            file_path: Path to Python file

        Returns:
            List of findings from this file
        """
        findings: List[Finding] = []

        try:
            path = Path(file_path)
            if not path.exists():
                return findings

            with open(path, "r", encoding="utf-8") as f:
                source = f.read()

            tree = ast.parse(source, filename=file_path)

            # Find all method chains in the file
            chain_visitor = ChainDepthVisitor(file_path)
            chain_visitor.visit(tree)

            # Create findings for chains exceeding threshold
            for chain_info in chain_visitor.chains:
                if chain_info["depth"] >= self.min_chain_depth:
                    finding = self._create_finding(
                        func_name=chain_info["function_qualified"],
                        func_simple_name=chain_info["function_name"],
                        file_path=file_path,
                        line_number=chain_info["line"],
                        chain_depth=chain_info["depth"],
                        chain_example=chain_info["example"],
                    )
                    findings.append(finding)

        except SyntaxError as e:
            logger.debug(f"Syntax error parsing {file_path}: {e}")
        except Exception as e:
            logger.debug(f"Error analyzing {file_path}: {e}")

        return findings

    def _create_finding(
        self,
        func_name: str,
        func_simple_name: str,
        file_path: str,
        line_number: Optional[int],
        chain_depth: int,
        chain_example: Optional[str] = None,
    ) -> Finding:
        """Create a finding for a message chain violation.

        Args:
            func_name: Qualified function name
            func_simple_name: Simple function name
            file_path: File path
            line_number: Line number
            chain_depth: Depth of the method chain
            chain_example: Example of the chain expression

        Returns:
            Finding object
        """
        finding_id = str(uuid.uuid4())

        # Determine severity based on chain depth
        if chain_depth >= self.critical_severity_depth:
            severity = Severity.CRITICAL
        elif chain_depth >= self.high_severity_depth:
            severity = Severity.HIGH
        else:
            severity = Severity.MEDIUM

        # Build description
        example_text = ""
        if chain_example:
            example_text = f"\n\n**Example chain:**\n```python\n{chain_example}\n```"

        description = (
            f"Method chain with **{chain_depth} levels** violates the Law of Demeter.\n\n"
            f"Function `{func_simple_name}` contains method chains that traverse "
            f"{chain_depth} levels deep into object structures. This indicates:"
            f"\n- Tight coupling to internal object structure"
            f"\n- Violation of encapsulation principles"
            f"\n- Fragile code that breaks when intermediate objects change"
            f"{example_text}"
        )

        suggestion = (
            "**Refactoring approaches:**\n\n"
            "1. **Delegate to intermediate object:**\n"
            "   Instead of `a.b().c().d()`, add `a.get_d()` that internally calls `b().c().d()`\n\n"
            "2. **Use Tell, Don't Ask principle:**\n"
            "   Instead of `user.get_profile().get_settings().get_notifications().is_email_enabled()`\n"
            "   Use `user.wants_email_notifications()`\n\n"
            "3. **Consider a Facade pattern:**\n"
            "   Create a simpler interface that hides the chain complexity\n\n"
            "4. **Extract a method:**\n"
            "   If the chain retrieves data for computation, extract a method on the first object"
        )

        finding = Finding(
            id=finding_id,
            detector="MessageChainDetector",
            severity=severity,
            title=f"Law of Demeter violation: {chain_depth}-level chain in {func_simple_name}",
            description=description,
            affected_nodes=[func_name],
            affected_files=[file_path] if file_path else [],
            line_start=line_number,
            graph_context={
                "pattern_type": "message_chain",
                "chain_depth": chain_depth,
                "chain_example": chain_example,
                "function_name": func_simple_name,
            },
            suggested_fix=suggestion,
            estimated_effort=self._estimate_effort(chain_depth),
            created_at=datetime.now(),
        )

        # Add collaboration metadata
        confidence = 0.85  # High confidence - structural pattern
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="MessageChainDetector",
            confidence=confidence,
            evidence=[f"chain_depth_{chain_depth}", "law_of_demeter_violation"],
            tags=["coupling", "law_of_demeter", "message_chain", "refactoring"]
        ))

        # Flag entity for cross-detector collaboration
        if self.enricher:
            try:
                self.enricher.flag_entity(
                    entity_qualified_name=func_name,
                    detector="MessageChainDetector",
                    severity=severity.value,
                    issues=["message_chain", "law_of_demeter"],
                    confidence=confidence,
                    metadata={
                        "chain_depth": chain_depth,
                        "chain_example": chain_example[:100] if chain_example else None
                    }
                )
            except Exception:
                pass

        return finding

    def _estimate_effort(self, chain_depth: int) -> str:
        """Estimate effort to fix based on chain depth.

        Args:
            chain_depth: Depth of the method chain

        Returns:
            Effort estimate string
        """
        if chain_depth >= 7:
            return "Medium (2-4 hours)"
        elif chain_depth >= 5:
            return "Small (1-2 hours)"
        else:
            return "Small (30-60 minutes)"


class ChainDepthVisitor(ast.NodeVisitor):
    """AST visitor to find method chain depths in Python code."""

    def __init__(self, file_path: str):
        """Initialize chain depth visitor.

        Args:
            file_path: Path to the file being analyzed
        """
        self.file_path = file_path
        self.current_class: Optional[str] = None
        self.current_class_line: Optional[int] = None
        self.current_function: Optional[str] = None
        self.current_function_line: Optional[int] = None
        self.chains: List[Dict] = []  # List of chain info dicts

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Visit class definition."""
        old_class = self.current_class
        old_class_line = self.current_class_line
        self.current_class = node.name
        self.current_class_line = node.lineno
        self.generic_visit(node)
        self.current_class = old_class
        self.current_class_line = old_class_line

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Visit function definition."""
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Visit async function definition."""
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef) -> None:
        """Process a function definition."""
        old_function = self.current_function
        old_function_line = self.current_function_line
        self.current_function = node.name
        self.current_function_line = node.lineno
        self.generic_visit(node)
        self.current_function = old_function
        self.current_function_line = old_function_line

    def visit_Call(self, node: ast.Call) -> None:
        """Visit a function call and check for chain depth."""
        if self.current_function:
            depth, chain_str = self._get_chain_depth(node)

            if depth >= 3:  # Only track chains of 3+
                # Build qualified function name
                if self.current_class and self.current_class_line:
                    func_qualified = (
                        f"{self.file_path}::{self.current_class}:{self.current_class_line}"
                        f".{self.current_function}:{self.current_function_line}"
                    )
                else:
                    func_qualified = f"{self.file_path}::{self.current_function}:{self.current_function_line}"

                self.chains.append({
                    "function_qualified": func_qualified,
                    "function_name": self.current_function,
                    "line": node.lineno,
                    "depth": depth,
                    "example": chain_str[:200] if chain_str else None,
                })

        self.generic_visit(node)

    def _get_chain_depth(self, node: ast.Call) -> Tuple[int, str]:
        """Calculate the depth of a method chain.

        Counts how many levels of attribute access and calls are chained.

        Args:
            node: Call AST node

        Returns:
            Tuple of (chain depth, chain string representation)
        """
        depth = 0
        parts: List[str] = []
        current = node

        while True:
            if isinstance(current, ast.Call):
                # This is a method call - add the method name
                if isinstance(current.func, ast.Attribute):
                    parts.append(f"{current.func.attr}()")
                    current = current.func.value
                    depth += 1
                elif isinstance(current.func, ast.Name):
                    parts.append(f"{current.func.id}()")
                    break
                else:
                    break
            elif isinstance(current, ast.Attribute):
                # This is an attribute access
                parts.append(current.attr)
                current = current.value
                depth += 1
            elif isinstance(current, ast.Name):
                # Base variable
                parts.append(current.id)
                break
            elif isinstance(current, ast.Subscript):
                # Array/dict access: obj[key]
                parts.append("[...]")
                current = current.value
            else:
                break

        # Build chain string (reversed to show left-to-right)
        parts.reverse()
        chain_str = ".".join(parts)

        return depth, chain_str

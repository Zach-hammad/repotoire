"""Generator/Iterator Misuse detector (REPO-232).

Detects common generator anti-patterns:
1. Unconsumed generators - generator expressions/functions that are never iterated
2. Immediate list conversion - generators immediately wrapped in list()
3. Single-yield generators - generators with only one yield (unnecessary complexity)

These patterns indicate misunderstanding of generators and can lead to:
- Memory inefficiency (list() defeats lazy evaluation)
- Bugs (unconsumed generators don't execute their code)
- Unnecessary complexity (single-yield generators)
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


class GeneratorMisuseDetector(CodeSmellDetector):
    """Detects generator/iterator misuse patterns.

    Uses the function_has_yield_idx index on (f.has_yield) property for
    efficient queries on generator functions.

    Anti-patterns detected:
    1. **Single-yield generators**: Generator with only one yield statement
       - Could be simplified to a regular function with return
       - Adds unnecessary generator overhead

    2. **Immediate list conversion**: Generator immediately wrapped in list()
       - Defeats the purpose of lazy evaluation
       - Should use list comprehension instead

    3. **Generators in boolean context**: Using generator in if/while without list()
       - Generator expressions are always truthy
       - Likely a bug: should be any() or list()
    """

    THRESHOLDS = {
        "max_yield_for_simple": 1,  # Max yields for "single-yield" warning
    }

    def __init__(
        self,
        neo4j_client: DatabaseClient,
        detector_config: Optional[dict] = None,
        enricher: Optional[GraphEnricher] = None
    ):
        """Initialize generator misuse detector.

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
        self.max_yield_for_simple = config.get(
            "max_yield_for_simple",
            self.THRESHOLDS["max_yield_for_simple"]
        )

    def detect(self) -> List[Finding]:
        """Detect generator misuse patterns.

        Uses a hybrid approach:
        1. Query graph for functions with has_yield=true
        2. Analyze source for specific patterns

        Returns:
            List of findings for detected generator misuse
        """
        logger.info("Running GeneratorMisuseDetector")
        findings: List[Finding] = []

        # Find single-yield generators from graph
        single_yield_findings = self._find_single_yield_generators()
        findings.extend(single_yield_findings)

        # Find immediate list conversions from source analysis
        list_conversion_findings = self._find_immediate_list_conversions()
        findings.extend(list_conversion_findings)

        # Find generators in boolean context
        boolean_context_findings = self._find_generators_in_boolean_context()
        findings.extend(boolean_context_findings)

        logger.info(f"Found {len(findings)} generator misuse pattern(s)")
        return findings

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity based on misuse type.

        Args:
            finding: Finding to assess

        Returns:
            Severity level
        """
        pattern_type = finding.graph_context.get("pattern_type", "")

        if pattern_type == "generator_boolean_context":
            return Severity.HIGH  # Likely a bug
        elif pattern_type == "immediate_list_conversion":
            return Severity.MEDIUM  # Inefficiency
        elif pattern_type == "single_yield":
            return Severity.LOW  # Code smell

        return Severity.LOW

    def _find_single_yield_generators(self) -> List[Finding]:
        """Find generator functions with only one yield statement.

        Uses the function_has_yield_idx index for efficient queries.

        Returns:
            List of findings for single-yield generators
        """
        findings: List[Finding] = []

        # Query for generator functions
        # Note: yield_count property would need to be added by parser
        query = """
        MATCH (f:Function)
        WHERE f.has_yield = true
        OPTIONAL MATCH (file:File)-[:CONTAINS*]->(f)
        RETURN f.qualifiedName AS func_name,
               f.name AS func_simple_name,
               f.filePath AS func_file,
               f.lineStart AS func_line,
               f.lineEnd AS func_line_end,
               COALESCE(f.yield_count, 0) AS yield_count,
               file.filePath AS containing_file
        ORDER BY f.qualifiedName
        LIMIT 200
        """

        try:
            results = self.db.execute_query(query)

            # For functions where we don't have yield_count, analyze source
            for record in results:
                func_name = record.get("func_name", "")
                func_file = record.get("containing_file") or record.get("func_file", "")
                yield_count = record.get("yield_count", 0)

                if not func_name or not func_file:
                    continue

                # If yield_count is 0 (not set), analyze source
                if yield_count == 0:
                    yield_count = self._count_yields_in_function(
                        func_file,
                        record.get("func_line"),
                        record.get("func_line_end")
                    )

                if yield_count == 1:
                    finding = self._create_single_yield_finding(
                        func_name=func_name,
                        func_simple_name=record.get("func_simple_name", ""),
                        file_path=func_file,
                        line_number=record.get("func_line"),
                    )
                    findings.append(finding)

        except Exception as e:
            logger.warning(f"Failed to query generator functions: {e}")

        return findings

    def _count_yields_in_function(
        self,
        file_path: str,
        line_start: Optional[int],
        line_end: Optional[int]
    ) -> int:
        """Count yield statements in a function by analyzing source.

        Args:
            file_path: Path to source file
            line_start: Function start line
            line_end: Function end line

        Returns:
            Number of yield statements
        """
        if not line_start or not line_end:
            return 0

        try:
            path = Path(file_path)
            if not path.exists():
                return 0

            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            # Extract function source
            func_lines = lines[line_start - 1:line_end]
            func_source = "".join(func_lines)

            # Parse and count yields
            try:
                # Need to parse as a complete module, so wrap in dummy
                tree = ast.parse(func_source, filename=file_path)
                yield_count = sum(
                    1 for node in ast.walk(tree)
                    if isinstance(node, (ast.Yield, ast.YieldFrom))
                )
                return yield_count
            except SyntaxError:
                # Try counting via simple pattern matching as fallback
                return func_source.count("yield ")

        except Exception:
            return 0

    def _find_immediate_list_conversions(self) -> List[Finding]:
        """Find generators immediately wrapped in list().

        Patterns like:
            list(x for x in items)  # Should be [x for x in items]
            numbers = list(generate_numbers())  # If generate_numbers has yield

        Returns:
            List of findings for immediate list conversions
        """
        findings: List[Finding] = []

        # Get Python files from graph
        query = """
        MATCH (f:File)
        WHERE f.language = 'python' OR f.filePath ENDS WITH '.py'
        RETURN f.filePath AS file_path
        LIMIT 500
        """

        try:
            results = self.db.execute_query(query)

            for record in results:
                file_path = record.get("file_path", "")
                if not file_path:
                    continue

                file_findings = self._analyze_file_for_list_conversions(file_path)
                findings.extend(file_findings)

        except Exception as e:
            logger.warning(f"Failed to query files: {e}")

        return findings

    def _analyze_file_for_list_conversions(self, file_path: str) -> List[Finding]:
        """Analyze a file for immediate list conversion patterns.

        Args:
            file_path: Path to Python file

        Returns:
            List of findings
        """
        findings: List[Finding] = []

        try:
            path = Path(file_path)
            if not path.exists():
                return findings

            with open(path, "r", encoding="utf-8") as f:
                source = f.read()

            tree = ast.parse(source, filename=file_path)

            visitor = ListConversionVisitor(file_path)
            visitor.visit(tree)

            for pattern_info in visitor.list_conversions:
                finding = self._create_list_conversion_finding(pattern_info)
                findings.append(finding)

        except SyntaxError:
            pass
        except Exception as e:
            logger.debug(f"Error analyzing {file_path}: {e}")

        return findings

    def _find_generators_in_boolean_context(self) -> List[Finding]:
        """Find generator expressions used in boolean context.

        Pattern: if (x for x in items):  # Always truthy - likely bug

        Returns:
            List of findings for generators in boolean context
        """
        findings: List[Finding] = []

        # Get Python files from graph
        query = """
        MATCH (f:File)
        WHERE f.language = 'python' OR f.filePath ENDS WITH '.py'
        RETURN f.filePath AS file_path
        LIMIT 500
        """

        try:
            results = self.db.execute_query(query)

            for record in results:
                file_path = record.get("file_path", "")
                if not file_path:
                    continue

                file_findings = self._analyze_file_for_boolean_context(file_path)
                findings.extend(file_findings)

        except Exception as e:
            logger.warning(f"Failed to query files: {e}")

        return findings

    def _analyze_file_for_boolean_context(self, file_path: str) -> List[Finding]:
        """Analyze a file for generators in boolean context.

        Args:
            file_path: Path to Python file

        Returns:
            List of findings
        """
        findings: List[Finding] = []

        try:
            path = Path(file_path)
            if not path.exists():
                return findings

            with open(path, "r", encoding="utf-8") as f:
                source = f.read()

            tree = ast.parse(source, filename=file_path)

            visitor = BooleanContextVisitor(file_path)
            visitor.visit(tree)

            for pattern_info in visitor.boolean_contexts:
                finding = self._create_boolean_context_finding(pattern_info)
                findings.append(finding)

        except SyntaxError:
            pass
        except Exception as e:
            logger.debug(f"Error analyzing {file_path}: {e}")

        return findings

    def _create_single_yield_finding(
        self,
        func_name: str,
        func_simple_name: str,
        file_path: str,
        line_number: Optional[int],
    ) -> Finding:
        """Create a finding for single-yield generator.

        Args:
            func_name: Qualified function name
            func_simple_name: Simple function name
            file_path: File path
            line_number: Line number

        Returns:
            Finding object
        """
        finding_id = str(uuid.uuid4())

        description = (
            f"Generator function `{func_simple_name}` has only **one yield statement**.\n\n"
            "Single-yield generators add unnecessary complexity:\n"
            "- Generator protocol overhead for single value\n"
            "- Harder to understand than a simple return\n"
            "- May indicate misunderstanding of generators\n\n"
            "**Exception:** This is valid for context managers using `@contextmanager`."
        )

        suggestion = (
            "**Option 1: Convert to regular function:**\n"
            "```python\n"
            "# Before (generator)\n"
            "def get_config():\n"
            "    yield load_config()\n\n"
            "# After (regular function)\n"
            "def get_config():\n"
            "    return load_config()\n"
            "```\n\n"
            "**Option 2: If intentional (context manager):**\n"
            "```python\n"
            "@contextmanager\n"
            "def managed_resource():\n"
            "    resource = acquire()\n"
            "    yield resource  # Single yield is correct here\n"
            "    release(resource)\n"
            "```"
        )

        finding = Finding(
            id=finding_id,
            detector="GeneratorMisuseDetector",
            severity=Severity.LOW,
            title=f"Single-yield generator: {func_simple_name}",
            description=description,
            affected_nodes=[func_name],
            affected_files=[file_path] if file_path else [],
            line_start=line_number,
            graph_context={
                "pattern_type": "single_yield",
                "function_name": func_simple_name,
                "yield_count": 1,
            },
            suggested_fix=suggestion,
            estimated_effort="Small (15-30 minutes)",
            created_at=datetime.now(),
        )

        # Add collaboration metadata
        confidence = 0.70  # Moderate - could be intentional for context manager
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="GeneratorMisuseDetector",
            confidence=confidence,
            evidence=["single_yield", "generator_function"],
            tags=["generator", "complexity", "code_smell"]
        ))

        return finding

    def _create_list_conversion_finding(self, pattern_info: Dict) -> Finding:
        """Create a finding for immediate list conversion.

        Args:
            pattern_info: Dictionary with pattern information

        Returns:
            Finding object
        """
        finding_id = str(uuid.uuid4())

        description = (
            f"Generator expression immediately converted to list at line {pattern_info['line']}.\n\n"
            "```python\n"
            f"{pattern_info.get('code', 'list(x for x in items)')}\n"
            "```\n\n"
            "This defeats the purpose of lazy evaluation:\n"
            "- Allocates memory for entire list immediately\n"
            "- No memory savings from generator\n"
            "- List comprehension is more idiomatic and slightly faster"
        )

        suggestion = (
            "**Use a list comprehension instead:**\n"
            "```python\n"
            "# Before (generator wrapped in list)\n"
            "result = list(x * 2 for x in items)\n\n"
            "# After (list comprehension)\n"
            "result = [x * 2 for x in items]\n"
            "```\n\n"
            "**When to keep generator:**\n"
            "- If you need a generator for streaming/pipeline processing\n"
            "- If the list() is conditional or deferred"
        )

        finding = Finding(
            id=finding_id,
            detector="GeneratorMisuseDetector",
            severity=Severity.MEDIUM,
            title=f"Generator immediately converted to list",
            description=description,
            affected_nodes=[pattern_info.get("function_qualified", "")],
            affected_files=[pattern_info["file_path"]] if pattern_info.get("file_path") else [],
            line_start=pattern_info.get("line"),
            graph_context={
                "pattern_type": "immediate_list_conversion",
                "code": pattern_info.get("code"),
            },
            suggested_fix=suggestion,
            estimated_effort="Small (5-10 minutes)",
            created_at=datetime.now(),
        )

        # Add collaboration metadata
        confidence = 0.85  # High confidence - clear anti-pattern
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="GeneratorMisuseDetector",
            confidence=confidence,
            evidence=["list_wrapped_generator", "immediate_conversion"],
            tags=["generator", "performance", "idiom"]
        ))

        return finding

    def _create_boolean_context_finding(self, pattern_info: Dict) -> Finding:
        """Create a finding for generator in boolean context.

        Args:
            pattern_info: Dictionary with pattern information

        Returns:
            Finding object
        """
        finding_id = str(uuid.uuid4())

        description = (
            f"Generator expression used in boolean context at line {pattern_info['line']}.\n\n"
            "```python\n"
            f"{pattern_info.get('code', 'if (x for x in items):')}\n"
            "```\n\n"
            "**This is likely a bug!** Generator expressions are always truthy.\n"
            "- The generator object exists, so it's truthy\n"
            "- The condition doesn't check if the generator produces values\n"
            "- The generator is never actually consumed"
        )

        suggestion = (
            "**Option 1: Use any() to check if any values exist:**\n"
            "```python\n"
            "# Before (always True)\n"
            "if (x for x in items if x > 0):\n"
            "    process()\n\n"
            "# After (correct)\n"
            "if any(x > 0 for x in items):\n"
            "    process()\n"
            "```\n\n"
            "**Option 2: Convert to list if you need the values:**\n"
            "```python\n"
            "filtered = [x for x in items if x > 0]\n"
            "if filtered:\n"
            "    process(filtered)\n"
            "```\n\n"
            "**Option 3: Check the source collection:**\n"
            "```python\n"
            "if items:  # Check if source is non-empty\n"
            "    result = (x for x in items)\n"
            "```"
        )

        finding = Finding(
            id=finding_id,
            detector="GeneratorMisuseDetector",
            severity=Severity.HIGH,  # Likely a bug
            title=f"Generator in boolean context (always truthy)",
            description=description,
            affected_nodes=[pattern_info.get("function_qualified", "")],
            affected_files=[pattern_info["file_path"]] if pattern_info.get("file_path") else [],
            line_start=pattern_info.get("line"),
            graph_context={
                "pattern_type": "generator_boolean_context",
                "code": pattern_info.get("code"),
                "context_type": pattern_info.get("context_type", "if"),
            },
            suggested_fix=suggestion,
            estimated_effort="Small (10-20 minutes)",
            created_at=datetime.now(),
        )

        # Add collaboration metadata
        confidence = 0.95  # Very high confidence - this is almost always a bug
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="GeneratorMisuseDetector",
            confidence=confidence,
            evidence=["generator_boolean_context", "always_truthy"],
            tags=["generator", "bug", "logic_error"]
        ))

        # Flag entity
        if self.enricher and pattern_info.get("function_qualified"):
            try:
                self.enricher.flag_entity(
                    entity_qualified_name=pattern_info["function_qualified"],
                    detector="GeneratorMisuseDetector",
                    severity="high",
                    issues=["generator_boolean_context"],
                    confidence=confidence,
                    metadata={"code": pattern_info.get("code", "")[:100]}
                )
            except Exception:
                pass

        return finding


class ListConversionVisitor(ast.NodeVisitor):
    """AST visitor to find list() wrapping generator expressions."""

    def __init__(self, file_path: str):
        """Initialize visitor.

        Args:
            file_path: Path to file being analyzed
        """
        self.file_path = file_path
        self.current_function: Optional[str] = None
        self.current_function_line: Optional[int] = None
        self.current_class: Optional[str] = None
        self.current_class_line: Optional[int] = None
        self.list_conversions: List[Dict] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Visit class definition."""
        old_class = self.current_class
        old_line = self.current_class_line
        self.current_class = node.name
        self.current_class_line = node.lineno
        self.generic_visit(node)
        self.current_class = old_class
        self.current_class_line = old_line

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Visit function definition."""
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Visit async function definition."""
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef) -> None:
        """Process function."""
        old_func = self.current_function
        old_line = self.current_function_line
        self.current_function = node.name
        self.current_function_line = node.lineno
        self.generic_visit(node)
        self.current_function = old_func
        self.current_function_line = old_line

    def visit_Call(self, node: ast.Call) -> None:
        """Visit function call to check for list(generator)."""
        # Check if this is list(...)
        if isinstance(node.func, ast.Name) and node.func.id == "list":
            # Check if argument is a generator expression
            if node.args and isinstance(node.args[0], ast.GeneratorExp):
                self._record_list_conversion(node)

        self.generic_visit(node)

    def _record_list_conversion(self, node: ast.Call) -> None:
        """Record a list conversion pattern.

        Args:
            node: Call AST node
        """
        # Build qualified function name
        if self.current_function:
            if self.current_class and self.current_class_line:
                func_qualified = (
                    f"{self.file_path}::{self.current_class}:{self.current_class_line}"
                    f".{self.current_function}:{self.current_function_line}"
                )
            else:
                func_qualified = f"{self.file_path}::{self.current_function}:{self.current_function_line}"
        else:
            func_qualified = self.file_path

        try:
            code = ast.unparse(node)
        except Exception:
            code = "list(generator_expression)"

        self.list_conversions.append({
            "file_path": self.file_path,
            "line": node.lineno,
            "function_qualified": func_qualified,
            "code": code[:100],
        })


class BooleanContextVisitor(ast.NodeVisitor):
    """AST visitor to find generator expressions in boolean context."""

    def __init__(self, file_path: str):
        """Initialize visitor.

        Args:
            file_path: Path to file being analyzed
        """
        self.file_path = file_path
        self.current_function: Optional[str] = None
        self.current_function_line: Optional[int] = None
        self.current_class: Optional[str] = None
        self.current_class_line: Optional[int] = None
        self.boolean_contexts: List[Dict] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Visit class definition."""
        old_class = self.current_class
        old_line = self.current_class_line
        self.current_class = node.name
        self.current_class_line = node.lineno
        self.generic_visit(node)
        self.current_class = old_class
        self.current_class_line = old_line

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Visit function definition."""
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Visit async function definition."""
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef) -> None:
        """Process function."""
        old_func = self.current_function
        old_line = self.current_function_line
        self.current_function = node.name
        self.current_function_line = node.lineno
        self.generic_visit(node)
        self.current_function = old_func
        self.current_function_line = old_line

    def visit_If(self, node: ast.If) -> None:
        """Visit if statement."""
        self._check_boolean_context(node.test, "if")
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        """Visit while statement."""
        self._check_boolean_context(node.test, "while")
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        """Visit boolean operation (and/or)."""
        for value in node.values:
            self._check_boolean_context(value, "bool_op")
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        """Visit ternary expression."""
        self._check_boolean_context(node.test, "ternary")
        self.generic_visit(node)

    def _check_boolean_context(self, test_node: ast.expr, context_type: str) -> None:
        """Check if a test expression is a generator expression.

        Args:
            test_node: Test expression AST node
            context_type: Type of boolean context (if, while, etc.)
        """
        if isinstance(test_node, ast.GeneratorExp):
            self._record_boolean_context(test_node, context_type)

    def _record_boolean_context(self, node: ast.GeneratorExp, context_type: str) -> None:
        """Record a generator in boolean context.

        Args:
            node: GeneratorExp AST node
            context_type: Type of boolean context
        """
        # Build qualified function name
        if self.current_function:
            if self.current_class and self.current_class_line:
                func_qualified = (
                    f"{self.file_path}::{self.current_class}:{self.current_class_line}"
                    f".{self.current_function}:{self.current_function_line}"
                )
            else:
                func_qualified = f"{self.file_path}::{self.current_function}:{self.current_function_line}"
        else:
            func_qualified = self.file_path

        try:
            code = f"{context_type} ({ast.unparse(node)}):"
        except Exception:
            code = f"{context_type} (generator_expression):"

        self.boolean_contexts.append({
            "file_path": self.file_path,
            "line": node.lineno,
            "function_qualified": func_qualified,
            "context_type": context_type,
            "code": code[:100],
        })

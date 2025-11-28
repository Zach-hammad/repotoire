"""Test Smell detector for common test anti-patterns (REPO-223).

Detects common test smells that indicate problematic test design:
1. Over-mocked tests (5+ mocks) - tests that mock so much they test nothing
2. Flaky patterns - time.sleep(), datetime.now() without freezing
3. Assert-free tests - tests without assertions (test nothing)

These patterns lead to unreliable tests that either:
- Don't actually test the code under test (over-mocking)
- Fail intermittently (flaky patterns)
- Pass without verifying anything (no assertions)
"""

import ast
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph.base import DatabaseClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.models import CollaborationMetadata, Finding, Severity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class TestSmellDetector(CodeSmellDetector):
    """Detects test anti-patterns and smells in test files.

    Test smells detected:
    1. **Over-mocked tests**: Functions with 5+ @patch decorators
       - Sign that too much is being mocked, making the test meaningless
       - Often indicates poor separation of concerns in the code under test

    2. **Flaky patterns**: Usage of time-sensitive operations without freezing
       - time.sleep() - causes slow and potentially flaky tests
       - datetime.now() / date.today() - non-deterministic results
       - time.time() - non-deterministic timing

    3. **Assert-free tests**: Test functions without any assertions
       - Tests that don't verify anything
       - May pass even when code is broken

    Thresholds:
        - over_mock_threshold: Number of mocks to trigger over-mock finding (default: 5)
    """

    # Mock-related decorator patterns
    MOCK_DECORATORS = {
        "patch",
        "mock.patch",
        "unittest.mock.patch",
        "patch.object",
        "mock.patch.object",
        "Mock",
        "MagicMock",
        "patch.dict",
        "mock.patch.dict",
    }

    # Flaky function call patterns
    FLAKY_CALLS = {
        "time.sleep": "Use pytest-timeout or freezegun for time-dependent tests",
        "sleep": "Use pytest-timeout or freezegun for time-dependent tests",
        "datetime.now": "Use freezegun (@freeze_time) to freeze time in tests",
        "datetime.utcnow": "Use freezegun (@freeze_time) to freeze time in tests",
        "date.today": "Use freezegun (@freeze_time) to freeze time in tests",
        "time.time": "Use freezegun or mock time.time for deterministic tests",
        "random.random": "Seed random with a fixed value or mock for determinism",
        "random.randint": "Seed random with a fixed value or mock for determinism",
        "random.choice": "Seed random with a fixed value or mock for determinism",
        "uuid.uuid4": "Mock uuid4 for deterministic test results",
    }

    # Assertion patterns to look for
    ASSERTION_PATTERNS = {
        "assert",  # Plain assert
        "assertEqual",
        "assertEquals",
        "assertNotEqual",
        "assertTrue",
        "assertFalse",
        "assertIs",
        "assertIsNot",
        "assertIsNone",
        "assertIsNotNone",
        "assertIn",
        "assertNotIn",
        "assertIsInstance",
        "assertRaises",
        "assertWarns",
        "pytest.raises",
        "pytest.warns",
        "expect",  # pytest-expect
        "should",  # pytest-should
        "verify",
        "assert_called",
        "assert_called_with",
        "assert_called_once",
        "assert_called_once_with",
        "assert_any_call",
        "assert_has_calls",
        "assert_not_called",
    }

    THRESHOLDS = {
        "over_mock_threshold": 5,  # Mocks to trigger over-mock finding
        "min_lines_for_no_assert": 5,  # Min lines to report assert-free test
    }

    def __init__(
        self,
        neo4j_client: DatabaseClient,
        detector_config: Optional[dict] = None,
        enricher: Optional[GraphEnricher] = None
    ):
        """Initialize test smell detector.

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
        self.over_mock_threshold = config.get(
            "over_mock_threshold",
            self.THRESHOLDS["over_mock_threshold"]
        )
        self.min_lines_for_no_assert = config.get(
            "min_lines_for_no_assert",
            self.THRESHOLDS["min_lines_for_no_assert"]
        )

    def detect(self) -> List[Finding]:
        """Detect test smells in test files.

        Uses a hybrid approach:
        1. Query graph for test functions with stored mock_count property
        2. Analyze test files directly for pattern detection

        Returns:
            List of findings for detected test smells
        """
        logger.info("Running TestSmellDetector")
        findings: List[Finding] = []

        # Get test files from graph
        test_files = self._get_test_files()

        if not test_files:
            logger.debug("No test files found in graph")
            return findings

        # Analyze each test file
        for file_path in test_files:
            file_findings = self._analyze_test_file(file_path)
            findings.extend(file_findings)

        logger.info(f"Found {len(findings)} test smell(s)")
        return findings

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity based on smell type.

        Args:
            finding: Finding to assess

        Returns:
            Severity level
        """
        smell_type = finding.graph_context.get("smell_type", "")

        if smell_type == "over_mocked":
            mock_count = finding.graph_context.get("mock_count", 0)
            if mock_count >= 8:
                return Severity.HIGH
            return Severity.MEDIUM
        elif smell_type == "flaky_pattern":
            return Severity.MEDIUM
        elif smell_type == "no_assertions":
            return Severity.LOW

        return Severity.LOW

    def _get_test_files(self) -> List[str]:
        """Get list of test files from the graph.

        Returns:
            List of test file paths
        """
        query = """
        MATCH (f:File)
        WHERE (f.is_test = true OR f.filePath CONTAINS 'test' OR f.name STARTS WITH 'test_')
          AND (f.language = 'python' OR f.filePath ENDS WITH '.py')
        RETURN f.filePath AS file_path
        LIMIT 500
        """

        try:
            results = self.db.execute_query(query)
            return [r["file_path"] for r in results if r.get("file_path")]
        except Exception as e:
            logger.warning(f"Failed to query test files: {e}")
            return []

    def _analyze_test_file(self, file_path: str) -> List[Finding]:
        """Analyze a test file for test smells.

        Args:
            file_path: Path to test file

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

            # Visit all test functions
            visitor = TestSmellVisitor(file_path, source)
            visitor.visit(tree)

            # Process over-mocked tests
            for test_info in visitor.over_mocked_tests:
                if test_info["mock_count"] >= self.over_mock_threshold:
                    finding = self._create_over_mocked_finding(test_info)
                    findings.append(finding)

            # Process flaky patterns
            for test_info in visitor.flaky_tests:
                finding = self._create_flaky_finding(test_info)
                findings.append(finding)

            # Process assert-free tests
            for test_info in visitor.no_assert_tests:
                if test_info["line_count"] >= self.min_lines_for_no_assert:
                    finding = self._create_no_assert_finding(test_info)
                    findings.append(finding)

        except SyntaxError as e:
            logger.debug(f"Syntax error parsing {file_path}: {e}")
        except Exception as e:
            logger.debug(f"Error analyzing test file {file_path}: {e}")

        return findings

    def _create_over_mocked_finding(self, test_info: Dict) -> Finding:
        """Create a finding for over-mocked test.

        Args:
            test_info: Dictionary with test information

        Returns:
            Finding object
        """
        finding_id = str(uuid.uuid4())
        mock_count = test_info["mock_count"]

        # Determine severity
        if mock_count >= 8:
            severity = Severity.HIGH
        elif mock_count >= 6:
            severity = Severity.MEDIUM
        else:
            severity = Severity.MEDIUM

        decorators_display = "\n".join(
            f"- `@{d}`" for d in test_info["mock_decorators"][:7]
        )
        if len(test_info["mock_decorators"]) > 7:
            decorators_display += f"\n- ... and {len(test_info['mock_decorators']) - 7} more"

        description = (
            f"Test `{test_info['name']}` has **{mock_count} mock decorators**.\n\n"
            f"Decorators:\n{decorators_display}\n\n"
            f"When a test requires this many mocks, it often indicates:\n"
            f"- The test is not testing the actual behavior\n"
            f"- The code under test has too many dependencies\n"
            f"- The test might pass even when the code is broken"
        )

        suggestion = (
            "**Consider these refactoring approaches:**\n\n"
            "1. **Split the test**: Break into smaller, focused tests that each mock fewer dependencies\n\n"
            "2. **Refactor the code under test**: High mock count often indicates the function does too much. "
            "Extract dependencies into separate classes/functions.\n\n"
            "3. **Use integration tests**: If testing the integration is important, consider fewer mocks "
            "and more real objects.\n\n"
            "4. **Use dependency injection**: Make dependencies explicit parameters rather than internal calls."
        )

        finding = Finding(
            id=finding_id,
            detector="TestSmellDetector",
            severity=severity,
            title=f"Over-mocked test: {test_info['name']} ({mock_count} mocks)",
            description=description,
            affected_nodes=[test_info["qualified_name"]],
            affected_files=[test_info["file_path"]],
            line_start=test_info["line"],
            graph_context={
                "smell_type": "over_mocked",
                "mock_count": mock_count,
                "mock_decorators": test_info["mock_decorators"],
                "test_name": test_info["name"],
            },
            suggested_fix=suggestion,
            estimated_effort="Medium (1-2 hours)",
            created_at=datetime.now(),
        )

        # Add collaboration metadata
        confidence = 0.90  # High confidence - clear pattern
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="TestSmellDetector",
            confidence=confidence,
            evidence=[f"mock_count_{mock_count}", "over_mocked_test"],
            tags=["test_smell", "over_mocked", "test_quality"]
        ))

        # Flag entity
        if self.enricher:
            try:
                self.enricher.flag_entity(
                    entity_qualified_name=test_info["qualified_name"],
                    detector="TestSmellDetector",
                    severity=severity.value,
                    issues=["over_mocked_test"],
                    confidence=confidence,
                    metadata={"mock_count": mock_count}
                )
            except Exception:
                pass

        return finding

    def _create_flaky_finding(self, test_info: Dict) -> Finding:
        """Create a finding for flaky test pattern.

        Args:
            test_info: Dictionary with test information

        Returns:
            Finding object
        """
        finding_id = str(uuid.uuid4())
        flaky_calls = test_info["flaky_calls"]

        # Build calls display
        calls_display = []
        suggestions_set: Set[str] = set()
        for call_name, suggestion in flaky_calls:
            calls_display.append(f"- `{call_name}` (line {test_info.get('call_lines', {}).get(call_name, '?')})")
            suggestions_set.add(suggestion)

        description = (
            f"Test `{test_info['name']}` uses potentially flaky patterns:\n\n"
            + "\n".join(calls_display) +
            "\n\nThese patterns can cause tests to:\n"
            "- Pass on one machine but fail on another\n"
            "- Fail intermittently based on timing\n"
            "- Produce non-deterministic results"
        )

        suggestion = "**Recommended fixes:**\n\n" + "\n".join(
            f"- {s}" for s in suggestions_set
        )

        if "time.sleep" in [c[0] for c in flaky_calls]:
            suggestion += (
                "\n\n**For time.sleep specifically:**\n"
                "- Use `pytest-timeout` for timeouts\n"
                "- Use `freezegun` to freeze time\n"
                "- Mock the sleep call if testing retry logic"
            )

        severity = Severity.MEDIUM if len(flaky_calls) >= 2 else Severity.LOW

        finding = Finding(
            id=finding_id,
            detector="TestSmellDetector",
            severity=severity,
            title=f"Flaky test pattern in {test_info['name']}",
            description=description,
            affected_nodes=[test_info["qualified_name"]],
            affected_files=[test_info["file_path"]],
            line_start=test_info["line"],
            graph_context={
                "smell_type": "flaky_pattern",
                "flaky_calls": [c[0] for c in flaky_calls],
                "test_name": test_info["name"],
            },
            suggested_fix=suggestion,
            estimated_effort="Small (30-60 minutes)",
            created_at=datetime.now(),
        )

        # Add collaboration metadata
        confidence = 0.80  # Good confidence - known flaky patterns
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="TestSmellDetector",
            confidence=confidence,
            evidence=["flaky_pattern"] + [c[0] for c in flaky_calls],
            tags=["test_smell", "flaky", "test_quality", "non_deterministic"]
        ))

        # Flag entity
        if self.enricher:
            try:
                self.enricher.flag_entity(
                    entity_qualified_name=test_info["qualified_name"],
                    detector="TestSmellDetector",
                    severity=severity.value,
                    issues=["flaky_test"],
                    confidence=confidence,
                    metadata={"flaky_calls": [c[0] for c in flaky_calls]}
                )
            except Exception:
                pass

        return finding

    def _create_no_assert_finding(self, test_info: Dict) -> Finding:
        """Create a finding for test without assertions.

        Args:
            test_info: Dictionary with test information

        Returns:
            Finding object
        """
        finding_id = str(uuid.uuid4())

        description = (
            f"Test `{test_info['name']}` has **no assertions** ({test_info['line_count']} lines of code).\n\n"
            "A test without assertions:\n"
            "- Will always pass regardless of code behavior\n"
            "- May give false confidence in code correctness\n"
            "- Provides no regression protection"
        )

        suggestion = (
            "**Add appropriate assertions:**\n\n"
            "1. **For return values:**\n"
            "   ```python\n"
            "   result = my_function()\n"
            "   assert result == expected_value\n"
            "   ```\n\n"
            "2. **For exceptions:**\n"
            "   ```python\n"
            "   with pytest.raises(ValueError):\n"
            "       my_function(invalid_input)\n"
            "   ```\n\n"
            "3. **For side effects:**\n"
            "   ```python\n"
            "   my_function()\n"
            "   mock_dependency.assert_called_once_with(expected_args)\n"
            "   ```\n\n"
            "4. **For state changes:**\n"
            "   ```python\n"
            "   obj.update(new_value)\n"
            "   assert obj.value == new_value\n"
            "   ```"
        )

        finding = Finding(
            id=finding_id,
            detector="TestSmellDetector",
            severity=Severity.LOW,
            title=f"Test without assertions: {test_info['name']}",
            description=description,
            affected_nodes=[test_info["qualified_name"]],
            affected_files=[test_info["file_path"]],
            line_start=test_info["line"],
            graph_context={
                "smell_type": "no_assertions",
                "line_count": test_info["line_count"],
                "test_name": test_info["name"],
            },
            suggested_fix=suggestion,
            estimated_effort="Small (15-30 minutes)",
            created_at=datetime.now(),
        )

        # Add collaboration metadata
        confidence = 0.75  # Moderate confidence - might have implicit assertions
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="TestSmellDetector",
            confidence=confidence,
            evidence=["no_assertions", f"line_count_{test_info['line_count']}"],
            tags=["test_smell", "no_assertions", "test_quality"]
        ))

        return finding


class TestSmellVisitor(ast.NodeVisitor):
    """AST visitor to detect test smells in test functions."""

    def __init__(self, file_path: str, source: str):
        """Initialize test smell visitor.

        Args:
            file_path: Path to the file being analyzed
            source: Source code of the file
        """
        self.file_path = file_path
        self.source = source
        self.current_class: Optional[str] = None
        self.current_class_line: Optional[int] = None

        # Results
        self.over_mocked_tests: List[Dict] = []
        self.flaky_tests: List[Dict] = []
        self.no_assert_tests: List[Dict] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Visit class definition (test class)."""
        old_class = self.current_class
        old_class_line = self.current_class_line
        self.current_class = node.name
        self.current_class_line = node.lineno
        self.generic_visit(node)
        self.current_class = old_class
        self.current_class_line = old_class_line

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Visit function definition."""
        self._analyze_test_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Visit async function definition."""
        self._analyze_test_function(node)

    def _analyze_test_function(self, node: ast.FunctionDef) -> None:
        """Analyze a test function for smells.

        Args:
            node: Function AST node
        """
        # Only analyze test functions
        if not self._is_test_function(node.name):
            return

        # Build qualified name
        if self.current_class and self.current_class_line:
            qualified_name = (
                f"{self.file_path}::{self.current_class}:{self.current_class_line}"
                f".{node.name}:{node.lineno}"
            )
        else:
            qualified_name = f"{self.file_path}::{node.name}:{node.lineno}"

        test_info = {
            "name": node.name,
            "qualified_name": qualified_name,
            "file_path": self.file_path,
            "line": node.lineno,
            "line_count": (node.end_lineno or node.lineno) - node.lineno + 1,
        }

        # Check for over-mocking
        mock_decorators = self._get_mock_decorators(node)
        if mock_decorators:
            test_info_copy = test_info.copy()
            test_info_copy["mock_count"] = len(mock_decorators)
            test_info_copy["mock_decorators"] = mock_decorators
            self.over_mocked_tests.append(test_info_copy)

        # Check for flaky patterns
        flaky_calls = self._find_flaky_calls(node)
        if flaky_calls:
            test_info_copy = test_info.copy()
            test_info_copy["flaky_calls"] = flaky_calls
            test_info_copy["call_lines"] = {}
            self.flaky_tests.append(test_info_copy)

        # Check for missing assertions
        has_assertions = self._has_assertions(node)
        if not has_assertions and test_info["line_count"] > 1:
            self.no_assert_tests.append(test_info.copy())

    def _is_test_function(self, name: str) -> bool:
        """Check if function name indicates a test.

        Args:
            name: Function name

        Returns:
            True if this is a test function
        """
        return name.startswith("test_") or name.startswith("test")

    def _get_mock_decorators(self, node: ast.FunctionDef) -> List[str]:
        """Get list of mock-related decorators on a function.

        Args:
            node: Function AST node

        Returns:
            List of mock decorator names
        """
        mock_decorators = []

        for decorator in node.decorator_list:
            decorator_name = self._get_decorator_name(decorator)
            if self._is_mock_decorator(decorator_name):
                mock_decorators.append(decorator_name)

        return mock_decorators

    def _get_decorator_name(self, decorator: ast.expr) -> str:
        """Extract decorator name from decorator node.

        Args:
            decorator: Decorator AST node

        Returns:
            Decorator name string
        """
        if isinstance(decorator, ast.Name):
            return decorator.id
        elif isinstance(decorator, ast.Attribute):
            parts = []
            node = decorator
            while isinstance(node, ast.Attribute):
                parts.append(node.attr)
                node = node.value
            if isinstance(node, ast.Name):
                parts.append(node.id)
            return ".".join(reversed(parts))
        elif isinstance(decorator, ast.Call):
            return self._get_decorator_name(decorator.func)
        return ""

    def _is_mock_decorator(self, decorator_name: str) -> bool:
        """Check if decorator name is a mock-related decorator.

        Args:
            decorator_name: Decorator name

        Returns:
            True if this is a mock decorator
        """
        # Check exact match
        if decorator_name in TestSmellDetector.MOCK_DECORATORS:
            return True

        # Check if it contains "patch" (common pattern)
        decorator_lower = decorator_name.lower()
        if "patch" in decorator_lower:
            return True

        return False

    def _find_flaky_calls(self, node: ast.FunctionDef) -> List[tuple]:
        """Find flaky function calls in a test function.

        Args:
            node: Function AST node

        Returns:
            List of (call_name, suggestion) tuples
        """
        flaky_calls = []

        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                call_name = self._get_call_name(child)
                if call_name:
                    for flaky_pattern, suggestion in TestSmellDetector.FLAKY_CALLS.items():
                        if call_name == flaky_pattern or call_name.endswith(f".{flaky_pattern}"):
                            flaky_calls.append((flaky_pattern, suggestion))
                            break

        return flaky_calls

    def _get_call_name(self, node: ast.Call) -> Optional[str]:
        """Get the name of a function call.

        Args:
            node: Call AST node

        Returns:
            Call name or None
        """
        if isinstance(node.func, ast.Name):
            return node.func.id
        elif isinstance(node.func, ast.Attribute):
            parts = []
            current = node.func
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            return ".".join(reversed(parts))
        return None

    def _has_assertions(self, node: ast.FunctionDef) -> bool:
        """Check if a test function has any assertions.

        Args:
            node: Function AST node

        Returns:
            True if function has assertions
        """
        for child in ast.walk(node):
            # Check for assert statements
            if isinstance(child, ast.Assert):
                return True

            # Check for assertion method calls
            if isinstance(child, ast.Call):
                call_name = self._get_call_name(child)
                if call_name:
                    # Check if any part of the call matches assertion patterns
                    for pattern in TestSmellDetector.ASSERTION_PATTERNS:
                        if pattern in call_name.lower():
                            return True

            # Check for pytest.raises context manager
            if isinstance(child, ast.With):
                for item in child.items:
                    if isinstance(item.context_expr, ast.Call):
                        call_name = self._get_call_name(item.context_expr)
                        if call_name and "raises" in call_name.lower():
                            return True

        return False

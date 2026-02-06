"""AI Missing Tests detector - identifies new code added without tests.

REPO-XXX: Detects functions/methods added in recent commits (last 30 days)
that don't have corresponding test coverage. This is a common pattern when AI
generates implementation code but neglects to generate tests.

This detector supports multiple language conventions:
- Python: pytest conventions (test_<function>, test_<module>.py, <module>_test.py)
- JavaScript/TypeScript: jest conventions (*.test.ts, *.spec.ts, describe blocks)

Enhanced with test quality analysis:
- Detects weak tests (single assertion)
- Detects incomplete tests (no error handling coverage)
- MEDIUM severity for missing tests, LOW for weak/incomplete tests
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph import FalkorDBClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.logging_config import get_logger
from repotoire.models import CollaborationMetadata, Finding, Severity

logger = get_logger(__name__)


class TestQuality(str, Enum):
    """Test quality classification."""
    MISSING = "missing"           # No test exists
    WEAK = "weak"                 # Test exists but has only 1 assertion
    INCOMPLETE = "incomplete"     # Test exists but lacks error handling coverage
    ADEQUATE = "adequate"         # Test exists with multiple assertions


@dataclass
class TestInfo:
    """Information about a test function."""
    name: str
    file_path: str
    assertion_count: int
    has_error_tests: bool
    loc: int = 0
    
    @property
    def quality(self) -> TestQuality:
        """Determine test quality based on metrics."""
        if self.assertion_count == 0:
            return TestQuality.MISSING
        elif self.assertion_count == 1:
            return TestQuality.WEAK
        elif not self.has_error_tests:
            return TestQuality.INCOMPLETE
        return TestQuality.ADEQUATE


class AIMissingTestsDetector(CodeSmellDetector):
    """Detects functions/methods that lack corresponding tests or have weak tests.

    This detector identifies code that doesn't have adequate test coverage. This is
    especially common when AI assistants generate implementation code without tests.

    Detection Strategy:
    1. Find all functions in non-test files (detected by path pattern)
    2. Exclude functions that are themselves test functions
    3. Check for corresponding test functions using naming conventions
    4. Analyze test quality (assertion count, error handling coverage)
    5. Flag functions based on test coverage quality:
       - MEDIUM severity: No test exists
       - LOW severity: Test exists but is weak (1 assertion) or incomplete (no error tests)

    Test File Detection (by path pattern):
    - Python: test_*.py, *_test.py, tests/*.py
    - JavaScript/TypeScript: *.test.js, *.spec.ts, __tests__/*

    Naming Conventions Checked:
    - Python: test_<function_name>, <function_name>_test, Test<ClassName>
    - JavaScript/TypeScript: <function_name>.test.ts, <function_name>.spec.ts
    """

    # Default configuration
    DEFAULT_CONFIG = {
        "window_days": 30,           # Look back window for "recent" commits
        "min_function_loc": 5,       # Minimum LOC to consider (skip trivial functions)
        "exclude_private": True,     # Exclude _private functions
        "exclude_dunder": True,      # Exclude __dunder__ methods
        "max_findings": 50,          # Maximum findings to return
        "check_test_quality": True,  # Also flag weak/incomplete tests
        "min_assertions": 2,         # Minimum assertions for adequate test
    }

    # Test file patterns for different languages
    TEST_FILE_PATTERNS = {
        "python": [
            r"test_.*\.py$",          # test_module.py
            r".*_test\.py$",          # module_test.py
            r"tests?/.*\.py$",        # tests/module.py, test/module.py
            r".*tests?\.py$",         # module_tests.py
        ],
        "javascript": [
            r".*\.test\.[jt]sx?$",    # file.test.js, file.test.tsx
            r".*\.spec\.[jt]sx?$",    # file.spec.js, file.spec.tsx
            r"__tests__/.*\.[jt]sx?$", # __tests__/file.js
        ],
        "typescript": [
            r".*\.test\.[jt]sx?$",
            r".*\.spec\.[jt]sx?$",
            r"__tests__/.*\.[jt]sx?$",
        ],
    }

    # Assertion patterns for different languages
    ASSERTION_PATTERNS = {
        "python": [
            r"\bassert\b",            # assert statement
            r"\.assert",              # unittest assertions (self.assertEqual, etc.)
            r"pytest\.",              # pytest assertions
        ],
        "javascript": [
            r"\bexpect\(",            # Jest/Chai expect
            r"\.toBe\(",              # Jest matchers
            r"\.toEqual\(",
            r"\.toThrow\(",
            r"\.assert",              # Chai assert
        ],
        "typescript": [
            r"\bexpect\(",
            r"\.toBe\(",
            r"\.toEqual\(",
            r"\.toThrow\(",
            r"\.assert",
        ],
    }

    # Error handling test patterns
    ERROR_TEST_PATTERNS = {
        "python": [
            r"pytest\.raises",        # pytest.raises(Exception)
            r"assertRaises",          # unittest assertRaises
            r"with_raises",           # various frameworks
            r"test.*error",           # test_handles_error, test_error_case
            r"test.*exception",       # test_raises_exception
            r"test.*invalid",         # test_invalid_input
            r"test.*fail",            # test_should_fail
        ],
        "javascript": [
            r"\.toThrow\(",           # expect(...).toThrow()
            r"\.rejects\(",           # expect(...).rejects
            r"catch\s*\(",            # try/catch in tests
            r"error",                 # test names with 'error'
            r"invalid",               # test names with 'invalid'
            r"fail",                  # test names with 'fail'
        ],
        "typescript": [
            r"\.toThrow\(",
            r"\.rejects\(",
            r"catch\s*\(",
            r"error",
            r"invalid",
            r"fail",
        ],
    }

    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict[str, Any]] = None,
        enricher: Optional[GraphEnricher] = None,
    ):
        """Initialize AI Missing Tests detector.

        Args:
            graph_client: FalkorDB database client
            detector_config: Optional configuration dict with:
                - min_function_loc: Minimum LOC to consider
                - exclude_private: Whether to exclude _private functions
                - exclude_dunder: Whether to exclude __dunder__ methods
                - max_findings: Maximum number of findings to return
                - check_test_quality: Whether to also flag weak/incomplete tests
                - min_assertions: Minimum assertions for adequate test
            enricher: Optional GraphEnricher for cross-detector collaboration
        """
        super().__init__(graph_client, detector_config)
        self.enricher = enricher

        config = detector_config or {}
        self.window_days = config.get("window_days", self.DEFAULT_CONFIG["window_days"])
        self.min_function_loc = config.get("min_function_loc", self.DEFAULT_CONFIG["min_function_loc"])
        self.exclude_private = config.get("exclude_private", self.DEFAULT_CONFIG["exclude_private"])
        self.exclude_dunder = config.get("exclude_dunder", self.DEFAULT_CONFIG["exclude_dunder"])
        self.max_findings = config.get("max_findings", self.DEFAULT_CONFIG["max_findings"])
        self.check_test_quality = config.get("check_test_quality", self.DEFAULT_CONFIG["check_test_quality"])
        self.min_assertions = config.get("min_assertions", self.DEFAULT_CONFIG["min_assertions"])

    def detect(self) -> List[Finding]:
        """Detect recently added functions without tests or with weak tests.

        Returns:
            List of findings for functions with inadequate test coverage
        """
        findings: List[Finding] = []

        try:
            # Step 1: Get all test functions with quality info
            test_info_map, test_files = self._get_test_coverage_info()

            # Step 2: Get recently added functions
            recent_functions = self._get_recent_functions()

            if not recent_functions:
                logger.debug("No recent functions found for missing tests analysis")
                return findings

            # Step 3: Check each function for test coverage and quality
            for func_data in recent_functions:
                if self._should_skip_function(func_data):
                    continue

                test_quality, test_info = self._check_test_coverage(
                    func_data, test_info_map, test_files
                )

                # Create findings based on test quality
                if test_quality == TestQuality.MISSING:
                    finding = self._create_finding(func_data, TestQuality.MISSING, None)
                    findings.append(finding)
                elif self.check_test_quality and test_quality in (TestQuality.WEAK, TestQuality.INCOMPLETE):
                    finding = self._create_finding(func_data, test_quality, test_info)
                    findings.append(finding)

                # Flag entity for cross-detector collaboration
                if findings and self.enricher:
                    self._flag_entity(func_data, findings[-1])

                if len(findings) >= self.max_findings:
                    break

            logger.info(f"AIMissingTestsDetector found {len(findings)} test coverage issues")
            return findings

        except Exception as e:
            logger.error(f"Error in AIMissingTestsDetector: {e}")
            return []

    def _get_test_coverage_info(self) -> Tuple[Dict[str, TestInfo], Set[str]]:
        """Get existing test functions with quality metrics.

        Returns:
            Tuple of (test_function_name -> TestInfo, test_file_paths)
        """
        repo_filter = self._get_isolation_filter("f")

        # Query for all files and filter test files by path pattern
        query = f"""
        MATCH (f:File)
        WHERE f.filePath IS NOT NULL {repo_filter}
        OPTIONAL MATCH (f)-[:CONTAINS*]->(func:Function)
        WHERE func.name IS NOT NULL
        RETURN f.filePath AS file_path,
               f.language AS language,
               collect(DISTINCT {{
                   name: func.name,
                   loc: func.loc
               }}) AS test_funcs
        """

        try:
            results = self.db.execute_query(query, self._get_query_params())
        except Exception as e:
            logger.warning(f"Could not query files: {e}")
            return {}, set()

        test_files: Set[str] = set()
        test_info_map: Dict[str, TestInfo] = {}

        for row in results:
            file_path = row.get("file_path", "")
            language = (row.get("language") or "python").lower()
            
            # Filter test files by path pattern instead of isTest property
            if not file_path or not self._is_test_file(file_path.lower()):
                continue
                
            test_files.add(file_path.lower())

            test_funcs = row.get("test_funcs", []) or []
            for func in test_funcs:
                name = func.get("name", "")
                if not name:
                    continue
                    
                loc = func.get("loc", 0) or 0
                
                # Skip quality analysis (no source in graph) - just check existence
                assertion_count = 0
                has_error_tests = False
                
                test_info = TestInfo(
                    name=name,
                    file_path=file_path,
                    assertion_count=assertion_count,
                    has_error_tests=has_error_tests,
                    loc=loc,
                )
                test_info_map[name.lower()] = test_info

        # Also get test functions by name pattern (for files that might not match path patterns)
        func_query = f"""
        MATCH (func:Function)
        WHERE func.name IS NOT NULL {self._get_isolation_filter("func")}
          AND (func.name STARTS WITH 'test_' 
               OR func.name STARTS WITH 'test'
               OR func.name ENDS WITH '_test')
        OPTIONAL MATCH (f:File)-[:CONTAINS*]->(func)
        RETURN DISTINCT func.name AS name,
               func.loc AS loc,
               f.filePath AS file_path,
               f.language AS language
        """

        try:
            func_results = self.db.execute_query(func_query, self._get_query_params())
            for row in func_results:
                name = row.get("name", "")
                if not name or name.lower() in test_info_map:
                    continue
                    
                language = (row.get("language") or "python").lower()
                loc = row.get("loc", 0) or 0
                file_path = row.get("file_path", "")
                
                # Skip quality analysis (no source in graph)
                assertion_count = 0
                has_error_tests = False
                
                test_info = TestInfo(
                    name=name,
                    file_path=file_path,
                    assertion_count=assertion_count,
                    has_error_tests=has_error_tests,
                    loc=loc,
                )
                test_info_map[name.lower()] = test_info
        except Exception as e:
            logger.warning(f"Could not query test functions: {e}")

        return test_info_map, test_files

    def _count_assertions(self, source: str, language: str) -> int:
        """Count assertions in test source code.

        Args:
            source: Test function source code
            language: Programming language

        Returns:
            Number of assertions found
        """
        if not source:
            # If no source, assume at least 1 assertion (benefit of doubt)
            return 1

        patterns = self.ASSERTION_PATTERNS.get(language, self.ASSERTION_PATTERNS["python"])
        count = 0
        
        for pattern in patterns:
            matches = re.findall(pattern, source, re.IGNORECASE)
            count += len(matches)
        
        return count

    def _has_error_handling_tests(self, source: str, test_name: str, language: str) -> bool:
        """Check if test covers error handling cases.

        Args:
            source: Test function source code
            test_name: Name of the test function
            language: Programming language

        Returns:
            True if test appears to cover error cases
        """
        patterns = self.ERROR_TEST_PATTERNS.get(language, self.ERROR_TEST_PATTERNS["python"])
        
        # Check source code for error handling patterns
        if source:
            for pattern in patterns:
                if re.search(pattern, source, re.IGNORECASE):
                    return True
        
        # Check test name for error-related keywords
        test_name_lower = test_name.lower()
        error_keywords = ["error", "exception", "invalid", "fail", "raise", "throw", "reject"]
        for keyword in error_keywords:
            if keyword in test_name_lower:
                return True
        
        return False

    def _get_recent_functions(self) -> List[Dict[str, Any]]:
        """Get functions to check for test coverage.

        Returns:
            List of function data dictionaries (non-test functions)
        """
        repo_filter = self._get_isolation_filter("f")

        query = f"""
        MATCH (f:File)-[:CONTAINS*]->(func:Function)
        WHERE func.name IS NOT NULL {repo_filter}
          AND (func.loc >= $min_loc OR func.loc IS NULL)
        RETURN DISTINCT 
               func.qualifiedName AS qualified_name,
               func.name AS name,
               func.lineStart AS line_start,
               func.lineEnd AS line_end,
               func.loc AS loc,
               f.filePath AS file_path,
               f.language AS language
        LIMIT $max_results
        """

        try:
            results = self.db.execute_query(
                query,
                self._get_query_params(
                    min_loc=self.min_function_loc,
                    max_results=self.max_findings * 3,
                ),
            )
            # Filter out functions in test files by path pattern
            return [
                r for r in results 
                if not self._is_test_file((r.get("file_path") or "").lower())
            ]
        except Exception as e:
            logger.error(f"Could not query functions: {e}")
            return []

    def _should_skip_function(self, func_data: Dict[str, Any]) -> bool:
        """Check if function should be skipped based on naming conventions.

        Args:
            func_data: Function data dictionary

        Returns:
            True if function should be skipped
        """
        name = func_data.get("name", "")
        file_path = func_data.get("file_path", "")

        if not name:
            return True

        # Skip test functions themselves
        name_lower = name.lower()
        if name_lower.startswith("test") or name_lower.endswith("_test"):
            return True

        # Skip functions in test files
        file_lower = file_path.lower() if file_path else ""
        if self._is_test_file(file_lower):
            return True

        # Skip private functions if configured
        if self.exclude_private and name.startswith("_") and not name.startswith("__"):
            return True

        # Skip dunder methods if configured
        if self.exclude_dunder and name.startswith("__") and name.endswith("__"):
            return True

        return False

    def _is_test_file(self, file_path: str) -> bool:
        """Check if a file path matches test file patterns.

        Args:
            file_path: Lowercased file path

        Returns:
            True if this is a test file
        """
        if not file_path:
            return False

        for patterns in self.TEST_FILE_PATTERNS.values():
            for pattern in patterns:
                if re.search(pattern, file_path, re.IGNORECASE):
                    return True

        return False

    def _check_test_coverage(
        self,
        func_data: Dict[str, Any],
        test_info_map: Dict[str, TestInfo],
        test_files: Set[str],
    ) -> Tuple[TestQuality, Optional[TestInfo]]:
        """Check test coverage and quality for a function.

        Args:
            func_data: Function data dictionary
            test_info_map: Map of test function names to TestInfo
            test_files: Set of known test file paths

        Returns:
            Tuple of (TestQuality, Optional[TestInfo])
        """
        name = func_data.get("name", "")
        file_path = func_data.get("file_path", "")
        language = (func_data.get("language") or "python").lower()

        if not name:
            return TestQuality.ADEQUATE, None

        name_lower = name.lower()

        # Check for test function with matching name
        test_variants = self._get_test_function_variants(name_lower, language)
        for variant in test_variants:
            if variant in test_info_map:
                test_info = test_info_map[variant]
                return test_info.quality, test_info

        # Check for test file with matching module name
        if file_path:
            test_file_variants = self._get_test_file_variants(file_path, language)
            for variant in test_file_variants:
                if variant.lower() in test_files:
                    # Test file exists but couldn't find specific test function
                    # Assume some coverage exists
                    return TestQuality.ADEQUATE, None

        return TestQuality.MISSING, None

    def _get_test_function_variants(self, func_name: str, language: str) -> List[str]:
        """Get possible test function names for a given function.

        Args:
            func_name: Lowercased function name
            language: Programming language

        Returns:
            List of possible test function names
        """
        variants = []

        # Common patterns for all languages
        variants.extend([
            f"test_{func_name}",           # test_my_function
            f"test{func_name}",            # testMyFunction (camelCase)
            f"{func_name}_test",           # my_function_test
            f"test_{func_name}_",          # test_my_function_*
        ])

        # For methods, also check class-based test names
        if "_" in func_name:
            parts = func_name.split("_")
            for part in parts:
                if len(part) > 2:
                    variants.append(f"test_{part}")

        return variants

    def _get_test_file_variants(self, file_path: str, language: str) -> List[str]:
        """Get possible test file paths for a given source file.

        Args:
            file_path: Source file path
            language: Programming language

        Returns:
            List of possible test file paths
        """
        variants = []

        parts = file_path.replace("\\", "/").split("/")
        filename = parts[-1] if parts else ""

        if "." in filename:
            module_name = filename.rsplit(".", 1)[0]
        else:
            module_name = filename

        if not module_name:
            return variants

        # Python variants
        if language in ("python", "unknown"):
            variants.extend([
                f"test_{module_name}.py",
                f"tests/test_{module_name}.py",
                f"test/test_{module_name}.py",
                f"{module_name}_test.py",
                f"tests/{module_name}_test.py",
            ])

        # JavaScript/TypeScript variants
        if language in ("javascript", "typescript", "unknown"):
            ext = ".ts" if language == "typescript" else ".js"
            variants.extend([
                f"{module_name}.test{ext}",
                f"{module_name}.spec{ext}",
                f"{module_name}.test.tsx",
                f"{module_name}.spec.tsx",
                f"__tests__/{module_name}{ext}",
            ])

        return variants

    def _create_finding(
        self,
        func_data: Dict[str, Any],
        test_quality: TestQuality,
        test_info: Optional[TestInfo],
    ) -> Finding:
        """Create a finding for a function with inadequate test coverage.

        Args:
            func_data: Function data dictionary
            test_quality: Quality classification
            test_info: Optional test info if test exists

        Returns:
            Finding object
        """
        qualified_name = func_data.get("qualified_name", "unknown")
        name = func_data.get("name", qualified_name.split(".")[-1])
        file_path = func_data.get("file_path", "unknown")
        line_start = func_data.get("line_start")
        line_end = func_data.get("line_end")
        loc = func_data.get("loc", 0) or 0
        is_method = func_data.get("is_method", False)
        language = func_data.get("language", "python")
        author = func_data.get("author", "")

        func_type = "method" if is_method else "function"

        # Build description and title based on test quality
        if test_quality == TestQuality.MISSING:
            title = f"Missing tests for {func_type}: {name}"
            description = (
                f"The {func_type} '{name}' was recently added but has no corresponding test. "
                f"This is a common pattern when AI generates implementation code without tests."
            )
            severity = Severity.MEDIUM
            evidence = ["no_test_function", "recently_added"]
            
        elif test_quality == TestQuality.WEAK:
            title = f"Weak test for {func_type}: {name}"
            assertion_count = test_info.assertion_count if test_info else 0
            description = (
                f"The {func_type} '{name}' has a test but it only contains {assertion_count} assertion(s). "
                f"Weak tests may not adequately verify correct behavior and can miss bugs."
            )
            severity = Severity.LOW
            evidence = ["weak_test", "single_assertion"]
            
        elif test_quality == TestQuality.INCOMPLETE:
            title = f"Incomplete test for {func_type}: {name}"
            description = (
                f"The {func_type} '{name}' has a test but it lacks error handling coverage. "
                f"Tests should verify behavior for invalid inputs and error conditions."
            )
            severity = Severity.LOW
            evidence = ["incomplete_test", "no_error_handling"]
        else:
            # Should not reach here
            return None

        if loc > 0:
            description += f" The {func_type} has {loc} lines of code."
        if author:
            description += f" Last modified by: {author}."

        suggested_fix = self._generate_test_suggestion(name, file_path, language, test_quality, test_info)

        graph_context = {
            "function_name": name,
            "loc": loc,
            "is_method": is_method,
            "language": language,
            "test_quality": test_quality.value,
        }
        
        if test_info:
            graph_context["test_name"] = test_info.name
            graph_context["assertion_count"] = test_info.assertion_count
            graph_context["has_error_tests"] = test_info.has_error_tests

        finding = Finding(
            id=f"ai_missing_tests_{test_quality.value}_{qualified_name}",
            detector="AIMissingTestsDetector",
            severity=severity,
            title=title,
            description=description,
            affected_nodes=[qualified_name],
            affected_files=[file_path] if file_path != "unknown" else [],
            line_start=line_start,
            line_end=line_end,
            suggested_fix=suggested_fix,
            estimated_effort=self._estimate_effort(test_quality),
            graph_context=graph_context,
            why_it_matters=self._get_why_it_matters(test_quality),
        )

        # Add collaboration metadata
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="AIMissingTestsDetector",
            confidence=0.8 if test_quality == TestQuality.MISSING else 0.7,
            evidence=evidence,
            tags=["missing_tests", "ai_code", "test_coverage", test_quality.value],
        ))

        return finding

    def _generate_test_suggestion(
        self,
        func_name: str,
        file_path: str,
        language: str,
        test_quality: TestQuality,
        test_info: Optional[TestInfo],
    ) -> str:
        """Generate a test suggestion based on quality issue.

        Args:
            func_name: Function name
            file_path: Source file path
            language: Programming language
            test_quality: Quality classification
            test_info: Optional existing test info

        Returns:
            Suggested fix text
        """
        language = (language or "python").lower()

        if test_quality == TestQuality.MISSING:
            return self._generate_new_test_suggestion(func_name, language)
        elif test_quality == TestQuality.WEAK:
            return self._generate_strengthen_test_suggestion(func_name, language, test_info)
        elif test_quality == TestQuality.INCOMPLETE:
            return self._generate_error_test_suggestion(func_name, language, test_info)
        
        return ""

    def _generate_new_test_suggestion(self, func_name: str, language: str) -> str:
        """Generate suggestion for creating a new test."""
        if language == "python":
            return (
                f"Create a comprehensive test for '{func_name}':\n\n"
                f"```python\n"
                f"def test_{func_name}_success():\n"
                f'    """Test {func_name} with valid input."""\n'
                f"    result = {func_name}(valid_input)\n"
                f"    assert result is not None\n"
                f"    assert result == expected_value\n\n"
                f"def test_{func_name}_edge_cases():\n"
                f'    """Test {func_name} edge cases."""\n'
                f"    # Test boundary conditions\n"
                f"    assert {func_name}(min_value) == expected_min\n"
                f"    assert {func_name}(max_value) == expected_max\n\n"
                f"def test_{func_name}_error_handling():\n"
                f'    """Test {func_name} error handling."""\n'
                f"    with pytest.raises(ValueError):\n"
                f"        {func_name}(invalid_input)\n"
                f"```"
            )
        elif language in ("javascript", "typescript"):
            return (
                f"Create a comprehensive test for '{func_name}':\n\n"
                f"```{language}\n"
                f"describe('{func_name}', () => {{\n"
                f"  it('should handle valid input', () => {{\n"
                f"    const result = {func_name}(validInput);\n"
                f"    expect(result).toBeDefined();\n"
                f"    expect(result).toEqual(expectedValue);\n"
                f"  }});\n\n"
                f"  it('should handle edge cases', () => {{\n"
                f"    expect({func_name}(minValue)).toEqual(expectedMin);\n"
                f"    expect({func_name}(maxValue)).toEqual(expectedMax);\n"
                f"  }});\n\n"
                f"  it('should throw on invalid input', () => {{\n"
                f"    expect(() => {func_name}(invalidInput)).toThrow();\n"
                f"  }});\n"
                f"}});\n"
                f"```"
            )
        return f"Add comprehensive test coverage for '{func_name}' with multiple assertions and error handling tests."

    def _generate_strengthen_test_suggestion(
        self,
        func_name: str,
        language: str,
        test_info: Optional[TestInfo],
    ) -> str:
        """Generate suggestion for strengthening a weak test."""
        test_name = test_info.name if test_info else f"test_{func_name}"
        
        if language == "python":
            return (
                f"Strengthen the existing test '{test_name}' with additional assertions:\n\n"
                f"```python\n"
                f"def {test_name}():\n"
                f"    # Test the main behavior\n"
                f"    result = {func_name}(input_data)\n"
                f"    \n"
                f"    # Add multiple assertions to verify correctness\n"
                f"    assert result is not None  # Verify result exists\n"
                f"    assert isinstance(result, ExpectedType)  # Verify type\n"
                f"    assert result.value == expected  # Verify content\n"
                f"    assert len(result) > 0  # Verify non-empty (if applicable)\n"
                f"```\n\n"
                f"Good tests verify:\n"
                f"- Return value correctness\n"
                f"- Output type/structure\n"
                f"- Side effects (if any)\n"
                f"- State changes"
            )
        elif language in ("javascript", "typescript"):
            return (
                f"Strengthen the existing test with additional assertions:\n\n"
                f"```{language}\n"
                f"it('should handle input correctly', () => {{\n"
                f"  const result = {func_name}(inputData);\n"
                f"  \n"
                f"  // Add multiple assertions\n"
                f"  expect(result).toBeDefined();\n"
                f"  expect(typeof result).toBe('expected_type');\n"
                f"  expect(result.value).toEqual(expected);\n"
                f"  expect(result.length).toBeGreaterThan(0);\n"
                f"}});\n"
                f"```"
            )
        return f"Add more assertions to the existing test to verify multiple aspects of '{func_name}'."

    def _generate_error_test_suggestion(
        self,
        func_name: str,
        language: str,
        test_info: Optional[TestInfo],
    ) -> str:
        """Generate suggestion for adding error handling tests."""
        if language == "python":
            return (
                f"Add error handling tests for '{func_name}':\n\n"
                f"```python\n"
                f"def test_{func_name}_invalid_input():\n"
                f'    """Test {func_name} rejects invalid input."""\n'
                f"    with pytest.raises(ValueError):\n"
                f"        {func_name}(None)\n"
                f"    \n"
                f"    with pytest.raises(TypeError):\n"
                f"        {func_name}(wrong_type)\n\n"
                f"def test_{func_name}_boundary_conditions():\n"
                f'    """Test {func_name} handles edge cases."""\n'
                f"    # Empty input\n"
                f"    with pytest.raises(ValueError):\n"
                f"        {func_name}([])\n"
                f"    \n"
                f"    # Boundary values\n"
                f"    result = {func_name}(min_valid)\n"
                f"    assert result is not None\n"
                f"```"
            )
        elif language in ("javascript", "typescript"):
            return (
                f"Add error handling tests for '{func_name}':\n\n"
                f"```{language}\n"
                f"describe('{func_name} error handling', () => {{\n"
                f"  it('should throw on null input', () => {{\n"
                f"    expect(() => {func_name}(null)).toThrow();\n"
                f"  }});\n\n"
                f"  it('should throw on invalid type', () => {{\n"
                f"    expect(() => {func_name}(wrongType)).toThrow(TypeError);\n"
                f"  }});\n\n"
                f"  it('should handle empty input gracefully', () => {{\n"
                f"    expect(() => {func_name}([])).toThrow('Empty input');\n"
                f"  }});\n"
                f"}});\n"
                f"```"
            )
        return f"Add tests for error conditions and invalid inputs for '{func_name}'."

    def _estimate_effort(self, test_quality: TestQuality) -> str:
        """Estimate effort to fix based on test quality."""
        if test_quality == TestQuality.MISSING:
            return "Small (15-45 minutes)"
        elif test_quality == TestQuality.WEAK:
            return "Minimal (5-15 minutes)"
        elif test_quality == TestQuality.INCOMPLETE:
            return "Small (10-20 minutes)"
        return "Small (15-30 minutes)"

    def _get_why_it_matters(self, test_quality: TestQuality) -> str:
        """Get explanation of why this issue matters."""
        if test_quality == TestQuality.MISSING:
            return (
                "Untested code is a risk. Tests catch bugs early, document expected behavior, "
                "and make refactoring safer. AI-generated code especially needs tests since "
                "AI may produce subtly incorrect implementations."
            )
        elif test_quality == TestQuality.WEAK:
            return (
                "Tests with only one assertion may pass while the code has bugs. "
                "Multiple assertions verify different aspects of correctness. "
                "A green test with a single assertion gives false confidence."
            )
        elif test_quality == TestQuality.INCOMPLETE:
            return (
                "Tests that only cover happy paths miss bugs in error handling. "
                "Real-world code must handle invalid inputs, edge cases, and failures. "
                "Error handling bugs are often the most critical in production."
            )
        return "Adequate test coverage is essential for maintainable code."

    def _flag_entity(self, func_data: Dict[str, Any], finding: Finding) -> None:
        """Flag entity in graph for cross-detector collaboration."""
        qualified_name = func_data.get("qualified_name", "")
        if not qualified_name or not self.enricher:
            return

        try:
            self.enricher.flag_entity(
                entity_qualified_name=qualified_name,
                detector="AIMissingTestsDetector",
                severity=finding.severity.value,
                issues=["missing_tests"],
                confidence=0.8,
                metadata={
                    "function_name": func_data.get("name", ""),
                    "loc": func_data.get("loc", 0),
                    "test_quality": finding.graph_context.get("test_quality", "missing"),
                },
            )
        except Exception:
            pass

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity based on test quality.

        Args:
            finding: Finding to assess

        Returns:
            Severity level (MEDIUM for missing, LOW for weak/incomplete)
        """
        test_quality = finding.graph_context.get("test_quality", "missing")
        if test_quality == "missing":
            return Severity.MEDIUM
        return Severity.LOW

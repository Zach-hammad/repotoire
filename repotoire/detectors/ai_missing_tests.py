"""AI Missing Tests detector - identifies new code added without tests.

REPO-XXX: Detects functions/methods added in recent commits (last 30 days)
that don't have corresponding test coverage. This is a common pattern when AI
generates implementation code but neglects to generate tests.

This detector supports multiple language conventions:
- Python: pytest conventions (test_<function>, test_<module>.py, <module>_test.py)
- JavaScript/TypeScript: jest conventions (*.test.ts, *.spec.ts, describe blocks)
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph import FalkorDBClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.logging_config import get_logger
from repotoire.models import CollaborationMetadata, Finding, Severity

logger = get_logger(__name__)


class AIMissingTestsDetector(CodeSmellDetector):
    """Detects new functions/methods that lack corresponding tests.

    This detector identifies code that was recently added (configurable window,
    default 30 days) but doesn't have test coverage. This is especially common
    when AI assistants generate implementation code without generating tests.

    Detection Strategy:
    1. Find all functions added in recent commits (via Session/Commit relationships)
    2. Exclude functions that are themselves test functions
    3. Check for corresponding test functions using naming conventions
    4. Flag functions without matching tests as MEDIUM severity

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

    # Test function name patterns
    TEST_FUNCTION_PATTERNS = {
        "python": [
            r"^test_",                # test_function_name
            r"_test$",                # function_name_test
        ],
        "javascript": [
            r"^test",                 # testFunctionName
            r"^it\(",                 # it('should...')
            r"^describe\(",           # describe('module')
        ],
        "typescript": [
            r"^test",
            r"^it\(",
            r"^describe\(",
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
                - window_days: Days to look back for recent commits
                - min_function_loc: Minimum LOC to consider
                - exclude_private: Whether to exclude _private functions
                - exclude_dunder: Whether to exclude __dunder__ methods
                - max_findings: Maximum number of findings to return
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

    def detect(self) -> List[Finding]:
        """Detect recently added functions without tests.

        Returns:
            List of findings for functions missing test coverage
        """
        findings: List[Finding] = []

        try:
            # Step 1: Get all test functions and test files in the codebase
            test_functions, test_files = self._get_test_coverage_info()

            # Step 2: Get recently added functions
            recent_functions = self._get_recent_functions()

            if not recent_functions:
                logger.debug("No recent functions found for missing tests analysis")
                return findings

            # Step 3: Check each function for test coverage
            for func_data in recent_functions:
                if self._should_skip_function(func_data):
                    continue

                if not self._has_test_coverage(func_data, test_functions, test_files):
                    finding = self._create_finding(func_data)
                    findings.append(finding)

                    # Flag entity for cross-detector collaboration
                    if self.enricher:
                        self._flag_entity(func_data, finding)

                    if len(findings) >= self.max_findings:
                        break

            logger.info(f"AIMissingTestsDetector found {len(findings)} untested functions")
            return findings

        except Exception as e:
            logger.error(f"Error in AIMissingTestsDetector: {e}")
            return []

    def _get_test_coverage_info(self) -> Tuple[Set[str], Set[str]]:
        """Get existing test functions and test files.

        Returns:
            Tuple of (test_function_names, test_file_paths)
        """
        repo_filter = self._get_isolation_filter("f")

        # Query for test files and their test functions
        query = f"""
        MATCH (f:File)
        WHERE f.filePath IS NOT NULL 
          AND f.isTest = true {repo_filter}
        OPTIONAL MATCH (f)-[:CONTAINS*]->(func:Function)
        WHERE func.name IS NOT NULL
        RETURN f.filePath AS file_path,
               collect(DISTINCT func.name) AS test_func_names
        """

        try:
            results = self.db.execute_query(query, self._get_query_params())
        except Exception as e:
            logger.warning(f"Could not query test files: {e}")
            return set(), set()

        test_files: Set[str] = set()
        test_functions: Set[str] = set()

        for row in results:
            file_path = row.get("file_path", "")
            if file_path:
                test_files.add(file_path.lower())

            func_names = row.get("test_func_names", []) or []
            for name in func_names:
                if name:
                    test_functions.add(name.lower())

        # Also get test function names directly (for files not marked as isTest)
        func_query = f"""
        MATCH (func:Function)
        WHERE func.name IS NOT NULL {self._get_isolation_filter("func")}
          AND (func.name STARTS WITH 'test_' 
               OR func.name STARTS WITH 'test'
               OR func.name ENDS WITH '_test')
        RETURN DISTINCT func.name AS name
        """

        try:
            func_results = self.db.execute_query(func_query, self._get_query_params())
            for row in func_results:
                name = row.get("name", "")
                if name:
                    test_functions.add(name.lower())
        except Exception as e:
            logger.warning(f"Could not query test functions: {e}")

        return test_functions, test_files

    def _get_recent_functions(self) -> List[Dict[str, Any]]:
        """Get functions added in recent commits.

        Returns:
            List of function data dictionaries
        """
        # Calculate cutoff timestamp for recent commits
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.window_days)
        cutoff_timestamp = int(cutoff.timestamp())

        repo_filter = self._get_isolation_filter("f")

        # Query for recently added/modified functions
        # We look for functions in files that were modified recently
        query = f"""
        MATCH (s:Session)-[:MODIFIED]->(f:File)-[:CONTAINS*]->(func:Function)
        WHERE s.committedAt >= $cutoff_timestamp
          AND func.name IS NOT NULL
          AND f.isTest <> true {repo_filter}
        WITH func, f, max(s.committedAt) AS last_modified, s.author AS author
        WHERE func.loc >= $min_loc OR func.loc IS NULL
        RETURN DISTINCT 
               func.qualifiedName AS qualified_name,
               func.name AS name,
               func.lineStart AS line_start,
               func.lineEnd AS line_end,
               func.loc AS loc,
               func.isMethod AS is_method,
               f.filePath AS file_path,
               f.language AS language,
               last_modified,
               author
        ORDER BY last_modified DESC
        LIMIT $max_results
        """

        try:
            results = self.db.execute_query(
                query,
                self._get_query_params(
                    cutoff_timestamp=cutoff_timestamp,
                    min_loc=self.min_function_loc,
                    max_results=self.max_findings * 3,  # Get more to account for filtering
                ),
            )
            return results
        except Exception as e:
            logger.warning(f"Could not query recent functions (falling back): {e}")
            # Fallback: just get all non-test functions
            return self._get_functions_fallback()

    def _get_functions_fallback(self) -> List[Dict[str, Any]]:
        """Fallback query when Session/MODIFIED relationships don't exist.

        Returns:
            List of function data dictionaries
        """
        repo_filter = self._get_isolation_filter("f")

        query = f"""
        MATCH (f:File)-[:CONTAINS*]->(func:Function)
        WHERE func.name IS NOT NULL
          AND f.isTest <> true {repo_filter}
          AND (func.loc >= $min_loc OR func.loc IS NULL)
        RETURN DISTINCT 
               func.qualifiedName AS qualified_name,
               func.name AS name,
               func.lineStart AS line_start,
               func.lineEnd AS line_end,
               func.loc AS loc,
               func.isMethod AS is_method,
               f.filePath AS file_path,
               f.language AS language
        LIMIT $max_results
        """

        try:
            return self.db.execute_query(
                query,
                self._get_query_params(
                    min_loc=self.min_function_loc,
                    max_results=self.max_findings * 2,
                ),
            )
        except Exception as e:
            logger.error(f"Fallback query also failed: {e}")
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

        # Check common patterns
        for patterns in self.TEST_FILE_PATTERNS.values():
            for pattern in patterns:
                if re.search(pattern, file_path, re.IGNORECASE):
                    return True

        return False

    def _has_test_coverage(
        self,
        func_data: Dict[str, Any],
        test_functions: Set[str],
        test_files: Set[str],
    ) -> bool:
        """Check if a function has corresponding test coverage.

        Args:
            func_data: Function data dictionary
            test_functions: Set of known test function names
            test_files: Set of known test file paths

        Returns:
            True if function appears to have test coverage
        """
        name = func_data.get("name", "")
        file_path = func_data.get("file_path", "")
        language = (func_data.get("language") or "python").lower()

        if not name:
            return True  # Can't check, assume covered

        name_lower = name.lower()

        # Check for test function with matching name
        test_variants = self._get_test_function_variants(name_lower, language)
        for variant in test_variants:
            if variant in test_functions:
                return True

        # Check for test file with matching module name
        if file_path:
            test_file_variants = self._get_test_file_variants(file_path, language)
            for variant in test_file_variants:
                if variant.lower() in test_files:
                    return True

        return False

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
        # e.g., TestMyClass.test_method
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

        # Extract module name from file path
        # e.g., "src/utils/helper.py" -> "helper"
        parts = file_path.replace("\\", "/").split("/")
        filename = parts[-1] if parts else ""

        # Remove extension
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

    def _create_finding(self, func_data: Dict[str, Any]) -> Finding:
        """Create a finding for a function without tests.

        Args:
            func_data: Function data dictionary

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

        description = (
            f"The {func_type} '{name}' was recently added but has no corresponding test. "
            f"This is a common pattern when AI generates implementation code without tests."
        )
        if loc > 0:
            description += f" The {func_type} has {loc} lines of code."
        if author:
            description += f" Last modified by: {author}."

        suggested_fix = self._generate_test_suggestion(name, file_path, language)

        finding = Finding(
            id=f"ai_missing_tests_{qualified_name}",
            detector="AIMissingTestsDetector",
            severity=Severity.MEDIUM,
            title=f"Missing tests for {func_type}: {name}",
            description=description,
            affected_nodes=[qualified_name],
            affected_files=[file_path] if file_path != "unknown" else [],
            line_start=line_start,
            line_end=line_end,
            suggested_fix=suggested_fix,
            estimated_effort="Small (15-45 minutes)",
            graph_context={
                "function_name": name,
                "loc": loc,
                "is_method": is_method,
                "language": language,
            },
            why_it_matters=(
                "Untested code is a risk. Tests catch bugs early, document expected behavior, "
                "and make refactoring safer. AI-generated code especially needs tests since "
                "AI may produce subtly incorrect implementations."
            ),
        )

        # Add collaboration metadata
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="AIMissingTestsDetector",
            confidence=0.8,
            evidence=["no_test_function", "recently_added"],
            tags=["missing_tests", "ai_code", "test_coverage"],
        ))

        return finding

    def _generate_test_suggestion(self, func_name: str, file_path: str, language: str) -> str:
        """Generate a test suggestion for the function.

        Args:
            func_name: Function name
            file_path: Source file path
            language: Programming language

        Returns:
            Suggested fix text
        """
        language = (language or "python").lower()

        if language == "python":
            return (
                f"Create a test for '{func_name}' in the appropriate test file:\n\n"
                f"```python\n"
                f"def test_{func_name}():\n"
                f'    """Test {func_name} functionality."""\n'
                f"    # Arrange\n"
                f"    # ... set up test data\n\n"
                f"    # Act\n"
                f"    result = {func_name}(...)\n\n"
                f"    # Assert\n"
                f"    assert result == expected\n"
                f"```\n\n"
                f"Consider testing:\n"
                f"- Normal/happy path cases\n"
                f"- Edge cases and boundary conditions\n"
                f"- Error handling and invalid inputs"
            )
        elif language in ("javascript", "typescript"):
            return (
                f"Create a test for '{func_name}' in a test file:\n\n"
                f"```{language}\n"
                f"describe('{func_name}', () => {{\n"
                f"  it('should handle normal case', () => {{\n"
                f"    // Arrange\n"
                f"    // ... set up test data\n\n"
                f"    // Act\n"
                f"    const result = {func_name}(...);\n\n"
                f"    // Assert\n"
                f"    expect(result).toEqual(expected);\n"
                f"  }});\n"
                f"}});\n"
                f"```\n\n"
                f"Consider testing:\n"
                f"- Normal/happy path cases\n"
                f"- Edge cases and boundary conditions\n"
                f"- Error handling and invalid inputs"
            )
        else:
            return (
                f"Add test coverage for '{func_name}':\n"
                f"1. Create a test file following your project's conventions\n"
                f"2. Write tests for normal operation\n"
                f"3. Test edge cases and error conditions\n"
                f"4. Verify expected outputs for various inputs"
            )

    def _flag_entity(self, func_data: Dict[str, Any], finding: Finding) -> None:
        """Flag entity in graph for cross-detector collaboration.

        Args:
            func_data: Function data dictionary
            finding: The finding created for this function
        """
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
                },
            )
        except Exception:
            pass  # Don't fail detection if enrichment fails

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity (always MEDIUM for missing tests).

        Args:
            finding: Finding to assess

        Returns:
            Severity level (MEDIUM)
        """
        return Severity.MEDIUM
